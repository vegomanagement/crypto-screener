"""funding.py / funding_data.py / funding_api.py with recorded venue responses (no network)."""
import json
from pathlib import Path

import pytest
from flask import Flask

import funding
import funding_data
from funding_api import register_funding_routes

RAW = json.loads((Path(__file__).parent / "fixtures" / "funding" / "live_raw.json").read_text())
NOW = RAW["_captured_at_ms"]


def test_decimal_helpers():
    assert funding.fmt(funding.dec("0.00010000")) == "0.0001"
    assert funding.fmt(funding.dec("-0.0000")) == "0"
    assert funding.dec(True) is None and funding.dec("nan") is None and funding.dec("") is None
    assert funding.annualized("0.0001", 8) == "0.1095"
    assert funding.annualized("0.0000125", 1) == "0.1095"
    assert funding.annualized("0.0001", 0) is None
    assert funding.premium("101", "100") == "0.01"


def test_trend_direction():
    rising = [{"rate": r, "time": i} for i, r in enumerate(["0.0001", "0.0001", "0.0001", "0.0003", "0.0003", "0.0003"])]
    t = funding.trend(rising)
    assert t["direction"] == "rising" and t["n"] == 6 and t["positive"] == 6
    assert funding.trend(rising[:1]) is None
    flat = [{"rate": "0.0001", "time": i} for i in range(6)]
    assert funding.trend(flat)["direction"] == "flat"


def test_binance_matches_raw_fields():
    p, h = RAW["binance_premiumIndex_BTCUSDT"], RAW["binance_fundingRate_BTCUSDT"]
    out = funding.binance("BTCUSDT", p, h, RAW["binance_fundingInfo_subset"], NOW)
    assert out["predicted"]["rate"] == funding.fmt(funding.dec(p["lastFundingRate"]))
    assert out["predicted"]["settles_at"] == p["nextFundingTime"]
    latest = max(h, key=lambda r: r["fundingTime"])
    assert out["last_settled"]["rate"] == funding.fmt(funding.dec(latest["fundingRate"]))
    assert out["last_settled"]["time"] == latest["fundingTime"]
    info = next(r for r in RAW["binance_fundingInfo_subset"] if r["symbol"] == "BTCUSDT")
    assert out["interval_hours"]["value"] == info["fundingIntervalHours"]
    assert out["cap"]["value"] == funding.fmt(funding.dec(info["adjustedFundingRateCap"]))
    assert out["annualized"]["predicted"] == funding.annualized(p["lastFundingRate"], info["fundingIntervalHours"])
    assert "live estimate" in out["predicted"]["note"]
    assert out["mark_price"]["value"] == funding.fmt(funding.dec(p["markPrice"]))
    assert len(out["history"]["items"]) == len(h)


def test_binance_unlisted_symbol_defaults_to_8h_with_reason():
    p = dict(RAW["binance_premiumIndex_BTCUSDT"], symbol="ETHUSDT")
    out = funding.binance("ETHUSDT", p, [], [], NOW)
    assert out["interval_hours"]["value"] == 8 and "default 8h" in out["interval_hours"]["source"]
    assert out["cap"] is None and "fundingInfo" in out["missing"]["cap"]
    assert out["last_settled"] is None and out["missing"]["last_settled"]


def test_bybit_matches_raw_fields():
    t = RAW["bybit_tickers_SOLUSDT"]["result"]["list"][0]
    hist = RAW["bybit_history_SOLUSDT"]["result"]["list"]
    out = funding.bybit("SOLUSDT", RAW["bybit_tickers_SOLUSDT"], RAW["bybit_history_SOLUSDT"], NOW)
    assert out["predicted"]["rate"] == funding.fmt(funding.dec(t["fundingRate"]))
    assert out["predicted"]["settles_at"] == int(t["nextFundingTime"])
    assert out["interval_hours"]["value"] == int(t["fundingIntervalHour"])
    assert out["cap"]["value"] == funding.fmt(funding.dec(t["fundingCap"]))
    assert out["floor"]["value"] == "-" + out["cap"]["value"]
    latest = max(hist, key=lambda r: int(r["fundingRateTimestamp"]))
    assert out["last_settled"]["rate"] == funding.fmt(funding.dec(latest["fundingRate"]))
    assert [i["time"] for i in out["history"]["items"]] == sorted(i["time"] for i in out["history"]["items"])


def test_bybit_error_body():
    out = funding.bybit("SOLUSDT", {"retCode": 10001, "result": {}}, None, NOW)
    assert out["predicted"] is None and out["missing"]["tickers"] and out["missing"]["history"]


def test_hyperliquid_matches_raw_fields():
    ctx = RAW["hl_metaAndAssetCtxs_BTC_only"][1][0]
    hist = RAW["hl_fundingHistory_BTC"]
    out = funding.hyperliquid("BTC", RAW["hl_metaAndAssetCtxs_BTC_only"], hist, NOW)
    assert out["predicted"]["rate"] == funding.fmt(funding.dec(ctx["funding"]))
    assert out["predicted"]["settles_at"] % funding.HOUR_MS == 0 and out["predicted"]["settles_at"] > NOW
    assert out["premium"]["value"] == funding.fmt(funding.dec(ctx["premium"]))
    assert out["last_settled"]["time"] == max(r["time"] for r in hist)
    assert out["interval_hours"]["value"] == 1 and out["cap"] is None


def test_cross_venue_and_coin_mapping():
    cv = funding.cross_venue(RAW["hl_predictedFundings_subset"], "BTC", NOW)
    assert {r["venue"] for r in cv["rows"]} == {"binance", "bybit", "hyperliquid"}
    for r in cv["rows"]:
        assert r["annualized"] == funding.annualized(r["rate"], r["interval_hours"])
        assert r["stale"] == (r["settles_at"] < NOW)
    assert cv["annualized_spread"] is not None
    assert funding.hl_coin("binance", "1000PEPEUSDT") == "kPEPE"
    assert funding.hl_coin("bybit", "BTCUSDT") == "BTC"
    assert funding.hl_coin("binance", "BTCUSDC") is None
    assert funding.cross_venue(RAW["hl_predictedFundings_subset"], "NOPE", NOW) is None


def fake_http(calls):
    def http(url, payload=None, timeout=None):
        calls.append((url, payload))
        if "premiumIndex" in url:
            return RAW["binance_premiumIndex_BTCUSDT"]
        if "fundingRate" in url:
            return RAW["binance_fundingRate_BTCUSDT"]
        if "fundingInfo" in url:
            return RAW["binance_fundingInfo_subset"]
        if payload and payload["type"] == "predictedFundings":
            return RAW["hl_predictedFundings_subset"]
        raise funding_data.FundingError("HTTP 500 from test")
    return http


def test_service_caches_30s_and_reports_fetch_failures():
    calls, t = [], [NOW / 1000]
    svc = funding_data.FundingService(http=fake_http(calls), clock=lambda: t[0])
    out = svc.get("binance", "BTCUSDT")
    assert out["cross_venue"]["coin"] == "BTC" and out["cache_ttl_s"] == 30
    n = len(calls)
    svc.get("binance", "BTCUSDT")
    assert len(calls) == n
    t[0] += 31
    svc.get("binance", "BTCUSDT")
    assert len(calls) > n
    with pytest.raises(funding_data.FundingError):
        svc.get("bybit", "SOLUSDT")  # every bybit fetch fails → no data
    with pytest.raises(ValueError):
        svc.get("kraken", "BTCUSDT")


def test_route(monkeypatch):
    app = Flask(__name__)
    register_funding_routes(app, service_factory=lambda: funding_data.FundingService(http=fake_http([]),
                                                                                    clock=lambda: NOW / 1000))
    client = app.test_client()
    r = client.get("/api/funding?exchange=binance&symbol=BTCUSDT")
    assert r.status_code == 200 and r.json["predicted"]["rate"]
    assert client.get("/api/funding?exchange=kraken&symbol=BTCUSDT").status_code == 400
    assert client.get("/api/funding?exchange=binance&symbol=BTC%20USDT").status_code == 400
    assert client.get("/api/funding?exchange=bybit&symbol=SOLUSDT").status_code == 502


def test_screener_registers_funding_route():
    import screener
    assert "/api/funding" in {r.rule for r in screener.app.url_map.iter_rules()}
