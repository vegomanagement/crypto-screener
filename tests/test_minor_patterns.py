"""Тесты minor_patterns.py — Inside Candle + Rejection Block."""

from minor_patterns import (
    InsideBreakout,
    RejectionBlock,
    find_inside_breakouts,
    find_rejection_blocks,
    latest_inside_breakout,
    latest_rejection_test,
)


def _b(o, h, lo, c, v=100.0):
    return {"o": o, "h": h, "l": lo, "c": c, "v": v}


# ─── Inside Candle Breakout ───────────────────────────────────────────────


def test_inside_empty_returns_empty():
    assert find_inside_breakouts([]) == []
    assert latest_inside_breakout([]) is None


def test_inside_bull_breakout_detected():
    """Inside candle с последующим bull breakout."""
    klines = [_b(100, 110, 90, 105) for _ in range(20)]
    # i=20: inside (high < 110, low > 90)
    klines.append(_b(100, 105, 95, 102))
    # i=21: breakout вверх (close > inside.high = 105)
    klines.append(_b(102, 108, 100, 107))
    events = find_inside_breakouts(klines)
    assert events
    last = events[-1]
    assert isinstance(last, InsideBreakout)
    assert last.direction == "bull"
    assert last.inside_idx == 20
    assert last.breakout_idx == 21


def test_inside_bear_breakout_detected():
    klines = [_b(100, 110, 90, 105) for _ in range(20)]
    klines.append(_b(100, 105, 95, 100))   # inside
    klines.append(_b(100, 102, 92, 93))    # bear breakout (close < 95)
    events = find_inside_breakouts(klines)
    assert events[-1].direction == "bear"


def test_inside_no_breakout_returns_none():
    klines = [_b(100, 110, 90, 105) for _ in range(20)]
    klines.append(_b(100, 105, 95, 100))   # inside
    # Следующие бары топчутся внутри inside range
    klines.append(_b(100, 104, 97, 100))
    klines.append(_b(100, 103, 96, 99))
    events = find_inside_breakouts(klines)
    # На последней inside-свече не было breakout'а → нет события для этого inside
    assert not [e for e in events if e.inside_idx == 20]


def test_inside_breakout_within_2_bars():
    """Breakout может произойти на 1-й или 2-й свече ПОСЛЕ inside."""
    klines = [_b(100, 110, 90, 105) for _ in range(20)]
    klines.append(_b(100, 105, 95, 100))   # i=20 inside
    klines.append(_b(100, 105, 96, 100))   # i=21 нет breakout
    klines.append(_b(100, 108, 96, 107))   # i=22 breakout вверх
    events = find_inside_breakouts(klines)
    assert events
    assert events[-1].breakout_idx == 22


def test_inside_breakout_beyond_2_bars_skipped():
    klines = [_b(100, 110, 90, 105) for _ in range(20)]
    klines.append(_b(100, 105, 95, 100))   # i=20
    klines.append(_b(100, 105, 96, 100))   # i=21
    klines.append(_b(100, 104, 97, 100))   # i=22
    klines.append(_b(100, 108, 96, 107))   # i=23 — slishком далеко
    events = find_inside_breakouts(klines)
    assert not [e for e in events if e.inside_idx == 20]


def test_latest_inside_breakout_age_filter():
    klines = [_b(100, 110, 90, 105) for _ in range(20)]
    klines.append(_b(100, 105, 95, 100))
    klines.append(_b(100, 108, 100, 107))   # breakout @ 21
    # Добавим тонну свежих
    for _ in range(20):
        klines.append(_b(107, 109, 105, 107))
    assert latest_inside_breakout(klines, max_bars_ago=3) is None
    assert latest_inside_breakout(klines, max_bars_ago=30) is not None


# ─── Rejection Block ─────────────────────────────────────────────────────


def test_rejection_empty_returns_empty():
    assert find_rejection_blocks([]) == []
    assert latest_rejection_test([]) is None


def test_bearish_rejection_block_detected():
    """
    Свеча с длинным верхним wick → bearish RB.
    Позже какой-то бар тестирует body_high.
    """
    # Warmup, ATR ≈ 1
    klines = [_b(100, 100.5, 99.5, 100) for _ in range(20)]
    # RB-свеча: body 100..101 (1 unit), upper wick до 110 (большой)
    klines.append(_b(100, 110, 99.5, 101))   # i=20
    # Несколько баров вниз
    for _ in range(3):
        klines.append(_b(99, 100, 98, 98.5))
    # Test: high возвращается к body_high=101
    klines.append(_b(98.5, 101.5, 98, 100.5))   # i=24
    events = find_rejection_blocks(klines)
    bears = [r for r in events if r.direction == "bear"]
    assert bears
    last = bears[-1]
    assert isinstance(last, RejectionBlock)
    assert last.candle_idx == 20
    assert last.body_high == 101
    assert last.wick_high == 110


def test_bullish_rejection_block_detected():
    """Свеча с длинным нижним wick → bullish RB."""
    klines = [_b(100, 100.5, 99.5, 100) for _ in range(20)]
    # RB: body 99..100, lower wick до 90
    klines.append(_b(100, 100.5, 90, 99))   # i=20
    for _ in range(3):
        klines.append(_b(99.5, 100, 99, 99.5))
    klines.append(_b(99.5, 100, 98.5, 99))   # тест body_low=99
    events = find_rejection_blocks(klines)
    bulls = [r for r in events if r.direction == "bull"]
    assert bulls
    last = bulls[-1]
    assert last.body_low == 99
    assert last.wick_low == 90


def test_rejection_filters_small_bodies():
    """Doji-свечи с длинным wick'ом, но мелким body не должны попадать."""
    klines = [_b(100, 100.5, 99.5, 100) for _ in range(20)]
    # Doji: body 100..100.05 (микроскопический), но wick до 110
    klines.append(_b(100, 110, 99.5, 100.05))
    for _ in range(5):
        klines.append(_b(100, 110, 100, 105))
    events = find_rejection_blocks(klines, min_body_atr=0.5)
    # body=0.05, ATR ~1 → body/ATR=0.05 < 0.5 → отсев
    assert not [r for r in events if r.candle_idx == 20]


def test_rejection_requires_dominant_wick():
    """Свеча с обычным распределением wick'ов — НЕ RB."""
    klines = [_b(100, 100.5, 99.5, 100) for _ in range(20)]
    # body=2, upper_wick=1, lower_wick=1 (равные) — не RB
    klines.append(_b(99, 102, 98, 101))   # ratio = 1.0
    for _ in range(5):
        klines.append(_b(101, 103, 100, 102))
    events = find_rejection_blocks(klines, wick_ratio=2.0)
    assert not [r for r in events if r.candle_idx == 20]


def test_latest_rejection_test_age_filter():
    klines = [_b(100, 100.5, 99.5, 100) for _ in range(20)]
    klines.append(_b(100, 110, 99.5, 101))
    for _ in range(3):
        klines.append(_b(99, 100, 98, 98.5))
    klines.append(_b(98.5, 101.5, 98, 100.5))   # test
    # Добавим 20 свежих
    for _ in range(20):
        klines.append(_b(99, 100, 98, 99))
    assert latest_rejection_test(klines, max_bars_ago=3) is None
    assert latest_rejection_test(klines, max_bars_ago=50) is not None


def test_rejection_no_retest_means_no_event():
    klines = [_b(100, 100.5, 99.5, 100) for _ in range(20)]
    klines.append(_b(100, 110, 99.5, 101))   # RB candle
    # Цена уходит вниз и не возвращается к body_high=101
    for _ in range(10):
        klines.append(_b(98, 99, 97, 97.5))
    events = find_rejection_blocks(klines)
    assert not [r for r in events if r.candle_idx == 20 and r.direction == "bear"]


# ─── Dataclass fields ─────────────────────────────────────────────────────


def test_inside_breakout_dataclass_fields():
    klines = [_b(100, 110, 90, 105) for _ in range(20)]
    klines.append(_b(100, 105, 95, 100))
    klines.append(_b(100, 108, 100, 107))
    ev = find_inside_breakouts(klines)[-1]
    for f in ("direction", "inside_idx", "breakout_idx", "inside_high",
              "inside_low", "breakout_close"):
        assert hasattr(ev, f)


def test_rejection_block_dataclass_fields():
    klines = [_b(100, 100.5, 99.5, 100) for _ in range(20)]
    klines.append(_b(100, 110, 99.5, 101))
    for _ in range(3):
        klines.append(_b(99, 100, 98, 98.5))
    klines.append(_b(98.5, 101.5, 98, 100.5))
    ev = find_rejection_blocks(klines)[-1]
    for f in ("direction", "candle_idx", "body_high", "body_low",
              "wick_high", "wick_low", "test_idx"):
        assert hasattr(ev, f)
