"""Тесты block_patterns.py — Mitigation + Breaker Block ICT."""

from block_patterns import (
    BlockPattern,
    find_breaker_blocks,
    find_mitigation_blocks,
    latest_bb_test,
    latest_mb_test,
)


def _b(o, h, lo, c, v=100.0):
    return {"o": o, "h": h, "l": lo, "c": c, "v": v}


# ─── Базовые ───────────────────────────────────────────────────────────────


def test_empty_returns_empty():
    assert find_mitigation_blocks([]) == []
    assert find_breaker_blocks([]) == []
    assert latest_mb_test([]) is None
    assert latest_bb_test([]) is None


def test_too_few_klines():
    klines = [_b(1, 2, 0, 1) for _ in range(5)]
    assert find_mitigation_blocks(klines, swing_length=5) == []
    assert find_breaker_blocks(klines, swing_length=5) == []


# ─── Bearish MB (swing low violated, swing high intact) ───────────────────


def _bear_mb_series():
    """
    Создаём:
     1. Swing high H ~ idx=5 (peak)
     2. Swing low L ~ idx=12 (valley)
     3. Bar после L: low < L (violation)
     4. Все бары остаются ниже H (high НЕ нарушен)
     5. Финальный bar: high ≥ L (тест обратно)
    """
    klines = []
    # подход к H
    for h, lo, c in [(8, 7, 7.5), (9, 8, 8.5), (10, 9, 9.5),
                     (11, 10, 10.5), (12, 11, 11.5)]:
        klines.append(_b(c, h, lo, c))   # i=0..4
    klines.append(_b(11.5, 13, 11, 12.5))   # i=5: swing high = 13
    for h, lo, c in [(12, 11, 11.5), (11, 10, 10.5), (10, 9, 9.5),
                     (9, 8, 8.5), (8, 7, 7.5), (7, 6, 6.5)]:
        klines.append(_b(c, h, lo, c))   # i=6..11
    klines.append(_b(6.5, 7, 5, 6))   # i=12: swing low = 5
    # подтверждение свинга (≥ length баров после)
    for _ in range(6):
        klines.append(_b(6, 7, 6, 6))   # i=13..18
    # violation: low < 5
    klines.append(_b(6, 6, 4.5, 5))   # i=19, low=4.5 < 5
    # бары между violation и тестом — все ниже H=13
    for _ in range(3):
        klines.append(_b(5, 6, 5, 5.5))   # i=20..22
    # test: high ≥ 5
    klines.append(_b(5.5, 6, 5, 5.5))   # i=23
    return klines


def test_bear_mb_detected():
    klines = _bear_mb_series()
    blocks = find_mitigation_blocks(klines, swing_length=3)
    bear_mbs = [b for b in blocks if b.direction == "bear"]
    assert bear_mbs, "ожидался bearish MB"
    mb = bear_mbs[-1]
    assert mb.kind == "MB"
    assert mb.swing_kind == "L"
    assert mb.level == 5
    assert mb.violated_at > mb.swing_at
    assert mb.test_at > mb.violated_at


def test_latest_mb_test_returns_recent_bear_mb():
    klines = _bear_mb_series()
    mb = latest_mb_test(klines, swing_length=3, max_bars_ago=15)
    assert mb is not None
    assert mb.direction == "bear"


# ─── Bullish MB (swing high violated, low intact) ──────────────────────────


def _bull_mb_series():
    """Зеркально bear: swing low L → swing high H → H пробит → возврат к H."""
    klines = []
    for h, lo, c in [(13, 12, 12.5), (12, 11, 11.5), (11, 10, 10.5),
                     (10, 9, 9.5), (9, 8, 8.5)]:
        klines.append(_b(c, h, lo, c))   # i=0..4
    klines.append(_b(8.5, 9, 7, 8))   # i=5: swing low = 7
    for h, lo, c in [(9, 8, 8.5), (10, 9, 9.5), (11, 10, 10.5),
                     (12, 11, 11.5), (13, 12, 12.5), (14, 13, 13.5)]:
        klines.append(_b(c, h, lo, c))   # i=6..11
    klines.append(_b(13.5, 15, 13, 14.5))   # i=12: swing high = 15
    for _ in range(6):
        klines.append(_b(14, 14, 13, 13.5))   # i=13..18 - всё ниже 15
    # violation: high > 15
    klines.append(_b(14, 16, 14, 15.5))   # i=19, high=16 > 15
    # bars остаются выше swing low 7
    for _ in range(3):
        klines.append(_b(15, 16, 15, 15.5))   # i=20..22
    # test: low ≤ 15
    klines.append(_b(15.5, 16, 15, 15.5))   # i=23
    return klines


def test_bull_mb_detected():
    klines = _bull_mb_series()
    blocks = find_mitigation_blocks(klines, swing_length=3)
    bull_mbs = [b for b in blocks if b.direction == "bull"]
    assert bull_mbs
    mb = bull_mbs[-1]
    assert mb.kind == "MB"
    assert mb.swing_kind == "H"
    assert mb.level == 15


# ─── Bearish BB (swing high violated + opp low ALSO violated → reversal) ──


def _bear_bb_series():
    """
    BB ≠ MB: ОБА extreme'а пробиты. Здесь:
     1. swing low L_old ≈ 7 (i=5)
     2. swing high H ≈ 13 (i=12)
     3. H пробит вверх → видим reversal
     4. далее цена пробивает L_old вниз → reversal complete
     5. возврат к H → test
    """
    klines = []
    # подъём к L_old
    for h, lo, c in [(8, 7.5, 7.7), (8.5, 7.8, 8), (8.8, 8, 8.3),
                     (9, 8.3, 8.7), (9.2, 8.5, 9)]:
        klines.append(_b(c, h, lo, c))   # i=0..4
    klines.append(_b(9, 9, 7, 8))   # i=5: swing low = 7
    # затем подъём к H
    for h, lo, c in [(9, 8, 8.5), (10, 9, 9.5), (11, 10, 10.5),
                     (12, 11, 11.5), (12.5, 11.5, 12), (12.8, 12, 12.5)]:
        klines.append(_b(c, h, lo, c))   # i=6..11
    klines.append(_b(12.5, 13, 11, 12.5))   # i=12: swing high = 13
    for _ in range(6):
        klines.append(_b(12, 12.5, 11.5, 12))   # i=13..18 - подтверждение
    # violation H вверх
    klines.append(_b(12, 14, 12, 13.5))   # i=19, high=14 > 13
    klines.append(_b(13.5, 14, 12.5, 13))   # i=20
    klines.append(_b(13, 13, 11, 11.5))   # i=21
    # opp violation: low < 7
    klines.append(_b(11.5, 12, 6.5, 7))   # i=22, low=6.5 < 7
    # стабилизация после reversal
    klines.append(_b(7, 8, 6.5, 7.5))   # i=23
    klines.append(_b(7.5, 9, 7.5, 8.5))   # i=24
    klines.append(_b(8.5, 10, 8.5, 9.5))   # i=25
    klines.append(_b(9.5, 11, 9.5, 10.5))   # i=26
    klines.append(_b(10.5, 12, 10.5, 11.5))   # i=27
    # test: high ≥ 13
    klines.append(_b(11.5, 13.2, 11.5, 13))   # i=28, high=13.2 ≥ 13
    return klines


def test_bear_bb_detected():
    klines = _bear_bb_series()
    blocks = find_breaker_blocks(klines, swing_length=3)
    bear_bbs = [b for b in blocks if b.direction == "bear"]
    assert bear_bbs, "ожидался bearish BB"
    bb = bear_bbs[-1]
    assert bb.kind == "BB"
    assert bb.swing_kind == "H"
    assert bb.level == 13


def test_latest_bb_test_returns_recent_bear_bb():
    klines = _bear_bb_series()
    bb = latest_bb_test(klines, swing_length=3, max_bars_ago=15)
    assert bb is not None
    assert bb.direction == "bear"


# ─── Negative: MB criteria not met ────────────────────────────────────────


def test_no_mb_when_no_violation():
    """Swing low не нарушен — MB не формируется."""
    klines = []
    for h, lo, c in [(11, 10, 10.5), (10, 9, 9.5), (9, 8, 8.5),
                     (8, 7, 7.5), (7, 6, 6.5)]:
        klines.append(_b(c, h, lo, c))
    klines.append(_b(6.5, 7, 5, 6))   # swing low = 5
    for _ in range(15):
        klines.append(_b(6, 7, 5.5, 6.5))   # никогда не <5
    blocks = find_mitigation_blocks(klines, swing_length=3)
    assert [b for b in blocks if b.swing_kind == "L"] == []


def test_no_mb_when_no_return_test():
    """L нарушен, но цена ВООБЩЕ не возвращается обратно — MB неактивен."""
    klines = []
    for h, lo, c in [(8, 7, 7.5), (9, 8, 8.5), (10, 9, 9.5),
                     (11, 10, 10.5), (12, 11, 11.5)]:
        klines.append(_b(c, h, lo, c))
    klines.append(_b(11.5, 13, 11, 12.5))   # i=5: swing high = 13
    for h, lo, c in [(12, 11, 11.5), (11, 10, 10.5), (10, 9, 9.5),
                     (9, 8, 8.5), (8, 7, 7.5), (7, 6, 6.5)]:
        klines.append(_b(c, h, lo, c))
    klines.append(_b(6.5, 7, 5, 6))   # i=12: swing low = 5
    for _ in range(6):
        klines.append(_b(6, 7, 6, 6))   # confirm
    # Violation + ВСЕ последующие бары ОСТАЮТСЯ ниже L=5
    klines.append(_b(6, 6, 4.5, 5))   # violation
    for _ in range(10):
        klines.append(_b(4, 4.5, 3.5, 4))   # high=4.5 < 5 → нет теста
    blocks = find_mitigation_blocks(klines, swing_length=3)
    bears = [b for b in blocks if b.direction == "bear" and b.swing_kind == "L"]
    assert not bears


# ─── BB criteria not met → falls back, может оказаться MB ─────────────────


def test_bb_requires_opp_violation():
    """Если opposite extreme НЕ нарушен — это MB, не BB."""
    klines = _bear_mb_series()   # swing high intact
    blocks_bb = find_breaker_blocks(klines, swing_length=3)
    # Никаких bear BB в bear-MB серии (high не нарушен)
    bears = [b for b in blocks_bb if b.direction == "bear"]
    assert not bears


# ─── max_bars_ago filter ─────────────────────────────────────────────────


def test_latest_mb_test_filters_by_age():
    klines = _bear_mb_series()
    # Добавим тонну «свежих» баров без событий
    for _ in range(30):
        klines.append(_b(5.5, 6, 5, 5.5))
    # max_bars_ago=5 — слишком строгий
    assert latest_mb_test(klines, swing_length=3, max_bars_ago=5) is None
    # Достаточно большой охват
    assert latest_mb_test(klines, swing_length=3, max_bars_ago=50) is not None


# ─── Dataclass ─────────────────────────────────────────────────────────────


def test_block_pattern_dataclass_fields():
    klines = _bear_mb_series()
    blocks = find_mitigation_blocks(klines, swing_length=3)
    assert blocks
    bp = blocks[0]
    assert isinstance(bp, BlockPattern)
    for field in ("kind", "direction", "level", "swing_at", "swing_kind",
                  "violated_at", "test_at"):
        assert hasattr(bp, field)
