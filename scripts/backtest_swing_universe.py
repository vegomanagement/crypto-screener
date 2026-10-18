#!/usr/bin/env python3
"""SMC swing model on higher-TF fractal pairs over a data-selected Binance USDT-M universe.

    python scripts/backtest_swing_universe.py universe   # select + cache data (~10 min first run)
    python scripts/backtest_swing_universe.py grid       # every variant × coin on 1d→4h, 1w→1d (~10 min)
    python scripts/backtest_swing_universe.py refs       # original model on 1d→1h, 4h→15m (+ no-HTF control)
    python scripts/backtest_swing_universe.py report     # stats, corrections, controls → docs/evidence/swing-htf

Pairs: 1d→4h and 1w→1d (the full variant grid), 1d→1h and 4h→15m (the original model only, as
references). 4h / 1d / 1w bars are aggregated from Binance 1h klines (complete buckets only, weeks
from Monday 00:00 UTC); 15m and 1h come from data.binance.vision. Split: one calendar date for
every coin, OOS_START = 2020-01-01 + 70 % of the span to END; a trade belongs to the period of its
signal bar. Costs per trade in R (notional per 1 R = entry / risk): maker 0.02 % per side for limit
fills (retest / POI-edge entries, target and partial exits), taker 0.05 % + 0.01 % slippage for
market fills (CHoCH-close entries, stop / break-even / timeout exits); historical funding from
/fapi/v1/fundingRate summed over the holding time (longs pay positive rates; half size after the
partial), FUNDING_FALLBACK per 8 h against the position when the venue has no history. A stress
column charges taker + slippage on both sides. Raw trades go to scratch (OUT_RAW), never the repo.
"""
from __future__ import annotations

import bisect
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
import signal_features as sf  # noqa: E402
import signals_data as data  # noqa: E402
import smc  # noqa: E402
import swing_model as sm  # noqa: E402
import swing_universe_data as U  # noqa: E402

DAY = 86_400_000
END = int(datetime(2026, 10, 18, tzinfo=UTC).timestamp() * 1000)
START = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)
OOS_START = START + int(0.7 * (END - START)) // DAY * DAY          # 2024-10-03 00:00 UTC
OUT = ROOT / "docs" / "evidence" / "swing-htf"
OUT_RAW = Path(os.environ.get("SWING_HTF_RAW", Path.home() / ".hermes/cache/scratch/swing-htf"))
TAKER, MAKER, SLIP = 0.0005, 0.0002, 0.0001
FUNDING_FALLBACK = 0.0001                                           # per 8 h, against the position
PAIRS = {"1d>4h": ("D", "240"), "1w>1d": ("W", "D"), "1d>1h": ("D", "60"), "4h>15m": ("240", "15m")}
GRID_PAIRS = ("1d>4h", "1w>1d")
EXITS = [{"target": "liq", "be": False}, {"target": "r2", "be": False}, {"target": "r3", "be": False},
         {"target": "r2", "be": True}]
ENTRIES = [{"entry": "retest", "stop": "choch"}, {"entry": "retest", "stop": "poi"},
           {"entry": "close", "stop": "choch"}, {"entry": "close", "stop": "poi"}, {"entry": "limit", "stop": "poi"}]
GRID = [dict(poi=poi, idm=idm, sweep=sw, **en, **ex)
        for poi, idm, sw, en, ex in itertools.product(("any", "ob", "fvg", "breaker"), ("none", "idm", "eq"),
                                                     (False, True), ENTRIES, EXITS)]
BASE = {}                                                            # the original model = sm.DEFAULTS


def vid(p):
    q = {**sm.DEFAULTS, **p}
    return (f"{q['poi']}|idm={q['idm']}|sweep={int(q['sweep'])}|{q['entry']}|stop={q['stop']}|"
            f"{q['target']}{'+be' if q['be'] else ''}")


def load(sym, pair, onboard):
    htf_tf, ltf_tf = PAIRS[pair]
    h1 = U.klines(sym, "1h", END, onboard)
    agg = (lambda tf: h1 if tf == "60" else md.aggregate(h1, tf, 3_600_000))
    ltf = U.klines(sym, "15m", END, onboard) if ltf_tf == "15m" else agg(ltf_tf)
    return ltf, agg(htf_tf)


def funding_index(sym):
    rows = U.funding(sym, END)
    if not rows:
        return None
    ts = [r["time"] for r in rows]
    cum, s = [], 0.0
    for r in rows:
        s += r["rate"]
        cum.append(s)
    return ts, cum


def funding_sum(fi, a, b):
    """Sum of funding rates with a < time ≤ b (fallback constant per 8 h when no history)."""
    if b <= a:
        return 0.0
    if fi is None:
        return FUNDING_FALLBACK * (b - a) / (8 * 3_600_000)
    ts, cum = fi
    i, j = bisect.bisect_right(ts, a), bisect.bisect_right(ts, b)
    return (cum[j - 1] if j else 0.0) - (cum[i - 1] if i else 0.0)


def costs(t, fi, ltf):
    """(fee_R realistic, fee_R all-taker stress, funding_R) of a closed trade."""
    unit = t["entry"] / t["risk"]
    ent = MAKER if t["entry_type"] == "maker" else TAKER + SLIP
    ex = MAKER if t["exit"] == "target" else TAKER + SLIP
    ex = 0.5 * MAKER + 0.5 * ex if t["partial"] else ex
    a, b = t["fill_ts"], t["exit_ts"]
    if t["partial"] and t.get("partial_i") is not None:
        mid = ltf[t["partial_i"]]["close_ts"]
        f = funding_sum(fi, a, mid) + 0.5 * funding_sum(fi, mid, b)
    else:
        f = funding_sum(fi, a, b)
    return unit * (ent + ex), unit * 2 * (TAKER + SLIP), -t["d"] * unit * f


def compact(t, fi, ltf, sym, v):
    fee, fee_t, fund = costs(t, fi, ltf)
    return {"v": v, "sym": sym, "ts": t["signal_ts"], "fill": t["fill_ts"], "exit_ts": t["exit_ts"], "d": t["d"],
            "r": round(t["r"], 4), "fee": round(fee, 4), "fee_t": round(fee_t, 4), "fund": round(fund, 4),
            "risk_atr": None if t["risk_atr"] is None else round(t["risk_atr"], 3), "rt": round(t["rt"], 3),
            "exit": t["exit"], "partial": t["partial"], "bars": t["i_exit"] - t["i_fill"]}


def prepare(ltf, htf):
    atr = sf.atr([c["h"] for c in ltf], [c["l"] for c in ltf], [c["c"] for c in ltf])
    return atr, sm.htf_arrays(htf), smc.structure(ltf, sm.DEFAULTS["ltf_len"], sm.DEFAULTS["ltf_len"], tf="ltf")


def run_job(args):
    """All variants (or ``variants``) of one coin × pair; filtered and/or unfiltered."""
    sym, onboard, pair, variants, modes = args
    t0 = time.time()
    ltf, htf = load(sym, pair, onboard)
    if len(htf) < 60 or len(ltf) < 300:
        return sym, pair, [], 0.0
    atr, H, L = prepare(ltf, htf)
    fi = funding_index(sym)
    out = []
    for v in variants:
        p = json.loads(v[1]) if isinstance(v, tuple) else v
        name = v[0] if isinstance(v, tuple) else vid(v)
        for filt in modes:
            trades, _ = sm.scan(ltf, htf, p, filtered=filt, atr=atr, H=H, L=L)
            tag = name if filt else "NOHTF:" + name
            out += [compact(t, fi, ltf, sym, tag) for t in trades if t["r"] is not None]
    return sym, pair, out, time.time() - t0


def universe():
    info = U.exchange_info()
    main, n_main = U.rank(info, END, END, False)
    asof, n_asof = U.rank(info, OOS_START, END, True)
    blob = {"rule": "Binance USDT-M PERPETUAL, underlyingType COIN, non-stablecoin base, onboardDate ≥ 3 years "
                    "before the decision date, ≥ 200 trading days in the 365 days before it, top 20 by mean daily "
                    "quote volume over those 365 days",
            "main": {"decision_date": "END", "candidates": n_main, "coins": main},
            "asof_oos": {"decision_date": "OOS_START (settled/delisted contracts included)",
                         "candidates": n_asof, "coins": asof},
            "end": END, "oos_start": OOS_START}
    OUT_RAW.mkdir(parents=True, exist_ok=True)
    (OUT_RAW / "universe.json").write_text(json.dumps(blob, indent=1))
    if "--no-fetch" in sys.argv:
        return blob
    for c in {r["symbol"]: r for r in main + asof}.values():
        U.klines(c["symbol"], "1h", END, c["onboard"])
        U.funding(c["symbol"], END)
    for c in main:
        U.klines(c["symbol"], "15m", END, c["onboard"])
    return blob


def load_universe():
    return json.loads((OUT_RAW / "universe.json").read_text())


def save(name, rows):
    with gzip.open(OUT_RAW / f"{name}.json.gz", "wt") as f:
        json.dump(rows, f)


def read(name):
    with gzip.open(OUT_RAW / f"{name}.json.gz", "rt") as f:
        return json.load(f)


def run(jobs, workers=6):
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for sym, pair, out, dt in pool.map(run_job, jobs):
            rows += out
            print(f"  {pair} {sym}: {len(out)} trades, {dt:.0f}s", flush=True)
    return rows


def grid():
    uni = load_universe()
    coins = [(c["symbol"], c["onboard"]) for c in uni["main"]["coins"]]
    t0 = time.time()
    for pair in GRID_PAIRS:
        rows = run([(s, ob, pair, GRID, (True,)) for s, ob in coins])
        save(f"grid-{pair}", rows)
        print(f"{pair}: {len(rows)} trades, {len(GRID)} variants, {time.time() - t0:.0f}s", flush=True)


def refs():
    uni = load_universe()
    coins = [(c["symbol"], c["onboard"]) for c in uni["main"]["coins"]]
    t0 = time.time()
    for pair in ("1d>1h", "4h>15m"):
        rows = run([(s, ob, pair, [BASE], (True, False)) for s, ob in coins], workers=4)
        save(f"ref-{pair}", rows)
        print(f"{pair}: {len(rows)} trades (original model + no-HTF control), {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "universe":
        u = universe()
        print([c["symbol"] for c in u["main"]["coins"]], [c["symbol"] for c in u["asof_oos"]["coins"]])
    elif cmd == "grid":
        grid()
    elif cmd == "refs":
        refs()
    else:
        import swing_universe_report as rep
        rep.main(sys.modules[__name__])
