"""Synthetic Wyckoff schematics for the tests (deterministic, no randomness)."""
from __future__ import annotations

import math

STEP = 3_600_000
T0 = 1_700_000_000_000


def _bar(i, o, c, v, wick=0.4, lo=None, hi=None, delta=None):
    h = max(o, c) + wick if hi is None else hi
    low = min(o, c) - wick if lo is None else lo
    bar = {"ts": T0 + i * STEP, "close_ts": T0 + (i + 1) * STEP, "o": o, "h": h, "l": low, "c": c,
           "v": float(v)}
    if delta is not None:
        bar["delta"] = float(delta)
    return bar


def _path(rows, start, closes, v, wobble=0.0):
    prev = start
    for k, c in enumerate(closes):
        c = c + wobble * math.sin(k * 1.7)
        rows.append(_bar(len(rows), prev, c, v))
        prev = c
    return prev


def accumulation(with_delta=False):
    """Returns (candles, marks) where marks are the bar indexes of the planted events."""
    rows, marks = [], {}
    prev = _path(rows, 100.0, [100 - 0.33 * k for k in range(1, 61)], 100, 0.15)   # decline → ~80
    marks["SC"] = len(rows)
    rows.append(_bar(len(rows), prev, 77.0, 420, lo=74.0, hi=prev + 0.3,
                     delta=-120 if with_delta else None))
    prev = _path(rows, 77.0, [77 + 0.9 * k for k in range(1, 9)], 160)              # AR → 84.2
    marks["AR"] = len(rows) - 1
    prev = _path(rows, prev, [84.2 - 0.8 * k for k in range(1, 8)], 110)             # reaction → 78.6
    marks["ST"] = len(rows)
    rows.append(_bar(len(rows), prev, 76.4, 120, lo=75.2))                           # ST
    prev = _path(rows, 76.4, [76.4 + 0.5 * k for k in range(1, 9)], 100)
    for k in range(30):                                                              # phase B
        rows.append(_bar(len(rows), prev, 80 + 2.4 * math.sin(k / 3), 100))
        prev = rows[-1]["c"]
    prev = _path(rows, prev, [prev - (prev - 76.0) * k / 6 for k in range(1, 7)], 100)
    marks["Spring"] = len(rows)
    rows.append(_bar(len(rows), prev, 74.3, 140, lo=72.6, delta=-20 if with_delta else None))
    rows.append(_bar(len(rows), 74.3, 76.2, 130, delta=60 if with_delta else None))  # reclaim
    prev = _path(rows, 76.2, [76.2 + 0.6 * k for k in range(1, 6)], 90)
    prev = _path(rows, prev, [prev - 0.7 * k for k in range(1, 5)], 80)              # test → ~76.4
    marks["Test"] = len(rows) - 1
    prev = _path(rows, prev, [prev + 0.9 * k for k in range(1, 9)], 110)             # → ~83.6
    marks["SOS"] = len(rows)
    rows.append(_bar(len(rows), prev, 86.2, 260, delta=90 if with_delta else None))
    prev = _path(rows, 86.2, [86.2 - 0.5 * k for k in range(1, 3)], 100)             # LPS → 85.2
    marks["LPS"] = len(rows) - 1
    prev = _path(rows, prev, [prev + 0.8 * k for k in range(1, 15)], 120)            # markup → 96.4
    marks["E"] = next(i for i in range(marks["LPS"], len(rows)) if rows[i]["c"] >= 84.2 + 10.2)
    return rows, marks


def mirror(candles, pivot=200.0):
    """Price-mirror a series (accumulation → distribution); volumes unchanged, delta flips."""
    out = []
    for b in candles:
        m = {**b, "o": pivot - b["o"], "c": pivot - b["c"], "h": pivot - b["l"], "l": pivot - b["h"]}
        if b.get("delta") is not None:
            m["delta"] = -b["delta"]
        out.append(m)
    return out


def trend(n=220, up=True, spikes=True):
    """A clean trend with pullbacks and high-volume trend bars (must not be called a range)."""
    rows, prev = [], 100.0
    for k in range(n):
        c = prev * (1 + (0.004 if up else -0.004) + 0.003 * math.sin(k / 2.5))
        v = 450 if spikes and k % 37 == 20 else 100
        rows.append(_bar(len(rows), prev, c, v, wick=0.2))
        prev = c
    return rows
