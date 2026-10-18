#!/usr/bin/env python3
"""Delta-neutral funding-carry analytics (docs/CARRY_PREREG.md) — research only, no orders.

    python scripts/carry_data.py        # fetch / cache history (scratch)
    python scripts/carry_analysis.py    # descriptive stats, 11 variants, risk → docs/evidence/carry/

Development = 2020-01-01 … 2025-12-31 per year; 2026-01-01 … 2026-10-17 reported separately. Each
variant runs continuously over the whole history (decisions use data ≤ the close only); the period
metrics are cut from that one equity curve. Tables for docs/CARRY_RESULT.md: scripts/carry_report.py.
"""
from __future__ import annotations

import gzip
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import carry_model as cm  # noqa: E402
from carry_data import END, RAW, START, UNIVERSE, XV  # noqa: E402

DAY = cm.DAY
DEV_END = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
OUT = ROOT / "docs" / "evidence" / "carry"
YEARS = list(range(2020, 2027))


def year_of(ts):
    return datetime.fromtimestamp(ts / 1000, tz=UTC).year


def load():
    with gzip.open(RAW / "carry.json.gz", "rt") as fh:
        return json.load(fh)


# ── descriptive (no trading rule) ───────────────────────────────────────────────────────────

def describe(rows, a, b):
    """Gross funding stats of prints with a ≤ time < b (annualised over the covered calendar span)."""
    w = [r for r in rows or () if a <= r["time"] < b]
    if len(w) < 3:
        return None
    span = (min(b, w[-1]["time"] + 1) - max(a, w[0]["time"])) / DAY
    span = max(span, 1.0)
    days = {}
    for r in w:
        days[r["time"] // DAY] = days.get(r["time"] // DAY, 0.0) + r["rate"]
    cum = peak = 0.0
    dd, dd_at, peak_at, worst = 0.0, None, w[0]["time"], None
    for r in w:
        cum += r["rate"]
        if cum > peak:
            peak, peak_at = cum, r["time"]
        if peak - cum > dd:
            dd, dd_at, worst = peak - cum, r["time"], peak_at
    return {"ann": sum(r["rate"] for r in w) * 365 / span, "n": len(w), "days": round(span, 1),
            "neg_prints": sum(r["rate"] < 0 for r in w) / len(w),
            "neg_days": sum(v < 0 for v in days.values()) / len(days),
            "worst_dd": dd, "dd_from": worst, "dd_to": dd_at}


def descriptive(blob):
    out = {}
    for venue, by in blob["funding"].items():
        for s in UNIVERSE:
            rows = by.get(s) or []
            if not rows:
                continue
            per = {str(y): describe(rows, ts(y), ts(y + 1)) for y in YEARS}
            out.setdefault(s, {})[venue] = {"years": per, "dev": describe(rows, START, DEV_END),
                                            "y2026": describe(rows, DEV_END, END), "first": rows[0]["time"]}
    return out


def ts(year):
    return int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000)


# ── instruments and variants ────────────────────────────────────────────────────────────────

def world(blob, L=1.0):
    days = list(range(START, END, DAY))
    perp = {s: cm.Inst(days, blob["perp"][s], "perp", "binance", blob["funding"]["binance"][s]) for s in UNIVERSE}
    spot = {s: cm.Inst(days, blob["spot"][s], "spot", "binance") for s in UNIVERSE}
    pairs = {s: cm.PairSpec(s, s, spot[s], perp[s], L) for s in UNIVERSE}
    xv = {}
    for v in ("bybit", "hyperliquid"):
        for s in XV:
            other = cm.Inst(days, blob["xv_klines"][v][s], "perp", v, blob["funding"][v][s])
            xv[(v, s)] = other
    return days, perp, spot, pairs, xv


def trailing(inst, days, d):
    """Annualised sum of the prints in the d days up to each close (None before d days of history)."""
    out = [None] * len(days)
    if not inst.fts:
        return out
    first = inst.fts[0]
    for i, t in enumerate(days):
        end = t + DAY
        if end - d * DAY >= first - 1:
            out[i] = inst.fsum(end - d * DAY, end) * 365 / d
    return out


def ready(pair, i):
    return pair.long.has(i) and pair.short.has(i)


def v_always(pairs, coins):
    def decide(i):
        return {s: 1.0 / len(coins) for s in coins if ready(pairs[s], i)} or None
    return decide


def v_equal_monthly(pairs, days):
    def decide(i):
        el = [s for s in UNIVERSE if ready(pairs[s], i)]
        nxt = datetime.fromtimestamp((days[i] + DAY) / 1000, tz=UTC)
        return {**{s: 1.0 / len(el) for s in el}, "__force__": nxt.day == 1} if el else None
    return decide


def v_topn(pairs, days, perp, n):
    tr = {s: trailing(perp[s], days, 30) for s in UNIVERSE}
    held = {}

    def decide(i):
        nonlocal held
        if datetime.fromtimestamp(days[i] / 1000, tz=UTC).weekday() == 6:
            el = sorted((tr[s][i], s) for s in UNIVERSE if ready(pairs[s], i) and tr[s][i] is not None and tr[s][i] > 0)
            held = {s: 1.0 / n for _, s in el[::-1][:n]}
        return {s: w for s, w in held.items() if ready(pairs[s], i)}
    return decide


def v_threshold(pairs, days, perp, d, enter, exit_):
    tr = {s: trailing(perp[s], days, d) for s in UNIVERSE}
    on = set()

    def decide(i):
        for s in UNIVERSE:
            x = tr[s][i]
            if not ready(pairs[s], i) or x is None:
                on.discard(s)
            elif s in on and x < exit_:
                on.discard(s)
            elif s not in on and x > enter:
                on.add(s)
        return {s: 1.0 / len(UNIVERSE) for s in sorted(on)}
    return decide


def xv_pairs(days, perp, xv, venue):
    out = {}
    for s in XV:
        a, b = perp[s], xv[(venue, s)]
        out[f"{s}:short_binance"] = cm.PairSpec(f"{s}:short_binance", s, b, a)
        out[f"{s}:short_{venue}"] = cm.PairSpec(f"{s}:short_{venue}", s, a, b)
    return out


def v_cross(days, perp, xv, venue, pairs):
    tr = {s: (trailing(perp[s], days, 7), trailing(xv[(venue, s)], days, 7)) for s in XV}
    state = {}

    def decide(i):
        tg = {}
        for s in XV:
            bn, ot = tr[s][0][i], tr[s][1][i]
            pid = state.get(s)
            if bn is None or ot is None or not (perp[s].has(i) and xv[(venue, s)].has(i)):
                state.pop(s, None)
                continue
            spread = {f"{s}:short_binance": bn - ot, f"{s}:short_{venue}": ot - bn}
            if pid is not None and spread[pid] < 0.05:
                state.pop(s)
                pid = None
            if pid is None:
                best = max(spread, key=spread.get)
                if spread[best] > 0.10:
                    state[s] = best
            if s in state:
                tg[state[s]] = 0.5
        return tg
    return decide


VARIANTS = ("V1 AO-BTC", "V2 AO-ETH", "V3 AO-EW", "V4 TOP3-30", "V5 TOP5-30", "V6 T7-10/5", "V7 T7-20/10",
            "V8 T30-10/5", "V9 T30-20/10", "V10 XV-BN-BY", "V11 XV-BN-HL")


def run_variant(name, blob, L=1.0, costs=None):
    days, perp, spot, pairs, xv = world(blob, L)
    k = name.split()[0]
    if k in ("V10", "V11"):
        venue = "bybit" if k == "V10" else "hyperliquid"
        ps = xv_pairs(days, perp, xv, venue)
        return days, cm.simulate(days, ps, v_cross(days, perp, xv, venue, ps), costs)
    decide = {"V1": lambda: v_always(pairs, ["BTCUSDT"]), "V2": lambda: v_always(pairs, ["ETHUSDT"]),
              "V3": lambda: v_equal_monthly(pairs, days), "V4": lambda: v_topn(pairs, days, perp, 3),
              "V5": lambda: v_topn(pairs, days, perp, 5),
              "V6": lambda: v_threshold(pairs, days, perp, 7, 0.10, 0.05),
              "V7": lambda: v_threshold(pairs, days, perp, 7, 0.20, 0.10),
              "V8": lambda: v_threshold(pairs, days, perp, 30, 0.10, 0.05),
              "V9": lambda: v_threshold(pairs, days, perp, 30, 0.20, 0.10)}[k]()
    return days, cm.simulate(days, pairs, decide, costs)


def metrics(days, res, a, b):
    idx = [i for i, t in enumerate(res["ts"]) if a <= t < b]
    if len(idx) < 2:
        return None
    i0 = idx[0]
    e0 = res["equity"][i0 - 1] if i0 > 0 else 1.0
    eq = [e0] + [res["equity"][i] for i in idx]
    rets = [eq[k] / eq[k - 1] - 1 for k in range(1, len(eq))]
    yrs = len(idx) / 365
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))
    peak, dd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak)
    avg = sum(eq) / len(eq)
    fund = sum(res["funding"][i] for i in idx) / avg / yrs
    fees = sum(res["fees"][i] for i in idx) / avg / yrs
    total = eq[-1] / eq[0] - 1
    return {"total": total, "cagr": (eq[-1] / eq[0]) ** (1 / yrs) - 1 if eq[-1] > 0 else -1.0, "vol": sd * math.sqrt(365),
            "sharpe": m / sd * math.sqrt(365) if sd else None, "max_dd": dd, "funding_yr": fund, "fees_yr": fees,
            "basis_yr": (eq[-1] - eq[0]) / avg / yrs - fund + fees,
            "deployed": sum(res["deployed"][i] for i in idx) / len(idx), "days": len(idx)}


def pumps(blob):
    """Largest 1/7/30-day perp pump (max high over the window ÷ the previous close − 1) per coin."""
    out = {}
    for s in UNIVERSE:
        b = blob["perp"][s]
        row = {}
        for label, (a, z) in (("dev", (START, DEV_END)), ("y2026", (DEV_END, END))):
            for w in (1, 7, 30):
                best, at = 0.0, None
                for i in range(1, len(b)):
                    if not a <= b[i]["ts"] < z:
                        continue
                    hi = max(x["h"] for x in b[i:i + w])
                    p = hi / b[i - 1]["c"] - 1
                    if p > best:
                        best, at = p, b[i]["ts"]
                row[f"{label}_{w}d"] = {"pump": best, "at": at}
        out[s] = row
    return out


def main():
    blob = load()
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {"descriptive": descriptive(blob), "variants": {}, "sensitivity": {}, "pumps": pumps(blob),
               "end": END, "dev_end": DEV_END}
    series = {}
    for name in VARIANTS:
        days, res = run_variant(name, blob)
        per = {str(y): metrics(days, res, ts(y), min(ts(y + 1), END)) for y in YEARS}
        summary["variants"][name] = {"dev": metrics(days, res, START, DEV_END), "y2026": metrics(days, res, DEV_END, END),
                                     "years": per, "rebalances": res["rebalances"], "trades": res["trades"],
                                     "liquidations": res["liquidations"][:50], "n_liquidations": len(res["liquidations"])}
        series[name] = {"ts": res["ts"], "equity": res["equity"]}
        d = summary["variants"][name]["dev"]
        print(f"{name}: dev CAGR {d['cagr']:+.2%} maxDD {d['max_dd']:.2%} funding/yr {d['funding_yr']:+.2%} "
              f"fees/yr {d['fees_yr']:.2%} liq {len(res['liquidations'])}", flush=True)
        _, bnb = run_variant(name, blob, costs=cm.Costs(spot=0.00075))
        summary["sensitivity"][f"{name}|bnb"] = {"dev": metrics(days, bnb, START, DEV_END),
                                                 "y2026": metrics(days, bnb, DEV_END, END)}
    for name in ("V1 AO-BTC", "V2 AO-ETH"):
        for L in (2.0, 3.0):
            days, res = run_variant(name, blob, L=L)
            summary["sensitivity"][f"{name}|L{int(L)}"] = {"dev": metrics(days, res, START, DEV_END),
                                                           "y2026": metrics(days, res, DEV_END, END),
                                                           "n_liquidations": len(res["liquidations"]),
                                                           "liquidations": res["liquidations"][:20]}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    with gzip.open(RAW / "carry_series.json.gz", "wt") as fh:
        json.dump(series, fh)
    print("wrote", OUT / "summary.json")


if __name__ == "__main__":
    main()
