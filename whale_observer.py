"""Paper-journal 'observer' strategy: hypothetical follow trades after Hyperliquid whale events.

Registered only when WHALE_RECORDER=1 (it reads the recorder's events). NO EDGE IS CLAIMED: this
only collects forward data for the pre-registered test in docs/WHALE_RECORDER.md.

Rules (frozen in PARAMS, part of the strategy version hash):
  * events: kind 'open' or 'flip' (new directional exposure) recorded by whale_recorder.py with
    knowable_ts ≥ the observer's first cycle (older events are warm-up, never followed);
  * wallets: the top-K (K = 10) of the tracked accounts by trailing 30-day PnL in the last
    leaderboard snapshot fetched at or before the event's knowable_ts;
  * de-duplication: at most one followed event per (wallet, coin) per 24 h — a later event within
    24 h of the last followed one is skipped (knowable at the time; stops one fast-flipping wallet
    from filling the sample: on the first live run one wallet flipped 65 times in 37 minutes);
  * entry: the OPEN of the first 1-minute Hyperliquid candle starting after knowable_ts, same
    side as the whale's new position; one unit of notional; exits at the open of the 1-minute
    candle exactly 1, 3 and 7 days after the entry (three rows per event);
  * costs: Hyperliquid taker 0.045 % per side, hourly funding over the holding window
    (fundingHistory; longs pay positive rates).
Prices come from candleSnapshot (1m keeps ~5000 candles ≈ 3.5 days: a row not priced in time
becomes 'no_price' with the reason, never filled with a guess).
"""
from __future__ import annotations

import os
import time

from paper_strategies import REGISTRY, register

OBS_ID = "obs_hl_whale_follow_top10_30d"
PARAMS = {"events": ["open", "flip"], "top_k": 10, "rank_by": "pnl_month", "horizons_d": [1, 3, 7], "dedupe_h": 24,
          "entry": "next_1m_open", "taker": 0.00045, "funding": "hyperliquid_hourly", "tracked": "top50_account_value"}
MIN, DAY = 60_000, 86_400_000
MAX_REQUESTS = 40                  # Hyperliquid info requests per cycle (paced ≤ 1/s by HttpClient)
SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_follow (
    id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_id TEXT NOT NULL, version INTEGER NOT NULL,
    event_id INTEGER NOT NULL, address TEXT NOT NULL, coin TEXT NOT NULL, side TEXT NOT NULL,
    event_kind TEXT NOT NULL, knowable_ts INTEGER NOT NULL, wallet_rank INTEGER, wallet_pnl_month TEXT,
    horizon_d INTEGER NOT NULL, entry_ts INTEGER NOT NULL, exit_ts INTEGER NOT NULL, entry_px REAL, exit_px REAL,
    fee REAL, funding REAL, ret_gross REAL, ret_net REAL, state TEXT NOT NULL, note TEXT,
    recorded_ts INTEGER NOT NULL, updated_ts INTEGER NOT NULL,
    UNIQUE (strategy_id, version, event_id, horizon_d));
"""


def entry_minute(knowable_ts):
    return (knowable_ts // MIN + 1) * MIN


def top_wallets(rows, k):
    """{address: (rank, pnl)} of the k rows with the highest trailing-30-day PnL."""
    def pnl(r):
        try:
            return float(r["pnl_month"])
        except (TypeError, ValueError):
            return float("-inf")
    ranked = sorted((r for r in rows if r.get("pnl_month") is not None), key=lambda r: (-pnl(r), r["address"]))[:k]
    return {r["address"]: (i + 1, r["pnl_month"]) for i, r in enumerate(ranked)}


class HLPrices:
    """1m candle opens and hourly funding from the public info endpoint (bounded per cycle)."""

    def __init__(self, http=None, budget=MAX_REQUESTS):
        if http is None:
            from positioning_data import HttpClient
            http = HttpClient()
        self.http, self.budget, self._c = http, budget, {}

    def _req(self, body):
        if self.budget <= 0:
            raise RuntimeError("request budget for this cycle used up")
        self.budget -= 1
        return self.http.request_json("https://api.hyperliquid.xyz/info", body=body)

    def open_at(self, coin, t):
        key = (coin, t // (60 * MIN))
        if key not in self._c:
            a = key[1] * 60 * MIN
            rows = self._req({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1m", "startTime": a,
                                                                "endTime": a + 60 * MIN - 1}})
            self._c[key] = {int(r["t"]): float(r["o"]) for r in rows or []}
        return self._c[key].get(t)

    def funding(self, coin, a, b):
        rows = self._req({"type": "fundingHistory", "coin": coin, "startTime": a + 1, "endTime": b})
        return sum(float(r["fundingRate"]) for r in rows or [] if a < int(r["time"]) <= b)


def cycle(store, strat, reason="interval", clock=time.time, whales=None, prices=None):
    from whale_store import WhaleStore, default_path
    now = int(clock() * 1000)
    for stmt in SCHEMA.strip().split(";"):
        if stmt.strip():
            store.x(stmt)
    version = store.ensure_version(strat)
    sid, p = strat["id"], strat["params"]
    st = store.symbol_state(sid, version, "*")
    if st is None:
        store.start_symbol(sid, version, "*", now, now)
        st = store.symbol_state(sid, version, "*")
    whales = whales or WhaleStore(default_path())
    prices = prices or HLPrices()
    done = {r["event_id"] for r in store.q("SELECT DISTINCT event_id FROM paper_follow WHERE strategy_id=? AND version=?",
                                           (sid, version))}
    last = {(r["address"], r["coin"]): r["t"] for r in store.q(
        "SELECT address, coin, MAX(knowable_ts) AS t FROM paper_follow WHERE strategy_id=? AND version=? "
        "GROUP BY address, coin", (sid, version))}
    new = 0
    for e in whales.events(since_ts=st["started_as_of"], kinds=p["events"]):
        if e["id"] in done or e["knowable_ts"] > now:
            continue
        prev = last.get((e["address"], e["coin"]))
        if prev is not None and e["knowable_ts"] - prev < p.get("dedupe_h", 0) * 3_600_000:
            continue
        top = top_wallets(whales.leaderboard_at(e["knowable_ts"]), p["top_k"])
        if e["address"] not in top:
            continue
        last[(e["address"], e["coin"])] = e["knowable_ts"]
        rank, pnl = top[e["address"]]
        t0 = entry_minute(e["knowable_ts"])
        for h in p["horizons_d"]:
            store.x("INSERT OR IGNORE INTO paper_follow (strategy_id, version, event_id, address, coin, side, event_kind, "
                    "knowable_ts, wallet_rank, wallet_pnl_month, horizon_d, entry_ts, exit_ts, state, recorded_ts, "
                    "updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, version, e["id"], e["address"], e["coin"], e["side"], e["kind"], e["knowable_ts"], rank, pnl,
                     h, t0, t0 + h * DAY, "waiting_entry", now, now))
            new += 1
    errors = {}
    rows = store.q("SELECT * FROM paper_follow WHERE strategy_id=? AND version=? AND state IN ('waiting_entry','open') "
                   "ORDER BY entry_ts, id", (sid, version))
    for r in rows:
        try:
            _advance(store, r, prices, p, now)
        except RuntimeError as exc:                      # budget used up: continue next cycle
            errors["budget"] = str(exc)
            break
        except Exception as exc:  # noqa: BLE001 — one coin's failure must not stop the others
            errors[r["coin"]] = f"{type(exc).__name__}: {str(exc)[:120]}"
    store.advance_symbol(sid, version, "*", now, now)
    store.log_run(now, sid, version, reason, 1, 1, errors, int(clock() * 1000) - now)
    return {"version": version, "results": [{"new": new}], "errors": errors}


def _advance(store, r, prices, p, now):
    sets = {}
    if r["state"] == "waiting_entry" and now >= r["entry_ts"] + MIN:
        px = prices.open_at(r["coin"], r["entry_ts"])
        sets = {"entry_px": px, "state": "open"} if px else \
            ({"state": "no_price", "note": "1m candle not served (older than ~3.5 days?)"}
             if now - r["entry_ts"] > 3 * DAY else {})
    elif r["state"] == "open" and now >= r["exit_ts"] + MIN:
        px = prices.open_at(r["coin"], r["exit_ts"])
        if px:
            d = 1 if r["side"] == "long" else -1
            gross = d * (px / r["entry_px"] - 1)
            fund = -d * prices.funding(r["coin"], r["entry_ts"], r["exit_ts"])
            fee = 2 * p["taker"]
            sets = {"exit_px": px, "ret_gross": gross, "fee": fee, "funding": fund, "ret_net": gross - fee + fund,
                    "state": "closed"}
        elif now - r["exit_ts"] > 3 * DAY:
            sets = {"state": "no_price", "note": "exit 1m candle not served"}
    if sets:
        store.x(f"UPDATE paper_follow SET {', '.join(k + '=?' for k in sets)}, updated_ts=? WHERE id=?",
                list(sets.values()) + [now, r["id"]])


def register_all(env=None):
    env = env if env is not None else os.environ
    from whale_recorder import enabled
    if not enabled(env):
        REGISTRY.pop(OBS_ID, None)
        return
    register(OBS_ID, cycle, PARAMS, kind="observer", cycle=cycle, title="Observer · follow HL top-10 (30d PnL) whale opens/flips · 1/3/7 d",
             timeframes=(), lifecycle_tf="1m", code=(_advance, top_wallets, entry_minute, HLPrices),
             expectation={"claim": "none — forward data collection; test pre-registered in docs/WHALE_RECORDER.md"},
             description="Hypothetical: enter at the next 1-minute open after a recorded whale open/flip, hold 1/3/7 "
                         "days, Hyperliquid taker fees and funding. No edge claimed.")


def summarize(rows):
    """Per-horizon counts and mean / median net return of closed rows (descriptive only)."""
    out = {}
    for h in sorted({r["horizon_d"] for r in rows}):
        rs = [r for r in rows if r["horizon_d"] == h]
        net = sorted(r["ret_net"] for r in rs if r["state"] == "closed" and r["ret_net"] is not None)
        out[str(h)] = {"rows": len(rs), "closed": len(net), "open": sum(r["state"] == "open" for r in rs),
                       "waiting": sum(r["state"] == "waiting_entry" for r in rs),
                       "no_price": sum(r["state"] == "no_price" for r in rs),
                       "mean_net": (sum(net) / len(net)) if net else None,
                       "median_net": net[len(net) // 2] if net else None,
                       "hit": (sum(x > 0 for x in net) / len(net)) if net else None}
    return out
