"""Seconds candles (1s/5s/15s/30s) from real trades only.

Pure aggregation, no I/O. A bucket opens at ``time_ms // step_ms * step_ms`` (UTC,
epoch-aligned; the same formula as footprint bars). A second without trades has no
bar: gaps stay gaps and nothing is interpolated. A bucket is ``complete`` only when
the trade window provably started before it (``oldest_ms < open``); the bucket that
contains the oldest fetched trade is partial and is dropped, never shown as whole.
"""
from __future__ import annotations

from decimal import Decimal

from footprint import fmt

SECONDS_INTERVALS = {"1s": 1, "5s": 5, "15s": 15, "30s": 30}


def bucket_open(time_ms: int, step_ms: int) -> int:
    return time_ms // step_ms * step_ms


def aggregate(trades, step_ms: int, *, oldest_ms: int | None = None) -> list[dict]:
    """Aggregate normalized trades (footprint ``Trade``) into ``step_ms`` buckets.

    Open/close follow the trade identity order when ids exist (Binance aggTrade id),
    otherwise time then input order, so out-of-order delivery cannot swap them.
    """
    if step_ms <= 0 or step_ms % 1000:
        raise ValueError("step must be a positive whole number of seconds")
    buckets: dict[int, dict] = {}
    for index, trade in enumerate(trades):
        key = (trade.ident if isinstance(trade.ident, int) else trade.time_ms, trade.time_ms, index)
        o = bucket_open(trade.time_ms, step_ms)
        bar = buckets.get(o)
        if bar is None:
            bar = buckets[o] = {"t": o, "first": key, "last": key, "o": trade.price, "c": trade.price,
                                "h": trade.price, "l": trade.price, "v": Decimal(0),
                                "buy_v": Decimal(0), "n": 0, "first_id": None, "last_id": None}
        if key < bar["first"]:
            bar["first"], bar["o"] = key, trade.price
        if key > bar["last"]:
            bar["last"], bar["c"] = key, trade.price
        bar["h"] = max(bar["h"], trade.price)
        bar["l"] = min(bar["l"], trade.price)
        bar["v"] += trade.qty
        if trade.side == "ask":
            bar["buy_v"] += trade.qty
        bar["n"] += 1
        if isinstance(trade.ident, int):
            bar["first_id"] = trade.ident if bar["first_id"] is None else min(bar["first_id"], trade.ident)
            bar["last_id"] = trade.ident if bar["last_id"] is None else max(bar["last_id"], trade.ident)
    out = []
    for o in sorted(buckets):
        if oldest_ms is not None and not oldest_ms < o:
            continue  # the window starts inside this bucket → partial, never shown as whole
        bar = buckets[o]
        out.append({"t": o, "o": fmt(bar["o"]), "h": fmt(bar["h"]), "l": fmt(bar["l"]), "c": fmt(bar["c"]),
                    "v": fmt(bar["v"]), "buy_v": fmt(bar["buy_v"]), "n": bar["n"],
                    "first_id": bar["first_id"], "last_id": bar["last_id"]})
    return out


def binance_backfill(service, symbol: str, *, minutes: int, seconds: int = 1,
                     max_requests: int = 20) -> dict:
    """Backfill payload from Binance aggTrades for the last ``minutes`` (bounded)."""
    if seconds not in SECONDS_INTERVALS.values():
        raise ValueError("seconds must be one of 1, 5, 15, 30")
    if not 1 <= minutes <= 15:
        raise ValueError("minutes must be 1..15")
    as_of_hint = service._clock()
    since = as_of_hint - minutes * 60_000
    trades, info = service.binance_trades_since(symbol, since, max_requests=max_requests)
    step = seconds * 1000
    bars = aggregate(trades, step, oldest_ms=info["oldest_ms"])
    forming_open = bucket_open(info["as_of"], step)
    for bar in bars:
        bar["forming"] = bar["t"] == forming_open
    complete_from = None if info["oldest_ms"] is None else bucket_open(info["oldest_ms"], step) + step
    warnings = list(info["warnings"])
    if info["truncated"] and complete_from is not None and complete_from > since:
        warnings.append("Request budget reached; backfill starts later than requested.")
    warnings.append("Newest bar is a REST snapshot as of 'as_of'; live trades after last_trade_id complete it.")
    return {"exchange": "binance", "symbol": symbol, "seconds": seconds, "source": "binance aggTrades",
            "bars": bars, "requested_from_ms": since, "complete_from_ms": complete_from,
            "as_of": info["as_of"], "last_trade_id": info["last_trade_id"], "requests": info["requests"],
            "truncated": info["truncated"], "trades": len(trades), "warnings": warnings}
