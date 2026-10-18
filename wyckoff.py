"""Deterministic Wyckoff trading-range (TR) and schematic-event detection — pure, no I/O.

Input: closed candles ``{ts, close_ts, o, h, l, c, v[, delta]}`` oldest first; ``delta`` is the
bar's taker-buy minus taker-sell volume when the caller has it (Binance kline field 9 or a
footprint), otherwise the delta checks are skipped. Every event carries its bar time,
price, the rule that fired, ``known_at`` (close of the bar at which the rule could fire,
never later than the last candle) and a confidence in [0, 1].

Accumulation is detected directly; distribution runs the same rules on the price-mirrored
series (h' = −l, l' = −h, c' = −c, delta' = −delta) and maps the labels back, so both
schematics obey exactly symmetric rules. Nothing is forced: no climax followed by a
range, a breakdown/failed breakout, or a range that is too small → ``status`` unclear.
"""
from __future__ import annotations

from wyckoff_rules import ACC_LABELS, DIST_LABELS, WORDS, guidance, phase_label

PARAMS = {
    "vol_window": 20,        # relative volume rv = v / mean(v of the previous 20 bars)
    "lookback": 30,          # climax = lowest low of the last 30 bars after a decline
    "trend_min_atr": 4.0,    # ... of at least 4 ATR from the window's highest high
    "climax_rv": 2.0,        # climax volume ≥ 2× average
    "climax_spread": 1.2,    # climax range ≥ 1.2 ATR
    "ar_max": 25,            # automatic rally must top out within 25 bars of the climax
    "ar_pullback": 0.35,     # AR is confirmed by a close giving back 35% of the rally
    "min_height_atr": 1.5,   # TR height ≥ 1.5 ATR
    "st_zone": 0.3,          # ST: low within 30% of the TR height above support
    "spring_eps_atr": 0.05,  # spring: low below support by more than 0.05 ATR
    "spring_reclaim": 2,     # ... and a close back above support within 2 bars
    "break_frac": 0.3,       # breakdown: close below support − max(0.3·height, 1 ATR)
    "sos_rv": 1.2,           # SOS volume ≥ 1.2× average or range ≥ 1 ATR
    "markup_bars": 12,       # phase E: held above the creek 12 bars after an LPS, or
}                            # a close ≥ creek + one TR height (measured move)


def _atr_series(h, low, c, period=14):
    n = len(c)
    out = [None] * n
    if n <= period:
        return out
    trs = [h[0] - low[0]] + [max(h[i] - low[i], abs(h[i] - c[i - 1]), abs(low[i] - c[i - 1]))
                             for i in range(1, n)]
    value = sum(trs[1:period + 1]) / period
    out[period] = value
    for i in range(period + 1, n):
        value = (value * (period - 1) + trs[i]) / period
        out[i] = value
    return out


class _Series:
    """Per-bar arrays of one (possibly mirrored) candle series."""

    def __init__(self, candles, mirror, p):
        s = -1.0 if mirror else 1.0
        self.ts = [c["ts"] for c in candles]
        self.known = [c.get("close_ts", c["ts"]) for c in candles]
        self.o = [s * c["o"] for c in candles]
        self.c = [s * c["c"] for c in candles]
        self.h = [s * (c["l"] if mirror else c["h"]) for c in candles]
        self.l = [s * (c["h"] if mirror else c["l"]) for c in candles]  # noqa: E741
        self.v = [c["v"] for c in candles]
        self.d = [None if c.get("delta") is None else s * c["delta"] for c in candles]
        self.atr = _atr_series(self.h, self.l, self.c)
        w, n = p["vol_window"], len(candles)
        self.rv = [None] * n
        run = sum(self.v[:w])
        for i in range(w, n):
            self.rv[i] = self.v[i] / (run / w) if run > 0 else None
            run += self.v[i] - self.v[i - w]

    def a(self, i):  # ATR known before bar i (falls back to bar i's own)
        return self.atr[i - 1] if i > 0 and self.atr[i - 1] else self.atr[i]

    def clv(self, i):
        rng = self.h[i] - self.l[i]
        return 0.5 if rng <= 0 else (self.c[i] - self.l[i]) / rng

    def spread(self, i):
        a = self.a(i)
        return (self.h[i] - self.l[i]) / a if a else None

    def dratio(self, i):
        return None if self.d[i] is None or not self.v[i] else self.d[i] / self.v[i]


def _clip(x):
    return max(0.0, min(1.0, x))


def _climax(x, i, p):
    lb, a, rv, sp = p["lookback"], x.a(i), x.rv[i], x.spread(i)
    if i < lb or not a or rv is None or sp is None:
        return False
    return (rv >= p["climax_rv"] and sp >= p["climax_spread"]
            and x.l[i] <= min(x.l[i - lb:i])
            and max(x.h[i - lb:i]) - x.l[i] >= p["trend_min_atr"] * a)


def _ev(x, key, i, price, rule, conf, known_i=None, **extra):
    rec = {"key": key, "i": i, "price": price, "rule": rule, "confidence": round(_clip(conf), 2),
           "known_i": i if known_i is None else known_i,
           "volume_rel": None if x.rv[i] is None else round(x.rv[i], 2),
           "spread_atr": None if x.spread(i) is None else round(x.spread(i), 2),
           "close_pos": round(x.clv(i), 2)}
    if x.dratio(i) is not None:
        rec["delta_ratio"] = round(x.dratio(i), 3)
    rec.update(extra)
    return rec


def _preliminary(x, s, sup, p):
    """PS: the last high-volume down bar or long lower wick above the climax low before it."""
    for j in range(s - 2, max(p["vol_window"], s - 40) - 1, -1):
        if (x.rv[j] or 0) >= 1.5 and x.l[j] > sup and (x.c[j] < x.o[j] or x.clv(j) >= 0.6) \
                and x.l[j] <= min(x.l[max(0, j - 3):j + 4]):
            return _ev(x, "PS", j, x.l[j], "rv ≥ 1.5 local {lo} {above} the climax {lo}, ≤ 40 bars before it", 0.35 + 0.15 * (x.clv(j) >= 0.5), known_i=s)
    return None


def _build(x, s, p):
    """One accumulation attempt from the climax at bar ``s`` (None = no range followed)."""
    n, a_s, sup = len(x.c), x.a(s), x.l[s]
    sc_conf = (0.4 + 0.2 * _clip((x.rv[s] - 2) / 2) + 0.2 * _clip((x.spread(s) - 1.2) / 1.0)
               + 0.2 * (x.clv(s) >= 0.4) + 0.1 * ((x.dratio(s) or 0) < -0.1))
    events = [e for e in (_preliminary(x, s, sup, p),) if e]
    events.append(_ev(x, "SC", s, sup, f"rv {x.rv[s]:.1f} ≥ {p['climax_rv']}, range "
                      f"{x.spread(s):.1f} ≥ {p['climax_spread']} ATR, {{lowest}} {{lo}} of "
                      f"{p['lookback']} bars after a ≥ {p['trend_min_atr']:g} ATR {{decline}}", sc_conf))
    out = {"s": s, "sup": sup, "res": None, "height": None, "events": events, "end": None,
           "failed": None, "phase": "A", "pending": None}
    top, a_i, confirm = x.c[s], None, None  # the AR is a later bar, never the climax bar itself
    for j in range(s + 1, min(n, s + p["ar_max"] + 1)):
        if x.h[j] > top:
            top, a_i = x.h[j], j
        if a_i is not None and j > a_i and x.c[j] <= top - p["ar_pullback"] * (top - sup):
            confirm = j
            break
        if x.c[j] < sup:
            return None  # no rally: the decline simply continued
    if confirm is None:
        if s + p["ar_max"] < n:
            return None  # rallied for 25 bars without a reaction: a V-reversal, not a range
        out["pending"] = "waiting for the automatic rally to top out (AR)"
        return out
    height = top - sup
    if height < p["min_height_atr"] * a_s:
        return None
    res, tol = top, max(p["break_frac"] * height, a_s)
    out.update(res=res, height=height)
    events.append(_ev(x, "AR", a_i, top, f"{{highest}} {{hi}} after the climax, confirmed by a close "
                      f"giving back {p['ar_pullback']:.0%} of the {{rally}}", 0.5 + 0.3 * _clip(
                          height / (3 * a_s)) + 0.2 * (x.v[a_i] < x.v[s]), known_i=confirm))
    st = spring = sos = lps = None
    below = 0
    for j in range(confirm + 1, n):
        a = x.a(j)
        if sos is None:
            if x.c[j] < sup - tol or (x.c[j] < sup and below >= p["spring_reclaim"]):
                out["failed"] = {"i": j, "rule": (f"close {{below}} {{support}} by more than {tol:.6g}" if x.c[j] < sup - tol
                                                       else "3 closes {below} {support}") + ": {breakdown}"}
                break
            below = below + 1 if x.c[j] < sup else 0
            if st is None and x.l[j] <= sup + p["st_zone"] * height and x.l[j] >= sup - 0.1 * height \
                    and x.c[j] >= sup and j > a_i:
                k = min(range(j, min(n, j + 3)), key=lambda q: x.l[q])
                if x.l[k] >= sup - 0.1 * height and x.c[k] >= sup - 0.1 * height:
                    st = _ev(x, "ST", k, x.l[k], f"retest within {p['st_zone']:.0%} of the TR height of "
                             "the climax {lo} on lower volume than the climax",
                             0.5 + 0.3 * (x.v[k] < 0.7 * x.v[s]) + 0.2 * (x.l[k] >= sup),
                             known_i=min(n - 1, k + 1) if k > j else j)
                    events.append(st)
                    continue
            if st and spring is None and x.l[j] < sup - p["spring_eps_atr"] * a:
                rec = next((q for q in range(j, min(n, j + p["spring_reclaim"] + 1))
                            if x.c[q] > sup), None)
                if rec is None:
                    if j + p["spring_reclaim"] >= n:
                        out["pending"] = "price under support: a close back above it would make a Spring"
                    continue
                k = min(range(j, rec + 1), key=lambda q: x.l[q])
                spring = _ev(x, "Spring", k, x.l[k], f"{{lo}} {{below}} {{support}}, close back {{above}} it "
                             f"within {p['spring_reclaim']} bars", 0.5 + 0.2 * (rec == k)
                             + 0.15 * (x.clv(rec) >= 0.5) + 0.15 * ((x.dratio(rec) or 0) > 0 or
                                                                    (x.rv[k] or 9) < 1.0),
                             known_i=rec)
                events.append(spring)
                continue
            if spring and spring.get("test") is None and j > spring["known_i"] \
                    and max(x.h[spring["known_i"]:j]) >= sup + 0.3 * height \
                    and x.l[j] <= sup + 0.2 * height and x.l[j] > spring["price"]:
                spring["test"] = True
                events.append(_ev(x, "Test", j, x.l[j], "{higher} {lo} near {support} after the {spring}",
                                  0.5 + 0.3 * (x.v[j] < x.v[spring["i"]]) + 0.2 * (x.c[j] > sup)))
            if x.c[j] > res + 0.05 * a:
                if st is None:
                    return None  # broke out before any test: a V-reversal, not a range
                strong = (x.rv[j] or 0) >= p["sos_rv"] or (x.spread(j) or 0) >= 1.0
                sos = _ev(x, "SOS", j, x.c[j], "first close {above} the {creek}" +
                          ("" if strong else " on low volume and a narrow range"),
                          (0.4 + 0.3 * _clip((x.rv[j] or 0) - 1) + 0.15 * (x.clv(j) >= 0.6)
                           + 0.15 * ((x.dratio(j) or 0) > 0.1)) if strong else 0.3)
                events.append(sos)
            continue
        if x.c[j] < sup + 0.5 * height:
            out["failed"] = {"i": j, "rule": "close back {below} the TR midpoint after the {sos}: failed {breakout}"}
            break
        if lps is None and x.l[j] <= res + 0.5 * a and x.c[j] >= res - 0.25 * height:
            lps = _ev(x, "LPS", j, x.l[j], "pullback to the {creek} holding {above} it (back-up)",
                      0.5 + 0.3 * (x.v[j] < x.v[sos["i"]]) + 0.2 * (x.c[j] >= res))
            events.append(lps)
        if x.c[j] >= res + height or (lps and j - sos["i"] >= p["markup_bars"]):
            out["end"] = j
            events.append(_ev(x, "E", j, x.c[j], "close {beyond} the {creek} by one TR height" if x.c[j] >= res + height
                              else f"held {{above}} the {{creek}} {p['markup_bars']} bars after the {{lps}}", 0.6))
            break
    out["phase"] = ("E" if out["end"] is not None else "D" if sos else "C" if spring
                    else "B" if st else "A")
    if out["failed"]:
        out["end"] = out["failed"]["i"]
    return out


def _chain(x, p):
    """Structures in time order; a climax inside a live structure belongs to it, not a new one."""
    found, busy_until = [], -1
    for s in range(p["lookback"], len(x.c)):
        if s <= busy_until or not _climax(x, s, p):
            continue
        st = _build(x, s, p)
        if st is None:
            continue
        found.append(st)
        busy_until = st["end"] if st["end"] is not None else len(x.c)
    return found


def analyze(candles, params=None):
    """Wyckoff block for the snapshot / dashboard (see module docstring)."""
    p = {**PARAMS, **(params or {})}
    base = {"source": "wyckoff.py over the chart's closed candles" + (
                " + per-bar taker delta" if any(c.get("delta") is not None for c in candles) else ""),
            "ts": candles[-1]["close_ts"] if candles else None, "bars": len(candles),
            "delta_used": any(c.get("delta") is not None for c in candles),
            "status": "unclear", "schematic": None, "phase": None, "phase_label": None,
            "confidence": None, "confidence_label": None, "tr": None, "creek": None, "ice": None,
            "events": [], "confirm": None, "invalidate": None, "notes": [],
            "reason": None, "params": p}
    if len(candles) < p["lookback"] + p["vol_window"]:
        return {**base, "reason": f"needs ≥ {p['lookback'] + p['vol_window']} candles"}
    chains = {}
    for mirror in (False, True):
        x = _Series(candles, mirror, p)
        chains[mirror] = (x, _chain(x, p))
    picks = []
    for mirror, (x, found) in chains.items():
        other = [o for o in chains[not mirror][1] if o["height"]]
        for st in found:
            # an opposite climax inside an earlier, defined range belongs to that range
            # (e.g. the high-volume SOS bar of an accumulation is not a new buying climax)
            if not any(o["s"] < st["s"] <= (o["end"] if o["end"] is not None else len(candles))
                       for o in other):
                picks.append((st, x, mirror))
    if not picks:
        return {**base, "reason": "no selling or buying climax followed by a trading range in the "
                                  "window — trending or no clear structure"}
    picks.sort(key=lambda t: t[0]["s"])
    latest = picks[-1]
    ranged = [t for t in picks if t[0]["height"] and not t[0]["failed"]]
    # a newer climax still waiting for its AR does not hide a live, defined range
    st, x, mirror = ranged[-1] if latest[0]["pending"] and not latest[0]["height"] and ranged else latest
    return _describe(base, st, x, mirror, len(candles))


def _describe(base, st, x, mirror, n):
    sign = -1.0 if mirror else 1.0
    labels = DIST_LABELS if mirror else ACC_LABELS
    words = WORDS[mirror]
    events = []
    for e in sorted(st["events"], key=lambda e: e["i"]):
        code, name = labels[e["key"]]
        rec = {"event": code, "label": name, "ts": x.ts[e["i"]], "known_at": x.known[e["known_i"]],
               "price": float(f"{sign * e['price']:.8g}"),
               "rule": e["rule"].format(**words),
               "confidence": e["confidence"]}
        rec.update({k: (round(1 - e[k], 2) if k == "close_pos" and mirror else e[k])
                    for k in ("volume_rel", "spread_atr", "close_pos", "delta_ratio") if k in e})
        if "delta_ratio" in rec and mirror:
            rec["delta_ratio"] = -rec["delta_ratio"]
        events.append(rec)
    if mirror:
        sup, res = (-st["res"] if st["res"] is not None else None), -st["sup"]
    else:
        sup, res = st["sup"], st["res"]
    schematic = "distribution" if mirror else "accumulation"
    confs = [e["confidence"] for e in events]
    expected = {"A": 2, "B": 3, "C": 4, "D": 5, "E": 6}[st["phase"]]
    core = sum(e["event"] not in ("PS", "PSY") for e in events)
    conf = round(sum(confs) / len(confs) * min(1.0, core / expected), 2)
    out = {**base, "schematic": schematic, "phase": st["phase"],
           "phase_label": phase_label(schematic, st["phase"]), "events": events,
           "confidence": conf,
           "confidence_label": "high" if conf >= 0.7 else "medium" if conf >= 0.45 else "low",
           "status": "structure", "pending": st["pending"]}
    top = res
    out["tr"] = {"support": float(f"{sup:.8g}") if sup is not None else None,
                 "resistance": float(f"{top:.8g}") if top is not None else None,
                 "height": float(f"{st['height']:.8g}") if st["height"] else None,
                 "start_ts": x.ts[st["s"]], "end_ts": x.ts[st["end"]] if st["end"] is not None else None,
                 "bars": (st["end"] if st["end"] is not None else n - 1) - st["s"] + 1}
    out["creek" if not mirror else "ice"] = out["tr"]["resistance" if not mirror else "support"]
    if st["failed"]:
        out.update(status="unclear", phase=None, phase_label=None,
                   reason=f"{schematic} attempt invalidated at {x.ts[st['failed']['i']]}: "
                          + st["failed"]["rule"].format(**words))
    out["confirm"], out["invalidate"] = guidance(out)
    return out

