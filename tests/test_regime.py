"""Unit tests for regime.py — accumulation/distribution + positioning."""

from regime import (
    Regime,
    classify_positioning,
    classify_regime,
    dealing_range,
    range_state,
)


def _c(o, h, low, c, v=100.0):
    return {"o": o, "h": h, "l": low, "c": c, "v": v}


def _flat_range(center, half, n=100, last_close=None):
    """N баров в горизонтальном диапазоне [center-half, center+half]."""
    out = []
    for i in range(n):
        hi = center + half
        lo = center - half
        cl = center
        out.append(_c(center, hi, lo, cl, 100))
    if last_close is not None:
        out[-1] = _c(center, center + half, center - half, last_close, 100)
    return out


# ─── dealing_range ────────────────────────────────────────────────────────


def test_dealing_range_position_low():
    candles = _flat_range(100, 10, last_close=92)  # near low
    dr = dealing_range(candles)
    assert dr["pos"] < 0.3


def test_dealing_range_position_high():
    candles = _flat_range(100, 10, last_close=108)  # near high
    dr = dealing_range(candles)
    assert dr["pos"] > 0.7


def test_dealing_range_equilibrium():
    candles = _flat_range(100, 10, last_close=100)
    dr = dealing_range(candles)
    assert 0.4 < dr["pos"] < 0.6


# ─── range_state ──────────────────────────────────────────────────────────


def test_range_state_compressing():
    # older bars wide, recent bars narrow
    older  = [_c(100, 120, 80, 100) for _ in range(14)]
    recent = [_c(100, 102, 98, 100) for _ in range(14)]
    assert range_state(older + recent) == "compressing"


def test_range_state_expanding():
    older  = [_c(100, 102, 98, 100) for _ in range(14)]
    recent = [_c(100, 130, 70, 100) for _ in range(14)]
    assert range_state(older + recent) == "expanding"


def test_range_state_normal_when_insufficient():
    assert range_state([_c(100, 101, 99, 100)]) == "normal"


# ─── positioning ──────────────────────────────────────────────────────────


def test_positioning_trapped_longs():
    market = {
        "change_24h": -3.0,
        "bybit": {"funding": 0.0006, "oi_chg": 2.0},
        "ls_ratio": {"bnb_long": 65.0},
        "liquidations": {},
    }
    pos, notes = classify_positioning(market)
    assert pos == "trapped_longs"


def test_positioning_trapped_shorts():
    market = {
        "change_24h": 3.0,
        "bybit": {"funding": -0.0006, "oi_chg": 2.0},
        "ls_ratio": {"bnb_long": 35.0},
        "liquidations": {},
    }
    pos, notes = classify_positioning(market)
    assert pos == "trapped_shorts"


def test_positioning_balanced():
    market = {
        "change_24h": 0.2,
        "bybit": {"funding": 0.00001, "oi_chg": 0.0},
        "ls_ratio": {"bnb_long": 50.0},
        "liquidations": {},
    }
    pos, _ = classify_positioning(market)
    assert pos == "balanced"


# ─── classify_regime ──────────────────────────────────────────────────────


def _market(candles, cvd, **extra):
    m = {
        "price": candles[-1]["c"],
        "_klines": {"60": candles},
        "cvd": cvd,
        "bybit": {"funding": 0.0, "oi_chg": 0.0},
        "ls_ratio": {},
        "liquidations": {},
        "change_24h": 0.0,
    }
    m.update(extra)
    return m


def test_regime_accumulation():
    # цена у низа диапазона + CVD растёт → накопление
    older  = [_c(100, 120, 80, 100) for _ in range(20)]
    recent = [_c(92, 94, 90, 92) for _ in range(20)]  # сжатие у низа
    candles = older + recent
    cvd = {"trend": "up", "price_trend": "down", "divergence": True}
    reg = classify_regime(_market(candles, cvd))
    assert reg.phase == "accumulation"
    assert reg.bias == "long"
    assert reg.zone == "discount"


def test_regime_distribution():
    older  = [_c(100, 120, 80, 100) for _ in range(20)]
    recent = [_c(108, 110, 106, 108) for _ in range(20)]  # сжатие у верха
    candles = older + recent
    cvd = {"trend": "down", "price_trend": "up", "divergence": True}
    reg = classify_regime(_market(candles, cvd))
    assert reg.phase == "distribution"
    assert reg.bias == "short"
    assert reg.zone == "premium"


def test_regime_markup():
    # расширение вверх + поток вверх
    older  = [_c(100, 102, 98, 100) for _ in range(20)]
    recent = []
    base = 100
    for i in range(20):
        base += 2
        recent.append(_c(base, base + 5, base - 1, base + 3))
    candles = older + recent
    cvd = {"trend": "up", "price_trend": "up", "divergence": False}
    reg = classify_regime(_market(candles, cvd))
    assert reg.phase == "markup"
    assert reg.bias == "long"


def test_regime_neutral_when_no_signal():
    candles = [_c(100, 101, 99, 100) for _ in range(100)]
    cvd = {"trend": "unknown", "price_trend": "unknown", "divergence": False}
    reg = classify_regime(_market(candles, cvd))
    assert reg.phase == "neutral"
    assert reg.bias == "neutral"


def test_regime_returns_dataclass_with_summary():
    candles = [_c(100, 101, 99, 100) for _ in range(100)]
    cvd = {"trend": "unknown", "price_trend": "unknown", "divergence": False}
    reg = classify_regime(_market(candles, cvd))
    assert isinstance(reg, Regime)
    assert isinstance(reg.summary(), str)
    assert 0 <= reg.confidence <= 100
