"""Portfolio finalists of docs/STRATEGY_RESEARCH_RESULT.md as paper-journal strategies (kind='portfolio').

Frozen exactly as ``docs/evidence/strategy-research/dev_selection.json`` (frozen 2026-10-18):
  A  ``A:ema50|long|k5``   trend: close > EMA50 (armed) → long at the next open, chandelier trail
                           highest high − 5 × ATR20 (resting stop), exit on close < EMA50; size
                           1 % equity risk / (5 ATR / close), ≤ 0.30 per coin, gross ≤ 2.0.
  D  ``D:ts|W90|H7|trend`` funding fade: daily funding z (W = 90 days) > +2 → short, < −2 → long,
                           only with the EMA50 trend (long above, short below), hold 7 days;
                           weight min(0.25, 0.005 / σ20), gross ≤ 1.5.
Same 20-coin universe, engine costs (taker 0.05 % + 0.01 % slippage, maker 0.02 %, real funding)
and history start (2020-01-01: indicators and funding z see the same history as the backtest).

Warm-up rule (documented in docs/PAPER_JOURNAL_RESULT.md): the journal STARTS FLAT at its first
daily close. A: every coin starts disarmed, so a long needs a fresh signal — a close ≤ EMA50
(re-arm) followed by a close > EMA50 — and trends already running at the start are not bought.
D: its signals are one-day funding events, so the first close's events are new and tradable.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime

import portfolio_engine as pe
import portfolio_strategies as pst
from paper_strategies import DEFAULT_UNIVERSE, register

HISTORY_START = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)
COSTS = {"taker": 0.0005, "slip": 0.0001, "maker": 0.0002, "fund_fallback": 0.0001}
A_ID, D_ID = "pf_trend_ema50_long_k5", "pf_funding_ts_w90_h7_trend"
SOURCE = "docs/STRATEGY_RESEARCH_RESULT.md + docs/evidence/strategy-research/{dev_selection,holdout}.json"


def trend_a(mkt, params, start_i):
    ind = pst.Ind(mkt)
    s = pst.Trend(mkt, ind, params["entry"], params["n"], params["ls"], params["k"], risk=params["risk"],
                  cap=params["cap"], gross=params["gross"])
    if params.get("warmup") == "flat_disarmed":
        for st in s.st.values():
            st["arm"] = {1: False, -1: False}
    return s


def funding_d(mkt, params, start_i):
    ind = pst.Ind(mkt)
    z = pst.funding_z(mkt, params["W"])
    return pst.FundingTS(mkt, ind, z, params["H"], params["trend"], thr=params["thr"], gross=params["gross"])


def band(cagr, vol):
    """Daily log drift / sd from the development CAGR and annualised vol (for the ±1.96σ band)."""
    return {"mu_daily": math.log1p(cagr) / 365, "sd_daily": vol / math.sqrt(365)}


A_PARAMS = {"source_id": "A:ema50|long|k5", "entry": "ema", "n": 50, "ls": False, "k": 5.0, "risk": 0.01,
            "cap": 0.30, "gross": 2.0, "warmup": "flat_disarmed", "costs": COSTS, "history_start": HISTORY_START,
            "universe": list(DEFAULT_UNIVERSE)}
D_PARAMS = {"source_id": "D:ts|W90|H7|trend", "W": 90, "H": 7, "trend": True, "thr": 2.0, "gross": 1.5,
            "warmup": "flat", "costs": COSTS, "history_start": HISTORY_START, "universe": list(DEFAULT_UNIVERSE)}
A_EXPECT = {"dev": {"cagr": 0.4069, "vol": 0.3337, "sharpe": 1.192, "max_dd": 0.3847},
            "holdout_2026": {"total_return": 0.2169, "cagr": 0.2802, "sharpe": 0.918, "max_dd": 0.1567},
            **band(0.4069, 0.3337), "source": SOURCE,
            "caveat": "no finalist passed the pre-registered gates (DSR 0.51; did not beat same-exposure random "
                      "timing)"}
D_EXPECT = {"dev": {"cagr": 0.1043, "vol": 0.1056, "sharpe": 0.991, "max_dd": 0.0946},
            "holdout_2026": {"total_return": 0.118, "cagr": 0.1508, "sharpe": 1.291, "max_dd": 0.0398},
            **band(0.1043, 0.1056), "source": SOURCE,
            "caveat": "no finalist passed the pre-registered gates (DSR 0.32; ZEC 38 % of P&L)"}
CODE = (pe, pst.Ind, pst.Trend, pst.FundingTS, pst.funding_z, pst.daily_funding, pst._gross_scale,
        pst._allow_all, band)


def register_all(env=None):
    register(A_ID, trend_a, A_PARAMS, kind="portfolio", title="Trend A · EMA50 long-only · 5-ATR chandelier · vol-sized",
             timeframes=("D",), lifecycle_tf="D", code=CODE, expectation=A_EXPECT,
             description="Research finalist A (daily, 20 coins): decisions at the UTC daily close, market fills at "
                         "the next open, trailing stop resting intraday. Starts flat; needs a fresh EMA50 cross.")
    register(D_ID, funding_d, D_PARAMS, kind="portfolio",
             title="Funding contrarian D · z(90d) > 2 fade · EMA50 trend filter · hold 7d",
             timeframes=("D",), lifecycle_tf="D", code=CODE, expectation=D_EXPECT,
             description="Research finalist D (daily, 20 coins): fade a funding z-score extreme in the direction of "
                         "the EMA50 trend, hold 7 days, vol-sized. Starts flat.")
