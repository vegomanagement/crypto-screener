"""
block_patterns.py — Mitigation Block (MB) + Breaker Block (BB) ICT
паттерны (Этап 12 фаза 4).

PDF SMC differentiates:

  • Mitigation Block (bear): swing low L был пробит вниз, но swing high H
    НЕ был пробит (диапазон сохранён). Цена возвращается к уровню L
    → bear-вход. Покупатели, что лонговали L, теперь под водой и хотят
    митигировать убыток (продают своё long).

  • Breaker Block (bear): swing high H был пробит ВВЕРХ, потом цена
    развернулась и сломала противоположный swing low. Цена возвращается
    к уровню H → bear-вход. Те, кто купил пробой H, теперь под водой.

Симметрично для bullish setups (с обратными extremes).

Ключевая разница MB vs BB:
  • MB: range intact (one extreme violated, other intact)
  • BB: full range break + reversal (both extremes violated)

Использует pivot swings из structure.py.
Pure stdlib. Klines: dict {"o","h","l","c","v"}.
"""

from __future__ import annotations

from dataclasses import dataclass

from structure import find_swing_points

__all__ = [
    "BlockPattern",
    "find_mitigation_blocks",
    "find_breaker_blocks",
    "latest_mb_test",
    "latest_bb_test",
    "DEFAULT_SWING_LENGTH",
    "DEFAULT_LOOKBACK",
    "DEFAULT_MAX_BARS_AGO",
]

DEFAULT_SWING_LENGTH = 5
DEFAULT_LOOKBACK     = 50
DEFAULT_MAX_BARS_AGO = 10


@dataclass(frozen=True)
class BlockPattern:
    """Mitigation или Breaker Block."""
    kind:         str    # "MB" or "BB"
    direction:    str    # "bull" or "bear" (signal direction)
    level:        float  # уровень, который тестируется
    swing_at:     int    # индекс original swing
    swing_kind:   str    # "H" or "L"
    violated_at:  int    # бар первого violation
    test_at:      int    # бар, тестирующий уровень обратно


def _max_swing_before(swings: list, idx: int, kind: str) -> tuple[int, float] | None:
    """Самый недавний swing типа kind с index < idx. Возвращает (idx, price)."""
    for s in reversed(swings):
        if s.index < idx and s.kind == kind:
            return (s.index, s.price)
    return None


def _has_violation(klines: list, start_idx: int, end_idx: int,
                   level: float, direction: str) -> int | None:
    """
    Возвращает индекс первого бара в (start_idx, end_idx], где цена
    нарушает level в указанном direction:
      direction='down': bar.l < level
      direction='up':   bar.h > level
    None если нарушения нет.
    """
    for i in range(start_idx + 1, min(end_idx + 1, len(klines))):
        bar = klines[i]
        if direction == "down" and bar["l"] < level:
            return i
        if direction == "up" and bar["h"] > level:
            return i
    return None


# ─── Mitigation Block ─────────────────────────────────────────────────────


def find_mitigation_blocks(
    klines: list,
    swing_length: int = DEFAULT_SWING_LENGTH,
    lookback: int = DEFAULT_LOOKBACK,
) -> list[BlockPattern]:
    """
    Найти MB-кандидаты в последних `lookback` барах.

    Bearish MB:
      1. Есть swing low L_idx с price L
      2. Bar i ∈ (L_idx+length, lookback_end]: l[i] < L (violation low)
      3. Опор swing high H_idx ДО L: max swing high в swings[..L_idx]
         Не нарушен (никакой бар в [L_idx, current] не имеет h > H)
      4. Какой-то bar j > i имеет h ≥ L (return to level)
    Mirror для bullish MB (extreme = swing high).
    """
    if len(klines) < 2 * swing_length + 1:
        return []

    swings = find_swing_points(klines, swing_length)
    if not swings:
        return []

    out: list[BlockPattern] = []
    scan_start = max(0, len(klines) - lookback)

    for s in swings:
        if s.index < scan_start:
            continue

        if s.kind == "L":
            # Потенциальный bearish MB на этом swing low.
            violation = _has_violation(
                klines, s.index, len(klines) - 1, s.price, "down")
            if violation is None:
                continue
            # Опор swing high до L. Если его нет — skip.
            ref_high = _max_swing_before(swings, s.index, "H")
            if ref_high is None:
                continue
            ref_h_idx, ref_h_price = ref_high
            # Проверка: high не нарушен между L и текущим
            if _has_violation(klines, ref_h_idx, len(klines) - 1,
                              ref_h_price, "up") is not None:
                continue
            # Test: бар после violation с h ≥ L
            test_idx = None
            for j in range(violation + 1, len(klines)):
                if klines[j]["h"] >= s.price:
                    test_idx = j
                    break
            if test_idx is None:
                continue
            out.append(BlockPattern(
                kind="MB", direction="bear",
                level=float(s.price), swing_at=s.index, swing_kind="L",
                violated_at=violation, test_at=test_idx,
            ))

        elif s.kind == "H":
            # Bullish MB на swing high.
            violation = _has_violation(
                klines, s.index, len(klines) - 1, s.price, "up")
            if violation is None:
                continue
            ref_low = _max_swing_before(swings, s.index, "L")
            if ref_low is None:
                continue
            ref_l_idx, ref_l_price = ref_low
            if _has_violation(klines, ref_l_idx, len(klines) - 1,
                              ref_l_price, "down") is not None:
                continue
            test_idx = None
            for j in range(violation + 1, len(klines)):
                if klines[j]["l"] <= s.price:
                    test_idx = j
                    break
            if test_idx is None:
                continue
            out.append(BlockPattern(
                kind="MB", direction="bull",
                level=float(s.price), swing_at=s.index, swing_kind="H",
                violated_at=violation, test_at=test_idx,
            ))

    out.sort(key=lambda b: b.test_at)
    return out


# ─── Breaker Block ────────────────────────────────────────────────────────


def find_breaker_blocks(
    klines: list,
    swing_length: int = DEFAULT_SWING_LENGTH,
    lookback: int = DEFAULT_LOOKBACK,
) -> list[BlockPattern]:
    """
    Bearish BB:
      1. swing high H_idx с price H
      2. Bar i ∈ (H_idx, lookback_end]: h[i] > H (violation up)
      3. Опор swing low до H: было нарушено вниз (price made lower low)
         после violation — то есть произошёл trend reversal
      4. Bar j > violated_low_idx: h[j] ≥ H (return to level)
    Mirror для bullish BB.
    """
    if len(klines) < 2 * swing_length + 1:
        return []

    swings = find_swing_points(klines, swing_length)
    if not swings:
        return []

    out: list[BlockPattern] = []
    scan_start = max(0, len(klines) - lookback)

    for s in swings:
        if s.index < scan_start:
            continue

        if s.kind == "H":
            # Bearish BB
            violation = _has_violation(
                klines, s.index, len(klines) - 1, s.price, "up")
            if violation is None:
                continue
            ref_low = _max_swing_before(swings, s.index, "L")
            if ref_low is None:
                continue
            ref_l_idx, ref_l_price = ref_low
            # Опор swing low ДОЛЖЕН быть нарушен после violation (reversal)
            opp_violation = _has_violation(
                klines, violation, len(klines) - 1, ref_l_price, "down")
            if opp_violation is None:
                continue
            # Test: после opp_violation бар с h ≥ H
            test_idx = None
            for j in range(opp_violation + 1, len(klines)):
                if klines[j]["h"] >= s.price:
                    test_idx = j
                    break
            if test_idx is None:
                continue
            out.append(BlockPattern(
                kind="BB", direction="bear",
                level=float(s.price), swing_at=s.index, swing_kind="H",
                violated_at=violation, test_at=test_idx,
            ))

        elif s.kind == "L":
            # Bullish BB
            violation = _has_violation(
                klines, s.index, len(klines) - 1, s.price, "down")
            if violation is None:
                continue
            ref_high = _max_swing_before(swings, s.index, "H")
            if ref_high is None:
                continue
            ref_h_idx, ref_h_price = ref_high
            opp_violation = _has_violation(
                klines, violation, len(klines) - 1, ref_h_price, "up")
            if opp_violation is None:
                continue
            test_idx = None
            for j in range(opp_violation + 1, len(klines)):
                if klines[j]["l"] <= s.price:
                    test_idx = j
                    break
            if test_idx is None:
                continue
            out.append(BlockPattern(
                kind="BB", direction="bull",
                level=float(s.price), swing_at=s.index, swing_kind="L",
                violated_at=violation, test_at=test_idx,
            ))

    out.sort(key=lambda b: b.test_at)
    return out


# ─── latest_*_test convenience ─────────────────────────────────────────────


def _latest_within(blocks: list[BlockPattern], n_klines: int,
                   max_bars_ago: int) -> BlockPattern | None:
    """Самый свежий block с test_at не старше max_bars_ago от last bar."""
    if not blocks:
        return None
    last = blocks[-1]
    if (n_klines - 1) - last.test_at > max_bars_ago:
        return None
    return last


def latest_mb_test(
    klines: list,
    swing_length: int = DEFAULT_SWING_LENGTH,
    lookback: int = DEFAULT_LOOKBACK,
    max_bars_ago: int = DEFAULT_MAX_BARS_AGO,
) -> BlockPattern | None:
    """Самый свежий MB-test в окне max_bars_ago от current bar."""
    if not klines:
        return None
    blocks = find_mitigation_blocks(klines, swing_length, lookback)
    return _latest_within(blocks, len(klines), max_bars_ago)


def latest_bb_test(
    klines: list,
    swing_length: int = DEFAULT_SWING_LENGTH,
    lookback: int = DEFAULT_LOOKBACK,
    max_bars_ago: int = DEFAULT_MAX_BARS_AGO,
) -> BlockPattern | None:
    """Самый свежий BB-test в окне max_bars_ago от current bar."""
    if not klines:
        return None
    blocks = find_breaker_blocks(klines, swing_length, lookback)
    return _latest_within(blocks, len(klines), max_bars_ago)
