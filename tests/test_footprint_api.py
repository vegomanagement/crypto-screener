"""GET /api/footprint with an injected fake venue (no network)."""
from decimal import Decimal

import pytest
from flask import Flask

import screener
from footprint_api import register_footprint_routes
from footprint_data import FootprintError, FootprintService
from tests.test_footprint_data import MIN, T0, FakeBinance, make_trades


def client_for(service):
    app = Flask(__name__)
    register_footprint_routes(app, service_factory=lambda: service)
    return app.test_client()


def fake_service(rows=None, now=T0 + 6 * MIN + 1):
    return FootprintService(request=FakeBinance(rows or make_trades(200, 7, start=T0 - MIN)),
                            clock=lambda: now)


def test_production_app_registers_footprint_route_and_rejects_bad_identity():
    client = screener.app.test_client()
    assert client.get('/api/footprint?exchange=kraken&symbol=BTCUSDT').status_code == 400
    assert client.get('/api/footprint?exchange=binance&symbol=BTC%20USDT').status_code == 400


def test_route_returns_exact_string_bars_with_coverage_and_no_store():
    response = client_for(fake_service()).get('/api/footprint', query_string={
        'exchange': 'binance', 'symbol': 'BTCUSDT', 'interval': '1', 'limit': '5',
        'include_forming': '0'})
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    body = response.json
    assert body['tick_size'] == '0.1' and body['group_mode'] == 'auto'
    assert body['coverage']['complete'] == 5 and body['truncated'] is False
    assert [b['time'] for b in body['bars']] == [(T0 + i * MIN) // 1000 for i in range(1, 6)]
    bar = body['bars'][0]
    assert isinstance(bar['total'], str) and Decimal(bar['total']) > 0
    assert sum(Decimal(lv['bid']) + Decimal(lv['ask']) for lv in bar['levels']) == Decimal(bar['total'])
    assert body['convention'].startswith('ask = buyer-initiated')


def test_route_passes_settings_and_manual_grouping():
    seen = {}

    class Spy:
        def footprint(self, *args, **kwargs):
            seen['args'], seen['kwargs'] = args, kwargs
            return {'bars': []}

    response = client_for(Spy()).get('/api/footprint', query_string={
        'exchange': 'hyperliquid', 'symbol': 'xyz:XYZ100', 'interval': '5', 'limit': '30',
        'group_ticks': '4', 'imbalance_ratio': '250', 'include_zero': '1', 'stacked_min': '4',
        'va_pct': '68', 'input': 'trades'})
    assert response.status_code == 200
    assert seen['args'] == ('hyperliquid', 'xyz:XYZ100', '5')
    kw = seen['kwargs']
    assert kw['limit'] == 30 and kw['group_ticks'] == 4 and kw['include_forming'] is True
    assert kw['settings']['imbalance_ratio'] == Decimal(250) and kw['settings']['include_zero'] is True
    assert (kw['settings']['stacked_min'], kw['settings']['va_pct'], kw['settings']['input']) == (4, 68, 'trades')


@pytest.mark.parametrize('query', [
    {'limit': 'x'}, {'limit': '0'}, {'limit': '999'}, {'interval': 'D'}, {'group_ticks': '-1'},
    {'group_ticks': '0'}, {'include_forming': 'yes'}, {'include_zero': 'maybe'}, {'va_pct': '0'},
    {'imbalance_ratio': '50'}, {'stacked_min': '1'}, {'input': 'orderbook'}, {'min_delta': 'nan'},
])
def test_route_rejects_invalid_queries_with_400(query):
    response = client_for(fake_service()).get('/api/footprint', query_string={
        'exchange': 'binance', 'symbol': 'BTCUSDT', **query})
    assert response.status_code == 400
    assert response.json['error'].startswith('invalid footprint query')


def test_upstream_failure_is_502_without_body_leak():
    class Down:
        def footprint(self, *a, **k):
            raise FootprintError('binance: public API unavailable')

    response = client_for(Down()).get('/api/footprint?exchange=binance&symbol=BTCUSDT')
    assert response.status_code == 502
    assert response.json == {'error': 'binance: public API unavailable'}
