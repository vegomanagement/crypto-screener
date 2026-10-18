#!/usr/bin/env python3
"""HTF-alignment filter on the existing event signals (re-runnable, stdlib only).

    python scripts/backtest_mtf.py [--end 2026-10-18] [--symbols ...] [--intervals 15m 1h 4h]

Same signals, entry and outcome rules as scripts/backtest_signals.py (entry at the signal bar's
close, ATR14 risk unit, stop 1 ATR, targets 1.5 / 2 ATR within 48 bars and 3 ATR within 96 bars,
stop first when both hit in one bar), fees 2 × 0.05 % taker and 2 × 0.02 % maker. HTFs: 15m →
(1h, 4h), 1h → (4h, 1d), 4h → (1d, 1w), built from complete 1h bars; an LTF bar at close time t
only sees HTF bars with close_ts ≤ t (never the forming HTF candle). HTF features per closed HTF
bar k (causal): bias = mtf.bias votes (EMA 7/14/21 stack, close vs EMA 200, EMA 200 slope, SMC
external trend, HH/HL; |score| ≥ 3 = clear) on full-history EMAs; Wyckoff = wyckoff.analyze over
the 300 HTF bars ending at k; levels = Wyckoff TR edges, last confirmed swing high/low, 120-bar
POC/VAH/VAL, EMA 200.
Filters: none | bias1 (HTF1 clear bias = side) | bias2 (HTF1 and HTF2) | smc1 | smc2 (SMC trend) |
wyck (HTF1 Wyckoff structure in the side's schematic) | wyck_na (HTF1 schematic not against) |
level (entry within 0.3 HTF1 ATR of an HTF1 level) | combo (bias2 + wyck_na + level).
Split 70/30 by time per series; params chosen per (tf, signal, filter) on IS (n ≥ 100).
Output: docs/evidence/mtf-sync/align.json and align.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import mtf  # noqa: E402
import mtf_data as md  # noqa: E402
import signal_eval as se  # noqa: E402
import signal_features as sf  # noqa: E402
import signals_data as data  # noqa: E402
import smc  # noqa: E402
import wyckoff  # noqa: E402

OUT = ROOT / "docs" / "evidence" / "mtf-sync"
HTFS = {"15m": ("60", "240"), "1h": ("240", "D"), "4h": ("D", "W")}
FILTERS = ("none", "bias1", "bias2", "smc1", "smc2", "wyck", "wyck_na", "level", "combo")
MAKER_SCALE = 0.02 / 0.05
IS_FRACTION, MIN_IS = 0.7, 100
NEWEST_FROM = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)


def htf_features(htf, periods=(7, 14, 21)):
    closes = [c["c"] for c in htf]
    ribbon = [mtf.ema_series(closes, p) for p in periods]
    e200 = mtf.ema_series(closes, 200)
    atr = sf.atr([c["h"] for c in htf], [c["l"] for c in htf], closes)
    S = smc.structure(htf, *smc.EXTERNAL, zones=False)
    by_c = {}
    for s in S["swings"]:
        by_c.setdefault(s["confirm_i"], []).append(s)
    hs, ls = [], []
    out = {"bias": [], "smc": S["trend_at"], "wy": [], "levels": [], "atr": atr}
    for k in range(len(htf)):
        for s in by_c.get(k, ()):
            (hs if s["kind"] == "H" else ls).append(s["price"])
        votes = []
        rv = [r[k] for r in ribbon]
        if None not in rv:
            votes.append(1 if all(a > b for a, b in zip(rv, rv[1:])) else -1 if all(a < b for a, b in zip(rv, rv[1:])) else 0)
        if e200[k] is not None:
            votes.append(1 if closes[k] > e200[k] else -1)
            if k >= 10 and e200[k - 10] is not None and atr[k]:
                dl = e200[k] - e200[k - 10]
                votes.append(0 if abs(dl) < 0.1 * atr[k] else (1 if dl > 0 else -1))
        votes.append(S["trend_at"][k])
        votes.append(0 if len(hs) < 2 or len(ls) < 2 else
                     1 if hs[-1] > hs[-2] and ls[-1] > ls[-2] else -1 if hs[-1] < hs[-2] and ls[-1] < ls[-2] else 0)
        score = sum(votes)
        out["bias"].append(1 if score >= mtf.BIAS_CLEAR else -1 if score <= -mtf.BIAS_CLEAR else 0)
        lv = [x for x in (e200[k], hs[-1] if hs else None, ls[-1] if ls else None) if x is not None]
        wd = 0
        if k >= 60:
            w = wyckoff.analyze(htf[max(0, k - mtf.CANDLES + 1):k + 1])
            if w["status"] == "structure":
                wd = 1 if w["schematic"] == "accumulation" else -1
            if w["tr"]:
                lv += [x for x in (w["tr"]["support"], w["tr"]["resistance"]) if x is not None]
            win = htf[max(0, k - mtf.PROFILE_BARS + 1):k + 1]
            p = sf.volume_profile([c["h"] for c in win], [c["l"] for c in win], [c["v"] for c in win])
            if p:
                lv += [p["poc"], p["vah"], p["val"]]
        out["wy"].append(wd)
        out["levels"].append(lv)
    return out


def htf_bars(sym, tf, end_ms):
    h1 = data.klines(sym, "1h", end_ms)
    return h1 if tf == "60" else md.aggregate(h1, tf, 3_600_000)


def keep(f, d, k1, k2, F1, F2, entry):
    if f == "none":
        return True
    if k1 < 0 or k2 < 0:
        return False
    b1, b2 = F1["bias"][k1] == d, F2["bias"][k2] == d
    s1, s2 = F1["smc"][k1] == d, F2["smc"][k2] == d
    na = F1["wy"][k1] != -d
    a = F1["atr"][k1]
    lvl = bool(a) and any(abs(entry - x) <= 0.3 * a for x in F1["levels"][k1])
    return {"bias1": b1, "bias2": b1 and b2, "smc1": s1, "smc2": s1 and s2, "wyck": F1["wy"][k1] == d,
            "wyck_na": na, "level": lvl, "combo": b1 and b2 and na and lvl}[f]


def run_series(args):
    sym, iv, end_ms = args
    t0 = time.time()
    candles = htf_bars(sym, "240", end_ms) if iv == "4h" else data.klines(sym, iv, end_ms)
    for b in candles:
        b["delta"] = 2 * b["tb"] - b["v"]
    tf1, tf2 = HTFS[iv]
    h1b, h2b = htf_bars(sym, tf1, end_ms), htf_bars(sym, tf2, end_ms)
    F1, F2 = htf_features(h1b), htf_features(h2b)
    i1, i2 = md.htf_index(candles, h1b), md.htf_index(candles, h2b)
    s = sf.prepare(candles)
    s["_wyckoff"] = se.wyckoff_events(candles)
    n, cut = len(candles), int(len(candles) * IS_FRACTION)
    from backtest_signals import series_events, variants
    names = sorted({(v[0], json.dumps(v[1])) for v in variants() if v[0] != "funding_extreme"})
    memo, rows = {}, {}
    for name, pj in names:
        evs = series_events(candles, s, [], name, json.loads(pj))
        for f in FILTERS:
            kept, last = [], -10**9
            for i, d in evs:
                if not keep(f, d, i1[i], i2[i], F1, F2, s["c"][i]):
                    continue
                if name != "control_every_12" and i - last < se.COOLDOWN:   # control: both sides kept
                    continue
                kept.append((i, d))
                last = i
            outs = []
            for i, d in kept:
                if (i, d) not in memo:
                    a = se.score(s, i, d)
                    b = se.score(s, i, d, horizon_rr=96, targets=(3.0,)) if a else None
                    memo[(i, d)] = None if a is None or b is None else (
                        a["atr_rule"], a["fee_r"], a["r1.5"], a["r2"], b["r3"])
                o = memo[(i, d)]
                if o is not None:
                    outs.append(o + (i >= cut, s["ts"][i]))
            rows[json.dumps([name, json.loads(pj), f])] = outs
    meta = {"symbol": sym, "interval": iv, "bars": n, "htf": [tf1, tf2], "oos_from_ts": candles[cut]["ts"],
            "last_close_ts": candles[-1]["close_ts"], "seconds": round(time.time() - t0, 1)}
    print(f"{sym} {iv}: {n} bars, {time.time() - t0:.0f}s", flush=True)
    return meta, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--end", default="2026-10-18")
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    ap.add_argument("--intervals", nargs="+", default=["15m", "1h", "4h"])
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    end_ms = int(datetime.fromisoformat(a.end).replace(tzinfo=UTC).timestamp() * 1000)
    jobs = [(s, i, end_ms) for i in a.intervals for s in a.symbols]
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        results = list(pool.map(run_series, jobs))
    from backtest_mtf_report import aggregate_align, align_tables
    report = aggregate_align(results, a, FILTERS, MIN_IS, MAKER_SCALE, NEWEST_FROM)
    report["rules"] = __doc__.split("Same signals")[1].split("Output:")[0].strip()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "align.json").write_text(json.dumps(report, indent=1))
    (OUT / "align.md").write_text(align_tables(report))
    print(f"variants {report['variants_tested']}, OOS hypotheses {report['oos_hypotheses']}, "
          f"z_bonf {report['z_bonferroni']}, passed {report['passed']}")


if __name__ == "__main__":
    main()
