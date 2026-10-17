"""Offline public context fixtures: no venue substitution or USD-size inference."""
from unittest.mock import patch

import pytest

import exchange_markets as em


@pytest.fixture(autouse=True)
def clear_context_cache():
    if hasattr(em, "_CONTEXT_CACHE"):
        em._CONTEXT_CACHE.clear()
    with patch.dict(em._META_CACHE, {}, clear=True), patch.dict(em._CACHE, {}, clear=True):
        yield
    if hasattr(em, "_CONTEXT_CACHE"):
        em._CONTEXT_CACHE.clear()


def test_bybit_native_context_fraction_units_and_targeted_metadata():
    ticker = {"symbol": "BTCUSDC", "fundingRate": "-0.000125", "openInterest": "12.5",
              "markPrice": "100", "indexPrice": "101"}
    metadata = {"symbol": "BTCUSDC", "baseCoin": "BTC", "fundingInterval": "240"}
    with patch.object(em, "_bybit", side_effect=[{"list": [ticker]}, {"list": [metadata]}]) as api, \
            patch.object(em, "fetch_exchange_candles", return_value=[]):
        result = em.get_exchange_context("bybit", "BTCUSDC")
    assert result["exchange"] == "bybit" and result["symbol"] == "BTCUSDC"
    assert result["funding_rate"] == -0.000125
    assert result["funding_interval_hours"] == 4
    assert result["open_interest"] == 12.5
    assert result["open_interest_unit"] == "base_asset"
    assert result["mark_price"] == 100 and result["index_price"] == 101
    assert api.call_args_list[0].kwargs == {"symbol": "BTCUSDC"}
    assert api.call_args_list[1].kwargs == {"symbol": "BTCUSDC"}
    assert result["ema_bias"] == {"60": "insufficient", "240": "insufficient", "D": "insufficient"}


def test_binance_preserves_native_oi_and_exact_quote_symbol_without_unit_guessing():
    premium = {"symbol": "BTCUSDC", "lastFundingRate": "0.0001",
               "markPrice": "60000", "indexPrice": "60010"}
    interest = {"symbol": "BTCUSDC", "openInterest": "19.125"}
    with patch.object(em, "_request", side_effect=[premium, interest]) as api, \
            patch.object(em, "fetch_exchange_candles", return_value=[]):
        result = em.get_exchange_context("binance", "BTCUSDC")
    assert result["funding_rate"] == 0.0001
    assert result["open_interest"] == 19.125  # Never multiplied by mark price.
    assert result["open_interest_unit"] is None
    assert result["funding_interval_hours"] is None  # No invented 8h default.
    assert result["warnings"]
    assert [c.args[1] for c in api.call_args_list] == [
        "/fapi/v1/premiumIndex", "/fapi/v1/openInterest"]
    assert all(c.kwargs["params"] == {"symbol": "BTCUSDC"} for c in api.call_args_list)


@pytest.mark.parametrize("symbol,dex,unit", [("BTC", "", "base_asset"),
                                          ("xyz:TSLA", "xyz", None)])
def test_hyperliquid_maps_original_unfiltered_universe_index(symbol, dex, unit):
    raw = [{"universe": [{"name": "xyz:OLD" if dex else "OLD", "isDelisted": True},
                         {"name": symbol}]},
           [{"funding": "9", "openInterest": "999", "markPx": "999", "oraclePx": "999"},
            {"funding": "0.0000125", "openInterest": "12.208", "markPx": "465.13",
             "oraclePx": "450.78"}]]
    with patch.object(em, "_hl", return_value=raw) as api, \
            patch.object(em, "fetch_exchange_candles", return_value=[]):
        result = em.get_exchange_context("hyperliquid", symbol)
    api.assert_called_once_with("metaAndAssetCtxs", dex=dex)
    assert result["funding_rate"] == 0.0000125
    assert result["funding_interval_hours"] == 1
    assert result["open_interest"] == 12.208
    assert result["open_interest_unit"] == unit
    assert result["mark_price"] == 465.13 and result["index_price"] == 450.78


def _closed_rows(values, tf="60"):
    duration = em.interval_ms(tf)
    end = 200 * duration
    return [{"ts": end - (len(values) - i) * duration,
             "o": value, "h": value, "l": value, "c": value, "v": 0}
            for i, value in enumerate(values)]


@pytest.mark.parametrize("values,expected", [
    (list(range(1, 81)), "bullish"), (list(range(80, 0, -1)), "bearish"),
    ([10] * 80, "mixed"), ([10] * 27, "insufficient"),
])
def test_ema_bias_uses_80_closed_candles_per_timeframe(values, expected):
    ticker = {"symbol": "BTCUSDT", "fundingIntervalHour": "8", "fundingRate": "0",
              "openInterest": "0", "markPrice": "100", "indexPrice": "100"}
    def history(exchange, symbol, tf, count):
        assert (exchange, symbol, count) == ("bybit", "BTCUSDT", 80)
        return _closed_rows(values, tf)
    with patch.object(em, "_bybit", return_value={"list": [ticker]}), \
            patch.object(em, "fetch_exchange_candles", side_effect=history) as fetch, \
            patch.object(em.time, "time", return_value=200 * 86_400):
        result = em.get_exchange_context("bybit", "BTCUSDT")
    assert result["ema_bias"] == {"60": expected, "240": expected, "D": expected}
    assert fetch.call_count == 3


def test_ema_is_sma_seeded_not_first_value_seeded():
    assert em._sma_seeded_ema([2, 8, 2], 3) == 4
    assert em._sma_seeded_ema([2, 8, 2, 10], 3) == 7


def test_ema_failure_is_isolated_and_forming_rows_do_not_bias():
    ticker = {"symbol": "BTCUSDT", "fundingIntervalHour": "8"}
    rows = _closed_rows([10] * 80, "D")
    rows.append({"ts": 200 * 86_400_000, "o": 1000, "h": 1000, "l": 1000,
                 "c": 1000, "v": 0})  # Still forming; must not affect bias.
    with patch.object(em, "_bybit", return_value={"list": [ticker]}), \
            patch.object(em, "fetch_exchange_candles", side_effect=[
                em.ExchangeDataError("offline"), [], rows]), \
            patch.object(em.time, "time", return_value=200 * 86_400 + 30):
        result = em.get_exchange_context("bybit", "BTCUSDT")
    assert result["ema_bias"] == {"60": "unavailable", "240": "insufficient", "D": "mixed"}
    assert any("60" in warning and "unavailable" in warning for warning in result["warnings"])


def test_context_cache_30s_is_complete_isolated_and_defensively_copied():
    ticker = {"symbol": "BTCUSDT", "fundingIntervalHour": "8", "fundingRate": "0",
              "openInterest": "2", "markPrice": "3", "indexPrice": "4"}
    with patch.object(em, "_bybit", return_value={"list": [ticker]}) as api, \
            patch.object(em, "fetch_exchange_candles", return_value=[]) as history, \
            patch.object(em.time, "time", return_value=100) as clock:
        first = em.get_exchange_context("bybit", "BTCUSDT")
        first["warnings"].append("caller mutation")
        first["ema_bias"]["D"] = "bullish"
        clock.return_value = 129.999
        cached = em.get_exchange_context("bybit", "BTCUSDT")
        assert cached["as_of"] == 100_000 and not cached["warnings"]
        assert cached["ema_bias"]["D"] == "insufficient"
        assert api.call_count == 1 and history.call_count == 3
        clock.return_value = 130
        refreshed = em.get_exchange_context("bybit", "BTCUSDT")
        assert refreshed["as_of"] == 130_000
        assert api.call_count == 2 and history.call_count == 6
        api.side_effect = em.ExchangeDataError("upstream unavailable")
        clock.return_value = 160
        with pytest.raises(em.ExchangeDataError):
            em.get_exchange_context("bybit", "BTCUSDT")  # Never serve stale on failure.


def test_bybit_reuses_fresh_metadata_without_loading_catalog():
    ticker = {"symbol": "BTCUSDT", "fundingRate": "0", "openInterest": "0",
              "markPrice": "3", "indexPrice": "4"}
    em._META_CACHE["bybit"] = (100, [{"symbol": "BTCUSDT", "fundingInterval": "60"}])
    with patch.object(em, "_bybit", return_value={"list": [ticker]}) as api, \
            patch.object(em, "list_exchange_markets") as catalog, \
            patch.object(em, "fetch_exchange_candles", return_value=[]), \
            patch.object(em.time, "time", return_value=101):
        result = em.get_exchange_context("bybit", "BTCUSDT")
    assert result["funding_interval_hours"] == 1
    api.assert_called_once_with("/v5/market/tickers", symbol="BTCUSDT")
    catalog.assert_not_called()


@pytest.mark.parametrize("venue,symbol,target", [
    ("bybit", "BTCUSDT", "_bybit"), ("binance", "BTCUSDC", "_request"),
    ("hyperliquid", "xyz:TSLA", "_hl"),
])
def test_context_upstream_failure_raises_without_cache_or_bias(venue, symbol, target):
    with patch.object(em, target, side_effect=em.ExchangeDataError("offline")), \
            patch.object(em, "fetch_exchange_candles") as history, \
            pytest.raises(em.ExchangeDataError):
        em.get_exchange_context(venue, symbol)
    assert not em._CONTEXT_CACHE
    history.assert_not_called()


@pytest.mark.parametrize("payload", [None, {}, [], [{}, []],
    [{"universe": [{"name": "BTC"}]}, []],
    [{"universe": [{"name": "BTC"}, {"name": "BTC"}]}, [{}, {}]],
    [{"universe": [{"name": "BTC", "isDelisted": True}]}, [{}]],
])
def test_hyperliquid_context_malformed_or_inactive_never_falls_back(payload):
    with patch.object(em, "_hl", return_value=payload) as api, \
            patch.object(em, "fetch_exchange_candles") as history, \
            pytest.raises(em.ExchangeDataError):
        em.get_exchange_context("hyperliquid", "BTC")
    api.assert_called_once_with("metaAndAssetCtxs", dex="")
    history.assert_not_called()


@pytest.mark.parametrize("bad", ["NaN", "Infinity", True, "", None])
def test_context_unknown_numeric_fields_are_null_with_warnings(bad):
    ticker = {"symbol": "BTCUSDT", "fundingIntervalHour": "8", "fundingRate": bad,
              "openInterest": bad, "markPrice": bad, "indexPrice": bad}
    with patch.object(em, "_bybit", return_value={"list": [ticker]}), \
            patch.object(em, "fetch_exchange_candles", return_value=[]):
        result = em.get_exchange_context("bybit", "BTCUSDT")
    assert all(result[key] is None for key in (
        "funding_rate", "open_interest", "mark_price", "index_price"))
    assert len(result["warnings"]) == 4


@pytest.mark.parametrize("venue,symbol,target,payload", [
    ("bybit", "BTCUSDT", "_bybit", {"list": [{"symbol": "ETHUSDT"}]}),
    ("bybit", "BTCUSDT", "_bybit", {"list": [], "category": "inverse"}),
    ("binance", "BTCUSDC", "_request", {"symbol": "BTCUSDT"}),
    ("binance", "BTCUSDC", "_request", []),
])
def test_context_rejects_symbol_category_and_array_mismatches(venue, symbol, target, payload):
    with patch.object(em, target, return_value=payload), \
            patch.object(em, "fetch_exchange_candles") as history, \
            pytest.raises(em.ExchangeDataError):
        em.get_exchange_context(venue, symbol)
    history.assert_not_called()


@pytest.mark.parametrize("field", ["fundingIntervalHour", "fundingInterval"])
def test_bybit_boolean_funding_intervals_are_not_numbers(field):
    ticker = {"symbol": "BTCUSDT", "fundingIntervalHour": True if field == "fundingIntervalHour" else ""}
    metadata = {"symbol": "BTCUSDT", "fundingInterval": True if field == "fundingInterval" else ""}
    with patch.object(em, "_bybit", side_effect=[{"list": [ticker]}, {"list": [metadata]}]), \
            patch.object(em, "fetch_exchange_candles", return_value=[]):
        result = em.get_exchange_context("bybit", "BTCUSDT")
    assert result["funding_interval_hours"] is None
