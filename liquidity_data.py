"""Uncached public REST order-book snapshots, never tick-complete history."""

import math
import re
import time

import requests

_ENDPOINTS = {
    "bybit": "https://api.bybit.com/v5/market/orderbook",
    "binance": "https://fapi.binance.com/fapi/v1/depth",
    "hyperliquid": "https://api.hyperliquid.xyz/info",
}


class LiquidityDataError(RuntimeError):
    """Safe upstream failure, without raw response bodies or request secrets."""


def _side(rows, *, descending):
    if not isinstance(rows, list) or not rows:
        raise ValueError("empty or malformed side")
    result = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError("malformed level")
        if any(isinstance(value, bool) or not isinstance(value, (str, int, float))
               for value in row):
            raise ValueError("malformed number")
        price, quantity = map(float, row)
        if not all(math.isfinite(n) and n > 0 for n in (price, quantity)):
            raise ValueError("nonpositive or nonfinite level")
        if result and not (result[-1][0] > price if descending else result[-1][0] < price):
            raise ValueError("unordered or duplicate level")
        result.append([price, quantity])
    return result


def _timestamp(value):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError("invalid timestamp")
    return value


def _sequence(value):
    if value is None:
        return None
    if not (type(value) is int and value >= 0 or
            isinstance(value, str) and re.fullmatch(r"[0-9]+", value)):
        raise ValueError("invalid sequence")
    return str(value)


def _decode(exchange, symbol, data):
    if not isinstance(data, dict):
        raise TypeError("malformed response")
    if exchange == "bybit":
        if type(data.get("retCode")) is not int or data["retCode"] != 0:
            raise ValueError("API error")
        book = data["result"]
        if book.get("s") != symbol:
            raise ValueError("symbol mismatch")
        bids, asks = book["b"], book["a"]
        timestamp, sequence = book.get("ts"), book.get("u")
    elif exchange == "binance":
        if "code" in data or "error" in data:
            raise ValueError("API error")
        bids, asks = data["bids"], data["asks"]
        timestamp, sequence = data.get("T", data.get("E")), data.get("lastUpdateId")
    else:
        if "error" in data or ("coin" in data and data["coin"] != symbol):
            raise ValueError("API error or coin mismatch")
        levels = data["levels"]
        if not isinstance(levels, list) or len(levels) != 2:
            raise ValueError("malformed sides")
        bids, asks = ([[row["px"], row["sz"]] for row in side] for side in levels)
        timestamp, sequence = data.get("time"), None
    bids, asks = _side(bids, descending=True), _side(asks, descending=False)
    if bids[0][0] >= asks[0][0]:
        raise ValueError("crossed or locked book")
    return bids, asks, _timestamp(timestamp), _sequence(sequence)


def symbol_pattern(exchange):
    """Shared venue symbol pattern; both validation and fetch paths must agree."""
    return (r"(?:[a-z][a-z0-9_-]{0,31}:)?[A-Za-z0-9][A-Za-z0-9._-]{0,79}"
            if exchange == "hyperliquid" else r"[^\W_]\w{0,79}"
            if exchange == "binance" else r"[A-Z0-9][A-Z0-9_]{0,79}")


def fetch_depth_snapshot(exchange, symbol, depth=200):
    """Fetch fresh native quantities. Invalid inputs raise ValueError; upstream
    failures raise LiquidityDataError. All returned timestamps are UTC ms.
    """
    if not isinstance(exchange, str) or exchange not in _ENDPOINTS:
        raise ValueError("unsupported exchange")
    pattern = symbol_pattern(exchange)
    if not isinstance(symbol, str) or not re.fullmatch(pattern, symbol):
        raise ValueError("invalid exchange symbol")
    if type(depth) is not int or not 1 <= depth <= 1000:
        raise ValueError("depth must be an integer from 1 to 1000")
    warnings = ["Sampled REST snapshot; not tick-complete history."]
    payload = None
    if exchange == "bybit":
        params = {"category": "linear", "symbol": symbol, "limit": depth}
    elif exchange == "binance":
        limit = max((n for n in (5, 10, 20, 50, 100, 500, 1000) if n <= depth), default=5)
        params = {"symbol": symbol, "limit": limit}
        if limit != depth:
            warnings.append(f"Binance supported request limit is {limit}, not {depth}.")
    else:
        params = None
        payload = {"type": "l2Book", "coin": symbol}
        if depth > 20:
            warnings.append("Hyperliquid l2Book is limited to 20 levels per side.")
    try:
        response = requests.request(
            "POST" if payload is not None else "GET", _ENDPOINTS[exchange],
            params=params, json=payload, timeout=10, allow_redirects=False,
            headers={"Accept": "application/json"},
        )
        observed_at = int(time.time() * 1000)
        if response.status_code != 200:
            raise LiquidityDataError(f"{exchange}: public depth HTTP {response.status_code}")
        bids, asks, timestamp, sequence = _decode(exchange, symbol, response.json())
    except (requests.RequestException, ValueError, TypeError, KeyError,
            AttributeError, OverflowError):
        raise LiquidityDataError(f"{exchange}: public depth unavailable or invalid") from None
    if len(bids) > depth or len(asks) > depth:
        warnings.append("Source snapshot truncated to requested depth.")
    bids, asks = bids[:depth], asks[:depth]
    if exchange == "bybit":
        warnings.append("RPI orders are excluded by the source endpoint.")
    if len(bids) < depth or len(asks) < depth:
        warnings.append("Available depth is smaller than requested.")
    quote = next((q for q in ("USDT", "USDC") if symbol.endswith(q)), None)
    return {
        "exchange": exchange, "symbol": symbol, "observed_at": observed_at,
        "time": timestamp, "bids": bids, "asks": asks,
        "depth_requested": depth, "depth_actual": {"bids": len(bids), "asks": len(asks)},
        "sequence": str(sequence) if sequence is not None else None,
        "quantity_unit": "native", "quote_unit": quote if exchange != "hyperliquid" else None,
        "warnings": warnings,
    }
