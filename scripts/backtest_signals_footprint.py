"""Stacked-imbalance sample from Binance aggTrades (scripts/backtest_signals.py footprint).

Full-history per-price footprints are too heavy for a stdlib run (BTC alone is ~1–2 M
aggTrades per day), so this scores stacked imbalances on the last ``--days`` UTC days only:
indicative, not part of the multiple-testing pass/fail. Footprint rules are the app's own
(footprint.analyze: ask@p vs bid@p−1 ≥ 300 %, ≥ 3 consecutive levels).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import footprint as fp
import signal_eval as se
import signal_features as sf
import signals_data as data

STEPS = {"BTCUSDT": 10.0, "ETHUSDT": 0.5, "SOLUSDT": 0.02}  # 5m level size; 15m uses 2×
IVS = {"5m": 300_000, "15m": 900_000}


def _bars_levels(sym, days, iv, mult):
    step = STEPS[sym] * mult
    out = {}
    for day in days:
        trades = data.agg_trades_day(sym, day)
        if trades is None:
            raise RuntimeError(f"no aggTrades file for {sym} {day}")
        for t, price, qty, buy in trades:
            bar = t // IVS[iv] * IVS[iv]
            cell = out.setdefault(bar, {}).setdefault(int(price // step), [0.0, 0.0])
            cell[1 if buy else 0] += qty
    return out, Decimal(str(step))


def stacked_events(levels_by_bar, step, s):
    """[(i, dir, kind)] continuation (+stack side, close on that side) and fade variants."""
    idx = {ts: i for i, ts in enumerate(s["ts"])}
    out = []
    for bar, cells in sorted(levels_by_bar.items()):
        i = idx.get(bar)
        if i is None:
            continue
        levels = [{"index": k, "bid": Decimal(f"{b:.8f}"), "ask": Decimal(f"{a:.8f}")}
                  for k, (b, a) in cells.items()]
        st = fp.analyze(levels, step)["stacked"]
        sides = {z["side"] for z in st}
        if sides == {"buy"}:
            out.append((i, 1, s["clv"][i] >= 0.5))
        elif sides == {"sell"}:
            out.append((i, -1, s["clv"][i] <= 0.5))
    return out


def cmd_footprint(a):
    from backtest_signals import OUT, SYMBOLS, _slim
    end = datetime.fromisoformat(a.end).replace(tzinfo=UTC)
    days = [(end - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(a.days - 1, -1, -1)]
    kl_end = int((end + timedelta(days=1)).timestamp() * 1000)  # the main run's cached end
    report = {"days": days, "rules": __doc__.strip(), "cells": {}}
    for iv, mult in (("5m", 1), ("15m", 2)):
        pooled = {"continuation": [], "fade": []}
        for sym in SYMBOLS:
            candles = data.klines(sym, iv, kl_end)
            candles = [c for c in candles if c["ts"] >= int(end.timestamp() * 1000) - 40 * 86_400_000]
            s = sf.prepare(candles)
            lv, step = _bars_levels(sym, days, iv, mult)
            evs = stacked_events(lv, step, s)
            for kind in ("continuation", "fade"):
                chosen = [(i, d if kind == "continuation" else -d) for i, d, same in evs if same]
                outs = [o for o in (se.score(s, i, d) for i, d in se.apply(chosen)) if o]
                pooled[kind] += outs
                report["cells"][f"{iv}:{sym}:{kind}"] = se.summarize([_slim(_t(o)) for o in outs])
            print(iv, sym, "bars with stacked imbalances:", len(evs), flush=True)
        for kind, outs in pooled.items():
            report["cells"][f"{iv}:ALL:{kind}"] = se.summarize([_slim(_t(o)) for o in outs])
    (OUT / "footprint_sample.json").write_text(json.dumps(report, indent=1))
    for k, v in report["cells"].items():
        print(f"{k:28s} n={v.get('n')} hit={v.get('hit_rate_decided')} E[R]net={v.get('expectancy_r')} "
              f"ci={v.get('expectancy_ci')}")


def _t(o):
    return (o["atr_rule"], o["fee_r"], o["r1.5"], o["r2"])
