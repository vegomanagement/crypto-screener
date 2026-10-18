"""Paper journal for PORTFOLIO strategies (daily, multi-coin): kind = 'portfolio'.

A portfolio strategy plugs in as ``fn(market, params, start_i) -> strategy`` (an object with
``decide(i, book)`` exactly as in portfolio_strategies.py). Each cycle:

  1. the as-of day = the last UTC day whose 1d bar is CLOSED for every active coin, at least
     ``SETTLE_MS`` after its close (so the 00:00 funding print is published); bars and funding
     after that close are cut off here, whatever the loader returned (no look-ahead by design);
  2. the first cycle fixes ``start_day`` = that day: the strategy starts FLAT there (warm-up
     rule: no position that already existed is bought; trend A additionally needs a fresh EMA
     cross per coin — see paper_portfolio_strategies.py);
  3. portfolio_engine.run replays the strategy from start_day to the as-of day on the closed data
     (deterministic, so the past is recomputed identically) with ``decide_last`` → the orders for
     the next open are what the strategy decided at this close;
  4. new days are appended (equity relative to 1.0 at start_day, fees, funding, turnover, gross,
     BTC close, the next-open orders for the as-of day), never rewritten: a recomputed past day
     that differs (venue revised a bar or a funding print) is logged as a 'divergence' event;
     days first recorded more than a day after their close are flagged ``late`` (server down);
  5. positions: one row per engine trade (entry at the next open after the decision, exit by
     stop / opposite signal / time / delisting), open rows are marked to market every cycle.
Costs are the engine's: taker 0.05 % + 0.01 % slippage, maker 0.02 %, real Binance funding.
"""
from __future__ import annotations

import json
import logging
import time

import portfolio_engine as pe

log = logging.getLogger(__name__)
DAY = pe.DAY
SETTLE_MS = 10 * 60_000             # wait 10 min after the daily close before using that day
STALE_DAYS = 3                      # a coin without bars for > 3 days no longer holds the cycle back
FINAL = ("exit_ts", "exit_px", "why", "pnl", "fees", "funding", "updated_ts")


def as_of_day(bars_by_sym, now_ms):
    """Open time of the last day closed for every active coin (or None)."""
    last = {s: max((b["ts"] for b in bars if b["ts"] + DAY + SETTLE_MS <= now_ms), default=None)
            for s, bars in bars_by_sym.items()}
    last = {s: t for s, t in last.items() if t is not None}
    if not last:
        return None
    top = max(last.values())
    return min(t for t in last.values() if t >= top - STALE_DAYS * DAY)


def build_market(bars_by_sym, funding_by_sym, day):
    """Market of the days ≤ ``day`` (open time) with funding ≤ its close. The last day of the window
    is not a delisting (the engine would close every coin at the end of a truncated window)."""
    end = day + DAY
    mkt = pe.Market(bars_by_sym, funding_by_sym, end_ms=end)
    if mkt.days:
        final = len(mkt.days) - 1
        mkt.last = {s: i for s, i in mkt.last.items() if i < final}
    return mkt


def simulate(strat, mkt, start_day):
    start_i = mkt.days.index(start_day)
    obj = strat["fn"](mkt, strat["params"], start_i)
    cfg = pe.Config(**strat["params"].get("costs", {}))
    return pe.run(mkt, obj, cfg, start_i=start_i, decide_last=True), start_i


def _orders_json(nxt):
    keep = ("w", "type", "price", "stop", "tag")
    orders = {s: {k: o.get(k) for k in keep if o.get(k) is not None} for s, o in sorted(nxt["orders"].items())}
    stops = {s: st for s, st in sorted(nxt["stops"].items()) if nxt["qty"].get(s)}
    return json.dumps({"orders": orders, "stops": stops}, sort_keys=True)


def observe(store, strat, version, bars_by_sym, funding_by_sym, now_ms, btc="BTCUSDT"):
    """One journal cycle for a portfolio strategy. Returns a summary dict."""
    sid = strat["id"]
    day = as_of_day(bars_by_sym, now_ms)
    out = {"as_of": day, "new_days": 0, "new_positions": 0, "closed": 0, "divergent": 0}
    if day is None:
        out["skipped"] = "no closed daily bars"
        return out
    st = store.pf_state(sid, version)
    if st is None:
        store.pf_start(sid, version, day, now_ms)
        st = store.pf_state(sid, version)
        out["warmup"] = "started flat at this close"
    elif day < st["last_day"]:
        out["skipped"] = "data older than the last cycle"
        return out
    known = {r["day_ts"]: r for r in store.pf_days(sid, version)}
    if day in known and day == st["last_day"]:
        out["skipped"] = "no new daily close"
        return out
    cut = {s: [b for b in bars if b["ts"] <= day] for s, bars in bars_by_sym.items()}
    fund = {s: (None if rows is None else [r for r in rows if r["time"] <= day + DAY])
            for s, rows in funding_by_sym.items()}
    mkt = build_market(cut, fund, day)
    if st["start_day"] not in mkt.days:
        out["skipped"] = "start day missing from the data"
        return out
    res, start_i = simulate(strat, mkt, st["start_day"])
    for k, ts in enumerate(res["ts"]):
        i = start_i + k
        row = {"strategy_id": sid, "version": version, "day_ts": ts, "equity": res["equity"][k], "ret": res["ret"][k],
               "fees": res["fees"][k], "funding": res["funding"][k], "turnover": res["turnover"][k],
               "gross": res["gross"][k], "n_pos": sum(1 for t in res["trades"] if t["entry_i"] <= i
                                                     and (t["exit_i"] is None or t["exit_i"] > i)),
               "btc_close": mkt.c[btc][i] if btc in mkt.c else None,
               "orders_json": _orders_json(res["next"]) if ts == day else None,
               "recorded_ts": now_ms, "late": int(now_ms > ts + 2 * DAY + SETTLE_MS)}
        old = known.get(ts)
        if old is None:
            out["new_days"] += store.pf_insert_day(row)
        elif abs(old["equity"] - row["equity"]) > 1e-9:
            store.add_event(sid, version, "*", "divergence", f"day:{ts}", ts,
                            {"stored": old["equity"], "recomputed": row["equity"]}, now_ms)
            out["divergent"] += 1
    eq = res["equity"]
    for t in res["trades"]:
        k0 = t["entry_i"] - start_i
        e_prev = eq[k0 - 1] if k0 > 0 else 1.0
        row = {"strategy_id": sid, "version": version, "symbol": t["sym"], "side": "long" if t["side"] > 0 else "short",
               "entry_ts": mkt.days[t["entry_i"]], "entry_px": t["entry_px"], "weight": t["notional"] / e_prev,
               "notional": t["notional"], "risk": t.get("risk"), "tag": t.get("tag") or t.get("kind"),
               "exit_ts": None if t["exit_i"] is None else mkt.days[t["exit_i"]],
               "exit_px": t["exit_px"] if t["exit_i"] is not None else None, "why": t["why"], "pnl": t["pnl"],
               "fees": t["fees"], "funding": t["funding"], "recorded_ts": now_ms, "updated_ts": now_ms}
        r = store.pf_upsert_position(row, FINAL)
        out["new_positions"] += r == "new"
        out["closed"] += r == "updated" and row["exit_ts"] is not None
    store.pf_advance(sid, version, day, now_ms)
    return out


def run_cycle(store, strat, load_candles, load_funding, reason="interval", clock=time.time):
    """``load_candles(sym, ['D'])`` → {'D': closed bars}; ``load_funding(sym, a, b)`` → [{time, rate}]."""
    t0 = clock()
    version = store.ensure_version(strat)
    syms = strat["params"]["universe"]
    bars, fund, errors = {}, {}, {}
    start = strat["params"]["history_start"]
    now = int(clock() * 1000)
    for s in syms:
        try:
            bars[s] = [{k: b[k] for k in ("ts", "o", "h", "l", "c", "v")}
                       for b in load_candles(s, ["D"])["D"] if b["ts"] >= start]
            fund[s] = load_funding(s, start - 1, now)
        except Exception as exc:  # noqa: BLE001 — a missing coin must hold the cycle, not crash it
            errors[s] = f"{type(exc).__name__}: {exc}"[:200]
    res = {"skipped": "fetch errors: the portfolio needs every coin"} if errors else \
        observe(store, strat, version, bars, fund, now)
    store.log_run(int(t0 * 1000), strat["id"], version, reason, len(syms), len(syms) - len(errors), errors,
                  int((clock() - t0) * 1000))
    return {"version": version, "results": [res], "errors": errors}


def replay(store, strat, version, bars_by_sym, funding_by_sym, start_day, delay_ms=15 * 60_000):
    """Run the journal once per daily close from ``start_day`` (simulated clock = close + delay)."""
    days = sorted({b["ts"] for bars in bars_by_sym.values() for b in bars if b["ts"] >= start_day})
    for d in days:
        observe(store, strat, version, bars_by_sym, funding_by_sym, d + DAY + delay_ms)
