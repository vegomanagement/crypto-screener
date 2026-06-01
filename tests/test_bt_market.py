"""Тесты bt_market.py — historical market dict builder."""

from datetime import timezone

import bt_market


def _b(ts, o, h, lo, c, v=100.0):
    return {"ts": ts, "o": o, "h": h, "l": lo, "c": c, "v": v}


# ─── ATR ──────────────────────────────────────────────────────────────────


def test_atr_returns_zero_for_short_series():
    assert bt_market.compute_atr([]) == 0.0
    assert bt_market.compute_atr([_b(1, 1, 2, 0, 1)] * 5) == 0.0


def test_atr_constant_range():
    """20 одинаковых баров с TR=1 → ATR должен ≈ 1."""
    klines = [_b(i, 100, 100.5, 99.5, 100) for i in range(20)]
    assert abs(bt_market.compute_atr(klines, period=14) - 1.0) < 1e-6


def test_atr_responds_to_volatility_spike():
    klines = [_b(i, 100, 100.5, 99.5, 100) for i in range(20)]
    spike = [_b(20, 100, 110, 90, 100)]  # TR=20
    atr_before = bt_market.compute_atr(klines, period=14)
    atr_after  = bt_market.compute_atr(klines + spike, period=14)
    assert atr_after > atr_before


# ─── RSI ──────────────────────────────────────────────────────────────────


def test_rsi_returns_50_for_short_series():
    assert bt_market.compute_rsi([]) == 50.0
    assert bt_market.compute_rsi([_b(1, 100, 100, 100, 100)] * 3) == 50.0


def test_rsi_all_gains_returns_100():
    klines = [_b(i, 100, 110, 100, 100 + i) for i in range(20)]
    assert bt_market.compute_rsi(klines, period=14) == 100.0


def test_rsi_all_losses_low_value():
    klines = [_b(i, 100, 100, 90, 100 - i) for i in range(20)]
    rsi = bt_market.compute_rsi(klines, period=14)
    assert rsi < 10  # очень низкий


def test_rsi_mid_range_for_alternating():
    klines = []
    for i in range(20):
        c = 100 + (1 if i % 2 == 0 else -1)
        klines.append(_b(i, 100, 101, 99, c))
    rsi = bt_market.compute_rsi(klines, period=14)
    assert 40 < rsi < 60


# ─── EMA / MACD ────────────────────────────────────────────────────────────


def test_ema_basic_smoothing():
    out = bt_market.compute_ema([10, 10, 10, 10, 10], 3)
    assert all(abs(v - 10) < 1e-6 for v in out)
    assert len(out) == 5


def test_ema_empty():
    assert bt_market.compute_ema([], 5) == []


def test_macd_trend_bull():
    """Восходящие closes → MACD trend 'bull'."""
    klines = [_b(i, 100 + i, 100 + i + 1, 100 + i - 1, 100 + i)
              for i in range(40)]
    macd = bt_market.compute_macd(klines)
    assert macd["trend"] == "bull"


def test_macd_trend_bear():
    klines = [_b(i, 200 - i, 200 - i + 1, 200 - i - 1, 200 - i)
              for i in range(40)]
    macd = bt_market.compute_macd(klines)
    assert macd["trend"] == "bear"


def test_macd_neutral_when_too_few():
    assert bt_market.compute_macd([_b(i, 100, 101, 99, 100) for i in range(5)])["trend"] == "neutral"


# ─── CVD proxy ─────────────────────────────────────────────────────────────


def test_cvd_unknown_for_empty():
    out = bt_market.compute_cvd_proxy([])
    assert out["trend"] == "unknown"
    assert out["divergence"] is False


def test_cvd_trend_up_when_buyers_dominate():
    """close ≥ open во всех барах → CVD растёт."""
    klines = [_b(i, 100, 101, 99, 100 + i, v=100) for i in range(50)]
    out = bt_market.compute_cvd_proxy(klines)
    assert out["trend"] == "up"
    assert out["price_trend"] == "up"
    assert out["divergence"] is False


def test_cvd_divergence_detected():
    """Цена растёт, но CVD падает (downclose-бары с большим volume на росте)."""
    klines = []
    for i in range(50):
        # Цена растёт по closes, но open > close (downclose) → CVD считает вниз
        klines.append(_b(i, 100 + i + 0.5, 102 + i, 99 + i, 100 + i, v=100))
    out = bt_market.compute_cvd_proxy(klines)
    assert out["price_trend"] == "up"
    assert out["trend"] == "down"
    assert out["divergence"] is True


# ─── EMA biases ────────────────────────────────────────────────────────────


def test_ema_biases_bull_on_uptrend():
    klines = [_b(i, 100 + i, 100 + i + 1, 100 + i - 1, 100 + i)
              for i in range(40)]
    assert bt_market.compute_ema_biases(klines) == "bull"


def test_ema_biases_bear_on_downtrend():
    klines = [_b(i, 200 - i, 200 - i + 1, 200 - i - 1, 200 - i)
              for i in range(40)]
    assert bt_market.compute_ema_biases(klines) == "bear"


def test_ema_biases_neutral_when_too_few():
    klines = [_b(i, 100, 101, 99, 100) for i in range(10)]
    assert bt_market.compute_ema_biases(klines) == "neutral"


# ─── change_24h ────────────────────────────────────────────────────────────


def test_change_24h_zero_when_too_short():
    klines = [_b(i, 100, 101, 99, 100) for i in range(100)]
    assert bt_market.compute_change_24h(klines, 99) == 0.0


def test_change_24h_correct_for_rally():
    """288 5m-баров (24ч), цена выросла с 100 до 110 → +10%."""
    klines = [_b(i, 100, 100.5, 99.5, 100) for i in range(288)]
    klines.append(_b(288, 110, 110.5, 109.5, 110))
    chg = bt_market.compute_change_24h(klines, 288)
    assert abs(chg - 10.0) < 1e-6


# ─── Pivots ────────────────────────────────────────────────────────────────


def test_pivots_empty_when_first_day():
    klines = [_b(1, 100, 110, 95, 105)]
    assert bt_market.compute_pivots(klines, 0) == {}


def test_pivots_classical_formula():
    # Prev day H=110, L=95, C=105 → P=(110+95+105)/3 ≈ 103.33
    klines = [_b(1, 100, 110, 95, 105), _b(2, 105, 108, 100, 104)]
    p = bt_market.compute_pivots(klines, 1)
    assert p["PDH"] == 110
    assert p["PDL"] == 95
    assert abs(p["P"]  - (110 + 95 + 105) / 3) < 1e-6
    assert abs(p["R1"] - (2 * p["P"] - 95)) < 1e-6
    assert abs(p["S1"] - (2 * p["P"] - 110)) < 1e-6


# ─── funding_at / oi_change_at ─────────────────────────────────────────────


def test_funding_at_empty_returns_zero():
    assert bt_market.funding_at([], 1000) == 0.0


def test_funding_at_picks_last_before_ts():
    hist = [
        {"ts": 100, "funding": 0.0001},
        {"ts": 200, "funding": 0.0002},
        {"ts": 300, "funding": 0.0003},
    ]
    assert bt_market.funding_at(hist, 150) == 0.0001
    assert bt_market.funding_at(hist, 250) == 0.0002
    assert bt_market.funding_at(hist, 1000) == 0.0003


def test_oi_change_at_returns_zero_for_short_history():
    assert bt_market.oi_change_at([], 1000) == 0.0
    assert bt_market.oi_change_at([{"ts": 100, "oi": 1000}], 200) == 0.0


def test_oi_change_at_computes_24h_change():
    # 25 точек (24h lookback + 1)
    hist = [{"ts": i * 3600_000, "oi": 1000 + i * 10} for i in range(25)]
    # past = hist[0].oi = 1000, cur = hist[24].oi = 1240 → +24%
    chg = bt_market.oi_change_at(hist, 24 * 3600_000, lookback_hours=24)
    assert abs(chg - 24.0) < 1e-6


# ─── build_market_at: интеграция ──────────────────────────────────────────


def _fake_data(n_5m=100, days_5m_only: bool = True):
    """Сгенерировать data dict в формате bt_data.fetch_all."""
    base_ts = 1_780_000_000_000   # ~июнь 2026
    klines_5m = [_b(base_ts + i * 300_000, 100, 100.5, 99.5, 100)
                 for i in range(n_5m)]
    data = {"symbol": "BTCUSDT", "days": 1,
            "klines": {"5": klines_5m}, "funding": [], "oi": []}
    if not days_5m_only:
        # daily 5 баров
        klines_d = [_b(base_ts + i * 86_400_000, 100, 110, 95, 105)
                    for i in range(5)]
        data["klines"]["D"] = klines_d
    return data


def test_build_market_at_returns_empty_on_bad_idx():
    data = _fake_data(n_5m=10)
    assert bt_market.build_market_at(data, idx=100) == {}
    assert bt_market.build_market_at(data, idx=-1) == {}


def test_build_market_at_shape_match_prod():
    data = _fake_data(n_5m=300)
    m = bt_market.build_market_at(data, idx=299)
    # Обязательные ключи
    for key in ("symbol", "price", "ts", "indicators", "bybit", "cvd",
                "ema_biases", "_klines", "change_24h", "pivots", "vp",
                "ls_ratio", "liquidations", "turtle_1h", "turtle_4h"):
        assert key in m, f"missing key: {key}"
    # indicators
    for sub in ("atr", "atr_pct", "rsi", "macd", "rsi_div"):
        assert sub in m["indicators"]
    # bybit
    assert "funding" in m["bybit"] and "oi_chg" in m["bybit"]
    # cvd
    assert "trend" in m["cvd"] and "divergence" in m["cvd"]


def test_build_market_at_ts_is_aware_utc():
    data = _fake_data(n_5m=50)
    m = bt_market.build_market_at(data, idx=49)
    assert m["ts"].tzinfo == timezone.utc


def test_build_market_at_klines_sliced_correctly():
    data = _fake_data(n_5m=100)
    m = bt_market.build_market_at(data, idx=50)
    # _klines["5"] не должен включать бары после idx
    assert len(m["_klines"]["5"]) == 51   # 0..50 inclusive
    cur_ts = data["klines"]["5"][50]["ts"]
    assert all(k["ts"] <= cur_ts for k in m["_klines"]["5"])


def test_build_market_at_pivots_computed_when_daily_present():
    data = _fake_data(n_5m=300, days_5m_only=False)
    m = bt_market.build_market_at(data, idx=299)
    # daily TF есть → pivots вычислены
    assert "PDH" in m["pivots"]
    assert "P" in m["pivots"]


def test_build_market_at_pivots_empty_when_no_daily():
    data = _fake_data(n_5m=300, days_5m_only=True)
    m = bt_market.build_market_at(data, idx=299)
    assert m["pivots"] == {}


def test_build_market_at_funding_resolved():
    data = _fake_data(n_5m=50)
    # funding точка ДО ts последнего 5m бара
    cur_ts = data["klines"]["5"][-1]["ts"]
    data["funding"] = [{"ts": cur_ts - 60_000, "funding": 0.0005}]
    m = bt_market.build_market_at(data, idx=49)
    assert m["bybit"]["funding"] == 0.0005


def test_build_market_at_safe_with_empty_funding_oi():
    data = _fake_data(n_5m=50)
    m = bt_market.build_market_at(data, idx=49)
    assert m["bybit"]["funding"] == 0.0
    assert m["bybit"]["oi_chg"] == 0.0
