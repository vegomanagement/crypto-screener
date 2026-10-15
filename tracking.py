"""
tracking.py — учёт исхода torgowyh сделок: TP/SL hit, R-multiple, win-rate.

Расширяет существующую таблицу signal_outcomes колонками для хранения
полных уровней Entry / SL / TP1-3 / RR / confidence, чтобы можно было
ретроспективно проверить достижение TP/SL и подсчитать реальный
R-multiple для калибровки engine.

Ключевые функции:
  • init_schema(conn)              — идемпотентная миграция (ALTER ADD COLUMN)
  • open_trade(conn, signal_id, decision, symbol)
                                   — сохранить decision-snapshot для tracking
  • check_open_trades(conn, fetch_klines)
                                   — walk через klines, обновить status / r_multiple
  • compute_stats(conn, days)      — агрегированная статистика
  • format_stats_message(stats)    — HTML-форматирование для Telegram

Состояния (status):
  • open       — сделка ещё активна
  • tp1_hit / tp2_hit / tp3_hit — достигнут соответствующий TP
  • sl_hit     — SL пробит
  • expired    — прошло EXPIRY_HOURS без касания TP/SL
  • skipped    — engine verdict был WAIT/SKIP (сохраняем для статы, но не торгуем)
"""

import json
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


EXPIRY_HOURS  = 168  # 7d horizon; expired only with observable history/close

# Legacy option name retained: "fair" now means AMBIGUOUS / unknown R,
# not an invented zero-R exit. "conservative" is an explicit pessimistic
# SL-first assumption, not an observed intrabar path. Full exit is at TP1.
# Legacy persisted TP2/TP3/tie records stay readable, not rewritten.
SAME_BAR_TIE_BREAK = "fair"

REALIZED_STATUSES = frozenset({"tp1_hit", "tp2_hit", "tp3_hit",
                               "sl_hit", "tie_hit", "expired"})

EXTRA_COLS = [
    ("decision_json", "TEXT"),
    ("verdict",       "TEXT"),
    ("entry_min",     "REAL"),
    ("entry_max",     "REAL"),
    ("sl",            "REAL"),
    ("tp1",           "REAL"),
    ("tp2",           "REAL"),
    ("tp3",           "REAL"),
    ("rr1",           "REAL"),
    ("rr2",           "REAL"),
    ("rr3",           "REAL"),
    ("confidence",    "INTEGER"),
    ("status",        "TEXT"),
    ("hit_level",     "TEXT"),
    ("hit_at",        "TEXT"),
    ("r_multiple",    "REAL"),
    ("expires_at",    "TEXT"),
    ("last_checked",  "TEXT"),
]


# ─── Schema migration ─────────────────────────────────────────────────────

def init_schema(conn) -> None:
    """
    Идемпотентно добавляет engine-tracking колонки к signal_outcomes.
    SQLite не поддерживает IF NOT EXISTS в ALTER, поэтому ловим
    OperationalError для уже-существующих колонок.
    """
    import sqlite3
    for col, sqltype in EXTRA_COLS:
        try:
            conn.execute(
                f"ALTER TABLE signal_outcomes ADD COLUMN {col} {sqltype}"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
    conn.commit()


# ─── Запись сделки ───────────────────────────────────────────────────────

def open_trade(conn, signal_id: int, decision: dict,
               symbol: str, signal_type: str,
               force_status: str | None = None) -> int | None:
    """
    Сохраняет торгуемую сделку для отслеживания TP/SL.

    Если verdict не LONG/SHORT — записывает с status='skipped'
    и не отслеживает (для статистики гейтинга).

    force_status переопределяет вычисленный статус — используется когда
    сигнал был LONG/SHORT, но cooldown gate его подавил (status='suppressed'):
    такие сделки НЕ трекаются и НЕ учитываются в win-rate, т.к. юзер их
    не получил.

    Возвращает id строки в signal_outcomes или None при ошибке.
    """
    verdict   = decision.get("verdict", "WAIT")
    direction = "bull" if verdict == "LONG" else (
        "bear" if verdict == "SHORT" else verdict.lower())
    entry     = decision.get("entry") or {}
    now       = datetime.now(timezone.utc)
    entry_ts  = now.strftime("%Y-%m-%d %H:%M")
    expires   = (now + timedelta(hours=EXPIRY_HOURS)).strftime("%Y-%m-%d %H:%M")
    status    = force_status or (
        "open" if verdict in ("LONG", "SHORT") else "skipped")

    # entry_price для совместимости со старой схемой — берём midpoint
    e_min = entry.get("min")
    e_max = entry.get("max")
    entry_price = (
        (e_min + e_max) / 2 if (e_min is not None and e_max is not None)
        else 0
    )

    snapshot = dict(decision)
    snapshot["_execution"] = {"as_of": now.isoformat(), "entry_ts": entry_ts,
                              "entry_policy": "midpoint_touch_next_full_bar",
                              "exit_policy": "full_tp1"}

    cur = conn.execute(
        """
        INSERT INTO signal_outcomes(
            signal_id, symbol, signal_type, direction,
            entry_price, entry_ts,
            decision_json, verdict,
            entry_min, entry_max, sl, tp1, tp2, tp3,
            rr1, rr2, rr3, confidence,
            status, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_id, symbol, signal_type, direction,
            entry_price, entry_ts,
            json.dumps(snapshot, ensure_ascii=False), verdict,
            e_min, e_max,
            decision.get("sl"), decision.get("tp1"),
            decision.get("tp2"), decision.get("tp3"),
            decision.get("rr1"), decision.get("rr2"),
            decision.get("rr3"), decision.get("confidence"),
            status, expires,
        ),
    )
    conn.commit()
    return cur.lastrowid


# ─── Проверка достижения TP/SL ────────────────────────────────────────────

def check_open_trades(conn, fetch_klines) -> dict:
    """
    Идёт по открытым LONG/SHORT trades, фетчит klines с момента entry
    и проверяет первый touch SL / TP.

    fetch_klines(symbol, interval, limit) returns oldest-first OHLCV with
    real bar OPEN timestamps `ts` in milliseconds. Interval is '5' (5m).
    Only closed contiguous post-as-of bars are evidence. Orders activate at
    the next full bar; the signal-straddling bar is never back-projected.
    Timestamp-less callbacks prove no fills or wins. Expiry follows backfill;
    missing history or an unobservable expiry close is censored, not zero R.
    _execution in existing decision_json stores precise as-of and fill-bar
    provenance without changing the SQLite schema. Fill timestamps denote
    bar starts, NOT exact intrabar instants. No funding/queue/spread model.

    Возвращает dict с агрегированной статистикой за прогон.
    """
    rows = conn.execute(
        """
        SELECT id, symbol, entry_ts, verdict, sl, tp1, tp2, tp3,
               rr1, rr2, rr3, expires_at, entry_price, decision_json
        FROM signal_outcomes
        WHERE status = 'open'
        """
    ).fetchall()

    if not rows:
        return {"checked": 0, "closed": 0, "still_open": 0}

    now      = datetime.now(timezone.utc)
    checked  = 0
    closed   = 0

    for row in rows:
        (oid, symbol, entry_ts, verdict, sl, tp1, tp2, tp3,
         rr1, rr2, rr3, expires_at, entry_price, decision_json) = row

        checked += 1

        # Parse expiry, but resolve observable history before applying it.
        exp_dt = None
        try:
            exp_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M").replace(
                tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass

        # Сколько 5m баров прошло с момента entry — берём только их,
        # иначе walk через старую историю даст ложные SL/TP касания
        # (баг ревью: 2000 баров = ~7 дней, entry мог быть 2ч назад)
        try:
            entry_dt = datetime.strptime(entry_ts, "%Y-%m-%d %H:%M").replace(
                tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue

        try:
            execution = (json.loads(decision_json or "{}").get("_execution") or {})
            if execution.get("entry_ts") == entry_ts:
                entry_dt = datetime.fromisoformat(execution["as_of"])
                entry_ts = entry_dt.isoformat()
        except (TypeError, ValueError, KeyError):
            pass

        minutes_since = max(1, int((now - entry_dt).total_seconds() / 60))
        # +2 бара буфер (на округление и текущий незакрытый бар)
        bars_needed = min(2000, max(2, minutes_since // 5 + 2))

        try:
            klines = fetch_klines(symbol, "5", bars_needed) or []
        except Exception as e:
            log.warning(f"tracking fetch_klines {symbol}: {e}")
            continue

        # Empty history still needs an explicit censored outcome at expiry.

        # Если API вернул больше баров чем нужно — обрезаем хвост по
        # числу баров с entry. Это защита от случаев, когда у нас
        # лимит =N, но API всё равно отдал N последних.
        if len(klines) > bars_needed:
            klines = klines[-bars_needed:]

        cutoff = min(now, exp_dt) if exp_dt is not None else now
        klines = [b for b in klines if isinstance(b.get("ts"), (int, float))
                  and b["ts"] >= entry_dt.timestamp() * 1000
                  and b["ts"] + 300000 <= cutoff.timestamp() * 1000]

        # Only a contiguous prefix from activation can prove the FIRST exit.
        expected = math.ceil(entry_dt.timestamp() * 1000 / 300000) * 300000
        prefix = []
        for bar in klines:
            if bar["ts"] != expected:
                break
            prefix.append(bar)
            expected += 300000
        klines = prefix

        observed = {}
        outcome = (_simulate_execution([{}] + klines, 0, verdict, sl, tp1, tp2, tp3,
                                       rr1, rr2, rr3, len(klines), entry=entry_price,
                                       details=observed) if klines else None)
        payload = json.loads(decision_json or "{}")
        execution = payload.setdefault("_execution", {})
        execution.setdefault("filled", False)
        execution.update(observed, exit_policy="full_tp1")
        conn.execute("UPDATE signal_outcomes SET decision_json=? WHERE id=?",
                     (json.dumps(payload, ensure_ascii=False), oid))
        if outcome and outcome[1] not in ("expired", "unfilled", "censored"):
            _, status, level, r_mult = outcome
            _close_trade(conn, oid, status, level, r_mult, now)
            closed += 1
        elif exp_dt is not None and now > exp_dt:
            complete = (klines and klines[0]["ts"] == entry_dt.timestamp() * 1000
                        and klines[-1]["ts"] + 300000 == exp_dt.timestamp() * 1000)
            result = outcome if complete else None
            status, level, r = result[1:] if result else ("censored", None, None)
            _close_trade(conn, oid, status, level, r, now)
            closed += 1
        else:
            conn.execute(
                "UPDATE signal_outcomes SET last_checked=? WHERE id=?",
                (now.strftime("%Y-%m-%d %H:%M"), oid),
            )
            conn.commit()

    return {
        "checked":   checked,
        "closed":    closed,
        "still_open": checked - closed,
    }


def _simulate_execution(
    klines_5m: list,
    open_idx:  int,
    verdict:   str,
    sl:        float | None,
    tp1:       float | None,
    tp2:       float | None,
    tp3:       float | None,
    rr1:       float | None,
    rr2:       float | None,
    rr3:       float | None,
    expiry_bars: int,
    *, entry: float | None = None,
    as_of: int | None = None, bar_ms: int | None = None,
    details: dict | None = None,
) -> tuple[int, str, str | None, float | None] | None:
    """
    Shared deterministic paper-execution kernel (full exit at TP1).

    A midpoint must trade inside a bar before any exit is possible. A fill
    and exit on a bar not opening at entry has unknown ordering; SL+TP is
    likewise ambiguous by default. Unknown outcomes have R=None. A complete
    horizon exits at the final close; a truncated/gapped dataset is censored.
    Legacy calls infer intended entry from the signal close, never a fill.
    Optional as_of/bar_ms enforce the decision-close clock and continuity.
    Returns (last_observed_or_exit_idx, status, level, R), or None for an
    invalid plan. details contains fill BAR provenance, not an exact time.
    All prices are modeled executions: no liquidity/queue/funding precision.
    """
    if sl is None or tp1 is None or verdict not in ("LONG", "SHORT"):
        return None
    if entry is None:
        entry = klines_5m[open_idx].get("c")
    if entry is None:
        return None
    if not all(math.isfinite(x) and x > 0 for x in (entry, sl, tp1)):
        return None
    if not (sl < entry < tp1 if verdict == "LONG" else tp1 < entry < sl):
        return None

    tps = [("TP1", tp1, abs(tp1 - entry) / abs(entry - sl))]

    end = min(len(klines_5m), open_idx + 1 + expiry_bars)
    filled = False

    for j in range(open_idx + 1, end):
        bar = klines_5m[j]
        if as_of is not None and bar_ms is not None:
            expected_ts = as_of + (j - open_idx - 1) * bar_ms
            if bar.get("ts") != expected_ts:
                return (j, "censored", None, None)
        prices = [bar.get(key) for key in ("o", "h", "l", "c")]
        if not all(isinstance(p, (int, float)) and math.isfinite(p) and p > 0
                   for p in prices):
            return (j, "censored", None, None)
        low, high = bar["l"], bar["h"]
        if not low <= min(bar["o"], bar["c"]) <= max(bar["o"], bar["c"]) <= high:
            return (j, "censored", None, None)
        new_fill = not filled
        if not filled:
            if not low <= entry <= high:
                continue
            filled = True
            if details is not None:
                details.update(filled=True, fill_idx=j, fill_ts=bar.get("ts"))

        if not new_fill and bar.get("o") is not None:
            gap_stop = bar["o"] <= sl if verdict == "LONG" else bar["o"] >= sl
            if gap_stop:
                r = (bar["o"] - entry) / abs(entry - sl) * (1 if verdict == "LONG" else -1)
                return (j, "sl_hit", "SL", r)

        if verdict == "LONG":
            sl_hit = low <= sl
            tp_cands = [(lvl, p, r) for (lvl, p, r) in tps if high >= p]
        elif verdict == "SHORT":
            sl_hit = high >= sl
            tp_cands = [(lvl, p, r) for (lvl, p, r) in tps if low <= p]
        else:
            return None

        if new_fill and bar.get("o") != entry and (sl_hit or tp_cands):
            return (j, "ambiguous", "AMBIGUOUS", None)

        # Same-bar tie: respect SAME_BAR_TIE_BREAK
        if sl_hit and tp_cands:
            if SAME_BAR_TIE_BREAK == "conservative":
                return (j, "sl_hit", "SL", -1.0)
            return (j, "ambiguous", "AMBIGUOUS", None)
        if sl_hit:
            return (j, "sl_hit", "SL", -1.0)
        if tp_cands:
            level, _, rr = tp_cands[0]
            return (j, f"{level.lower()}_hit", level, float(rr))

    if end < open_idx + 1 + expiry_bars:
        return (end - 1, "censored", None, None)
    if not filled:
        return (end - 1, "unfilled", None, None)
    r = ((klines_5m[end - 1]["c"] - entry) / abs(entry - sl)
         * (1 if verdict == "LONG" else -1))
    return (end - 1, "expired", None, r)


def _detect_hit(klines, verdict, sl, tp1, tp2, tp3, rr1, rr2, rr3,
                entry_ts, *, entry=None) -> tuple | None:
    """Timestamped post-as-of bars only; missing timestamps prove no hit.

    Legacy positional API is retained. Pass the intended midpoint as entry
    to validate fills. No claim of observed intrabar path is made.
    """
    try:
        dt = datetime.fromisoformat(entry_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        as_of = dt.timestamp() * 1000
    except (TypeError, ValueError):
        return None
    bars = [b for b in klines if isinstance(b.get("ts"), (int, float))
            and b["ts"] >= as_of]
    prefix = []
    expected = math.ceil(as_of / 300000) * 300000
    for bar in bars:
        if bar["ts"] != expected:
            break
        prefix.append(bar)
        expected += 300000
    bars = prefix
    if not bars:
        return None
    result = _simulate_execution([{}] + bars, 0, verdict, sl, tp1, tp2, tp3,
                                 rr1, rr2, rr3, len(bars), entry=entry)
    if result and result[1] not in ("expired", "unfilled", "censored"):
        return result[2], result[3]
    return None


def _close_trade(conn, outcome_id: int, status: str,
                 hit_level: str | None, r_mult: float | None,
                 closed_at: datetime) -> None:
    conn.execute(
        """
        UPDATE signal_outcomes
           SET status=?, hit_level=?, hit_at=?, r_multiple=?,
               last_checked=?, done=1
         WHERE id=?
        """,
        (status, hit_level,
         closed_at.strftime("%Y-%m-%d %H:%M"),
         r_mult,
         closed_at.strftime("%Y-%m-%d %H:%M"),
         outcome_id),
    )
    conn.commit()


# ─── Risk-adjusted метрики (R-space) ──────────────────────────────────────
#
# Все метрики оперируют в пространстве R-multiple (1R = риск SL). Нет
# нормализации по капиталу и нет risk-free rate — это безразмерные числа,
# отражающие профиль исходов сигналов. Calibration constants:
#
#   Sharpe_R    = mean(R) / std(R)         — стабильность результата
#   Sortino_R   = mean(R) / down_std(R)    — наказывает только волатильность вниз
#   ProfitFactor = sum(R+) / |sum(R-)|     — стандарт индустрии (>=1.5 = good)
#   MaxDD       — самая глубокая просадка equity curve, в R
#   MaxConsecL  — длиннейшая серия лоссов подряд (для psychology / sizing)


def _profit_factor(rs: list) -> float:
    """sum(R+) / |sum(R-)|. 0 если нет прибыли. inf если только прибыль."""
    wins   = sum(r for r in rs if r > 0)
    losses = sum(-r for r in rs if r < 0)
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return wins / losses


def _sharpe_r(rs: list) -> float:
    """avg(R) / std(R). 0 при n<2 или нулевой дисперсии."""
    if len(rs) < 2:
        return 0.0
    mean = sum(rs) / len(rs)
    var  = sum((r - mean) ** 2 for r in rs) / len(rs)  # population
    std  = math.sqrt(var)
    if std == 0:
        return 0.0
    return mean / std


def _sortino_r(rs: list) -> float:
    """
    avg(R) / downside_std(R). Downside_std считается по отклонениям от 0
    (R уже центрирован вокруг breakeven), только для отрицательных значений.
    """
    if len(rs) < 2:
        return 0.0
    mean = sum(rs) / len(rs)
    downs = [r for r in rs if r < 0]
    if not downs:
        return float("inf") if mean > 0 else 0.0
    dstd = math.sqrt(sum(r * r for r in downs) / len(downs))
    if dstd == 0:
        return 0.0
    return mean / dstd


def _max_drawdown_r(rs_chrono: list) -> float:
    """
    Максимальная просадка equity curve (cumulative R), хронологически.
    Возвращает 0.0 или отрицательное число (величина max-dd в R).
    Требует упорядоченный по времени список.
    """
    if not rs_chrono:
        return 0.0
    eq     = 0.0
    peak   = 0.0
    max_dd = 0.0
    for r in rs_chrono:
        eq   += r
        peak  = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    return max_dd


def _max_consec_loss(rs_chrono: list) -> int:
    """Длиннейшая серия r<=0 подряд (включая 0R ties — не победы)."""
    best = 0
    cur  = 0
    for r in rs_chrono:
        if r <= 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


# ─── ASCII sparkline (для equity curve) ───────────────────────────────────

_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list, width: int = 20) -> str:
    """
    Компактный ASCII-график серии (для equity curve в Telegram).
    Ресемплирует серию до `width` точек, нормализует к 8 уровням.
    """
    if not values:
        return ""
    n = len(values)
    if n <= width:
        sampled = values
    else:
        step = n / width
        sampled = [values[min(n - 1, int(i * step))] for i in range(width)]
    lo, hi = min(sampled), max(sampled)
    if hi == lo:
        return _SPARK_CHARS[3] * len(sampled)
    span = hi - lo
    return "".join(
        _SPARK_CHARS[min(7, int((v - lo) / span * 7))] for v in sampled
    )


# ─── Stats aggregation ────────────────────────────────────────────────────

def compute_stats(conn, days: int = 30) -> dict:
    """
    Агрегированная статистика по закрытым (и открытым) trades за N дней.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M")

    rows = conn.execute(
        """
        SELECT signal_type, symbol, verdict, status, hit_level,
               r_multiple, confidence, rr1
        FROM signal_outcomes
        WHERE entry_ts >= ? AND verdict IN ('LONG', 'SHORT')
        ORDER BY entry_ts ASC
        """,
        (since,),
    ).fetchall()

    total      = len(rows)
    by_status  = defaultdict(int)
    by_signal  = defaultdict(lambda: {"n": 0, "wins": 0, "r_sum": 0.0})
    by_symbol  = defaultdict(lambda: {"n": 0, "wins": 0, "r_sum": 0.0})
    by_conf    = defaultdict(lambda: {"n": 0, "wins": 0, "r_sum": 0.0})

    closed_r   = []

    for sig_type, symbol, verdict, status, hit_level, r_mult, conf, _rr1 in rows:
        by_status[status] += 1
        # open/skipped/suppressed — не закрытые торгуемые сделки:
        # suppressed = подавлен cooldown gate, юзер его не получил,
        # поэтому в win-rate не учитываем (статистика только по sent-сигналам).
        if status not in REALIZED_STATUSES or r_mult is None:
            continue

        r = r_mult if r_mult is not None else 0
        closed_r.append(r)
        is_win = r > 0

        by_signal[sig_type]["n"]    += 1
        by_signal[sig_type]["r_sum"] += r
        if is_win:
            by_signal[sig_type]["wins"] += 1

        sym = (symbol or "?").replace("USDT", "")
        by_symbol[sym]["n"]    += 1
        by_symbol[sym]["r_sum"] += r
        if is_win:
            by_symbol[sym]["wins"] += 1

        bucket = _conf_bucket(conf)
        by_conf[bucket]["n"]    += 1
        by_conf[bucket]["r_sum"] += r
        if is_win:
            by_conf[bucket]["wins"] += 1

    closed_n = len(closed_r)
    total_wins = sum(1 for r in closed_r if r > 0)
    win_rate = (total_wins / closed_n * 100) if closed_n else 0
    avg_r    = (sum(closed_r) / closed_n) if closed_n else 0

    # closed_r собран в хронологическом порядке (ORDER BY entry_ts ASC),
    # поэтому equity curve / drawdown / streaks считаются как walk слева направо.
    equity = []
    cum    = 0.0
    for r in closed_r:
        cum += r
        equity.append(cum)

    pf      = _profit_factor(closed_r) if closed_r else 0.0
    sharpe  = _sharpe_r(closed_r)      if closed_r else 0.0
    sortino = _sortino_r(closed_r)     if closed_r else 0.0
    max_dd  = _max_drawdown_r(closed_r) if closed_r else 0.0
    consec  = _max_consec_loss(closed_r) if closed_r else 0
    best_r  = max(closed_r) if closed_r else 0.0
    worst_r = min(closed_r) if closed_r else 0.0

    return {
        "days":       days,
        "total":      total,
        "open":       by_status.get("open", 0),
        "closed":     closed_n,
        "win_rate":   round(win_rate, 1),
        "avg_r":      round(avg_r, 2),
        "suppressed": by_status.get("suppressed", 0),
        "censored": by_status.get("censored", 0),
        "unfilled": by_status.get("unfilled", 0),
        "ambiguous": by_status.get("ambiguous", 0),
        "hits": {
            "tp1": by_status.get("tp1_hit", 0),
            "tp2": by_status.get("tp2_hit", 0),
            "tp3": by_status.get("tp3_hit", 0),
            "sl":  by_status.get("sl_hit",  0),
            "tie": by_status.get("tie_hit", 0),
            "expired": by_status.get("expired", 0),
        },
        "risk": {
            "profit_factor":   round(pf, 2) if pf != float("inf") else "∞",
            "sharpe_r":        round(sharpe, 2),
            "sortino_r":       (round(sortino, 2)
                                if sortino != float("inf") else "∞"),
            "max_drawdown_r":  round(max_dd, 2),
            "max_consec_loss": consec,
            "best_r":          round(best_r, 2),
            "worst_r":         round(worst_r, 2),
        },
        "equity":     [round(v, 2) for v in equity],
        "spark":      _sparkline(equity, width=20),
        "by_signal":  _summarize(by_signal),
        "by_symbol":  _summarize(by_symbol),
        "by_conf":    _summarize(by_conf),
    }


CONF_BUCKETS = ("75+", "60-74", "50-59", "35-49", "<35")


def _conf_bucket(conf) -> str:
    if conf is None:
        return "?"
    if conf >= 75:
        return "75+"
    if conf >= 60:
        return "60-74"
    if conf >= 50:
        return "50-59"
    if conf >= 35:
        return "35-49"
    return "<35"


def _summarize(d: dict) -> list:
    """Convert defaultdict to sorted list of (key, n, win_rate, avg_r)."""
    out = []
    for key, v in d.items():
        n  = v["n"]
        if n == 0:
            continue
        wr = round(v["wins"] / n * 100, 1)
        ar = round(v["r_sum"] / n, 2)
        out.append((key, n, wr, ar))
    out.sort(key=lambda x: -x[1])  # sort by sample size desc
    return out


# ─── Telegram formatting ──────────────────────────────────────────────────

def format_stats_message(stats: dict) -> str:
    days = stats["days"]
    hits = stats["hits"]

    closed = stats["closed"]
    wr     = stats["win_rate"]
    avg_r  = stats["avg_r"]

    if stats["total"] == 0:
        return (f"📊 <b>Статистика {days} дней</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Пока нет торгуемых сигналов (LONG/SHORT).\n"
                f"Статистика появится после первых закрытых сделок.")

    excluded = (f"unfilled: {stats.get('unfilled', 0)} · "
                f"censored: {stats.get('censored', 0)} · "
                f"ambiguous: {stats.get('ambiguous', 0)} (вне R-статистики)")

    if closed == 0:
        note = ("Пока нет исходов с известным R."
                if any(stats.get(k) for k in ("unfilled", "censored", "ambiguous"))
                else "Пока ни одна сделка не закрылась.")
        return (f"📊 <b>Статистика {days} дней</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Всего: {stats['total']} · открыто: {stats['open']}\n"
                f"{excluded}\n"
                f"{note}")

    wr_icon = "🟢" if wr >= 55 else ("🟡" if wr >= 45 else "🔴")
    ar_icon = "🟢" if avg_r >= 0.5 else ("🟡" if avg_r >= 0 else "🔴")

    lines = [
        f"📊 <b>Статистика {days} дней</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        (f"Всего: {stats['total']} · открыто: {stats['open']} · "
         f"закрыто: {closed}"),
        f"{wr_icon} Win-rate: <b>{wr}%</b>  ·  {ar_icon} Avg R: <b>{avg_r:+.2f}</b>",
        "",
        "<b>По уровням:</b>",
        f"  🎯 TP1 hit: {hits['tp1']}  ·  TP2: {hits['tp2']}  ·  TP3: {hits['tp3']}",
        f"  🛑 SL hit:  {hits['sl']}",
        f"  ↔️ Tie (same-bar SL+TP, 0R): {hits.get('tie', 0)}",
        f"  ⏰ Expired: {hits['expired']}",
        excluded,
    ]

    if stats.get("suppressed"):
        lines.append(f"  🚫 Подавлено gate: {stats['suppressed']} "
                     f"(не учтены в win-rate)")

    risk = stats.get("risk") or {}
    if risk:
        pf      = risk.get("profit_factor", 0)
        sharpe  = risk.get("sharpe_r", 0)
        sortino = risk.get("sortino_r", 0)
        pf_ic = ("🟢" if isinstance(pf, (int, float)) and pf >= 1.5
                 else ("🟡" if isinstance(pf, (int, float)) and pf >= 1.0
                       else "🔴"))
        sh_ic = ("🟢" if isinstance(sharpe, (int, float)) and sharpe >= 0.3
                 else ("🟡" if isinstance(sharpe, (int, float)) and sharpe >= 0
                       else "🔴"))
        lines.append("\n<b>📐 Risk-adjusted (R-space):</b>")
        lines.append(f"  {pf_ic} Profit Factor: <b>{pf}</b>")
        lines.append(f"  {sh_ic} Sharpe: <b>{sharpe}</b>  ·  "
                     f"Sortino: <b>{sortino}</b>")
        lines.append(f"  📉 Max DD: <b>{risk.get('max_drawdown_r', 0):+.2f}R</b>"
                     f"  ·  Consec losses: <b>{risk.get('max_consec_loss', 0)}</b>")
        lines.append(f"  🏆 Best: <b>{risk.get('best_r', 0):+.2f}R</b>"
                     f"  ·  Worst: <b>{risk.get('worst_r', 0):+.2f}R</b>")

    spark = stats.get("spark")
    if spark and closed >= 3:
        lines.append(f"\n<b>📈 Equity (cum R):</b>  <code>{spark}</code>")

    if stats["by_signal"]:
        lines.append("\n<b>По типам сигналов:</b>")
        for sig_type, n, wr_s, ar_s in stats["by_signal"][:8]:
            ic = "🟢" if wr_s >= 55 else ("🟡" if wr_s >= 45 else "🔴")
            lines.append(
                f"  {ic} <code>{sig_type:<14}</code> "
                f"{n:>3} · {wr_s:>4.0f}% · {ar_s:+.2f}R"
            )

    if stats["by_symbol"]:
        lines.append("\n<b>По символам (топ-5):</b>")
        for sym, n, wr_s, ar_s in stats["by_symbol"][:5]:
            ic = "🟢" if wr_s >= 55 else ("🟡" if wr_s >= 45 else "🔴")
            lines.append(
                f"  {ic} <code>{sym:<6}</code> "
                f"{n:>3} · {wr_s:>4.0f}% · {ar_s:+.2f}R"
            )

    if stats["by_conf"]:
        lines.append("\n<b>По confidence (калибровка engine):</b>")
        for bucket in CONF_BUCKETS:
            row = next((r for r in stats["by_conf"] if r[0] == bucket), None)
            if row:
                _, n, wr_s, ar_s = row
                ic = "🟢" if wr_s >= 55 else ("🟡" if wr_s >= 45 else "🔴")
                lines.append(
                    f"  {ic} <code>conf {bucket:<6}</code> "
                    f"{n:>3} · {wr_s:>4.0f}% · {ar_s:+.2f}R"
                )

    return "\n".join(lines)


# ─── Список последних сделок (/trades) ─────────────────────────────────────

def recent_trades(conn, days: int = 7, limit: int = 30) -> list:
    """
    Terminal paper outcomes, newest first, including unfilled/censored/
    ambiguous attempts explicitly labeled without R. Historical TP2/TP3/tie
    records stay readable. Open/skipped/suppressed are not listed.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M")
    rows = conn.execute(
        """
        SELECT entry_ts, symbol, signal_type, verdict, status, hit_level,
               r_multiple, confidence
        FROM signal_outcomes
        WHERE entry_ts >= ? AND verdict IN ('LONG', 'SHORT')
          AND status IN ('tp1_hit', 'tp2_hit', 'tp3_hit',
                         'sl_hit', 'tie_hit', 'expired',
                         'unfilled', 'censored', 'ambiguous')
        ORDER BY entry_ts DESC
        LIMIT ?
        """,
        (since, int(limit)),
    ).fetchall()
    return rows


_OUTCOME_ICON = {
    "tp1_hit": "🟢", "tp2_hit": "🟢", "tp3_hit": "🟢",
    "sl_hit":  "🔴",
    "tie_hit": "↔️",
    "expired": "⏰",
}


def format_trades_message(trades: list, days: int) -> str:
    """Компактный список последних N сделок для Telegram."""
    if not trades:
        return (f"📋 <b>Сделки за {days} дней</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Закрытых торгуемых сделок нет.")

    lines = [
        f"📋 <b>Последние {len(trades)} сделок за {days} дней</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for (ts, sym, sig_type, verdict, status, hit_level,
         r_mult, conf) in trades:
        out_ic    = _OUTCOME_ICON.get(status, "•")
        verdict_ic = "🟢" if verdict == "LONG" else "🔴"
        sym_short  = (sym or "?").replace("USDT", "")
        time_short = (ts or "")[5:] if ts else "?"  # "MM-DD HH:MM"
        r_str = (f"{r_mult:+.2f}R" if r_mult is not None
                 else f"N/A ({status})")
        conf_str = f"c{conf}" if conf is not None else "c?"
        sig_short = (sig_type or "?")[:12]
        lines.append(
            f"  {out_ic} <code>{time_short}</code> {verdict_ic} "
            f"<code>{sym_short:<5}</code> "
            f"<code>{sig_short:<12}</code> "
            f"{r_str:>7}  {conf_str}"
        )
    return "\n".join(lines)
