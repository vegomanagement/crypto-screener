"""Higher-timeframe bars for the MTF backtests, aggregated from cached Binance 1h klines.

Only COMPLETE buckets are kept (4 × 1h for 4h, 24 for 1d, 7 daily bars for 1w, Monday 00:00 UTC
like Binance/Bybit), so an HTF bar exists only once all of its hours closed; ``close_ts`` is the
bucket end. ``htf_index`` maps an LTF bar to the last HTF bar closed by the LTF bar's close.
"""
from __future__ import annotations

import bisect

DAY = 86_400_000
SPAN = {"60": 3_600_000, "240": 14_400_000, "D": DAY, "W": 7 * DAY}


def aggregate(bars, tf, base_ms):
    span = SPAN[tf]
    anchor = 4 * DAY if tf == "W" else 0     # epoch was a Thursday; weeks start Monday
    need = span // base_ms
    out, cur, group = [], None, []

    def flush():
        if group and len(group) == need:
            out.append({"ts": cur, "close_ts": cur + span, "o": group[0]["o"],
                        "h": max(b["h"] for b in group), "l": min(b["l"] for b in group),
                        "c": group[-1]["c"], "v": sum(b["v"] for b in group),
                        "tb": sum(b.get("tb", 0.0) for b in group)})
    for b in bars:
        start = (b["ts"] - anchor) // span * span + anchor
        if start != cur:
            flush()
            cur, group = start, []
        group.append(b)
    flush()
    return out


def htf_index(ltf, htf):
    """[k] per LTF bar: index of the last HTF bar with close_ts ≤ the LTF bar's close_ts (−1 = none)."""
    ends = [b["close_ts"] for b in htf]
    return [bisect.bisect_right(ends, b["close_ts"]) - 1 for b in ltf]
