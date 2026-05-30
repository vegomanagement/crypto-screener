"""
structure.py — детект слома структуры рынка (Этап 10, фаза 2).

ICT-методология (публичная): swing-точки → BOS (Break of Structure,
продолжение тренда) и CHoCH (Change of Character, разворот). Реализация
независимая (не порт LuxAlgo Pine).

Для killzone-фичи: подтверждение слома структуры на 5m+15m в одном
направлении — сигнал для движка о реальном смещении.

Алгоритм:
  1. Pivot swing-точки: бар i — swing high, если его high — максимум окна
     [i-length, i+length]. Аналогично swing low.
  2. Walk klines: на каждом баре активируем уже подтверждённые swings
     (length баров прошло после них). При close выше последнего активного
     swing high → событие (BOS если тренд был bull, CHoCH если bear).
     Симметрично для swing low.

Чистый модуль, только stdlib. Klines: dict {"o","h","l","c","v"}.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "Swing",
    "StructureEvent",
    "StructureState",
    "find_swing_points",
    "detect_structure",
    "latest_break",
    "confirmed_break_5m_15m",
    "DEFAULT_SWING_LENGTH",
    "DEFAULT_MAX_BARS_AGO_5M",
    "DEFAULT_MAX_BARS_AGO_15M",
]

DEFAULT_SWING_LENGTH = 5
DEFAULT_MAX_BARS_AGO_5M = 20      # 100 минут на 5m
DEFAULT_MAX_BARS_AGO_15M = 10     # 150 минут на 15m


@dataclass(frozen=True)
class Swing:
    """Pivot swing-точка."""
    index: int
    kind: str          # "H" (high) or "L" (low)
    price: float


@dataclass(frozen=True)
class StructureEvent:
    """Событие слома структуры — BOS или CHoCH."""
    kind: str           # "BOS" or "CHOCH"
    direction: str      # "bull" or "bear"
    at: int             # индекс свечи, на которой произошёл слом
    swing_at: int       # индекс свинга, который был сломан
    level: float        # цена сломанного уровня
    close: float        # close свечи слома


@dataclass
class StructureState:
    """Полный результат разбора klines."""
    events: list[StructureEvent] = field(default_factory=list)
    trend: str = "neutral"          # "bull" / "bear" / "neutral"
    last_swing_high: Swing | None = None
    last_swing_low: Swing | None = None


def find_swing_points(klines: list[dict],
                      length: int = DEFAULT_SWING_LENGTH) -> list[Swing]:
    """
    Pivot swings: бар i — swing high, если его high равен максимуму окна
    klines[i-length : i+length+1]. Аналогично swing low по low. Бар, который
    одновременно swing high и low (плоская свеча в плоском окне), даёт две
    записи. Возвращает swings, отсортированные по индексу.
    """
    if length <= 0 or len(klines) < 2 * length + 1:
        return []

    swings: list[Swing] = []
    for i in range(length, len(klines) - length):
        window = klines[i - length: i + length + 1]
        h = klines[i]["h"]
        ll = klines[i]["l"]
        if h == max(b["h"] for b in window):
            swings.append(Swing(index=i, kind="H", price=float(h)))
        if ll == min(b["l"] for b in window):
            swings.append(Swing(index=i, kind="L", price=float(ll)))
    swings.sort(key=lambda s: s.index)
    return swings


def detect_structure(klines: list[dict],
                     swing_length: int = DEFAULT_SWING_LENGTH) -> StructureState:
    """
    Walk klines в хронологическом порядке. Когда close уходит выше
    последнего подтверждённого swing high → событие (BOS если уже bull-тренд,
    CHoCH если был bear). Симметрично для swing low.

    «Подтверждённый» = прошло swing_length баров после самой свинг-свечи
    (только тогда мы достоверно знаем, что это был экстремум).

    После пробоя swing считается «consumed» — ждём нового свинга в эту сторону.
    """
    if not klines:
        return StructureState()

    swings = find_swing_points(klines, swing_length)
    swing_by_idx: dict[int, list[Swing]] = {}
    for s in swings:
        swing_by_idx.setdefault(s.index, []).append(s)

    state = StructureState()
    last_sh: Swing | None = None
    last_sl: Swing | None = None
    pending_swings = list(swings)  # очередь к активации

    for i, bar in enumerate(klines):
        # Активируем подтверждённые свинги: все s, где s.index + length <= i.
        # Берём САМЫЕ свежие — они перебивают старые активные неподтверждённые.
        still_pending: list[Swing] = []
        for s in pending_swings:
            if s.index + swing_length <= i:
                if s.kind == "H":
                    if last_sh is None or s.index > last_sh.index:
                        last_sh = s
                else:
                    if last_sl is None or s.index > last_sl.index:
                        last_sl = s
            else:
                still_pending.append(s)
        pending_swings = still_pending

        close = float(bar["c"])

        # Слом вверх — close выше последнего активного swing high
        if last_sh is not None and close > last_sh.price:
            kind = "BOS" if state.trend == "bull" else "CHOCH"
            state.events.append(StructureEvent(
                kind=kind, direction="bull", at=i, swing_at=last_sh.index,
                level=last_sh.price, close=close,
            ))
            state.trend = "bull"
            last_sh = None

        # Слом вниз
        if last_sl is not None and close < last_sl.price:
            kind = "BOS" if state.trend == "bear" else "CHOCH"
            state.events.append(StructureEvent(
                kind=kind, direction="bear", at=i, swing_at=last_sl.index,
                level=last_sl.price, close=close,
            ))
            state.trend = "bear"
            last_sl = None

    state.last_swing_high = last_sh
    state.last_swing_low = last_sl
    return state


def latest_break(klines: list[dict],
                 swing_length: int = DEFAULT_SWING_LENGTH,
                 max_bars_ago: int | None = None) -> StructureEvent | None:
    """
    Самое свежее событие BOS/CHoCH, если оно не старше max_bars_ago.
    None если событий нет или последнее слишком старое.
    """
    state = detect_structure(klines, swing_length)
    if not state.events:
        return None
    ev = state.events[-1]
    if max_bars_ago is None:
        return ev
    if (len(klines) - 1) - ev.at > max_bars_ago:
        return None
    return ev


def confirmed_break_5m_15m(
    klines_5m: list[dict],
    klines_15m: list[dict],
    swing_length: int = DEFAULT_SWING_LENGTH,
    max_bars_ago_5m: int = DEFAULT_MAX_BARS_AGO_5M,
    max_bars_ago_15m: int = DEFAULT_MAX_BARS_AGO_15M,
) -> dict | None:
    """
    Подтверждённый слом структуры на двух ТФ в одном направлении.

    Возвращает:
        {"direction": "bull" | "bear",
         "kind_5m":   "BOS" | "CHOCH",
         "kind_15m":  "BOS" | "CHOCH",
         "events":    {"5m": StructureEvent, "15m": StructureEvent}}

    Или None, если хотя бы один ТФ без свежего слома или направления
    не совпадают. Используется фазой 3 как hard-gate подтверждения killzone.
    """
    b5 = latest_break(klines_5m, swing_length, max_bars_ago_5m)
    b15 = latest_break(klines_15m, swing_length, max_bars_ago_15m)
    if b5 is None or b15 is None:
        return None
    if b5.direction != b15.direction:
        return None
    return {
        "direction": b5.direction,
        "kind_5m":   b5.kind,
        "kind_15m":  b15.kind,
        "events":    {"5m": b5, "15m": b15},
    }
