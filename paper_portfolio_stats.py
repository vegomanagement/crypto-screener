"""Read-only statistics of a portfolio paper strategy: forward equity vs BTC buy-and-hold from the
same start close and vs the backtest expectation band (development drift ± 1.96 σ √days)."""
from __future__ import annotations

import json
import math

NOTE = ("Paper results of a daily portfolio strategy — not a signal, no orders. A few weeks say almost "
        "nothing: the development Sharpe CI of these finalists spans ~0.3…2.0, and neither passed the "
        "pre-registered gates. The band is the backtest drift ± 1.96 σ √days, not a forecast.")


def summarize(days, positions, expectation=None):
    exp = expectation or {}
    out = {"n_days": max(0, len(days) - 1), "note": NOTE, "curve": [], "positions": {}, "expectation": exp or None}
    if not days:
        return out
    b0 = days[0]["btc_close"]
    mu, sd = exp.get("mu_daily"), exp.get("sd_daily")
    peak, dd = 1.0, 0.0
    for k, d in enumerate(days):
        peak = max(peak, d["equity"])
        dd = max(dd, 1 - d["equity"] / peak)
        pt = {"ts": d["day_ts"], "equity": d["equity"], "ret_pct": 100 * (d["equity"] - 1),
              "btc_pct": None if not (b0 and d["btc_close"]) else 100 * (d["btc_close"] / b0 - 1),
              "late": d["late"]}
        if mu is not None and sd is not None:
            pt["band_lo_pct"] = 100 * (math.exp(k * mu - 1.96 * sd * math.sqrt(k)) - 1)
            pt["band_mid_pct"] = 100 * (math.exp(k * mu) - 1)
            pt["band_hi_pct"] = 100 * (math.exp(k * mu + 1.96 * sd * math.sqrt(k)) - 1)
        out["curve"].append(pt)
    rets = [d["ret"] for d in days[1:]]
    last = out["curve"][-1]
    out.update(total_return_pct=last["ret_pct"], btc_return_pct=last["btc_pct"], max_dd_pct=100 * dd,
               fees_pct=100 * sum(d["fees"] for d in days), funding_pct=100 * sum(d["funding"] for d in days),
               turnover=sum(d["turnover"] for d in days), late_days=sum(d["late"] for d in days),
               start_ts=days[0]["day_ts"], last_ts=days[-1]["day_ts"],
               in_band=None if "band_lo_pct" not in last else last["band_lo_pct"] <= last["ret_pct"] <= last["band_hi_pct"])
    if len(rets) >= 2:
        m = sum(rets) / len(rets)
        sdv = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))
        out["vol_ann_pct"] = 100 * sdv * math.sqrt(365)
        out["sharpe_ann"] = None if sdv == 0 else m / sdv * math.sqrt(365)
    closed = [p for p in positions if p["exit_ts"] is not None]
    opened = [p for p in positions if p["exit_ts"] is None]
    by = {}
    for p in closed:
        b = by.setdefault(p["symbol"], {"n": 0, "pnl_pct": 0.0, "wins": 0})
        b["n"] += 1
        b["pnl_pct"] += 100 * p["pnl"]
        b["wins"] += p["pnl"] > 0
    out["positions"] = {"open": len(opened), "closed": len(closed),
                        "hit": (sum(p["pnl"] > 0 for p in closed) / len(closed)) if closed else None,
                        "per_coin": by}
    last_orders = next((d["orders_json"] for d in reversed(days) if d["orders_json"]), None)
    out["next_orders"] = json.loads(last_orders) if last_orders else None
    return out
