"""Тесты htf_bias.py — Top-down PDA bias detector."""

from htf_bias import HTFBias, compute_htf_bias


def _kline(close, high=None, low=None, ts=0):
    high = high if high is not None else close + 0.5
    low  = low  if low  is not None else close - 0.5
    return {"ts": ts, "o": close, "h": high, "l": low, "c": close, "v": 100}


def _flat_klines(level: float, n: int) -> list:
    """N плоских свечей на одном уровне."""
    return [_kline(level) for _ in range(n)]


def _range_klines(low: float, high: float, current_pos: float,
                  n: int = 100) -> list:
    """
    Создаёт klines так, что dealing_range даст [low, high], а current_pos
    (0..1) определяет, где сейчас цена в диапазоне.
    Делает: первые n-2 баров — равномерно low/high; последняя свеча — current.
    """
    klines = []
    # Один бар достигающий low
    klines.append(_kline(low, high=low + 0.1, low=low))
    # Один бар достигающий high
    klines.append(_kline(high, high=high, low=high - 0.1))
    # Заполнитель в середине
    mid = (low + high) / 2
    for _ in range(n - 3):
        klines.append(_kline(mid))
    # Финальный бар с current price
    current = low + (high - low) * current_pos
    klines.append(_kline(current))
    return klines


# ─── compute_htf_bias: 3/3 agreement ──────────────────────────────────────


def test_htf_bias_strong_long_all_discount():
    """Все 3 TF в discount → strong long."""
    market = {"_klines": {
        "D":   _range_klines(100, 200, current_pos=0.2),   # discount
        "240": _range_klines(100, 200, current_pos=0.3),   # discount
        "60":  _range_klines(100, 200, current_pos=0.35),  # discount
    }}
    bias = compute_htf_bias(market)
    assert bias.strength == "strong"
    assert bias.direction == "long"
    assert bias.votes_long == 3
    assert bias.votes_short == 0


def test_htf_bias_strong_short_all_premium():
    """Все 3 TF в premium → strong short."""
    market = {"_klines": {
        "D":   _range_klines(100, 200, current_pos=0.8),
        "240": _range_klines(100, 200, current_pos=0.7),
        "60":  _range_klines(100, 200, current_pos=0.65),
    }}
    bias = compute_htf_bias(market)
    assert bias.strength == "strong"
    assert bias.direction == "short"


# ─── 2/3 agreement → moderate ─────────────────────────────────────────────


def test_htf_bias_moderate_long_2_of_3():
    """2 TF в discount, 1 в equilibrium → moderate long."""
    market = {"_klines": {
        "D":   _range_klines(100, 200, current_pos=0.3),    # discount
        "240": _range_klines(100, 200, current_pos=0.35),   # discount
        "60":  _range_klines(100, 200, current_pos=0.5),    # equilibrium
    }}
    bias = compute_htf_bias(market)
    assert bias.strength == "moderate"
    assert bias.direction == "long"
    assert bias.votes_long == 2


def test_htf_bias_moderate_short_2_of_3():
    market = {"_klines": {
        "D":   _range_klines(100, 200, current_pos=0.7),
        "240": _range_klines(100, 200, current_pos=0.65),
        "60":  _range_klines(100, 200, current_pos=0.5),
    }}
    bias = compute_htf_bias(market)
    assert bias.strength == "moderate"
    assert bias.direction == "short"


# ─── Mixed votes → neutral ────────────────────────────────────────────────


def test_htf_bias_neutral_when_mixed():
    """1 в discount, 1 в premium, 1 в equilibrium → neutral."""
    market = {"_klines": {
        "D":   _range_klines(100, 200, current_pos=0.2),   # discount
        "240": _range_klines(100, 200, current_pos=0.8),   # premium
        "60":  _range_klines(100, 200, current_pos=0.5),   # equilibrium
    }}
    bias = compute_htf_bias(market)
    assert bias.strength == "neutral"
    assert bias.direction == "neutral"


def test_htf_bias_neutral_all_equilibrium():
    market = {"_klines": {
        "D":   _range_klines(100, 200, current_pos=0.5),
        "240": _range_klines(100, 200, current_pos=0.5),
        "60":  _range_klines(100, 200, current_pos=0.5),
    }}
    bias = compute_htf_bias(market)
    assert bias.strength == "neutral"
    assert bias.direction == "neutral"


# ─── Weak (1/N votes) ─────────────────────────────────────────────────────


def test_htf_bias_weak_one_tf_only():
    """1 в discount, 2 в equilibrium → weak long."""
    market = {"_klines": {
        "D":   _range_klines(100, 200, current_pos=0.2),   # discount
        "240": _range_klines(100, 200, current_pos=0.5),
        "60":  _range_klines(100, 200, current_pos=0.5),
    }}
    bias = compute_htf_bias(market)
    assert bias.strength == "weak"
    assert bias.direction == "long"
    assert bias.votes_long == 1


# ─── Missing TFs / graceful ───────────────────────────────────────────────


def test_htf_bias_no_klines_returns_neutral():
    bias = compute_htf_bias({})
    assert bias.strength == "neutral"
    assert bias.direction == "neutral"
    assert bias.available_tfs == []


def test_htf_bias_partial_availability_2_tfs():
    """Только 2 TF доступны → ещё может быть strong (2/2)."""
    market = {"_klines": {
        "D":   _range_klines(100, 200, current_pos=0.2),
        "240": _range_klines(100, 200, current_pos=0.3),
        # 60 — нет
    }}
    bias = compute_htf_bias(market)
    assert "D" in bias.available_tfs
    assert "240" in bias.available_tfs
    assert "60" not in bias.available_tfs
    assert bias.strength == "strong"
    assert bias.direction == "long"


def test_htf_bias_single_tf_no_strong():
    """Только 1 TF — strong невозможен (нужно >= 2 для consensus)."""
    market = {"_klines": {
        "D": _range_klines(100, 200, current_pos=0.2),
    }}
    bias = compute_htf_bias(market)
    # 1/1 vote_long — strength weak (не strong т.к. n<2)
    assert bias.strength == "weak"
    assert bias.direction == "long"


def test_htf_bias_tf_with_too_few_bars_marked_unknown():
    """TF с менее MIN_BARS_PER_TF баров → unknown."""
    market = {"_klines": {
        "D":   [_kline(150) for _ in range(5)],   # too few
        "240": _range_klines(100, 200, current_pos=0.2),
        "60":  _range_klines(100, 200, current_pos=0.2),
    }}
    bias = compute_htf_bias(market)
    assert bias.zones["D"] == "unknown"
    assert "D" not in bias.available_tfs
    # 2 TF говорят long → strong (since n=2 and votes_long==n)
    assert bias.strength == "strong"
    assert bias.direction == "long"


# ─── HTFBias structure ─────────────────────────────────────────────────────


def test_htfbias_dataclass_fields():
    market = {"_klines": {
        "D":   _range_klines(100, 200, current_pos=0.3),
        "240": _range_klines(100, 200, current_pos=0.3),
        "60":  _range_klines(100, 200, current_pos=0.3),
    }}
    bias = compute_htf_bias(market)
    assert isinstance(bias, HTFBias)
    for field in ("strength", "direction", "zones", "votes_long",
                  "votes_short", "available_tfs"):
        assert hasattr(bias, field)
    assert set(bias.zones.keys()) == {"D", "240", "60"}
