"""Тесты structure.py — детект BOS/CHoCH на 5m+15m (Этап 10, фаза 2)."""

from structure import (
    StructureEvent,
    confirmed_break_5m_15m,
    detect_structure,
    find_swing_points,
    latest_break,
)


def _b(o, h, low, c, v=100.0):
    return {"o": o, "h": h, "l": low, "c": c, "v": v}


# ─── find_swing_points ─────────────────────────────────────────────────────


def test_swing_points_empty_when_too_few_klines():
    klines = [_b(1, 2, 0, 1) for _ in range(5)]
    assert find_swing_points(klines, length=5) == []


def test_swing_high_at_peak():
    # peak в индексе 5, окруженный low-барами с обеих сторон
    klines = [
        _b(1, 2, 0, 1), _b(1, 2, 0, 1), _b(1, 2, 0, 1),
        _b(1, 3, 0, 1), _b(1, 4, 0, 1),
        _b(1, 10, 0, 1),                                  # ← peak (i=5)
        _b(1, 4, 0, 1), _b(1, 3, 0, 1),
        _b(1, 2, 0, 1), _b(1, 2, 0, 1), _b(1, 2, 0, 1),
    ]
    swings = find_swing_points(klines, length=3)
    highs = [s for s in swings if s.kind == "H"]
    assert any(s.index == 5 and s.price == 10 for s in highs)


def test_swing_low_at_valley():
    klines = [
        _b(10, 11, 9, 10), _b(10, 11, 9, 10), _b(10, 11, 9, 10),
        _b(10, 11, 8, 9),
        _b(10, 11, 1, 5),                                 # ← valley (i=4)
        _b(10, 11, 8, 9),
        _b(10, 11, 9, 10), _b(10, 11, 9, 10), _b(10, 11, 9, 10),
    ]
    swings = find_swing_points(klines, length=3)
    lows = [s for s in swings if s.kind == "L"]
    assert any(s.index == 4 and s.price == 1 for s in lows)


def test_swing_points_sorted_by_index():
    klines = [_b(1, 2 + (i % 3), 0, 1) for i in range(20)]
    swings = find_swing_points(klines, length=2)
    indices = [s.index for s in swings]
    assert indices == sorted(indices)


# ─── detect_structure ──────────────────────────────────────────────────────


def _ramp(values):
    """Делает kline series из списка цен — каждый бар o=h=l=c=price."""
    return [_b(v, v, v, v) for v in values]


def test_detect_structure_empty_klines():
    state = detect_structure([])
    assert state.events == []
    assert state.trend == "neutral"


def test_detect_first_break_up_is_choch_when_neutral():
    """
    Первый слом в любую сторону из neutral-тренда — CHoCH (с neutral нет
    предыдущего тренда, но в нашем определении neutral != bull, поэтому
    первый break high даёт CHOCH).
    """
    prices = [
        # Сначала строим swing high около индекса 5
        10, 10, 10, 11, 12, 15, 12, 11, 10, 10, 10,
        # Затем пробиваем 15
        16,
    ]
    state = detect_structure(_ramp(prices), swing_length=3)
    assert len(state.events) >= 1
    first = state.events[0]
    assert first.direction == "bull"
    assert first.kind == "CHOCH"   # из neutral
    assert state.trend == "bull"


def test_detect_consecutive_up_break_is_bos():
    """Первый up break после уже-bullish тренда → BOS, не CHoCH."""
    prices = [
        # First swing high
        10, 10, 10, 11, 12, 15, 12, 11, 10, 10, 10,
        # First break (CHoCH bull) at i=11
        16, 16, 16,
        # Second swing high после break — около индекса 16
        16, 17, 20, 17, 16, 16, 16,
        # Second break — BOS bull
        21,
    ]
    state = detect_structure(_ramp(prices), swing_length=3)
    assert len(state.events) >= 2
    bull_events = [e for e in state.events if e.direction == "bull"]
    assert any(e.kind == "BOS" for e in bull_events[1:])  # вторая bull = BOS


def test_detect_choch_on_trend_reversal():
    """
    После bull-тренда первый break VNIZ должен быть CHoCH bear (смена характера).
    """
    prices = [
        # Bull setup: swing high near 5, break at 11
        10, 10, 10, 11, 12, 15, 12, 11, 10, 10, 10, 16,
        # После break trend = bull. Теперь swing LOW около индекса 17.
        16, 15, 14, 12, 13, 8, 13, 14, 14, 14,
        # Break вниз последнего swing low (8)
        7,
    ]
    state = detect_structure(_ramp(prices), swing_length=3)
    bear = [e for e in state.events if e.direction == "bear"]
    assert bear, "должен быть хотя бы один bear-слом"
    assert bear[0].kind == "CHOCH"


def test_structure_event_records_level_and_close():
    prices = [10, 10, 10, 11, 12, 15, 12, 11, 10, 10, 10, 16]
    state = detect_structure(_ramp(prices), swing_length=3)
    ev = state.events[0]
    assert isinstance(ev, StructureEvent)
    assert ev.level == 15
    assert ev.close == 16
    assert ev.swing_at == 5


# ─── latest_break ──────────────────────────────────────────────────────────


def test_latest_break_returns_most_recent_event():
    prices = [
        10, 10, 10, 11, 12, 15, 12, 11, 10, 10, 10, 16,
        16, 15, 14, 12, 13, 8, 13, 14, 14, 14, 7,
    ]
    ev = latest_break(_ramp(prices), swing_length=3)
    assert ev is not None
    assert ev.direction == "bear"


def test_latest_break_too_old_returns_none():
    """Старое событие не возвращается, если max_bars_ago мал."""
    prices = (
        [10, 10, 10, 11, 12, 15, 12, 11, 10, 10, 10, 16]
        # Длинный «без событий» хвост
        + [16] * 30
    )
    ev = latest_break(_ramp(prices), swing_length=3, max_bars_ago=5)
    assert ev is None


def test_latest_break_none_when_no_events():
    ev = latest_break([_b(1, 1, 1, 1) for _ in range(20)], swing_length=3)
    assert ev is None


# ─── confirmed_break_5m_15m ────────────────────────────────────────────────


def _make_bull_break_series(swing_length=3, padding=0):
    """Helper: серия цен с одним свежим bull-сломом в конце."""
    prices = [10, 10, 10, 11, 12, 15, 12, 11, 10, 10, 10, 16]
    if padding:
        prices += [16] * padding
    return _ramp(prices)


def _make_bear_break_series(swing_length=3, padding=0):
    """Helper: серия с одним свежим bear-сломом."""
    prices = [20, 20, 20, 19, 18, 15, 18, 19, 20, 20, 20, 14]
    if padding:
        prices += [14] * padding
    return _ramp(prices)


def test_confirmed_break_both_tfs_same_direction():
    k5 = _make_bull_break_series()
    k15 = _make_bull_break_series()
    out = confirmed_break_5m_15m(k5, k15, swing_length=3)
    assert out is not None
    assert out["direction"] == "bull"
    assert "events" in out
    assert "5m" in out["events"] and "15m" in out["events"]


def test_confirmed_break_disagreement_returns_none():
    k5 = _make_bull_break_series()
    k15 = _make_bear_break_series()
    assert confirmed_break_5m_15m(k5, k15, swing_length=3) is None


def test_confirmed_break_missing_one_tf_returns_none():
    k5 = _make_bull_break_series()
    k15 = _ramp([10] * 20)  # без событий
    assert confirmed_break_5m_15m(k5, k15, swing_length=3) is None


def test_confirmed_break_too_old_returns_none():
    # Слом случился давно, max_bars_ago мал
    k5 = _make_bull_break_series(padding=50)
    k15 = _make_bull_break_series(padding=50)
    assert confirmed_break_5m_15m(
        k5, k15, swing_length=3,
        max_bars_ago_5m=5, max_bars_ago_15m=5,
    ) is None


def test_confirmed_break_uses_bear_direction():
    k5 = _make_bear_break_series()
    k15 = _make_bear_break_series()
    out = confirmed_break_5m_15m(k5, k15, swing_length=3)
    assert out is not None
    assert out["direction"] == "bear"
