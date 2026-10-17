"""Read-only chart data. All timestamps are genuine UTC milliseconds."""
from __future__ import annotations

import math
import re
import time
from collections import defaultdict
from datetime import UTC, datetime

from market_candles import INTERVAL_MS

# Deliberately use only intervals supported by the shared provider contract.
_BASE = {value: value for value in INTERVAL_MS}
_BASE.update({"10": "5", "12": "3", "45": "15", "180": "60",
              "480": "60", "720": "60", "2D": "D", "3D": "D", "M": "D"})
_VALUES = ("1", "3", "5", "10", "12", "15", "30", "45", "60", "120",
           "180", "240", "480", "720", "D", "2D", "3D", "W", "M")


def list_chart_intervals() -> list[dict]:
    """Return UI choices; seconds intervals are trade-built and client-side only."""
    labels = {"D": "D", "2D": "2D", "3D": "3D", "W": "W", "M": "M",
              "60": "1h", "120": "2h", "180": "3h", "240": "4h",
              "480": "8h", "720": "12h"}
    seconds = [{"value": value, "label": value, "available": True, "source": "trades",
                "reason": "Built in the browser from real trades (live stream; Binance also "
                          "backfills recent minutes from aggTrades). Not served by /api/chart."}
               for value in ("1s", "5s", "15s", "30s")]
    return seconds + [
        {"value": value, "label": labels.get(value, f"{value}m"),
         "available": True} for value in _VALUES]


def _duration(interval):
    if interval in INTERVAL_MS:
        return INTERVAL_MS[interval]
    if interval in ("2D", "3D"):
        return int(interval[0]) * INTERVAL_MS["D"]
    return int(interval) * 60_000


def _bounds(ts, interval):
    if interval == "M":
        date = datetime.fromtimestamp(ts / 1000, UTC)
        start = datetime(date.year, date.month, 1, tzinfo=UTC)
        end = datetime(date.year + (date.month == 12), date.month % 12 + 1,
                       1, tzinfo=UTC)
        return int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    duration = _duration(interval)
    # Native weekly bars start Monday; fixed multi-day bars anchor at Unix epoch.
    anchor = 4 * INTERVAL_MS["D"] if interval == "W" else 0
    start = (ts - anchor) // duration * duration + anchor
    return start, start + duration


def _validated(rows, symbol, base, now):
    result, sources, seen = [], set(), set()
    for row in rows:
        try:
            ts, close = row["ts"], row["close_ts"]
            source = row["source"]
            if (type(ts) is not int or type(close) is not int
                    or _bounds(ts, base) != (ts, close)
                    or not isinstance(source, str) or not source.strip()
                    or row.get("symbol", symbol) != symbol
                    or row.get("interval", base) != base
                    or row.get("closed", True) is not True):
                raise ValueError("invalid candle timestamp or provenance")
            values = [float(row[key]) for key in ("o", "h", "l", "c", "v")]
            o, high, low, c, volume = values
            if (not all(math.isfinite(x) for x in values) or min(values[:4]) <= 0
                    or volume < 0 or low > min(o, c) or high < max(o, c)):
                raise ValueError("invalid candle OHLCV")
            if ts in seen:
                raise ValueError("duplicate candle timestamps")
            seen.add(ts)
            sources.add(source)
            if len(sources) > 1:
                raise ValueError("mixed candle providers are not allowed")
            if close <= now:
                result.append(dict(row))
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError("invalid candle data") from exc
    return sorted(result, key=lambda row: row["ts"])


def _aggregate(rows, interval, base):
    if interval == base:
        return rows
    buckets = defaultdict(list)
    for row in rows:
        buckets[_bounds(row["ts"], interval)].append(row)
    result = []
    step = INTERVAL_MS[base]
    for (start, end), group in sorted(buckets.items()):
        if ([row["ts"] for row in group] != list(range(start, end, step))
                or group[-1]["close_ts"] != end):
            continue
        # These boundaries must match actual observed first/last base bars.
        result.append({"ts": group[0]["ts"], "close_ts": group[-1]["close_ts"],
                       "o": group[0]["o"], "h": max(x["h"] for x in group),
                       "l": min(x["l"] for x in group), "c": group[-1]["c"],
                       "v": sum(x["v"] for x in group), "closed": True,
                       "source": group[0]["source"], "symbol": group[0].get("symbol"),
                       "interval": interval})
    return result


def _fvg_zones(candles):
    """Return the latest 100 unfilled residual gaps, in creation order.

    idx is the third candle's index in the returned closed candle snapshot.
    Later wick extremes mitigate; the formation candle itself cannot fill a gap.
    """
    zones = []
    for idx in range(2, len(candles)):
        first, middle, third = candles[idx - 2:idx + 1]
        if first["close_ts"] != middle["ts"] or middle["close_ts"] != third["ts"]:
            continue
        if third["l"] > first["h"]:
            direction, bottom, top = "bull", first["h"], third["l"]
            for later in candles[idx + 1:]:
                top = min(top, later["l"])
        elif third["h"] < first["l"]:
            direction, bottom, top = "bear", third["h"], first["l"]
            for later in candles[idx + 1:]:
                bottom = max(bottom, later["h"])
        else:
            continue
        if top > bottom:
            zones.append({"type": "fvg", "direction": direction, "top": top,
                          "bottom": bottom, "start_ts": third["ts"],
                          "end_ts": candles[-1]["close_ts"], "idx": idx,
                          "mitigated": False})
    return zones[-100:]


def build_chart_snapshot(symbol, interval, limit, fetch_candles, as_of_ms=None, *, exchange=None):
    """Fetch one source without trading, persistence, or provider relabeling."""
    if exchange is not None and exchange not in {"bybit", "binance", "hyperliquid"}:
        raise ValueError("unsupported exchange")
    pattern = (r"[A-Za-z0-9_.:-]{1,80}" if exchange == "hyperliquid" else
               r"[^\W_]\w{0,79}" if exchange == "binance" else
               r"[A-Z0-9_]{1,80}" if exchange else r"[A-Z0-9]+USDT")
    if not isinstance(symbol, str) or not re.fullmatch(pattern, symbol):
        raise ValueError("invalid native symbol for selected exchange")
    if interval not in _BASE:
        raise ValueError(f"unsupported chart interval: {interval}")
    if type(limit) is not int or limit <= 0:
        raise ValueError("limit must be a positive integer")
    now = int(time.time() * 1000) if as_of_ms is None else as_of_ms
    if type(now) is not int:
        raise ValueError("as_of_ms must be an integer millisecond timestamp")
    base = _BASE[interval]
    notes = []
    if interval == "W" and exchange == "hyperliquid":
        # Hyperliquid's native 1w candles open Thursday 00:00 UTC (epoch-aligned); the
        # chart's weeks are Monday 00:00 UTC like Binance/Bybit, so build them from 7
        # complete daily candles (incomplete weeks are dropped, never padded).
        base = "D"
        notes.append("Hyperliquid 1w candles start Thursday 00:00 UTC; Monday-based weeks are "
                     "aggregated from 7 complete daily candles")
    ratio = 31 if interval == "M" else _duration(interval) // INTERVAL_MS[base]
    request_limit = min(1000, limit if base == interval else (limit + 1) * ratio)
    rows = fetch_candles(symbol, base, request_limit)
    validated = _validated(rows, symbol, base, now)
    if exchange is not None and any(row['source'] != exchange for row in validated):
        raise ValueError("candle provider does not match selected exchange")
    candles = _aggregate(validated, interval, base)[-min(limit, 1000):]
    return {"symbol": symbol, "interval": interval, "exchange": exchange,
            "source": validated[0]["source"] if validated else None,
            "closed_only": True, "candles": candles, "zones": _fvg_zones(candles),
            "warnings": notes + ([] if len(candles) >= limit else [
                f"Only {len(candles)} of {limit} requested closed candles available"])}
