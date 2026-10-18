"""Statistics, corrections and controls for scripts/backtest_swing_universe.py (stdlib only).

net R per trade = gross R − fees (R) + funding P&L (R). Inference is clustered by calendar month
(trades of one month across coins are not independent): the z statistic uses the month-cluster
standard error, CIs come from a block bootstrap that resamples months. Multiple testing: Holm over
EVERY variant tried (the grid on both new pairs + the two reference runs), one-sided H1: mean > 0.
Pass = OOS net mean > 0 with Holm-adjusted p < 0.05, the bootstrap CI above 0, the mean still > 0
without the best coin, ≥ 60 % of coins with trades positive, better than the random-entry baseline
and the no-HTF control, and positive in the walk-forward folds.
"""
from __future__ import annotations

import math
import random
import statistics as st
from collections import defaultdict
from datetime import UTC, datetime

YEAR = 365.25 * 86_400_000


def net(t):
    return t["r"] - t["fee"] + t["fund"]


def net_taker(t):
    return t["r"] - t["fee_t"] + t["fund"]


def month(ts):
    d = datetime.fromtimestamp(ts / 1000, tz=UTC)
    return d.year * 12 + d.month


def cluster_z(trades, f=net):
    """(mean, month-cluster SE, z, one-sided p)."""
    n = len(trades)
    if n < 2:
        return (f(trades[0]) if n else None), None, None, 1.0
    xs = [f(t) for t in trades]
    m = sum(xs) / n
    g = defaultdict(float)
    for t, x in zip(trades, xs):
        g[month(t["ts"])] += x - m
    G = len(g)
    if G < 2:
        return m, None, None, 1.0
    var = G / (G - 1) * sum(v * v for v in g.values()) / n ** 2
    se = math.sqrt(var) if var > 0 else 1e-12
    z = m / se
    return m, se, z, 0.5 * math.erfc(z / math.sqrt(2))


def boot_ci(trades, f=net, B=2000, seed=7):
    by = defaultdict(list)
    for t in trades:
        by[month(t["ts"])].append(f(t))
    groups = list(by.values())
    if len(groups) < 3:
        return None, None
    rnd, means = random.Random(seed), []
    for _ in range(B):
        s = n = 0
        for _ in groups:
            g = groups[rnd.randrange(len(groups))]
            s += sum(g)
            n += len(g)
        means.append(s / n)
    means.sort()
    return means[int(0.025 * B)], means[int(0.975 * B) - 1]


def holm(pvals):
    """{key: Holm-adjusted p} for a {key: p} family."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m, out, run = len(items), {}, 0.0
    for i, (k, p) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    return out


def max_dd(trades, f=net):
    cum = peak = dd = 0.0
    for t in sorted(trades, key=lambda t: t["exit_ts"]):
        cum += f(t)
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def summary(trades, years_by_coin, bar_ms, ci=False):
    if not trades:
        return {"n": 0}
    m, se, z, p = cluster_z(trades)
    xs = [net(t) for t in trades]
    win = sum(x for x in xs if x > 0)
    loss = -sum(x for x in xs if x < 0)
    coins = sorted({t["sym"] for t in trades})
    out = {"n": len(trades), "coins": len(coins), "hit": round(sum(x > 0 for x in xs) / len(xs), 3),
           "gross": round(st.mean(t["r"] for t in trades), 3), "fees": round(st.mean(t["fee"] for t in trades), 3),
           "funding": round(st.mean(t["fund"] for t in trades), 3), "net": round(m, 3),
           "net_taker": round(st.mean(net_taker(t) for t in trades), 3),
           "se": None if se is None else round(se, 3), "z": None if z is None else round(z, 2), "p": p,
           "pf": None if not loss else round(win / loss, 2), "max_dd_r": round(max_dd(trades), 2),
           "hold_days": round(st.mean(t["bars"] for t in trades) * bar_ms / 86_400_000, 1),
           "per_coin_year": round(len(trades) / max(1e-9, sum(years_by_coin.get(c, 0) for c in years_by_coin)), 2),
           "long_share": round(sum(t["d"] > 0 for t in trades) / len(trades), 2)}
    if ci:
        lo, hi = boot_ci(trades)
        out["ci95"] = None if lo is None else [round(lo, 3), round(hi, 3)]
        out["by_coin"] = by_coin(trades)
        pos = [c for c, s in out["by_coin"].items() if s["n"] >= 1]
        out["coins_positive"] = f"{sum(out['by_coin'][c]['net'] > 0 for c in pos)}/{len(pos)}"
        best = max(pos, key=lambda c: out["by_coin"][c]["sum"]) if pos else None
        rest = [t for t in trades if t["sym"] != best]
        out["without_best_coin"] = {"coin": best, "n": len(rest),
                                    "net": round(st.mean(net(t) for t in rest), 3) if rest else None}
        top2 = sorted(pos, key=lambda c: -out["by_coin"][c]["sum"])[:2]
        tot = sum(xs)
        out["top2_share_of_total"] = None if tot <= 0 else round(sum(out["by_coin"][c]["sum"] for c in top2) / tot, 2)
    return out


def by_coin(trades):
    g = defaultdict(list)
    for t in trades:
        g[t["sym"]].append(net(t))
    return {c: {"n": len(v), "net": round(st.mean(v), 3), "sum": round(sum(v), 2)} for c, v in sorted(g.items())}


def split(trades, oos_start):
    return [t for t in trades if t["ts"] < oos_start], [t for t in trades if t["ts"] >= oos_start]


def years(coins, oos_start, end, start):
    is_y = {c["symbol"]: max(0.0, (oos_start - max(start, c["onboard"])) / YEAR) for c in coins}
    oos_y = {c["symbol"]: max(0.0, (end - max(oos_start, c["onboard"])) / YEAR) for c in coins}
    return is_y, oos_y


def walk_forward(trades, variants, folds, min_n=30):
    """Expanding-window selection: per fold year, pick the variant with the best net mean on all
    earlier trades (n ≥ min_n), record its net mean in the fold. Returns rows per fold."""
    by_v = defaultdict(list)
    for t in trades:
        by_v[t["v"]].append(t)
    rows = []
    for a, b in folds:
        best, best_m = None, -1e9
        for v in variants:
            past = [t for t in by_v[v] if t["ts"] < a]
            if len(past) >= min_n:
                m = st.mean(net(t) for t in past)
                if m > best_m:
                    best, best_m = v, m
        fold = [t for t in by_v.get(best, []) if a <= t["ts"] < b]
        rows.append({"fold": datetime.fromtimestamp(a / 1000, tz=UTC).strftime("%Y-%m-%d"),
                     "to": datetime.fromtimestamp(b / 1000, tz=UTC).strftime("%Y-%m-%d"), "chosen": best,
                     "train_net": None if best is None else round(best_m, 3), "n": len(fold),
                     "net": round(st.mean(net(t) for t in fold), 3) if fold else None})
    return rows


def fixed_walk_forward(trades, folds):
    """Net mean of ONE fixed variant per fold (consistency of a chosen rule over time)."""
    out = []
    for a, b in folds:
        f = [t for t in trades if a <= t["ts"] < b]
        out.append({"fold": datetime.fromtimestamp(a / 1000, tz=UTC).strftime("%Y-%m-%d"), "n": len(f),
                    "net": round(st.mean(net(t) for t in f), 3) if f else None})
    return out
