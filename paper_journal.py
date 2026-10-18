"""Paper journal (forward test) engine: record each live setup of a registered strategy at the moment
it becomes knowable, then follow it on later CLOSED bars with the backtest's own rules.

One cycle per (strategy version, symbol) = ``observe``: call the strategy (the backtest scanner)
on every closed candle up to the last closed entry-TF bar (``as_of``) and reconcile with the store:
  * first cycle (warm-up): every resting/open setup is stored with ``seen_at_start = 1`` and is
    excluded from the stats; trades closed before the start are never stored (no backfill);
  * later cycles: a new resting setup → a 'pending' row with knowable_ts (the bar close at which it
    became knowable), recorded_ts (wall clock) and the planned entry/stop/target + inputs (frozen);
    ``late = 1`` when it was first observed after a later bar close than knowable_ts (server down);
  * a pending row whose setup the strategy reports filled → 'filled' → 'be' (stop moved to entry)
    → 'target' / 'stop' / 'breakeven' / 'timeout'; a pending row that is no longer a live order and
    did not fill → 'expired'. A fill whose setup was never recorded before the fill (it appeared and
    filled while the journal was not running) is NOT recorded as a trade: it goes to paper_events as
    'missed_fill', so nothing is ever recorded retroactively.
Every lifecycle decision is the strategy's own (same bars, same intrabar rule: stop first). Costs
are charged here, exactly as scripts/backtest_swing_universe.py: maker 0.02 % for limit fills and
target exits, taker 0.05 % + 0.01 % slippage for stop / break-even / timeout exits, plus the real
funding from /fapi/v1/fundingRate over the holding time (longs pay positive rates).
"""
from __future__ import annotations

import json
import logging
import time

from paper_store import CLOSED_STATES, OPEN_STATES, now_ms

log = logging.getLogger(__name__)

TAKER, MAKER, SLIP = 0.0005, 0.0002, 0.0001
EXPIRE_REASON = ("no longer a live order at this bar close: zone mitigated / invalidated, HTF trend or "
                 "discount-premium changed, or the coin is in another trade")


class FundingUnavailable(RuntimeError):
    pass


def slice_closed(candles_by_tf, t):
    """Only candles closed by ``t`` (the no-look-ahead cut used by the replay)."""
    return {tf: [c for c in bars if c["close_ts"] <= t] for tf, bars in candles_by_tf.items()}


def costs(trade, funding_rates):
    """(fee_R, funding_R) of a closed trade; ``funding_rates(a, b)`` → [rate, ...] with a < time ≤ b."""
    d = 1 if trade["side"] == "long" else -1
    risk = d * (trade["entry"] - trade["stop"])
    unit = trade["entry"] / risk
    ent = MAKER if trade["entry_type"] == "maker" else TAKER + SLIP
    ex = MAKER if trade["exit_reason"] == "target" else TAKER + SLIP
    if trade.get("partial"):
        ex = 0.5 * MAKER + 0.5 * ex
    a, b = trade["fill_ts"], trade["exit_ts"]
    if trade.get("partial") and trade.get("partial_ts"):
        f = sum(funding_rates(a, trade["partial_ts"])) + 0.5 * sum(funding_rates(trade["partial_ts"], b))
    else:
        f = sum(funding_rates(a, b))
    return unit * (ent + ex), -d * unit * f


def _state_of(t):
    if t["status"] == "open":
        return "be" if t.get("be_ts") else "filled"
    if t["exit_reason"] == "stop" and t.get("be_ts") and abs(t["r"]) < 1e-9:
        return "breakeven"
    return t["exit_reason"]


def _trade_fields(t):
    f = {"state": _state_of(t), "fill_ts": t["fill_ts"], "fill_close_ts": t.get("fill_close_ts"),
         "entry": t["entry"], "stop": t["stop"], "target": t["target"], "be_ts": t.get("be_ts")}
    d = 1 if t["side"] == "long" else -1
    f["risk_pct"] = round(100 * d * (t["entry"] - t["stop"]) / t["entry"], 6)
    if t["status"] == "closed":
        f.update(exit_ts=t["exit_ts"], exit_reason=t["exit_reason"], r_gross=t["r"], partial=int(bool(t.get("partial"))),
                 partial_ts=t.get("partial_ts"))
    return f


def _apply_costs(store, row_id, t, symbol, funding):
    if funding is None:
        store.update_setup(row_id, fund_status="no funding source")
        return
    try:
        fee, fund = costs(t, lambda a, b: funding(symbol, a, b))
    except FundingUnavailable as exc:
        store.update_setup(row_id, fund_status=f"pending: {exc}"[:200])
        return
    net = t["r"] - fee + fund
    d = 1 if t["side"] == "long" else -1
    unit = t["entry"] / (d * (t["entry"] - t["stop"]))
    store.update_setup(row_id, fee_r=round(fee, 6), fund_r=round(fund, 6), fund_status="ok",
                       net_r=round(net, 6), net_pct=round(100 * net / unit, 6))


def _new_row(strat_id, version, symbol, s, as_of, recorded_ts, seen_at_start):
    return {"strategy_id": strat_id, "version": version, "symbol": symbol, "key": s["key"], "side": s["side"],
            "state": "pending", "seen_at_start": int(seen_at_start), "late": int(s["knowable_ts"] < as_of),
            "knowable_ts": s["knowable_ts"], "recorded_ts": recorded_ts, "observed_as_of": as_of,
            "planned_entry": s["entry"], "planned_stop": s["stop"], "planned_target": s["target"],
            "entry_type": s.get("entry_type"), "inputs_json": json.dumps(s.get("inputs") or {}, default=str),
            "updated_ts": recorded_ts}


def observe(store, strat, version, symbol, candles_by_tf, recorded_ts=None, funding=None):
    """One journal cycle for one symbol on closed candles. Returns a small summary dict."""
    recorded_ts = recorded_ts or now_ms()
    ltf = candles_by_tf[strat["lifecycle_tf"]]
    if not ltf:
        return {"symbol": symbol, "skipped": "no closed bars"}
    as_of = ltf[-1]["close_ts"]
    sid = strat["id"]
    setups = strat["fn"](candles_by_tf, strat["params"])
    st = store.symbol_state(sid, version, symbol)
    out = {"symbol": symbol, "as_of": as_of, "new": 0, "filled": 0, "closed": 0, "expired": 0, "missed": 0}
    if st is None:                                                    # warm-up: tag, never count
        store.start_symbol(sid, version, symbol, as_of, recorded_ts)
        for s in setups:
            if s["status"] == "closed":
                continue
            ks = s.get("knowable_ts") or s.get("fill_close_ts") or as_of
            row = _new_row(sid, version, symbol, dict(s, knowable_ts=ks), as_of, recorded_ts, True)
            row["late"] = 0
            row_id = store.insert_setup(row)
            if row_id and s["status"] == "open":
                store.update_setup(row_id, **_trade_fields(s))
            out["new"] += 1
        out["warmup"] = True
        return out
    if as_of < st["last_as_of"]:
        out["skipped"] = "data older than the last cycle"
        return out
    started = st["started_as_of"]
    rows = store.setups(sid, version, symbol, states=("pending",) + OPEN_STATES)
    pending = {r["key"]: r for r in rows if r["state"] == "pending"}
    running = {(r["key"], r["fill_ts"]): r for r in rows if r["state"] in OPEN_STATES}
    done = {(r["key"], r["fill_ts"]) for r in store.setups(sid, version, symbol, states=CLOSED_STATES)}
    touched = set()
    for t in (s for s in setups if s["status"] in ("open", "closed")):
        row = running.get((t["key"], t["fill_ts"]))
        if row is None:
            if (t["key"], t["fill_ts"]) in done or t["fill_ts"] < started:
                continue                                              # already final / before the start
            p = pending.get(t["key"])
            if p is None or p["id"] in touched or p["observed_as_of"] > t["fill_ts"]:
                # filled before the journal had recorded the setup: never record it retroactively
                store.add_event(sid, version, symbol, "missed_fill", t["key"], t["fill_ts"],
                                {k: t.get(k) for k in ("side", "entry", "stop", "target", "exit_reason", "r")},
                                recorded_ts)
                out["missed"] += 1
                continue
            row = p
            out["filled"] += 1
        touched.add(row["id"])
        fields = _trade_fields(t)
        if row["state"] == "pending" or any(row.get(k) != v for k, v in fields.items()):
            store.update_setup(row["id"], updated_ts=recorded_ts, **fields)
            if t["status"] == "closed":
                out["closed"] += 1
                _apply_costs(store, row["id"], t, symbol, funding)
    resting = {s["key"]: s for s in setups if s["status"] == "resting"}
    for key, s in resting.items():
        p = pending.get(key)
        if p is not None and p["id"] not in touched:
            continue
        if store.insert_setup(_new_row(sid, version, symbol, s, as_of, recorded_ts, s["knowable_ts"] < started)):
            out["new"] += 1
    for key, p in pending.items():
        if p["id"] not in touched and key not in resting:
            store.update_setup(p["id"], state="expired", cancel_ts=as_of, cancel_reason=EXPIRE_REASON,
                               updated_ts=recorded_ts)
            out["expired"] += 1
    if funding is not None:                                           # retry funding that was unavailable
        for r in store.q("SELECT * FROM paper_setups WHERE strategy_id=? AND version=? AND symbol=? AND "
                         "net_r IS NULL AND exit_ts IS NOT NULL AND (fund_status IS NULL OR fund_status LIKE 'pending%')",
                         (sid, version, symbol)):
            _apply_costs(store, r["id"], {**r, "r": r["r_gross"]}, symbol, funding)
    store.advance_symbol(sid, version, symbol, as_of, recorded_ts)
    return out


def run_cycle(store, strat, symbols, load_candles, funding=None, reason="interval", clock=time.time):
    """``load_candles(symbol, timeframes)`` → closed candles by TF (raises on fetch errors)."""
    t0 = clock()
    version = store.ensure_version(strat)
    results, errors = [], {}
    for sym in symbols:
        try:
            results.append(observe(store, strat, version, sym, load_candles(sym, strat["timeframes"]),
                                   int(clock() * 1000), funding))
        except Exception as exc:  # noqa: BLE001 — one coin's failure must not stop the cycle
            log.warning("paper journal %s %s: %s", strat["id"], sym, exc)
            errors[sym] = f"{type(exc).__name__}: {exc}"[:200]
    store.log_run(int(t0 * 1000), strat["id"], version, reason, len(symbols), len(results), errors,
                  int((clock() - t0) * 1000))
    return {"version": version, "results": results, "errors": errors}


def replay(store, strat, version, symbol, candles_by_tf, start_i, funding=None, clock_ms=None):
    """Run the journal bar by bar from entry-TF bar ``start_i`` (warm-up) to the end, each cycle on
    the candles closed by that bar only. ``clock_ms(as_of)`` = the simulated wall clock."""
    ltf = candles_by_tf[strat["lifecycle_tf"]]
    for i in range(start_i, len(ltf)):
        t = ltf[i]["close_ts"]
        observe(store, strat, version, symbol, slice_closed(candles_by_tf, t),
                (clock_ms or (lambda x: x + 1000))(t), funding)
