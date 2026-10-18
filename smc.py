"""Deterministic SMC swing structure (pure, stdlib, causal) — the core of the MTF layer.

Input: closed candles ``{ts, close_ts, o, h, l, c, v}`` oldest first. Every item carries the bar
time ``ts``, the price, the rule text and ``confirm_ts`` = close of the bar at which the item
became knowable. Nothing uses a bar that closed after ``confirm_ts``, so the state "as of" any
time t is simply the items with ``confirm_ts <= t`` (see ``as_of``).

Rules (long side; short is the mirror):
  swing high   fractal: h[i] > every high of the ``left`` bars before it and >= every high of
               the ``right`` bars after it; knowable at the close of bar i + right.
               EXTERNAL structure uses strength 5/5, INTERNAL structure 2/2 (configurable).
  labels       a swing high is HH when above the previous swing high of the same strength, else
               LH; a swing low is HL when above the previous swing low, else LL.
  BOS / CHoCH  the first CLOSE above the most recent confirmed, unbroken swing high: BOS when the
               structure was already bullish, CHoCH when it was bearish (the first break against
               the trend); the very first break of a series is an 'initial' BOS. A broken swing is
               consumed. Wicks do not break structure.
  sweep        a bar whose high trades above an unbroken swing high but closes at or below it
               (liquidity grab); recorded once per swing, the swing stays intact.
  order block  for a break up: the leg starts at the lowest low between the broken swing and the
               break bar; the OB is the last down candle (c < o) at or up to 10 bars before that
               low, zone = its full high/low range. Mitigated when a later bar trades into it,
               invalidated by a later close below its low.
  FVG          three-bar gaps l[t] > h[t-2] inside the displacement leg (low → break bar),
               zone [h[t-2], l[t]]; mitigated when price trades into it, filled when a later low
               reaches its bottom. Zones older than ZONE_MAX_AGE (500) bars expire.
  dealing range  the latest confirmed swing high and low, each extended by any later extreme;
               equilibrium = 50 %; price above it = premium, below = discount.
"""
from __future__ import annotations

EXTERNAL = (5, 5)
INTERNAL = (2, 2)
OB_LOOKBACK = 10
ZONE_MAX_AGE = 500   # bars after the break; older untouched zones count as expired


def _r(x):
    return None if x is None else float(f"{x:.10g}")


def swings(candles, left, right):
    """Fractal swings: dicts {i, kind 'H'|'L', price, confirm_i} in index order."""
    h = [c["h"] for c in candles]
    lo = [c["l"] for c in candles]
    out = []
    for i in range(left, len(candles) - right):
        if h[i] > max(h[i - left:i]) and h[i] >= max(h[i + 1:i + right + 1]):
            out.append({"i": i, "kind": "H", "price": h[i], "confirm_i": i + right})
        if lo[i] < min(lo[i - left:i]) and lo[i] <= min(lo[i + 1:i + right + 1]):
            out.append({"i": i, "kind": "L", "price": lo[i], "confirm_i": i + right})
    return out


def _label(sw):
    prev = {"H": None, "L": None}
    for s in sw:
        p = prev[s["kind"]]
        if s["kind"] == "H":
            s["label"] = None if p is None else ("HH" if s["price"] > p else "LH")
        else:
            s["label"] = None if p is None else ("HL" if s["price"] > p else "LL")
        prev[s["kind"]] = s["price"]
    return sw


def _order_block(candles, start, j, d):
    """Last opposite candle at/before the leg extreme in [start, j] (d=+1 bull break)."""
    seg = range(start, j + 1)
    k0 = (min(seg, key=lambda q: candles[q]["l"]) if d > 0
          else max(seg, key=lambda q: candles[q]["h"]))
    for q in range(k0, max(-1, k0 - OB_LOOKBACK - 1), -1):
        c = candles[q]
        if (c["c"] < c["o"]) if d > 0 else (c["c"] > c["o"]):
            return q, k0
    return None, k0


def _fvgs(candles, k0, j, d):
    out = []
    for t in range(max(k0 + 2, 2), j + 1):
        a, b = candles[t - 2], candles[t]
        if d > 0 and b["l"] > a["h"]:
            out.append((t, a["h"], b["l"]))
        elif d < 0 and b["h"] < a["l"]:
            out.append((t, b["h"], a["l"]))
    return out


def _zone_state(candles, after, bottom, top, d):
    """(mitigated_i, invalidated_i) of a zone from bar ``after`` + 1 on (None = not yet).

    The scan is bounded by ZONE_MAX_AGE bars; a zone untouched that long is invalidated
    ('expired') at bar after + ZONE_MAX_AGE, which keeps a full-history pass linear."""
    mit = inv = None
    end = after + ZONE_MAX_AGE
    if end < len(candles):
        inv = end
    for q in range(after + 1, min(len(candles), end)):
        c = candles[q]
        if mit is None and (c["l"] <= top if d > 0 else c["h"] >= bottom):
            mit = q
        if (c["c"] < bottom) if d > 0 else (c["c"] > top):
            inv = q
            break
    return mit, inv


def structure(candles, left=5, right=5, tf=None, zones=True):
    """Causal single pass. Returns swings, breaks, sweeps, order blocks, FVGs and the trend."""
    sw = _label(swings(candles, left, right))
    by_confirm = {}
    for s in sw:
        by_confirm.setdefault(s["confirm_i"], []).append(s)
    ts = [c["ts"] for c in candles]
    cts = [c.get("close_ts", c["ts"]) for c in candles]
    last = {"H": None, "L": None}
    trend, breaks, sweeps, obs, fvgs = 0, [], [], [], []
    trend_at = [0] * len(candles)
    for j, bar in enumerate(candles):
        for s in by_confirm.get(j, ()):
            last[s["kind"]] = dict(s, swept=False)
        for kind, d in (("H", 1), ("L", -1)):
            s = last[kind]
            if s is None:
                continue
            beyond_close = bar["c"] > s["price"] if d > 0 else bar["c"] < s["price"]
            beyond_wick = bar["h"] > s["price"] if d > 0 else bar["l"] < s["price"]
            if beyond_close:
                kind_b = "BOS" if trend == d else "CHoCH" if trend == -d else "BOS"
                rec = {"tf": tf, "kind": kind_b, "initial": trend == 0, "dir": "bull" if d > 0 else "bear",
                       "level": _r(s["price"]), "swing_ts": ts[s["i"]], "ts": ts[j], "confirm_ts": cts[j],
                       "close": _r(bar["c"]), "i": j, "swing_i": s["i"],
                       "rule": f"first close {'above' if d > 0 else 'below'} the confirmed swing "
                               f"{'high' if d > 0 else 'low'} ({left}/{right} fractal)"}
                if zones:
                    q, k0 = _order_block(candles, s["i"], j, d)
                    rec["leg_i"] = k0
                    rec["leg_price"] = _r(candles[k0]["l"] if d > 0 else candles[k0]["h"])
                    if q is not None:
                        mit, inv = _zone_state(candles, j, candles[q]["l"], candles[q]["h"], d)
                        obs.append({"tf": tf, "dir": rec["dir"], "ts": ts[q], "i": q, "top": _r(candles[q]["h"]),
                                    "bottom": _r(candles[q]["l"]), "confirm_ts": cts[j], "confirm_i": j,
                                    "break": kind_b, "mitigated_i": mit, "invalidated_i": inv,
                                    "rule": f"last {'down' if d > 0 else 'up'} candle before the "
                                            f"displacement that made the {kind_b}"})
                    for t, bot, top in _fvgs(candles, k0, j, d):
                        mit, inv = _zone_state(candles, j, bot, top, d)
                        fill = next((x for x in range(j + 1, min(len(candles), j + ZONE_MAX_AGE))
                                     if (candles[x]["l"] <= bot if d > 0 else candles[x]["h"] >= top)), None)
                        if fill is None and j + ZONE_MAX_AGE < len(candles):
                            fill = j + ZONE_MAX_AGE
                        fvgs.append({"tf": tf, "dir": rec["dir"], "ts": ts[t - 1], "i": t - 1, "top": _r(top),
                                     "bottom": _r(bot), "confirm_ts": cts[j], "confirm_i": j,
                                     "mitigated_i": mit, "invalidated_i": fill,
                                     "rule": f"3-bar gap inside the displacement that made the {kind_b}"})
                breaks.append(rec)
                trend = d
                last[kind] = None
            elif beyond_wick and not s["swept"]:
                s["swept"] = True
                sweeps.append({"tf": tf, "dir": "bull" if d < 0 else "bear", "side": "high" if d > 0 else "low",
                               "level": _r(s["price"]), "swing_ts": ts[s["i"]], "ts": ts[j],
                               "confirm_ts": cts[j], "i": j,
                               "price": _r(bar["h"] if d > 0 else bar["l"]),
                               "rule": f"wick {'above' if d > 0 else 'below'} the swing "
                                       f"{'high' if d > 0 else 'low'}, close back inside: liquidity grab"})
        trend_at[j] = trend
    for s in sw:
        s.update(tf=tf, ts=ts[s["i"]], confirm_ts=cts[s["confirm_i"]], price=_r(s["price"]),
                 rule=f"{left}/{right} fractal {'high' if s['kind'] == 'H' else 'low'}")
    return {"tf": tf, "left": left, "right": right, "trend": trend, "trend_at": trend_at,
            "swings": sw, "breaks": breaks, "sweeps": sweeps, "obs": obs, "fvgs": fvgs}


def dealing_range(candles, sw, upto=None):
    """Latest confirmed swing high/low as of bar ``upto``, each extended by later extremes."""
    n = len(candles) if upto is None else upto + 1
    hs = [s for s in sw if s["kind"] == "H" and s["confirm_i"] < n]
    ls = [s for s in sw if s["kind"] == "L" and s["confirm_i"] < n]
    if not hs or not ls:
        return None
    sh, sl = hs[-1], ls[-1]
    hi = max([sh["price"]] + [c["h"] for c in candles[sh["i"]:n]])
    lo = min([sl["price"]] + [c["l"] for c in candles[sl["i"]:n]])
    if hi <= lo:
        return None
    price = candles[n - 1]["c"]
    eq = (hi + lo) / 2
    return {"high": _r(hi), "low": _r(lo), "eq": _r(eq), "position": round((price - lo) / (hi - lo), 4),
            "zone": "premium" if price > eq else "discount" if price < eq else "equilibrium",
            "high_ts": candles[sh["i"]]["ts"], "low_ts": candles[sl["i"]]["ts"],
            "rule": "latest confirmed external swing high/low, extended by later extremes; 50 % = equilibrium"}


def zone_state(z, upto):
    """'unmitigated' | 'mitigated' | 'invalidated' as of bar index ``upto`` (no look-ahead)."""
    if z.get("invalidated_i") is not None and z["invalidated_i"] <= upto:
        return "invalidated"
    if z.get("mitigated_i") is not None and z["mitigated_i"] <= upto:
        return "mitigated"
    return "unmitigated"


def as_of(items, t):
    """Items knowable at time t (their confirm_ts <= t)."""
    return [x for x in items if x["confirm_ts"] <= t]
