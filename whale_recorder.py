"""Hyperliquid whale-position recorder (forward-test data; research only, no orders).

Off by default: ``WHALE_RECORDER=1`` starts ONE background thread per database (non-blocking
flock on ``<WHALES_DB_PATH>.lock``). Every ``WHALE_INTERVAL_MIN`` minutes (default 5) it records
the positions of the top-N Hyperliquid accounts tracked by hl_whale_tracker (top 50 by account
value from the public leaderboard). Rate-limit friendly:
  * it SHARES the positioning page's WhaleTracker: a poll younger than half an interval is reused,
    the poll itself is sequential at ≤ 1 request/s (positioning_data.HttpClient pacing);
  * the ~38 MB leaderboard is cached by the tracker (failed refresh → old list, retried at most every
    10 min, never re-downloaded per error); with the recorder on, its TTL is raised to
    ``WHALE_LEADERBOARD_TTL_MIN`` (default 360) so the tracked set stays stable and downloads rare.
Position-change events are derived from consecutive answers of the same address:
open / add / reduce / close / flip, knowable at the poll's finish time (when we could know it);
``prev_ts`` = the previous answer (the change happened in (prev_ts, knowable_ts]); ``gap`` = 1
when that window is longer than 3 intervals. An address seen for the first time is a baseline
(its existing positions are not events).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from decimal import Decimal

from whale_store import WhaleStore, default_path

log = logging.getLogger(__name__)
_STARTED: dict = {}
_LOCK = threading.Lock()
ZERO = Decimal(0)


def enabled(env=None):
    return (env if env is not None else os.environ).get("WHALE_RECORDER", "").strip() in ("1", "true", "yes", "on")


def _dec(v):
    return ZERO if v in (None, "") else Decimal(str(v))


def classify(p, c):
    """Event kind for a size change p → c (signed szi), or None when unchanged."""
    if p == c:
        return None
    if p == 0:
        return "open"
    if c == 0:
        return "close"
    if (p > 0) != (c > 0):
        return "flip"
    return "add" if abs(c) > abs(p) else "reduce"


def derive_events(address, before, after, prev_ts, ts, gap_ms):
    """``before`` (stored state rows or positions) vs ``after`` ({coin: position}) of one address."""
    out = []
    for coin in sorted(set(before) | set(after)):
        p, c = _dec((before.get(coin) or {}).get("szi")), _dec((after.get(coin) or {}).get("szi"))
        kind = classify(p, c)
        if kind is None:
            continue
        ref = after.get(coin) or before.get(coin) or {}
        value, szi = ref.get("value"), _dec(ref.get("szi"))
        unit = (_dec(value) / abs(szi)) if value not in (None, "") and szi else None
        out.append({"address": address, "coin": coin, "kind": kind, "side": "long" if (c or p) > 0 else "short",
                    "from_szi": str(p), "to_szi": str(c),
                    "entry_px": None if (after.get(coin) or {}).get("entry") is None else str(after[coin]["entry"]),
                    "notional": None if unit is None else str((abs(c - p) * unit).quantize(Decimal("0.01"))),
                    "prev_ts": prev_ts, "knowable_ts": ts, "gap": ts - prev_ts > gap_ms})
    return out


def leaderboard_rows(lb):
    rows = []
    for rank, r in enumerate(lb.get("rows") or [], 1):
        pnl = r.get("pnl") or {}
        rows.append((int(lb["fetched_at"] * 1000), rank, r["address"], str(r["account_value"]),
                     *(None if pnl.get(k) is None else str(pnl[k]) for k in ("day", "week", "month", "allTime")),
                     r.get("display_name")))
    return rows


def record(store, ts, poll, states, lb, *, interval_ms, full_every_ms, recorded_ts):
    """Persist one poll (idempotent on ``ts``). ``states`` = {address: {coin: position}} of the
    addresses that answered. Returns a summary."""
    if store.has_poll(ts):
        return {"skipped": "already recorded", "ts": ts}
    last_full = store.last_full_ts()
    full = int(last_full is None or ts - last_full >= full_every_ms)
    rows, events = [], []
    for address in sorted(states):
        after = states[address]
        before = store.state(address)
        seen = store.seen(address)
        if seen is not None:
            events += derive_events(address, before, after, seen["last_ts"], ts, 3 * interval_ms)
        for coin in sorted(set(before) | set(after)):
            p = after.get(coin)
            changed = _dec((before.get(coin) or {}).get("szi")) != _dec((p or {}).get("szi"))
            if p is None and changed:
                rows.append((ts, address, coin, "0", None, None, None, None, None, full))
            elif p is not None and (full or changed):
                rows.append((ts, address, coin, str(p["szi"]), *(None if p.get(k) is None else str(p[k])
                                                                 for k in ("entry", "value")),
                             p.get("leverage"), *(None if p.get(k) is None else str(p[k]) for k in ("upnl", "liq")),
                             full))
    fetched = int(lb["fetched_at"] * 1000) if lb else None
    lrows = leaderboard_rows(lb) if lb and not store.has_leaderboard(fetched) else []
    store.write_poll({"ts": ts, "started_at": poll["started_at"], "requested": poll["requested"], "ok": poll["ok"],
                      "failed": poll["failed"], "full": full, "leaderboard_fetched_at": fetched},
                     rows, states, events, lrows, recorded_ts)
    return {"ts": ts, "full": full, "rows": len(rows), "events": len(events), "addresses": len(states)}


class WhaleRecorder:
    def __init__(self, store, tracker, interval_s=300, full_every_s=3600, clock=time.time):
        self.store, self.tracker, self.interval_s, self.full_every_s = store, tracker, interval_s, full_every_s
        self.clock = clock
        self.stop = threading.Event()
        self.status = {"running": False, "cycles": 0, "last": None, "error": None}

    def record_once(self):
        tr = self.tracker
        last = tr.last_poll
        now_ms = int(self.clock() * 1000)
        if not last or now_ms - last["finished_at"] > self.interval_s * 500:
            tr.poll_once()
        with tr._lock:
            last, states, lb, err = tr.last_poll, dict(tr.states), tr.leaderboard, tr.error
        if not last or err:
            self.status["error"] = err or "no poll yet"
            return None
        res = record(self.store, last["finished_at"], last, states, lb, interval_ms=self.interval_s * 1000,
                     full_every_ms=self.full_every_s * 1000, recorded_ts=int(self.clock() * 1000))
        self.status.update(last=res, error=None)
        self.status["cycles"] += 1
        return res

    def run(self):
        self.status["running"] = True
        while not self.stop.is_set():
            try:
                self.record_once()
            except Exception as exc:  # noqa: BLE001 — the recorder must survive a bad cycle
                log.exception("whale recorder cycle failed")
                self.status["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            self.stop.wait(self.interval_s)
        self.status["running"] = False


def _flock(path):
    if path == ":memory:":
        return True
    import fcntl
    fh = open(f"{path}.lock", "w")  # noqa: SIM115 — held for the life of the process
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    _STARTED["lock_fh"] = fh
    return True


def start(tracker_factory, env=None, thread=True, store=None):
    """Start the recorder once per process/DB when WHALE_RECORDER=1; returns it or None."""
    env = env if env is not None else os.environ
    if not enabled(env):
        return None
    with _LOCK:
        store = store or WhaleStore(default_path(env))
        if store.path in _STARTED:
            return _STARTED[store.path]
        if not _flock(store.path):
            log.warning("whale recorder: another process holds %s.lock — not started", store.path)
            return None
        tracker = tracker_factory()
        tracker.leaderboard_ttl_s = max(tracker.leaderboard_ttl_s, 60 * float(env.get("WHALE_LEADERBOARD_TTL_MIN", "360")))
        rec = WhaleRecorder(store, tracker, interval_s=60 * float(env.get("WHALE_INTERVAL_MIN", "5")),
                            full_every_s=60 * float(env.get("WHALE_FULL_EVERY_MIN", "60")))
        _STARTED[store.path] = rec
        _STARTED["current"] = rec
        if thread:
            threading.Thread(target=rec.run, name="whale-recorder", daemon=True).start()
        log.info("whale recorder started: %s every %.0f s", store.path, rec.interval_s)
        return rec


def register_whale_routes(app, env=None):
    """Read-only status route (always) and the gated recorder thread (WHALE_RECORDER=1)."""
    from flask import jsonify, request
    env = env if env is not None else os.environ
    state = {"store": None}

    def get_store():
        rec = _STARTED.get("current")
        if rec is not None:
            return rec.store
        if state["store"] is None:
            state["store"] = WhaleStore(default_path(env))
        return state["store"]

    @app.get("/api/whales/recorder")
    def whale_recorder_status():
        st = get_store()
        rec = _STARTED.get("current")
        limit = min(max(request.args.get("limit", 50, type=int) or 50, 1), 500)
        return jsonify({"enabled": enabled(env), "status": rec.status if rec else {"running": False},
                        "db": os.path.basename(st.path), **st.counts(), "recent_events": st.events(limit=limit),
                        "label": "Hyperliquid top-50 accounts by account value (public leaderboard), not all whales",
                        "research_only": True})

    if enabled(env):
        def tracker_factory():
            return app.extensions["positioning"]["get_service"]().whales
        start(tracker_factory, env)
