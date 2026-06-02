"""Тесты ote.py — Optimal Trade Entry (Этап 12 фаза 3)."""

from ote import OTEZone, compute_ote_zone


def _b(o, h, lo, c, v=100.0):
    return {"o": o, "h": h, "l": lo, "c": c, "v": v}


def _bull_impulse_series():
    """
    Серия с bull-impulse:
      • swing low около i=5 (low=90)
      • swing high около i=12 (high=110)
      • bull BOS на i ~18 (close > swing high)
    """
    klines = []
    # подход вниз к swing low
    for h, lo, c in [(95, 92, 93), (94, 91, 92.5), (93, 90.5, 91.5),
                     (92, 90, 91), (91.5, 89.5, 90.5)]:
        klines.append(_b(c, h, lo, c))   # i=0..4
    klines.append(_b(90.5, 91, 90, 90.5))   # i=5: swing low = 90
    # подъём
    for h, lo, c in [(91, 90, 90.5), (93, 91, 92), (96, 93, 95),
                     (100, 95, 99), (105, 100, 103), (108, 103, 106)]:
        klines.append(_b(c, h, lo, c))   # i=6..11
    klines.append(_b(106, 110, 105, 108))   # i=12: swing high = 110
    # подтверждение swing (≥ length баров)
    for _ in range(6):
        klines.append(_b(107, 108, 106, 107))   # i=13..18
    # BOS bull
    klines.append(_b(107, 112, 107, 111.5))   # i=19, close > 110 = swing high
    # ещё пара баров после BOS для теста "свежести"
    for _ in range(3):
        klines.append(_b(111, 112, 110, 111))
    return klines


def _bear_impulse_series():
    """Зеркально bull: swing high → spring low → bear BOS."""
    klines = []
    for h, lo, c in [(105, 102, 103), (106, 103, 104.5), (107, 104, 105.5),
                     (108, 105, 106), (109, 106, 107)]:
        klines.append(_b(c, h, lo, c))
    klines.append(_b(108, 110, 107, 109))   # i=5: swing high = 110
    for h, lo, c in [(108, 107, 107.5), (107, 105, 106), (105, 102, 103),
                     (102, 98, 100), (98, 94, 96), (95, 91, 93)]:
        klines.append(_b(c, h, lo, c))
    klines.append(_b(93, 95, 90, 91))   # i=12: swing low = 90
    for _ in range(6):
        klines.append(_b(92, 93, 91, 92))
    # BOS bear: close < 90
    klines.append(_b(92, 92, 88, 88.5))
    for _ in range(3):
        klines.append(_b(89, 90, 88, 89))
    return klines


# ─── Базовые случаи ───────────────────────────────────────────────────────


def test_empty_returns_none():
    assert compute_ote_zone([], "long") is None


def test_invalid_direction_returns_none():
    klines = _bull_impulse_series()
    assert compute_ote_zone(klines, "neutral") is None
    assert compute_ote_zone(klines, "wrong") is None


def test_too_few_klines_returns_none():
    klines = [_b(1, 2, 0, 1) for _ in range(5)]
    assert compute_ote_zone(klines, "long") is None


# ─── Bull OTE ─────────────────────────────────────────────────────────────


def test_bull_ote_computed():
    klines = _bull_impulse_series()
    ote = compute_ote_zone(klines, "long", swing_length=3)
    assert ote is not None
    assert isinstance(ote, OTEZone)
    assert ote.direction == "long"
    # Импульс шёл вверх → entry_max > entry_min, и оба ниже impulse_end
    assert ote.entry_min < ote.entry_max
    assert ote.entry_max < ote.impulse_end
    # SL за impulse_start (ниже)
    assert ote.sl < ote.impulse_start


def test_bull_ote_fib_levels():
    klines = _bull_impulse_series()
    ote = compute_ote_zone(klines, "long", swing_length=3)
    assert ote is not None
    # fib_79 ниже fib_62 (для bull: чем глубже retracement, тем ниже цена)
    assert ote.fib_79 < ote.fib_62
    # Проверка формулы: fib_62 = impulse_end - 0.62 * delta
    delta = ote.impulse_end - ote.impulse_start
    expected_62 = ote.impulse_end - 0.62 * delta
    expected_79 = ote.impulse_end - 0.79 * delta
    assert abs(ote.fib_62 - expected_62) < 1e-6
    assert abs(ote.fib_79 - expected_79) < 1e-6


# ─── Bear OTE ─────────────────────────────────────────────────────────────


def test_bear_ote_computed():
    klines = _bear_impulse_series()
    ote = compute_ote_zone(klines, "short", swing_length=3)
    assert ote is not None
    assert ote.direction == "short"
    # Bear OTE: entry выше impulse_end (low)
    assert ote.entry_min > ote.impulse_end
    # SL за impulse_start (выше)
    assert ote.sl > ote.impulse_start


def test_bear_ote_fib_levels():
    klines = _bear_impulse_series()
    ote = compute_ote_zone(klines, "short", swing_length=3)
    assert ote is not None
    # fib_79 выше fib_62 (для bear: чем глубже retracement, тем выше цена)
    assert ote.fib_79 > ote.fib_62


# ─── Direction mismatch ───────────────────────────────────────────────────


def test_bull_impulse_short_direction_returns_none():
    """Bull impulse + short direction → None (нет sell setup)."""
    klines = _bull_impulse_series()
    assert compute_ote_zone(klines, "short", swing_length=3) is None


def test_bear_impulse_long_direction_returns_none():
    klines = _bear_impulse_series()
    assert compute_ote_zone(klines, "long", swing_length=3) is None


# ─── Свежесть импульса ───────────────────────────────────────────────────


def test_too_old_impulse_returns_none():
    """Импульс старше max_bars_since → None."""
    klines = _bull_impulse_series()
    # Добавим 40 свежих баров без новых событий
    for _ in range(40):
        klines.append(_b(110, 111, 109, 110))
    assert compute_ote_zone(klines, "long", swing_length=3,
                            max_bars_since=10) is None


def test_fresh_impulse_with_larger_window_works():
    """Тот же impulse с большим max_bars_since — должен пройти."""
    klines = _bull_impulse_series()
    for _ in range(40):
        klines.append(_b(110, 111, 109, 110))
    assert compute_ote_zone(klines, "long", swing_length=3,
                            max_bars_since=60) is not None


# ─── OTEZone dataclass ────────────────────────────────────────────────────


def test_otezone_has_all_fields():
    klines = _bull_impulse_series()
    ote = compute_ote_zone(klines, "long", swing_length=3)
    assert ote is not None
    for f in ("direction", "impulse_start_idx", "impulse_end_idx",
              "impulse_start", "impulse_end", "fib_62", "fib_79",
              "entry_min", "entry_max", "sl"):
        assert hasattr(ote, f)
