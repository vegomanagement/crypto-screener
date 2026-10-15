"""Offline regressions for the public-exchange candle contract."""
from unittest.mock import Mock, patch

import pytest

import screener


def test_bybit_candles_preserve_open_times_and_exclude_unclosed_bar():
    response = Mock()
    response.json.return_value = {
        "retCode": 0,
        "result": {"list": [
            ["28800000", "102", "105", "101", "104", "5", "520"],
            ["14400000", "100", "103", "99", "102", "10", "1020"],
        ]},
    }
    with patch.object(screener.requests, "get", return_value=response), \
            patch.object(screener.time, "time", return_value=36000):
        rows = screener._klines("BTCUSDT", "240", 10)
    assert [r.get("ts") for r in rows] == [14400000]
    assert rows[0]["close_ts"] == 28800000
    assert rows[0]["source"] == "bybit"
    assert rows[0]["closed"] is True
    assert rows[0]["amount"] == 1020


@pytest.mark.parametrize("provider", ["binance", "hl"])
def test_fallback_candles_have_the_same_closed_timestamp_contract(provider):
    response = Mock()
    if provider == "binance":
        response.json.return_value = [
            [14400000, "100", "103", "99", "102", "10", 28799999, "1020"],
            [28800000, "102", "105", "101", "104", "5", 43199999, "520"],
        ]
        function, method = screener._klines_binance, "get"
    else:
        response.json.return_value = [
            {"t": 14400000, "o": "100", "h": "103", "l": "99", "c": "102", "v": "10"},
            {"t": 28800000, "o": "102", "h": "105", "l": "101", "c": "104", "v": "5"},
        ]
        function, method = screener._klines_hl, "post"
    with patch.object(screener.requests, method, return_value=response), \
            patch.object(screener.time, "time", return_value=36000):
        rows = function("BTCUSDT", "240", 10)
    assert [r.get("ts") for r in rows] == [14400000]
    assert rows[0]["source"] == provider
    assert rows[0]["close_ts"] == 28800000


def test_candle_api_preserves_exchange_time_and_source_across_reloads():
    rows = [{"ts": 14400000, "close_ts": 28800000, "o": 100, "h": 103,
             "l": 99, "c": 102, "v": 10, "source": "bybit", "closed": True}]
    client = screener.app.test_client()
    with patch.object(screener, "_klines", return_value=rows):
        first = client.get("/api/klines?symbol=BTCUSDT&interval=240").get_json()
        second = client.get("/api/klines?symbol=BTCUSDT&interval=240").get_json()
    assert first["klines"][0]["ts"] == second["klines"][0]["ts"] == 14400000
    assert first["source"] == "bybit"
    assert first["klines"][0]["close_ts"] == 28800000


@pytest.mark.parametrize("route", ["/api/klines", "/api/zones"])
def test_unknown_interval_rejected_before_any_provider_call(route):
    with patch.object(screener, "_klines", return_value=[]) as fetch:
        response = screener.app.test_client().get(route + "?symbol=BTCUSDT&interval=banana")
    assert response.status_code == 400
    fetch.assert_not_called()


@pytest.mark.parametrize("fault", ["duplicate", "gap", "nan", "impossible_ohlc", "negative_volume"])
def test_invalid_candle_series_is_rejected_instead_of_scored(fault):
    from market_candles import closed_candles

    rows = [{"ts": 0, "o": 100, "h": 103, "l": 99, "c": 102, "v": 10},
            {"ts": 3600000, "o": 102, "h": 104, "l": 100, "c": 103, "v": 12}]
    if fault == "duplicate":
        rows[1]["ts"] = 0
    elif fault == "gap":
        rows[1]["ts"] = 7200000
    elif fault == "nan":
        rows[1]["c"] = float("nan")
    elif fault == "impossible_ohlc":
        rows[1]["l"] = 105
    else:
        rows[1]["v"] = -1
    with pytest.raises(ValueError):
        closed_candles(rows, "60", source="bybit", symbol="BTCUSDT", as_of_ms=14400000)


def test_vwap_uses_real_utc_session_times_not_a_count_from_now():
    from datetime import datetime, timezone

    now = datetime(2026, 1, 6, 2, tzinfo=timezone.utc)
    start = int(datetime(2026, 1, 6, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [{"ts": start - 3600000, "h": 100, "l": 100, "c": 100, "v": 1},
            {"ts": start, "h": 200, "l": 200, "c": 200, "v": 1},
            {"ts": start + 3600000, "h": 200, "l": 200, "c": 200, "v": 1}]
    with patch.object(screener, "datetime") as clock:
        clock.now.return_value = now
        output = screener.compute_vwap(rows)
    assert output["daily"]["vwap"] == 200


def test_market_api_preserves_actual_producer_schema():
    market = {"price": 102, "ema_biases": {"1H": "bull", "4H": "bear", "1D": "bull"},
              "bybit": {"oi": 42}, "vwap": {"daily": {"vwap": 101}, "weekly": {"vwap": 99}}}
    with patch.object(screener, "fetch_market", return_value=market):
        response = screener.app.test_client().get("/api/market?symbol=BTCUSDT")
    assert response.status_code == 200
    output = response.get_json()
    assert output["ema_bias"] == {"1h": "bull", "4h": "bear", "1d": "bull"}
    assert output["open_int"] == 42
    assert output["vwap"] == 101
    assert output["vwap_bands"] == market["vwap"]
