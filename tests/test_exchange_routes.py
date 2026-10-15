"""Exchange-scoped workspace requests must not fall back to another venue."""
from unittest.mock import Mock, patch

import screener


def test_exchange_catalog_route_is_available():
    response = screener.app.test_client().get('/api/markets?exchange=unknown')
    assert response.status_code == 400


def test_native_hyperliquid_symbol_reaches_only_selected_provider():
    provider = Mock(return_value=[])
    with patch('exchange_markets.fetch_exchange_candles', provider):
        response = screener.app.test_client().get('/api/chart?exchange=hyperliquid&symbol=kBONK&interval=240')
    assert response.status_code == 200
    provider.assert_called_once()
    args = provider.call_args.args
    assert args[:3] == ('hyperliquid', 'kBONK', '240')
    assert response.json['exchange'] == 'hyperliquid'
    assert response.json['symbol'] == 'kBONK'


def test_unknown_exchange_never_fetches_candles():
    with patch.object(screener, '_klines') as legacy:
        response = screener.app.test_client().get('/api/chart?exchange=wrong&symbol=BTCUSDT&interval=240')
    assert response.status_code == 400
    legacy.assert_not_called()


def test_snapshot_supports_case_sensitive_native_symbol_only_with_explicit_exchange():
    from chart_data import build_chart_snapshot

    fetch = Mock(return_value=[])
    result = build_chart_snapshot('kBONK', '240', 10, fetch, exchange='hyperliquid')
    assert result['symbol'] == 'kBONK'
    assert result['exchange'] == 'hyperliquid'
    fetch.assert_called_once_with('kBONK', '240', 10)


def test_snapshot_never_accepts_a_different_provider_under_selected_exchange():
    import pytest
    from chart_data import build_chart_snapshot

    rows = [{'ts': 0, 'close_ts': 3600000, 'o': 100, 'h': 101, 'l': 99,
             'c': 100, 'v': 1, 'source': 'bybit', 'symbol': 'BTC'}]
    with pytest.raises(ValueError, match='selected exchange'):
        build_chart_snapshot('BTC', '60', 10, Mock(return_value=rows),
                             as_of_ms=7200000, exchange='hyperliquid')


def test_unicode_binance_instrument_is_preserved_end_to_end():
    provider = Mock(return_value=[])
    with patch('exchange_markets.fetch_exchange_candles', provider):
        response = screener.app.test_client().get('/api/chart', query_string={
            'exchange': 'binance', 'symbol': '币安人生USDT', 'interval': '240'})
    assert response.status_code == 200
    assert response.json['symbol'] == '币安人生USDT'
    assert provider.call_args.args[:2] == ('binance', '币安人生USDT')


def test_unicode_symbol_does_not_depend_on_warm_catalog_cache():
    import exchange_markets

    with patch.dict(exchange_markets._CACHE, {}, clear=True), \
            patch.object(exchange_markets, '_candles', return_value=[]) as candles:
        assert exchange_markets.fetch_exchange_candles('binance', '币安人生USDT', '240', 10) == []
    candles.assert_called_once_with('binance', '币安人生USDT', '240', 10)


def test_hyperliquid_rejects_conflicting_upstream_instrument_and_interval():
    import pytest
    import exchange_markets

    for fields in [{'s': 'ETH', 'i': '1m'}, {'s': 'BTC', 'i': '5m'}]:
        row = {'t': 0, 'o': '100', 'h': '101', 'l': '99', 'c': '100', 'v': '1', **fields}
        with patch.object(exchange_markets, '_hl', return_value=[row]), \
                pytest.raises(exchange_markets.ExchangeDataError):
            exchange_markets.fetch_exchange_candles('hyperliquid', 'BTC', '1', 10)
