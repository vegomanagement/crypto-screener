"""Single ticker lookup must reuse the existing bulk catalog cache, not fetch per-coin."""
from unittest.mock import Mock, patch

import exchange_markets
import screener


def test_get_ticker_reuses_cached_catalog_without_extra_network_calls():
    catalog = {'exchange': 'bybit', 'as_of': 123, 'cached': True, 'warnings': [],
               'markets': [{'symbol': 'BTCUSDT', 'price': 50000.5, 'change_24h': 1.2}]}
    with patch.object(exchange_markets, 'list_exchange_markets', Mock(return_value=catalog)) as bulk:
        ticker = exchange_markets.get_ticker('bybit', 'BTCUSDT')
    bulk.assert_called_once_with('bybit')
    assert ticker == {'symbol': 'BTCUSDT', 'price': 50000.5, 'change_24h': 1.2, 'as_of': 123}


def test_get_ticker_returns_none_for_unknown_or_delisted_symbol():
    catalog = {'exchange': 'bybit', 'as_of': 1, 'cached': False, 'warnings': [], 'markets': []}
    with patch.object(exchange_markets, 'list_exchange_markets', Mock(return_value=catalog)):
        assert exchange_markets.get_ticker('bybit', 'NOPEUSDT') is None


def test_ticker_route_exposes_single_native_symbol_price():
    catalog = {'exchange': 'hyperliquid', 'as_of': 99, 'cached': True, 'warnings': [],
               'markets': [{'symbol': 'kBONK', 'price': 0.0031, 'change_24h': -2.5}]}
    with patch.object(exchange_markets, 'list_exchange_markets', Mock(return_value=catalog)):
        response = screener.app.test_client().get(
            '/api/ticker', query_string={'exchange': 'hyperliquid', 'symbol': 'kBONK'})
    assert response.status_code == 200
    assert response.json == {'symbol': 'kBONK', 'price': 0.0031, 'change_24h': -2.5, 'as_of': 99}


def test_ticker_route_rejects_unsupported_exchange():
    response = screener.app.test_client().get(
        '/api/ticker', query_string={'exchange': 'wrong', 'symbol': 'BTCUSDT'})
    assert response.status_code == 400
