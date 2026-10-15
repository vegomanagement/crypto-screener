"""Chart context route remains read-only and strictly exchange-scoped."""
from unittest.mock import patch

import screener


def test_context_rejects_unknown_exchange_without_fetching():
    response = screener.app.test_client().get('/api/chart/context?exchange=wrong&symbol=BTCUSDT')
    assert response.status_code == 400


def test_context_preserves_native_symbol_and_metadata():
    payload = {'exchange': 'hyperliquid', 'symbol': 'kBONK', 'as_of': 100,
               'funding_rate': 0.0001, 'funding_interval_hours': 1,
               'open_interest': 25, 'open_interest_unit': 'kBONK',
               'mark_price': 0.003, 'index_price': 0.003, 'warnings': [],
               'ema_bias': {'60': 'mixed', '240': 'bullish', 'D': 'insufficient'}}
    with patch('exchange_markets.get_exchange_context', return_value=payload, create=True) as fetch:
        response = screener.app.test_client().get('/api/chart/context?exchange=hyperliquid&symbol=kBONK')
    assert response.status_code == 200
    assert response.json == payload
    fetch.assert_called_once_with('hyperliquid', 'kBONK')


def test_context_failure_never_falls_back_to_legacy_market():
    with patch('exchange_markets.get_exchange_context', side_effect=RuntimeError('upstream failure'), create=True):
        response = screener.app.test_client().get('/api/chart/context?exchange=binance&symbol=BTCUSDT')
    assert response.status_code == 502
    assert response.json['exchange'] == 'binance'
