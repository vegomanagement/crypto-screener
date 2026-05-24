"""Unit tests for signal_gate.py — aggregator + cooldown gate."""

import sqlite3
import threading

import pytest

from signal_gate import (
    SignalAggregator,
    cooldown_check,
    cooldown_minutes,
    get_active_dispatch,
    init_schema,
    normalize_tf,
    record_dispatch,
    signal_type_priority,
    tf_priority,
    verdict_from_signal_type,
)


# ─── Helpers ──────────────────────────────────────────────────────────────


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    yield c
    c.close()


def _payload(signal: str, tf: str = "60", symbol: str = "BTCUSDT") -> dict:
    return {"signal": signal, "tf": tf, "symbol": symbol, "price": 100.0}


class _ImmediateTimer:
    """Synchronous fake Timer — run callback inline so tests are deterministic."""

    def __init__(self, _delay, fn, args=None, kwargs=None):
        self._fn = fn
        self._args = args or []
        self._kwargs = kwargs or {}
        self._cancelled = False

    def start(self):
        if not self._cancelled:
            self._fn(*self._args, **self._kwargs)

    def cancel(self):
        self._cancelled = True


class _ManualTimer:
    """Timer that never auto-fires — use aggregator.flush_now() in tests."""
    def __init__(self, _delay, fn, args=None, kwargs=None):
        self.fn = fn
        self.args = args or []
        self.kwargs = kwargs or {}
        self.cancelled = False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


# ─── Pure helpers ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("inp,expected", [
    ("60", "60"), ("1H", "60"), ("4H", "240"), ("4h", "240"),
    ("15M", "15"), ("D", "D"), ("1D", "D"), ("1W", "W"), ("", ""),
])
def test_normalize_tf(inp, expected):
    assert normalize_tf(inp) == expected


def test_tf_priority_ordering():
    assert tf_priority("5") < tf_priority("60") < tf_priority("240") < tf_priority("D")
    assert tf_priority("1H") == tf_priority("60")


def test_signal_type_priority():
    assert signal_type_priority("BOS_BULL") > signal_type_priority("CHOCH_BEAR")
    assert signal_type_priority("CHOCH_BULL") > signal_type_priority("OB_BULL")
    assert signal_type_priority("OB_BEAR") > signal_type_priority("FVG_BULL")
    assert signal_type_priority("ICT_NY_OPEN") < signal_type_priority("BOS_BULL")
    assert signal_type_priority("UNKNOWN_XYZ") == 0


@pytest.mark.parametrize("sig,verdict", [
    ("BOS_BULL", "LONG"),
    ("CHOCH_BEAR", "SHORT"),
    ("LIQ_SWEEP_L", "LONG"),
    ("LIQ_SWEEP_H", "SHORT"),
    ("ICT_NY_OPEN", None),
    ("FVG_FILLED", None),
])
def test_verdict_from_signal_type(sig, verdict):
    assert verdict_from_signal_type(sig) == verdict


def test_cooldown_minutes_scaling():
    assert cooldown_minutes("5") < cooldown_minutes("60") < cooldown_minutes("240")
    assert cooldown_minutes("D") == 1440


# ─── Cooldown gate ────────────────────────────────────────────────────────


def test_no_active_dispatch_sends(conn):
    g = cooldown_check(conn, "BTCUSDT", "LONG", confidence=70, tf="60")
    assert g.action == "send"
    assert g.active is None


def test_same_direction_same_tf_suppressed(conn):
    record_dispatch(conn, "BTCUSDT", "LONG", "60", "BOS_BULL", confidence=70)
    g = cooldown_check(conn, "BTCUSDT", "LONG", confidence=80, tf="60")
    assert g.action == "suppress"
    assert "already active LONG" in g.reason


def test_same_direction_higher_tf_reversal(conn):
    record_dispatch(conn, "BTCUSDT", "LONG", "5", "BOS_BULL", confidence=70)
    g = cooldown_check(conn, "BTCUSDT", "LONG", confidence=72, tf="240")
    assert g.action == "reversal"
    assert "upgrade" in g.reason


def test_same_direction_lower_tf_suppressed(conn):
    record_dispatch(conn, "BTCUSDT", "LONG", "240", "BOS_BULL", confidence=70)
    g = cooldown_check(conn, "BTCUSDT", "LONG", confidence=80, tf="5")
    assert g.action == "suppress"


def test_opposite_direction_below_threshold_suppressed(conn):
    record_dispatch(conn, "BTCUSDT", "LONG", "60", "BOS_BULL", confidence=70)
    g = cooldown_check(conn, "BTCUSDT", "SHORT", confidence=84, tf="60")
    assert g.action == "suppress"


def test_opposite_direction_above_threshold_reversal(conn):
    record_dispatch(conn, "BTCUSDT", "LONG", "60", "BOS_BULL", confidence=70)
    g = cooldown_check(conn, "BTCUSDT", "SHORT", confidence=86, tf="60")
    assert g.action == "reversal"
    assert g.active is not None
    assert g.active.verdict == "LONG"


def test_cooldown_expires(conn, monkeypatch):
    # Запишем dispatch со сроком в прошлом.
    conn.execute(
        """INSERT INTO signal_dispatch(symbol,verdict,tf,signal_type,confidence,
           sent_at,cooldown_until) VALUES (?,?,?,?,?,?,?)""",
        ("BTCUSDT", "LONG", "60", "BOS_BULL", 70,
         "2020-01-01 00:00:00", "2020-01-01 00:05:00"),
    )
    conn.commit()
    assert get_active_dispatch(conn, "BTCUSDT") is None
    g = cooldown_check(conn, "BTCUSDT", "SHORT", confidence=50, tf="60")
    assert g.action == "send"


def test_non_tradeable_verdict_passes(conn):
    record_dispatch(conn, "BTCUSDT", "LONG", "60", "BOS_BULL", confidence=70)
    g = cooldown_check(conn, "BTCUSDT", "WAIT", confidence=0, tf="60")
    assert g.action == "send"


# ─── Aggregator ───────────────────────────────────────────────────────────


def test_aggregator_picks_higher_tf():
    received = []

    def cb(winner, suppressed):
        received.append((winner, suppressed))

    agg = SignalAggregator(callback=cb, timer_factory=_ManualTimer)
    agg.submit("BTCUSDT", _payload("BOS_BULL", tf="5"))
    agg.submit("BTCUSDT", _payload("BOS_BEAR", tf="240"))
    agg.submit("BTCUSDT", _payload("CHOCH_BULL", tf="15"))

    agg.flush_now("BTCUSDT")

    assert len(received) == 1
    winner, suppressed = received[0]
    assert winner.tf == "240"
    assert winner.sig_type == "BOS_BEAR"
    assert len(suppressed) == 2


def test_aggregator_picks_higher_type_priority_at_same_tf():
    received = []
    agg = SignalAggregator(
        callback=lambda w, s: received.append((w, s)),
        timer_factory=_ManualTimer,
    )
    agg.submit("ETHUSDT", _payload("FVG_BULL", tf="60", symbol="ETHUSDT"))
    agg.submit("ETHUSDT", _payload("BOS_BULL", tf="60", symbol="ETHUSDT"))
    agg.submit("ETHUSDT", _payload("OB_BULL",  tf="60", symbol="ETHUSDT"))

    agg.flush_now("ETHUSDT")
    winner, _ = received[0]
    assert winner.sig_type == "BOS_BULL"


def test_aggregator_isolates_symbols():
    received = []
    agg = SignalAggregator(
        callback=lambda w, s: received.append((w.payload["symbol"], len(s))),
        timer_factory=_ManualTimer,
    )
    agg.submit("BTCUSDT", _payload("BOS_BULL", tf="60", symbol="BTCUSDT"))
    agg.submit("ETHUSDT", _payload("BOS_BULL", tf="60", symbol="ETHUSDT"))
    agg.flush_now("BTCUSDT")
    agg.flush_now("ETHUSDT")
    assert sorted(received) == [("BTCUSDT", 0), ("ETHUSDT", 0)]


def test_aggregator_immediate_timer_fires_synchronously():
    received = []

    def cb(winner, suppressed):
        received.append(winner.sig_type)

    agg = SignalAggregator(callback=cb, timer_factory=_ImmediateTimer)
    agg.submit("BTCUSDT", _payload("BOS_BULL", tf="60"))
    # ImmediateTimer вызывает callback внутри submit (когда start() — синхронно)
    assert received == ["BOS_BULL"]


def test_aggregator_callback_failure_does_not_kill_timer():
    """Если callback падает — aggregator не должен ронять процесс."""
    def bad_cb(w, s):
        raise RuntimeError("boom")
    agg = SignalAggregator(callback=bad_cb, timer_factory=_ManualTimer)
    agg.submit("BTCUSDT", _payload("BOS_BULL", tf="60"))
    # Не должно бросать наружу
    agg.flush_now("BTCUSDT")
    assert agg.pending_symbols() == []


def test_aggregator_real_timer_fires():
    """Sanity: реальный threading.Timer тоже работает."""
    received = threading.Event()
    seen = []

    def cb(winner, suppressed):
        seen.append(winner.sig_type)
        received.set()

    agg = SignalAggregator(callback=cb, window_fn=lambda tf: 0.05)
    agg.submit("BTCUSDT", _payload("BOS_BULL", tf="60"))
    assert received.wait(timeout=2.0), "timer never fired"
    assert seen == ["BOS_BULL"]


# ─── End-to-end smoke: gate + dispatch sequence ───────────────────────────


def test_gate_full_sequence(conn):
    """LONG 1h → SHORT 1h conf+10 (suppress) → SHORT 1h conf+20 (reversal)."""
    record_dispatch(conn, "BTCUSDT", "LONG", "60", "BOS_BULL", confidence=70)

    g1 = cooldown_check(conn, "BTCUSDT", "SHORT", confidence=80, tf="60")
    assert g1.action == "suppress"

    g2 = cooldown_check(conn, "BTCUSDT", "SHORT", confidence=90, tf="60")
    assert g2.action == "reversal"
