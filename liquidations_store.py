"""In-memory rolling liquidation store (default 24 h) with coverage tracking.

Totals are only as complete as the venue stream and the time the collector was connected:
* Binance ``forceOrder`` pushes at most the latest liquidation per symbol per 1000 ms, so
  Binance totals are a LOWER BOUND even while connected.
* Bybit ``allLiquidation`` pushes all liquidations (500 ms batches) but the price is the
  bankruptcy price, so notional is approximate; totals cover only connected time.
* Hyperliquid has no public liquidation stream.
No threads, files or network at import time.
"""
from __future__ import annotations

import json
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
    def __init__(self, retention_ms=24 * HOUR_MS, max_events_per_market=20000, clock_ms=None):
        self.retention_ms = retention_ms
        self.max_events = max_events_per_market
        self._clock = clock_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.Lock()
        self._events = {}      # (venue, symbol) -> deque of events ascending by ts
        self._coverage = {}    # (venue, symbol) -> [[start, end_or_None], ...]

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
        added = 0
        with self._lock:
            for e in events:
                if e["ts"] < now - self.retention_ms or e["ts"] > now + 60_000:
                    continue
                dq = self._events.setdefault((e["venue"], e["symbol"]),
                                             deque(maxlen=self.max_events))
                dq.append(e)
                added += 1
            self._prune(now)
        return added

    def cover_start(self, venue, symbol, now_ms=None):
        now = self._clock() if now_ms is None else now_ms
        with self._lock:
            spans = self._coverage.setdefault((venue, symbol), [])
            if not spans or spans[-1][1] is not None:
                spans.append([now, None])

    def cover_end(self, venue, symbol, now_ms=None):
        now = self._clock() if now_ms is None else now_ms
        with self._lock:
            spans = self._coverage.get((venue, symbol)) or []
            if spans and spans[-1][1] is None:
                spans[-1][1] = now

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
