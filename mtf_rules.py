"""MTF reconciliation rules (pure): Wyckoff nesting of a lower-TF range in the higher-TF context,
and SMC swing divergence / synchronisation across the ladder. Each rule has an id, a plain-
language explanation and 'what would resolve it' triggers. HTF priority: when two TFs disagree
the higher one is the context and the lower one's read is the counter-trend move inside it.

Nesting position of an LTF trading range [ls, lr] in the HTF range [hs, hr] (tol = max(0.5 HTF
ATR, 0.1 HTF height)): at_htf_support when |ls − hs| ≤ tol, at_htf_resistance when |lr − hr| ≤ tol,
inside when hs ≤ ls and lr ≤ hr, above when ls > hr, below when lr < hs, else overlapping.

Wyckoff rules (``W*``; accumulation shown, distribution is the mirror):
  W1 LTF accumulation inside / at the edge of an HTF distribution → counter-trend bounce inside a
     top (a reaccumulation-looking pause); HTF wins.
  W2 LTF accumulation at HTF support while the HTF is bullish (clear bias or accumulation) → aligned.
  W3 LTF Spring below the HTF range low → an HTF Spring candidate only once an HTF bar closes back
     inside (≥ HTF support); an HTF close still below it = HTF breakdown; no closed HTF bar since
     the Spring = pending.
  W4 LTF range while the HTF trends without a range → in the trend direction a continuation
     pause (re-accumulation / re-distribution), against it a counter-trend structure.
  W5 LTF range entirely outside the HTF range → breakout/UTAD-or-Spring ambiguity, resolved by
     the HTF close.
  B1 both TFs have a clear bias and they oppose → counter-trend LTF.
SMC rules (``S*``): S1 LTF CHoCH against an intact HTF trend = divergence (pullback, or the start
of an HTF reversal); S2 later HTF CHoCH in the LTF direction = reversal propagated; S3 the LTF
breaking back in the HTF direction = the pullback ended; S4 LTF trend against the HTF through BOS
= established counter-trend leg.
"""
from __future__ import annotations

from mtf import TF_LABEL, tf_minutes

DIR = {"bull": 1, "bear": -1, "none": 0, "neutral": 0}


def _px(x):
    return f"{x:.10g}" if isinstance(x, (int, float)) else "—"


def lab(tf):
    return TF_LABEL.get(tf, tf)


def wy_dir(w):
    if not w or w.get("status") != "structure":
        return 0
    return 1 if w["schematic"] == "accumulation" else -1


def nest(ltr, htr, hatr):
    ls, lr, hs, hr = ltr.get("support"), ltr.get("resistance"), htr.get("support"), htr.get("resistance")
    if None in (ls, lr, hs, hr):
        return None
    tol = max(0.5 * (hatr or 0), 0.1 * (hr - hs))
    if abs(ls - hs) <= tol:
        return "at_htf_support"
    if abs(lr - hr) <= tol:
        return "at_htf_resistance"
    if hs <= ls and lr <= hr:
        return "inside"
    if ls > hr:
        return "above"
    if lr < hs:
        return "below"
    return "overlapping"


def _htf_close_after(htf, ts):
    """First CLOSED HTF bar containing or after ``ts`` (None while it is still forming)."""
    for c in htf.get("_candles") or []:
        if c["close_ts"] > ts:
            return c
    return None


def wyckoff_rules(lo_tf, lo, hi_tf, hi):
    """Rule hits for one (LTF, HTF) pair. Each: {id, kind conflict|aligned|note, tfs, text, resolve}."""
    out = []
    lw, hw = lo.get("wyckoff") or {}, hi.get("wyckoff") or {}
    ld, hd = wy_dir(lw), wy_dir(hw)
    hb = hi["bias"]
    hbd = DIR[hb["bias"]] if hb["clear"] else 0
    L, H = lab(lo_tf), lab(hi_tf)
    ltr, htr = lw.get("tr") or {}, hw.get("tr") or {}
    pos = nest(ltr, htr, hi.get("atr14")) if ltr and htr else None
    hs, hr = htr.get("support"), htr.get("resistance")

    def hit(rid, kind, text, resolve=()):
        out.append({"id": rid, "kind": kind, "tfs": [lo_tf, hi_tf], "position": pos, "text": text,
                    "resolve": list(resolve)})
    if ld and hd == -ld and pos in ("inside", "at_htf_support", "at_htf_resistance", "overlapping"):
        lw_name, hw_name = lw["schematic"], hw["schematic"]
        up = ld > 0
        hit("W1" if up else "W1m", "conflict",
            f"{L} {lw_name} (phase {lw['phase']}) sits {pos.replace('_', ' ')} of the {H} {hw_name} "
            f"(phase {hw['phase']}, {_px(hs)}–{_px(hr)}): a counter-trend "
            f"{'bounce inside a top' if up else 'dip inside a base'}; the {H} read has priority.",
            [f"{H} close {'above' if up else 'below'} {_px(hr if up else hs)} would invalidate the {H} "
             f"{hw_name} and align with the {L} {lw_name}",
             f"{H} close {'below' if up else 'above'} {_px(hs if up else hr)} would confirm the {H} "
             f"{hw_name}; the {L} {lw_name} would fail"])
    if ld and (hd == ld or hbd == ld) and pos == ("at_htf_support" if ld > 0 else "at_htf_resistance"):
        hit("W2" if ld > 0 else "W2m", "aligned",
            f"{L} {lw['schematic']} at the {H} {'support' if ld > 0 else 'resistance'} "
            f"{_px(hs if ld > 0 else hr)} with the {H} {'bullish' if ld > 0 else 'bearish'}: aligned.",
            [f"{L} close {'above' if ld > 0 else 'below'} {_px(ltr.get('resistance') if ld > 0 else ltr.get('support'))} "
             f"(SOS/SOW) would confirm"])
    for ev in lw.get("events") or []:
        spring = ev["event"] == "Spring"
        if not (spring or ev["event"] == "UTAD") or None in (hs, hr):
            continue
        edge = hs if spring else hr
        if (ev["price"] >= edge) if spring else (ev["price"] <= edge):
            continue
        if pos not in ("at_htf_support" if spring else "at_htf_resistance", "inside", "overlapping"):
            continue          # the LTF range is not at that HTF edge: no nesting to judge
        bar = _htf_close_after(hi, ev["ts"])
        name = "Spring" if spring else "UTAD"
        if bar is None:
            hit("W3" if spring else "W3m", "note",
                f"{L} {name} {_px(ev['price'])} {'below' if spring else 'above'} the {H} range "
                f"{'low' if spring else 'high'} {_px(edge)}: {H} {name} candidate pending the {H} close.",
                [f"{H} close back {'above' if spring else 'below'} {_px(edge)} = {H} {name} candidate"])
        elif (bar["c"] >= edge) if spring else (bar["c"] <= edge):
            against = hd == (-1 if spring else 1)
            hit("W3" if spring else "W3m", "conflict" if against else "aligned",
                f"{L} {name} {_px(ev['price'])} pierced the {H} {'low' if spring else 'high'} {_px(edge)} "
                f"and the {H} bar closed back inside ({_px(bar['c'])}): "
                + (f"inside the {H} {hw['schematic']} this is a test of its edge, not a reversal yet."
                   if against else f"{H} {name} candidate."),
                [f"{H} close {'above' if spring else 'below'} {_px(hr if spring else hs)} would turn it into "
                 f"a {H} {'SOS' if spring else 'SOW'}"])
        else:
            hit("W3" if spring else "W3m", "conflict",
                f"{L} {name} {_px(ev['price'])} is {'below' if spring else 'above'} the {H} range and the "
                f"{H} bar closed {'below' if spring else 'above'} it ({_px(bar['c'])}): a {H} "
                f"{'breakdown' if spring else 'breakout'}, not a {name}.",
                [f"{H} close back {'above' if spring else 'below'} {_px(edge)} would reopen the {H} {name} case"])
    if ld and not hd and hbd:
        same = hbd == ld
        hit("W4" if same else "W4c", "aligned" if same else "conflict",
            f"{L} {lw['schematic']} while the {H} trends {'up' if hbd > 0 else 'down'} without a range: "
            + (f"a continuation pause (re-{'accumulation' if ld > 0 else 'distribution'})." if same
               else "a counter-trend structure inside the higher-TF trend."),
            [] if same else [f"{H} CHoCH {'up' if ld > 0 else 'down'} would align the {H} with the {L}"])
    if ld and pos in ("above", "below"):
        hit("W5", "note", f"{L} range is entirely {pos} the {H} range {_px(hs)}–{_px(hr)}: "
                          f"{'breakout or UTAD' if pos == 'above' else 'breakdown or Spring'} — the {H} close decides.",
            [f"{H} close back inside {_px(hs)}–{_px(hr)} = failed {'breakout (UTAD)' if pos == 'above' else 'breakdown (Spring)'}"])
    lb = lo["bias"]
    if lb["clear"] and hb["clear"] and lb["bias"] != hb["bias"]:
        hit("B1", "conflict", f"{L} bias {lb['bias']} ({lb['score']:+d}/5) against {H} bias {hb['bias']} "
                              f"({hb['score']:+d}/5): the {L} move is counter-trend.",
            [f"{H} bias flipping {lb['bias']} (e.g. a {H} CHoCH) would align them"])
    return out


def _chochs(s, d):
    return [b for b in s.get("breaks") or [] if b["kind"] == "CHoCH" and DIR[b["dir"]] == d]


def smc_pair(lo_tf, lo, hi_tf, hi):
    """Structural relation of an adjacent (LTF, HTF) pair."""
    ls, hs = lo["smc"], hi["smc"]
    ld, hd = DIR[ls["trend"]], DIR[hs["trend"]]
    L, H = lab(lo_tf), lab(hi_tf)
    lb, hb = ls.get("last_break"), hs.get("last_break")
    base = {"ltf": lo_tf, "htf": hi_tf, "ltf_trend": ls["trend"], "htf_trend": hs["trend"]}
    hsw = {k: [s for s in hs["swings"] if s["kind"] == k] for k in "HL"}
    if not ld or not hd:
        return {**base, "status": "no_structure", "divergence": False,
                "text": f"{L if not ld else H} has no external structure break yet", "resolve": []}
    if ld == hd:
        status, text = "aligned", f"{L} and {H} both {ls['trend']}"
        lch = _chochs(ls, ld)
        if hb and hb["kind"] == "CHoCH" and lch and lch[-1]["confirm_ts"] <= hb["confirm_ts"]:
            status, text = "reversal_propagated", (f"{L} CHoCH {ls['trend']} at {_px(lch[-1]['level'])} was "
                                                   f"followed by a {H} CHoCH at {_px(hb['level'])}: {H} reversal confirmed")
        elif lb and lb["kind"] == "CHoCH" and hb and lb["confirm_ts"] > hb["confirm_ts"]:
            status, text = "pullback_ended", (f"{L} broke back {ls['trend']} (CHoCH at {_px(lb['level'])}): the "
                                              f"counter-trend pullback against the {H} ended")
        return {**base, "status": status, "divergence": False, "text": text, "resolve": []}
    key = hsw["L" if hd > 0 else "H"]
    htf_level = key[-1]["price"] if key else None
    if lb and lb["kind"] == "CHoCH":
        return {**base, "status": "divergence", "divergence": True,
                "text": (f"{L} printed a {ls['trend']} CHoCH at {_px(lb['level'])} against the intact {H} "
                         f"{hs['trend']} structure: a pullback, or the start of a {H} reversal"),
                "resolve": [f"{H} close {'below' if hd > 0 else 'above'} {_px(htf_level)} (a {H} CHoCH) would "
                            f"confirm the reversal",
                            f"{L} close {'above' if hd > 0 else 'below'} its last swing "
                            f"{'high' if hd > 0 else 'low'} would end the pullback"]}
    return {**base, "status": "counter_trend", "divergence": False,
            "text": f"{L} trends {ls['trend']} (BOS) against the {H} {hs['trend']} structure",
            "resolve": [f"{H} close {'below' if hd > 0 else 'above'} {_px(htf_level)} would align the {H}"]}


def sync(tfs, reads):
    """Lowest TF from which every TF above has the same external direction (None if the top is flat)."""
    have = [tf for tf in sorted(tfs, key=tf_minutes, reverse=True) if tf in reads]
    if not have:
        return None, None
    d = DIR[reads[have[0]]["smc"]["trend"]]
    if not d:
        return None, None
    chain = [have[0]]
    for tf in have[1:]:
        if DIR[reads[tf]["smc"]["trend"]] != d:
            break
        chain.append(tf)
    s = chain[-1]
    below = [tf for tf in have if tf_minutes(tf) < tf_minutes(s)]
    return s, (below[0] if below else None)
