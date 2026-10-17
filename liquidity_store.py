"""Separate SQLite archive and opt-in lease-driven sampled-book collector.

No file, thread, configuration or network activity occurs at import time.
"""
from __future__ import annotations

import itertools
import json
import math
import sqlite3
import threading
import time
import zlib
from pathlib import Path

SAMPLE_PERIOD_MS = 10000
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_QUERY_MS = 604800000


def _integer(value, name, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"invalid {name}")
    return value


def _text(value, name, limit=80):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"invalid {name}")
    if any(ord(c) < 32 for c in value):
        raise ValueError(f"invalid {name}")
    return value


def _snapshot_json(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")  # noqa: TRY004 - uniform API validation error
    _text(snapshot.get("exchange"), "exchange")
    _text(snapshot.get("symbol"), "symbol")
    _integer(snapshot.get("observed_at"), "observed_at")
    if snapshot.get("time") is not None:
        _integer(snapshot["time"], "time")
    for side in ("bids", "asks"):
        levels = snapshot.get(side)
        if not isinstance(levels, list) or not 1 <= len(levels) <= 5000:
            raise ValueError(f"invalid {side}")
        for pair in levels:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("invalid price/quantity pair")
            try:
                finite = all(type(v) in (float, int) and math.isfinite(v) for v in pair)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError("non-finite price/quantity")
            if pair[0] <= 0 or pair[1] < 0:
                raise ValueError("invalid price/quantity")
    warnings = snapshot.get("warnings", [])
    if (not isinstance(warnings, list) or len(warnings) > 100
            or any(not isinstance(w, str) or len(w) > 2000 for w in warnings)):
        raise ValueError("invalid warnings")
    try:
        raw = json.dumps(snapshot, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("invalid JSON snapshot") from exc
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds 2 MiB uncompressed limit")
    return raw


def _decode(payload):
    try:
        dec = zlib.decompressobj()
        raw = dec.decompress(payload, MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES or not dec.eof or dec.unused_data:
            raise ValueError("invalid compressed snapshot")
        result = json.loads(raw)
        _snapshot_json(result)
        return result
    except (zlib.error, UnicodeError, TypeError, RecursionError) as exc:
        raise ValueError("invalid stored snapshot") from exc


class LiquidityStore:
    """Caller owns path and lifecycle; never point this at signals.db."""

    def __init__(self, path, retention_seconds=604800, max_bytes=134217728):
        _integer(retention_seconds, "retention_seconds", 1)
        _integer(max_bytes, "max_bytes", 1)
        self.path = str(path)
        if Path(self.path).name == "signals.db":
            raise ValueError("liquidity archive must be separate from signals.db")
        self.retention_seconds = retention_seconds
        self.max_bytes = max_bytes
        self._lock = threading.RLock()
        self._closed = False
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=5)
        try:
            # Must precede schema creation: incremental vacuum without full VACUUM.
            self._db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA journal_size_limit=4194304")
            self._db.execute("PRAGMA wal_autocheckpoint=128")
            self._db.execute("""CREATE TABLE IF NOT EXISTS observations (
                exchange TEXT NOT NULL, symbol TEXT NOT NULL,
                observed_at INTEGER NOT NULL, payload BLOB NOT NULL,
                PRIMARY KEY (exchange, symbol, observed_at))""")
            self._db.execute("CREATE INDEX IF NOT EXISTS observations_time "
                             "ON observations (observed_at)")
            self._db.commit()
        except Exception:
            self._db.close()
            raise

    def append(self, snapshot, now_ms=None):
        """True on insert, False on identical duplicate; conflicting key raises."""
        raw = _snapshot_json(snapshot)
        payload = zlib.compress(raw)
        if len(payload) > self.max_bytes:
            raise ValueError("snapshot exceeds compressed archive byte cap")
        now = _integer(int(time.time() * 1000) if now_ms is None else now_ms, "now_ms")
        key = (snapshot["exchange"], snapshot["symbol"], snapshot["observed_at"])
        if key[2] > now + SAMPLE_PERIOD_MS:
            raise ValueError("observation is in the future")
        with self._lock:
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                existing = self._db.execute(
                    "SELECT payload FROM observations WHERE exchange=? AND symbol=? "
                    "AND observed_at=?", key).fetchone()
                if existing and existing[0] != payload:
                    raise ValueError("conflicting payload for observation key")
                inserted = existing is None
                if inserted:
                    self._db.execute("INSERT INTO observations VALUES (?, ?, ?, ?)",
                                     (*key, payload))
                self._db.execute("DELETE FROM observations WHERE observed_at < ?",
                                 (now - self.retention_seconds * 1000,))
                total = self._db.execute(
                    "SELECT COALESCE(SUM(length(payload)), 0) FROM observations").fetchone()[0]
                if total > self.max_bytes:
                    for rowid, size in self._db.execute(
                            "SELECT rowid, length(payload) FROM observations "
                            "ORDER BY observed_at, rowid").fetchall():
                        self._db.execute("DELETE FROM observations WHERE rowid=?", (rowid,))
                        total -= size
                        if total <= self.max_bytes:
                            break
            self._db.execute("PRAGMA incremental_vacuum(64)")
        return inserted

    def history(self, exchange, symbol, start_ms, end_ms, max_frames=360):
        _text(exchange, "exchange")
        _text(symbol, "symbol")
        _integer(start_ms, "start_ms")
        _integer(end_ms, "end_ms")
        _integer(max_frames, "max_frames", 10, 600)
        if end_ms < start_ms or end_ms - start_ms > MAX_QUERY_MS:
            raise ValueError("history range must be ordered and at most seven days")
        width = max(1, (end_ms - start_ms + 1 + max_frames - 1) // max_frames)
        params = (exchange, symbol, start_ms, end_ms)
        # Keep range metadata and sampled blobs in one WAL read transaction.
        with self._lock, self._db:
            self._db.execute("BEGIN")
            times = [r[0] for r in self._db.execute(
                "SELECT observed_at FROM observations WHERE exchange=? AND symbol=? "
                "AND observed_at>=? AND observed_at<=? ORDER BY observed_at", params)]
            rows = self._db.execute(
                "SELECT observed_at, payload FROM observations WHERE exchange=? AND symbol=? "
                "AND observed_at IN (SELECT MAX(observed_at) FROM observations "
                "WHERE exchange=? AND symbol=? AND observed_at>=? AND observed_at<=? "
                "GROUP BY ((observed_at - ?) / ?)) ORDER BY observed_at",
                (exchange, symbol, *params, start_ms, width)).fetchall()
        books, warnings, invalid_times = [], [], set()
        for observed_at, payload in rows:
            try:
                b = _decode(payload)
                if (b["exchange"], b["symbol"], b["observed_at"]) != (
                        exchange, symbol, observed_at):
                    raise ValueError("stored key mismatch")
                books.append(b)
            except ValueError:
                invalid_times.add(observed_at)
                warnings.append(f"Unreadable stored observation at {observed_at}; omitted")
        times = [at for at in times if at not in invalid_times]
        if not times:
            warnings.append("No recorded snapshots in requested range")
        gaps = [{"start": a, "end": b} for a, b in itertools.pairwise(times)
                if b - a > 2 * SAMPLE_PERIOD_MS]
        if gaps:
            warnings.append("Collection gaps present; do not extend books across gaps")
        return {"exchange": exchange, "symbol": symbol,
                    "coverage_start": times[0] if times else None,
                    "coverage_end": times[-1] if times else None,
                    "samples": len(times),
                    "frames": [{k: b[k] for k in ("observed_at", "bids", "asks")} for b in books],
                    "latest": books[-1] if books else None, "warnings": warnings,
                    "gaps": gaps, "sample_period_ms": SAMPLE_PERIOD_MS,
                    "frame_bucket_ms": width}

    def stats(self):
        with self._lock:
            count, size, first, last = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(length(payload)), 0), "
                "MIN(observed_at), MAX(observed_at) FROM observations").fetchone()
            pages = self._db.execute("PRAGMA page_count").fetchone()[0]
            page_size = self._db.execute("PRAGMA page_size").fetchone()[0]
            free_pages = self._db.execute("PRAGMA freelist_count").fetchone()[0]
            def file_size(suffix):
                try:
                    return Path(self.path + suffix).stat().st_size
                except FileNotFoundError:
                    return 0
            return {"samples": count, "compressed_bytes": size, "max_bytes": self.max_bytes,
                        "retention_seconds": self.retention_seconds,
                        "coverage_start": first, "coverage_end": last,
                        "database_bytes": file_size(""), "wal_bytes": file_size("-wal"),
                        "shm_bytes": file_size("-shm"), "logical_database_bytes": pages * page_size,
                        "free_bytes": free_pages * page_size,
                        "warnings": ["Byte cap covers compressed payloads, not SQLite pages, indexes or WAL"]}

    def close(self):
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True


class CapacityError(ValueError):
    """A watch cannot fit without evicting another owner's subscription."""


class LiquidityCollector:
    """Serial bounded collector; fetch adapter must enforce its own 8s timeout.

    Construction and watch never launch threads. start() is an explicit parent
    application action. collect_once() is synchronous and deterministic in tests.
    """

    def __init__(self, store, fetch_snapshot, period_seconds=10, max_markets=6,
                 lease_seconds=90, *, clock_ms=None):
        for name, value in (("period_seconds", period_seconds), ("lease_seconds", lease_seconds)):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid {name}")
        _integer(max_markets, "max_markets", 1, 6)
        if not callable(fetch_snapshot):
            raise ValueError("fetch_snapshot must be callable")  # noqa: TRY004 - uniform validation error
        self.store = store
        self.fetch_snapshot = fetch_snapshot
        self.period_ms = max(1, int(period_seconds * 1000))
        self.lease_ms = max(1, int(lease_seconds * 1000))
        self.max_markets = max_markets
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.RLock()
        self._collect_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._markets = {}
        self._service_error = None

    def _expire(self, now):
        for key, state in list(self._markets.items()):
            state["leases"] = {o: end for o, end in state["leases"].items() if end > now}
            if not state["leases"] and not state["inflight"]:
                del self._markets[key]

    def watch(self, exchange, symbol, owner):
        _text(exchange, "exchange")
        _text(symbol, "symbol")
        _text(owner, "owner")
        key = (exchange, symbol)
        with self._lock:
            now = self._clock_ms()
            self._expire(now)
            state = self._markets.get(key)
            if state is None and len(self._markets) >= self.max_markets:
                raise CapacityError("maximum active markets reached")
            subscriptions = sum(len(s["leases"]) for s in self._markets.values())
            if subscriptions >= 24 and (state is None or owner not in state["leases"]):
                raise CapacityError("maximum 24 owner subscriptions reached")
            if state is None:
                state = {"leases": {}, "next_due": 0, "inflight": False,
                             "last_observed_at": None, "last_attempt_at": None, "error": None}
                self._markets[key] = state
            state["leases"][owner] = now + self.lease_ms
            return self.status(exchange, symbol)

    def unwatch(self, exchange, symbol, owner):
        _text(exchange, "exchange")
        _text(symbol, "symbol")
        _text(owner, "owner")
        with self._lock:
            state = self._markets.get((exchange, symbol))
            if state:
                state["leases"].pop(owner, None)
            return self.status(exchange, symbol)

    def status(self, exchange, symbol):
        _text(exchange, "exchange")
        _text(symbol, "symbol")
        with self._lock:
            now = self._clock_ms()
            self._expire(now)
            state = self._markets.get((exchange, symbol))
            active = bool(state and state["leases"])
            error = state["error"] if state else None
            return {"exchange": exchange, "symbol": symbol, "active": active,
                        "owners": len(state["leases"]) if state else 0,
                        "expires_at": max(state["leases"].values()) if active else None,
                        "last_observed_at": state["last_observed_at"] if state else None,
                        "last_success": state["last_observed_at"] if state else None,
                        "collecting": active and bool(self._thread and self._thread.is_alive())
                        and not self._stop.is_set(),
                        "last_attempt_at": state["last_attempt_at"] if state else None,
                        "unavailable": bool(error), "error": error,
                        "inflight": bool(state and state["inflight"]),
                        "running": bool(self._thread and self._thread.is_alive()),
                        "stale": not state or state["last_observed_at"] is None
                        or now - state["last_observed_at"] > 2 * self.period_ms,
                        "period_ms": self.period_ms, "lease_seconds": self.lease_ms / 1000,
                        "warnings": ([error] if error else [])
                        + ([self._service_error] if self._service_error else [])
                        + ["Renew watch every 45s; collection stops after the 90s lease expires"]}

    def collect_once(self, now_ms=None):
        """Attempt due leased markets once; no catch-up bursts or overlapping calls."""
        if now_ms is not None:
            _integer(now_ms, "now_ms")
        if not self._collect_lock.acquire(blocking=False):
            return 0
        collected = 0
        try:
            with self._lock:
                now = self._clock_ms() if now_ms is None else now_ms
                self._expire(now)
                keys = list(self._markets)
            for key in keys:
                if self._stop.is_set():
                    break
                with self._lock:
                    now = self._clock_ms() if now_ms is None else now_ms
                    self._expire(now)
                    state = self._markets.get(key)
                    if (not state or not state["leases"] or state["inflight"]
                            or now < state["next_due"]):
                        continue
                    state["inflight"] = True
                    state["last_attempt_at"] = now
                observed_at, error = None, None
                try:
                    snapshot = self.fetch_snapshot(*key)
                    if not isinstance(snapshot, dict) or (
                            snapshot.get("exchange"), snapshot.get("symbol")) != key:
                        raise ValueError("fetch returned a different market")
                    finished = self._clock_ms() if now_ms is None else now_ms
                    self.store.append(snapshot, now_ms=finished)
                    observed_at = snapshot["observed_at"]
                    collected += 1
                except Exception as exc:  # noqa: BLE001 - isolate arbitrary injected adapter failures
                    error = f"{type(exc).__name__}: {str(exc)[:500]}"
                finally:
                    with self._lock:
                        finished = self._clock_ms() if now_ms is None else now_ms
                        state["inflight"] = False
                        state["next_due"] = finished + self.period_ms
                        state["error"] = error
                        if observed_at is not None:
                            state["last_observed_at"] = observed_at
                        self._expire(finished)
            return collected
        finally:
            self._collect_lock.release()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.collect_once()
                self._service_error = None
            except Exception as exc:  # noqa: BLE001 - expose service errors instead of losing worker
                self._service_error = f"Collector error: {type(exc).__name__}: {str(exc)[:500]}"
            self._stop.wait(1)

    def start(self):
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="liquidity-collector", daemon=True)
            self._thread.start()

    def stop(self):
        """Join the serial worker; parent closes the store only after this returns."""
        with self._lifecycle_lock:
            self._stop.set()
            if self._thread and self._thread is not threading.current_thread():
                self._thread.join()
            # Also wait for a caller-driven collect_once already in flight.
            with self._collect_lock:
                pass
