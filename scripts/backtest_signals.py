#!/usr/bin/env python3
"""Event-signal backtest of the order-flow / structure candidates (re-runnable, stdlib only).

    python scripts/backtest_signals.py run [--end 2026-10-18] [--symbols ...] [--intervals ...]
    python scripts/backtest_signals.py footprint [--days 14]    # stacked-imbalance sample

Data: Binance USDT-M klines from data.binance.vision (+ /fapi for the current month), cached in
SIGNALS_CACHE (scratch, never the repo). Delta per bar = 2·taker_buy − volume (kline field 9).
Rules: entry at the signal bar's close, risk unit = ATR14 at that close.
  atr rule  : +1 ATR in the signal direction before −1 ATR, within 12 bars (same as the analyst)
  R:R rule  : stop 1 ATR, target 1.5 / 2 ATR, 48 bars, stop first if both hit in one bar,
              timeout marked to the close; net = minus 2 × 0.05 % taker fee in R.
Split: per series, the first 70 % of bars (in time) is in-sample (IS), the rest out-of-sample.
For each (signal, timeframe) the variant (parameters × HTF filter) with the best IS net
expectancy at 1.5R (IS n ≥ 300) is chosen; only that one is scored OOS.
Output: docs/evidence/wyckoff-signals/results.json and tables.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import signal_eval as se  # noqa: E402
import signal_features as sf  # noqa: E402
import signals_data as data  # noqa: E402

OUT = ROOT / "docs" / "evidence" / "wyckoff-signals"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
INTERVALS = ("5m", "15m", "1h")
IS_FRACTION = 0.7
MIN_IS = 300
MIN_OOS = 100


def variants():
    out = []
    for name, (_fn, _delta, grid) in se.RULES.items():
        out += [(name, p) for p in grid]
    out += [(name, {}) for name in se.WYCKOFF]
    out += [("funding_extreme", {"q": 0.05}), ("funding_extreme", {"q": 0.10})]
    out += [("control_every_12", {})]
    return [(n, p, f) for n, p in out for f in se.FILTERS]


def series_events(candles, s, rates, name, params):
    if name in se.RULES:
        return se.rule_events(s, name, params)
    if name in se.WYCKOFF:
        long_code, short_code = se.WYCKOFF[name]
        wy = s["_wyckoff"]
        return sorted([(i, 1) for i, _d in wy.get(long_code, [])]
                      + [(i, -1) for i, _d in wy.get(short_code, [])])
    if name == "funding_extreme":
        return se.funding_events(s, rates, q=params["q"])
    if name == "control_every_12":  # both directions every 12 bars: the drift / fee control
        return [(i, d) for i in range(50, len(candles), 12) for d in (1, -1)]
    raise KeyError(name)


def run_series(args):
    sym, iv, end_ms = args
    t0 = time.time()
    candles = data.klines(sym, iv, end_ms)
    for b in candles:
        b["delta"] = 2 * b["tb"] - b["v"]
    s = sf.prepare(candles)
    s["_wyckoff"] = se.wyckoff_events(candles)
    rates = data.funding(sym, end_ms)
    bias = se.htf_bias(s)
    n = len(candles)
    cut = int(n * IS_FRACTION)
    volr, ema, k = [], None, 2 / 501
    for i in range(n):
        x = (s["atr"][i] or 0) / s["c"][i]
        ema = x if ema is None else ema + k * (x - ema)
        volr.append("high" if x > ema else "low")
    rows = {}
    for name, params, filt in variants():
        key = json.dumps([name, params, filt])
        evs = series_events(candles, s, rates, name, params)
        if name == "control_every_12":
            evs = [e for e in evs if filt == "none" or bias[e[0]] == e[1]]
        else:
            evs = se.apply(evs, bias if filt == "htf" else None)
        outs = []
        for i, d in evs:
            o = se.score(s, i, d)
            if o is None:
                continue
            # compact tuple (pickled back from the worker): see FIELDS
            outs.append((o["atr_rule"], o["fee_r"], o["r1.5"], o["r2"], i >= cut, volr[i],
                         "aligned" if bias[i] == d else "counter" if bias[i] == -d else "flat"))
        rows[key] = outs
    meta = {"symbol": sym, "interval": iv, "bars": n, "first_ts": candles[0]["ts"],
            "last_close_ts": candles[-1]["close_ts"], "oos_from_ts": candles[cut]["ts"],
            "seconds": round(time.time() - t0, 1)}
    print(f"{sym} {iv}: {n} bars, {time.time() - t0:.0f}s", flush=True)
    return sym, iv, meta, rows


FIELDS = ("atr_rule", "fee_r", "r1.5", "r2", "oos", "vol", "trend")


def _slim(o):
    return dict(zip(FIELDS[:4], o[:4]))


def cmd_run(a):
    end_ms = int(datetime.fromisoformat(a.end).replace(tzinfo=UTC).timestamp() * 1000)
    jobs = [(s, i, end_ms) for s in a.symbols for i in a.intervals]
    with ProcessPoolExecutor(max_workers=min(len(jobs), a.workers)) as pool:
        results = list(pool.map(run_series, jobs))
    all_variants = variants()
    report = {"generated": datetime.now(UTC).isoformat(timespec="seconds"), "end": a.end,
              "rules": __doc__.split("Rules:")[1].split("Output:")[0].strip(),
              "fee_per_side": sf.FEE_PER_SIDE, "is_fraction": IS_FRACTION,
              "series": [r[2] for r in results], "variants_tested": 0, "choices": {}}
    by_iv = {}
    for sym, iv, _meta, rows in results:
        for key, outs in rows.items():
            by_iv.setdefault(iv, {}).setdefault(key, []).extend((sym, o) for o in outs)
    tested, chosen = 0, []
    families = sorted({v[0] for v in all_variants})
    for iv in a.intervals:
        for fam in families:
            cands = []
            for name, params, filt in all_variants:
                if name != fam:
                    continue
                key = json.dumps([name, params, filt])
                outs = by_iv[iv].get(key, [])
                is_rows = [_slim(o) for _s, o in outs if not o[4]]
                tested += 1
                st = se.summarize(is_rows)
                cands.append((st.get("expectancy_r", -9) if st["n"] >= MIN_IS else -9, key, st))
            best = max(cands, key=lambda c: c[0])
            chosen.append((iv, fam, best[1], best[2], [(c[1], c[2]) for c in cands]))
    report["variants_tested"] = tested
    m_oos = sum(1 for c in chosen if c[3]["n"] >= MIN_IS and not c[1].startswith("control"))
    z_bonf = se.z_for(0.05 / max(1, m_oos))
    report.update(oos_hypotheses=m_oos, z_bonferroni=round(z_bonf, 3))
    for iv, fam, key, is_stats, cands in chosen:
        outs = by_iv[iv].get(key, [])
        oos = [o for _s, o in outs if o[4]]
        block = {"variant": json.loads(key), "is": is_stats,
                 "is_all_variants": {k: {f: v.get(f) for f in ("n", "expectancy_r", "hit_rate_decided")}
                                     for k, v in cands},
                 "oos_net_1.5": se.summarize([_slim(o) for o in oos]),
                 "oos_net_1.5_bonf": se.summarize([_slim(o) for o in oos], z=z_bonf),
                 "oos_net_2": se.summarize([_slim(o) for o in oos], key="r2"),
                 "oos_gross_1.5": se.summarize([_slim(o) for o in oos], net=False),
                 "oos_by_symbol": {}, "oos_by_regime": {}}
        for sym in a.symbols:
            block["oos_by_symbol"][sym] = se.summarize(
                [_slim(o) for s_, o in outs if o[4] and s_ == sym])
        for dim, col in (("trend", 6), ("vol", 5)):
            for val in sorted({o[col] for o in oos}):
                block["oos_by_regime"][f"{dim}={val}"] = se.summarize(
                    [_slim(o) for o in oos if o[col] == val])
        st = block["oos_net_1.5_bonf"]
        pos_syms = sum(1 for v in block["oos_by_symbol"].values()
                       if v.get("n", 0) >= 30 and v["expectancy_r"] > 0)
        block["passes"] = bool(not fam.startswith("control") and is_stats["n"] >= MIN_IS
                               and st.get("n", 0) >= MIN_OOS and st.get("expectancy_ci")
                               and st["expectancy_ci"][0] > 0 and pos_syms >= 2)
        report["choices"][f"{iv}:{fam}"] = block
    # OOS stats of EVERY variant (descriptive; the pass/fail above uses only IS-chosen ones):
    # the dashboard shows the stats of exactly the variant it detects live.
    stats = {"generated": report["generated"], "end": a.end, "fee_per_side": sf.FEE_PER_SIDE,
             "rule": "stop 1 ATR, target 1.5 ATR, 48 bars, net of fees; ±1 ATR hit within 12 bars",
             "symbols": list(a.symbols), "oos_from": {f"{m['symbol']}:{m['interval']}": m["oos_from_ts"]
                                                      for m in report["series"]},
             "passed": [k for k, b in report["choices"].items() if b["passes"]], "variants": {}}
    for iv in a.intervals:
        for name, params, filt in all_variants:
            key = json.dumps([name, params, filt])
            oos = [_slim(o) for _s, o in by_iv[iv].get(key, []) if o[4]]
            st = se.summarize(oos)
            stats["variants"][f"{iv}|{key}"] = {k: st.get(k) for k in (
                "n", "hit_rate_decided", "hit_ci95_decided", "rr_win_rate", "expectancy_r",
                "expectancy_ci", "mean_fee_r")}
    report["variants_oos"] = stats["variants"]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(report, indent=1))
    (ROOT / "signal_stats.json").write_text(json.dumps(stats, indent=0, sort_keys=True))
    from backtest_signals_report import write_tables
    write_tables(report, OUT / "tables.md")
    print(f"variants tested {tested}, OOS hypotheses {m_oos}, z_bonf {z_bonf:.2f}")
    for k, b in report["choices"].items():
        s = b["oos_net_1.5"]
        print(f"{k:28s} {b['variant'][1]!s:28s} {b['variant'][2]:4s} IS n={b['is']['n']:6d} "
              f"exp={b['is'].get('expectancy_r')} | OOS n={s.get('n')} exp={s.get('expectancy_r')} "
              f"ci={s.get('expectancy_ci')} hit={s.get('hit_rate_decided')} pass={b['passes']}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--end", default="2026-10-18")
    r.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    r.add_argument("--intervals", nargs="+", default=list(INTERVALS))
    r.add_argument("--workers", type=int, default=int(os.environ.get("SIGNALS_WORKERS", "5")))
    f = sub.add_parser("footprint")
    f.add_argument("--days", type=int, default=14)
    f.add_argument("--end", default="2026-10-17")
    a = p.parse_args()
    if a.cmd == "run":
        cmd_run(a)
    else:
        from backtest_signals_footprint import cmd_footprint
        cmd_footprint(a)


if __name__ == "__main__":
    main()
