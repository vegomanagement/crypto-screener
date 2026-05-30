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
from collections import defaultdict
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


EXPIRY_HOURS  = 168  # 7 дней — после этого open trade принудительно expired

# Same-bar tie-break: что делать, если в одной свече задеты И SL, И TP.
# "conservative" — SL первым, full -1R (старое поведение, занижает winrate).
# "fair" — ничья, r_multiple=0.0, status='tie_hit'. По умолчанию (P4-фикс).
# На крипте 5m свеча часто свипает обе стороны → conservative системно ловит
# ложные лоссы. Fair честнее показывает «исход был неоднозначен».
SAME_BAR_TIE_BREAK = "fair"

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
            json.dumps(decision, ensure_ascii=False), verdict,
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

    fetch_klines(symbol, interval, limit) → list[{"o","h","l","c","v"}]
    Параметр интервала — '5' (5m); klines в порядке oldest→newest.

    Conservative: если в одном баре low ≤ SL И high ≥ TP — считаем SL первым
    (худший сценарий, иначе завышаем win-rate).

    Возвращает dict с агрегированной статистикой за прогон.
    """
    rows = conn.execute(
        """
        SELECT id, symbol, entry_ts, verdict, sl, tp1, tp2, tp3,
               rr1, rr2, rr3, expires_at
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
         rr1, rr2, rr3, expires_at) = row

        checked += 1

        # Проверка expiry
        try:
            exp_dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M").replace(
                tzinfo=timezone.utc)
            if now > exp_dt:
                _close_trade(conn, oid, "expired", None, 0.0, now)
                closed += 1
                continue
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

        minutes_since = max(1, int((now - entry_dt).total_seconds() / 60))
        # +2 бара буфер (на округление и текущий незакрытый бар)
        bars_needed = min(2000, max(2, minutes_since // 5 + 2))

        try:
            klines = fetch_klines(symbol, "5", bars_needed) or []
        except Exception as e:
            log.warning(f"tracking fetch_klines {symbol}: {e}")
            continue

        if not klines:
            continue

        # Если API вернул больше баров чем нужно — обрезаем хвост по
        # числу баров с entry. Это защита от случаев, когда у нас
        # лимит =N, но API всё равно отдал N последних.
        if len(klines) > bars_needed:
            klines = klines[-bars_needed:]

        hit = _detect_hit(klines, verdict, sl, tp1, tp2, tp3, rr1, rr2, rr3,
                          entry_ts)
        if hit:
            level, r_mult = hit
            status = f"{level.lower()}_hit"
            _close_trade(conn, oid, status, level, r_mult, now)
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


def _detect_hit(klines, verdict, sl, tp1, tp2, tp3, rr1, rr2, rr3,
                entry_ts) -> tuple | None:
    """
    Walk через klines в хронологическом порядке.
    Возвращает (hit_level, r_multiple) или None если ничего не задето.

    Same-bar tie-break (SL+TP в одной свече) определяется SAME_BAR_TIE_BREAK:
      • "conservative" — SL первым, r_multiple=-1.0
      • "fair" (default) — ("TIE", 0.0) → status='tie_hit', не считается
        ни победой, ни классическим лоссом

    Note: klines не имеют timestamps, поэтому считаем что fetch_klines
    вернул свечи с момента entry до now включительно. Это допущение
    верно для большинства fetch'еров (они отдают N последних свечей и
    мы не знаем точно сколько прошло — но для проверки TP/SL это OK,
    нам важен сам факт касания).
    """
    if sl is None or tp1 is None:
        return None

    # Заранее посчитаем уровни TP с RR
    tps = []
    if tp1 is not None:
        tps.append(("TP1", tp1, rr1 or 1.5))
    if tp2 is not None:
        tps.append(("TP2", tp2, rr2 or 2.5))
    if tp3 is not None:
        tps.append(("TP3", tp3, rr3 or 4.0))

    for bar in klines:
        low  = bar.get("l", 0)
        high = bar.get("h", 0)

        if verdict == "LONG":
            sl_hit = low  <= sl
            tp_candidates = [(lvl, p, r) for (lvl, p, r) in tps if high >= p]
        elif verdict == "SHORT":
            sl_hit = high >= sl
            tp_candidates = [(lvl, p, r) for (lvl, p, r) in tps if low <= p]
        else:
            return None

        # Same-bar tie: оба задеты в одной свече
        if sl_hit and tp_candidates:
            if SAME_BAR_TIE_BREAK == "conservative":
                return ("SL", -1.0)
            return ("TIE", 0.0)

        if sl_hit:
            return ("SL", -1.0)

        if tp_candidates:
            # Берём САМЫЙ ДАЛЁКИЙ задетый TP (TP3 > TP2 > TP1 по приоритету)
            tp_candidates.sort(key=lambda x: x[2], reverse=True)
            level, _, rr = tp_candidates[0]
            return (level, float(rr))

    return None


def _close_trade(conn, outcome_id: int, status: str,
                 hit_level: str | None, r_mult: float,
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
        if status in ("open", "skipped", "suppressed"):
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

    return {
        "days":       days,
        "total":      total,
        "open":       by_status.get("open", 0),
        "closed":     closed_n,
        "win_rate":   round(win_rate, 1),
        "avg_r":      round(avg_r, 2),
        "suppressed": by_status.get("suppressed", 0),
        "hits": {
            "tp1": by_status.get("tp1_hit", 0),
            "tp2": by_status.get("tp2_hit", 0),
            "tp3": by_status.get("tp3_hit", 0),
            "sl":  by_status.get("sl_hit",  0),
            "tie": by_status.get("tie_hit", 0),
            "expired": by_status.get("expired", 0),
        },
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

    if closed == 0:
        return (f"📊 <b>Статистика {days} дней</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Всего: {stats['total']} · открыто: {stats['open']}\n"
                f"Пока ни одна сделка не закрылась.")

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
    ]

    if stats.get("suppressed"):
        lines.append(f"  🚫 Подавлено gate: {stats['suppressed']} "
                     f"(не учтены в win-rate)")

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
