"""SQLite persistence of the liquidation store: restart survival, retention, size cap, honest coverage."""
import sqlite3
from decimal import Decimal

import pytest

from liquidations_collector import LiquidationCollector
from liquidations_store import HEARTBEAT_WRITE_MS, LiquidationStore, default_path

H = 3_600_000


def ev(side, price, ts, qty="1", venue="binance", symbol="BTCUSDT"):
    p, q = Decimal(price), Decimal(qty)
    return {"venue": venue, "symbol": symbol, "side": side, "price": p, "qty": q, "notional": p * q, "ts": ts}


def rows(path, table):
    with sqlite3.connect(path) as db:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_events_and_closed_coverage_survive_a_restart(tmp_path):
    path, now = str(tmp_path / "liq.db"), 100 * H
    a = LiquidationStore(clock_ms=lambda: now, path=path)
    a.cover_start("binance", "BTCUSDT", now - 2 * H)
    a.add([ev("long", "80000.5", now - 10, "0.25"), ev("short", "250.5", now - H // 2)], now_ms=now)
    a.cover_end("binance", "BTCUSDT", now - 60_000)
    before = a.aggregate("binance", "BTCUSDT", now_ms=now)
    a.close()
    b = LiquidationStore(clock_ms=lambda: now, path=path)
    after = b.aggregate("binance", "BTCUSDT", now_ms=now)
    assert after["windows"] == before["windows"]
    assert after["largest"] == before["largest"]
    assert after["largest"][0] == {"side": "long", "price": "80000.5", "qty": "0.25", "notional_usd": "20000.12",
                                   "ts": now - 10}
    assert after["coverage"]["spans"] == [[now - 2 * H, now - 60_000]]
    assert after["coverage"]["connected"] is False
    assert after["windows"]["4h"]["covered_ms"] == 2 * H - 60_000 and after["windows"]["4h"]["complete_coverage"] is False


def test_open_span_after_a_crash_ends_at_the_last_heartbeat_not_at_restart(tmp_path):
    path, t = str(tmp_path / "liq.db"), [100 * H]
    a = LiquidationStore(clock_ms=lambda: t[0], path=path)
    a.cover_start("bybit", "ETHUSDT", t[0])
    a.heartbeat("bybit", {"ETHUSDT"}, t[0] + HEARTBEAT_WRITE_MS - 1)       # throttled: not written
    a.heartbeat("bybit", {"ETHUSDT"}, t[0] + 30 * 60_000)                   # written
    a.heartbeat("bybit", {"ETHUSDT"}, t[0] + 30 * 60_000 + 5_000)           # throttled
    start = t[0]
    # process dies without cover_end; restart two hours later
    t[0] = start + 2 * H
    b = LiquidationStore(clock_ms=lambda: t[0], path=path)
    agg = b.aggregate("bybit", "ETHUSDT", now_ms=t[0])
    assert agg["coverage"]["spans"] == [[start, start + 30 * 60_000]]
    assert agg["coverage"]["connected"] is False
    assert agg["windows"]["4h"]["covered_ms"] == 30 * 60_000          # not 2 h: the gap is not claimed
    assert agg["windows"]["1h"]["covered_ms"] == 0
    # a new connection after restart opens a fresh span
    b.cover_start("bybit", "ETHUSDT", t[0])
    assert b.aggregate("bybit", "ETHUSDT", now_ms=t[0] + H)["windows"]["1h"]["covered_ms"] == H


def test_crash_before_any_heartbeat_claims_no_coverage(tmp_path):
    path = str(tmp_path / "liq.db")
    a = LiquidationStore(clock_ms=lambda: 100 * H, path=path)
    a.cover_start("binance", "SOLUSDT", 100 * H)
    b = LiquidationStore(clock_ms=lambda: 101 * H, path=path)
    agg = b.aggregate("binance", "SOLUSDT", now_ms=101 * H)
    assert agg["windows"] is None and "not run" in agg["reason"]


def test_retention_prunes_the_file_on_write_and_on_load(tmp_path):
    path, t = str(tmp_path / "liq.db"), [100 * H]
    a = LiquidationStore(clock_ms=lambda: t[0], path=path)
    a.cover_start("binance", "BTCUSDT", t[0] - 3 * H)
    a.cover_end("binance", "BTCUSDT", t[0] - 2 * H)
    a.cover_start("binance", "BTCUSDT", t[0] - H)
    a.add([ev("long", "1", t[0] - 23 * H), ev("long", "2", t[0] - H)], now_ms=t[0])
    assert rows(path, "liq_events") == 2 and rows(path, "liq_coverage") == 2
    t[0] += 22 * H + 30 * 60_000   # first event now 23.5 h + 22 h old; the closed span ended 24.5 h ago
    a.add([ev("short", "3", t[0])], now_ms=t[0])
    assert rows(path, "liq_events") == 2              # the 45 h-old event is gone from the file
    a.cover_end("binance", "BTCUSDT", t[0])
    assert rows(path, "liq_coverage") == 1            # the span that ended > 24 h ago is gone
    a.close()
    t[0] += 23 * H
    b = LiquidationStore(clock_ms=lambda: t[0], path=path)
    assert rows(path, "liq_events") == 1              # load prunes too
    agg = b.aggregate("binance", "BTCUSDT", now_ms=t[0])
    assert agg["windows"]["24h"]["short_count"] == 1 and agg["windows"]["24h"]["long_count"] == 0


def test_per_market_size_cap_applies_to_the_file(tmp_path):
    path, now = str(tmp_path / "liq.db"), 100 * H
    a = LiquidationStore(max_events_per_market=3, clock_ms=lambda: now, path=path)
    a.add([ev("long", str(i), now - 100 + i) for i in range(1, 8)], now_ms=now)
    a.add([ev("long", "9", now, venue="bybit")], now_ms=now)
    with sqlite3.connect(path) as db:
        kept = db.execute("SELECT price FROM liq_events WHERE venue='binance' ORDER BY ts").fetchall()
    assert [r[0] for r in kept] == ["5", "6", "7"]
    assert rows(path, "liq_events") == 4
    b = LiquidationStore(max_events_per_market=3, clock_ms=lambda: now, path=path)
    b.cover_start("binance", "BTCUSDT", now - H)
    assert b.aggregate("binance", "BTCUSDT", now_ms=now)["windows"]["1h"]["long_usd"] == "18"


def test_collector_loop_writes_heartbeats(tmp_path):
    class WS:
        def __init__(self):
            self.n = 0

        def send_text(self, text):
            pass

        def recv(self):
            import socket
            raise socket.timeout()

        def close(self):
            pass

    path, t = str(tmp_path / "liq.db"), [100 * H]

    def clock():
        t[0] += 6_000
        return t[0]

    store = LiquidationStore(clock_ms=clock, path=path)
    col = LiquidationCollector(store, connect=lambda url: WS(), clock_ms=clock, lease_ms=10 * H)
    col.watch("binance", "BTCUSDT")
    col.run_venue("binance", max_loops=6)
    with sqlite3.connect(path) as db:
        start, end, seen = db.execute("SELECT start_ms, end_ms, seen_ms FROM liq_coverage").fetchone()
    assert end is not None and seen == end and seen - start > HEARTBEAT_WRITE_MS


def test_default_path_and_signals_db_guard(tmp_path, monkeypatch):
    monkeypatch.delenv("LIQUIDATIONS_DB_PATH", raising=False)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "data" / "signals.db"))
    assert default_path() == str(tmp_path / "data" / "liquidations.db")
    monkeypatch.setenv("LIQUIDATIONS_DB_PATH", "")
    assert default_path() is None
    monkeypatch.setenv("LIQUIDATIONS_DB_PATH", str(tmp_path / "x.db"))
    assert default_path() == str(tmp_path / "x.db")
    with pytest.raises(ValueError):
        LiquidationStore(path=str(tmp_path / "signals.db"))
    mem = LiquidationStore(clock_ms=lambda: 100 * H)
    mem.heartbeat("binance", {"BTCUSDT"})   # no file: a no-op
    assert mem.path is None
