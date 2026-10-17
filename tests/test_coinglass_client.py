"""CoinGlass adapter (fixtures from the official v4 docs) and the shared HTTP client."""
import io
import json
from pathlib import Path

import pytest

from coinglass_client import NO_KEY, CoinGlassClient, parse_balance_list
from positioning import NormaliseError
from positioning_data import FetchError, HttpClient, coin_of, fetch_raw

FIX = Path(__file__).parent / "fixtures" / "positioning"
SECRET = "cg-test-key-should-never-leak"


def load(name):
    return json.loads((FIX / name).read_text())


class RecordingHttp:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload, error, []

    def request_json(self, url, headers=None, **kw):
        self.calls.append({"url": url, "headers": headers, **kw})
        if self.error:
            raise self.error
        return self.payload


def test_key_absent_returns_null_with_reason_and_no_request():
    http = RecordingHttp(load("coinglass_exchange_balance_list.json"))
    client = CoinGlassClient(http, env={})
    out = client.exchange_flows("BTC")
    assert out["available"] is False and out["value"] is None and out["reason"] == NO_KEY
    assert http.calls == []
    assert not CoinGlassClient(http, env={"COINGLASS_API_KEY": "   "}).enabled


def test_key_present_parses_documented_balance_list():
    http = RecordingHttp(load("coinglass_exchange_balance_list.json"))
    client = CoinGlassClient(http, env={"COINGLASS_API_KEY": SECRET})
    out = client.exchange_flows("BTC", now_ms=1)
    assert http.calls[0]["url"] == "https://open-api-v4.coinglass.com/api/exchange/balance/list?symbol=BTC"
    assert http.calls[0]["headers"] == {"CG-API-KEY": SECRET}
    assert SECRET not in json.dumps(http.calls[0]["cache_key"])
    assert out["available"] is True and out["unit"] == "BTC"
    assert out["value"] == "1144.48008"                 # 638.797302 + 505.682778
    assert out["totals"]["change_7d"] == "-2984.918032"  # 799.967408 + -3784.88544
    assert out["exchanges"][0]["exchange"] == "Coinbase"
    assert SECRET not in json.dumps(out) and SECRET not in repr(client)


def test_error_code_and_failures_never_leak_key():
    with pytest.raises(NormaliseError, match="401"):
        parse_balance_list(load("coinglass_error_401.json"))
    client = CoinGlassClient(RecordingHttp(load("coinglass_error_401.json")), env={"COINGLASS_API_KEY": SECRET})
    out = client.exchange_flows("ETH")
    assert out["value"] is None and "401" in out["reason"]
    boom = CoinGlassClient(RecordingHttp(error=RuntimeError(f"bad {SECRET}")), env={"COINGLASS_API_KEY": SECRET})
    assert SECRET not in boom.exchange_flows("BTC")["reason"]
    unsupported = CoinGlassClient(RecordingHttp(), env={"COINGLASS_API_KEY": SECRET}).exchange_flows("SOL")
    assert unsupported["value"] is None and "does not cover SOL" in unsupported["reason"]


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_http_client_caches_paces_and_caps():
    opened, slept, now = [], [], [0.0]

    def opener(req, timeout):
        opened.append((req.full_url, req.get_method(), dict(req.header_items())))
        return FakeResp(b'{"ok": 1}')

    c = HttpClient(opener=opener, clock=lambda: now[0], sleep=slept.append, min_interval={"h.test": 1.0})
    assert c.request_json("https://h.test/a", ttl=10) == {"ok": 1}
    assert c.request_json("https://h.test/a", ttl=10) == {"ok": 1}
    assert len(opened) == 1                      # cached
    c.request_json("https://h.test/b")
    assert slept == [1.0]                        # second request paced 1 s after the first
    c.request_json("https://h.test/c", body={"type": "x"})
    assert opened[-1][1] == "POST"
    meta = {}
    c.request_json("https://h.test/a", ttl=10, meta=meta)
    assert meta["cached"] is True and isinstance(meta["fetched_at"], int)
    big = HttpClient(opener=lambda r, timeout: FakeResp(b"[" + b"1," * 3_000_000 + b"1]"), min_interval={})
    with pytest.raises(FetchError, match="exceeds"):
        big.request_json("https://h.test/big")


def test_http_errors_hide_details():
    def opener(req, timeout):
        err = OSError("secret header dump")
        err.code = 429
        raise err

    with pytest.raises(FetchError) as info:
        HttpClient(opener=opener, min_interval={}).request_json("https://h.test/x", headers={"CG-API-KEY": SECRET})
    assert str(info.value) == "h.test: HTTP 429" and SECRET not in str(info.value)


def test_fetch_raw_keys_and_failure_capture():
    class Http:
        def get(self, base, path, params, ttl):
            if "basis" in path:
                raise FetchError("fapi.binance.com: HTTP 418")
            return [path, params]

        def request_json(self, url, body=None, ttl=0, meta=None):
            meta.update(fetched_at=123)
            return body

    raw = fetch_raw(Http(), "binance", "BTCUSDT", "1h")
    assert set(raw) == {"top_account", "top_position", "global_account", "taker", "oi_hist", "oi_now",
                        "basis", "premium", "klines"}
    assert isinstance(raw["basis"], FetchError)
    assert raw["oi_hist"][1] == {"symbol": "BTCUSDT", "period": "1h", "limit": 25}
    by = fetch_raw(Http(), "bybit", "ETHUSDT", "5m")
    assert by["by_account_ratio"][1]["period"] == "5min"
    assert fetch_raw(Http(), "hyperliquid", "BTC", "1h") == {"hl_ctxs": {"type": "metaAndAssetCtxs"},
                                                             "hl_fetched_at": 123}
    assert coin_of("binance", "1000PEPEUSDT") == "PEPE" and coin_of("bybit", "ETHUSDT") == "ETH"
