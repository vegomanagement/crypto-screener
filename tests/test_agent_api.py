"""agent_api routes: validation, fallback status, rate limit, stats, no secret leakage."""
import json

import pytest
from flask import Flask

import analyst_hermes as ah
from agent_api import register_agent_routes
from analyst_service import AnalystService
from tests.test_analyst import providers
from tests.test_analyst_service import good

SECRET = "sk-test-not-a-real-key-123"


def make_app(tmp_path, send, rate=(6, 60), **over):
    app = Flask(__name__)
    register_agent_routes(app, store_path=tmp_path / "agent.sqlite3", fetch_bars=lambda *a: [],
                          service_factory=lambda store: AnalystService(
                              providers(**over), send, store, rate=rate))
    return app.test_client()


def post(client, body, **headers):
    return client.post("/api/agent/analysis", data=json.dumps(body),
                       content_type="application/json", headers=headers)


@pytest.mark.parametrize("body", [
    {"exchange": "kraken", "symbol": "BTCUSDT", "interval": "15"},
    {"exchange": "binance", "symbol": "BTC USDT", "interval": "15"},
    {"exchange": "binance", "symbol": "BTCUSDT", "interval": "7"},
    {"exchange": "binance", "symbol": "BTCUSDT", "interval": "5s"},
    {"exchange": "binance", "symbol": "BTCUSDT", "interval": "15", "force": "yes"},
    [1, 2],
])
def test_rejects_bad_input(tmp_path, body):
    client = make_app(tmp_path, lambda m, deadline=None: ("", "m"))
    assert post(client, body).status_code == 400


def test_rejects_cross_site(tmp_path):
    client = make_app(tmp_path, lambda m, deadline=None: ("", "m"))
    r = post(client, {"exchange": "binance", "symbol": "BTCUSDT", "interval": "15"},
             Origin="https://evil.example")
    assert r.status_code == 403


def test_ok_and_logged(tmp_path):
    import analyst
    snap = analyst.build_snapshot("binance", "BTCUSDT", "15", providers())
    client = make_app(tmp_path, lambda m, deadline=None: (json.dumps(good(snap)), "hermes-agent"))
    r = post(client, {"exchange": "binance", "symbol": "BTCUSDT", "interval": "15"})
    assert r.status_code == 200
    assert r.json["status"] == "ok" and r.json["analysis"]["long_pct"] + r.json["analysis"]["short_pct"] == 100
    assert r.json["analysis"]["split"]["weights"]["agent"] > 0
    stats = client.get("/api/agent/stats?exchange=binance&symbol=BTCUSDT&interval=15").json
    assert stats["total"] == 1 and stats["pending"] == 1 and stats["overall"]["hit_rate"] is None


def test_hermes_down_returns_baseline(tmp_path):
    def send(m, deadline=None):
        raise ah.HermesUnavailable("gateway unreachable: URLError")
    r = post(make_app(tmp_path, send), {"exchange": "bybit", "symbol": "ETHUSDT", "interval": "60"})
    assert r.status_code == 200 and r.json["status"] == "hermes_unavailable"
    assert r.json["source"] == "baseline"


def test_rate_limited(tmp_path):
    client = make_app(tmp_path, lambda m, deadline=None: ("x", "m"), rate=(1, 60))
    assert post(client, {"exchange": "binance", "symbol": "AUSDT", "interval": "15"}).status_code == 200
    r = post(client, {"exchange": "binance", "symbol": "BUSDT", "interval": "15"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


def test_snapshot_failure_is_502(tmp_path):
    def boom(*a):
        raise RuntimeError("down")
    r = post(make_app(tmp_path, lambda m, deadline=None: ("", "m"), chart=boom),
             {"exchange": "binance", "symbol": "BTCUSDT", "interval": "15"})
    assert r.status_code == 502


def test_stats_filter_validation(tmp_path):
    client = make_app(tmp_path, lambda m, deadline=None: ("", "m"))
    assert client.get("/api/agent/stats?symbol=BTCUSDT").status_code == 400
    assert client.get("/api/agent/stats?interval=nope").status_code == 400
    assert client.get("/api/agent/stats").status_code == 200


def test_default_service_never_exposes_key(tmp_path, monkeypatch):
    """Real transport against an unreachable URL: key must not appear in the response."""
    app = Flask(__name__)
    register_agent_routes(app, store_path=tmp_path / "a.sqlite3", fetch_bars=lambda *a: [],
                          env={"HERMES_API_URL": "http://127.0.0.1:9/v1", "HERMES_API_KEY": SECRET})
    import analyst
    monkeypatch.setattr(analyst, "default_providers", lambda **kw: providers())
    r = post(app.test_client(), {"exchange": "binance", "symbol": "BTCUSDT", "interval": "15"})
    assert r.status_code == 200 and r.json["status"] == "hermes_unavailable"
    assert SECRET not in r.get_data(as_text=True)


def test_screener_registers_routes():
    import screener
    rules = {r.rule for r in screener.app.url_map.iter_rules()}
    assert {"/api/agent/analysis", "/api/agent/stats"} <= rules


@pytest.mark.parametrize("periods", [[1, 14, 21], [7, 14, 401], [7, 14, 21, 28], [7.5], ["7"], [], "7,14,21", {"a": 1}])
def test_rejects_bad_ema_periods(tmp_path, periods):
    client = make_app(tmp_path, lambda m, deadline=None: ("", "m"))
    r = post(client, {"exchange": "binance", "symbol": "BTCUSDT", "interval": "15", "ema_periods": periods})
    assert r.status_code == 400 and "ema_periods" in r.json["error"]


def test_ema_periods_reach_the_snapshot_and_split_the_cache(tmp_path):
    import analyst
    snap = analyst.build_snapshot("binance", "BTCUSDT", "15", providers())
    calls = []

    def send(m, deadline=None):
        calls.append(m)
        return json.dumps(good(snap)), "hermes-agent"
    client = make_app(tmp_path, send)
    body = {"exchange": "binance", "symbol": "BTCUSDT", "interval": "15"}
    default = post(client, body).json
    assert default["snapshot"]["indicators"]["ema_periods"] == [7, 14, 21]
    custom = post(client, {**body, "ema_periods": [55, 9, 21]}).json
    assert custom["cached"] is False and custom["snapshot"]["indicators"]["ema_periods"] == [9, 21, 55]
    again = post(client, {**body, "ema_periods": [9, 21, 55]}).json
    assert again["cached"] is True and len(calls) == 2
    assert '"ema_periods":[9,21,55]' in calls[1][1]["content"]
