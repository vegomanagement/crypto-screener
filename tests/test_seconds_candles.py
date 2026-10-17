"""Seconds candles: pure aggregation, Binance backfill and /api/seconds (no network)."""
import json
from pathlib import Path

import pytest
from flask import Flask

import footprint as fp
import footprint_data as fd
import screener
import seconds_candles as sc
from seconds_api import register_seconds_routes
from tests.test_footprint_data import FakeBinance

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "seconds_parity.json").read_text())


def fixture_trades():
    return [fp.normalize_binance(r) for r in FIXTURE["trades"]]


@pytest.mark.parametrize("seconds", ["1", "5", "15", "30"])
def test_shared_fixture_utc_alignment_gaps_and_out_of_order(seconds):
    bars = sc.aggregate(fixture_trades(), int(seconds) * 1000)
    assert bars == FIXTURE["expected"][seconds]
    for bar in bars:
        assert bar["t"] % (int(seconds) * 1000) == 0, "epoch/UTC aligned"


def test_gap_seconds_have_no_candle_and_midnight_splits_buckets():
    midnight = FIXTURE["midnight_ms"]
    times = [b["t"] for b in sc.aggregate(fixture_trades(), 1000)]
    assert not [t for t in times if midnight + 3000 <= t < midnight + 29000]
    assert midnight - 1000 in times and midnight in times  # 23:59:59.999 vs 00:00:00.000


def test_bucket_times_match_footprint_bar_formula():
    # Same formula as footprint_data: open = time_ms // duration * duration.
    for trade in fixture_trades():
        for step in (1000, 5000, 15000, 30000, 60000):
            assert sc.bucket_open(trade.time_ms, step) == trade.time_ms // step * step


def test_partial_first_bucket_is_dropped():
    case = FIXTURE["partial_case"]
    bars = sc.aggregate(fixture_trades(), case["seconds"] * 1000, oldest_ms=case["oldest_ms"])
    assert bars[0]["t"] == case["expected_first_t"]


def test_rejects_non_whole_second_steps():
    with pytest.raises(ValueError):
        sc.aggregate([], 1500)


def rows(n, start, first_id=100, every=250):
    return [{"a": first_id + i, "p": f"{100 + i % 5}.5", "q": "0.010", "f": 1, "l": 1,
             "T": start + i * every, "m": i % 2 == 0} for i in range(n)]


def test_backfill_pages_backward_and_reports_exact_coverage():
    start = 1_792_195_200_000
    trades = rows(3000, start)  # 750 s of trades, 4 per second
    now = trades[-1]["T"] + 400
    fake = FakeBinance(trades)
    svc = fd.FootprintService(request=fake, clock=lambda: now)
    out = sc.binance_backfill(svc, "BTCUSDT", minutes=5, seconds=5)
    assert out["requests"] >= 2 and not out["truncated"]
    assert out["last_trade_id"] == trades[-1]["a"]
    oldest = out["complete_from_ms"] - 5000
    assert all(b["t"] >= out["complete_from_ms"] for b in out["bars"])
    assert out["complete_from_ms"] <= now - 5 * 60_000 + 5000
    # every complete bar has exactly 20 trades (5 s × 4/s) and volume 0.2
    closed = [b for b in out["bars"] if not b["forming"]]
    assert closed and all(b["n"] == 20 and b["v"] == "0.2" for b in closed)
    # as_of falls in an empty bucket: no fabricated forming bar
    assert not any(b["forming"] for b in out["bars"])
    assert out["bars"][-1]["t"] == (trades[-1]["T"] // 5000) * 5000
    assert oldest < out["bars"][0]["t"]


def test_backfill_budget_truncates_old_history_not_new():
    start = 1_792_195_200_000
    trades = rows(6000, start, every=50)
    now = trades[-1]["T"] + 10
    svc = fd.FootprintService(request=FakeBinance(trades), clock=lambda: now)
    out = sc.binance_backfill(svc, "BTCUSDT", minutes=5, max_requests=2)
    assert out["truncated"] is True and out["requests"] == 2
    assert out["last_trade_id"] == trades[-1]["a"]
    assert any("budget" in w for w in out["warnings"])
    assert out["complete_from_ms"] > out["requested_from_ms"]


def test_backfill_raises_on_non_contiguous_ids():
    start = 1_792_195_200_000
    trades = rows(1500, start)
    del trades[200]
    svc = fd.FootprintService(request=FakeBinance(trades), clock=lambda: trades[-1]["T"] + 1)
    with pytest.raises(fd.FootprintError):
        sc.binance_backfill(svc, "BTCUSDT", minutes=5)


def client_for(service):
    app = Flask(__name__)
    register_seconds_routes(app, service_factory=lambda: service)
    return app.test_client()


def test_route_payload_and_validation():
    start = 1_792_195_200_000
    trades = rows(800, start)
    svc = fd.FootprintService(request=FakeBinance(trades), clock=lambda: trades[-1]["T"] + 1)
    client = client_for(svc)
    response = client.get("/api/seconds?exchange=binance&symbol=BTCUSDT&minutes=2&seconds=1")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_json()
    assert body["source"] == "binance aggTrades" and body["bars"]
    assert isinstance(body["bars"][0]["v"], str)
    for query, code in [("exchange=bybit&symbol=BTCUSDT", 400), ("exchange=hyperliquid&symbol=BTC", 400),
                        ("exchange=kraken&symbol=BTCUSDT", 400), ("exchange=binance&symbol=BTC%20USDT", 400),
                        ("exchange=binance&symbol=BTCUSDT&minutes=99", 400),
                        ("exchange=binance&symbol=BTCUSDT&seconds=7", 400),
                        ("exchange=binance&symbol=BTCUSDT&minutes=x", 400)]:
        assert client.get(f"/api/seconds?{query}").status_code == code, query
    assert "live stream" in client.get("/api/seconds?exchange=bybit&symbol=BTCUSDT").get_json()["error"]


def test_production_app_registers_route_and_chart_api_still_rejects_seconds():
    client = screener.app.test_client()
    assert client.get("/api/seconds?exchange=kraken&symbol=BTCUSDT").status_code == 400
    for interval in ("1s", "5s", "15s", "30s"):
        assert client.get(f"/api/chart?symbol=BTCUSDT&interval={interval}").status_code == 400


def test_production_route_shares_footprint_service_rate_window():
    shared = screener.app.extensions["footprint"]["get_service"]()
    assert isinstance(shared, fd.FootprintService)
    assert shared is screener.app.extensions["footprint"]["get_service"]()
