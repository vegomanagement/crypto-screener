"""Paper journal routes (read-only) and the in-process background worker.

Off by default. ``PAPER_JOURNAL=1`` starts ONE worker per database (a non-blocking flock on
``<PAPER_DB_PATH>.lock`` guards against a second process / a second registration). The worker runs
a cycle every ``PAPER_INTERVAL_MIN`` minutes (default 15) and ``PAPER_CLOSE_DELAY_S`` seconds
(default 30) after every 4h close (1d closes are 4h closes). Never places orders, never uses keys.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from flask import Blueprint, jsonify, render_template, request

log = logging.getLogger(__name__)
FOUR_H = 4 * 3_600_000
_START_LOCK = threading.Lock()
_STARTED: dict = {}


def enabled(env=None):
    return (env if env is not None else os.environ).get("PAPER_JOURNAL", "").strip() in ("1", "true", "yes", "on")


def next_wake(now_s, interval_s, delay_s):
    """(seconds to sleep, reason): the earlier of the next interval tick and the next 4h close + delay."""
    now_ms = int(now_s * 1000)
    close = (now_ms // FOUR_H + 1) * FOUR_H + int(delay_s * 1000)
    if close - FOUR_H > now_ms:                       # still inside the delay after the last close
        close -= FOUR_H
    to_close = (close - now_ms) / 1000
    return (to_close, "bar_close") if to_close <= interval_s else (interval_s, "interval")


class Worker:
    def __init__(self, store, venue, strategies, symbols, interval_s=900, delay_s=30, clock=time.time):
        self.store, self.venue, self.strategies, self.symbols = store, venue, strategies, symbols
        self.interval_s, self.delay_s, self.clock = interval_s, delay_s, clock
        self.stop = threading.Event()
        self.status = {"running": False, "cycles": 0, "last": None, "next": None}

    def run_one(self, strat, reason):
        from paper_journal import run_cycle
        kind = strat.get("kind", "setup")
        if kind == "portfolio":
            import paper_portfolio
            return paper_portfolio.run_cycle(self.store, strat, self.venue.candles, self.venue.funding_rows,
                                             reason=reason, clock=self.clock)
        if kind == "observer":
            return strat["cycle"](self.store, strat, reason=reason, clock=self.clock)
        return run_cycle(self.store, strat, self.symbols, self.venue.candles, funding=self.venue.funding,
                         reason=reason, clock=self.clock)

    def cycle(self, reason):
        for strat in self.strategies():
            try:
                res = self.run_one(strat, reason)
            except Exception:  # noqa: BLE001 — one strategy's failure must not stop the others
                log.exception("paper journal %s cycle failed", strat["id"])
                continue
            self.status["last"] = {"ts": int(self.clock() * 1000), "reason": reason, "strategy": strat["id"],
                                   "version": res["version"], "errors": res["errors"],
                                   "new": sum(r.get("new", 0) + r.get("new_days", 0) for r in res["results"])}
        self.status["cycles"] += 1

    def run(self):
        self.status["running"] = True
        reason = "start"
        while not self.stop.is_set():
            try:
                self.cycle(reason)
            except Exception:  # noqa: BLE001 — the worker must survive any one bad cycle
                log.exception("paper journal cycle failed")
            wait, reason = next_wake(self.clock(), self.interval_s, self.delay_s)
            self.status["next"] = {"ts": int((self.clock() + wait) * 1000), "reason": reason}
            self.stop.wait(wait)
        self.status["running"] = False


def _acquire_file_lock(path):
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


def start_worker(store, env=None, venue=None, thread=True):
    """Start the worker once per process and per DB file; returns the Worker or None."""
    import paper_strategies as ps
    env = env if env is not None else os.environ
    with _START_LOCK:
        if store.path in _STARTED:
            return _STARTED[store.path]
        if not _acquire_file_lock(store.path):
            log.warning("paper journal: another process holds %s.lock — worker not started", store.path)
            return None
        ps.load_plugins(env)
        if venue is None:
            from paper_data import VenueData
            venue = VenueData(store)
        w = Worker(store, venue, lambda: list(ps.REGISTRY.values()), ps.universe(env),
                   interval_s=60 * float(env.get("PAPER_INTERVAL_MIN", "15")),
                   delay_s=float(env.get("PAPER_CLOSE_DELAY_S", "30")))
        _STARTED[store.path] = w
        if thread:
            threading.Thread(target=w.run, name="paper-journal", daemon=True).start()
        log.info("paper journal worker started: %d strategies, %d symbols", len(ps.REGISTRY), len(w.symbols))
        return w


def _public(r):
    import json
    out = {k: v for k, v in r.items() if k != "inputs_json"}
    out["inputs"] = json.loads(r.get("inputs_json") or "{}")
    return out


def register_paper_routes(app, *, store=None, env=None, start=None):
    from paper_store import STATE_GROUPS, STATES, PaperStore, default_path
    env = env if env is not None else os.environ
    bp = Blueprint("paper", __name__)
    lock, state = threading.Lock(), {"store": store}

    def get_store():
        with lock:
            if state["store"] is None:
                state["store"] = PaperStore(default_path(env))
            return state["store"]

    def current(sid, version):
        st = get_store()
        if version is None:
            vs = [r["version"] for r in st.strategies() if r["id"] == sid]
            version = max(vs) if vs else None
        return st, version

    @bp.get("/api/paper/strategies")
    def strategies():
        import paper_strategies as ps
        st = get_store()
        rows = st.strategies()
        for r in rows:
            reg = ps.REGISTRY.get(r["id"])
            r["active"] = bool(reg and reg["hash"] == r["params_hash"])
        w = _STARTED.get(st.path)
        return jsonify({"strategies": rows, "worker": {"enabled": enabled(env), **(w.status if w else {"running": False})},
                        "universe": ps.universe(env), "paper_only": True, "runs": st.runs(10)})

    @bp.get("/api/paper/trades")
    def trades():
        sid, sel = request.args.get("strategy"), request.args.get("state")
        version = request.args.get("version", type=int)
        states = STATE_GROUPS.get(sel) or ((sel,) if sel in STATES else None)
        if sel and states is None:
            return jsonify({"error": "unknown state"}), 400
        st, version = current(sid, version) if sid else (get_store(), None)
        rows = st.setups(sid, version, request.args.get("symbol"), states, limit=2000)
        return jsonify({"strategy": sid, "version": version, "trades": [_public(r) for r in rows],
                        "events": st.events(sid, version, 50) if sid else []})

    @bp.get("/api/paper/stats")
    def stats():
        import paper_stats
        sid = request.args.get("strategy")
        if not sid:
            return jsonify({"error": "strategy required"}), 400
        st, version = current(sid, request.args.get("version", type=int))
        meta = next((r for r in st.strategies() if r["id"] == sid and r["version"] == version), None)
        rows = st.setups(sid, version) if version else []
        return jsonify({"strategy": sid, "version": version, "paper_only": True,
                        **paper_stats.summarize(rows, (meta or {}).get("expectation"))})

    @bp.get("/api/paper/portfolio")
    def portfolio():
        import paper_portfolio_stats
        sid = request.args.get("strategy")
        if not sid:
            return jsonify({"error": "strategy required"}), 400
        st, version = current(sid, request.args.get("version", type=int))
        meta = next((r for r in st.strategies() if r["id"] == sid and r["version"] == version), None)
        if meta is None or meta.get("kind") != "portfolio":
            return jsonify({"error": "not a portfolio strategy"}), 404
        days, pos = st.pf_days(sid, version), st.pf_positions(sid, version)
        return jsonify({"strategy": sid, "version": version, "paper_only": True, "state": st.pf_state(sid, version),
                        "days": days[-400:], "positions": pos[-500:],
                        "events": st.events(sid, version, 50),
                        **paper_portfolio_stats.summarize(days, pos, meta.get("expectation"))})

    @bp.get("/api/paper/observer")
    def observer():
        import whale_observer as wo
        sid = request.args.get("strategy")
        if not sid:
            return jsonify({"error": "strategy required"}), 400
        st, version = current(sid, request.args.get("version", type=int))
        meta = next((r for r in st.strategies() if r["id"] == sid and r["version"] == version), None)
        if meta is None or meta.get("kind") != "observer":
            return jsonify({"error": "not an observer strategy"}), 404
        has = st.q("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_follow'")
        rows = st.q("SELECT * FROM paper_follow WHERE strategy_id=? AND version=? ORDER BY knowable_ts DESC, id DESC",
                    (sid, version)) if has else []
        return jsonify({"strategy": sid, "version": version, "paper_only": True, "no_edge_claimed": True,
                        "state": st.symbol_state(sid, version, "*"), "by_horizon": wo.summarize(rows),
                        "rows": rows[:300], "note": "Hypothetical follow trades for a future pre-registered test "
                                                    "(docs/WHALE_RECORDER.md). Not a signal; no orders."})

    @bp.get("/paper")
    def page():
        return render_template("paper.html")

    app.register_blueprint(bp)
    app.extensions["paper"] = {"get_store": get_store}
    if (start if start is not None else enabled(env)):
        start_worker(get_store(), env)
