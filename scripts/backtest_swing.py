#!/usr/bin/env python3
"""Backtest of the SMC swing entry model (swing_model.py) per fractal pair (stdlib only).

    python scripts/backtest_swing.py [--end 2026-10-18] [--symbols ...] [--seeds 50]

Pairs: 4h→15m, 1h→5m, 1d→1h on Binance USDT-M BTC/ETH/SOL (data.binance.vision, cached in
scratch). 4h/1d bars are aggregated from complete 1h bars; an LTF bar sees only HTF bars closed
by its own close. Split per series: the first 70 % of LTF bars (by time) are in-sample; a trade
belongs to the segment of its signal bar. Grid per pair: poi ∈ {any, ob, fvg} × sweep ∈ {no, yes}
× ltf_len ∈ {2, 3} (12 filtered variants) plus the unfiltered CHoCH→retest model with ltf_len ∈
{2, 3}. The variant with the best IS net (taker) expectancy (IS n ≥ 30) is scored out of sample;
Bonferroni over the OOS hypotheses. Controls: the same entries without the HTF filter, and random
entries (same count per symbol in the OOS segment, random side, same risk in ATR and same R
targets, same management), ``--seeds`` repeats.
Output: docs/evidence/mtf-sync/swing.json and swing.md.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import mtf_data as md  # noqa: E402
import signal_eval as se  # noqa: E402
import signal_features as sf  # noqa: E402
import signals_data as data  # noqa: E402
import swing_model as sm  # noqa: E402

OUT = ROOT / "docs" / "evidence" / "mtf-sync"
PAIRS = {"4h>15m": ("240", "15m"), "1h>5m": ("60", "5m"), "1d>1h": ("D", "1h")}
TAKER, MAKER = 0.0005, 0.0002
IS_FRACTION, MIN_IS, MIN_OOS = 0.7, 30, 30
GRID = [{"poi": poi, "sweep": sw, "ltf_len": n} for poi in ("any", "ob", "fvg") for sw in (False, True)
        for n in (2, 3)]
UNFILTERED = [{"ltf_len": n} for n in (2, 3)]


def load(sym, pair, end_ms):
    htf_tf, ltf_iv = PAIRS[pair]
    h1 = data.klines(sym, "1h", end_ms)
    htf = h1 if htf_tf == "60" else md.aggregate(h1, htf_tf, 3_600_000)
    ltf = data.klines(sym, ltf_iv, end_ms)
    return ltf, htf


def run_job(args):
    sym, pair, end_ms, seeds = args
    t0 = time.time()
    ltf, htf = load(sym, pair, end_ms)
    atr = sf.atr([c["h"] for c in ltf], [c["l"] for c in ltf], [c["c"] for c in ltf])
    cut = int(len(ltf) * IS_FRACTION)
    rows = {}
    for filtered, grid in ((True, GRID), (False, UNFILTERED)):
        for p in grid:
            trades, _ = sm.scan(ltf, htf, p, filtered=filtered, atr=atr)
            rows[json.dumps([filtered, p], sort_keys=True)] = [
                (t["r"], sm.fee_r(t, TAKER), sm.fee_r(t, MAKER), t["i_signal"] >= cut, ltf[t["i_exit"]]["close_ts"],
                 t["risk_atr"], t["rt"], t["d"]) for t in trades]
    meta = {"symbol": sym, "pair": pair, "ltf_bars": len(ltf), "htf_bars": len(htf), "first_ts": ltf[0]["ts"],
            "last_close_ts": ltf[-1]["close_ts"], "oos_from_ts": ltf[cut]["ts"],
            "oos_months": (ltf[-1]["close_ts"] - ltf[cut]["ts"]) / (30.44 * 86_400_000),
            "is_months": (ltf[cut]["ts"] - ltf[0]["ts"]) / (30.44 * 86_400_000)}
    # random control per chosen-later variant: precompute for every filtered variant's OOS trades
    rnd_rows = {}
    for key, outs in rows.items():
        oos = [o for o in outs if o[3]]
        if not oos or not json.loads(key)[0]:
            continue
        res = []
        for seed in range(seeds):
            rng = random.Random(f"{sym}{pair}{key}{seed}")
            for o in oos:
                for _ in range(20):
                    j = rng.randrange(cut, len(ltf) - 2)
                    if atr[j]:
                        break
                d = rng.choice((1, -1))
                entry = ltf[j]["c"]
                risk = o[5] * atr[j]
                r, _ei, why, _p = sm.manage(ltf, j, d, entry, entry - d * risk, entry + d * o[6] * risk, at_close=True)
                if why != "open":
                    res.append((seed, r, 2 * TAKER * entry / risk, 2 * MAKER * entry / risk))
        rnd_rows[key] = res
    print(f"{sym} {pair}: {len(ltf)} bars, {time.time() - t0:.0f}s", flush=True)
    return meta, rows, rnd_rows


def stats(outs, fee_col=1, z=1.96):
    n = len(outs)
    if not n:
        return {"n": 0}
    rs = [o[0] - (o[fee_col] if fee_col is not None else 0.0) for o in outs]
    mean = sum(rs) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1)) if n > 1 else 0.0
    wins = sum(o[0] - (o[fee_col] if fee_col else 0) > 0 for o in outs)
    eq, peak, dd = 0.0, 0.0, 0.0
    for r in [r for _t, r in sorted(zip([o[4] for o in outs], rs))]:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"n": n, "win_rate": round(wins / n, 4), "win_ci95": se.wilson(wins, n, z),
            "expectancy_r": round(mean, 4),
            "expectancy_ci": None if n < 2 else [round(mean - z * sd / math.sqrt(n), 4), round(mean + z * sd / math.sqrt(n), 4)],
            "max_dd_r": round(dd, 2), "total_r": round(sum(rs), 2),
            "mean_fee_r": None if fee_col is None else round(sum(o[fee_col] for o in outs) / n, 4)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--end", default="2026-10-18")
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    ap.add_argument("--pairs", nargs="+", default=list(PAIRS))
    ap.add_argument("--seeds", type=int, default=50)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    end_ms = int(datetime.fromisoformat(a.end).replace(tzinfo=UTC).timestamp() * 1000)
    jobs = [(s, p, end_ms, a.seeds) for p in a.pairs for s in a.symbols]
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        results = list(pool.map(run_job, jobs))
    report = {"generated": datetime.now(UTC).isoformat(timespec="seconds"), "end": a.end,
              "rules": sm.__doc__.strip(), "fees": {"taker": TAKER, "maker": MAKER}, "series": [], "pairs": {}}
    for meta, _r, _x in results:
        report["series"].append(meta)
    tested = 0
    chosen = []
    for pair in a.pairs:
        res = [r for r in results if r[0]["pair"] == pair]
        months = sum(m["oos_months"] for m, _r, _x in res)
        block = {"variants_is": {}, "oos_months_total": round(months, 1)}
        for filtered in (True, False):
            best = None
            for key in res[0][1]:
                if json.loads(key)[0] != filtered:
                    continue
                tested += 1
                outs = [(sym_meta["symbol"], o) for sym_meta, rows, _x in res for o in rows[key]]
                st = stats([o for _s, o in outs if not o[3]])
                block["variants_is"][key] = {k: st.get(k) for k in ("n", "expectancy_r", "win_rate")}
                score = st.get("expectancy_r", -9) if st["n"] >= MIN_IS else -9
                if best is None or score > best[0]:
                    best = (score, key, outs)
            chosen.append((pair, filtered, best))
        report["pairs"][pair] = block
    m_oos = sum(1 for _p, _f, b in chosen if b and b[0] > -9)
    z_b = se.z_for(0.05 / max(1, m_oos))
    report.update(variants_tested=tested, oos_hypotheses=m_oos, z_bonferroni=round(z_b, 3))
    for pair, filtered, (score, key, outs) in chosen:
        oos = [o for _s, o in outs if o[3]]
        months = report["pairs"][pair]["oos_months_total"]
        b = {"variant": json.loads(key), "is": stats([o for _s, o in outs if not o[3]]),
             "oos_taker": stats(oos), "oos_taker_bonf": stats(oos, z=z_b), "oos_maker": stats(oos, fee_col=2),
             "oos_gross": stats(oos, fee_col=None),
             "oos_trades_per_month_per_symbol": round(len(oos) / months, 2) if months else None,
             "oos_by_symbol": {s: stats([o for s2, o in outs if o[3] and s2 == s]) for s in a.symbols}}
        if filtered:
            rnd = [x for _m, _r, rr in results if _m["pair"] == pair for x in rr.get(key, [])]
            per_seed = {}
            for seed, r, ft, fm in rnd:
                per_seed.setdefault(seed, []).append(r - ft)
            means = sorted(sum(v) / len(v) for v in per_seed.values() if v)
            b["random_control"] = {"seeds": len(means), "n_per_seed": len(rnd) // max(1, len(means)),
                                   "mean_expectancy_taker": round(sum(means) / len(means), 4) if means else None,
                                   "p05": round(means[int(0.05 * len(means))], 4) if means else None,
                                   "p95": round(means[int(0.95 * len(means)) - 1], 4) if means else None,
                                   "model_beats_share": round(sum(m < b["oos_taker"].get("expectancy_r", -9) for m in means) / len(means), 3) if means else None}
        st = b["oos_taker_bonf"]
        pos = sum(1 for v in b["oos_by_symbol"].values() if v.get("n", 0) >= 10 and v["expectancy_r"] > 0)
        b["passes"] = bool(filtered and st.get("n", 0) >= MIN_OOS and st.get("expectancy_ci")
                           and st["expectancy_ci"][0] > 0 and pos >= 2)
        report["pairs"][pair]["filtered" if filtered else "unfiltered"] = b
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "swing.json").write_text(json.dumps(report, indent=1))
    from backtest_mtf_report import swing_tables
    (OUT / "swing.md").write_text(swing_tables(report))
    print(f"variants {tested}, OOS hypotheses {m_oos}, z_bonf {z_b:.2f}")
    for pair, blk in report["pairs"].items():
        for k in ("filtered", "unfiltered"):
            o = blk[k]["oos_taker"]
            print(pair, k, blk[k]["variant"][1], "IS", blk[k]["is"].get("n"), blk[k]["is"].get("expectancy_r"),
                  "| OOS n", o.get("n"), "E", o.get("expectancy_r"), o.get("expectancy_ci"), "pass", blk[k]["passes"])


if __name__ == "__main__":
    main()
