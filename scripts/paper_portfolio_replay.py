#!/usr/bin/env python3
"""Replay check for the portfolio paper strategies on REAL data (no network).

    python scripts/paper_portfolio_replay.py <paper_journal.db> [YYYY-MM-DD start]

Reads the daily klines and funding the journal itself cached (paper_klines / paper_funding),
replays the journal once per daily close from the start day into an in-memory store, and compares
every recorded day with one portfolio_engine run over the same window (exact equality expected).
Also reports the full-history engine run on these native 1d klines next to the research cache
(1h-aggregated bars) when that cache exists, to show the data-source difference.
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import paper_portfolio as pp  # noqa: E402
import paper_portfolio_strategies as pps  # noqa: E402
import paper_strategies as ps  # noqa: E402
import portfolio_engine as pe  # noqa: E402
from paper_store import PaperStore  # noqa: E402

DAY = pe.DAY


def main():
    src = PaperStore(sys.argv[1])
    start = int(datetime.fromisoformat(sys.argv[2] if len(sys.argv) > 2 else "2026-01-01").replace(tzinfo=UTC)
                .timestamp() * 1000)
    pps.register_all()
    syms = ps.DEFAULT_UNIVERSE
    bars = {s: [{k: b[k] for k in ("ts", "o", "h", "l", "c", "v")} for b in src.klines(s, "D")
                if b["ts"] >= pps.HISTORY_START] for s in syms}
    fund = {s: src.funding_rows(s, pps.HISTORY_START - 1, 1 << 62) for s in syms}
    last = min(bars[s][-1]["ts"] for s in syms)
    out = {"start": start, "last_day": last, "strategies": {}}
    for sid in (pps.A_ID, pps.D_ID):
        strat = ps.REGISTRY[sid]
        mem = PaperStore()
        v = mem.ensure_version(strat)
        days = [d for d in sorted({b["ts"] for b in bars["BTCUSDT"]}) if start <= d <= last]
        for d in days:
            pp.observe(mem, strat, v, bars, fund, d + DAY + 15 * 60_000)
        rec = mem.pf_days(sid, v)
        mkt = pp.build_market(bars, fund, last)
        res, _ = pp.simulate(strat, mkt, start)
        same = [r["equity"] for r in rec] == res["equity"] and [r["day_ts"] for r in rec] == res["ts"]
        pos = mem.pf_positions(sid, v)
        btc = rec[-1]["btc_close"] / rec[0]["btc_close"] - 1
        full = pe.run(mkt, strat["fn"](mkt, dict(strat["params"], warmup="none"), 0), pe.Config())
        out["strategies"][sid] = {"days": len(rec), "exact_equal": same, "positions": len(pos),
                                  "closed": sum(p["exit_ts"] is not None for p in pos),
                                  "forward_return": rec[-1]["equity"] - 1, "btc_return": btc,
                                  "native_full_history_final": full["equity"][-1]}
    cache = Path.home() / ".hermes/cache/scratch/strategy-research/bars.json.gz"
    if cache.exists():
        with gzip.open(cache, "rt") as fh:
            blob = json.load(fh)
        m2 = pe.Market({s: blob["daily"][s] for s in syms}, {s: blob["funding"][s] for s in syms}, end_ms=last + DAY)
        m2.last = {s: i for s, i in m2.last.items() if i < len(m2.days) - 1}
        for sid in (pps.A_ID, pps.D_ID):
            strat = ps.REGISTRY[sid]
            r2 = pe.run(m2, strat["fn"](m2, dict(strat["params"], warmup="none"), 0), pe.Config())
            out["strategies"][sid]["research_cache_full_history_final"] = r2["equity"][-1]
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
