"""/api/trades/recent: raw trade backfill for big-trade bubbles (fake venues, no network)."""
from decimal import Decimal

import pytest
from flask import Flask

import footprint_data as fd
import trades_api
from tests.test_footprint_data import MIN, T0, FakeBinance, make_trades


def _svc(fake, now, **kw):
    return fd.FootprintService(request=fake, clock=lambda: now, **kw)


def test_binance_rows_are_exact_contiguous_aggtrades_with_sides():
    rows = make_trades(500, 6)  # 3000 aggTrades over 6 minutes
    now = T0 + 6 * MIN - 1
    payload = trades_api.trades_payload(_svc(FakeBinance(rows), now), "binance", "BTCUSDT", minutes=5, max_requests=10)
    assert payload["coverage"] == "exact" and payload["truncated"] is False
    got = payload["trades"]
    ids = [r[0] for r in got]
    assert ids == list(range(ids[0], ids[-1] + 1)), "contiguous ids"
    assert payload["oldest_ms"] <= now - 5 * MIN, "reaches back to the requested window"
    by_id = {r["a"]: r for r in rows}
    for ident, t, price, qty, side in got:
        raw = by_id[ident]
        assert (t, Decimal(price), Decimal(qty)) == (raw["T"], Decimal(raw["p"]), Decimal(raw["q"]))
        assert side == (-1 if raw["m"] else 1), "m=true → buyer is maker → taker SELL"
    assert payload["last_trade_id"] == rows[-1]["a"]


def test_binance_budget_cuts_oldest_and_says_so():
    rows = make_trades(1000, 6)
    now = T0 + 6 * MIN - 1
    payload = trades_api.trades_payload(_svc(FakeBinance(rows), now), "binance", "BTCUSDT", minutes=6, max_requests=2)
    assert payload["truncated"] is True
    assert payload["trades"][-1][0] == rows[-1]["a"], "newest trades are never the ones cut"
    assert any("budget" in w.lower() for w in payload["warnings"])


def test_bybit_is_partial_and_maps_sides():
    def request(exchange, path, *, params=None, payload=None):
        assert exchange == "bybit" and path == "/v5/market/recent-trade"
        return {"retCode": 0, "result": {"list": [
            {"execId": "x2", "symbol": "SOLUSDT", "price": "150.1", "size": "10", "side": "Sell", "time": str(T0 + 5)},
            {"execId": "x1", "symbol": "SOLUSDT", "price": "150.0", "size": "2.5", "side": "Buy", "time": str(T0)},
        ]}}
    payload = trades_api.trades_payload(_svc(request, T0 + 10), "bybit", "SOLUSDT", minutes=15)
    assert payload["coverage"] == "partial" and payload["count"] == 2
    assert payload["trades"] == [["x1", T0, "150", "2.5", 1], ["x2", T0 + 5, "150.1", "10", -1]]
    assert "partial" in payload["warnings"][0]


@pytest.mark.parametrize("query,status", [
    ("exchange=binance&symbol=BTCUSDT&minutes=0", 400),
    ("exchange=binance&symbol=BTCUSDT&minutes=61", 400),
    ("exchange=binance&symbol=BTCUSDT&max_requests=50", 400),
    ("exchange=kraken&symbol=BTCUSDT", 400),
    ("exchange=binance&symbol=btc%20usdt", 400),
])
def test_route_validates(query, status):
    app = Flask(__name__)
    trades_api.register_trades_routes(app, service_factory=lambda: _svc(FakeBinance(make_trades(10, 2)), T0 + 2 * MIN))
    assert app.test_client().get(f"/api/trades/recent?{query}").status_code == status


def test_route_ok_and_malformed_venue_is_502():
    app = Flask(__name__)
    trades_api.register_trades_routes(app, service_factory=lambda: _svc(FakeBinance(make_trades(10, 2)), T0 + 2 * MIN - 1))
    response = app.test_client().get("/api/trades/recent?exchange=binance&symbol=BTCUSDT&minutes=1")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["count"] > 0
    bad = Flask(__name__)
    trades_api.register_trades_routes(bad, service_factory=lambda: _svc(lambda *a, **k: {"retCode": 0, "result": {"list": [{"symbol": "SOLUSDT"}]}}, T0))
    assert bad.test_client().get("/api/trades/recent?exchange=bybit&symbol=SOLUSDT").status_code == 502
