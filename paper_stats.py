"""Read-only statistics of the paper journal: counted trades only (closed, not seen at start,
net R known). Hit = net R > 0 (as docs/SWING_HTF_RESULT.md), Wilson 95 % CI; mean net R with a
seeded bootstrap 95 % CI (month-block when the trades span ≥ 3 calendar months, like the
backtest, else per-trade); equity curve and max drawdown in R by exit time; per-coin table."""
from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import UTC, datetime

from paper_store import CLOSED_STATES, OPEN_STATES

MIN_TRADES_NOTE = ("Fewer than 30–50 closed trades say almost nothing: the backtest's own 95 % CI on 422 "
                   "trades was +0.017…+0.263 R. Paper results, not a signal; no orders are placed.")


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(mid - half, 4), round(mid + half, 4)]


def _month(ts):
    d = datetime.fromtimestamp(ts / 1000, tz=UTC)
    return d.year * 12 + d.month


def bootstrap_ci(rows, B=2000, seed=7):
    """95 % CI of the mean net R; month-block bootstrap with ≥ 3 months, else per trade. None if n < 3."""
    if len(rows) < 3:
        return None, None, None
    by = defaultdict(list)
    for r in rows:
        by[_month(r["exit_ts"])].append(r["net_r"])
    groups = list(by.values()) if len(by) >= 3 else [[r["net_r"]] for r in rows]
    rnd, means = random.Random(seed), []
    for _ in range(B):
        s = n = 0
        for _ in groups:
            g = groups[rnd.randrange(len(groups))]
            s += sum(g)
            n += len(g)
        means.append(s / n)
    means.sort()
    return round(means[int(0.025 * B)], 4), round(means[int(0.975 * B) - 1], 4), "month-block" if len(by) >= 3 else "trade"


def equity(rows):
    """[{ts, cum_r, symbol}] by exit time and the max drawdown (R) of that curve."""
    cum = peak = dd = 0.0
    pts = []
    for r in sorted(rows, key=lambda r: (r["exit_ts"], r["id"])):
        cum += r["net_r"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        pts.append({"ts": r["exit_ts"], "cum_r": round(cum, 4), "symbol": r["symbol"], "net_r": round(r["net_r"], 4)})
    return pts, round(dd, 4)


def block(rows):
    n = len(rows)
    if not n:
        return {"n": 0, "hit": None, "hit_ci": None, "mean_r": None, "mean_r_ci": None, "sum_r": 0.0}
    wins = sum(1 for r in rows if r["net_r"] > 0)
    mean = sum(r["net_r"] for r in rows) / n
    ci = bootstrap_ci(rows)
    return {"n": n, "hit": round(wins / n, 4), "hit_ci": wilson(wins, n), "mean_r": round(mean, 4),
            "mean_r_ci": None if ci[0] is None else [ci[0], ci[1]], "ci_method": ci[2],
            "sum_r": round(sum(r["net_r"] for r in rows), 4),
            "mean_gross_r": round(sum(r["r_gross"] for r in rows) / n, 4),
            "mean_fee_r": round(sum(r["fee_r"] or 0 for r in rows) / n, 4),
            "mean_fund_r": round(sum(r["fund_r"] or 0 for r in rows) / n, 4),
            "mean_pct": round(sum(r["net_pct"] or 0 for r in rows) / n, 4)}


def summarize(all_rows, expectation=None):
    closed = [r for r in all_rows if r["state"] in CLOSED_STATES]
    counted = [r for r in closed if not r["seen_at_start"] and r["net_r"] is not None]
    pts, dd = equity(counted)
    coins = defaultdict(list)
    for r in counted:
        coins[r["symbol"]].append(r)
    per_coin = {s: {"n": len(v), "hit": round(sum(1 for r in v if r["net_r"] > 0) / len(v), 4),
                    "mean_r": round(sum(r["net_r"] for r in v) / len(v), 4), "sum_r": round(sum(r["net_r"] for r in v), 4)}
                for s, v in sorted(coins.items())}
    out = {**block(counted), "cum_r": pts[-1]["cum_r"] if pts else 0.0, "max_dd_r": dd, "equity": pts,
           "per_coin": per_coin,
           "counts": {"pending": sum(1 for r in all_rows if r["state"] == "pending" and not r["seen_at_start"]),
                      "open": sum(1 for r in all_rows if r["state"] in OPEN_STATES and not r["seen_at_start"]),
                      "closed_counted": len(counted),
                      "closed_awaiting_funding": sum(1 for r in closed if not r["seen_at_start"] and r["net_r"] is None),
                      "expired": sum(1 for r in all_rows if r["state"] == "expired" and not r["seen_at_start"]),
                      "seen_at_start": sum(1 for r in all_rows if r["seen_at_start"]),
                      "late": sum(1 for r in all_rows if r["late"])},
           "exits": {s: sum(1 for r in counted if r["state"] == s) for s in CLOSED_STATES},
           "note": MIN_TRADES_NOTE}
    if expectation:
        exp = expectation.get("oos_net_r")
        out["vs_backtest"] = {"expected_mean_r": exp, "expected_ci": expectation.get("oos_ci"),
                              "expected_n": expectation.get("oos_n"),
                              "diff_r": None if out["mean_r"] is None or exp is None else round(out["mean_r"] - exp, 4),
                              "enough_trades": len(counted) >= 30}
    return out
