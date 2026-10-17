"""Sampled books are observations, never synthetic liquidity."""
import importlib
import math
import sqlite3

import pytest

from liquidity_store import LiquidityStore


@pytest.fixture
def store(tmp_path):
    result = LiquidityStore(tmp_path / "books.sqlite")
    yield result
    result.close()


def test_duplicate_is_idempotent_but_fork_rejected(store):
    assert store.append(book(), now_ms=100_000)
    assert store.append(book(), now_ms=100_000) is False
    fork = book()
    fork["bids"][0][1] = 5
    with pytest.raises(ValueError, match="conflict"):
        store.append(fork, now_ms=100_000)
    assert store.history("binance", "BTCUSDT", 0, 100_000)["latest"] == book()


@pytest.mark.parametrize("field,value", [
    ("exchange", ""), ("symbol", "x" * 81), ("observed_at", True),
    ("observed_at", -1), ("time", math.inf), ("bids", [[99, math.nan]]),
    ("asks", [[0, 1]]), ("bids", [[99, -1]]), ("asks", []),
    ("warnings", "bad"), ("extra", {"bad": math.inf}),
    ("extra", "x" * 2_100_000), ("bids", [[10**400, 1]]),
    ("observed_at", 2**64), ("asks", [[101, True]]),
], ids=lambda value: str(value)[:40])
def test_invalid_snapshots_rejected(store, field, value):
    snapshot = book()
    snapshot[field] = value
    with pytest.raises(ValueError):
        store.append(snapshot, now_ms=100_000)


def test_time_bucket_keeps_one_real_book_and_reports_gaps(store):
    for at in [1000, 1100, 2000, 90000]:
        snapshot = book(at)
        snapshot["bids"][0][1] = at
        store.append(snapshot, now_ms=100000)
    result = store.history("binance", "BTCUSDT", 0, 100000, max_frames=10)
    assert result["samples"] == 4
    assert [b["observed_at"] for b in result["frames"]] == [2000, 90000]
    assert result["frames"][0]["bids"][0][1] == 2000
    assert result["gaps"] == [{"start": 2000, "end": 90000}]
    assert result["coverage_start"] == 1000
    earlier = store.history("binance", "BTCUSDT", 0, 3000)
    assert earlier["latest"]["observed_at"] == 2000
    assert store.history("none", "NONE", 0, 100000)["frames"] == []


@pytest.mark.parametrize("start,end,frames", [(-1, 1, 10), (2, 1, 10),
    (0, 604800001, 10), (0, 1, 9), (0, 1, 601), (0, True, 10)])
def test_bounded_history(store, start, end, frames):
    with pytest.raises(ValueError):
        store.history("binance", "BTCUSDT", start, end, frames)


def test_retention_and_payload_cap(tmp_path):
    store = LiquidityStore(tmp_path / "small.sqlite", retention_seconds=1, max_bytes=500)
    store.append(book(1000), now_ms=1000)
    store.append(book(3000), now_ms=3000)
    assert store.history("binance", "BTCUSDT", 0, 4000)["samples"] == 1
    for at in range(3010, 3200, 10):
        store.append(book(at), now_ms=at)
    stats = store.stats()
    assert stats["compressed_bytes"] <= 500
    assert stats["samples"] < 20
    assert stats["database_bytes"] > 0
    assert stats["max_bytes"] == 500
    store.close()
    db = sqlite3.connect(tmp_path / "small.sqlite")
    assert db.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
    db.close()


def test_collector_refcounts_period_expiration_and_recovery(store):
    module = importlib.import_module("liquidity_store")
    now = [100_000]
    calls = []
    fail = [False]
    def fetch(exchange, symbol):
        calls.append((exchange, symbol))
        if fail[0]:
            raise RuntimeError("upstream unavailable")
        return book(now[0])
    collector = module.LiquidityCollector(store, fetch, clock_ms=lambda: now[0])
    assert collector.watch("binance", "BTCUSDT", "a")["owners"] == 1
    assert collector.watch("binance", "BTCUSDT", "b")["owners"] == 2
    collector.collect_once(now_ms=now[0])
    collector.collect_once(now_ms=now[0])
    assert len(calls) == 1
    assert collector.status("binance", "BTCUSDT")["last_observed_at"] == 100000
    now[0] += 10000
    fail[0] = True
    collector.collect_once(now_ms=now[0])
    status = collector.status("binance", "BTCUSDT")
    assert status["unavailable"] is True
    assert status["last_observed_at"] == 100000
    assert store.stats()["samples"] == 1
    fail[0] = False
    now[0] += 10000
    collector.collect_once(now_ms=now[0])
    assert collector.status("binance", "BTCUSDT")["unavailable"] is False
    collector.unwatch("binance", "BTCUSDT", "a")
    assert collector.status("binance", "BTCUSDT")["owners"] == 1
    now[0] += 90001
    collector.collect_once(now_ms=now[0])
    assert len(calls) == 3
    assert collector.status("binance", "BTCUSDT")["active"] is False
    assert store.stats()["samples"] == 2
    collector.stop()


def test_collector_capacities_and_renewal(store):
    module = importlib.import_module("liquidity_store")
    now = [100000]
    collector = module.LiquidityCollector(store, lambda e, s: book(), clock_ms=lambda: now[0])
    for i in range(6):
        collector.watch("binance", f"X{i}", "owner")
    with pytest.raises(ValueError):
        collector.watch("binance", "seventh", "owner")
    for i in range(18):
        collector.watch("binance", "X0", str(i))
    with pytest.raises(ValueError):
        collector.watch("binance", "X0", "overflow")
    with pytest.raises(ValueError):
        collector.watch("binance", "X0", "x" * 81)
    now[0] += 45000
    collector.watch("binance", "X0", "owner")
    now[0] += 45001
    assert collector.status("binance", "X0")["owners"] == 1
    assert collector.status("binance", "X1")["active"] is False
    collector.stop()


def test_collector_no_overlap_and_restart(store):
    import threading
    module = importlib.import_module("liquidity_store")
    entered, release, second = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def fetch(exchange, symbol):
        calls.append(1)
        entered.set()
        assert release.wait(2)
        if len(calls) == 2:
            second.set()
        return book(100000 + len(calls))
    collector = module.LiquidityCollector(store, fetch, period_seconds=0.001,
                                          clock_ms=lambda: 100000)
    collector.watch("binance", "BTCUSDT", "owner")
    collector.start()
    collector.start()
    assert entered.wait(2)
    collector.collect_once(now_ms=100000)
    assert len(calls) == 1
    release.set()
    collector.stop()
    assert collector.status("binance", "BTCUSDT")["running"] is False
    # A deterministic due clock, not a test sleep, makes restart collect again.
    collector._clock_ms = lambda: 110000
    collector.start()
    assert second.wait(2)
    collector.stop()
    assert len(calls) == 2


def test_collector_rejects_wrong_market(store):
    module = importlib.import_module("liquidity_store")
    collector = module.LiquidityCollector(store, lambda e, s: book(), clock_ms=lambda: 100000)
    collector.watch("bybit", "BTCUSDT", "pane")
    collector.collect_once(now_ms=100000)
    assert collector.status("bybit", "BTCUSDT")["unavailable"]
    assert store.stats()["samples"] == 0
    collector.stop()


def test_status_aliases_and_capacity_exception(store):
    module = importlib.import_module("liquidity_store")
    collector = module.LiquidityCollector(store, lambda e, s: book(), max_markets=1,
                                          clock_ms=lambda: 100000)
    status = collector.watch("binance", "BTCUSDT", "pane")
    assert status["collecting"] is False
    assert status["last_success"] is None
    with pytest.raises(module.CapacityError):
        collector.watch("bybit", "BTCUSDT", "pane")
    collector.collect_once(now_ms=100000)
    assert collector.status("binance", "BTCUSDT")["last_success"] == 100000
    collector.stop()
    collector.stop()


def test_corrupt_observation_is_not_genuine_coverage(store):
    store.append(book(1000), now_ms=100000)
    store.append(book(90000), now_ms=100000)
    with store._db:
        store._db.execute("UPDATE observations SET payload=? WHERE observed_at=?", (b"broken", 90000))
    result = store.history("binance", "BTCUSDT", 0, 100000)
    assert result["coverage_end"] == 1000
    assert result["samples"] == 1
    assert "Unreadable" in " ".join(result["warnings"])


def test_real_time_default_retention_and_bounded_frames(store, monkeypatch):
    monkeypatch.setattr("liquidity_store.time.time", lambda: 1000)
    for at in range(1000, 1300):
        store.append(book(at))
    result = store.history("binance", "BTCUSDT", 0, 2000, 10)
    assert len(result["frames"]) <= 10
    assert result["samples"] == 300
    assert result["latest"]["observed_at"] == 1299
    store.close()
    store.close()


def test_exchange_timestamp_may_be_unknown(store):
    snapshot = book()
    snapshot["time"] = None
    store.append(snapshot, now_ms=100000)
    assert store.history("binance", "BTCUSDT", 0, 100000)["latest"]["time"] is None


def test_two_connections_are_threadsafe_and_sql_parameters_literal(tmp_path):
    import concurrent.futures
    path = tmp_path / "concurrent.sqlite"
    stores = [LiquidityStore(path), LiquidityStore(path)]
    snapshot = book()
    snapshot["symbol"] = "BTC'; DROP TABLE observations;--"
    def save(i):
        return stores[i % 2].append(snapshot, now_ms=100000)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(save, range(12)))
    assert outcomes.count(True) == 1
    assert stores[0].history("binance", snapshot["symbol"], 0, 100000)["samples"] == 1
    readonly = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    assert readonly.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    readonly.close()
    for archive in stores:
        archive.close()


def test_store_constructor_and_payload_limit(tmp_path):
    with pytest.raises(ValueError):
        LiquidityStore(tmp_path / "signals.db")
    for kwargs in [{"max_bytes": 0}, {"retention_seconds": True}]:
        with pytest.raises(ValueError):
            LiquidityStore(tmp_path / "bad.sqlite", **kwargs)
    archive = LiquidityStore(tmp_path / "tiny.sqlite", max_bytes=1)
    with pytest.raises(ValueError, match="cap"):
        archive.append(book(), now_ms=100000)
    assert archive.stats()["samples"] == 0
    archive.close()


def book(at=100_000, **extra):
    return dict(exchange="binance", symbol="BTCUSDT", observed_at=at,
                time=at, bids=[[99.0, 2.0]], asks=[[101.0, 3.0]],
                warnings=[], **extra)


def test_persistent_roundtrip(tmp_path):
    module = importlib.import_module("liquidity_store")
    path = tmp_path / "books.sqlite"
    store = module.LiquidityStore(path)
    assert store.append(book(), now_ms=100_000) is True
    store.close()
    reopened = module.LiquidityStore(path)
    result = reopened.history("binance", "BTCUSDT", 0, 100_000)
    assert result["latest"] == book()
    assert result["frames"] == [{"observed_at": 100_000,
                                  "bids": [[99.0, 2.0]], "asks": [[101.0, 3.0]]}]
    assert result["samples"] == 1
    reopened.close()
