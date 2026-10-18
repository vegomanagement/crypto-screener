"""Portfolio metrics and tests for scripts/backtest_portfolio.py (stdlib) — prereg §5.

Daily returns r_t = E_t / E_{t−1} − 1 on a 365-day year. Inference is clustered by calendar month:
the month-block bootstrap resamples whole months; the one-sided p of mean > 0 uses the
month-cluster standard error. PSR / DSR follow Bailey & López de Prado (2012, 2014).
"""
from __future__ import annotations

import math
import random
import statistics as st
from collections import defaultdict
from datetime import UTC, datetime

DAY = 86_400_000
YR = 365.0
EULER = 0.5772156649


def _phi(x):
    return 0.5 * math.erfc(-x / math.sqrt(2))


def _phinv(p):
    lo, hi = -10.0, 10.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if _phi(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def sharpe(rets):
    if len(rets) < 2:
        return 0.0
    sd = st.pstdev(rets)
    return 0.0 if sd == 0 else st.fmean(rets) / sd * math.sqrt(YR)


def sortino(rets):
    dn = math.sqrt(sum(min(0.0, r) ** 2 for r in rets) / max(1, len(rets)))
    return 0.0 if dn == 0 else st.fmean(rets) / dn * math.sqrt(YR)


def drawdown(equity, ts):
    """(max drawdown as a fraction, its duration in days: the worst peak → recovery or the end)."""
    peak_i, mdd, worst = 0, 0.0, 0
    for i, e in enumerate(equity):
        if e >= equity[peak_i]:
            peak_i = i
        dd = 1 - e / equity[peak_i]
        if dd > mdd:
            mdd, worst = dd, peak_i
    if mdd == 0:
        return 0.0, 0
    rec = next((i for i in range(worst + 1, len(equity)) if equity[i] >= equity[worst]), len(equity) - 1)
    return mdd, int((ts[rec] - ts[worst]) // DAY)


def month_key(ts):
    d = datetime.fromtimestamp(ts / 1000, tz=UTC)
    return d.year * 12 + d.month - 1


def _months(rets, ts):
    g = defaultdict(list)
    for r, t in zip(rets, ts):
        g[month_key(t)].append(r)
    return [g[k] for k in sorted(g)]


def cluster_p(rets, ts):
    """One-sided p of mean daily return > 0 with a month-cluster SE."""
    n = len(rets)
    if n < 30:
        return 1.0
    m = st.fmean(rets)
    groups = _months([r - m for r in rets], ts)
    G = len(groups)
    var = G / (G - 1) * sum(sum(g) ** 2 for g in groups) / n ** 2
    if var <= 0:
        return 1.0
    return 0.5 * math.erfc(m / math.sqrt(var) / math.sqrt(2))


def boot(rets, ts, B=2000, seed=7):
    """Month-block bootstrap 95 % CIs: (sharpe_lo, sharpe_hi, mean_lo, mean_hi) (mean annualised)."""
    groups = _months(rets, ts)
    if len(groups) < 6:
        return None
    rnd, shs, mus = random.Random(seed), [], []
    for _ in range(B):
        xs = []
        for _ in groups:
            xs += groups[rnd.randrange(len(groups))]
        shs.append(sharpe(xs))
        mus.append(st.fmean(xs) * YR)
    shs.sort()
    mus.sort()
    lo, hi = int(0.025 * B), int(0.975 * B) - 1
    return shs[lo], shs[hi], mus[lo], mus[hi]


def psr(rets, sr_star_daily=0.0):
    """Probabilistic Sharpe: P(true daily SR > sr_star) given skew/kurtosis of the sample."""
    n = len(rets)
    if n < 30:
        return 0.0
    m, sd = st.fmean(rets), st.pstdev(rets)
    if sd == 0:
        return 0.0
    sr = m / sd
    sk = sum((r - m) ** 3 for r in rets) / n / sd ** 3
    ku = sum((r - m) ** 4 for r in rets) / n / sd ** 4
    den = math.sqrt(max(1e-12, 1 - sk * sr + (ku - 1) / 4 * sr * sr))
    return _phi((sr - sr_star_daily) * math.sqrt(n - 1) / den)


def dsr(rets, n_trials, var_sr_daily):
    """Deflated Sharpe: PSR against the expected max daily SR of ``n_trials`` null trials."""
    if n_trials < 2:
        return psr(rets)
    e_max = math.sqrt(max(0.0, var_sr_daily)) * ((1 - EULER) * _phinv(1 - 1 / n_trials)
                                                 + EULER * _phinv(1 - 1 / (n_trials * math.e)))
    return psr(rets, e_max)


def holm(pvals):
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m, out, run = len(items), {}, 0.0
    for i, (k, p) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    return out


def trade_stats(trades):
    """Hit rate, avg win/loss in R, top-third share of P&L, top coin share, P&L without top coin."""
    done = [t for t in trades if t.get("pnl") is not None]
    if not done:
        return {"n_trades": 0}
    rs = []
    for t in done:
        rf = t.get("risk") or None
        rs.append(t["pnl"] / (t["notional"] * rf) if rf and t["notional"] else None)
    wins = [r for r in rs if r is not None and r > 0]
    loss = [r for r in rs if r is not None and r <= 0]
    pn = sorted((t["pnl"] for t in done), reverse=True)
    tot = sum(pn)
    k = max(1, len(pn) // 3)
    by = defaultdict(float)
    for t in done:
        by[t["sym"]] += t["pnl"]
    top = max(by, key=lambda c: by[c])
    return {"n_trades": len(done), "hit": round(sum(t["pnl"] > 0 for t in done) / len(done), 3),
            "avg_win_r": round(st.fmean(wins), 3) if wins else None,
            "avg_loss_r": round(st.fmean(loss), 3) if loss else None,
            "top_third_share": None if tot <= 0 else round(sum(pn[:k]) / tot, 2),
            "top_coin": top, "top_coin_share": None if tot <= 0 else round(by[top] / tot, 2),
            "pnl_total": round(tot, 4), "pnl_without_top_coin": round(tot - by[top], 4),
            "hold_days": round(st.fmean((t["exit_i"] if t["exit_i"] is not None else t["entry_i"]) - t["entry_i"]
                                        for t in done), 1),
            "long_share": round(sum(t["side"] > 0 for t in done) / len(done), 2)}


def per_year(equity, ts):
    """{year: return} from an equity curve whose first point is the start value."""
    out, start, prev_y = {}, equity[0], None
    for i in range(1, len(equity)):
        y = datetime.fromtimestamp(ts[i] / 1000, tz=UTC).year
        if prev_y is not None and y != prev_y:
            out[str(prev_y)] = round(equity[i - 1] / start - 1, 4)
            start = equity[i - 1]
        prev_y = y
    if prev_y is not None:
        out[str(prev_y)] = round(equity[-1] / start - 1, 4)
    return out


def metrics(res, a=0, b=None, ci=True):
    """Every prereg §5 metric of a run, over the day-index window [a, b)."""
    eq_all = res["equity"]
    b = len(eq_all) if b is None else b
    base = eq_all[a - 1] if a > 0 else 1.0
    eq = [e / base for e in eq_all[a:b]]
    ts = res["ts"][a:b]
    rets = [eq[0] - 1] + [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
    yrs = len(eq) / YR
    cagr = eq[-1] ** (1 / yrs) - 1 if eq[-1] > 0 else -1.0
    mdd, ddd = drawdown([1.0] + eq, [ts[0] - DAY] + ts)
    eqs = [eq_all[i - 1] if i > 0 else 1.0 for i in range(a, b)]
    fees = sum(f / e for f, e in zip(res["fees"][a:b], eqs))
    fund = sum(f / e for f, e in zip(res["funding"][a:b], eqs))
    turn = sum(f / e for f, e in zip(res["turnover"][a:b], eqs))
    out = {"days": len(eq), "total_return": round(eq[-1] - 1, 4), "cagr": round(cagr, 4),
           "vol": round(st.pstdev(rets) * math.sqrt(YR), 4), "sharpe": round(sharpe(rets), 3),
           "sortino": round(sortino(rets), 3), "max_dd": round(mdd, 4), "dd_days": ddd,
           "calmar": None if mdd == 0 else round(cagr / mdd, 3),
           "turnover_yr": round(turn / yrs, 2), "fee_drag_yr": round(fees / yrs, 4),
           "funding_yr": round(fund / yrs, 4), "avg_gross": round(st.fmean(res["gross"][a:b]), 3),
           "exposure": round(sum(g > 0 for g in res["gross"][a:b]) / len(eq), 3),
           "p_mean": cluster_p(rets, ts), "psr": round(psr(rets), 4),
           "sr_daily": st.fmean(rets) / st.pstdev(rets) if st.pstdev(rets) else 0.0,
           "per_year": per_year([1.0] + eq, [ts[0] - DAY] + ts)}
    if ci:
        bt = boot(rets, ts)
        out["ci_sharpe"] = None if bt is None else [round(bt[0], 3), round(bt[1], 3)]
        out["ci_mean_ann"] = None if bt is None else [round(bt[2], 4), round(bt[3], 4)]
    t0, t1 = res["ts"][a], res["ts"][b - 1]
    tr = [t for t in res.get("trades", []) if t0 <= res["ts"][t["entry_i"]] <= t1]
    out.update(trade_stats(tr))
    return out
