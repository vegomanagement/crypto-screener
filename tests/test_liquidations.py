"""Liquidation store windows, lower-bound labelling, coverage and the lease-driven collector."""
import json
import socket
from decimal import Decimal
from pathlib import Path

import pytest

from liquidations_collector import LiquidationCapacityError, LiquidationCollector
from liquidations_store import HL_REASON, LiquidationStore, normalise_binance, normalise_bybit, parse_message
from positioning import NormaliseError

FIX = Path(__file__).parent / "fixtures" / "positioning"
H = 3_600_000


def ev(side, notional, ts, venue="binance", symbol="BTCUSDT"):
    return {"venue": venue, "symbol": symbol, "side": side, "price": Decimal(notional), "qty": Decimal(1),
            "notional": Decimal(notional), "ts": ts}


def test_binance_force_order_recorded_messages():
    msgs = json.loads((FIX / "binance_forceOrder_ws.json").read_text())
    events = [e for m in msgs for e in normalise_binance(m)]
    assert len(events) == len(msgs) > 0
    first = msgs[0]["o"]
    assert events[0]["symbol"] == first["s"]
    assert events[0]["side"] == ("long" if first["S"] == "SELL" else "short")   # SELL closes a long
    assert events[0]["notional"] == Decimal(first["ap"]) * Decimal(first["z"])
    assert parse_message("binance", "not json") == []


def test_bybit_all_liquidation_shape_and_side_semantics():
    # Response example from https://bybit-exchange.github.io/docs/v5/websocket/public/all-liquidation
    doc = {"topic": "allLiquidation.ROSEUSDT", "type": "snapshot", "ts": 1739502303204,
           "data": [{"T": 1739502302929, "s": "ROSEUSDT", "S": "Sell", "v": "20000", "p": "0.04499"}]}
    [e] = normalise_bybit(doc)
    assert e["side"] == "short" and e["notional"] == Decimal("899.80000")   # Buy = long liquidated
    assert normalise_bybit({"success": True, "op": "subscribe"}) == []
    with pytest.raises(NormaliseError):
        normalise_bybit({"topic": "allLiquidation.X", "data": [{"S": "Up", "v": "1", "p": "1", "s": "X", "T": 1}]})
    recorded = json.loads((FIX / "bybit_allLiquidation_ws.json").read_text())
    live = [e for m in recorded for e in normalise_bybit(m)]
    assert len(live) == 6
    assert (live[0]["symbol"], live[0]["side"], live[0]["notional"]) == ("ETHUSDT", "short", Decimal("24.1816"))


def test_store_windows_lower_bound_and_largest():
    now = 100 * H
    store = LiquidationStore(clock_ms=lambda: now)
    store.cover_start("binance", "BTCUSDT", now - 2 * H)
    store.add([ev("long", "1000", now - 10), ev("short", "250.5", now - H // 2),
               ev("long", "4000", now - 3 * H), ev("short", "7", now - 30 * H)], now_ms=now)
    agg = store.aggregate("binance", "BTCUSDT", now_ms=now)
    assert agg["lower_bound"] is True and "LOWER BOUND" in agg["label"]
    w1, w4 = agg["windows"]["1h"], agg["windows"]["4h"]
    assert (w1["long_usd"], w1["short_usd"], w1["long_count"], w1["short_count"]) == ("1000", "250.5", 1, 1)
    assert w4["long_usd"] == "5000" and w4["total_usd"] == "5250.5"
    assert w1["complete_coverage"] is True and w4["complete_coverage"] is False
    assert w4["covered_ms"] == 2 * H
    assert [x["notional_usd"] for x in agg["largest"]] == ["4000", "1000", "250.5"]   # 30h-old event pruned


def test_store_without_coverage_and_hyperliquid_reason():
    store = LiquidationStore(clock_ms=lambda: 10 * H)
    agg = store.aggregate("bybit", "ETHUSDT")
    assert agg["windows"] is None and "not run" in agg["reason"]
    hl = store.aggregate("hyperliquid", "BTC")
    assert hl["value"] is None and hl["reason"] == HL_REASON


def test_store_rejects_stale_and_future_events_and_caps_size():
    now = 50 * H
    store = LiquidationStore(max_events_per_market=3, clock_ms=lambda: now)
    assert store.add([ev("long", "1", now - 25 * H), ev("long", "1", now + 120_000)], now_ms=now) == 0
    store.add([ev("long", str(i), now - i) for i in range(1, 6)], now_ms=now)
    store.cover_start("binance", "BTCUSDT", now - H)
    assert store.aggregate("binance", "BTCUSDT", now_ms=now)["windows"]["1h"]["long_count"] == 3


class FakeWS:
    def __init__(self, messages):
        self.messages, self.sent, self.closed = list(messages), [], False

    def send_text(self, text):
        self.sent.append(json.loads(text))

    def recv(self):
        if self.messages:
            return self.messages.pop(0)
        raise socket.timeout()

    def close(self):
        self.closed = True


def test_collector_leases_gate_work_and_expire():
    t = [0]
    store = LiquidationStore(clock_ms=lambda: t[0] + 100 * H)
    col = LiquidationCollector(store, connect=lambda url: FakeWS([]), lease_ms=1000, clock_ms=lambda: t[0])
    assert col.wanted("binance") == []
    col.watch("binance", "BTCUSDT", "page-a")
    col.watch("binance", "BTCUSDT", "page-b")
    assert col.wanted("binance") == ["BTCUSDT"]
    t[0] = 1500
    assert col.wanted("binance") == []
    col.run_venue("binance")                # returns immediately: nothing wanted, nothing connected
    assert col.status("binance")["connects"] == 0
    with pytest.raises(ValueError):
        col.watch("hyperliquid", "BTC")


def test_collector_capacity_limit():
    col = LiquidationCollector(LiquidationStore(), connect=lambda url: FakeWS([]))
    for i in range(10):
        col.watch("bybit", f"S{i}USDT")
    with pytest.raises(LiquidationCapacityError):
        col.watch("bybit", "EXTRAUSDT")


def test_collector_bybit_subscribes_stores_and_records_coverage():
    doc = json.dumps({"topic": "allLiquidation.BTCUSDT", "type": "snapshot", "ts": 5,
                      "data": [{"T": 100 * H - 5, "s": "BTCUSDT", "S": "Buy", "v": "0.5", "p": "80000"}]})
    other = json.dumps({"topic": "allLiquidation.ETHUSDT", "data": [{"T": 1, "s": "ETHUSDT", "S": "Buy",
                                                                     "v": "1", "p": "1"}]})
    ws = FakeWS([doc, other, "{\"op\":\"pong\"}"])
    clock = lambda: 100 * H  # noqa: E731
    store = LiquidationStore(clock_ms=clock)
    col = LiquidationCollector(store, connect=lambda url: ws, clock_ms=clock)
    col.watch("bybit", "BTCUSDT")
    col.run_venue("bybit", max_loops=5)
    assert ws.sent[0] == {"op": "subscribe", "args": ["allLiquidation.BTCUSDT"]}
    assert ws.closed
    agg = store.aggregate("bybit", "BTCUSDT", now_ms=100 * H)
    assert agg["windows"]["1h"]["long_usd"] == "40000" and agg["windows"]["1h"]["long_count"] == 1
    assert agg["coverage"]["connected"] is False and agg["coverage"]["since"] == 100 * H
    assert col.status("bybit")["events"] == 1


def test_collector_reconnects_with_backoff_after_failure():
    attempts, slept = [], []

    def connect(url):
        attempts.append(url)
        raise OSError("network down")

    col = LiquidationCollector(LiquidationStore(), connect=connect, sleep=slept.append, backoff=(1, 2))
    col.watch("binance", "BTCUSDT")

    def stop_after_three(delay):
        slept.append(delay)
        if len(slept) >= 3:
            col._leases.clear()

    col._sleep = stop_after_three
    col.run_venue("binance")
    assert len(attempts) == 3 and slept == [1, 2, 2]
    assert "network down" in col.status("binance")["error"]
