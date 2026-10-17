"""Forming (unclosed) candle preview: real exchange values, honest scope limits."""
from unittest.mock import Mock, patch

import pytest

import exchange_markets
import screener


def test_forming_candle_uses_the_most_recent_still_open_bar():
    now = 1_000_050_000
    raw = [
        {"ts": now - 90_000, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "10"},
        {"ts": now - 30_000, "o": "1.5", "h": "1.9", "l": "1.4", "c": "1.6", "v": "7"},
    ]
    with patch("exchange_markets.time.time", return_value=now / 1000), \
            patch.object(exchange_markets, "_bybit", return_value={"list": [
                [str(r["ts"]), r["o"], r["h"], r["l"], r["c"], r["v"]] for r in raw
            ]}):
        candle = exchange_markets.fetch_forming_candle("bybit", "BTCUSDT", "1")
    assert candle["ts"] == now - 30_000
    assert candle["close_ts"] == now + 30_000
    assert candle["is_forming"] is True
    assert candle["c"] == 1.6


def test_forming_candle_returns_none_when_boundary_just_rolled_over():
    now = 1_000_020_000
    raw = [{"ts": now - 180_000, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "10"}]
    with patch("exchange_markets.time.time", return_value=now / 1000), \
            patch.object(exchange_markets, "_bybit", return_value={"list": [
                [str(r["ts"]), r["o"], r["h"], r["l"], r["c"], r["v"]] for r in raw
            ]}):
        assert exchange_markets.fetch_forming_candle("bybit", "BTCUSDT", "1") is None


def test_forming_candle_unsupported_for_aggregated_intervals_without_network():
    bybit = Mock()
    with patch.object(exchange_markets, "_bybit", bybit):
        for interval in ["10", "12", "45", "180", "480", "720", "2D", "3D", "M"]:
            assert exchange_markets.fetch_forming_candle("bybit", "BTCUSDT", interval) is None
    bybit.assert_not_called()


def test_forming_candle_supports_hyperliquid_native_3m():
    raw = [{"t": 1_000_080_000, "s": "BTC", "i": "3m",
            "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "0"}]
    with patch("exchange_markets.time.time", return_value=1_000_110), \
            patch.object(exchange_markets, "_hl", return_value=raw) as hl:
        candle = exchange_markets.fetch_forming_candle("hyperliquid", "BTC", "3")
    assert candle["ts"] == raw[0]["t"]
    assert hl.call_args.kwargs["req"]["interval"] == "3m"


@pytest.mark.parametrize("field", list("ohlcv"))
def test_hyperliquid_forming_rejects_boolean_numeric_values(field):
    row = {"t": 1_000_020_000, "s": "BTC", "i": "1m", "o": 1, "h": 2,
           "l": 0.5, "c": 1.5, "v": 0, field: True}
    with patch("exchange_markets.time.time", return_value=1_000_050), \
            patch.object(exchange_markets, "_hl", return_value=[row]), \
            pytest.raises(exchange_markets.ExchangeDataError):
        exchange_markets.fetch_forming_candle("hyperliquid", "BTC", "1")


@pytest.mark.parametrize("timestamps", [
    [1_000_080_000],  # future open
    [1_000_020_000, 1_000_020_000],  # duplicate
    [999_900_000, 1_000_020_000],  # missing adjacent bar
    [1_000_020_001], ["1000020000.5"], [True], [-60_000],
])
def test_forming_rejects_invalid_timestamps(timestamps):
    rows = [[ts, "1", "2", "0.5", "1.5", "0"] for ts in timestamps]
    with patch("exchange_markets.time.time", return_value=1_000_050), \
            patch.object(exchange_markets, "_bybit", return_value={"list": rows}), \
            pytest.raises(exchange_markets.ExchangeDataError):
        exchange_markets.fetch_forming_candle("bybit", "BTCUSDT", "1")


def test_live_chart_route_reports_null_candle_honestly_for_aggregated_interval():
    response = screener.app.test_client().get(
        "/api/chart/live", query_string={"exchange": "bybit", "symbol": "BTCUSDT", "interval": "45"})
    assert response.status_code == 200
    assert response.json["candle"] is None


def test_live_chart_route_rejects_unsupported_exchange():
    response = screener.app.test_client().get(
        "/api/chart/live", query_string={"exchange": "wrong", "symbol": "BTCUSDT", "interval": "1"})
    assert response.status_code == 400


def test_live_chart_route_returns_real_forming_candle():
    fake = {"ts": 123, "close_ts": 456, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 9,
            "is_forming": True, "source": "bybit", "symbol": "BTCUSDT", "interval": "1"}
    with patch("exchange_markets.fetch_forming_candle", return_value=fake):
        response = screener.app.test_client().get(
            "/api/chart/live", query_string={"exchange": "bybit", "symbol": "BTCUSDT", "interval": "1"})
    assert response.status_code == 200
    assert response.json["candle"] == fake
    assert response.json["exchange"] == "bybit"


@pytest.mark.parametrize("raw", [None, {}, "oops", [[1]], [{"0": 1}],
                                 [[1_000_020_000, True, 2, 0.5, 1, 1]]])
def test_forming_requires_well_typed_candle_arrays(raw):
    with patch("exchange_markets.time.time", return_value=1_000_050), \
            patch.object(exchange_markets, "_bybit", return_value={"list": raw}), \
            pytest.raises(exchange_markets.ExchangeDataError):
        exchange_markets.fetch_forming_candle("bybit", "BTCUSDT", "1")


@pytest.mark.parametrize("field,value", [("o", 0), ("c", "nan"), ("v", -1),
                                        ("h", 1), ("l", 2), ("v", "inf")])
def test_forming_rejects_bad_ohlcv_even_in_closed_neighbor(field, value):
    row = {"o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 0, field: value}
    raw = [[999_960_000, *(row[k] for k in "ohlcv")],
           [1_000_020_000, 1, 2, 0.5, 1.5, 0]]
    with patch("exchange_markets.time.time", return_value=1_000_050), \
            patch.object(exchange_markets, "_bybit", return_value={"list": raw}), \
            pytest.raises(exchange_markets.ExchangeDataError):
        exchange_markets.fetch_forming_candle("bybit", "BTCUSDT", "1")


@pytest.mark.parametrize("conflict", [{"s": "ETH"}, {"i": "5m"}, {"source": "bybit"},
                                      {"symbol": "ETH"}, {"interval": "5"},
                                      {"T": 1_000_020_000}])
def test_hyperliquid_forming_rejects_provenance_and_close_time_conflicts(conflict):
    row = {"t": 1_000_020_000, "s": "BTC", "i": "1m", "o": "1", "h": "2",
           "l": "0.5", "c": "1.5", "v": "0", **conflict}
    with patch("exchange_markets.time.time", return_value=1_000_050), \
            patch.object(exchange_markets, "_hl", return_value=[row]), \
            pytest.raises(exchange_markets.ExchangeDataError):
        exchange_markets.fetch_forming_candle("hyperliquid", "BTC", "1")


@pytest.mark.parametrize("conflict", [{"symbol": "ETHUSDT"}, {"category": "inverse"},
                                      {"interval": "5"}, {"source": "binance"}])
def test_bybit_forming_rejects_provenance_conflicts(conflict):
    result = {"list": [[1_000_020_000, 1, 2, 0.5, 1.5, 0]], **conflict}
    with patch("exchange_markets.time.time", return_value=1_000_050), \
            patch.object(exchange_markets, "_bybit", return_value=result), \
            pytest.raises(exchange_markets.ExchangeDataError):
        exchange_markets.fetch_forming_candle("bybit", "BTCUSDT", "1")


def test_forming_excludes_exact_close_boundary_and_week_uses_utc_monday():
    with patch("exchange_markets.time.time", return_value=1_000_020), \
            patch.object(exchange_markets, "_bybit", return_value={
                "list": [[999_960_000, 1, 2, 0.5, 1.5, 0]]}):
        assert exchange_markets.fetch_forming_candle("bybit", "BTCUSDT", "1") is None
    monday = 345_600_000  # 1970-01-05 Monday, not Unix Thursday epoch.
    with patch("exchange_markets.time.time", return_value=(monday + 30_000) / 1000), \
            patch.object(exchange_markets, "_bybit", return_value={
                "list": [[monday, 1, 2, 0.5, 1.5, 0]]}):
        candle = exchange_markets.fetch_forming_candle("bybit", "BTCUSDT", "W")
    assert candle["ts"] == monday
    assert candle["close_ts"] == monday + 604_800_000


@pytest.mark.parametrize("raw", [{}, None, [[1_000_020_000, 1, 2, 0.5, 1.5, 0, 0]]])
def test_binance_forming_rejects_wrong_response_type_or_close_time(raw):
    with patch("exchange_markets.time.time", return_value=1_000_050), \
            patch.object(exchange_markets, "_request", return_value=raw), \
            pytest.raises(exchange_markets.ExchangeDataError):
        exchange_markets.fetch_forming_candle("binance", "BTCUSDT", "1")
