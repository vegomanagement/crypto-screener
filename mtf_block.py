"""MTF block: dominant TF, verdict, conflicts, triggers, level confluence, swing setups (pure).

Dominant TF = the highest TF (pane TF and above) with a clear read: a clear bias (|score| ≥ 3/5)
or a Wyckoff structure with confidence ≥ 0.45 (HTF priority). Per-TF lean = the bias sign when
clear, else the Wyckoff lean in phases C–E (accumulation +1 / distribution −1), else 0.
Verdict over the pane TF and the TFs above it:
  unclear              no TF has a clear read
  mixed_counter_trend  some context TF leans against the dominant TF's lean
  aligned_long/short   dominant lean ≠ 0 and the pane TF leans the same way
  range_bound          dominant TF is a range (lean 0), or the pane TF has no lean
Levels: every TF's levels, weight = WEIGHT[tf] (HTF higher), clustered when within
CLUSTER_ATR × the pane ATR of the cluster's running mean; rank = (number of TFs, Σ max weight
per TF). Swing setups = swing_model.scan on the live windows of each fractal pair in the ladder.
"""
from __future__ import annotations

import mtf_rules as R
from mtf import TF_LABEL, WEIGHT, tf_minutes

CLUSTER_ATR = 0.5
MAX_LEVELS = 12
WY_CLEAR = 0.45
PAIRS = (("240", "15"), ("60", "5"), ("D", "60"))
SETUP_STATS_NOTE = ("see docs/MTF_SYNC_RESULT.md and docs/SWING_HTF_RESULT.md — no proven out-of-sample edge "
                    "(0 of 962 variants incl. breaker / IDM / 1d→4h / 1w→1d pass Holm)")


def lean(a):
    b, w = a["bias"], a.get("wyckoff") or {}
    if b["clear"]:
        return R.DIR[b["bias"]]
    if w.get("status") == "structure" and w.get("phase") in ("C", "D", "E"):
        return R.wy_dir(w)
    return 0


def clear(a):
    w = a.get("wyckoff") or {}
    return a["bias"]["clear"] or (w.get("status") == "structure" and (w.get("confidence") or 0) >= WY_CLEAR)


def dominant(tfs, reads, interval):
    ctx = [tf for tf in tfs if tf in reads and tf_minutes(tf) >= tf_minutes(interval)]
    for tf in sorted(ctx, key=tf_minutes, reverse=True):
        if clear(reads[tf]):
            return tf
    return None


def verdict(tfs, reads, interval):
    dom = dominant(tfs, reads, interval)
    if dom is None:
        return "unclear", None
    d = lean(reads[dom])
    ctx = [tf for tf in tfs if tf in reads and tf_minutes(tf) >= tf_minutes(interval)]
    if d and any(lean(reads[tf]) == -d for tf in ctx):
        return "mixed_counter_trend", dom
    pane = lean(reads[interval]) if interval in reads else 0
    if d and pane == d:
        return ("aligned_long" if d > 0 else "aligned_short"), dom
    return "range_bound", dom


def merge_levels(reads, ref_atr, price):
    pts = sorted((lv for a in reads.values() for lv in a.get("levels") or []), key=lambda x: x["price"])
    tol = CLUSTER_ATR * (ref_atr or 0) or 1e-9
    clusters = []
    for lv in pts:
        c = clusters[-1] if clusters else None
        if c and abs(lv["price"] - c["sum"] / c["w"]) <= tol:
            c["items"].append(lv)
            c["sum"] += lv["price"] * WEIGHT.get(lv["tf"], 1)
            c["w"] += WEIGHT.get(lv["tf"], 1)
        else:
            clusters.append({"items": [lv], "sum": lv["price"] * WEIGHT.get(lv["tf"], 1),
                             "w": WEIGHT.get(lv["tf"], 1)})
    out = []
    for c in clusters:
        tfs = sorted({x["tf"] for x in c["items"]}, key=tf_minutes, reverse=True)
        px = c["sum"] / c["w"]
        out.append({"price": float(f"{px:.10g}"), "low": min(x["price"] for x in c["items"]),
                    "high": max(x["price"] for x in c["items"]),
                    "tfs": [TF_LABEL.get(t, t) for t in tfs], "n_tfs": len(tfs),
                    "score": round(sum(WEIGHT.get(t, 1) for t in tfs), 2),
                    "kinds": [f"{TF_LABEL.get(x['tf'], x['tf'])} {x['kind']}" for x in
                              sorted(c["items"], key=lambda x: -tf_minutes(x["tf"]))][:6],
                    "side": "above" if px > price else "below",
                    "distance_atr": None if not ref_atr else round((px - price) / ref_atr, 2)})
    out.sort(key=lambda c: (-c["n_tfs"], -c["score"], abs(c["price"] - price)))
    return out[:MAX_LEVELS]


def setups(reads):
    import swing_model as sm
    out = []
    for htf, ltf in PAIRS:
        if htf not in reads or ltf not in reads:
            continue
        trades, st = sm.scan(reads[ltf]["_candles"], reads[htf]["_candles"])
        mode = st["mode"]
        t = st.get("t")
        state = {"idle": "waiting_for_poi", "armed": "at_poi_waiting_ltf_choch",
                 "pending": "triggered_waiting_retest", "trade": "triggered_filled"}[mode]
        out.append({"pair": f"{TF_LABEL[htf]}→{TF_LABEL[ltf]}", "htf": htf, "ltf": ltf, "state": state,
                    "htf_trend": reads[htf]["smc"]["trend"], "poi": st.get("poi") or (t or {}).get("poi"),
                    "entry": None if not t else round(t["entry"], 10), "stop": None if not t else round(t["stop"], 10),
                    "target": None if not t else round(t["target"], 10),
                    "partial_2r": None if not t else round(t["entry"] + t["d"] * 2 * t["risk"], 10),
                    "direction": None if not t else ("long" if t["d"] > 0 else "short"),
                    "note": st.get("last"), "edge": SETUP_STATS_NOTE})
    return out


def build(tfs, reads, missing, interval):
    """The full MTF block (dashboard); ``compact`` derives the snapshot block from it."""
    ladder = sorted([tf for tf in tfs if tf in reads], key=tf_minutes, reverse=True)
    v, dom = verdict(tfs, reads, interval)
    sync_tf, entry_tf = R.sync(tfs, reads)
    conflicts, aligned, pairs = [], [], []
    for lo, hi in zip(ladder[1:], ladder):
        pairs.append(R.smc_pair(lo, reads[lo], hi, reads[hi]))
        for h in R.wyckoff_rules(lo, reads[lo], hi, reads[hi]):
            (conflicts if h["kind"] == "conflict" else aligned).append(h)
    if dom:
        for lo in ladder:
            if tf_minutes(lo) < tf_minutes(dom) and lo not in (ladder[ladder.index(dom) + 1:ladder.index(dom) + 2]):
                for h in R.wyckoff_rules(lo, reads[lo], dom, reads[dom]):
                    if h["kind"] == "conflict":
                        conflicts.append(h)
    for p in pairs:
        if p["status"] in ("divergence", "counter_trend"):
            conflicts.append({"id": "S1" if p["divergence"] else "S4", "kind": "conflict",
                              "tfs": [p["ltf"], p["htf"]], "text": p["text"], "resolve": p["resolve"]})
    pane = reads.get(interval)
    price = pane["price"] if pane else (reads[ladder[-1]]["price"] if ladder else None)
    out = {"source": "mtf.py + mtf_rules.py + mtf_block.py over each TF's closed candles (300 per TF)",
           "ts": max((a["ts"] for a in reads.values()), default=None), "interval": interval,
           "ladder": ladder, "verdict": v, "dominant_tf": dom, "sync_tf": sync_tf, "entry_tf": entry_tf,
           "missing": missing, "pairs": pairs, "conflicts": conflicts, "aligned": aligned,
           "resolve": [r for c in conflicts for r in c["resolve"]][:8],
           "levels": merge_levels(reads, pane["atr14"] if pane else None, price) if price else [],
           "tfs": {tf: {k: v2 for k, v2 in reads[tf].items() if not k.startswith("_")} for tf in ladder},
           "rules": "HTF priority; rule ids W1-W5, B1, S1-S4 documented in mtf_rules.py",
           "edge": "descriptive context: no proven out-of-sample edge (docs/MTF_SYNC_RESULT.md)"}
    out["position_vs_htf"] = {}
    for lo, hi in zip(ladder[1:], ladder):
        lt = (reads[lo]["wyckoff"] or {}).get("tr") or {}
        ht = (reads[hi]["wyckoff"] or {}).get("tr") or {}
        out["position_vs_htf"][lo] = R.nest(lt, ht, reads[hi]["atr14"]) if lt and ht else None
    try:
        out["setups"] = setups(reads)
    except Exception as exc:  # noqa: BLE001 — recorded, never invented
        out["setups"], out["missing"]["setups"] = [], f"{type(exc).__name__}: {exc}"[:160]
    return out


def _brk(b):
    return None if not b else {"kind": b["kind"], "dir": b["dir"], "level": b["level"], "ts": b["ts"]}


def compact(block, max_conflicts=6):
    """Bounded snapshot version (cite as mtf.*); ≈ 4-6 kB for a 5-TF ladder."""
    tfs = {}
    for tf, a in block["tfs"].items():
        w, s, p = a["wyckoff"] or {}, a["smc"], a["profile"] or {}
        ob = (s.get("order_blocks") or [None])[0]
        brk, idm = (s.get("breakers") or [None])[0], s.get("inducement")
        tfs[TF_LABEL.get(tf, tf)] = {
            "bias": a["bias"]["bias"], "bias_score": a["bias"]["score"], "smc_trend": s["trend"],
            "internal_trend": s["internal_trend"], "last_break": _brk(s["last_break"]),
            "zone": (s.get("dealing_range") or {}).get("zone"), "eq": (s.get("dealing_range") or {}).get("eq"),
            "wyckoff": None if w.get("status") != "structure" else {
                "schematic": w["schematic"], "phase": w["phase"], "support": (w.get("tr") or {}).get("support"),
                "resistance": (w.get("tr") or {}).get("resistance"),
                "last_event": (w.get("last_event") or {}).get("event")},
            "poc": p.get("poc"), "value_position": p.get("position"), "vwap_position": p.get("vwap_position"),
            "nearest_ob": None if not ob else {k: ob[k] for k in ("dir", "top", "bottom", "state")},
            "nearest_breaker": None if not brk else {k: brk[k] for k in ("dir", "top", "bottom", "state")},
            "inducement": None if not idm else {k: idm[k] for k in ("side", "price", "state")},
            "equal_levels": [{k: q[k] for k in ("kind", "price")} for q in (s.get("equal_levels") or [])[-2:]],
            "position_vs_htf": block["position_vs_htf"].get(tf), "atr14": a["atr14"]}
    lab = lambda t: None if t is None else TF_LABEL.get(t, t)  # noqa: E731
    return {"source": block["source"], "ts": block["ts"], "verdict": block["verdict"],
            "dominant_tf": lab(block["dominant_tf"]), "sync_tf": lab(block["sync_tf"]),
            "entry_tf": lab(block["entry_tf"]), "ladder": [lab(t) for t in block["ladder"]], "tfs": tfs,
            "pairs": [{"pair": f"{lab(p['htf'])}>{lab(p['ltf'])}", "status": p["status"]} for p in block["pairs"]],
            "conflicts": [{"id": c["id"], "tfs": [lab(t) for t in c["tfs"]], "text": c["text"][:220]}
                          for c in block["conflicts"][:max_conflicts]],
            "resolve": [r[:160] for r in block["resolve"][:5]],
            "levels": [{"price": lv["price"], "tfs": lv["tfs"], "kinds": lv["kinds"][:3], "score": lv["score"]}
                       for lv in block["levels"][:8]],
            "setups": [{k: s[k] for k in ("pair", "state", "direction", "entry", "stop", "target")}
                       for s in block.get("setups") or []],
            "edge": block["edge"], "missing": block["missing"]}


def htf_direction(compact_block):
    """(+1/−1/0, tf label) of the dominant TF in a compact snapshot block."""
    if not compact_block or not compact_block.get("dominant_tf"):
        return 0, None
    tf = compact_block["dominant_tf"]
    a = (compact_block.get("tfs") or {}).get(tf) or {}
    if a.get("bias") in ("bull", "bear"):
        return (1 if a["bias"] == "bull" else -1), tf
    w = a.get("wyckoff") or {}
    if w.get("phase") in ("C", "D", "E"):
        return (1 if w["schematic"] == "accumulation" else -1), tf
    return 0, tf


def counter_trend_warning(compact_block, split_bias):
    """Text when the computed split leans against the dominant HTF, else None (no weight change:
    the backtest found no edge in HTF alignment, see docs/MTF_SYNC_RESULT.md)."""
    d, tf = htf_direction(compact_block)
    s = {"long": 1, "short": -1}.get(split_bias, 0)
    if not d or not s or d == s:
        return None
    return (f"counter-trend vs HTF: the split leans {split_bias} while the dominant {tf} reads "
            f"{'bullish' if d > 0 else 'bearish'} (MTF verdict {compact_block.get('verdict')})")
