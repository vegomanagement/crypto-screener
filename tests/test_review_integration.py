"""Regression tests for defects found in the claude/integration review (docs/REVIEW_INTEGRATION.md).

Each test reproduces one confirmed defect; the fix lives in the module named in the test.
"""
import json
import socket

import pytest

import analyst
import analyst_hermes as ah
from analyst_store import AnalysisStore, BarsUnavailable, evaluate_pending
from tests.test_analyst import providers
from tests.test_analyst_service import good


@pytest.fixture
def snap():
    return analyst.build_snapshot("binance", "BTCUSDT", "15", providers())


# ── analyst_hermes: malformed model output must not crash the request (HTTP 500) ──

@pytest.mark.parametrize("field,value", [("levels", {"a": 1}), ("levels", 5), ("scenarios", 3),
                                         ("scenarios", {"name": "x"}), ("risks", 7), ("risks", {"a": "b"})])
def test_validate_ignores_non_list_sections(snap, field, value):
    out, warnings = ah.validate(good(snap, **{field: value}), snap)
    assert out[field] == []
    assert any(field in w and "not a list" in w for w in warnings)


def test_non_string_gateway_content_falls_back_instead_of_crashing(snap):
    out = ah.ask_hermes(snap, lambda m, deadline=None: ([{"type": "text", "text": "{}"}], "m"))
    assert out["analysis"] is None and out["attempts"] == 2 and out["error"]


# ── analyst_store: unscorable rows must not starve the evaluator ──

BASE = {"exchange": "binance", "symbol": "BTCUSDT", "interval": "1", "horizon_bars": 2,
        "source": "hermes", "status": "ok", "price": 100.0, "atr": 1.0, "bias": "long",
        "long_pct": 70, "short_pct": 30}


def test_too_old_rows_are_expired_and_newer_rows_still_get_scored():
    store = AnalysisStore(":memory:")
    for i in range(15):   # analysed long ago: bars no longer fetchable for scoring
        store.record({**BASE, "ts": i, "bar_close_ts": i * 60_000})
    fresh = store.record({**BASE, "ts": 100, "bar_close_ts": 10_000_000})
    bars = [{"h": 101.5, "l": 99.9}, {"h": 100, "l": 100}]

    def fetch(exchange, symbol, interval, after_ms, count):
        if after_ms < 1_000_000:
            raise BarsUnavailable("analysis too old for the candle window")
        return bars
    now = 10_000_000 + 10 * 60_000
    for _ in range(3):
        evaluate_pending(store, fetch, now, lambda i: 60_000, limit=10)
    rows = dict(store._conn.execute("SELECT id, outcome FROM agent_analyses").fetchall())
    assert rows[fresh] == "win"
    assert sum(1 for o in rows.values() if o == "expired") == 15
    stats = store.stats()
    assert stats["pending"] == 0 and stats["expired"] == 15 and stats["evaluated"] == 1


def test_rows_without_atr_are_marked_unscorable_without_fetching():
    store = AnalysisStore(":memory:")
    for i in range(12):
        store.record({**BASE, "ts": i, "bar_close_ts": 0, "atr": None})
    fresh = store.record({**BASE, "ts": 50, "bar_close_ts": 0})
    fetched = []

    def fetch(*args):
        fetched.append(args)
        return [{"h": 101.5, "l": 99.9}, {"h": 100, "l": 100}]
    evaluate_pending(store, fetch, 10 ** 7, lambda i: 60_000, limit=10)
    evaluate_pending(store, fetch, 10 ** 7, lambda i: 60_000, limit=10)
    rows = dict(store._conn.execute("SELECT id, outcome FROM agent_analyses").fetchall())
    assert rows[fresh] == "win" and len(fetched) == 1
    assert sum(1 for o in rows.values() if o == "unscorable") == 12


def test_pending_looks_past_the_first_500_not_yet_due_rows():
    store = AnalysisStore(":memory:")
    for i in range(510):   # weekly analyses: horizon far in the future, never due yet
        store.record({**BASE, "interval": "W", "ts": i, "bar_close_ts": 10 ** 7})
    due = store.record({**BASE, "ts": 1000, "bar_close_ts": 0})
    durations = {"W": 7 * 86_400_000, "1": 60_000}
    assert [r[0] for r in store.pending(10 ** 7, durations.__getitem__)] == [due]


def test_default_fetch_bars_signals_expiry_with_bars_unavailable():
    import agent_api
    with pytest.raises(BarsUnavailable):
        agent_api.default_fetch_bars("binance", "BTCUSDT", "1", 0, 12)


# ── ws_client: a read timeout in the middle of a frame must not desynchronise the stream ──

class ChunkSock:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def recv(self, n):
        chunk = self.chunks.pop(0)
        if chunk is socket.timeout:
            raise socket.timeout()
        return chunk

    def sendall(self, data):
        pass

    def close(self):
        pass


def test_recv_survives_timeout_between_header_and_payload():
    from ws_client import WebSocket
    payload = b'{"hello":"world"}'
    frame = bytes([0x81, len(payload)]) + payload
    ws = WebSocket(ChunkSock([frame[:2], socket.timeout, frame[2:], frame]))
    with pytest.raises(socket.timeout):
        ws.recv()
    assert ws.recv() == payload.decode()
    assert ws.recv() == payload.decode()


# ── liquidations_collector: a socket that drops right after connecting must back off ──

def test_collector_backs_off_when_connections_drop_immediately():
    from liquidations_collector import LiquidationCollector
    from liquidations_store import LiquidationStore
    from ws_client import WebSocketClosed

    class DropWS:
        def recv(self):
            raise WebSocketClosed("server closed")

        def send_text(self, text):
            pass

        def close(self):
            pass
    sleeps = []
    collector = LiquidationCollector(LiquidationStore(clock_ms=lambda: 1000), connect=lambda url: DropWS(),
                                     sleep=sleeps.append, clock_ms=lambda: 1000)
    collector.watch("binance", "BTCUSDT")
    calls = {"n": 0}
    wanted = collector.wanted

    def limited(venue):
        calls["n"] += 1
        return wanted(venue) if calls["n"] < 30 else []
    collector.wanted = limited
    collector.run_venue("binance")
    assert sleeps[:6] == [2, 5, 15, 30, 60, 60]


def test_collector_resets_backoff_after_a_stable_connection():
    from liquidations_collector import STABLE_CONNECTION_MS, LiquidationCollector
    from liquidations_store import LiquidationStore
    from ws_client import WebSocketClosed
    now = {"ms": 0}

    class LongWS:
        def recv(self):
            now["ms"] += STABLE_CONNECTION_MS + 1
            raise WebSocketClosed("24h rotation")

        def send_text(self, text):
            pass

        def close(self):
            pass
    sleeps = []
    collector = LiquidationCollector(LiquidationStore(clock_ms=lambda: now["ms"]), connect=lambda url: LongWS(),
                                     sleep=sleeps.append, clock_ms=lambda: now["ms"], lease_ms=10 ** 12)
    collector.watch("binance", "BTCUSDT")
    calls = {"n": 0}
    wanted = collector.wanted

    def limited(venue):
        calls["n"] += 1
        return wanted(venue) if calls["n"] < 12 else []
    collector.wanted = limited
    collector.run_venue("binance")
    assert sleeps and set(sleeps) == {2}


# ── hl_whale_tracker: the ~38 MB leaderboard must not be re-fetched every 10 s on failure ──

class FakeTime:
    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s


def test_failing_leaderboard_backs_off():
    from hl_whale_tracker import WhaleTracker
    t, opens = FakeTime(), []

    def open_lb():
        opens.append(t.now)
        raise OSError("connection reset mid-download")
    w = WhaleTracker(client=None, open_leaderboard=open_lb, clock=t.clock, sleep=t.sleep, lease_ms=600_000)
    w.watch("x")
    w._run()
    assert len(opens) <= 8, f"{len(opens)} leaderboard downloads in 10 minutes"
    assert w.error and "OSError" in w.error


class LB:
    def __init__(self, rows):
        self.data = json.dumps({"leaderboardRows": rows}).encode()

    def read(self, n):
        out, self.data = self.data[:n], self.data[n:]
        return out

    def close(self):
        pass


def test_stale_leaderboard_is_reused_when_the_hourly_refresh_fails():
    from hl_whale_tracker import WhaleTracker
    t = FakeTime()
    answers = [LB([{"ethAddress": "0xa", "accountValue": "100", "displayName": None}])]

    def open_lb():
        if answers:
            return answers.pop()
        raise OSError("refresh failed")

    class Client:
        def request_json(self, url, body=None):
            return {"assetPositions": [{"position": {"coin": "BTC", "szi": "1", "entryPx": "10",
                                                     "positionValue": "10"}}]}
    w = WhaleTracker(Client(), open_leaderboard=open_lb, clock=t.clock, sleep=t.sleep)
    assert w.poll_once()["ok"] == 1
    t.now += 2 * 3600            # leaderboard TTL (1 h) has passed; the refresh fails
    poll = w.poll_once()
    assert poll is not None and poll["ok"] == 1 and w.error is None
    assert w.leaderboard["refresh_error"].startswith("OSError")


# ── funding_data / positioning_snapshot: malformed venue JSON must degrade, not 500 ──

def test_funding_malformed_venue_payload_is_a_funding_error():
    import funding_data

    def http(url, payload=None):
        if "tickers" in url:
            return {"retCode": 0, "result": {"list": ["not-an-object"]}}
        if "history" in url:
            return {"retCode": 0, "result": {"list": []}}
        return []
    with pytest.raises(funding_data.FundingError):
        funding_data.FundingService(http=http).get("bybit", "BTCUSDT")


def test_funding_route_returns_502_on_malformed_payload():
    from flask import Flask

    import funding_api
    import funding_data

    def http(url, payload=None):
        return [{"universe": ["BTC"]}, ["x"]] if payload and payload.get("type") == "metaAndAssetCtxs" else []
    app = Flask(__name__)
    funding_api.register_funding_routes(app, service_factory=lambda: funding_data.FundingService(http=http))
    resp = app.test_client().get("/api/funding?exchange=hyperliquid&symbol=BTC")
    assert resp.status_code == 502 and resp.get_json()["error"] == "funding unavailable"


def test_positioning_snapshot_survives_attribute_errors_in_one_section():
    import positioning_snapshot as ps
    snap = ps.build_snapshot("hyperliquid", "BTC", "1h", {"hl_ctxs": [{"universe": ["BTC"]}, [{}]]},
                             now_ms=1, coin="BTC")
    assert snap["basis"]["value"] is None and "unexpected response" in snap["basis"]["reason"]
    assert snap["open_interest"]["current"]["value"] is None


# ── analyst.build_snapshot: the provider timeout is one deadline, not one per provider ──

def test_hanging_providers_share_one_deadline(monkeypatch):
    import threading
    import time as _time
    release = threading.Event()

    def hang(*args):
        release.wait(5)
        raise RuntimeError("released")
    monkeypatch.setattr(analyst, "PROVIDER_TIMEOUT_S", 0.3)
    p = providers(context=hang, depth=hang, funding=hang, footprint=hang, market=hang, positioning=hang)
    started = _time.monotonic()
    try:
        snap = analyst.build_snapshot("binance", "BTCUSDT", "15", p)
    finally:
        release.set()
    elapsed = _time.monotonic() - started
    assert elapsed < 1.0, f"build_snapshot waited {elapsed:.2f}s for 6 hanging providers"
    assert "timed out" in snap["missing"]["funding"]


# ── agent_api: same-origin check must survive a TLS-terminating proxy (Railway) ──

@pytest.mark.parametrize("origin,expected", [
    ("https://bot.example", 400),        # the page itself, served over https by the proxy
    ("http://bot.example", 400),
    ("https://evil.example", 403),
    ("https://bot.example.evil.example", 403),
    ("null", 403),
    ("http://[::1", 403),
])
def test_same_origin_check_ignores_proxy_scheme(tmp_path, origin, expected):
    from tests.test_agent_api import make_app
    client = make_app(tmp_path, lambda m, deadline=None: ("", "m"))
    resp = client.post("/api/agent/analysis", data="[]", content_type="application/json",
                       base_url="http://bot.example", headers={"Origin": origin})
    assert resp.status_code == expected
