"""Synthetic multi-TF fixtures for the MTF rules (reads shaped like mtf.analyze_tf output)."""
from __future__ import annotations

H1 = 3_600_000
T0 = 1_790_000_000_000


def read(tf, *, bias="neutral", score=0, trend="none", breaks=(), wyckoff=None, atr=100.0,
         price=81000.0, candles=(), levels=(), swings=()):
    last = breaks[-1] if breaks else None
    return {"tf": tf, "label": tf, "ts": T0, "price": price, "atr14": atr,
            "bias": {"bias": bias, "score": score, "clear": abs(score) >= 3, "votes": {}, "ema200": None},
            "smc": {"trend": trend, "internal_trend": trend, "last_break": last, "breaks": list(breaks),
                    "swings": list(swings), "sweeps": [], "dealing_range": None,
                    "order_blocks": [], "fvgs": []},
            "wyckoff": wyckoff or {"status": "unclear", "schematic": None, "phase": None, "tr": None,
                                   "events": [], "confidence": None},
            "profile": {}, "levels": list(levels), "_candles": list(candles)}


def wy(schematic, phase, support, resistance, events=(), confidence=0.75):
    return {"status": "structure", "schematic": schematic, "phase": phase, "confidence": confidence,
            "tr": {"support": support, "resistance": resistance, "start_ts": T0 - 100 * H1, "end_ts": None},
            "events": list(events), "creek": None, "ice": None}


def brk(kind, d, level, ts):
    return {"kind": kind, "dir": d, "level": level, "ts": ts, "confirm_ts": ts + 1, "close": level}


def htf_bar(ts, close, span=H1):
    return {"ts": ts, "close_ts": ts + span, "o": close, "h": close + 50, "l": close - 50, "c": close, "v": 1.0}


def live_btc_case(htf_close_after_spring=81099.5):
    """The live 2026-10-18 case: BTC 1h distribution phase A (80780–82900.5) vs 15m accumulation
    phase C with a Spring at 80751 (TR 80780–82092.1)."""
    spring_ts = T0 - 3 * H1
    h1 = read("60", wyckoff=wy("distribution", "A", 80780.0, 82900.5,
                                [{"event": "BC", "price": 82900.5, "ts": T0 - 40 * H1},
                                 {"event": "AR", "price": 80780.0, "ts": T0 - 30 * H1}]),
              atr=246.5, candles=[htf_bar(spring_ts - H1 // 2, htf_close_after_spring)])
    m15 = read("15", wyckoff=wy("accumulation", "C", 80780.0, 82092.1,
                                 [{"event": "SC", "price": 80780.0, "ts": T0 - 30 * H1},
                                  {"event": "Spring", "price": 80751.0, "ts": spring_ts}]), atr=95.8)
    return m15, h1
