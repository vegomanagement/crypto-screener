"""Liquidity endpoints: bounded read-only market observation, never order submission."""
from unittest.mock import Mock

import pytest
import screener


def test_liquidity_history_rejects_unknown_exchange():
    response = screener.app.test_client().get('/api/liquidity/history?exchange=wrong&symbol=BTCUSDT')
    assert response.status_code == 400


def test_history_uses_only_selected_market_and_never_starts_collection(tmp_path):
    from flask import Flask
    from liquidity_api import register_liquidity_routes

    store = Mock()
    store.history.return_value = {'exchange': 'hyperliquid', 'symbol': 'kBONK', 'frames': [], 'latest': None}
    collector = Mock()
    collector.status.return_value = {'collecting': False, 'last_success': None, 'error': None}
    app = Flask(__name__)
    register_liquidity_routes(app, store_path=tmp_path / 'liquidity.sqlite3',
                              service_factory=lambda path: (store, collector))
    response = app.test_client().get('/api/liquidity/history', query_string={
        'exchange': 'hyperliquid', 'symbol': 'kBONK', 'start_ms': 1, 'end_ms': 1000, 'max_frames': 20})
    assert response.status_code == 200
    assert response.json['exchange'] == 'hyperliquid'
    assert response.json['symbol'] == 'kBONK'
    store.history.assert_called_once_with('hyperliquid', 'kBONK', 1, 1000, max_frames=20)
    collector.start.assert_not_called()


def test_watch_starts_only_explicit_native_market_and_owner(tmp_path):
    from flask import Flask
    from liquidity_api import register_liquidity_routes

    collector = Mock()
    collector.watch.return_value = {'exchange': 'hyperliquid', 'symbol': 'kBONK', 'collecting': True}
    app = Flask(__name__)
    register_liquidity_routes(app, store_path=tmp_path / 'depth.sqlite3',
                              service_factory=lambda path: (Mock(), collector))
    response = app.test_client().post('/api/liquidity/watch', json={
        'exchange': 'hyperliquid', 'symbol': 'kBONK', 'owner': 'tab-1:pane-0'})
    assert response.status_code == 200
    assert response.json['collecting'] is True
    collector.watch.assert_called_once_with('hyperliquid', 'kBONK', 'tab-1:pane-0')
    collector.start.assert_called_once_with()


def test_unwatch_releases_one_owner_without_starting_worker(tmp_path):
    from flask import Flask
    from liquidity_api import register_liquidity_routes

    collector = Mock()
    collector.status.return_value = {'exchange': 'bybit', 'symbol': 'BTCUSDT', 'collecting': False}
    app = Flask(__name__)
    register_liquidity_routes(app, store_path=tmp_path / 'depth.sqlite3',
                              service_factory=lambda path: (Mock(), collector))
    response = app.test_client().post('/api/liquidity/unwatch', json={
        'exchange': 'bybit', 'symbol': 'BTCUSDT', 'owner': 'tab-1:pane-0'})
    assert response.status_code == 200
    assert response.json['collecting'] is False
    collector.unwatch.assert_called_once_with('bybit', 'BTCUSDT', 'tab-1:pane-0')
    collector.start.assert_not_called()


@pytest.mark.parametrize('route', ['watch', 'unwatch'])
@pytest.mark.parametrize('headers', [
    {'Origin': 'https://untrusted.example'}, {'Sec-Fetch-Site': 'cross-site'}])
def test_cross_site_cannot_change_collection(route, headers, tmp_path):
    from flask import Flask
    from liquidity_api import register_liquidity_routes
    factory = Mock()
    app = Flask(__name__)
    register_liquidity_routes(app, store_path=tmp_path / 'depth.sqlite3', service_factory=factory)
    response = app.test_client().post('/api/liquidity/' + route, json={
        'exchange': 'bybit', 'symbol': 'BTCUSDT', 'owner': 'tab-1'}, headers=headers)
    assert response.status_code == 403
    factory.assert_not_called()


def test_empty_history_does_not_create_database_or_worker(tmp_path):
    from flask import Flask
    from liquidity_api import register_liquidity_routes
    app = Flask(__name__)
    path = tmp_path / 'depth.sqlite3'
    register_liquidity_routes(app, store_path=path)
    response = app.test_client().get('/api/liquidity/history?exchange=bybit&symbol=BTCUSDT')
    assert response.status_code == 200
    assert response.json['frames'] == []
    assert response.json['latest'] is None
    assert response.json['status']['collecting'] is False
    assert not path.exists()


@pytest.mark.parametrize('query', [
    {'start_ms': -1}, {'end_ms': 0}, {'start_ms': 20, 'end_ms': 10},
    {'start_ms': 1, 'end_ms': 604800002}, {'max_frames': 0}, {'max_frames': 601},
    {'symbol': '../../bad'}, {'symbol': 'BTCUSDT\n'}, {'end_ms': 'nan'}])
def test_history_invalid_query_rejected_before_storage(query, tmp_path):
    from flask import Flask
    from liquidity_api import register_liquidity_routes
    factory = Mock()
    app = Flask(__name__)
    register_liquidity_routes(app, store_path=tmp_path / 'depth.sqlite3', service_factory=factory)
    params = {'exchange': 'bybit', 'symbol': 'BTCUSDT', 'start_ms': 1, 'end_ms': 1000, 'max_frames': 20}
    params.update(query)
    response = app.test_client().get('/api/liquidity/history', query_string=params)
    assert response.status_code == 400
    factory.assert_not_called()


@pytest.mark.parametrize('route', ['watch', 'unwatch'])
@pytest.mark.parametrize('delta', [
    {'exchange': []}, {'exchange': 'wrong'}, {'symbol': '../../bad'},
    {'symbol': 'BTCUSDT\n'}, {'owner': ''}, {'owner': None},
    {'owner': 'x' * 81}, {'owner': 'tab\n1'}])
def test_invalid_subscription_rejected_before_service(route, delta, tmp_path):
    from flask import Flask
    from liquidity_api import register_liquidity_routes
    app = Flask(__name__)
    factory = Mock()
    register_liquidity_routes(app, store_path=tmp_path / 'depth.sqlite3', service_factory=factory)
    payload = {'exchange': 'bybit', 'symbol': 'BTCUSDT', 'owner': 'tab:0'}
    payload.update(delta)
    response = app.test_client().post('/api/liquidity/' + route, json=payload)
    assert response.status_code == 400
    factory.assert_not_called()


def test_watch_capacity_error_maps_to_429(tmp_path):
    from flask import Flask
    from liquidity_api import register_liquidity_routes
    from liquidity_store import CapacityError

    collector = Mock()
    collector.watch.side_effect = CapacityError('collector full')
    app = Flask(__name__)
    register_liquidity_routes(app, store_path=tmp_path / 'depth.sqlite3',
                              service_factory=lambda path: (Mock(), collector))
    response = app.test_client().post('/api/liquidity/watch', json={
        'exchange': 'bybit', 'symbol': 'BTCUSDT', 'owner': 'tab-1'})
    assert response.status_code == 429


def test_symbol_regex_matches_fetch_module_rejects_leading_underscore():
    from liquidity_api import valid_identity
    assert valid_identity('bybit', '_BTCUSDT') is False
    assert valid_identity('bybit', 'BTCUSDT') is True


def test_real_end_to_end_watch_collect_history(tmp_path, monkeypatch):
    from flask import Flask
    from liquidity_api import register_liquidity_routes
    import liquidity_data
    import time as time_module

    observed_at = int(time_module.time() * 1000)

    def fake_fetch(exchange, symbol, depth=200):
        return {
            'exchange': exchange, 'symbol': symbol, 'observed_at': observed_at,
            'time': observed_at, 'bids': [[100.0, 1.0], [99.5, 2.0]],
            'asks': [[100.5, 1.5], [101.0, 2.5]], 'depth_requested': 200,
            'depth_actual': {'bids': 2, 'asks': 2}, 'sequence': '1', 'quantity_unit': 'native',
            'quote_unit': None, 'warnings': [],
        }

    monkeypatch.setattr(liquidity_data, 'fetch_depth_snapshot', fake_fetch)
    app = Flask(__name__)
    register_liquidity_routes(app, store_path=tmp_path / 'depth.sqlite3')
    client = app.test_client()

    watch = client.post('/api/liquidity/watch', json={
        'exchange': 'bybit', 'symbol': 'BTCUSDT', 'owner': 'tab-1'})
    assert watch.status_code == 200

    service = app.extensions['liquidity']['get_service']()
    store, collector = service
    deadline = time_module.monotonic() + 5
    body = None
    while time_module.monotonic() < deadline:
        history = client.get('/api/liquidity/history', query_string={
            'exchange': 'bybit', 'symbol': 'BTCUSDT',
            'start_ms': observed_at - 60_000, 'end_ms': observed_at + 60_000})
        assert history.status_code == 200
        body = history.json
        if body['samples'] >= 1:
            break
        time_module.sleep(0.05)
    collector.stop()
    assert body is not None and body['samples'] == 1
    assert body['frames'][0]['bids'] == [[100.0, 1.0], [99.5, 2.0]]
    assert body['status']['collecting'] is True

    client.post('/api/liquidity/unwatch', json={
        'exchange': 'bybit', 'symbol': 'BTCUSDT', 'owner': 'tab-1'})
