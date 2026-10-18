"""smc.py: fractal swings, close-confirmed BOS/CHoCH, sweeps, OB/FVG, dealing range, no look-ahead."""
import math

import smc

STEP = 900_000
T0 = 1_700_000_000_000


def bars(closes, wick=0.3, highs=None, lows=None):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        h = max(o, c) + wick if not highs or highs.get(i) is None else highs[i]
        lo = min(o, c) - wick if not lows or lows.get(i) is None else lows[i]
        out.append({"ts": T0 + i * STEP, "close_ts": T0 + (i + 1) * STEP, "o": o, "h": h, "l": lo, "c": c, "v": 1.0})
        prev = c
    return out


def zigzag(legs, step=1.0):
    path = [legs[0]]
    for a, b in zip(legs, legs[1:]):
        n = max(1, int(abs(b - a) / step))
        path += [a + (b - a) * k / n for k in range(1, n + 1)]
    return path


def test_swings_are_fractals_known_only_after_the_right_bars():
    c = bars(zigzag([100, 110, 104, 116, 108, 120, 112]))
    sw = smc.swings(c, 2, 2)
    for s in sw:
        i = s["i"]
        key = "h" if s["kind"] == "H" else "l"
        win = [x[key] for x in c[i - 2:i + 3]]
        assert s["price"] == (max(win) if key == "h" else min(win))
        assert s["confirm_i"] == i + 2
    labels = [s["label"] for s in smc._label(sw) if s["label"]]
    assert "HH" in labels and "HL" in labels


def test_bos_vs_choch_need_a_close_and_wicks_are_sweeps():
    # up-trend (HH/HL) then a wick above the last high that closes back below → sweep, no break;
    # then a close below the last swing low → bearish CHoCH (first break against the trend).
    path = zigzag([100, 110, 104, 116, 108, 114, 101, 96], step=1.0)
    c = bars(path)
    st = smc.structure(c, 2, 2, tf="15")
    kinds = [(b["kind"], b["dir"]) for b in st["breaks"]]
    assert ("BOS", "bull") in kinds and ("CHoCH", "bear") in kinds
    for b in st["breaks"]:
        bar = c[b["i"]]
        assert (bar["c"] > b["level"]) if b["dir"] == "bull" else (bar["c"] < b["level"])
        prev = [x["c"] for x in c[c.index(bar) - 1:c.index(bar)]]
        assert b["confirm_ts"] == bar["close_ts"] and b["rule"]
        assert prev  # first close beyond: the previous close was not beyond
    # planted sweep: one bar wicks above 116 but closes below it
    i = next(k for k, x in enumerate(path) if k > 30 and abs(x - 112) < 0.6)
    c2 = bars(path, highs={i: 117.5})
    st2 = smc.structure(c2, 2, 2)
    assert any(s["side"] == "high" and s["i"] == i for s in st2["sweeps"])
    assert not any(b["i"] == i for b in st2["breaks"])


def test_order_block_and_fvg_of_the_displacement_and_mitigation():
    path = zigzag([100, 106, 98, 99], step=1.0) + [99.5, 104, 109, 113, 112, 111, 110, 103, 100, 98.5]
    c = bars(path, wick=0.2)
    st = smc.structure(c, 2, 2)
    bull = [b for b in st["breaks"] if b["dir"] == "bull"][-1]
    ob = [z for z in st["obs"] if z["confirm_i"] == bull["i"]][0]
    assert c[ob["i"]]["c"] < c[ob["i"]]["o"] and ob["i"] <= bull["leg_i"] + 0
    assert ob["confirm_ts"] == c[bull["i"]]["close_ts"]
    gaps = [z for z in st["fvgs"] if z["confirm_i"] == bull["i"]]
    assert gaps and all(c[z["i"] + 1]["l"] > c[z["i"] - 1]["h"] for z in gaps)
    assert all(z["bottom"] < z["top"] for z in gaps)
    last = len(c) - 1
    assert smc.zone_state(gaps[0], bull["i"]) == "unmitigated"
    assert smc.zone_state(gaps[0], last) in ("mitigated", "invalidated")


def test_dealing_range_premium_discount():
    c = bars(zigzag([100, 120, 104, 118, 106]))
    st = smc.structure(c, 2, 2)
    dr = smc.dealing_range(c, st["swings"])
    assert dr["low"] < dr["eq"] < dr["high"] and dr["eq"] == (dr["high"] + dr["low"]) / 2
    assert dr["zone"] == ("premium" if c[-1]["c"] > dr["eq"] else "discount")


def test_no_look_ahead_poisoned_future():
    path = [100 + 8 * math.sin(i / 6) + i * 0.05 + 2 * math.sin(i / 1.7) for i in range(400)]
    c = bars(path)
    full = smc.structure(c, 3, 3)
    for cut in (120, 250, 333):
        poisoned = c[:cut + 1] + [dict(x, h=x["h"] * 3, l=x["l"] / 3, c=x["c"] * 2) for x in c[cut + 1:]]
        p = smc.structure(poisoned, 3, 3)
        t = c[cut]["close_ts"]
        key = lambda b: (b["kind"], b["dir"], b["level"], b["i"])  # noqa: E731
        assert [key(b) for b in smc.as_of(full["breaks"], t)] == [key(b) for b in smc.as_of(p["breaks"], t)]
        assert [(s["i"], s["price"]) for s in smc.as_of(full["swings"], t)] == \
            [(s["i"], s["price"]) for s in smc.as_of(p["swings"], t)]
        assert full["trend_at"][:cut + 1] == p["trend_at"][:cut + 1]
        for a, b in zip(smc.as_of(full["obs"], t), smc.as_of(p["obs"], t)):
            assert (a["top"], a["bottom"], smc.zone_state(a, cut)) == (b["top"], b["bottom"], smc.zone_state(b, cut))
        assert len(smc.as_of(full["sweeps"], t)) == len(smc.as_of(p["sweeps"], t))
