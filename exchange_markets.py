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
from itertools import pairwise

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


_CONTEXT_CACHE = {}


def _context_value(row, key, warnings, *, positive=False, nonnegative=False):
    value = None if isinstance(row.get(key), bool) else _number(row.get(key))
    if value is not None and (positive and value <= 0 or nonnegative and value < 0):
        value = None
    if value is None:
        warnings.append(f"{key}: unavailable or invalid upstream value")
    return value


def _exact_context_row(rows, symbol):
    rows = _objects(rows, ("symbol",))
    if len(rows) != 1 or rows[0]["symbol"] != symbol:
        raise ValueError("context symbol mismatch or missing instrument")
    return rows[0]


def _bybit_context(symbol):
    result = _bybit("/v5/market/tickers", symbol=symbol)
    if result.get("category", "linear") != "linear":
        raise ValueError("unexpected context category")
    row = _exact_context_row(result["list"], symbol)
    warnings = []
    hours = (None if isinstance(row.get("fundingIntervalHour"), bool)
             else _number(row.get("fundingIntervalHour")))
    if hours is None or hours <= 0:
        cached = _META_CACHE.get("bybit")
        metadata = next((r for r in cached[1] if r["symbol"] == symbol), None) if (
            cached and 0 <= time.time() - cached[0] < 300
        ) else None
        if metadata is None:
            result = _bybit("/v5/market/instruments-info", symbol=symbol)
            if result.get("category", "linear") != "linear":
                raise ValueError("unexpected metadata category")
            metadata = _exact_context_row(result["list"], symbol)
        minutes = (None if isinstance(metadata.get("fundingInterval"), bool)
                   else _number(metadata.get("fundingInterval")))
        hours = minutes / 60 if minutes is not None and minutes > 0 else None
    if hours is None:
        warnings.append("Funding interval unavailable")
    return {
        "funding_rate": _context_value(row, "fundingRate", warnings),
        "funding_interval_hours": hours,
        "open_interest": _context_value(row, "openInterest", warnings, nonnegative=True),
        "open_interest_unit": "base_asset",
        "mark_price": _context_value(row, "markPrice", warnings, positive=True),
        "index_price": _context_value(row, "indexPrice", warnings, positive=True),
        "warnings": warnings,
    }


def _binance_context(symbol):
    row = _request("binance", "/fapi/v1/premiumIndex", params={"symbol": symbol})
    interest = _request("binance", "/fapi/v1/openInterest", params={"symbol": symbol})
    if not isinstance(row, dict) or not isinstance(interest, dict) or (
        row.get("symbol") != symbol or interest.get("symbol") != symbol
    ):
        raise ValueError("context symbol mismatch")
    warnings = ["Funding interval unavailable; no default assumed",
                "Open interest is native quantity; unit unavailable from this endpoint"]
    return {
        "funding_rate": _context_value(row, "lastFundingRate", warnings),
        "funding_interval_hours": None,
        "open_interest": _context_value(interest, "openInterest", warnings, nonnegative=True),
        "open_interest_unit": None,
        "mark_price": _context_value(row, "markPrice", warnings, positive=True),
        "index_price": _context_value(row, "indexPrice", warnings, positive=True),
        "warnings": warnings,
    }


def _hyperliquid_context(symbol):
    dex = symbol.split(":", 1)[0] if ":" in symbol else ""
    data = _hl("metaAndAssetCtxs", dex=dex)
    if not isinstance(data, list) or len(data) != 2:
        raise ValueError("expected metadata and contexts")
    universe = _objects(data[0]["universe"], ("name",))
    contexts = _objects(data[1])
    if len(universe) != len(contexts):
        raise ValueError("context count mismatch")
    matches = [(asset, context) for asset, context in zip(universe, contexts)
               if asset["name"] == symbol]
    if len(matches) != 1:
        raise ValueError("unknown or duplicate instrument")
    asset, row = matches[0]
    if asset.get("isDelisted") or asset.get("isDeleted") or asset.get("isActive") is False:
        raise ValueError("inactive instrument")
    # Native perps: one contract is one underlying unit. HIP-3 deployers choose
    # their own contract specifications; never assume an equity is one share.
    warnings = (["HIP-3 open interest unit unspecified; raw quantity retained"] if dex else [])
    return {
        "funding_rate": _context_value(row, "funding", warnings),
        "funding_interval_hours": 1,
        "open_interest": _context_value(row, "openInterest", warnings, nonnegative=True),
        "open_interest_unit": None if dex else "base_asset",
        "mark_price": _context_value(row, "markPx", warnings, positive=True),
        "index_price": _context_value(row, "oraclePx", warnings, positive=True),
        "warnings": warnings,
    }


def _sma_seeded_ema(values, period):
    value = sum(values[:period]) / period
    alpha = 2 / (period + 1)
    for close in values[period:]:
        value += alpha * (close - value)
    return value


def _context_ema_bias(exchange, symbol, interval, now):
    rows = fetch_exchange_candles(exchange, symbol, interval, 80)
    _objects(rows)
    if any(any(key in row and row[key] != expected for key, expected in (
        ("source", exchange), ("symbol", symbol), ("interval", interval),
    )) for row in rows):
        raise ValueError("bias candle provenance mismatch")
    rows = closed_candles(rows, interval, source=exchange, symbol=symbol, as_of_ms=now)
    if len(rows) < 28:
        return "insufficient"
    closes = [row["c"] for row in rows[-80:]]
    fast, middle, slow = (_sma_seeded_ema(closes, period) for period in (7, 14, 28))
    if fast > middle > slow:
        return "bullish"
    if fast < middle < slow:
        return "bearish"
    return "mixed"


def get_exchange_context(exchange: str, symbol: str) -> dict:
    """Read-only, 30-second snapshot; rates are fractions, OI is never USD-converted.

    ``as_of`` is the local snapshot request time in UTC milliseconds, not a tick
    timestamp. Context requests are independent of the historical-bar pipeline.
    ``base_asset`` labels quantities documented as underlying units. Unspecified
    Binance/HIP-3 unit labels remain null; native OI is never inferred as USD or
    shares. Each bias requests 80 closed bars and requires at least 28; bullish
    means EMA7 > EMA14 > EMA28, bearish is the reverse, equal/unordered is mixed.
    """
    _exchange(exchange)
    pattern = (r"(?:[a-z][a-z0-9_-]{0,31}:)?[A-Za-z0-9][A-Za-z0-9._-]{0,79}"
               if exchange == "hyperliquid" else r"[^\W_]\w{0,79}"
               if exchange == "binance" else r"[A-Z0-9][A-Z0-9_]{0,79}")
    if not isinstance(symbol, str) or not re.fullmatch(pattern, symbol):
        raise ValueError("invalid exchange symbol")
    now = int(time.time() * 1000)
    cached = _CONTEXT_CACHE.get((exchange, symbol))
    if cached is not None and 0 <= now - cached["as_of"] < 30_000:
        return copy.deepcopy(cached)
    provider = {"bybit": _bybit_context, "binance": _binance_context,
                "hyperliquid": _hyperliquid_context}[exchange]
    result = _safe_call(exchange, provider, symbol)
    result.update(exchange=exchange, symbol=symbol, as_of=now)
    result["ema_bias"] = {}
    for tf in ("60", "240", "D"):
        try:
            result["ema_bias"][tf] = _safe_call(
                exchange, _context_ema_bias, exchange, symbol, tf, now)
        except ExchangeDataError:
            result["ema_bias"][tf] = "unavailable"
            result["warnings"].append(f"EMA bias {tf}: closed candle data unavailable")
    _CONTEXT_CACHE[(exchange, symbol)] = copy.deepcopy(result)
    return result


def _native_forming_intervals(exchange: str) -> set:
    """Intervals the venue reports directly, with no client-side aggregation.

    A forming-bar preview is only honest where a real in-progress exchange
    bar exists; custom chart aggregations are intentionally excluded.
    Hyperliquid officially supports a native 3m candleSnapshot interval.
    """
    return {"1", "3", "5", "15", "30", "60", "120", "240", "D", "W"}


def fetch_forming_candle(exchange: str, symbol: str, interval: str) -> dict | None:
    """The venue's own currently in-progress bar, or None if not available.

    Never fabricates a partial candle: returns the real exchange-reported
    cumulative OHLCV mid-bar, or None for unsupported timeframes and for
    the rare moment right after a bar boundary rolls over.
    """
    _exchange(exchange)
    if interval not in _native_forming_intervals(exchange):
        return None
    known = {row["symbol"] for row in _CACHE.get(exchange, {}).get("markets", [])}
    pattern = (
        r"(?:[a-z][a-z0-9_-]{0,31}:)?[A-Za-z0-9][A-Za-z0-9._-]{0,79}"
        if exchange == "hyperliquid"
        else r"[^\W_]\w{0,79}" if exchange == "binance"
        else r"[A-Z0-9][A-Z0-9_]{0,79}"
    )
    if not isinstance(symbol, str) or (
        symbol not in known and not re.fullmatch(pattern, symbol)
    ):
        raise ValueError("invalid exchange symbol")
    duration = interval_ms(interval)
    now = int(time.time() * 1000)
    return _safe_call(exchange, _forming, exchange, symbol, interval, duration, now)


def _forming_arrays(raw):
    if not isinstance(raw, list) or any(
        not isinstance(r, list) or len(r) < 6 or any(isinstance(v, bool) for v in r[:6])
        for r in raw
    ):
        raise ValueError("expected candle arrays")
    return [{"ts": _forming_timestamp(r[0]),
             **dict(zip("ohlcv", map(float, r[1:6])))} for r in raw]


def _forming_timestamp(value):
    if isinstance(value, bool) or not (
        type(value) is int or isinstance(value, str) and re.fullmatch(r"[0-9]+", value)
    ):
        raise ValueError("invalid timestamp")
    return int(value)


def _forming(exchange, symbol, interval, duration, now):
    mapped = {
        "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
        "60": "1h", "120": "2h", "240": "4h", "D": "1d", "W": "1w",
    }[interval]
    if exchange == "hyperliquid":
        raw = _hl(
            "candleSnapshot",
            req={"coin": symbol, "interval": mapped,
                 "startTime": now - 2 * duration, "endTime": now},
        )
        _objects(raw)
        if any(any(key in r and r[key] != expected for key, expected in (
            ("s", symbol), ("i", mapped), ("source", exchange),
            ("symbol", symbol), ("interval", interval),
        )) for r in raw):
            raise ValueError("candle provenance mismatch")
        if any(isinstance(r[k], bool) for r in raw for k in "ohlcv"):
            raise ValueError("invalid candle numeric type")
        rows = [{"ts": _forming_timestamp(r["t"]),
                 **{k: float(r[k]) for k in "ohlcv"}} for r in raw]
        if any("T" in r and _forming_timestamp(r["T"]) != row["ts"] + duration - 1
               for r, row in zip(raw, rows)):
            raise ValueError("candle close time mismatch")
    elif exchange == "bybit":
        result = _bybit("/v5/market/kline", symbol=symbol, interval=interval, limit=2)
        if any(key in result and result[key] != expected for key, expected in (
            ("category", "linear"), ("symbol", symbol), ("source", exchange),
            ("interval", interval),
        )):
            raise ValueError("candle provenance mismatch")
        raw = result["list"]
        rows = _forming_arrays(raw)
    else:
        raw = _request("binance", "/fapi/v1/klines",
                       params={"symbol": symbol, "interval": mapped, "limit": 2})
        if not isinstance(raw, list):
            raise ValueError("expected candle list")
        rows = _forming_arrays(raw)
        if any(len(r) > 6 and _forming_timestamp(r[6]) != row["ts"] + duration - 1
               for r, row in zip(raw, rows)):
            raise ValueError("candle close time mismatch")
    rows.sort(key=lambda row: row["ts"])
    # Unix epoch was Thursday; venue weeks begin Monday 00:00 UTC.
    anchor = 345_600_000 if interval == "W" else 0
    if any(row["ts"] < 0 or row["ts"] > now
           or (row["ts"] - anchor) % duration for row in rows):
        raise ValueError("invalid candle open time")
    if any(b["ts"] - a["ts"] != duration for a, b in pairwise(rows)):
        raise ValueError("duplicate or missing candle timestamps")
    for item in rows:
        o, h, low, c, volume = (item[k] for k in "ohlcv")
        if (not all(math.isfinite(v) for v in (o, h, low, c, volume))
                or min(o, h, low, c) <= 0 or volume < 0
                or low > min(o, c) or h < max(o, c) or low > h):
            raise ValueError("invalid candle OHLCV")
    candidates = [row for row in rows if row["ts"] <= now < row["ts"] + duration]
    if not candidates:
        return None
    row = max(candidates, key=lambda r: r["ts"])
    o, h, low, c, volume = (row[k] for k in "ohlcv")
    return {"ts": row["ts"], "close_ts": row["ts"] + duration, "o": o, "h": h,
            "l": low, "c": c, "v": volume, "is_forming": True,
            "source": exchange, "symbol": symbol, "interval": interval}


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
