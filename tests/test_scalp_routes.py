"""/scalp page and /api/scalp/instrument with injected fake venues (no network)."""
from flask import Flask

import screener
from footprint_data import FootprintError, FootprintService
from scalp_api import register_scalp_routes


def fake_request(calls):
    def request(exchange, path, *, params=None, payload=None):
        calls.append((exchange, path, params, payload))
        if path == "/fapi/v1/exchangeInfo":
            return {"symbols": [{"symbol": "BTCUSDT", "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.10"}]}]}
        if path == "/v5/market/instruments-info":
            return {"retCode": 0, "result": {"list": [{"symbol": "ETHUSDT",
                                                        "priceFilter": {"tickSize": "0.01"}}]}}
        if path == "/info":
            return {"universe": [{"name": "BTC", "szDecimals": 5}]}
        raise AssertionError(path)
    return request


def client_for(service):
    app = Flask(__name__, template_folder=screener.app.template_folder)
    register_scalp_routes(app, service_getter=lambda: service)
    return app.test_client()


def test_scalp_page_is_served_with_its_modules_in_dependency_order():
    client = screener.app.test_client()
    response = client.get('/scalp')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    order = ['/static/market-stream.js', '/static/footprint.js', '/static/orderbook.js', '/static/levels.js',
             '/static/scalp-alerts.js', '/static/scalp-render.js', '/static/scalp-settings.js', '/static/scalp-column.js',
             '/static/scalp-dom.js', '/static/scalp-alerts-ui.js', '/static/scalp-levels-ui.js', '/static/scalp-alerts-panel.js',
             '/static/scalp.js']
    positions = [html.index(src) for src in order]
    assert positions == sorted(positions)
    assert '/static/scalp.css' in html
    assert 'href="/ui"' in html
    assert 'VIEW ONLY' in html
    for asset in order[2:] + ['/static/scalp.css']:
        assert client.get(asset).status_code == 200
    assert 'vataga' not in html.lower()
    for asset in order[2:]:
        assert 'vataga' not in client.get(asset).get_data(as_text=True).lower()


def test_workspace_header_links_to_scalp_page():
    html = screener.app.test_client().get('/ui').get_data(as_text=True)
    assert 'href="/scalp"' in html


def test_instrument_returns_venue_tick_and_caches_metadata():
    calls = []
    client = client_for(FootprintService(request=fake_request(calls), clock=lambda: 1))
    first = client.get('/api/scalp/instrument?exchange=binance&symbol=BTCUSDT')
    assert first.status_code == 200
    assert first.json == {"exchange": "binance", "symbol": "BTCUSDT", "tick_size": "0.1",
                          "sz_decimals": None, "tick_rule": "fixed"}
    assert 'max-age' in first.headers['Cache-Control']
    client.get('/api/scalp/instrument?exchange=binance&symbol=BTCUSDT')
    assert len(calls) == 1, 'metadata is cached'
    bybit = client.get('/api/scalp/instrument?exchange=bybit&symbol=ETHUSDT').json
    assert bybit['tick_size'] == '0.01'
    hl = client.get('/api/scalp/instrument?exchange=hyperliquid&symbol=BTC').json
    assert hl['tick_size'] is None and hl['sz_decimals'] == 5
    assert calls[-1][3] == {"type": "meta"}


def test_instrument_rejects_bad_identity_without_network_and_hides_upstream_errors():
    calls = []
    client = client_for(FootprintService(request=fake_request(calls), clock=lambda: 1))
    assert client.get('/api/scalp/instrument?exchange=kraken&symbol=BTCUSDT').status_code == 400
    assert client.get('/api/scalp/instrument?exchange=binance&symbol=BTC%20USDT').status_code == 400
    assert calls == []
    missing = client.get('/api/scalp/instrument?exchange=binance&symbol=ETHUSDT')
    assert missing.status_code == 502
    assert missing.json == {"error": "binance: instrument metadata unavailable"}

    class Boom:
        def instrument(self, exchange, symbol):
            raise FootprintError("binance: instrument metadata unavailable")
    assert client_for(Boom()).get(
        '/api/scalp/instrument?exchange=binance&symbol=BTCUSDT').status_code == 502


def test_production_app_shares_the_footprint_service_for_instrument_metadata():
    client = screener.app.test_client()
    assert client.get('/api/scalp/instrument?exchange=binance&symbol=bad%20sym').status_code == 400
    service = screener.app.extensions['footprint']['get_service']()
    assert hasattr(service, 'instrument')
