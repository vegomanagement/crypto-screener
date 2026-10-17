"""Positioning routes: validation, wiring, demo page — with a stubbed service (no network)."""
from flask import Flask

import screener
from positioning_api import register_positioning_routes, valid_query


class StubService:
    def __init__(self):
        self.calls = []

    def snapshot(self, exchange, symbol, period, owner="positioning", watch=True):
        self.calls.append(("snapshot", exchange, symbol, period, owner, watch))
        return {"exchange": exchange, "symbol": symbol, "period": period}

    def whale_view(self, coin, owner="positioning", watch=True):
        self.calls.append(("whales", coin, owner, watch))
        if coin == "FULL":
            raise ValueError("capacity")
        return {"coin": coin, "label": "Hyperliquid top-50 accounts by account value"}


def make_app(stub):
    app = Flask(__name__, template_folder=str(__import__("pathlib").Path(screener.__file__).parent / "templates"))
    register_positioning_routes(app, service_factory=lambda: stub)
    return app.test_client()


def test_registered_in_screener_and_rejects_bad_input_without_network():
    client = screener.app.test_client()
    assert client.get("/api/positioning?exchange=okx&symbol=BTCUSDT").status_code == 400
    assert client.get("/api/positioning?exchange=binance&symbol=btc/usdt").status_code == 400
    assert client.get("/api/positioning?exchange=binance&symbol=BTCUSDT&period=7m").status_code == 400
    assert client.get("/api/positioning/whales?coin=../x").status_code == 400


def test_valid_query_rules():
    assert valid_query("binance", "BTCUSDT", "1h")
    assert valid_query("hyperliquid", "kPEPE", "5m")
    assert not valid_query("bybit", "btcusdt", "1h")
    assert not valid_query("binance", "BTCUSDT", "2h")      # Bybit has no 2h; periods are the common set
    assert not valid_query("binance", "B" * 40, "1h")


def test_snapshot_route_passes_validated_params():
    stub = StubService()
    client = make_app(stub)
    resp = client.get("/api/positioning?exchange=bybit&symbol=ETHUSDT&period=15m&owner=card-1&watch=0")
    assert resp.status_code == 200 and resp.get_json()["symbol"] == "ETHUSDT"
    assert stub.calls[-1] == ("snapshot", "bybit", "ETHUSDT", "15m", "card-1", False)
    assert client.get("/api/positioning?exchange=bybit&symbol=ETHUSDT&watch=2").status_code == 400
    assert client.get("/api/positioning?exchange=bybit&symbol=ETHUSDT&owner=a%20b").status_code == 400


def test_whales_route_and_capacity():
    stub = StubService()
    client = make_app(stub)
    resp = client.get("/api/positioning/whales?coin=BTC")
    assert resp.status_code == 200 and "top-50" in resp.get_json()["label"]
    assert stub.calls[-1] == ("whales", "BTC", "positioning", True)
    assert client.get("/api/positioning/whales?coin=FULL").status_code == 429


def test_upstream_failure_is_502_without_details():
    class Broken(StubService):
        def snapshot(self, *a, **k):
            raise RuntimeError("secret internals")

    resp = make_app(Broken()).get("/api/positioning?exchange=binance&symbol=BTCUSDT")
    assert resp.status_code == 502 and "secret" not in resp.get_data(as_text=True)


def test_demo_page_mounts_panel_module():
    html = make_app(StubService()).get("/positioning").get_data(as_text=True)
    assert "/static/positioning-panel.js" in html and "PositioningPanel" in html
