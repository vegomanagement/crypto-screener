"""SQLite persistence for the paper journal (forward test). Same pattern as analyst_store /
liquidations_store: one file, created lazily, ``check_same_thread=False`` + a lock.

Path: ``PAPER_DB_PATH`` if set (empty string = in memory), else ``paper_journal.db`` next to
``DB_PATH``. Rows are append-mostly: a setup row's planned fields and its knowable/recorded times
are written once; only its lifecycle columns move forward. Strategy versions are never edited.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_strategies (
    id TEXT NOT NULL, version INTEGER NOT NULL, params_json TEXT NOT NULL, params_hash TEXT NOT NULL,
    code_version TEXT NOT NULL, title TEXT, expectation_json TEXT, created_ts INTEGER NOT NULL,
    PRIMARY KEY (id, version), UNIQUE (id, params_hash));
CREATE TABLE IF NOT EXISTS paper_symbols (
    strategy_id TEXT NOT NULL, version INTEGER NOT NULL, symbol TEXT NOT NULL,
    started_as_of INTEGER NOT NULL, started_ts INTEGER NOT NULL, last_as_of INTEGER NOT NULL,
    last_run_ts INTEGER NOT NULL, cycles INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (strategy_id, version, symbol));
CREATE TABLE IF NOT EXISTS paper_setups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL, version INTEGER NOT NULL, symbol TEXT NOT NULL, key TEXT NOT NULL,
    side TEXT NOT NULL, state TEXT NOT NULL, seen_at_start INTEGER NOT NULL DEFAULT 0,
    late INTEGER NOT NULL DEFAULT 0, knowable_ts INTEGER NOT NULL, recorded_ts INTEGER NOT NULL,
    observed_as_of INTEGER NOT NULL, planned_entry REAL, planned_stop REAL, planned_target REAL,
    entry_type TEXT, inputs_json TEXT,
    fill_ts INTEGER, fill_close_ts INTEGER, entry REAL, stop REAL, target REAL, be_ts INTEGER,
    exit_ts INTEGER, exit_reason TEXT, r_gross REAL, partial INTEGER, partial_ts INTEGER,
    fee_r REAL, fund_r REAL, fund_status TEXT, net_r REAL, net_pct REAL, risk_pct REAL,
    cancel_ts INTEGER, cancel_reason TEXT, updated_ts INTEGER,
    UNIQUE (strategy_id, version, symbol, key, knowable_ts));
CREATE INDEX IF NOT EXISTS paper_setups_state ON paper_setups (strategy_id, version, state);
CREATE TABLE IF NOT EXISTS paper_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_id TEXT NOT NULL, version INTEGER NOT NULL,
    symbol TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, ts INTEGER NOT NULL,
    detail_json TEXT, recorded_ts INTEGER NOT NULL,
    UNIQUE (strategy_id, version, symbol, kind, key, ts));
CREATE TABLE IF NOT EXISTS paper_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, strategy_id TEXT, version INTEGER,
    reason TEXT, symbols INTEGER, ok INTEGER, errors_json TEXT, duration_ms INTEGER);
CREATE TABLE IF NOT EXISTS paper_klines (
    symbol TEXT NOT NULL, tf TEXT NOT NULL, ts INTEGER NOT NULL, close_ts INTEGER NOT NULL,
    o REAL NOT NULL, h REAL NOT NULL, l REAL NOT NULL, c REAL NOT NULL, v REAL NOT NULL,
    PRIMARY KEY (symbol, tf, ts));
CREATE TABLE IF NOT EXISTS paper_funding (
    symbol TEXT NOT NULL, time INTEGER NOT NULL, rate REAL NOT NULL, PRIMARY KEY (symbol, time));
CREATE TABLE IF NOT EXISTS paper_funding_cover (
    symbol TEXT PRIMARY KEY, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL);
"""

SETUP_COLS = ("id", "strategy_id", "version", "symbol", "key", "side", "state", "seen_at_start", "late",
              "knowable_ts", "recorded_ts", "observed_as_of", "planned_entry", "planned_stop", "planned_target",
              "entry_type", "inputs_json", "fill_ts", "fill_close_ts", "entry", "stop", "target", "be_ts", "exit_ts",
              "exit_reason", "r_gross", "partial", "partial_ts", "fee_r", "fund_r", "fund_status", "net_r", "net_pct",
              "risk_pct", "cancel_ts", "cancel_reason", "updated_ts")
OPEN_STATES = ("filled", "be")
CLOSED_STATES = ("target", "stop", "breakeven", "timeout")
STATES = ("pending", "expired") + OPEN_STATES + CLOSED_STATES
STATE_GROUPS = {"pending": ("pending",), "open": OPEN_STATES, "closed": CLOSED_STATES, "expired": ("expired",)}


def default_path(env=None):
    env = os.environ if env is None else env
    explicit = env.get("PAPER_DB_PATH")
    if explicit is not None:
        return explicit or ":memory:"
    base = os.path.dirname(os.path.abspath(env.get("DB_PATH", "signals.db")))
    return os.path.join(base, "paper_journal.db")


def now_ms():
    return int(time.time() * 1000)


class PaperStore:
    def __init__(self, path=":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def q(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def x(self, sql, args=()):
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur

    def many(self, sql, rows):
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    # strategies ---------------------------------------------------------------------------------
    def ensure_version(self, strat, ts=None):
        """Version row for the strategy's current params hash: reused when it exists, else
        version = max + 1. Never edits an existing row."""
        with self._lock:
            row = self.q("SELECT version FROM paper_strategies WHERE id=? AND params_hash=?",
                         (strat["id"], strat["hash"]))
            if row:
                return row[0]["version"]
            last = self.q("SELECT MAX(version) AS v FROM paper_strategies WHERE id=?", (strat["id"],))[0]["v"]
            version = (last or 0) + 1
            self.x("INSERT INTO paper_strategies (id, version, params_json, params_hash, code_version, title, "
                   "expectation_json, created_ts) VALUES (?,?,?,?,?,?,?,?)",
                   (strat["id"], version, json.dumps(strat["params"], sort_keys=True), strat["hash"],
                    strat["code_version"], strat.get("title"), json.dumps(strat.get("expectation")),
                    ts or now_ms()))
            return version

    def strategies(self):
        rows = self.q("SELECT * FROM paper_strategies ORDER BY id, version")
        for r in rows:
            r["params"] = json.loads(r.pop("params_json"))
            r["expectation"] = json.loads(r.pop("expectation_json") or "null")
        return rows

    # per-symbol cursor ----------------------------------------------------------------------------
    def symbol_state(self, sid, version, symbol):
        rows = self.q("SELECT * FROM paper_symbols WHERE strategy_id=? AND version=? AND symbol=?",
                      (sid, version, symbol))
        return rows[0] if rows else None

    def start_symbol(self, sid, version, symbol, as_of, ts):
        self.x("INSERT OR IGNORE INTO paper_symbols (strategy_id, version, symbol, started_as_of, started_ts, "
               "last_as_of, last_run_ts) VALUES (?,?,?,?,?,?,?)", (sid, version, symbol, as_of, ts, as_of, ts))

    def advance_symbol(self, sid, version, symbol, as_of, ts):
        self.x("UPDATE paper_symbols SET last_as_of=MAX(last_as_of, ?), last_run_ts=?, cycles=cycles+1 "
               "WHERE strategy_id=? AND version=? AND symbol=?", (as_of, ts, sid, version, symbol))

    # setups ---------------------------------------------------------------------------------------
    def insert_setup(self, row):
        cols = [c for c in SETUP_COLS if c != "id" and c in row]
        cur = self.x(f"INSERT OR IGNORE INTO paper_setups ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     [row[c] for c in cols])
        return cur.lastrowid if cur.rowcount else None

    def update_setup(self, setup_id, **fields):
        cols = list(fields)
        self.x(f"UPDATE paper_setups SET {', '.join(c + '=?' for c in cols)} WHERE id=?",
               [fields[c] for c in cols] + [setup_id])

    def setups(self, sid=None, version=None, symbol=None, states=None, limit=None):
        where, args = [], []
        for col, val in (("strategy_id", sid), ("version", version), ("symbol", symbol)):
            if val is not None:
                where.append(f"{col}=?")
                args.append(val)
        if states:
            where.append(f"state IN ({','.join('?' * len(states))})")
            args += list(states)
        sql = "SELECT * FROM paper_setups" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.q(sql, args)

    def add_event(self, sid, version, symbol, kind, key, ts, detail, recorded_ts):
        self.x("INSERT OR IGNORE INTO paper_events (strategy_id, version, symbol, kind, key, ts, detail_json, "
               "recorded_ts) VALUES (?,?,?,?,?,?,?,?)",
               (sid, version, symbol, kind, key, ts, json.dumps(detail, default=str), recorded_ts))

    def events(self, sid=None, version=None, limit=200):
        sql, args = "SELECT * FROM paper_events", []
        if sid is not None:
            sql += " WHERE strategy_id=? AND version=?"
            args = [sid, version]
        return self.q(sql + f" ORDER BY id DESC LIMIT {int(limit)}", args)

    def log_run(self, ts, sid, version, reason, symbols, ok, errors, duration_ms):
        self.x("INSERT INTO paper_runs (ts, strategy_id, version, reason, symbols, ok, errors_json, duration_ms) "
               "VALUES (?,?,?,?,?,?,?,?)", (ts, sid, version, reason, symbols, ok, json.dumps(errors), duration_ms))

    def runs(self, limit=20):
        return self.q(f"SELECT * FROM paper_runs ORDER BY id DESC LIMIT {int(limit)}")

    # market data cache ---------------------------------------------------------------------------
    def klines(self, symbol, tf):
        return self.q("SELECT ts, close_ts, o, h, l, c, v FROM paper_klines WHERE symbol=? AND tf=? ORDER BY ts",
                      (symbol, tf))

    def last_kline_ts(self, symbol, tf):
        return self.q("SELECT MAX(ts) AS t FROM paper_klines WHERE symbol=? AND tf=?", (symbol, tf))[0]["t"]

    def put_klines(self, symbol, tf, bars):
        self.many("INSERT OR REPLACE INTO paper_klines (symbol, tf, ts, close_ts, o, h, l, c, v) "
                  "VALUES (?,?,?,?,?,?,?,?,?)",
                  [(symbol, tf, b["ts"], b["close_ts"], b["o"], b["h"], b["l"], b["c"], b["v"]) for b in bars])

    def funding_cover(self, symbol):
        rows = self.q("SELECT start_ms, end_ms FROM paper_funding_cover WHERE symbol=?", (symbol,))
        return rows[0] if rows else None

    def put_funding(self, symbol, rows, start_ms, end_ms):
        self.many("INSERT OR REPLACE INTO paper_funding (symbol, time, rate) VALUES (?,?,?)",
                  [(symbol, r["time"], r["rate"]) for r in rows])
        cov = self.funding_cover(symbol)
        if cov and start_ms <= cov["end_ms"] and end_ms >= cov["start_ms"]:      # overlapping: union
            start_ms, end_ms = min(start_ms, cov["start_ms"]), max(end_ms, cov["end_ms"])
        self.x("INSERT OR REPLACE INTO paper_funding_cover (symbol, start_ms, end_ms) VALUES (?,?,?)",
               (symbol, start_ms, end_ms))

    def funding_rows(self, symbol, a, b):
        return self.q("SELECT time, rate FROM paper_funding WHERE symbol=? AND time>? AND time<=? ORDER BY time",
                      (symbol, a, b))
