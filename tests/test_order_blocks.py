"""Тесты order_blocks.py — ICT Order Block детектор (Этап 12, фаза 1)."""

from order_blocks import (
    OrderBlock,
    find_order_blocks,
    latest_ob_test,
)


def _b(o, h, low, c, v=100.0):
    return {"o": o, "h": h, "l": low, "c": c, "v": v}


# ─── Базовые случаи ────────────────────────────────────────────────────────


def test_empty_returns_empty():
    assert find_order_blocks([]) == []
    assert latest_ob_test([]) is None


def test_too_few_klines():
    klines = [_b(1, 2, 0, 1) for _ in range(10)]
    assert find_order_blocks(klines) == []


def test_no_significant_bodies_returns_empty():
    """Если все свечи доджи (body < min_body_atr) — OB нет."""
    klines = [_b(10, 10.05, 9.95, 10) for _ in range(50)]
    assert find_order_blocks(klines, min_body_atr=0.5) == []


# ─── Bullish OB ────────────────────────────────────────────────────────────


def _bull_ob_series(mitigated: bool = False):
    """
    Чистая серия (без spurious OB-кандидатов):
     • 20 разогревочных баров с малой амплитудой (ATR ~1).
     • i=20: жирная downclose-свеча — кандидат на Bullish OB.
     • i=21: одно validation-бар, чуть пробивающий OB.high.
     • i=22-26: 5 «doji»-баров с минимальным body (не дают bear OB).
     • Опционально: возврат цены в body OB (mitigated).
    """
    klines = []
    for _ in range(20):
        klines.append(_b(100, 100.5, 99.5, 100))
    klines.append(_b(100, 100.5, 96, 97))        # i=20: OB candidate
    klines.append(_b(97, 100.8, 97, 100.6))      # i=21: validation
    for _ in range(5):
        klines.append(_b(100.6, 100.8, 100.4, 100.6))  # i=22..26 doji
    if mitigated:
        klines.append(_b(100.6, 100.6, 97.5, 98))   # i=27 mitigate
        klines.append(_b(98, 98.5, 97, 97.5))
    return klines


def test_bullish_ob_detected_and_validated():
    klines = _bull_ob_series(mitigated=False)
    obs = find_order_blocks(klines, lookback=40)
    bulls = [ob for ob in obs if ob.direction == "bull"]
    assert bulls, "ожидался хотя бы один Bullish OB"
    ob = next(ob for ob in bulls if ob.candle_idx == 20)
    assert ob.validated is True
    assert ob.mitigated is False
    assert ob.body_high == 100  # max(open=100, close=97)
    assert ob.body_low  == 97   # min(open=100, close=97)
    assert ob.high == 100.5
    assert ob.low  == 96


def test_bullish_ob_mitigated_flag():
    klines = _bull_ob_series(mitigated=True)
    obs = find_order_blocks(klines, lookback=40)
    ob = next(ob for ob in obs if ob.candle_idx == 20)
    assert ob.mitigated is True


def test_latest_ob_test_returns_bull_when_testing():
    """Цена возвращается к Bullish OB — latest_ob_test возвращает его."""
    klines = _bull_ob_series(mitigated=False)
    # Тестирующий бар: low=98 ≤ body_high=100, close=99 > OB.low=96
    klines.append(_b(100.6, 100.7, 98, 99))
    ob = latest_ob_test(klines, lookback=50)
    assert ob is not None
    assert ob.direction == "bull"
    assert ob.candle_idx == 20


def test_latest_ob_test_skips_mitigated():
    """Mitigated OB не возвращается даже если касается."""
    klines = _bull_ob_series(mitigated=True)
    # ещё один тест после mitigation
    klines.append(_b(98.5, 99, 97, 98))
    assert latest_ob_test(klines, lookback=60) is None


# ─── Bearish OB ────────────────────────────────────────────────────────────


def _bear_ob_series(mitigated: bool = False):
    """Зеркально Bull: жирная upclose + минимальный dump, чистые данные."""
    klines = []
    for _ in range(20):
        klines.append(_b(100, 100.5, 99.5, 100))
    klines.append(_b(100, 104, 99.5, 103))       # i=20: bear OB candidate
    klines.append(_b(103, 103, 99.2, 99.4))      # i=21: validation, l<99.5
    for _ in range(5):
        klines.append(_b(99.4, 99.6, 99.2, 99.4))  # i=22..26 doji
    if mitigated:
        klines.append(_b(99.4, 102, 99.4, 101))    # i=27 mitigate (touch body)
        klines.append(_b(101, 101.5, 100, 100.5))
    return klines


def test_bearish_ob_detected_and_validated():
    klines = _bear_ob_series()
    obs = find_order_blocks(klines, lookback=40)
    bears = [ob for ob in obs if ob.direction == "bear"]
    assert bears
    ob = next(ob for ob in bears if ob.candle_idx == 20)
    assert ob.validated is True
    assert ob.mitigated is False
    assert ob.body_low  == 100   # min(open=100, close=103)
    assert ob.body_high == 103
    assert ob.high == 104
    assert ob.low  == 99.5


def test_latest_ob_test_returns_bear_when_testing():
    """Цена откатывает вверх к Bearish OB — entry trigger."""
    klines = _bear_ob_series(mitigated=False)
    # Тестирующий бар: high=101 ≥ body_low=100, close=99.8 < OB.high=104
    klines.append(_b(99.4, 101, 99.3, 99.8))
    ob = latest_ob_test(klines, lookback=50)
    assert ob is not None
    assert ob.direction == "bear"
    assert ob.candle_idx == 20


# ─── Validation требуется ──────────────────────────────────────────────────


def test_unvalidated_ob_not_returned():
    """Жирная downclose, но НИКАКОЙ бар после неё не пробивает её high."""
    klines = []
    for _ in range(20):
        klines.append(_b(100, 101, 99, 100))
    klines.append(_b(99, 100, 95, 96))      # downclose, high=100
    # Все последующие бары остаются НИЖЕ 100
    for _ in range(15):
        klines.append(_b(96, 98, 95, 96))
    obs = find_order_blocks(klines, lookback=40)
    # Bullish-OB на i=20 не должен попасть (нет valid)
    assert not [ob for ob in obs
                if ob.direction == "bull" and ob.candle_idx == 20]


def test_validation_window_respects_limit():
    """Validation должна произойти в пределах validation_window."""
    klines = []
    for _ in range(20):
        klines.append(_b(100, 101, 99, 100))
    klines.append(_b(99, 100, 95, 96))   # i=20 downclose
    # 12 баров топчутся ниже 100 (validation_window=10 — не проходит)
    for _ in range(12):
        klines.append(_b(96, 98, 95, 96))
    # Только потом пробой
    klines.append(_b(96, 110, 96, 109))

    obs = find_order_blocks(klines, lookback=40, validation_window=10)
    # OB не должен быть найден — пробой пришёл на 13 свече, > window=10
    assert not [ob for ob in obs
                if ob.direction == "bull" and ob.candle_idx == 20]


# ─── Свойства результата ───────────────────────────────────────────────────


def test_order_block_dataclass_fields():
    klines = _bull_ob_series()
    obs = find_order_blocks(klines, lookback=40)
    assert obs
    ob = obs[0]
    assert isinstance(ob, OrderBlock)
    # Все ключевые поля заполнены
    for field in ("direction", "candle_idx", "high", "low", "open", "close",
                  "body_high", "body_low", "body_atr", "validated", "mitigated"):
        assert hasattr(ob, field)


def test_atr_filter_removes_small_bodies():
    """Высокий min_body_atr → даже валидные OB отсеиваются."""
    klines = _bull_ob_series()
    obs_loose  = find_order_blocks(klines, lookback=40, min_body_atr=0.1)
    obs_strict = find_order_blocks(klines, lookback=40, min_body_atr=5.0)
    assert len(obs_strict) <= len(obs_loose)
    assert obs_strict == []  # body=3, ATR проб. ~1.5 → body/ATR ~2, < 5


# ─── latest_ob_test без событий ────────────────────────────────────────────


def test_latest_ob_test_none_when_no_test():
    """OB есть, но текущий бар НЕ в зоне теста — None."""
    klines = _bull_ob_series()
    # бар сильно ВЫШЕ OB body (не тестирует)
    klines.append(_b(105.5, 106, 105, 105.5))
    assert latest_ob_test(klines, lookback=50) is None
