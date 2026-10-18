"""Report for scripts/backtest_swing_universe.py: selection, corrections, controls, walk-forward.

Protocol (fixed before looking at OOS):
  family      every variant tried = the grid on 1d→4h and 1w→1d + the original model on the two
              reference pairs; Holm over all of them (one-sided OOS p, month-clustered z);
  pre-reg     per pair the 5 best variants by IN-SAMPLE net mean (IS n ≥ 30) are the candidates;
  post-hoc    the 5 best by OOS z are reported as 'closest to passing' (selection on OOS, so they
              only count after Holm over the whole family);
  controls    for every candidate: month-block bootstrap CI, per-coin table, random entry with
              the same coin / side / count / stop-in-ATR / R:R / exits (20 seeds), the same rules
              without the HTF filter, the universe chosen as of OOS_START, yearly folds.
"""
from __future__ import annotations

import json
import math
import random
import statistics as st
import time
import zlib
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

import swing_universe_stats as S

BAR_MS = {"240": 14_400_000, "D": 86_400_000, "60": 3_600_000, "15m": 900_000}
TOP = 5
MIN_IS = 30
MIN_OOS = 10
SEEDS = 20


def random_job(args):
    """Gross R of random entries matched to a variant's OOS trades on one coin (per seed)."""
    import backtest_swing_universe as B
    import signal_features as sf
    import swing_model as sm
    sym, onboard, pair, params, specs, seeds = args
    ltf, _ = B.load(sym, pair, onboard)
    atr = sf.atr([c["h"] for c in ltf], [c["l"] for c in ltf], [c["c"] for c in ltf])
    p = {**sm.DEFAULTS, **params}
    pool = [j for j, c in enumerate(ltf) if c["ts"] >= B.OOS_START and atr[j] and j + 1 < len(ltf)]
    out = []
    for seed in range(seeds):
        rnd, rs = random.Random(zlib.crc32(f"{sym}|{pair}|{seed}".encode())), []
        for d, risk_atr, rt in specs:
            if not pool or not risk_atr:
                continue
            j = rnd.choice(pool)
            entry = ltf[j]["c"]
            risk = risk_atr * atr[j]
            r, _, why, _ = sm.manage(ltf, j, d, entry, entry - d * risk, entry + d * rt * risk, p, at_close=True)
            if r is not None:
                rs.append(r)
        out.append(rs)
    return sym, out


def _pool(fn, jobs, workers=6):
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, jobs))


def main(B):
    t0 = time.time()
    uni = B.load_universe()
    coins = uni["main"]["coins"]
    onboard = {c["symbol"]: c["onboard"] for c in coins + uni["asof_oos"]["coins"]}
    is_y, oos_y = S.years(coins, B.OOS_START, B.END, B.START)
    params = {B.vid(p): p for p in B.GRID}
    res = {"universe": uni, "oos_start": B.OOS_START, "end": B.END, "grid_size": len(B.GRID),
           "costs": {"taker": B.TAKER, "maker": B.MAKER, "slippage": B.SLIP, "funding_fallback_8h": B.FUNDING_FALLBACK},
           "pairs": {}}
    trades, pvals = {}, {}
    for pair in B.GRID_PAIRS:
        trades[pair] = B.read(f"grid-{pair}")
    for pair in ("1d>1h", "4h>15m"):
        trades[pair] = B.read(f"ref-{pair}")
    # ---- per-variant IS / OOS summaries and the Holm family
    table = {}
    for pair, rows in trades.items():
        by_v = defaultdict(list)
        for t in rows:
            by_v[t["v"]].append(t)
        names = [B.vid(p) for p in B.GRID] if pair in B.GRID_PAIRS else [B.vid(B.BASE)]
        for v in names:
            a, b = S.split(by_v.get(v, []), B.OOS_START)
            si = S.summary(a, is_y, BAR_MS[B.PAIRS[pair][1]])
            so = S.summary(b, oos_y, BAR_MS[B.PAIRS[pair][1]])
            table[(pair, v)] = {"is": si, "oos": so}
            pvals[(pair, v)] = so["p"] if so["n"] >= MIN_OOS else 1.0
        if pair not in B.GRID_PAIRS:
            ctl = by_v.get("NOHTF:" + B.vid(B.BASE), [])
            table[(pair, "NOHTF")] = {"is": S.summary(S.split(ctl, B.OOS_START)[0], is_y, BAR_MS[B.PAIRS[pair][1]]),
                                      "oos": S.summary(S.split(ctl, B.OOS_START)[1], oos_y, BAR_MS[B.PAIRS[pair][1]])}
    adj = S.holm(pvals)
    res["family_size"] = len(pvals)
    res["bonferroni_z_one_sided"] = round(_z_of(0.05 / len(pvals)), 2)
    tested = sum(1 for k in pvals if table[k]["oos"]["n"] >= MIN_OOS)
    res["variants_with_oos_n_ge_10"] = tested
    for k, v in table.items():
        if k in adj:
            v["oos"]["p_holm"] = adj[k]
    # ---- selection
    cands = {}
    for pair in B.GRID_PAIRS:
        names = [B.vid(p) for p in B.GRID]
        ok = [v for v in names if table[(pair, v)]["is"].get("n", 0) >= MIN_IS]
        pre = sorted(ok, key=lambda v: -table[(pair, v)]["is"]["net"])[:TOP]
        post = sorted([v for v in names if table[(pair, v)]["oos"]["n"] >= MIN_OOS],
                      key=lambda v: -(table[(pair, v)]["oos"]["z"] or -99))[:TOP]
        cands[pair] = {"pre": pre, "post": post}
        res["pairs"][pair] = {"n_variants": len(names), "n_is_ge_30": len(ok),
                              "n_oos_ge_10": sum(table[(pair, v)]["oos"]["n"] >= MIN_OOS for v in names),
                              "pre_registered": pre, "closest_to_passing": post,
                              "factor_means": factor_means(table, pair, names, params)}
    # ---- controls for every candidate
    by_pv = defaultdict(list)
    for pair in B.GRID_PAIRS:
        for t in trades[pair]:
            by_pv[(pair, t["v"])].append(t)
    folds = yearly_folds(B)
    jobs_nohtf, jobs_asof, jobs_rand = [], [], []
    for pair in B.GRID_PAIRS:
        vs = list(dict.fromkeys(cands[pair]["pre"] + cands[pair]["post"]))
        cands[pair]["all"] = vs
        for c in coins:
            jobs_nohtf.append((c["symbol"], c["onboard"], pair, [(v, json.dumps(params[v])) for v in vs], (False,)))
        for c in uni["asof_oos"]["coins"]:
            jobs_asof.append((c["symbol"], c["onboard"], pair, [(v, json.dumps(params[v])) for v in vs], (True,)))
        for v in vs:
            oos = S.split(by_pv[(pair, v)], B.OOS_START)[1]
            per = defaultdict(list)
            for t in oos:
                per[t["sym"]].append((t["d"], t["risk_atr"], t["rt"]))
            for sym, specs in per.items():
                jobs_rand.append((sym, onboard[sym], pair, params[v], specs, SEEDS, v))
    print(f"controls: {len(jobs_nohtf)} no-HTF, {len(jobs_asof)} as-of, {len(jobs_rand)} random jobs", flush=True)
    nohtf = [dict(t, pair=pr) for _, pr, out, _ in _pool(B.run_job, jobs_nohtf) for t in out]
    asof_rows = [dict(t, pair=pr) for _, pr, out, _ in _pool(B.run_job, jobs_asof) for t in out]
    rand = _pool(random_job, [j[:6] for j in jobs_rand])
    nohtf_by, asof_by = defaultdict(list), defaultdict(list)
    for t in nohtf:
        nohtf_by[(t["pair"], t["v"][6:])].append(t)
    for t in asof_rows:
        asof_by[(t["pair"], t["v"])].append(t)
    rand_by = defaultdict(lambda: [[] for _ in range(SEEDS)])
    for job, (sym, seeds) in zip(jobs_rand, rand):
        for s, rs in enumerate(seeds):
            rand_by[(job[2], job[6])][s] += rs
    ay_is, ay_oos = S.years(uni["asof_oos"]["coins"], B.OOS_START, B.END, B.START)
    detail = {}
    for pair in B.GRID_PAIRS:
        bm = BAR_MS[B.PAIRS[pair][1]]
        for v in cands[pair]["all"]:
            a, b = S.split(by_pv[(pair, v)], B.OOS_START)
            d = {"is": S.summary(a, is_y, bm, ci=True), "oos": S.summary(b, oos_y, bm, ci=True)}
            d["oos"]["p_holm"] = adj[(pair, v)]
            ctl = S.split(nohtf_by[(pair, v)], B.OOS_START)
            d["no_htf_oos"] = S.summary(ctl[1], oos_y, bm)
            d["asof_universe_oos"] = S.summary(S.split(asof_by[(pair, v)], B.OOS_START)[1], ay_oos, bm, ci=True)
            seeds = [st.mean(x) for x in rand_by[(pair, v)] if x]
            d["random_oos_gross"] = None if not seeds else {
                "mean": round(st.mean(seeds), 3), "sd_across_seeds": round(st.pstdev(seeds), 3),
                "share_seeds_ge_variant": round(sum(m >= d["oos"].get("gross", 0) for m in seeds) / len(seeds), 2)}
            d["folds"] = S.fixed_walk_forward(by_pv[(pair, v)], folds)
            d["passes"] = passes(d)
            detail[f"{pair} {v}"] = d
        res["pairs"][pair]["walk_forward_selection"] = S.walk_forward(
            trades[pair], [B.vid(p) for p in B.GRID], folds[1:])
    res["candidates"] = detail
    res["references"] = {f"{pair} {k[1]}": table[k] for k in table for pair in ("1d>1h", "4h>15m") if k[0] == pair}
    res["closest"] = {f"{k[0]} {k[1]}": dict(table[k]["oos"], is_net=table[k]["is"].get("net"), is_n=table[k]["is"]["n"])
                      for k in sorted((k for k in table if k in adj and table[k]["oos"]["n"] >= MIN_OOS),
                                      key=lambda k: (adj[k], pvals[k]))[:15]}
    res["any_pass"] = any(d["passes"]["all"] for d in detail.values())
    res["elapsed_s"] = round(time.time() - t0)
    B.OUT.mkdir(parents=True, exist_ok=True)
    (B.OUT / "results.json").write_text(json.dumps(res, indent=1, default=str))
    full = {f"{k[0]} {k[1]}": v for k, v in table.items()}
    with open(B.OUT_RAW / "all_variants.json", "w") as f:
        json.dump(full, f)
    import swing_universe_md as md
    (B.OUT / "results.md").write_text(md.render(res, full))
    print(f"report: any_pass={res['any_pass']} family={res['family_size']} ({res['elapsed_s']}s)")


def _z_of(p):
    lo, hi = 0.0, 10.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if 0.5 * math.erfc(mid / math.sqrt(2)) > p else (lo, mid)
    return lo


def passes(d):
    o = d["oos"]
    r = d["random_oos_gross"] or {}
    folds = [f for f in d["folds"] if f["n"] >= 5]
    chk = {"net_gt_0": (o.get("net") or -1) > 0,
           "holm_p_lt_0.05": o.get("p_holm", 1) < 0.05,
           "ci_above_0": bool(o.get("ci95")) and o["ci95"][0] > 0,
           "not_one_coin": (o.get("without_best_coin", {}).get("net") or -1) > 0,
           "coins_majority_positive": _ratio(o.get("coins_positive")) >= 0.6,
           "beats_random": r.get("share_seeds_ge_variant", 1) <= 0.05,
           "beats_no_htf": (o.get("net") or -1) > (d["no_htf_oos"].get("net") or -9),
           "asof_universe_positive": (d["asof_universe_oos"].get("net") or -1) > 0,
           "folds_positive": bool(folds) and sum((f["net"] or 0) > 0 for f in folds) >= 0.75 * len(folds)}
    chk["all"] = all(chk.values())
    return chk


def _ratio(s):
    if not s:
        return 0.0
    a, b = s.split("/")
    return int(a) / int(b) if int(b) else 0.0


def yearly_folds(B):
    from datetime import UTC, datetime
    ys = [int(datetime(y, 1, 1, tzinfo=UTC).timestamp() * 1000) for y in range(2021, 2027)]
    return list(zip(ys, ys[1:] + [B.END]))


def factor_means(table, pair, names, params):
    """OOS net mean pooled over every variant sharing a factor level (trade-weighted)."""
    out = {}
    for f in ("poi", "idm", "sweep", "entry", "stop", "target", "be"):
        lv = defaultdict(lambda: [0.0, 0])
        for v in names:
            o = table[(pair, v)]["oos"]
            if o["n"]:
                key = str(params[v].get(f))
                lv[key][0] += o["net"] * o["n"]
                lv[key][1] += o["n"]
        out[f] = {k: {"net": round(s / n, 3) if n else None, "trades": n} for k, (s, n) in lv.items()}
    return out
