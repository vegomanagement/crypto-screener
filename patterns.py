"""
patterns.py — детект продвинутых SMC-паттернов (Этап 11, фаза 1).

Текущее: sweep+reclaim — классика SMC. Цена снимает swing-точку (low или
high) и в ближайшие 1-2 свечи возвращается обратно внутрь (reclaim) —
ловушка для тех, кто заходил по пробою, и сигнал на разворот.

Направление сигнала ОБРАТНО снятию: sweep низа (swing low снят) → bull
setup (ждём отскок наверх).

Использует pivot-swings из structure.py — независимо от liquidity.py
(после Этапа 9 untapped-pruning удаляет swept-пулы из карты, так что
liquidity_map недоступен для этого детекта).

Pure stdlib + import structure. Klines: dict {"o","h","l","c","v"}.
"""

from __future__ import annotations

from dataclasses import dataclass

from structure import find_swing_points

__all__ = [
    "SweepReclaim",
    "find_sweep_reclaim_events",
    "latest_sweep_reclaim",
    "DEFAULT_SWING_LENGTH",
    "DEFAULT_LOOKBACK_BARS",
    "DEFAULT_MAX_BARS_AGO",
    "MAX_RECLAIM_BARS",
]

DEFAULT_SWING_LENGTH = 5
DEFAULT_LOOKBACK_BARS = 30   # сколько последних баров смотрим на sweep
DEFAULT_MAX_BARS_AGO  = 10   # «свежесть» события для latest_sweep_reclaim
MAX_RECLAIM_BARS      = 2    # sweep и reclaim должны быть в пределах K баров


@dataclass(frozen=True)
class SweepReclaim:
    """Завершённое событие sweep+reclaim."""
    direction:    str    # "bull" (snyat низ) | "bear" (snyat верх)
    sweep_idx:    int
    reclaim_idx:  int
    swing_at:     int
    swing_kind:   str    # "L" или "H"
    swing_price:  float  # уровень, который был снят
    sweep_extreme: float  # как далеко цена ушла (low баром снятия, или high)
    reclaim_close: float


def _find_reclaim_after_sweep(
    klines: list,
    start_idx: int,
    direction: str,
    level: float,
) -> int | None:
    """
    Ищет первую свечу в [start_idx, start_idx + MAX_RECLAIM_BARS], которая
    закрывается обратно за level. Возвращает индекс или None.
    """
    end = min(len(klines), start_idx + MAX_RECLAIM_BARS + 1)
    for j in range(start_idx, end):
        rb = klines[j]
        c = rb["c"]
        if direction == "bull" and c > level:
            return j
        if direction == "bear" and c < level:
            return j
    return None


def find_sweep_reclaim_events(
    klines: list,
    swing_length: int = DEFAULT_SWING_LENGTH,
    lookback: int = DEFAULT_LOOKBACK_BARS,
) -> list[SweepReclaim]:
    """
    Все sweep+reclaim события в последних `lookback` барах klines.

    Алгоритм:
      1. Собрать pivot swings (structure.find_swing_points) на всех klines.
      2. Для каждого свинга смотреть, был ли пробит экстремум в ближайших
         lookback барах ПОСЛЕ его подтверждения (i > swing.index + length).
      3. Если пробит — искать reclaim в пределах MAX_RECLAIM_BARS.
      4. Регистрируем событие; на один свинг — одно первое событие.
    """
    if len(klines) < 2 * swing_length + 1:
        return []

    swings = find_swing_points(klines, swing_length)
    if not swings:
        return []

    # Sweep ищем в окне [len-lookback, len-1] — что-то «свежее».
    sweep_window_start = max(0, len(klines) - lookback)

    events: list[SweepReclaim] = []
    for s in swings:
        sweep_search_start = max(s.index + swing_length, sweep_window_start)
        # Sweep должен быть СВЕЖИМ (в lookback окне) и ПОСЛЕ подтверждения свинга.
        if sweep_search_start >= len(klines):
            continue

        for i in range(sweep_search_start, len(klines)):
            bar = klines[i]
            if s.kind == "L":
                if bar["l"] < s.price:
                    rec_j = _find_reclaim_after_sweep(
                        klines, i, "bull", s.price)
                    if rec_j is not None:
                        events.append(SweepReclaim(
                            direction="bull",
                            sweep_idx=i, reclaim_idx=rec_j,
                            swing_at=s.index, swing_kind="L",
                            swing_price=float(s.price),
                            sweep_extreme=float(bar["l"]),
                            reclaim_close=float(klines[rec_j]["c"]),
                        ))
                    break  # один sweep на свинг
            elif s.kind == "H":
                if bar["h"] > s.price:
                    rec_j = _find_reclaim_after_sweep(
                        klines, i, "bear", s.price)
                    if rec_j is not None:
                        events.append(SweepReclaim(
                            direction="bear",
                            sweep_idx=i, reclaim_idx=rec_j,
                            swing_at=s.index, swing_kind="H",
                            swing_price=float(s.price),
                            sweep_extreme=float(bar["h"]),
                            reclaim_close=float(klines[rec_j]["c"]),
                        ))
                    break

    events.sort(key=lambda e: e.reclaim_idx)
    return events


def latest_sweep_reclaim(
    klines: list,
    swing_length: int = DEFAULT_SWING_LENGTH,
    lookback: int = DEFAULT_LOOKBACK_BARS,
    max_bars_ago: int = DEFAULT_MAX_BARS_AGO,
) -> SweepReclaim | None:
    """
    Самое свежее событие, если оно не старше `max_bars_ago` баров.
    Удобный обёртка для real-time-детекта в auto_scan.
    """
    if not klines:
        return None
    events = find_sweep_reclaim_events(klines, swing_length, lookback)
    if not events:
        return None
    last = events[-1]
    if (len(klines) - 1) - last.reclaim_idx > max_bars_ago:
        return None
    return last
