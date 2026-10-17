"""Offline official-API fixtures; no credentials or live network required."""

import importlib
from typing import ClassVar

import pytest


@pytest.fixture
def api(monkeypatch):
    import exchange_markets as module

    getattr(module, "_CACHE", {}).clear()
    getattr(module, "_META_CACHE", {}).clear()
    calls = []
    replies = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        assert replies, "unexpected request"
        value = replies.pop(0)
        if isinstance(value, Exception):
            raise value
        if hasattr(value, "json"):
            return value

        class Response:
            status_code = 200
            headers: ClassVar = {"Content-Type": "application/json"}

            def json(self):
                return value

        return Response()

    monkeypatch.setattr("requests.request", request)
    monkeypatch.setattr("time.time", lambda: 1_800_000_000)
    return module, replies, calls


def bybit_page(rows, cursor=""):
    return {"retCode": 0, "result": {"list": rows, "nextPageCursor": cursor}}


def bybit_market(symbol="BTCUSDT", quote="USDT", **extra):
    return {
        "symbol": symbol,
        "baseCoin": "BTC",
        "quoteCoin": quote,
        "settleCoin": quote,
        "contractType": "LinearPerpetual",
        "status": "Trading",
        **extra,
    }


def test_bybit_fully_paginates_and_bulk_prices_without_dropping_nulls(api):
    module, replies, calls = api
    replies.extend(
        [
            bybit_page([bybit_market(), bybit_market("DEAD", status="Closed")], "next"),
            bybit_page(
                [
                    bybit_market("BTCPERP", "USDC"),
                    bybit_market("DATED", contractType="LinearFutures"),
                ]
            ),
            bybit_page(
                [{"symbol": "BTCUSDT", "lastPrice": "0", "price24hPcnt": "0.25"}]
            ),
        ]
    )
    result = module.list_exchange_markets("bybit")
    assert result["total"] == 2
    markets = {m["symbol"]: m for m in result["markets"]}
    assert markets["BTCUSDT"] == {
        "symbol": "BTCUSDT",
        "display_symbol": "BTC/USDT",
        "base": "BTC",
        "quote": "USDT",
        "settle": "USDT",
        "exchange": "bybit",
        "price": 0.0,
        "change_24h": 25.0,
    }
    assert markets["BTCPERP"]["price"] is None
    assert result["as_of"] == 1_800_000_000_000
    assert not result["cached"]
    assert result["warnings"] == []
    assert len(calls) == 3
    assert calls[1][2]["params"]["cursor"] == "next"
    assert "symbol" not in calls[2][2]["params"]


def test_repeated_bybit_cursor_fails_without_cache_poison(api):
    module, replies, calls = api
    replies.extend([bybit_page([], "loop"), bybit_page([], "loop")])
    with pytest.raises(module.ExchangeDataError, match="bybit"):
        module.list_exchange_markets("bybit")
    assert len(calls) == 2
    assert not module._CACHE
    assert not module._META_CACHE


@pytest.mark.parametrize(
    "reply",
    [
        {"retCode": 10001, "retMsg": "secret upstream text"},
        {"retCode": 0, "result": {"list": "bad"}},
        {"retCode": 0, "result": {"list": [{}]}},
        None,
        [],
        "<html>secret upstream text</html>",
    ],
)
def test_bad_bybit_responses_raise_safe_typed_errors(api, reply):
    module, replies, _ = api
    replies.append(reply)
    with pytest.raises(module.ExchangeDataError) as exc:
        module.list_exchange_markets("bybit")
    assert "bybit" in str(exc.value)
    assert "secret" not in str(exc.value)
    assert not module._CACHE


def test_binance_filters_usdm_perpetuals_and_uses_one_bulk_ticker(api):
    module, replies, calls = api

    def market(symbol, quote="USDT", **extra):
        return {
            "symbol": symbol,
            "baseAsset": "BTC",
            "quoteAsset": quote,
            "marginAsset": quote,
            "status": "TRADING",
            "contractType": "PERPETUAL",
            **extra,
        }

    replies.extend(
        [
            {
                "symbols": [
                    market("BTCUSDT"),
                    market("BTCUSDC", "USDC"),
                    market("BTCUSD", "USD"),
                    market("FUTURE", contractType="CURRENT_QUARTER"),
                    market("DEAD", status="SETTLING"),
                ]
            },
            [
                {"symbol": "BTCUSDT", "lastPrice": "NaN", "priceChangePercent": "-2.5"},
                {
                    "symbol": "BTCUSDC",
                    "lastPrice": "12",
                    "priceChangePercent": "Infinity",
                },
            ],
        ]
    )
    result = module.list_exchange_markets("binance")
    assert result["total"] == 2
    markets = {m["symbol"]: m for m in result["markets"]}
    assert markets["BTCUSDT"]["price"] is None
    assert markets["BTCUSDT"]["change_24h"] == -2.5
    assert markets["BTCUSDC"]["price"] == 12
    assert markets["BTCUSDC"]["change_24h"] is None
    assert markets["BTCUSDC"]["settle"] == "USDC"
    assert all("fapi.binance.com" in call[1] for call in calls)
    assert calls[1][1].endswith("/fapi/v1/ticker/24hr")
    assert len(calls) == 2
    assert not calls[1][2]["params"]


def test_hyperliquid_native_and_hip3_keep_exact_names_and_context_indices(api):
    module, replies, calls = api
    replies.extend(
        [
            [None, {"name": "xyz"}],
            {"tokens": [{"index": 0, "name": "USDC"}, {"index": 1, "name": "USDH"}]},
            [
                {
                    "universe": [
                        {"name": "DEAD", "isDelisted": True},
                        {"name": "kBONK"},
                        {"name": "BTC"},
                        {"name": "INACTIVE", "isActive": False},
                    ],
                    "collateralToken": 0,
                },
                [
                    {"markPx": "999"},
                    {"markPx": "2", "prevDayPx": "1", "funding": "0.123"},
                    {"markPx": "NaN", "midPx": "4", "prevDayPx": "0"},
                    {"markPx": "9"},
                ],
            ],
            [
                {
                    "universe": [{"name": "xyz:TSLA"}, {"name": "xyz:ZERO"}],
                    "collateralToken": 1,
                },
                [
                    {"markPx": "300", "prevDayPx": "250"},
                    {"markPx": "0", "prevDayPx": "1"},
                ],
            ],
        ]
    )
    result = module.list_exchange_markets("hyperliquid")
    markets = {m["symbol"]: m for m in result["markets"]}
    assert result["total"] == 4
    assert markets["kBONK"]["price"] == 2
    assert markets["kBONK"]["change_24h"] == 100
    assert markets["BTC"]["price"] == 4
    assert markets["BTC"]["change_24h"] is None
    assert markets["xyz:TSLA"]["settle"] == "USDH"
    assert markets["xyz:TSLA"]["change_24h"] == pytest.approx(20)
    assert markets["xyz:ZERO"]["price"] == 0
    assert result["warnings"] == []
    assert [c[2]["json"]["type"] for c in calls] == [
        "perpDexs",
        "spotMeta",
        "metaAndAssetCtxs",
        "metaAndAssetCtxs",
    ]
    assert calls[-1][2]["json"]["dex"] == "xyz"


def test_cache_ttls_force_copy_and_failed_refresh_are_honest(api, monkeypatch):
    import requests

    module, replies, calls = api
    replies.extend([bybit_page([bybit_market()]), bybit_page([])])
    first = module.list_exchange_markets("bybit")
    first["markets"].clear()
    cached = module.list_exchange_markets("bybit")
    assert cached["cached"] is True
    assert cached["total"] == len(cached["markets"]) == 1
    assert len(calls) == 2
    monkeypatch.setattr(module.time, "time", lambda: 1_800_000_031)
    replies.append(bybit_page([{"symbol": "BTCUSDT", "lastPrice": "123"}]))
    fresh_prices = module.list_exchange_markets("bybit")
    assert len(calls) == 3  # metadata still fresh
    assert fresh_prices["markets"][0]["price"] == 123
    assert fresh_prices["cached"] is False
    monkeypatch.setattr(module.time, "time", lambda: 1_800_000_062)
    replies.append(requests.Timeout("sensitive URL token"))
    with pytest.raises(module.ExchangeDataError):
        module.list_exchange_markets("bybit")
    assert module._CACHE["bybit"]["as_of"] == fresh_prices["as_of"]
    replies.extend([bybit_page([]), bybit_page([])])
    forced = module.list_exchange_markets("bybit", force=True)
    assert forced["total"] == 0  # successful empty is not an error or stale fallback
    assert not forced["cached"]
    monkeypatch.setattr(module.time, "time", lambda: 1_800_000_400)
    replies.extend([bybit_page([bybit_market()]), bybit_page([])])
    assert module.list_exchange_markets("bybit")["total"] == 1
    assert calls[-2][1].endswith("instruments-info")


@pytest.mark.parametrize(
    "exchange,symbol",
    [
        ("bybit", "BTCUSDT"),
        ("binance", "BTCUSDC"),
        ("hyperliquid", "kBONK"),
        ("hyperliquid", "xyz:TSLA"),
    ],
)
def test_candles_use_native_symbol_closed_ohlcv_and_exact_source(api, exchange, symbol):
    module, replies, calls = api
    now = 1_800_000_000_000
    ts = now - 180_000
    if exchange == "bybit":
        replies.append(
            bybit_page(
                [
                    [str(now), "1", "3", "1", "2", "10", "20"],
                    [str(ts + 60_000), "1", "3", "1", "2", "10", "20"],
                    [str(ts), "1", "3", "1", "2", "10", "20"],
                ]
            )
        )
    elif exchange == "binance":
        replies.append(
            [[t, "1", "3", "1", "2", "10", t + 59_999] for t in (ts, ts + 60_000, now)]
        )
    else:
        replies.append(
            [
                {
                    "t": t,
                    "T": t + 59_999,
                    "s": symbol,
                    "i": "1m",
                    "o": "1",
                    "h": "3",
                    "l": "1",
                    "c": "2",
                    "v": "10",
                }
                for t in (ts, ts + 60_000, now)
            ]
        )
    candles = module.fetch_exchange_candles(exchange, symbol, "1", 2)
    assert len(candles) == 2
    assert candles[0] == {
        "ts": ts,
        "close_ts": ts + 60_000,
        "o": 1.0,
        "h": 3.0,
        "l": 1.0,
        "c": 2.0,
        "v": 10.0,
        "source": exchange,
        "symbol": symbol,
        "interval": "1",
        "closed": True,
    }
    assert len(calls) == 1  # chart calls must not discover entire market catalog
    request = calls[0][2]
    if exchange == "hyperliquid":
        assert request["json"]["req"]["coin"] == symbol
        assert request["json"]["req"]["interval"] == "1m"
    else:
        assert request["params"]["symbol"] == symbol
        assert request["params"]["interval"] == ("1" if exchange == "bybit" else "1m")


def test_hyperliquid_three_minute_aggregates_only_complete_one_minute_groups(api):
    module, replies, calls = api
    now = 1_800_000_000_000
    replies.append(
        [
            {
                "t": now - i * 60_000,
                "s": "kBONK",
                "i": "1m",
                "o": "1",
                "h": "4",
                "l": "1",
                "c": "2",
                "v": "1",
            }
            for i in range(8, -1, -1)
        ]
    )
    rows = module.fetch_exchange_candles("hyperliquid", "kBONK", "3", 2)
    assert [r["ts"] for r in rows] == [now - 360_000, now - 180_000]
    assert all(r["v"] == 3 and r["interval"] == "3" and r["closed"] for r in rows)
    assert calls[0][2]["json"]["req"]["interval"] == "1m"


@pytest.mark.parametrize(
    "symbol",
    ["../BTC", "BTC?api_key=secret", "BTC/USDT", " BTC", "", "x:y:z", "https://evil"],
)
@pytest.mark.parametrize("exchange", ["bybit", "binance", "hyperliquid"])
def test_symbol_injection_rejected_before_any_network(api, symbol, exchange):
    module, _, calls = api
    with pytest.raises(ValueError, match="symbol"):
        module.fetch_exchange_candles(exchange, symbol, "1", 2)
    assert calls == []


@pytest.mark.parametrize("limit", [0, -1, 1001, True, 2.5, "2"])
def test_invalid_candle_limit_rejected_before_network(api, limit):
    module, _, calls = api
    with pytest.raises(ValueError, match="limit"):
        module.fetch_exchange_candles("bybit", "BTCUSDT", "1", limit)
    assert calls == []


@pytest.mark.parametrize(
    "exchange,reply",
    [
        ("binance", {"code": -1003, "msg": "secret"}),
        ("binance", {"symbols": "bad"}),
        ("binance", {"symbols": [{}]}),
        ("hyperliquid", {"error": "secret"}),
        ("hyperliquid", [None, {"name": "../invalid"}]),
    ],
)
def test_malformed_catalogs_fail_typed_without_metadata_poison(api, exchange, reply):
    module, replies, _ = api
    replies.append(reply)
    with pytest.raises(module.ExchangeDataError) as exc:
        module.list_exchange_markets(exchange)
    assert "secret" not in str(exc.value)
    assert exchange in str(exc.value)
    assert not module._META_CACHE
    assert not module._CACHE


@pytest.mark.parametrize("exchange", ["bybit", "binance", "hyperliquid"])
@pytest.mark.parametrize("bad", ["NaN", "Infinity", "oops"])
def test_bad_closed_candle_ohlcv_is_safe_typed_error(api, exchange, bad):
    module, replies, _ = api
    ts = 1_799_999_940_000
    if exchange == "bybit":
        replies.append(bybit_page([[ts, "1", "3", "1", bad, "1"]]))
    elif exchange == "binance":
        replies.append([[ts, "1", "3", "1", bad, "1"]])
    else:
        replies.append([{"t": ts, "o": "1", "h": "3", "l": "1", "c": bad, "v": "1"}])
    with pytest.raises(module.ExchangeDataError, match=exchange):
        module.fetch_exchange_candles(exchange, "BTC", "1", 2)


@pytest.mark.parametrize(
    "status,content_type",
    [
        (403, "application/json"),
        (429, "text/html"),
        (200, "text/html"),
        (302, "application/json"),
    ],
)
def test_http_block_or_nonjson_headers_cannot_be_mistaken_for_success(
    api, status, content_type
):
    module, replies, calls = api

    class Response:
        status_code = status
        headers: ClassVar = {"Content-Type": content_type}

        def json(self):
            return bybit_page([])

    replies.extend([Response(), bybit_page([])])
    with pytest.raises(module.ExchangeDataError, match="bybit"):
        module.list_exchange_markets("bybit")
    assert len(calls) == 1
    assert not module._CACHE


@pytest.mark.parametrize("exchange", ["binance", "hyperliquid"])
def test_empty_object_is_not_empty_candle_list(api, exchange):
    module, replies, _ = api
    replies.append({})
    with pytest.raises(module.ExchangeDataError, match=exchange):
        module.fetch_exchange_candles(exchange, "BTC", "1", 2)


def test_hyperliquid_context_length_mismatch_is_not_silently_truncated(api):
    module, replies, _ = api
    replies.extend(
        [
            [None],
            {"tokens": [{"index": 0, "name": "USDC"}]},
            [{"universe": [{"name": "BTC"}, {"name": "ETH"}]}, [{"markPx": "1"}]],
        ]
    )
    with pytest.raises(module.ExchangeDataError, match="hyperliquid"):
        module.list_exchange_markets("hyperliquid")
    assert not module._CACHE


def test_hip3_failure_reports_partial_scope_without_caching_failure(api):
    import requests

    module, replies, calls = api
    native = [{"universe": [{"name": "BTC"}], "collateralToken": 0}, [{"markPx": "1"}]]
    replies.extend(
        [
            [None, {"name": "xyz"}],
            {"tokens": [{"index": 0, "name": "USDC"}]},
            native,
            requests.Timeout("secret"),
        ]
    )
    result = module.list_exchange_markets("hyperliquid")
    assert result["total"] == 1
    assert result["warnings"] and "xyz" in result["warnings"][0]
    assert "partial" in result["warnings"][0].lower()
    assert "secret" not in result["warnings"][0]
    assert "hyperliquid" not in module._CACHE
    replies.extend([native, [{"universe": []}, []]])
    recovered = module.list_exchange_markets("hyperliquid")
    assert not recovered["cached"]
    assert recovered["warnings"] == []
    assert len(calls) == 6  # only discovery metadata was cached


@pytest.mark.parametrize("exchange", ["bybit", "binance"])
@pytest.mark.parametrize("tickers", [{}, "not a list", [{}]])
def test_malformed_bulk_tickers_are_not_silent_null_price_success(
    api, exchange, tickers
):
    module, replies, _ = api
    if exchange == "bybit":
        replies.extend([bybit_page([bybit_market()]), bybit_page(tickers)])
    else:
        replies.extend(
            [
                {
                    "symbols": [
                        {
                            "symbol": "BTCUSDT",
                            "baseAsset": "BTC",
                            "quoteAsset": "USDT",
                            "marginAsset": "USDT",
                            "status": "TRADING",
                            "contractType": "PERPETUAL",
                        }
                    ]
                },
                tickers,
            ]
        )
    with pytest.raises(module.ExchangeDataError, match=exchange):
        module.list_exchange_markets(exchange)
    assert not module._CACHE


@pytest.mark.parametrize("exchange", ["bybit", "binance"])
def test_max_limit_requests_only_closed_window_without_losing_one_bar(api, exchange):
    module, replies, calls = api
    replies.append(bybit_page([]) if exchange == "bybit" else [])
    assert module.fetch_exchange_candles(exchange, "BTCUSDT", "1", 1000) == []
    params = calls[0][2]["params"]
    assert params["limit"] == 1000
    assert (
        params["end" if exchange == "bybit" else "endTime"]
        <= 1_800_000_000_000 - 60_000
    )


def test_unknown_exchange_is_explicitly_rejected():
    module = importlib.import_module("exchange_markets")
    with pytest.raises(ValueError, match="exchange"):
        module.list_exchange_markets("hl")
