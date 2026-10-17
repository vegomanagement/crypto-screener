"""Lease-driven liquidation WS collector (Binance forceOrder, Bybit allLiquidation).

Nothing starts at import or construction. ``watch()`` adds a lease; ``ensure_running()``
starts one worker thread per venue that has leases. A worker exits (and closes its socket)
as soon as its venue has no unexpired lease, so collection only runs while a page asks.
"""
from __future__ import annotations

import json
import socket
import threading
import time

from liquidations_store import parse_message
from positioning import NormaliseError

URLS = {"binance": "wss://fstream.binance.com/market/ws/!forceOrder@arr",
        "bybit": "wss://stream.bybit.com/v5/public/linear"}
MAX_SYMBOLS_PER_VENUE = 10
MAX_OWNERS = 40


class LiquidationCapacityError(ValueError):
    pass


def _default_connect(url):
    from ws_client import WebSocket
    ws = WebSocket.connect(url, timeout=10)
    ws.settimeout(1.0)
    return ws


class LiquidationCollector:
    def __init__(self, store, *, connect=None, lease_ms=120_000, clock_ms=None,
                 ping_seconds=20, backoff=(2, 5, 15, 30, 60), sleep=time.sleep):
        self.store = store
        self.connect = connect or _default_connect
        self.lease_ms = lease_ms
        self._clock = clock_ms or (lambda: int(time.time() * 1000))
        self.ping_seconds = ping_seconds
        self.backoff = backoff
        self._sleep = sleep
        self._lock = threading.RLock()
        self._leases = {}        # venue -> symbol -> owner -> expiry
        self._threads = {}
        self._state = {v: {"connected": False, "error": None, "messages": 0, "events": 0,
                           "connects": 0, "last_message_at": None, "subscribed": []} for v in URLS}

    # ----- leases -----
    def _expire(self, now):
        for venue, symbols in self._leases.items():
            for symbol, owners in list(symbols.items()):
                for owner, end in list(owners.items()):
                    if end <= now:
                        del owners[owner]
                if not owners:
                    del symbols[symbol]

    def watch(self, venue, symbol, owner="positioning"):
        if venue not in URLS:
            raise ValueError("venue has no public liquidation stream")
        with self._lock:
            now = self._clock()
            self._expire(now)
            symbols = self._leases.setdefault(venue, {})
            if symbol not in symbols and len(symbols) >= MAX_SYMBOLS_PER_VENUE:
                raise LiquidationCapacityError("too many liquidation symbols watched")
            if sum(len(o) for s in self._leases.values() for o in s.values()) >= MAX_OWNERS \
                    and owner not in symbols.get(symbol, {}):
                raise LiquidationCapacityError("too many liquidation subscriptions")
            symbols.setdefault(symbol, {})[owner] = now + self.lease_ms
        return self.status(venue)

    def wanted(self, venue):
        with self._lock:
            self._expire(self._clock())
            return sorted(self._leases.get(venue, {}))

    def status(self, venue=None):
        with self._lock:
            venues = [venue] if venue else list(URLS)
            out = {}
            for v in venues:
                thread = self._threads.get(v)
                out[v] = {**self._state[v], "subscribed": list(self._state[v]["subscribed"]),
                          "running": bool(thread and thread.is_alive()), "watched": self.wanted(v)}
            return out[venue] if venue else out

    def ensure_running(self):
        with self._lock:
            for venue in URLS:
                thread = self._threads.get(venue)
                if self.wanted(venue) and not (thread and thread.is_alive()):
                    thread = threading.Thread(target=self.run_venue, args=(venue,),
                                              name=f"liq-{venue}", daemon=True)
                    self._threads[venue] = thread
                    thread.start()

    # ----- worker -----
    def handle(self, venue, text, wanted):
        """Parse one WS message and store events for watched symbols. Returns events stored."""
        st = self._state[venue]
        st["messages"] += 1
        st["last_message_at"] = self._clock()
        try:
            events = [e for e in parse_message(venue, text) if e["symbol"] in wanted]
        except (NormaliseError, KeyError, TypeError, ValueError) as exc:
            st["error"] = f"bad message: {str(exc)[:120]}"
            return 0
        added = self.store.add(events)
        st["events"] += added
        return added

    def _sync_bybit(self, ws, subscribed, wanted):
        add, drop = sorted(set(wanted) - subscribed), sorted(subscribed - set(wanted))
        now = self._clock()
        if add:
            ws.send_text(json.dumps({"op": "subscribe", "args": [f"allLiquidation.{s}" for s in add]}))
        if drop:
            ws.send_text(json.dumps({"op": "unsubscribe", "args": [f"allLiquidation.{s}" for s in drop]}))
        for s in add:
            self.store.cover_start("bybit", s, now)
        for s in drop:
            self.store.cover_end("bybit", s, now)
        return set(wanted)

    def run_venue(self, venue, max_loops=None):
        attempt, loops = 0, 0
        while self.wanted(venue):
            ws, covered = None, set()
            try:
                ws = self.connect(URLS[venue])
                st = self._state[venue]
                st.update(connected=True, error=None)
                st["connects"] += 1
                attempt = 0
                last_ping = time.monotonic()
                while True:
                    loops += 1
                    if max_loops is not None and loops > max_loops:
                        return
                    wanted = self.wanted(venue)
                    if not wanted:
                        return
                    if venue == "bybit":
                        covered = self._sync_bybit(ws, covered, wanted)
                        if time.monotonic() - last_ping >= self.ping_seconds:
                            ws.send_text('{"op":"ping"}')
                            last_ping = time.monotonic()
                    else:
                        now = self._clock()
                        for s in set(wanted) - covered:
                            self.store.cover_start(venue, s, now)
                        for s in covered - set(wanted):
                            self.store.cover_end(venue, s, now)
                        covered = set(wanted)
                    st["subscribed"] = sorted(covered)
                    try:
                        text = ws.recv()
                    except socket.timeout:
                        continue
                    self.handle(venue, text, set(wanted))
            except Exception as exc:  # noqa: BLE001 - worker must survive any socket failure
                self._state[venue]["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                delay = self.backoff[min(attempt, len(self.backoff) - 1)]
                attempt += 1
            else:
                delay = 0
            finally:
                now = self._clock()
                for s in covered:
                    self.store.cover_end(venue, s, now)
                self._state[venue].update(connected=False, subscribed=[])
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:  # noqa: BLE001
                        pass
            if max_loops is not None and loops >= max_loops:
                return
            if delay:
                self._sleep(delay)
