"""Rolling liquidation store (default 24 h) with coverage tracking, optionally persisted to SQLite.

Totals are only as complete as the venue stream and the time the collector was connected:
* Binance ``forceOrder`` pushes at most the latest liquidation per symbol per 1000 ms, so
  Binance totals are a LOWER BOUND even while connected.
* Bybit ``allLiquidation`` pushes all liquidations (500 ms batches) but the price is the
  bankruptcy price, so notional is approximate; totals cover only connected time.
* Hyperliquid has no public liquidation stream.
Persistence (``path``): events and coverage spans are mirrored to a small SQLite file so the
24 h history survives a restart. A span still open when the process died is closed at its last
heartbeat (the last time the collector loop confirmed it was connected), never at the restart
time, so coverage is not over-claimed. Retention and the per-market cap apply to the file too.
No threads, files or network at import time.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections import deque
from decimal import Decimal

from positioning import NormaliseError, dec, fmt, q, Q2

HOUR_MS = 3_600_000
WINDOWS = {"1h": HOUR_MS, "4h": 4 * HOUR_MS, "24h": 24 * HOUR_MS}
LABELS = {
    "binance": ("LOWER BOUND — Binance pushes at most one liquidation per symbol per 1000 ms "
                "(forceOrder snapshot stream); only connected time is covered."),
    "bybit": ("Bybit allLiquidation pushes all liquidations (500 ms batches); notional uses the "
              "bankruptcy price; only connected time is covered, so totals are a lower bound."),
}
SOURCES = {"binance": "WS fstream.binance.com/market/ws/!forceOrder@arr",
           "bybit": "WS stream.bybit.com/v5/public/linear allLiquidation.<symbol>"}
HL_REASON = "Hyperliquid has no public liquidation stream"
HEARTBEAT_WRITE_MS = 10_000
SCHEMA = """
CREATE TABLE IF NOT EXISTS liq_events (
    venue TEXT NOT NULL, symbol TEXT NOT NULL, ts INTEGER NOT NULL, side TEXT NOT NULL,
    price TEXT NOT NULL, qty TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS liq_events_market ON liq_events (venue, symbol, ts);
CREATE TABLE IF NOT EXISTS liq_coverage (
    venue TEXT NOT NULL, symbol TEXT NOT NULL, start_ms INTEGER NOT NULL,
    end_ms INTEGER, seen_ms INTEGER NOT NULL, PRIMARY KEY (venue, symbol, start_ms));
"""


def default_path():
    """``LIQUIDATIONS_DB_PATH`` if set (empty string = in memory only), else next to ``DB_PATH``."""
    explicit = os.environ.get("LIQUIDATIONS_DB_PATH")
    if explicit is not None:
        return explicit or None
    base = os.path.dirname(os.path.abspath(os.environ.get("DB_PATH", "signals.db")))
    return os.path.join(base, "liquidations.db")


def normalise_binance(message):
    """forceOrder event -> [event]. o.S == SELL means a LONG position was liquidated."""
    if not isinstance(message, dict) or message.get("e") != "forceOrder":
        return []
    o = message.get("o")
    if not isinstance(o, dict):
        raise NormaliseError("forceOrder without order")
    side = {"SELL": "long", "BUY": "short"}.get(o.get("S"))
    if side is None:
        raise NormaliseError("forceOrder with unknown side")
    qty = dec(o.get("z")) or dec(o.get("q"))
    price = dec(o.get("ap")) or dec(o.get("p"))
    if qty is None or price is None:
        raise NormaliseError("forceOrder without qty/price")
    return [{"venue": "binance", "symbol": o["s"], "side": side, "price": price, "qty": qty,
             "notional": price * qty, "ts": int(o["T"])}]


def normalise_bybit(message):
    """allLiquidation push -> events. S == Buy means a LONG position was liquidated (Bybit docs)."""
    if not isinstance(message, dict) or not str(message.get("topic", "")).startswith("allLiquidation."):
        return []
    data = message.get("data")
    items = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    out = []
    for d in items:
        side = {"Buy": "long", "Sell": "short"}.get(d.get("S"))
        qty, price = dec(d.get("v")), dec(d.get("p"))
        if side is None or qty is None or price is None:
            raise NormaliseError("allLiquidation item malformed")
        out.append({"venue": "bybit", "symbol": d["s"], "side": side, "price": price, "qty": qty,
                    "notional": price * qty, "ts": int(d["T"])})
    return out


def parse_message(venue, text):
    try:
        message = json.loads(text)
    except ValueError:
        return []
    return normalise_binance(message) if venue == "binance" else normalise_bybit(message)


def _event_json(e):
    return {"side": e["side"], "price": fmt(e["price"]), "qty": fmt(e["qty"]),
            "notional_usd": fmt(q(e["notional"], Q2)), "ts": e["ts"]}


class LiquidationStore:
    def __init__(self, retention_ms=24 * HOUR_MS, max_events_per_market=20000, clock_ms=None, path=None):
        self.retention_ms = retention_ms
        self.max_events = max_events_per_market
        self._clock = clock_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.Lock()
        self._events = {}      # (venue, symbol) -> deque of events ascending by ts
        self._coverage = {}    # (venue, symbol) -> [[start, end_or_None], ...]
        self._seen = {}        # (venue, symbol) -> last heartbeat written for the open span
        self.path = path
        self._db = None
        if path:
            if os.path.basename(str(path)) == "signals.db":
                raise ValueError("liquidation store must be separate from signals.db")
            if str(path) != ":memory:":
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
            self._db.commit()
            self._load(self._clock())

    # ----- persistence -----
    def _load(self, now):
        """Restore events and spans from the file; open spans end at their last heartbeat."""
        cutoff = now - self.retention_ms
        with self._lock, self._db:
            self._db.execute("UPDATE liq_coverage SET end_ms = seen_ms WHERE end_ms IS NULL")
            self._db_prune(cutoff)
            for venue, symbol, ts, side, price, qty in self._db.execute(
                    "SELECT venue, symbol, ts, side, price, qty FROM liq_events ORDER BY ts, rowid"):
                p, qn = Decimal(price), Decimal(qty)
                self._events.setdefault((venue, symbol), deque(maxlen=self.max_events)).append(
                    {"venue": venue, "symbol": symbol, "side": side, "price": p, "qty": qn,
                     "notional": p * qn, "ts": ts})
            for venue, symbol, start, end in self._db.execute(
                    "SELECT venue, symbol, start_ms, end_ms FROM liq_coverage ORDER BY start_ms"):
                if end > start:
                    self._coverage.setdefault((venue, symbol), []).append([start, end])

    def _db_prune(self, cutoff):
        self._db.execute("DELETE FROM liq_events WHERE ts < ?", (cutoff,))
        self._db.execute("DELETE FROM liq_coverage WHERE end_ms IS NOT NULL AND end_ms < ?", (cutoff,))

    def _db_cap(self, keys):
        for venue, symbol in keys:
            self._db.execute(
                "DELETE FROM liq_events WHERE venue=? AND symbol=? AND rowid NOT IN ("
                "SELECT rowid FROM liq_events WHERE venue=? AND symbol=? ORDER BY ts DESC, rowid DESC LIMIT ?)",
                (venue, symbol, venue, symbol, self.max_events))

    def close(self):
        if self._db is not None:
            with self._lock:
                self._db.close()
                self._db = None

    def heartbeat(self, venue, symbols, now_ms=None):
        """The collector is still connected for ``symbols``: lets a restart close open spans here."""
        now = self._clock() if now_ms is None else now_ms
        if self._db is None:
            return
        with self._lock:
            due = [s for s in symbols if now - self._seen.get((venue, s), 0) >= HEARTBEAT_WRITE_MS
                   and (self._coverage.get((venue, s)) or [[0, 0]])[-1][1] is None]
            if not due:
                return
            with self._db:
                for s in due:
                    start = self._coverage[(venue, s)][-1][0]
                    self._db.execute("UPDATE liq_coverage SET seen_ms=? WHERE venue=? AND symbol=? AND start_ms=?",
                                     (now, venue, s, start))
                    self._seen[(venue, s)] = now

    def _prune(self, now):
        cutoff = now - self.retention_ms
        for key, events in list(self._events.items()):
            while events and events[0]["ts"] < cutoff:
                events.popleft()
            if not events and key not in self._coverage:
                del self._events[key]
        for key, spans in list(self._coverage.items()):
            spans[:] = [s for s in spans if s[1] is None or s[1] >= cutoff]
            if not spans:
                del self._coverage[key]

    def add(self, events, now_ms=None):
        now = self._clock() if now_ms is None else now_ms
        added, rows = 0, []
        with self._lock:
            for e in events:
                if e["ts"] < now - self.retention_ms or e["ts"] > now + 60_000:
                    continue
                dq = self._events.setdefault((e["venue"], e["symbol"]),
                                             deque(maxlen=self.max_events))
                dq.append(e)
                added += 1
                rows.append((e["venue"], e["symbol"], int(e["ts"]), e["side"], fmt(e["price"]), fmt(e["qty"])))
            self._prune(now)
            if self._db is not None and rows:
                with self._db:
                    self._db.executemany("INSERT INTO liq_events VALUES (?, ?, ?, ?, ?, ?)", rows)
                    self._db_prune(now - self.retention_ms)
                    self._db_cap({(r[0], r[1]) for r in rows})
        return added

    def cover_start(self, venue, symbol, now_ms=None):
        now = self._clock() if now_ms is None else now_ms
        with self._lock:
            spans = self._coverage.setdefault((venue, symbol), [])
            if not spans or spans[-1][1] is not None:
                spans.append([now, None])
                if self._db is not None:
                    with self._db:
                        self._db.execute("INSERT OR REPLACE INTO liq_coverage VALUES (?, ?, ?, NULL, ?)",
                                         (venue, symbol, now, now))
                    self._seen[(venue, symbol)] = now

    def cover_end(self, venue, symbol, now_ms=None):
        now = self._clock() if now_ms is None else now_ms
        with self._lock:
            spans = self._coverage.get((venue, symbol)) or []
            if spans and spans[-1][1] is None:
                spans[-1][1] = now
                if self._db is not None:
                    with self._db:
                        self._db.execute("UPDATE liq_coverage SET end_ms=?, seen_ms=? WHERE venue=? AND symbol=? "
                                         "AND start_ms=?", (now, now, venue, symbol, spans[-1][0]))
                        self._db_prune(now - self.retention_ms)

    def covered_ms(self, venue, symbol, start, end):
        with self._lock:
            spans = list(self._coverage.get((venue, symbol)) or [])
        total = 0
        for s, e in spans:
            e = end if e is None else e
            total += max(0, min(e, end) - max(s, start))
        return total

    def aggregate(self, venue, symbol, now_ms=None, top=5):
        if venue == "hyperliquid":
            return {"venue": venue, "symbol": symbol, "value": None, "reason": HL_REASON,
                    "lower_bound": None, "source": None}
        now = self._clock() if now_ms is None else now_ms
        with self._lock:
            self._prune(now)
            events = list(self._events.get((venue, symbol)) or [])
            spans = [list(s) for s in self._coverage.get((venue, symbol)) or []]
        windows = {}
        for name, span in WINDOWS.items():
            start = now - span
            sel = [e for e in events if start <= e["ts"] <= now]
            long_usd = sum((e["notional"] for e in sel if e["side"] == "long"), Decimal(0))
            short_usd = sum((e["notional"] for e in sel if e["side"] == "short"), Decimal(0))
            covered = self.covered_ms(venue, symbol, start, now)
            windows[name] = {"long_usd": fmt(q(long_usd, Q2)), "short_usd": fmt(q(short_usd, Q2)),
                             "total_usd": fmt(q(long_usd + short_usd, Q2)),
                             "long_count": sum(e["side"] == "long" for e in sel),
                             "short_count": sum(e["side"] == "short" for e in sel),
                             "window_ms": span, "covered_ms": covered, "complete_coverage": covered >= span}
        largest = sorted(events, key=lambda e: e["notional"], reverse=True)[:top]
        has_coverage = bool(spans)
        return {"venue": venue, "symbol": symbol, "source": SOURCES.get(venue), "lower_bound": True,
                "label": LABELS.get(venue), "timestamp": now,
                "windows": windows if has_coverage else None,
                "largest": [_event_json(e) for e in largest],
                "coverage": {"since": spans[0][0] if spans else None,
                             "connected": bool(spans and spans[-1][1] is None), "spans": spans[-10:]},
                "reason": None if has_coverage else "liquidation collector has not run for this market yet"}
