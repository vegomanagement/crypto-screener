"""Offline REST contract fixtures; no network in CI."""

import copy
import importlib

import pytest


def bybit_book():
    return {"retCode": 0, "result": {"s": "BTCUSDT", "ts": 1754450974231,
            "u": 9007199254740993123, "b": [["100", "2.75"], ["99", "1"]],
            "a": [["101", "3"], ["102", "4"]]}}


def install(monkeypatch, payload, status=200):
    module = importlib.import_module("liquidity_data")
    calls = []

    class Response:
        status_code = status

        def json(self):
            return copy.deepcopy(payload)

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr(module.requests, "request", request)
    monkeypatch.setattr(module.time, "time", lambda: 1800000000.125)
    return module, calls


def test_bybit_native_snapshot_contract(monkeypatch):
    module, calls = install(monkeypatch, bybit_book())
    result = module.fetch_depth_snapshot("bybit", "BTCUSDT")
    assert result == {
        "exchange": "bybit", "symbol": "BTCUSDT", "observed_at": 1800000000125,
        "time": 1754450974231, "bids": [[100.0, 2.75], [99.0, 1.0]],
        "asks": [[101.0, 3.0], [102.0, 4.0]], "depth_requested": 200,
        "depth_actual": {"bids": 2, "asks": 2}, "sequence": "9007199254740993123",
        "quantity_unit": "native", "quote_unit": "USDT",
        "warnings": ["Sampled REST snapshot; not tick-complete history.",
                     "RPI orders are excluded by the source endpoint.",
                     "Available depth is smaller than requested."],
    }
    assert calls == [(("GET", "https://api.bybit.com/v5/market/orderbook"), {
        "params": {"category": "linear", "symbol": "BTCUSDT", "limit": 200},
        "json": None, "timeout": 10, "allow_redirects": False,
        "headers": {"Accept": "application/json"}})]


def test_binance_unicode_native_and_supported_limits(monkeypatch):
    payload = {"lastUpdateId": 9007199254740993123, "T": 1754450974231,
               "bids": [["100", "2"]], "asks": [["101", "3"]]}
    module, calls = install(monkeypatch, payload)
    result = module.fetch_depth_snapshot("binance", "币安人生USDT")
    assert calls[0][0] == ("GET", "https://fapi.binance.com/fapi/v1/depth")
    assert calls[0][1]["params"] == {"symbol": "币安人生USDT", "limit": 100}
    assert result["sequence"] == "9007199254740993123"
    assert result["time"] == payload["T"]
    assert any("100" in warning for warning in result["warnings"])
    module.fetch_depth_snapshot("binance", "BTCUSDC", 500)
    assert calls[-1][1]["params"]["limit"] == 500


def test_hyperliquid_hip3_exact_case_and_unknown_quote(monkeypatch):
    payload = {"coin": "xyz:CL", "time": 1754450974231, "levels": [
        [{"px": "100", "sz": "2.75", "n": 1}],
        [{"px": "101", "sz": "3", "n": 2}]]}
    module, calls = install(monkeypatch, payload)
    result = module.fetch_depth_snapshot("hyperliquid", "xyz:CL")
    assert calls[0][0] == ("POST", "https://api.hyperliquid.xyz/info")
    assert calls[0][1]["json"] == {"type": "l2Book", "coin": "xyz:CL"}
    assert calls[0][1]["params"] is None
    assert result["quote_unit"] is None
    assert result["sequence"] is None
    assert result["quantity_unit"] == "native"
    assert result["bids"] == [[100.0, 2.75]]
    assert any("20" in warning for warning in result["warnings"])


@pytest.mark.parametrize("exchange,symbol,depth", [
    ("other", "BTCUSDT", 200), ([], "BTCUSDT", 200),
    ("bybit", "../BTC", 200), ("binance", "BTC/USDT", 200),
    ("hyperliquid", "xyz:CL?x", 200), ("bybit", None, 200),
    ("bybit", "BTCUSDT", 0), ("bybit", "BTCUSDT", 1001),
    ("bybit", "BTCUSDT", True), ("bybit", "BTCUSDT", 2.5),
])
def test_invalid_input_makes_no_request(monkeypatch, exchange, symbol, depth):
    module, calls = install(monkeypatch, bybit_book())
    with pytest.raises(ValueError):
        module.fetch_depth_snapshot(exchange, symbol, depth)
    assert not calls


@pytest.mark.parametrize("side", [
    [], [["100", "0"]], [["100", "-1"]], [["nan", "1"]],
    [["inf", "1"]], [["100", "nan"]], [[True, "1"]], [["100"]],
    [["100", "1", "extra"]], [["99", "1"], ["100", "1"]],
    [["100", "1"], ["100", "2"]], [["102", "1"]],
    "not levels", [None], [["0", "1"]], [["-1", "1"]],
    [["100", None]], [["100", {}]],
])
def test_rejects_whole_invalid_book(monkeypatch, side):
    payload = bybit_book()
    payload["result"]["b"] = side
    module, _ = install(monkeypatch, payload)
    with pytest.raises(RuntimeError):
        module.fetch_depth_snapshot("bybit", "BTCUSDT")


@pytest.mark.parametrize("payload", [
    {"retCode": 10001, "result": {}}, {"error": "failed"}, [], None,
    {"retCode": 0, "result": None},
])
def test_upstream_errors_are_safe(monkeypatch, payload):
    module, _ = install(monkeypatch, payload)
    with pytest.raises(RuntimeError):
        module.fetch_depth_snapshot("bybit", "BTCUSDT")


@pytest.mark.parametrize("key,value", [("ts", 1.5), ("ts", "1754450974231"),
                                       ("ts", True), ("ts", -1),
                                       ("u", 1.5), ("u", True), ("u", "1e20"),
                                       ("s", "ETHUSDT")])
def test_rejects_invalid_identity_timestamp_sequence(monkeypatch, key, value):
    payload = bybit_book()
    payload["result"][key] = value
    module, _ = install(monkeypatch, payload)
    with pytest.raises(RuntimeError):
        module.fetch_depth_snapshot("bybit", "BTCUSDT")


def test_http_error_is_not_a_snapshot(monkeypatch):
    module, _ = install(monkeypatch, bybit_book(), status=429)
    with pytest.raises(RuntimeError):
        module.fetch_depth_snapshot("bybit", "BTCUSDT")


def test_hl_mismatched_coin_rejected(monkeypatch):
    module, _ = install(monkeypatch, {"coin": "ETH", "levels": [
        [{"px": "100", "sz": "1"}], [{"px": "101", "sz": "1"}]]})
    with pytest.raises(RuntimeError):
        module.fetch_depth_snapshot("hyperliquid", "BTC")


def test_binance_error_code_rejected(monkeypatch):
    module, _ = install(monkeypatch, {"code": -1121, "msg": "Invalid symbol"})
    with pytest.raises(RuntimeError):
        module.fetch_depth_snapshot("binance", "BTCUSDT")


def test_trimming_is_explicit_and_validates_unreturned_levels(monkeypatch):
    module, _ = install(monkeypatch, bybit_book())
    result = module.fetch_depth_snapshot("bybit", "BTCUSDT", 1)
    assert result["depth_actual"] == {"bids": 1, "asks": 1}
    assert any("truncated" in warning for warning in result["warnings"])
    malformed = bybit_book()
    malformed["result"]["b"][1][1] = "0"
    module, _ = install(monkeypatch, malformed)
    with pytest.raises(RuntimeError):
        module.fetch_depth_snapshot("bybit", "BTCUSDT", 1)


@pytest.mark.parametrize("exchange,symbol", [("bybit", "BTCPERP"),
                                             ("binance", "BTCUSDC"),
                                             ("hyperliquid", "kBONK")])
def test_native_symbols_quote_and_nullable_time(monkeypatch, exchange, symbol):
    if exchange == "bybit":
        payload = bybit_book()
        payload["result"]["s"] = symbol
        payload["result"].pop("ts")
        payload["result"]["u"] = "0009007199254740993123"
    elif exchange == "binance":
        payload = {"bids": [["100", "1"]], "asks": [["101", "1"]]}
    else:
        payload = {"levels": [[{"px": "100", "sz": "1"}],
                              [{"px": "101", "sz": "1"}]]}
    module, calls = install(monkeypatch, payload)
    result = module.fetch_depth_snapshot(exchange, symbol)
    assert result["symbol"] == symbol
    assert result["time"] is None
    assert result["quote_unit"] == ("USDC" if exchange == "binance" else None)
    if exchange == "bybit":
        assert result["sequence"] == "0009007199254740993123"
    module.fetch_depth_snapshot(exchange, symbol)
    assert len(calls) == 2  # no cache


def test_response_clock_not_request_clock_or_exchange_time(monkeypatch):
    module, _ = install(monkeypatch, bybit_book())
    now = [1000.0]

    class Response:
        status_code = 200

        def json(self):
            now[0] = 9999.0
            return bybit_book()

    def request(*args, **kwargs):
        now[0] = 1234.5
        return Response()

    monkeypatch.setattr(module.time, "time", lambda: now[0])
    monkeypatch.setattr(module.requests, "request", request)
    assert module.fetch_depth_snapshot("bybit", "BTCUSDT")["observed_at"] == 1234500


def test_timeout_safe_without_retry_or_fallback(monkeypatch):
    module, _ = install(monkeypatch, bybit_book())
    calls = []

    def request(*args, **kwargs):
        calls.append(args)
        raise module.requests.Timeout("body should not leak")

    monkeypatch.setattr(module.requests, "request", request)
    with pytest.raises(module.LiquidityDataError, match="unavailable") as error:
        module.fetch_depth_snapshot("bybit", "BTCUSDT")
    assert "body should not leak" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize("depth,limit", [(1, 5), (4, 5), (5, 5), (19, 10),
                                        (200, 100), (499, 100), (500, 500),
                                        (999, 500), (1000, 1000)])
def test_binance_supported_depth_mapping(monkeypatch, depth, limit):
    payload = {"bids": [["100", "1"]], "asks": [["101", "1"]]}
    module, calls = install(monkeypatch, payload)
    result = module.fetch_depth_snapshot("binance", "BTCUSDT", depth)
    assert calls[0][1]["params"]["limit"] == limit
    assert result["depth_requested"] == depth


@pytest.mark.parametrize("asks", [[["101", "1"], ["100.5", "1"]],
                                  [["101", "1"], ["101", "1"]],
                                  [["100", "1"]], []])
def test_invalid_asks_or_locked_book_rejected(monkeypatch, asks):
    payload = bybit_book()
    payload["result"]["a"] = asks
    module, _ = install(monkeypatch, payload)
    with pytest.raises(module.LiquidityDataError):
        module.fetch_depth_snapshot("bybit", "BTCUSDT")


