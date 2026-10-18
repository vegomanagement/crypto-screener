"""SQLite store for the Hyperliquid whale-position recorder (whale_recorder.py).

Path: ``WHALES_DB_PATH`` if set (empty string = in memory), else ``whales.db`` next to ``DB_PATH``.
Sizes and prices are stored as exact decimal TEXT. To stay small over months, a poll writes a row
per position only when its size changed (or the position closed: szi '0'), plus every position
on a 'full' poll (default hourly, with uPnL). ``whale_state`` is the latest known book per address
(restart-safe event derivation); ``whale_seen`` the last poll in which each address answered.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS whale_polls (
    ts INTEGER PRIMARY KEY, started_at INTEGER NOT NULL, requested INTEGER NOT NULL, ok INTEGER NOT NULL,
    failed INTEGER NOT NULL, full INTEGER NOT NULL, leaderboard_fetched_at INTEGER, ok_json TEXT,
    recorded_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS whale_leaderboard (
    fetched_at INTEGER NOT NULL, rank INTEGER NOT NULL, address TEXT NOT NULL, account_value TEXT,
    pnl_day TEXT, pnl_week TEXT, pnl_month TEXT, pnl_all TEXT, display_name TEXT,
    PRIMARY KEY (fetched_at, address));
CREATE TABLE IF NOT EXISTS whale_positions (
    poll_ts INTEGER NOT NULL, address TEXT NOT NULL, coin TEXT NOT NULL, szi TEXT NOT NULL, entry TEXT,
    value TEXT, leverage REAL, upnl TEXT, liq TEXT, full INTEGER NOT NULL,
    PRIMARY KEY (poll_ts, address, coin));
CREATE TABLE IF NOT EXISTS whale_state (
    address TEXT NOT NULL, coin TEXT NOT NULL, szi TEXT NOT NULL, entry TEXT, value TEXT, leverage REAL,
    upnl TEXT, liq TEXT, poll_ts INTEGER NOT NULL, PRIMARY KEY (address, coin));
CREATE TABLE IF NOT EXISTS whale_seen (
    address TEXT PRIMARY KEY, first_ts INTEGER NOT NULL, last_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS whale_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, address TEXT NOT NULL, coin TEXT NOT NULL, kind TEXT NOT NULL,
    side TEXT NOT NULL, from_szi TEXT NOT NULL, to_szi TEXT NOT NULL, entry_px TEXT, notional TEXT,
    prev_ts INTEGER NOT NULL, knowable_ts INTEGER NOT NULL, gap INTEGER NOT NULL DEFAULT 0,
    recorded_ts INTEGER NOT NULL, UNIQUE (address, coin, knowable_ts));
CREATE INDEX IF NOT EXISTS whale_events_ts ON whale_events (knowable_ts);
"""
POS_COLS = ("szi", "entry", "value", "leverage", "upnl", "liq")


def default_path(env=None):
    env = os.environ if env is None else env
    explicit = env.get("WHALES_DB_PATH")
    if explicit is not None:
        return explicit or ":memory:"
    base = os.path.dirname(os.path.abspath(env.get("DB_PATH", "signals.db")))
    return os.path.join(base, "whales.db")


def _s(v):
    return None if v is None else str(v)


class WhaleStore:
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

    def has_poll(self, ts):
        return bool(self.q("SELECT 1 FROM whale_polls WHERE ts=?", (ts,)))

    def last_full_ts(self):
        return self.q("SELECT MAX(ts) AS t FROM whale_polls WHERE full=1")[0]["t"]

    def state(self, address):
        return {r["coin"]: r for r in self.q("SELECT * FROM whale_state WHERE address=?", (address,))}

    def seen(self, address):
        rows = self.q("SELECT * FROM whale_seen WHERE address=?", (address,))
        return rows[0] if rows else None

    def has_leaderboard(self, fetched_at):
        return bool(self.q("SELECT 1 FROM whale_leaderboard WHERE fetched_at=? LIMIT 1", (fetched_at,)))

    def write_poll(self, poll, positions_rows, state_by_address, events, leaderboard_rows, recorded_ts):
        """One transaction: poll row, changed/full position rows, new per-address state, events."""
        with self._lock:
            c = self._conn
            c.execute("INSERT OR IGNORE INTO whale_polls (ts, started_at, requested, ok, failed, full, "
                      "leaderboard_fetched_at, ok_json, recorded_ts) VALUES (?,?,?,?,?,?,?,?,?)",
                      (poll["ts"], poll["started_at"], poll["requested"], poll["ok"], poll["failed"], poll["full"],
                       poll.get("leaderboard_fetched_at"), json.dumps(sorted(state_by_address)), recorded_ts))
            c.executemany("INSERT OR IGNORE INTO whale_leaderboard (fetched_at, rank, address, account_value, pnl_day, "
                          "pnl_week, pnl_month, pnl_all, display_name) VALUES (?,?,?,?,?,?,?,?,?)", leaderboard_rows)
            c.executemany("INSERT OR IGNORE INTO whale_positions (poll_ts, address, coin, szi, entry, value, leverage, "
                          "upnl, liq, full) VALUES (?,?,?,?,?,?,?,?,?,?)", positions_rows)
            for address, book in state_by_address.items():
                c.execute("DELETE FROM whale_state WHERE address=?", (address,))
                c.executemany("INSERT INTO whale_state (address, coin, szi, entry, value, leverage, upnl, liq, poll_ts) "
                              "VALUES (?,?,?,?,?,?,?,?,?)",
                              [(address, coin, _s(p["szi"]), _s(p.get("entry")), _s(p.get("value")), p.get("leverage"),
                                _s(p.get("upnl")), _s(p.get("liq")), poll["ts"]) for coin, p in sorted(book.items())])
                c.execute("INSERT INTO whale_seen (address, first_ts, last_ts) VALUES (?,?,?) ON CONFLICT(address) "
                          "DO UPDATE SET last_ts=MAX(last_ts, excluded.last_ts)", (address, poll["ts"], poll["ts"]))
            c.executemany("INSERT OR IGNORE INTO whale_events (address, coin, kind, side, from_szi, to_szi, entry_px, "
                          "notional, prev_ts, knowable_ts, gap, recorded_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          [(e["address"], e["coin"], e["kind"], e["side"], e["from_szi"], e["to_szi"], e.get("entry_px"),
                            e.get("notional"), e["prev_ts"], e["knowable_ts"], int(e["gap"]), recorded_ts)
                           for e in events])
            c.commit()

    def events(self, since_ts=None, kinds=None, limit=None):
        where, args = [], []
        if since_ts is not None:
            where.append("knowable_ts >= ?")
            args.append(since_ts)
        if kinds:
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            args += list(kinds)
        sql = "SELECT * FROM whale_events" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY knowable_ts, id"
        if limit:
            sql = f"SELECT * FROM ({sql.replace(' ORDER BY knowable_ts, id', ' ORDER BY knowable_ts DESC, id DESC')} " \
                  f"LIMIT {int(limit)}) ORDER BY knowable_ts, id"
        return self.q(sql, args)

    def leaderboard_at(self, ts):
        """Rows of the last leaderboard fetched at or before ``ts`` (what was knowable then)."""
        t = self.q("SELECT MAX(fetched_at) AS t FROM whale_leaderboard WHERE fetched_at <= ?", (ts,))[0]["t"]
        return [] if t is None else self.q("SELECT * FROM whale_leaderboard WHERE fetched_at=? ORDER BY rank", (t,))

    def counts(self):
        one = self.q("SELECT COUNT(*) AS n, MIN(ts) AS first, MAX(ts) AS last FROM whale_polls")[0]
        ev = self.q("SELECT kind, COUNT(*) AS n FROM whale_events GROUP BY kind")
        return {"polls": one["n"], "first_poll": one["first"], "last_poll": one["last"],
                "events": {r["kind"]: r["n"] for r in ev},
                "position_rows": self.q("SELECT COUNT(*) AS n FROM whale_positions")[0]["n"],
                "addresses": self.q("SELECT COUNT(*) AS n FROM whale_seen")[0]["n"]}
