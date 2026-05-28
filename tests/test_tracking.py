"""Tests for tracking.py — TP/SL outcome tracking + stats."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import tracking


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def conn():
    """In-memory SQLite with the production schema."""
    c = sqlite3.connect(":memory:")
    c.execute("""
        CREATE TABLE IF NOT EXISTS signal_outcomes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id   INTEGER NOT NULL,
            symbol      TEXT    NOT NULL,
            signal_type TEXT    NOT NULL,
            direction   TEXT    NOT NULL,
            entry_price REAL    NOT NULL,
            entry_ts    TEXT    NOT NULL,
            price_1h    REAL,
            price_4h    REAL,
            price_24h   REAL,
            pct_1h      REAL,
            pct_4h      REAL,
            pct_24h     REAL,
            done        INTEGER DEFAULT 0
        )
    """)
    c.commit()
    tracking.init_schema(c)
    yield c
    c.close()


def _decision(verdict="LONG", **overrides):
    base = {
        "verdict":    verdict,
        "direction":  "long" if verdict == "LONG" else "short",
        "entry":      {"min": 99.5, "max": 100.5},
        "sl":         98.0,
        "tp1":        103.0,
        "tp2":        105.0,
        "tp3":        108.0,
        "rr1":        1.5, "rr2": 2.5, "rr3": 4.0,
        "confidence": 78,
        "veto_reasons": [],
        "key_factors":  ["CVD ✅"],
    }
    base.update(overrides)
    return base


def _bar(o, h, low, c, v=100):
    return {"o": o, "h": h, "l": low, "c": c, "v": v}


# ─── Schema migration ────────────────────────────────────────────────────


def test_init_schema_adds_engine_columns(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(signal_outcomes)")}
    for col, _ in tracking.EXTRA_COLS:
        assert col in cols, f"missing column: {col}"


def test_init_schema_is_idempotent(conn):
    # second invocation must not raise
    tracking.init_schema(conn)
    tracking.init_schema(conn)


# ─── open_trade ──────────────────────────────────────────────────────────


def test_open_trade_persists_decision_snapshot(conn):
    oid = tracking.open_trade(conn, signal_id=1,
                              decision=_decision(),
                              symbol="BTCUSDT", signal_type="BOS_BULL")
    row = conn.execute(
        "SELECT verdict, sl, tp1, tp3, rr1, confidence, status, decision_json"
        " FROM signal_outcomes WHERE id=?", (oid,)).fetchone()
    verdict, sl, tp1, tp3, rr1, conf, status, dj = row
    assert verdict == "LONG"
    assert sl == 98.0
    assert tp1 == 103.0
    assert tp3 == 108.0
    assert rr1 == 1.5
    assert conf == 78
    assert status == "open"
    payload = json.loads(dj)
    assert payload["verdict"] == "LONG"
    assert payload["entry"]["max"] == 100.5


def test_open_trade_wait_marks_skipped(conn):
    tracking.open_trade(conn, 1, _decision(verdict="WAIT", entry=None,
                                          sl=None, tp1=None),
                        "BTCUSDT", "BOS_BULL")
    status = conn.execute(
        "SELECT status FROM signal_outcomes").fetchone()[0]
    assert status == "skipped"


def test_open_trade_sets_expiry(conn):
    tracking.open_trade(conn, 1, _decision(), "BTCUSDT", "BOS_BULL")
    expires = conn.execute(
        "SELECT expires_at FROM signal_outcomes").fetchone()[0]
    assert expires is not None
    parsed = datetime.strptime(expires, "%Y-%m-%d %H:%M").replace(
        tzinfo=timezone.utc)
    delta = parsed - datetime.now(timezone.utc)
    # Должно быть около EXPIRY_HOURS
    assert timedelta(hours=tracking.EXPIRY_HOURS - 1) < delta < \
           timedelta(hours=tracking.EXPIRY_HOURS + 1)


# ─── check_open_trades: hit detection ────────────────────────────────────


def test_long_tp1_hit(conn):
    tracking.open_trade(conn, 1, _decision(verdict="LONG"),
                        "BTCUSDT", "BOS_BULL")
    # Bar with high reaching TP1 (103) but not breaching SL (98)
    bars = [_bar(100, 103.5, 99.5, 102.5)]
    stats = tracking.check_open_trades(conn, fetch_klines=lambda *_: bars)
    assert stats["closed"] == 1

    row = conn.execute(
        "SELECT status, hit_level, r_multiple FROM signal_outcomes"
    ).fetchone()
    assert row == ("tp1_hit", "TP1", 1.5)


def test_long_tp3_takes_priority_over_tp1_in_same_bar(conn):
    tracking.open_trade(conn, 1, _decision(verdict="LONG"),
                        "BTCUSDT", "BOS_BULL")
    # Bar reaches all three TPs simultaneously — выбираем самый дальний
    bars = [_bar(100, 110, 99.5, 109)]
    tracking.check_open_trades(conn, fetch_klines=lambda *_: bars)
    row = conn.execute(
        "SELECT status, hit_level, r_multiple FROM signal_outcomes"
    ).fetchone()
    assert row == ("tp3_hit", "TP3", 4.0)


def test_long_sl_hit(conn):
    tracking.open_trade(conn, 1, _decision(verdict="LONG"),
                        "BTCUSDT", "BOS_BULL")
    # Bar breaching SL with low (price drop)
    bars = [_bar(100, 101, 97, 97.5)]
    tracking.check_open_trades(conn, fetch_klines=lambda *_: bars)
    row = conn.execute(
        "SELECT status, hit_level, r_multiple FROM signal_outcomes"
    ).fetchone()
    assert row == ("sl_hit", "SL", -1.0)


def test_long_sl_wins_when_same_bar_hits_both(conn):
    """Conservative tie-break: SL первым (worst-case)."""
    tracking.open_trade(conn, 1, _decision(verdict="LONG"),
                        "BTCUSDT", "BOS_BULL")
    # Bar simultaneously sweeps SL and TP3
    bars = [_bar(100, 110, 97, 105)]
    tracking.check_open_trades(conn, fetch_klines=lambda *_: bars)
    row = conn.execute(
        "SELECT status, hit_level FROM signal_outcomes"
    ).fetchone()
    assert row == ("sl_hit", "SL")


def test_short_tp1_hit(conn):
    d = _decision(verdict="SHORT", entry={"min": 99.5, "max": 100.5},
                  sl=102.0, tp1=97.0, tp2=95.0, tp3=92.0)
    tracking.open_trade(conn, 1, d, "BTCUSDT", "BOS_BEAR")
    # Bar reaches TP1 (96.5 <= 97) without breaching SL (102)
    bars = [_bar(100, 100.5, 96.5, 97.0)]
    tracking.check_open_trades(conn, fetch_klines=lambda *_: bars)
    row = conn.execute(
        "SELECT status, hit_level, r_multiple FROM signal_outcomes"
    ).fetchone()
    assert row == ("tp1_hit", "TP1", 1.5)


def test_short_sl_hit(conn):
    d = _decision(verdict="SHORT", sl=102.0, tp1=97.0, tp2=95.0, tp3=92.0)
    tracking.open_trade(conn, 1, d, "BTCUSDT", "BOS_BEAR")
    bars = [_bar(100, 103, 99.5, 102.5)]
    tracking.check_open_trades(conn, fetch_klines=lambda *_: bars)
    row = conn.execute(
        "SELECT status, hit_level, r_multiple FROM signal_outcomes"
    ).fetchone()
    assert row == ("sl_hit", "SL", -1.0)


def test_trade_still_open_when_nothing_touched(conn):
    tracking.open_trade(conn, 1, _decision(), "BTCUSDT", "BOS_BULL")
    bars = [_bar(100, 100.8, 99.5, 100.2),
            _bar(100.2, 101.0, 99.8, 100.5)]
    stats = tracking.check_open_trades(conn, fetch_klines=lambda *_: bars)
    assert stats["closed"] == 0
    status = conn.execute(
        "SELECT status FROM signal_outcomes").fetchone()[0]
    assert status == "open"


def test_first_bar_with_hit_wins(conn):
    """Если первая свеча задела TP1, а вторая SL — фиксируем TP1."""
    tracking.open_trade(conn, 1, _decision(verdict="LONG"),
                        "BTCUSDT", "BOS_BULL")
    bars = [
        _bar(100, 103.5, 99.6, 103),    # TP1 hit
        _bar(103, 103, 97, 97.5),       # потом якобы SL — игнор
    ]
    tracking.check_open_trades(conn, fetch_klines=lambda *_: bars)
    row = conn.execute(
        "SELECT hit_level FROM signal_outcomes").fetchone()
    assert row[0] == "TP1"


def test_expired_trade_closes_with_zero_r(conn):
    """Trade с истёкшим expires_at → status=expired, r_multiple=0."""
    tracking.open_trade(conn, 1, _decision(), "BTCUSDT", "BOS_BULL")
    # Вручную выставляем expires_at в прошлое
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%d %H:%M")
    conn.execute("UPDATE signal_outcomes SET expires_at=?", (past,))
    conn.commit()
    bars = [_bar(100, 100.5, 99.5, 100)]
    stats = tracking.check_open_trades(conn, fetch_klines=lambda *_: bars)
    assert stats["closed"] == 1
    row = conn.execute(
        "SELECT status, r_multiple FROM signal_outcomes").fetchone()
    assert row == ("expired", 0.0)


def test_skipped_trades_not_checked(conn):
    """WAIT/SKIP не должны проходить через TP/SL detection."""
    tracking.open_trade(conn, 1, _decision(verdict="WAIT"),
                        "BTCUSDT", "BOS_BULL")
    fetched = {"called": False}

    def _fetch(*_):
        fetched["called"] = True
        return [_bar(100, 200, 50, 100)]

    stats = tracking.check_open_trades(conn, fetch_klines=_fetch)
    assert stats["checked"] == 0  # skipped не в open
    assert fetched["called"] is False


def test_no_open_trades_short_circuits(conn):
    stats = tracking.check_open_trades(conn, fetch_klines=lambda *_: [])
    assert stats == {"checked": 0, "closed": 0, "still_open": 0}


def test_fetch_klines_exception_handled(conn):
    tracking.open_trade(conn, 1, _decision(), "BTCUSDT", "BOS_BULL")

    def _boom(*_):
        raise RuntimeError("network down")

    # Не должно крашить worker
    stats = tracking.check_open_trades(conn, fetch_klines=_boom)
    assert stats["checked"] == 1
    assert stats["closed"] == 0
    # Сделка остаётся open
    status = conn.execute(
        "SELECT status FROM signal_outcomes").fetchone()[0]
    assert status == "open"


def test_check_open_trades_requests_bars_proportional_to_age(conn):
    """
    Ревью-фикс: не запрашивать всё 2000 баров если trade открыт давно.
    Берём ровно столько, сколько прошло с entry, чтобы не walk'ить
    pre-entry историю и не получать ложные SL/TP касания.
    """
    tracking.open_trade(conn, 1, _decision(), "BTCUSDT", "BOS_BULL")

    requested = {"bars": None}

    def _capture(symbol, interval, limit):
        requested["bars"] = limit
        # Возвращаем "цена топчется" — без касаний
        return [_bar(100, 100.5, 99.5, 100.2) for _ in range(limit)]

    tracking.check_open_trades(conn, fetch_klines=_capture)
    # Сделка открыта только что → должно запросить минимум баров,
    # а НЕ весь хвост 2000.
    assert requested["bars"] is not None
    assert requested["bars"] < 100, \
        f"запросили {requested['bars']} баров для свежесозданной сделки"


def test_pre_entry_klines_dont_trigger_false_sl(conn):
    """
    Регрессия: до фикса worker walk'ил все 2000 баров и SL мог
    "сработать" на цене недельной давности. После фикса берётся
    только окно с entry; ниже даём всего 2 бара (соответствует
    свежей сделке) и проверяем что не закрылась.
    """
    tracking.open_trade(conn, 1, _decision(verdict="LONG"),
                        "BTCUSDT", "BOS_BULL")

    def _fetch(symbol, interval, limit):
        # API вернул столько баров сколько просили — без касаний
        return [_bar(100, 100.5, 99.5, 100.2)] * min(limit, 5)

    stats = tracking.check_open_trades(conn, fetch_klines=_fetch)
    assert stats["closed"] == 0
    status = conn.execute(
        "SELECT status FROM signal_outcomes").fetchone()[0]
    assert status == "open"


# ─── compute_stats + format_stats_message ────────────────────────────────


def _seed_closed_trade(conn, *, signal_type, status, r_multiple,
                       symbol="BTCUSDT", verdict="LONG", confidence=70,
                       days_ago=1):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%d %H:%M")
    conn.execute(
        """INSERT INTO signal_outcomes(
            signal_id, symbol, signal_type, direction, entry_price, entry_ts,
            verdict, status, r_multiple, confidence, rr1, done
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (1, symbol, signal_type,
         "bull" if verdict == "LONG" else "bear",
         100.0, ts, verdict, status, r_multiple, confidence, 1.5, 1),
    )
    conn.commit()


def test_compute_stats_empty(conn):
    s = tracking.compute_stats(conn, days=30)
    assert s["total"] == 0
    assert s["closed"] == 0
    assert s["win_rate"] == 0


def test_compute_stats_basic_math(conn):
    _seed_closed_trade(conn, signal_type="BOS_BULL",
                       status="tp1_hit", r_multiple=1.5)
    _seed_closed_trade(conn, signal_type="BOS_BULL",
                       status="tp2_hit", r_multiple=2.5)
    _seed_closed_trade(conn, signal_type="BOS_BULL",
                       status="sl_hit",  r_multiple=-1.0)

    s = tracking.compute_stats(conn, days=30)
    assert s["closed"] == 3
    assert s["win_rate"] == round(2 / 3 * 100, 1)
    assert s["avg_r"] == round((1.5 + 2.5 - 1.0) / 3, 2)
    assert s["hits"]["tp1"] == 1
    assert s["hits"]["tp2"] == 1
    assert s["hits"]["sl"]  == 1


def test_compute_stats_groups_by_signal_and_symbol(conn):
    _seed_closed_trade(conn, signal_type="BOS_BULL", symbol="BTCUSDT",
                       status="tp1_hit", r_multiple=1.5)
    _seed_closed_trade(conn, signal_type="BOS_BULL", symbol="ETHUSDT",
                       status="sl_hit",  r_multiple=-1.0)
    _seed_closed_trade(conn, signal_type="OB_BULL",  symbol="BTCUSDT",
                       status="tp2_hit", r_multiple=2.5)

    s = tracking.compute_stats(conn, days=30)
    by_sig = dict((k, (n, wr, ar)) for k, n, wr, ar in s["by_signal"])
    assert by_sig["BOS_BULL"][0] == 2
    assert by_sig["OB_BULL"][0]  == 1
    by_sym = dict((k, (n, wr, ar)) for k, n, wr, ar in s["by_symbol"])
    assert by_sym["BTC"][0] == 2
    assert by_sym["ETH"][0] == 1


def test_compute_stats_confidence_buckets(conn):
    _seed_closed_trade(conn, signal_type="X", status="tp1_hit",
                       r_multiple=1.5, confidence=80)   # → 75+
    _seed_closed_trade(conn, signal_type="X", status="tp1_hit",
                       r_multiple=1.5, confidence=65)   # → 60-74
    _seed_closed_trade(conn, signal_type="X", status="tp1_hit",
                       r_multiple=1.5, confidence=55)   # → 50-59
    _seed_closed_trade(conn, signal_type="X", status="sl_hit",
                       r_multiple=-1.0, confidence=40)  # → 35-49
    _seed_closed_trade(conn, signal_type="X", status="sl_hit",
                       r_multiple=-1.0, confidence=20)  # → <35

    s = tracking.compute_stats(conn, days=30)
    buckets = {b[0]: b for b in s["by_conf"]}
    assert buckets["75+"][1]    == 1
    assert buckets["60-74"][1]  == 1
    assert buckets["50-59"][1]  == 1
    assert buckets["35-49"][1]  == 1
    assert buckets["<35"][1]    == 1


def test_open_trade_force_status_suppressed(conn):
    """force_status переопределяет 'open' для подавленных gate сигналов."""
    oid = tracking.open_trade(conn, signal_id=1, decision=_decision(),
                              symbol="BTCUSDT", signal_type="BOS_BULL",
                              force_status="suppressed")
    status = conn.execute(
        "SELECT status FROM signal_outcomes WHERE id=?", (oid,)).fetchone()[0]
    assert status == "suppressed"


def test_compute_stats_excludes_suppressed(conn):
    """suppressed-сигналы не учитываются в win-rate (юзер их не получил)."""
    _seed_closed_trade(conn, signal_type="X", status="tp1_hit", r_multiple=1.5)
    _seed_closed_trade(conn, signal_type="X", status="suppressed",
                       r_multiple=0.0, confidence=30)
    _seed_closed_trade(conn, signal_type="X", status="suppressed",
                       r_multiple=0.0, confidence=25)

    s = tracking.compute_stats(conn, days=30)
    assert s["closed"] == 1          # только tp1_hit
    assert s["win_rate"] == 100.0    # suppressed не разбавили win-rate
    assert s["suppressed"] == 2


def test_compute_stats_filters_by_days(conn):
    _seed_closed_trade(conn, signal_type="A", status="tp1_hit",
                       r_multiple=1.5, days_ago=1)
    _seed_closed_trade(conn, signal_type="A", status="sl_hit",
                       r_multiple=-1.0, days_ago=50)

    s = tracking.compute_stats(conn, days=7)
    assert s["closed"] == 1  # only recent one


def test_open_and_skipped_excluded_from_win_rate(conn):
    _seed_closed_trade(conn, signal_type="A", status="tp1_hit",
                       r_multiple=1.5)
    # Открытая сделка
    conn.execute(
        """INSERT INTO signal_outcomes(
            signal_id, symbol, signal_type, direction, entry_price, entry_ts,
            verdict, status
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (2, "BTCUSDT", "A", "bull", 100.0,
         datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
         "LONG", "open"),
    )
    conn.commit()

    s = tracking.compute_stats(conn, days=30)
    assert s["total"]  == 2
    assert s["open"]   == 1
    assert s["closed"] == 1


def test_format_stats_message_no_data(conn):
    s = tracking.compute_stats(conn, days=30)
    msg = tracking.format_stats_message(s)
    assert "Пока нет торгуемых сигналов" in msg


def test_format_stats_message_no_closed(conn):
    conn.execute(
        """INSERT INTO signal_outcomes(
            signal_id, symbol, signal_type, direction, entry_price, entry_ts,
            verdict, status
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (1, "BTCUSDT", "A", "bull", 100.0,
         datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
         "LONG", "open"),
    )
    conn.commit()
    s = tracking.compute_stats(conn, days=30)
    msg = tracking.format_stats_message(s)
    assert "ни одна сделка не закрылась" in msg


def test_format_stats_message_contains_all_sections(conn):
    _seed_closed_trade(conn, signal_type="BOS_BULL",
                       status="tp1_hit", r_multiple=1.5,
                       symbol="BTCUSDT", confidence=80)
    _seed_closed_trade(conn, signal_type="CHOCH_BEAR", verdict="SHORT",
                       status="sl_hit",  r_multiple=-1.0,
                       symbol="ETHUSDT", confidence=60)
    s = tracking.compute_stats(conn, days=30)
    msg = tracking.format_stats_message(s)
    assert "Win-rate" in msg
    assert "По типам сигналов" in msg
    assert "По символам" in msg
    assert "BOS_BULL" in msg
    assert "BTC" in msg
    assert "ETH" in msg
    assert "calibration" in msg.lower() or "confidence" in msg.lower()
