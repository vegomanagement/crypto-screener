"""
signal_gate.py — координация исходящих сигналов.

Решает две проблемы:
  1. Aggregator — за короткое окно по символу может прийти несколько
     противоречащих TV-алертов (BOS_BULL 5m + CHOCH_BEAR 5m + …).
     Буферим их, в конце окна шлём ОДИН лучший сигнал.
  2. Cooldown — после отправки сигнала по символу блокируем
     повторные/противоположные сигналы на TF-зависимое время,
     чтобы не противоречить уже открытой позиции.

Reversal допускается только если новый verdict уверенно лучше
активного (confidence новый > активный + REVERSAL_CONF_DELTA).

Чистый модуль без зависимостей от screener — для упрощения тестов.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

log = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────

# Cooldown в минутах после успешной отправки. Ключи — нормализованные TF.
TF_COOLDOWN_MIN: dict[str, int] = {
    "1":   5,
    "3":   5,
    "5":   10,
    "15":  20,
    "30":  30,
    "60":  60,
    "120": 90,
    "240": 240,
    "D":   1440,
    "W":   10080,
    "M":   43200,
}

# Размер окна aggregator: первый сигнал диктует длительность.
# Маленький TF → быстрый flush; высокий TF → больше времени собрать confluence.
ADAPTIVE_WINDOW_SEC: dict[str, int] = {
    "1":   5,
    "3":   5,
    "5":   5,
    "15":  15,
    "30":  15,
    "60":  30,
    "120": 30,
    "240": 30,
    "D":   30,
    "W":   30,
    "M":   30,
}
DEFAULT_WINDOW_SEC = 15

# Чем выше число — тем приоритетнее winner.
TF_PRIORITY: dict[str, int] = {
    "1":   1, "3": 1, "5": 2,
    "15":  3, "30": 4,
    "60":  5, "120": 6, "240": 7,
    "D":   8, "W": 9, "M": 10,
}

# Приоритет типов сигналов (BOS — самый структурный, ICT-сессии — слабые).
SIGNAL_TYPE_PRIORITY: dict[str, int] = {
    "BOS":        100,
    "CHOCH":      90,
    "LIQ_SWEEP":  80,
    "OB":         70,
    "TURTLE":     65,
    "RSI_DIV":    55,
    "EMA_CROSS":  50,
    "FVG":        40,
    "VOL_SPIKE":  35,
    "EQH":        30,
    "EQL":        30,
    "ICT":        20,
    "DAILY_OPEN":   15,
    "WEEKLY_OPEN":  15,
    "MONTHLY_OPEN": 15,
    "ALERT":      10,
}

REVERSAL_CONF_DELTA = 15


# ─── Helpers ──────────────────────────────────────────────────────────────

def normalize_tf(tf: str | int | None) -> str:
    """Привести TF к канонической строке: '5', '60', 'D' и т.п."""
    if tf is None:
        return ""
    s = str(tf).strip().upper()
    # TradingView иногда шлёт '1H', '4H', '1D', '15M'
    aliases = {
        "1M": "1", "3M": "3", "5M": "5", "15M": "15", "30M": "30",
        "1H": "60", "2H": "120", "4H": "240",
        "1D": "D", "1W": "W", "1MO": "M", "MO": "M",
        "60M": "60",
    }
    return aliases.get(s, s)


def tf_priority(tf: str) -> int:
    return TF_PRIORITY.get(normalize_tf(tf), 0)


def signal_type_priority(sig_type: str) -> int:
    """Match по префиксу: 'BOS_BULL' → 'BOS' → 100."""
    s = (sig_type or "").upper()
    for prefix, score in SIGNAL_TYPE_PRIORITY.items():
        if s.startswith(prefix):
            return score
    return 0


def cooldown_minutes(tf: str) -> int:
    return TF_COOLDOWN_MIN.get(normalize_tf(tf), 30)


def aggregator_window(tf: str) -> int:
    return ADAPTIVE_WINDOW_SEC.get(normalize_tf(tf), DEFAULT_WINDOW_SEC)


# Ключи в TradingView alert payload, где может лежать timestamp алерта.
# TV-Pine `{{timenow}}` → ISO UTC. `{{time}}` — Unix ms. Кастомные алерты —
# любая из этих переменных. Пробуем по очереди.
_ALERT_TS_KEYS = ("time", "timestamp", "alert_time", "alertTime",
                  "alertTimestamp", "ts")


def parse_alert_ts(payload: dict) -> datetime | None:
    """
    Возвращает aware UTC datetime из TradingView alert payload или None,
    если timestamp отсутствует/неразбираем. None означает, что вызывающая
    сторона должна fallback'нуть на now() (это поведение по умолчанию в
    killzones.in_killzone).

    Поддерживаются:
      • Unix seconds (int/float)
      • Unix milliseconds (int/float, >= 1e12)
      • ISO 8601 с 'Z' или offset нотацией ('2026-05-30T19:28:00Z')
    """
    if not payload:
        return None
    for key in _ALERT_TS_KEYS:
        v = payload.get(key)
        if v is None or v == "":
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            try:
                secs = float(v) / 1000.0 if v > 1e12 else float(v)
                return datetime.fromtimestamp(secs, tz=timezone.utc)
            except (OSError, ValueError, OverflowError):
                continue
        if isinstance(v, str):
            s = v.strip()
            # Числовая строка — попробуем как epoch
            try:
                num = float(s)
                secs = num / 1000.0 if num > 1e12 else num
                return datetime.fromtimestamp(secs, tz=timezone.utc)
            except ValueError:
                pass
            # ISO 8601
            try:
                iso = s.replace("Z", "+00:00")
                return datetime.fromisoformat(iso).astimezone(timezone.utc)
            except ValueError:
                continue
    return None


def verdict_from_signal_type(sig_type: str) -> str | None:
    """Эвристика для aggregator до запуска decision engine."""
    s = (sig_type or "").upper()
    if any(k in s for k in ("BULL", "LONG", "SWEEP_L", "EQL", "DIV_BULL", "CROSS_BULL")):
        return "LONG"
    if any(k in s for k in ("BEAR", "SHORT", "SWEEP_H", "EQH", "DIV_BEAR", "CROSS_BEAR")):
        return "SHORT"
    return None


def opposite(verdict: str) -> str:
    return "SHORT" if verdict == "LONG" else "LONG"


# ─── Schema ───────────────────────────────────────────────────────────────

DISPATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_dispatch (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    verdict         TEXT NOT NULL,
    tf              TEXT,
    signal_type     TEXT,
    confidence      INTEGER,
    sent_at         TEXT NOT NULL,
    cooldown_until  TEXT NOT NULL,
    outcome_id      INTEGER,
    note            TEXT
)
"""
DISPATCH_INDEX = """
CREATE INDEX IF NOT EXISTS idx_dispatch_active
ON signal_dispatch(symbol, cooldown_until)
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(DISPATCH_SCHEMA)
    conn.execute(DISPATCH_INDEX)
    conn.commit()


# ─── Cooldown gate ────────────────────────────────────────────────────────

@dataclass
class ActiveDispatch:
    id:          int
    verdict:     str
    tf:          str | None
    confidence:  int
    sent_at:     str
    cooldown_until: str


@dataclass
class GateDecision:
    """Результат проверки cooldown gate."""
    action:        str            # "send" | "suppress" | "reversal"
    reason:        str
    active:        ActiveDispatch | None = None


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_active_dispatch(conn: sqlite3.Connection, symbol: str) -> ActiveDispatch | None:
    """Последняя dispatch-запись по символу, чей cooldown ещё не истёк."""
    now = _now_str()
    row = conn.execute(
        """
        SELECT id, verdict, tf, confidence, sent_at, cooldown_until
        FROM signal_dispatch
        WHERE symbol = ? AND cooldown_until > ?
        ORDER BY sent_at DESC
        LIMIT 1
        """,
        (symbol, now),
    ).fetchone()
    if not row:
        return None
    return ActiveDispatch(
        id=row[0], verdict=row[1], tf=row[2],
        confidence=row[3] or 0, sent_at=row[4], cooldown_until=row[5],
    )


def cooldown_check(
    conn: sqlite3.Connection,
    symbol: str,
    verdict: str,
    confidence: int,
    tf: str,
) -> GateDecision:
    """
    Применяет правила:
      • Нет активного → send
      • Активный того же направления:
          – новый TF ≤ активный TF → suppress (нижний TF не нужен поверх)
          – новый TF > активный TF → reversal (апгрейд позиции)
      • Активный противоположного направления:
          – new conf > active conf + REVERSAL_CONF_DELTA → reversal
          – иначе → suppress
    """
    if verdict not in ("LONG", "SHORT"):
        return GateDecision("send", "non-tradeable verdict, no gating")

    active = get_active_dispatch(conn, symbol)
    if active is None:
        return GateDecision("send", "no active dispatch")

    new_tf_p = tf_priority(tf)
    act_tf_p = tf_priority(active.tf or "")

    if active.verdict == verdict:
        if new_tf_p > act_tf_p:
            return GateDecision(
                "reversal",
                f"upgrade same-direction TF "
                f"{active.tf}→{normalize_tf(tf)}",
                active,
            )
        return GateDecision(
            "suppress",
            f"already active {verdict} (TF {active.tf}, "
            f"conf {active.confidence})",
            active,
        )

    # Противоположное направление
    if confidence > (active.confidence or 0) + REVERSAL_CONF_DELTA:
        return GateDecision(
            "reversal",
            f"new conf {confidence} > active {active.confidence}+{REVERSAL_CONF_DELTA}",
            active,
        )
    return GateDecision(
        "suppress",
        f"conflicts with active {active.verdict} "
        f"(new conf {confidence} ≤ {active.confidence}+{REVERSAL_CONF_DELTA})",
        active,
    )


def record_dispatch(
    conn: sqlite3.Connection,
    symbol: str,
    verdict: str,
    tf: str,
    signal_type: str,
    confidence: int,
    outcome_id: int | None = None,
    note: str | None = None,
) -> int:
    """Запись об успешно отправленном сигнале + cooldown_until."""
    now = datetime.now(timezone.utc)
    cd_min = cooldown_minutes(tf)
    until = (now + timedelta(minutes=cd_min)).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        """
        INSERT INTO signal_dispatch(
            symbol, verdict, tf, signal_type, confidence,
            sent_at, cooldown_until, outcome_id, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            symbol, verdict, normalize_tf(tf), signal_type, int(confidence or 0),
            now.strftime("%Y-%m-%d %H:%M:%S"), until, outcome_id, note,
        ),
    )
    conn.commit()
    return cur.lastrowid


# ─── Aggregator ───────────────────────────────────────────────────────────

@dataclass
class BufferedSignal:
    payload:   dict
    tf:        str
    sig_type:  str
    verdict:   str | None     # эвристика по типу
    added_at:  float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())


def _score(sig: BufferedSignal) -> tuple:
    """Сортировочный ключ winner-а. Больше = лучше."""
    return (
        tf_priority(sig.tf),
        signal_type_priority(sig.sig_type),
        sig.added_at,
    )


class SignalAggregator:
    """
    Буферизирует входящие сигналы по символу. Когда окно истекает —
    выбирает лучший по приоритету (TF → тип → свежесть) и вызывает
    callback с winner + список suppressed.

    Окно начинается с первого сигнала по символу. Размер окна — по
    TF этого первого сигнала (adaptive). Последующие сигналы того же
    символа в течение окна попадают в тот же буфер.
    """

    def __init__(
        self,
        callback: Callable[[BufferedSignal, list[BufferedSignal]], None],
        window_fn: Callable[[str], int] = aggregator_window,
        timer_factory: Callable | None = None,
    ):
        self._callback = callback
        self._window_fn = window_fn
        # timer_factory для тестов (например, fake clock)
        self._timer_factory = timer_factory or threading.Timer
        self._buffer: dict[str, list[BufferedSignal]] = defaultdict(list)
        self._timers: dict[str, object] = {}
        self._lock = threading.Lock()

    def submit(self, symbol: str, payload: dict) -> None:
        """
        Поставить сигнал в очередь. Webhook вызывает это и сразу возвращает
        ответ TV — фактическая отправка произойдёт в _flush через окно.
        """
        sig_type = (payload.get("signal", "ALERT") or "ALERT").upper()
        tf = str(payload.get("tf", payload.get("interval", "")))
        buf = BufferedSignal(
            payload=payload,
            tf=tf,
            sig_type=sig_type,
            verdict=verdict_from_signal_type(sig_type),
        )

        with self._lock:
            self._buffer[symbol].append(buf)
            existing = self._timers.get(symbol)

        if existing is None:
            window = self._window_fn(tf)
            log.info(
                f"[aggregator] {symbol}: new window {window}s "
                f"(first sig {sig_type} TF={tf})"
            )
            self._schedule(symbol, window)
        else:
            log.info(
                f"[aggregator] {symbol}: queued {sig_type} TF={tf} "
                f"(buffer size={len(self._buffer[symbol])})"
            )

    def _schedule(self, symbol: str, window: int) -> None:
        t = self._timer_factory(window, self._flush, args=[symbol])
        # threading.Timer specific — игнорируется тестовыми фабриками
        if hasattr(t, "daemon"):
            t.daemon = True
        if hasattr(t, "start"):
            t.start()
        with self._lock:
            self._timers[symbol] = t

    def _flush(self, symbol: str) -> None:
        with self._lock:
            items = self._buffer.pop(symbol, [])
            self._timers.pop(symbol, None)

        if not items:
            return

        winner, suppressed = self._pick_winner(items)
        log.info(
            f"[aggregator] {symbol}: flush — winner={winner.sig_type} "
            f"TF={winner.tf}, suppressed={len(suppressed)}"
        )
        try:
            self._callback(winner, suppressed)
        except Exception as e:
            log.exception(f"[aggregator] callback failed for {symbol}: {e}")

    def _pick_winner(
        self, items: list[BufferedSignal]
    ) -> tuple[BufferedSignal, list[BufferedSignal]]:
        ordered = sorted(items, key=_score, reverse=True)
        return ordered[0], ordered[1:]

    # ─── Тестовый/диагностический API ─────────────────────────────────────

    def flush_now(self, symbol: str) -> None:
        """Принудительно завершить окно для символа (для тестов)."""
        with self._lock:
            t = self._timers.get(symbol)
        # Cancel pending timer if it has .cancel()
        if t is not None and hasattr(t, "cancel"):
            try:
                t.cancel()
            except Exception:
                pass
        self._flush(symbol)

    def pending_symbols(self) -> list[str]:
        with self._lock:
            return list(self._buffer.keys())


# ─── Formatting helpers (used by screener) ────────────────────────────────

def format_suppressed_note(suppressed: list[BufferedSignal]) -> str:
    """HTML-фрагмент для Telegram-сообщения о подавленных сигналах в окне."""
    if not suppressed:
        return ""
    parts = []
    for s in suppressed[:5]:
        parts.append(f"{s.sig_type} {normalize_tf(s.tf) or '?'}")
    extra = f" +{len(suppressed) - 5}" if len(suppressed) > 5 else ""
    return f"\n📋 За окно также: {', '.join(parts)}{extra}"


def format_reversal_note(active: ActiveDispatch, new_verdict: str) -> str:
    return (
        f"\n🔄 <b>РАЗВОРОТ:</b> предыдущий {active.verdict} "
        f"(conf {active.confidence}, TF {active.tf or '?'}) "
        f"перебит новым {new_verdict}. Закрой старую позицию."
    )
