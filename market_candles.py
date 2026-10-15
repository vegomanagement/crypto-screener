"""Provider-independent closed-candle contract (timestamps are UTC milliseconds)."""
from __future__ import annotations

import math


INTERVAL_MS = {
    "1": 60_000, "3": 180_000, "5": 300_000, "15": 900_000,
    "30": 1_800_000, "60": 3_600_000, "120": 7_200_000,
    "240": 14_400_000, "D": 86_400_000, "W": 604_800_000,
}


def interval_ms(interval: str) -> int:
    """Reject unsupported timeframes instead of relabeling hourly candles."""
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported candle interval: {interval}")
    return INTERVAL_MS[interval]


def closed_candles(rows: list[dict], interval: str, *, source: str,
                   symbol: str, as_of_ms: int) -> list[dict]:
    """Keep genuine provider open times and admit only fully closed bars."""
    duration = interval_ms(interval)
    result = []
    for row in rows:
        ts = int(row["ts"])
        close_ts = ts + duration
        if close_ts > as_of_ms:
            continue
        values = [float(row[k]) for k in ("o", "h", "l", "c", "v")]
        o, h, low, c, volume = values
        if (not all(math.isfinite(v) for v in values)
                or min(o, h, low, c) <= 0 or volume < 0
                or low > min(o, c) or h < max(o, c) or low > h):
            raise ValueError("invalid candle OHLCV")
        result.append({**row, "ts": ts, "close_ts": close_ts,
                       "source": source, "symbol": symbol,
                       "interval": interval, "closed": True})
    result.sort(key=lambda row: row["ts"])
    if any(b["ts"] - a["ts"] != duration for a, b in zip(result, result[1:])):
        raise ValueError("duplicate or missing candle timestamps")
    return result
