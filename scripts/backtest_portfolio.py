#!/usr/bin/env python3
"""Portfolio strategy research (docs/STRATEGY_PREREG.md) — re-runnable subcommands.

    python scripts/backtest_portfolio.py data      # 1h cache → 1d / 4h bars + funding (scratch)
    python scripts/backtest_portfolio.py dev       # all 97 prereg variants + benchmarks, DEVELOPMENT only
    python scripts/backtest_portfolio.py select    # stats, DSR, Holm, pass criteria 1–6 → finalists (frozen)
    python scripts/backtest_portfolio.py holdout   # opens the sealed 2026 holdout ONCE for the finalists
    python scripts/backtest_portfolio.py report    # SVG equity curves + summary JSON for the result doc

Development = 2020-01-01 … 2025-12-31 (the Market is built with bars and funding truncated at
DEV_END, so no dev run can see 2026). Holdout = 2026-01-01 … the last closed day. Raw series go
to scratch (RAW); compact evidence to docs/evidence/strategy-research.
"""
from __future__ import annotations

import gzip
import itertools
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import mtf_data as md  # noqa: E402
import portfolio_engine as pe  # noqa: E402
import portfolio_stats as pst  # noqa: E402
import portfolio_strategies as ps  # noqa: E402

DAY = 86_400_000
H1 = 3_600_000
END = int(datetime(2026, 10, 18, tzinfo=UTC).timestamp() * 1000)       # cache end (= last closed day + 1)
START = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)
DEV_END = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
SWING = Path(os.environ.get("SWING_HTF_RAW", Path.home() / ".hermes/cache/scratch/swing-htf"))
RAW = Path(os.environ.get("PORTFOLIO_RAW", Path.home() / ".hermes/cache/scratch/strategy-research"))
OUT = ROOT / "docs" / "evidence" / "strategy-research"
SENTINEL = RAW / "HOLDOUT_OPENED"
N_TRIALS = 97
SMC_BASE = "any|idm=none|sweep=0|limit|stop=poi|r2+be"


def universe():
    u = json.loads((SWING / "universe.json").read_text())
    return ({c["symbol"]: c["onboard"] for c in u["main"]["coins"]},
            {c["symbol"]: c["onboard"] for c in u["asof_oos"]["coins"]})


def cmd_data():
    import swing_universe_data as U
    main, asof = universe()
    RAW.mkdir(parents=True, exist_ok=True)
    blob = {"daily": {}, "h4": {}, "funding": {}}
    for sym, ob in {**main, **asof}.items():
        h1 = U.klines(sym, "1h", END, ob)
        h1 = [b for b in h1 if b["ts"] >= START]
        live = max((i for i, b in enumerate(h1) if b["v"] > 0), default=-1)
        h1 = h1[:live + 1]                   # settled contracts: drop the flat zero-volume tail
        d = md.aggregate(h1, "D", H1)
        blob["daily"][sym] = [{k: b[k] for k in ("ts", "o", "h", "l", "c", "v")} for b in d]
        blob["h4"][sym] = [{k: b[k] for k in ("ts", "close_ts", "o", "h", "l", "c", "v")}
                           for b in md.aggregate(h1, "240", H1)]
        blob["funding"][sym] = U.funding(sym, END)
        f = blob["funding"][sym]
        print(f"{sym}: {len(d)} days {fmt(d[0]['ts'])}…{fmt(d[-1]['ts'])}, "
              f"{len(blob['h4'][sym])} 4h, {len(f) if f else 'no'} funding", flush=True)
    with gzip.open(RAW / "bars.json.gz", "wt") as fh:
        json.dump(blob, fh)


def fmt(ts):
    return datetime.fromtimestamp(ts / 1000, tz=UTC).strftime("%Y-%m-%d")


_BLOB = None


def bars():
    global _BLOB
    if _BLOB is None:
        with gzip.open(RAW / "bars.json.gz", "rt") as fh:
            _BLOB = json.load(fh)
    return _BLOB


def market(syms, end_ms):
    b = bars()
    return pe.Market({s: b["daily"][s] for s in syms}, {s: b["funding"][s] for s in syms}, end_ms=end_ms)


# ── variant grid (prereg §3) ────────────────────────────────────────────────────────────────

def grid():
    out = []
    for entry, n in (("ema", 50), ("ema", 100), ("dc", 20), ("dc", 55)):
        for ls in (False, True):
            for k in (3.0, 5.0):
                base = f"{entry}{n}|{'ls' if ls else 'long'}|k{int(k)}"
                out.append({"id": f"A:{base}", "idea": "A", "kind": "trend", "entry": entry, "n": n, "ls": ls, "k": k})
                for fb in ("market", "skip"):
                    out.append({"id": f"ASMC:{base}|{fb}", "idea": "ASMC", "kind": "trend", "entry": entry, "n": n,
                                "ls": ls, "k": k, "smc": fb, "pair": f"A:{base}"})
    for L in (2, 4, 8):
        for K in (3, 5):
            for mode in ("long", "abs", "ls"):
                out.append({"id": f"B:L{L}|K{K}|{mode}", "idea": "B", "kind": "mom", "L": L, "K": K, "mode": mode})
    for W in (30, 90):
        for H in (1, 3, 7):
            for tf in (False, True):
                out.append({"id": f"D:ts|W{W}|H{H}|{'trend' if tf else 'raw'}", "idea": "D", "kind": "fts",
                            "W": W, "H": H, "trend": tf})
    for W in (30, 90):
        for H in (1, 3, 7):
            out.append({"id": f"D:xs|W{W}|H{H}", "idea": "D", "kind": "fxs", "W": W, "H": H})
    return out


def c_grid(best_a, best_b):
    out = [{"id": "C:smc|none", "idea": "C", "kind": "smc", "regime": None}]
    for r in ("R1", "R2", "R3", "R4"):
        out.append(dict(best_a, id=f"C:{best_a['id']}|{r}", idea="C", regime=r, base=best_a["id"]))
        out.append(dict(best_b, id=f"C:{best_b['id']}|{r}", idea="C", regime=r, base=best_b["id"]))
        out.append({"id": f"C:smc|{r}", "idea": "C", "kind": "smc", "regime": r, "base": "C:smc|none"})
    return out


_CTX = {}


def context(syms, end_ms):
    key = (tuple(syms), end_ms)
    if key not in _CTX:
        mkt = market(syms, end_ms)
        _CTX.clear()
        _CTX[key] = {"mkt": mkt, "ind": ps.Ind(mkt), "z": {}, "reg": {}, "zones": None}
    return _CTX[key]


def strategy(v, ctx, seed=None):
    mkt, ind = ctx["mkt"], ctx["ind"]
    allow = None
    if v.get("regime"):
        if v["regime"] not in ctx["reg"]:
            ctx["reg"][v["regime"]] = ps.Regime(mkt, v["regime"])
        allow = ctx["reg"][v["regime"]].allow
    if v["kind"] == "trend":
        zones = None
        if v.get("smc"):
            if ctx["zones"] is None:
                h4 = {s: [b for b in bars()["h4"][s] if b["close_ts"] <= mkt.days[-1] + DAY] for s in mkt.syms}
                ctx["zones"] = ps.SmcZones(mkt, h4)
            zones = ctx["zones"]
        return ps.Trend(mkt, ind, v["entry"], v["n"], v["ls"], v["k"], allow=allow, smc=v.get("smc"), zones=zones)
    if v["kind"] == "mom":
        return ps.Momentum(mkt, ind, v["L"], v["K"], v["mode"], allow=allow, seed=seed)
    if v["W"] not in ctx["z"]:
        ctx["z"][v["W"]] = ps.funding_z(mkt, v["W"])
    if v["kind"] == "fts":
        return ps.FundingTS(mkt, ind, ctx["z"][v["W"]], v["H"], v["trend"], allow=allow)
    return ps.FundingXS(mkt, ind, ctx["z"][v["W"]], v["H"])


# ── runs ────────────────────────────────────────────────────────────────────────────────────

def compact(res, mkt):
    """Series + trades small enough to cache (raw → scratch)."""
    keep = ("sym", "side", "entry_i", "exit_i", "entry_px", "exit_px", "notional", "risk", "pnl", "fees",
            "funding", "kind", "why", "tag")
    return {"ts": res["ts"], "equity": res["equity"], "fees": res["fees"], "funding": res["funding"],
            "turnover": res["turnover"], "gross": res["gross"],
            "trades": [{k: t.get(k) for k in keep} for t in res["trades"]],
            "coin_pnl": res["coin_pnl"], "syms": mkt.syms}


def smc_series(ctx, regime, end_ms, risk=0.005):
    """C on the SMC swing base: cached 1d→4h trades, 0.5 % risk each, net R booked on the exit day."""
    with gzip.open(SWING / "grid-1d>4h.json.gz", "rt") as fh:
        rows = [t for t in json.load(fh) if t["v"] == SMC_BASE and t["exit_ts"] < end_ms and t["ts"] >= START]
    mkt = ctx["mkt"]
    allow = (lambda i: ps.ALWAYS) if regime is None else ps.Regime(mkt, regime).allow
    import bisect
    days = mkt.days
    pnl = [0.0] * len(days)
    fees = [0.0] * len(days)
    fund = [0.0] * len(days)
    trades = []
    for t in rows:
        di = bisect.bisect_right(days, t["ts"] - DAY) - 1        # last daily close ≤ the signal bar's open
        if di >= 0 and not allow(di)[0 if t["d"] > 0 else 1]:
            continue
        xi = min(len(days) - 1, max(0, bisect.bisect_right(days, t["exit_ts"]) - 1))
        net = t["r"] - t["fee"] + t["fund"]
        pnl[xi] += risk * net
        fees[xi] -= risk * t["fee"]
        fund[xi] += risk * t["fund"]
        fi = max(0, bisect.bisect_right(days, t["fill"]) - 1)
        trades.append({"sym": t["sym"], "side": t["d"], "entry_i": fi, "exit_i": xi, "pnl": risk * net,
                       "notional": 1.0, "risk": risk, "why": t["exit"]})
    eq, e = [], 1.0
    rr = []
    for x in pnl:                                               # P&L as a fraction of current equity
        rr.append(x)
        e *= 1 + x
        eq.append(e)
    by = {}
    for t in trades:
        by[t["sym"]] = by.get(t["sym"], 0.0) + t["pnl"]
    return {"ts": days, "equity": eq, "fees": [-f for f in fees], "funding": fund,
            "turnover": [0.0] * len(days), "gross": [0.0] * len(days), "trades": trades, "coin_pnl": by,
            "syms": mkt.syms}


def run_variant(v, syms, end_ms, seed=None):
    ctx = context(syms, end_ms)
    if v["kind"] == "smc":
        return smc_series(ctx, v.get("regime"), end_ms)
    res = pe.run(ctx["mkt"], strategy(v, ctx, seed))
    return compact(res, ctx["mkt"])


def bench(syms, end_ms):
    ctx = context(syms, end_ms)
    spot = pe.Config(funding=False)
    return {"BTC": compact(pe.run(ctx["mkt"], ps.BuyHold(ctx["mkt"], "BTCUSDT"), spot), ctx["mkt"]),
            "EW": compact(pe.run(ctx["mkt"], ps.BuyHold(ctx["mkt"]), spot), ctx["mkt"])}


def random_baseline(v, res, syms, end_ms, seeds=20):
    """Same-exposure random entries (prereg §4): Sharpe of each seed's run."""
    ctx = context(syms, end_ms)
    mkt, out = ctx["mkt"], []
    if v["kind"] == "mom":
        for s in range(seeds):
            r = pe.run(mkt, strategy(v, ctx, seed=f"{v['id']}|{s}"))
            out.append(pst.metrics(r, ci=False)["sharpe"])
        return out
    if v["kind"] == "smc":
        return None
    n = len(mkt.days)
    real = [t for t in res["trades"] if t["notional"]]
    for s in range(seeds):
        rnd = ps.crc_rng(v["id"], s)
        sched = []
        for t in real:
            hold = max(1, (t["exit_i"] if t["exit_i"] is not None else n - 1) - t["entry_i"])
            lo, hi = mkt.first[t["sym"]] + 1, mkt.last[t["sym"]] - hold
            if hi <= lo:
                continue
            a = rnd.randint(lo, hi)
            w = t["side"] * t["notional"] / max(1e-9, res["equity"][max(0, t["entry_i"] - 1)])
            sched.append((t["sym"], a, a + hold, w))
        r = pe.run(mkt, ps.Schedule(sched))
        out.append(pst.metrics(r, ci=False)["sharpe"])
    return out


def _job(args):
    v, syms, end_ms, rnd = args
    t0 = time.time()
    res = run_variant(v, syms, end_ms)
    base = random_baseline(v, res, syms, end_ms) if rnd else None
    return v, res, base, time.time() - t0


def save(name, obj):
    RAW.mkdir(parents=True, exist_ok=True)
    with gzip.open(RAW / f"{name}.json.gz", "wt") as fh:
        json.dump(obj, fh)


def load(name):
    with gzip.open(RAW / f"{name}.json.gz", "rt") as fh:
        return json.load(fh)


def run_many(variants, syms, end_ms, rnd=True, workers=8):
    out = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for v, res, base, dt in pool.map(_job, [(v, syms, end_ms, rnd) for v in variants]):
            m = pst.metrics(res, ci=False)
            out[v["id"]] = {"v": v, "res": res, "random": base}
            print(f"  {v['id']:<34} sharpe {m['sharpe']:>6} cagr {m['cagr']:>8} mdd {m['max_dd']:>6} "
                  f"trades {m.get('n_trades')} {dt:.0f}s", flush=True)
    return out


def best_by(runs, idea):
    ids = [k for k, r in runs.items() if r["v"]["idea"] == idea]
    return max(ids, key=lambda k: pst.metrics(runs[k]["res"], ci=False)["sharpe"])


def cmd_dev():
    main, _ = universe()
    syms = sorted(main)
    t0 = time.time()
    runs = run_many(grid(), syms, DEV_END)
    ba, bb = best_by(runs, "A"), best_by(runs, "B")
    print(f"C bases: {ba}, {bb}", flush=True)
    runs.update(run_many(c_grid(runs[ba]["v"], runs[bb]["v"]), syms, DEV_END))
    assert len(runs) == N_TRIALS, len(runs)
    save("dev_runs", runs)
    save("dev_bench", bench(syms, DEV_END))
    print(f"dev: {len(runs)} variants, {time.time() - t0:.0f}s", flush=True)



# ── selection (development only) ────────────────────────────────────────────────────────────

FOLDS = [int(datetime(y, 1, 1, tzinfo=UTC).timestamp() * 1000) for y in (2022, 2023, 2024, 2025, 2026)]


def _rets(res):
    eq = res["equity"]
    return [eq[0] - 1] + [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]


def coin_conc(res):
    by = res["coin_pnl"]
    tot = sum(by.values())
    top = max(by, key=lambda c: by[c])
    return {"top_coin": top, "top_coin_share": None if tot <= 0 else round(by[top] / tot, 3),
            "pnl_total": round(tot, 4), "pnl_without_top_coin": round(tot - by[top], 4)}


def walk_forward(runs, idea):
    """Expanding: per fold year pick the best-Sharpe variant on earlier dev days, stitch its fold returns."""
    ids = [k for k, r in runs.items() if r["v"]["idea"] == idea]
    rows, stitched = [], []
    for a, b in itertools.pairwise(FOLDS):
        def sh(k, a=a):
            res = runs[k]["res"]
            return pst.sharpe([x for x, t in zip(_rets(res), res["ts"]) if t < a])
        best = max(ids, key=sh)
        res = runs[best]["res"]
        fr = [x for x, t in zip(_rets(res), res["ts"]) if a <= t < b]
        stitched += fr
        e = 1.0
        for x in fr:
            e *= 1 + x
        rows.append({"fold": fmt(a)[:4], "chosen": best, "train_sharpe": round(sh(best), 3),
                     "fold_return": round(e - 1, 4), "fold_sharpe": round(pst.sharpe(fr), 3)})
    e = 1.0
    for x in stitched:
        e *= 1 + x
    return {"folds": rows, "stitched_return": round(e - 1, 4), "stitched_sharpe": round(pst.sharpe(stitched), 3)}


def fixed_folds(res):
    out = []
    for a, b in itertools.pairwise(FOLDS):
        fr = [x for x, t in zip(_rets(res), res["ts"]) if a <= t < b]
        e = 1.0
        for x in fr:
            e *= 1 + x
        out.append({"fold": fmt(a)[:4], "return": round(e - 1, 4), "sharpe": round(pst.sharpe(fr), 3)})
    return out


def cmd_select():
    runs, bench_runs = load("dev_runs"), load("dev_bench")
    assert len(runs) == N_TRIALS
    btc = pst.metrics(bench_runs["BTC"])
    ew = pst.metrics(bench_runs["EW"])
    table, srs, pv = {}, [], {}
    for k, r in runs.items():
        m = pst.metrics(r["res"])
        m.update(coin_conc(r["res"]))
        rnd = r["random"]
        if rnd:
            q = sorted(rnd)
            m["random_sharpe_p95"] = round(q[min(len(q) - 1, int(0.95 * len(q)))], 3)
            m["random_sharpe_median"] = round(q[len(q) // 2], 3)
        table[k] = m
        srs.append(m["sr_daily"])
        pv[k] = m["p_mean"]
    var_sr = pst.st.pvariance(srs)
    hp = pst.holm(pv)
    for k, r in runs.items():
        table[k]["dsr"] = round(pst.dsr(_rets(r["res"]), N_TRIALS, var_sr), 4)
        table[k]["p_holm"] = hp[k]
    if "C:smc|none" in table:
        base_sh = table["C:smc|none"]["sharpe"]
        for k, row in table.items():
            if k.startswith("C:smc|R"):
                row["random_sharpe_p95"] = base_sh          # prereg §4: filtered vs unfiltered base
    finals = {}
    for idea in ("A", "ASMC", "B", "C", "D"):
        best = best_by(runs, idea)
        m, wf = table[best], walk_forward(runs, idea)
        crit = {"1_dsr": m["sharpe"] > 0 and m["dsr"] >= 0.95, "2_holm": m["p_holm"] < 0.05,
                "3_not_concentrated": m["pnl_without_top_coin"] > 0 and (m["top_coin_share"] or 1) <= 0.5,
                "4_beats_btc": m["sharpe"] > btc["sharpe"] and (m["calmar"] or 0) > (btc["calmar"] or 0),
                "5_beats_random": m.get("random_sharpe_p95") is not None and m["sharpe"] > m["random_sharpe_p95"],
                "6_walk_forward": wf["stitched_return"] > 0}
        finals[idea] = {"id": best, "v": runs[best]["v"], "criteria": crit, "dev_pass": all(crit.values()),
                        "walk_forward": wf, "fixed_folds": fixed_folds(runs[best]["res"])}
    pairs = []
    for k, r in runs.items():
        if r["v"]["idea"] == "ASMC":
            a, b = table[r["v"]["pair"]], table[k]
            fills = [t for t in r["res"]["trades"]]
            pairs.append({"smc": k, "a": r["v"]["pair"], "d_sharpe": round(b["sharpe"] - a["sharpe"], 3),
                          "d_cagr": round(b["cagr"] - a["cagr"], 4), "d_fee_drag": round(b["fee_drag_yr"] - a["fee_drag_yr"], 4),
                          "d_trades": b["n_trades"] - a["n_trades"],
                          "limit_share": round(sum(t["kind"] == "limit" for t in fills) / max(1, len(fills)), 3)})
    _, asof = universe()
    surv = {}
    for idea, f in finals.items():
        if f["v"]["kind"] == "smc":
            continue
        res = run_variant(f["v"], sorted(asof), DEV_END)
        m = pst.metrics(res, ci=False)
        surv[idea] = {k: m[k] for k in ("cagr", "sharpe", "max_dd", "calmar")}
    asof_btc = pst.metrics(bench(sorted(asof), DEV_END)["EW"], ci=False)
    blob = {"n_trials": N_TRIALS, "var_sr_daily": var_sr, "benchmarks": {"BTC": btc, "EW": ew},
            "finalists": finals, "asmc_vs_a": pairs, "asof_universe": {"finalists": surv, "EW": asof_btc},
            "variants": table, "frozen_at": datetime.now(tz=UTC).isoformat()}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dev_selection.json").write_text(json.dumps(blob, indent=1, default=str))
    for idea, f in finals.items():
        m = table[f["id"]]
        print(f"{idea:<5} {f['id']:<34} sharpe {m['sharpe']} dsr {m['dsr']} holm {m['p_holm']:.3g} "
              f"cagr {m['cagr']} mdd {m['max_dd']} {f['criteria']} wf {f['walk_forward']['stitched_return']}")
    print("BTC", btc["sharpe"], btc["calmar"], "EW", ew["sharpe"], ew["calmar"])



# ── sealed holdout (run ONCE) ───────────────────────────────────────────────────────────────

def cmd_holdout():
    sel = json.loads((OUT / "dev_selection.json").read_text())
    if SENTINEL.exists():
        print(f"holdout already opened at {SENTINEL.read_text().strip()} — refusing to re-run "
              f"(results: {OUT / 'holdout.json'})")
        return
    RAW.mkdir(parents=True, exist_ok=True)
    SENTINEL.write_text(datetime.now(tz=UTC).isoformat())
    main, _ = universe()
    syms = sorted(main)
    ctx = context(syms, END)
    h0 = next(i for i, t in enumerate(ctx["mkt"].days) if t >= DEV_END)
    bm = bench(syms, END)
    out = {"window": [fmt(ctx["mkt"].days[h0]), fmt(ctx["mkt"].days[-1])], "benchmarks": {}, "finalists": {}}
    for k, r in bm.items():
        out["benchmarks"][k] = pst.metrics(r, a=h0)
    raw = {"bench": bm}
    btc_sh = out["benchmarks"]["BTC"]["sharpe"]
    for idea, f in sel["finalists"].items():
        res = run_variant(f["v"], syms, END)
        m = pst.metrics(res, a=h0)
        m["dev_check"] = pst.metrics(res, b=h0, ci=False)["sharpe"]     # must equal the dev Sharpe
        crit7 = m["total_return"] > 0 and m["sharpe"] > btc_sh
        out["finalists"][idea] = {"id": f["id"], "metrics": m, "7_holdout": crit7,
                                  "passes_all": bool(f["dev_pass"] and crit7)}
        raw[idea] = res
        print(f"{idea:<5} {f['id']:<34} holdout ret {m['total_return']} sharpe {m['sharpe']} mdd {m['max_dd']} "
              f"(dev sharpe recomputed {m['dev_check']}) crit7 {crit7}")
    print("BTC", {x: out["benchmarks"]["BTC"][x] for x in ("total_return", "sharpe", "max_dd")},
          "EW", {x: out["benchmarks"]["EW"][x] for x in ("total_return", "sharpe", "max_dd")})
    (OUT / "holdout.json").write_text(json.dumps(out, indent=1, default=str))
    save("holdout_runs", raw)



# ── report: SVG curves + markdown tables ────────────────────────────────────────────────────

def _rebase(res, a=0):
    base = res["equity"][a - 1] if a > 0 else 1.0
    return res["ts"][a:], [e / base for e in res["equity"][a:]]


def _row(name, m):
    def p(x):
        return "–" if x is None else f"{100 * x:.1f}%"
    ci = m.get("ci_sharpe")
    calmar = "–" if m["calmar"] is None else f"{m['calmar']:.2f}"
    return (f"| {name} | {p(m['cagr'])} | {p(m['vol'])} | {m['sharpe']:.2f} | "
            f"{'–' if not ci else f'[{ci[0]:.2f}, {ci[1]:.2f}]'} | {m['sortino']:.2f} | {p(m['max_dd'])} | "
            f"{m['dd_days']} | {calmar} | "
            f"{m['turnover_yr']:.1f} | {p(m['fee_drag_yr'])} | {p(m['funding_yr'])} |")


def cmd_report():
    import portfolio_svg as svg
    sel = json.loads((OUT / "dev_selection.json").read_text())
    hold = json.loads((OUT / "holdout.json").read_text())
    raw = load("holdout_runs")
    h0 = next(i for i, t in enumerate(raw["bench"]["BTC"]["ts"]) if t >= DEV_END)
    bt, be = _rebase(raw["bench"]["BTC"])
    et, ee = _rebase(raw["bench"]["EW"])
    allc = [("BTC buy&hold", bt, be, True), ("EW buy&hold", et, ee, True)]
    for idea, f in sel["finalists"].items():
        t, e = _rebase(raw[idea])
        allc.append((f"{idea}: {f['id']}", t, e, False))
        (OUT / f"equity_{idea}.svg").write_text(svg.equity_svg(
            [(f["id"], t, e, False), ("BTC buy&hold", bt, be, True)],
            f"{idea} finalist vs BTC buy-and-hold (net of costs, log scale)", split_ts=DEV_END))
    (OUT / "equity_all.svg").write_text(svg.equity_svg(allc, "Finalists vs benchmarks, 2020–2026 (log)",
                                                       split_ts=DEV_END, height=480))
    hc = [("BTC buy&hold", *_rebase(raw["bench"]["BTC"], h0), True), ("EW buy&hold", *_rebase(raw["bench"]["EW"], h0), True)]
    hc += [(f"{idea}", *_rebase(raw[idea], h0), False) for idea in sel["finalists"]]
    (OUT / "equity_holdout.svg").write_text(svg.equity_svg(hc, "Sealed holdout 2026-01-01 … 2026-10-17 (linear)",
                                                           log=False))
    hdr = ("| strategy | CAGR | vol | Sharpe | Sharpe 95% CI | Sortino | max DD | DD days | Calmar | "
           "turnover/yr | fees/yr | funding/yr |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
    lines = ["## development", hdr]
    lines += [_row(k, sel["benchmarks"][k]) for k in ("BTC", "EW")]
    lines += [_row(f"{i}: {f['id']}", sel["variants"][f["id"]]) for i, f in sel["finalists"].items()]
    lines += ["", "## holdout", hdr]
    lines += [_row(k, hold["benchmarks"][k]) for k in ("BTC", "EW")]
    lines += [_row(f"{i}: {f['id']}", f["metrics"]) for i, f in hold["finalists"].items()]
    (RAW / "tables.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"data": cmd_data, "dev": cmd_dev, "select": cmd_select, "holdout": cmd_holdout, "report": cmd_report}[cmd]()
