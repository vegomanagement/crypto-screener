"""
ote.py — Optimal Trade Entry (OTE) по канону ICT (Этап 12 фаза 3).

PDF SMC учит: entry на Fibonacci retracement 62-79% последнего impulse leg.
Это сладкая зона, где цена обычно реагирует на возврате после структурного
движения.

Алгоритм:
  1. Определить последний impulse leg через structure.detect_structure:
     • Если последнее событие — bull BOS/CHOCH: impulse from swing_low to
       close — направление UP.
     • Если bear: impulse from swing_high to close — направление DOWN.
  2. Для bull-entry (long): OTE zone = [Fib 0.79, Fib 0.62] (deeper part
     of pullback from импульса вверх).
  3. Для bear-entry (short): OTE zone = [Fib 0.62, Fib 0.79] (выше equilibrium).
  4. SL: за extreme импульса + buffer.

Возвращает OTEZone dataclass или None если impulse не найден или слишком стар.

Pure stdlib + import structure. Klines: dict {"o","h","l","c","v"}.
"""

from __future__ import annotations

from dataclasses import dataclass

from structure import detect_structure

__all__ = [
    "OTEZone",
    "compute_ote_zone",
    "FIB_DEEP",
    "FIB_SHALLOW",
    "SL_BUFFER_PCT",
    "MAX_BARS_SINCE_IMPULSE",
]

FIB_DEEP    = 0.79   # глубже в импульс — лучшая sweet spot для разворота
FIB_SHALLOW = 0.62   # неглубокая Fib — край OTE-зоны
SL_BUFFER_PCT = 0.001  # 0.1% за extreme импульса для SL
MAX_BARS_SINCE_IMPULSE = 30   # impulse не должен быть старше 30 баров


@dataclass(frozen=True)
class OTEZone:
    """Optimal Trade Entry zone — Fib 62-79% retracement последнего impulse."""
    direction:        str    # "long" | "short" (signal direction)
    impulse_start_idx: int   # индекс начала импульса
    impulse_end_idx:   int   # индекс конца импульса (= bar with BOS/CHoCH)
    impulse_start:    float  # цена начала импульса (low для bull, high для bear)
    impulse_end:      float  # цена конца импульса
    fib_62:           float  # 0.62 retracement level
    fib_79:           float  # 0.79 retracement level
    entry_min:        float  # min(fib_62, fib_79)
    entry_max:        float  # max(fib_62, fib_79)
    sl:               float  # за extreme импульса + buffer


def compute_ote_zone(
    klines: list,
    direction: str,
    *,
    swing_length: int = 5,
    max_bars_since: int = MAX_BARS_SINCE_IMPULSE,
) -> OTEZone | None:
    """
    Вычислить OTE zone для сигнала direction на основе последнего impulse.

    Возвращает None если:
     • Нет structure events.
     • Последнее событие не совпадает с direction (например, direction='long'
       но последний BOS был bear).
     • Импульс старше max_bars_since баров от current.
     • Klines слишком короткие.
    """
    if direction not in ("long", "short"):
        return None
    if not klines or len(klines) < 2 * swing_length + 2:
        return None

    state = detect_structure(klines, swing_length=swing_length)
    if not state.events:
        return None

    # Берём последнее событие
    last = state.events[-1]
    if last.direction == "bull" and direction != "long":
        return None
    if last.direction == "bear" and direction != "short":
        return None

    # Слишком старый impulse
    if (len(klines) - 1) - last.at > max_bars_since:
        return None

    # Identify impulse start (swing point, который был пробит)
    impulse_start_idx = last.swing_at
    impulse_end_idx   = last.at

    if last.direction == "bull":
        # Impulse: from swing_low_price (level) up to close_price
        impulse_start = last.level   # это был swing high, который был пробит
        # Wait — last.level это уровень who was BROKEN. Для bull BOS/CHOCH
        # was breaks swing HIGH, который был resistance. Но импульс шёл UP от
        # каких-то предыдущих low. Возьмём low бар на swing_at как импульс_start.
        # На самом деле для OTE нужен SWING LOW from which impulse started.

        # Простейшая трактовка: impulse = from swing_at's bar low to event bar high
        bar_at_swing = klines[last.swing_at]
        bar_at_event = klines[last.at]
        impulse_start = bar_at_swing["l"]
        impulse_end   = bar_at_event["h"]
        if impulse_end <= impulse_start:
            return None
        delta = impulse_end - impulse_start
        fib_62 = impulse_end - 0.62 * delta
        fib_79 = impulse_end - 0.79 * delta
        entry_min = min(fib_62, fib_79)
        entry_max = max(fib_62, fib_79)
        sl = impulse_start * (1 - SL_BUFFER_PCT)
    else:
        # Bear: impulse from swing_high down to event_bar's low
        bar_at_swing = klines[last.swing_at]
        bar_at_event = klines[last.at]
        impulse_start = bar_at_swing["h"]
        impulse_end   = bar_at_event["l"]
        if impulse_end >= impulse_start:
            return None
        delta = impulse_start - impulse_end
        fib_62 = impulse_end + 0.62 * delta
        fib_79 = impulse_end + 0.79 * delta
        entry_min = min(fib_62, fib_79)
        entry_max = max(fib_62, fib_79)
        sl = impulse_start * (1 + SL_BUFFER_PCT)

    return OTEZone(
        direction=direction,
        impulse_start_idx=impulse_start_idx,
        impulse_end_idx=impulse_end_idx,
        impulse_start=float(impulse_start),
        impulse_end=float(impulse_end),
        fib_62=float(fib_62),
        fib_79=float(fib_79),
        entry_min=float(entry_min),
        entry_max=float(entry_max),
        sl=float(sl),
    )
