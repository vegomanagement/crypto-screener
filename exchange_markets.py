"""Official public perpetual data, without auth, brokerage or venue fallback.

Catalogs cache complete price snapshots for 30s; Bybit/Binance instrument metadata
and Hyperliquid dex/collateral discovery cache for 300s. Hyperliquid bundles fresh
universe metadata with its asset contexts, one bulk request per discovered dex.
Partial HIP-3 failures are explicitly warned and never cached as fresh catalogs.
"""

import copy
import math
import re
import time

import requests

from market_candles import closed_candles, interval_ms

EXCHANGES = frozenset({"bybit", "binance", "hyperliquid"})
_BASE = {
    "bybit": "https://api.bybit.com",
    "binance": "https://fapi.binance.com",
    "hyperliquid": "https://api.hyperliquid.xyz",
}
_CACHE = {}
_META_CACHE = {}


class ExchangeDataError(RuntimeError):
    """Safe public error; never contains upstream bodies or credentials."""


def _exchange(exchange):
    if exchange not in EXCHANGES:
        raise ValueError("unsupported exchange")
    return exchange


def _number(value):
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _request(exchange, path, *, params=None, payload=None):
    try:
        response = requests.request(
            "POST" if payload is not None else "GET",
            _BASE[exchange] + path,
            params=params,
            json=payload,
            timeout=(5, 20),
            allow_redirects=False,
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            raise ExchangeDataError(
                f"{exchange}: public API HTTP {int(response.status_code)}"
            )
        if (
            response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            != "application/json"
        ):
            raise ExchangeDataError(f"{exchange}: public API returned non-JSON data")
        return response.json()
    except (requests.RequestException, ValueError):
        raise ExchangeDataError(f"{exchange}: public API unavailable") from None


def _metadata(exchange, loader, force):
    now = time.time()
    cached = _META_CACHE.get(exchange)
    if not force and cached is not None and 0 <= now - cached[0] < 300:
        return cached[1]
    value = loader()
    _META_CACHE[exchange] = (now, value)
    return value


def _bybit(path, **params):
    data = _request("bybit", path, params={"category": "linear", **params})
    if not isinstance(data, dict) or data.get("retCode") != 0:
        raise ExchangeDataError("bybit: API response rejected")
    result = data["result"]
    if not isinstance(result, dict) or not isinstance(result.get("list"), list):
        raise ExchangeDataError("bybit: malformed response")
    return result


def _market(exchange, symbol, base, quote, settle, price=None, change=None):
    return {
        "exchange": exchange,
        "symbol": symbol,
        "display_symbol": f"{base}/{quote}",
        "base": base,
        "quote": quote,
        "settle": settle,
        "price": _number(price),
        "change_24h": _number(change),
    }


def _bybit_metadata():
    rows, cursor, seen = [], "", set()
    while True:
        page = _bybit("/v5/market/instruments-info", limit=1000, cursor=cursor)
        for row in page["list"]:
            for key in (
                "symbol",
                "contractType",
                "status",
                "baseCoin",
                "quoteCoin",
                "settleCoin",
            ):
                if not isinstance(row.get(key), str) or not row[key]:
                    raise ExchangeDataError("bybit: malformed market metadata")
        rows.extend(page["list"])
        cursor = page.get("nextPageCursor", "")
        if not isinstance(cursor, str) or cursor in seen:
            raise ExchangeDataError("bybit: repeated or invalid pagination cursor")
        if not cursor:
            break
        seen.add(cursor)
    return rows


def _bybit_markets(force=False):
    rows = _metadata("bybit", _bybit_metadata, force)
    tickers = {r["symbol"]: r for r in _bybit("/v5/market/tickers")["list"]}
    markets = []
    for row in rows:
        if (
            row["contractType"] != "LinearPerpetual"
            or row["status"] != "Trading"
            or row["quoteCoin"] not in {"USDT", "USDC"}
        ):
            continue
        ticker = tickers.get(row["symbol"], {})
        change = _number(ticker.get("price24hPcnt"))
        markets.append(
            _market(
                "bybit",
                row["symbol"],
                row["baseCoin"],
                row["quoteCoin"],
                row["settleCoin"],
                ticker.get("lastPrice"),
                change * 100 if change is not None else None,
            )
        )
    return markets


def _safe_call(exchange, function, *args):
    try:
        return function(*args)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError):
        raise ExchangeDataError(f"{exchange}: malformed public API data") from None


def _objects(value, keys=()):
    if not isinstance(value, list):
        raise TypeError("expected list")
    for row in value:
        if not isinstance(row, dict):
            raise TypeError("expected object")
        if any(not isinstance(row.get(key), str) or not row[key] for key in keys):
            raise ValueError("missing field")
    return value


def _binance_metadata():
    data = _request("binance", "/fapi/v1/exchangeInfo")
    return _objects(
        data["symbols"],
        ("symbol", "baseAsset", "quoteAsset", "marginAsset", "status", "contractType"),
    )


def _binance_markets(force=False):
    rows = _metadata("binance", _binance_metadata, force)
    tickers = {
        r["symbol"]: r
        for r in _objects(_request("binance", "/fapi/v1/ticker/24hr"), ("symbol",))
    }
    markets = []
    for row in rows:
        if (
            row["contractType"] != "PERPETUAL"
            or row["status"] != "TRADING"
            or row["quoteAsset"] not in {"USDT", "USDC"}
        ):
            continue
        ticker = tickers.get(row["symbol"], {})
        markets.append(
            _market(
                "binance",
                row["symbol"],
                row["baseAsset"],
                row["quoteAsset"],
                row["marginAsset"],
                ticker.get("lastPrice"),
                ticker.get("priceChangePercent"),
            )
        )
    return markets


def _hl(kind, **fields):
    return _request("hyperliquid", "/info", payload={"type": kind, **fields})


def _hyperliquid_metadata():
    data = _hl("perpDexs")
    if not isinstance(data, list):
        raise TypeError("expected dex list")
    dexes = [""]
    for row in data:
        if row is None:
            continue
        name = row["name"]
        if not isinstance(name, str) or not re.fullmatch(
            r"[a-z][a-z0-9_-]{0,31}", name
        ):
            raise ValueError("invalid dex name")
        if name not in dexes:
            dexes.append(name)
    tokens = {
        row["index"]: row["name"]
        for row in _objects(_hl("spotMeta")["tokens"], ("name",))
    }
    return dexes, tokens


def _hyperliquid_dex_markets(dex, tokens):
    data = _hl("metaAndAssetCtxs", dex=dex)
    if not isinstance(data, list) or len(data) != 2:
        raise ValueError("expected metadata and contexts")
    meta, contexts = data
    universe = _objects(meta["universe"], ("name",))
    contexts = _objects(contexts)
    if len(universe) != len(contexts):
        raise ValueError("context count mismatch")
    quote = tokens[meta.get("collateralToken", 0)]
    markets = []
    for row, context in zip(universe, contexts):
        if (
            row.get("isDelisted")
            or row.get("isDeleted")
            or row.get("isActive") is False
        ):
            continue
        symbol = row["name"]
        if dex and not symbol.startswith(dex + ":"):
            raise ValueError("unexpected HIP-3 symbol namespace")
        price = _number(context.get("markPx"))
        if price is None:
            price = _number(context.get("midPx"))
        previous = _number(context.get("prevDayPx"))
        change = (
            (price / previous - 1) * 100
            if price is not None and previous is not None and previous > 0
            else None
        )
        markets.append(
            _market(
                "hyperliquid",
                symbol,
                symbol.split(":")[-1],
                quote,
                quote,
                price,
                change,
            )
        )
    return markets


def _hyperliquid_markets(force=False):
    dexes, tokens = _metadata("hyperliquid", _hyperliquid_metadata, force)
    markets, warnings = [], []
    for dex in dexes:
        try:
            markets.extend(
                _safe_call("hyperliquid", _hyperliquid_dex_markets, dex, tokens)
            )
        except ExchangeDataError:
            if not dex:
                raise
            warnings.append(f"Partial Hyperliquid catalog: HIP-3 dex {dex} unavailable")
    return markets, warnings


def list_exchange_markets(exchange: str, force: bool = False) -> dict:
    """List active perpetuals, retaining zero/null prices and exact venue IDs.

    ``force`` bypasses both cache layers. Failed refreshes raise rather than
    relabeling stale data as fresh. A successful empty universe remains empty.
    """
    _exchange(exchange)
    now = int(time.time() * 1000)
    cached = _CACHE.get(exchange)
    if not force and cached is not None and 0 <= now - cached["as_of"] < 30_000:
        return {**copy.deepcopy(cached), "cached": True}
    provider = {
        "bybit": _bybit_markets,
        "binance": _binance_markets,
        "hyperliquid": _hyperliquid_markets,
    }[exchange]
    markets = _safe_call(exchange, provider, force)
    markets, warnings = markets if exchange == "hyperliquid" else (markets, [])
    result = {
        "exchange": exchange,
        "markets": markets,
        "total": len(markets),
        "as_of": now,
        "cached": False,
        "warnings": warnings,
    }
    if not warnings:
        _CACHE[exchange] = copy.deepcopy(result)
    return result


def get_ticker(exchange: str, symbol: str) -> dict | None:
    """One native-symbol quote from the already-cached bulk catalog.

    Never issues a dedicated per-coin request; returns None for an unknown
    or delisted symbol rather than a fabricated price.
    """
    catalog = list_exchange_markets(exchange)
    for market in catalog["markets"]:
        if market["symbol"] == symbol:
            return {"symbol": symbol, "price": market["price"],
                    "change_24h": market["change_24h"], "as_of": catalog["as_of"]}
    return None


def fetch_exchange_candles(
    exchange: str, symbol: str, interval: str, limit: int
) -> list[dict]:
    """Return venue-native, finite OHLCV with UTC millisecond open/close times."""
    _exchange(exchange)
    pattern = (
        r"(?:[a-z][a-z0-9_-]{0,31}:)?[A-Za-z0-9][A-Za-z0-9._-]{0,79}"
        if exchange == "hyperliquid"
        else r"[^\W_]\w{0,79}" if exchange == "binance"
        else r"[A-Z0-9][A-Z0-9_]{0,79}"
    )
    known = {row["symbol"] for row in _CACHE.get(exchange, {}).get("markets", [])}
    if not isinstance(symbol, str) or (
        symbol not in known and not re.fullmatch(pattern, symbol)
    ):
        raise ValueError("invalid exchange symbol")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("candle limit must be an integer from 1 to 1000")
    interval_ms(interval)
    return _safe_call(exchange, _candles, exchange, symbol, interval, limit)


def _candles(exchange, symbol, interval, limit):
    duration = interval_ms(interval)
    now = int(time.time() * 1000)
    mapped = {
        "1": "1m",
        "3": "3m",
        "5": "5m",
        "15": "15m",
        "30": "30m",
        "60": "1h",
        "120": "2h",
        "240": "4h",
        "D": "1d",
        "W": "1w",
    }[interval]
    if exchange == "hyperliquid":
        base_interval = "1" if interval == "3" else interval
        if interval == "3":
            # Deliberately derive 3m from complete 1m bars, also for custom 12m
            # chart aggregation. Never relabel 1m bars or use incomplete groups.
            mapped = "1m"
        raw = _hl(
            "candleSnapshot",
            req={
                "coin": symbol,
                "interval": mapped,
                "startTime": now - (limit + 1) * duration,
                "endTime": now,
            },
        )
        _objects(raw)
        if any(("s" in row and row["s"] != symbol)
               or ("i" in row and row["i"] != mapped) for row in raw):
            raise ExchangeDataError("hyperliquid: candle instrument or interval mismatch")
        rows = [{"ts": int(r["t"]), **{k: float(r[k]) for k in "ohlcv"}} for r in raw]
        rows = closed_candles(
            rows, base_interval, source=exchange, symbol=symbol, as_of_ms=now
        )
        if interval == "3":
            groups = {}
            for row in rows:
                start = row["ts"] // duration * duration
                groups.setdefault(start, []).append(row)
            rows = [
                {
                    "ts": start,
                    "o": group[0]["o"],
                    "h": max(r["h"] for r in group),
                    "l": min(r["l"] for r in group),
                    "c": group[-1]["c"],
                    "v": sum(r["v"] for r in group),
                }
                for start, group in groups.items()
                if len(group) == 3 and group[0]["ts"] == start
            ]
    else:
        if exchange == "bybit":
            raw = _bybit(
                "/v5/market/kline",
                symbol=symbol,
                interval=interval,
                limit=limit,
                end=now - duration,
            )["list"]
        else:
            raw = _request(
                "binance",
                "/fapi/v1/klines",
                params={
                    "symbol": symbol,
                    "interval": mapped,
                    "limit": limit,
                    "endTime": now - duration,
                },
            )
        if not isinstance(raw, list):
            raise ValueError("expected candle list")
        rows = [
            {"ts": int(r[0]), **dict(zip("ohlcv", map(float, r[1:6])))} for r in raw
        ]
    return closed_candles(rows, interval, source=exchange, symbol=symbol, as_of_ms=now)[
        -limit:
    ]
