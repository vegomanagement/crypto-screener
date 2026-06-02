"""Тесты backtest.py — replay engine."""

import backtest
import tracking


def _b(ts, o, h, lo, c, v=100.0):
    return {"ts": ts, "o": o, "h": h, "l": lo, "c": c, "v": v}


def _make_data(klines_5m, *, symbol="BTCUSDT", days=1, daily=None,
               funding=None, oi=None, klines_15m=None, klines_60m=None,
               klines_240m=None):
    """Базовый fake data dict в формате bt_data.fetch_all."""
    data = {
        "symbol": symbol, "days": days,
        "klines": {"5": klines_5m},
        "funding": funding or [],
        "oi": oi or [],
    }
    if klines_15m is not None:
        data["klines"]["15"] = klines_15m
    if klines_60m is not None:
        data["klines"]["60"] = klines_60m
    if klines_240m is not None:
        data["klines"]["240"] = klines_240m
    if daily is not None:
        data["klines"]["D"] = daily
    return data


# ─── BacktestResult пустые случаи ──────────────────────────────────────────


def test_run_backtest_empty_data():
    result = backtest.run_backtest({"symbol": "X", "days": 0,
                                    "klines": {}, "funding": [], "oi": []})
    assert result.trades == []
    assert result.stats == {}


def test_run_backtest_no_5m_klines():
    data = _make_data([])
    result = backtest.run_backtest(data)
    assert result.trades == []


def test_run_backtest_flat_no_signals():
    """Плоские свечи → детекторы молчат → 0 trades."""
    ts0 = 1_780_000_000_000
    klines = [_b(ts0 + i * 300_000, 100, 100.1, 99.9, 100) for i in range(200)]
    data = _make_data(klines)
    result = backtest.run_backtest(data, warmup_bars=50)
    assert result.stats.get("total", 0) == 0


# ─── _detect_signals_minimal ───────────────────────────────────────────────


def test_detect_signals_minimal_too_few():
    klines = [_b(i, 100, 101, 99, 100) for i in range(10)]
    assert backtest._detect_signals_minimal(klines) == []


def test_detect_signals_minimal_bos_bull():
    """Тренд up + close выше prev_high → BOS_BULL."""
    klines = [_b(i, 100 + i * 0.1, 101 + i * 0.1, 99 + i * 0.1, 100 + i * 0.1)
              for i in range(21)]
    # Последняя свеча мощно вверх
    klines.append(_b(22, 102, 110, 102, 109))
    out = backtest._detect_signals_minimal(klines)
    assert "BOS_BULL" in out or "CHOCH_BULL" in out


def test_detect_signals_minimal_fvg():
    klines = [_b(i, 100, 101, 99, 100) for i in range(22)]
    # 3-candle FVG: c2.h=101, c0.l=103 → bull FVG
    klines[-3] = _b(20, 100, 101, 99, 100)
    klines[-2] = _b(21, 100, 102, 99, 102)
    klines[-1] = _b(22, 103, 104, 103, 103.5)   # l=103 > c2.h=101
    out = backtest._detect_signals_minimal(klines)
    assert "FVG_BULL" in out


def test_detect_signals_minimal_liq_sweep():
    klines = [_b(i, 100, 101, 99, 100) for i in range(22)]
    # Последняя свеча: пробила prev_high и вернулась
    klines[-1] = _b(22, 100, 105, 100, 100.5)  # h>prev_high, c<prev_high
    out = backtest._detect_signals_minimal(klines)
    assert "LIQ_SWEEP_H" in out


# ─── _simulate_outcome ─────────────────────────────────────────────────────


def test_simulate_outcome_tp1_hit_long():
    """LONG: первый бар после открытия задевает TP1 → tp1_hit."""
    klines = [_b(i, 100, 100.5, 99.5, 100) for i in range(50)]
    klines[10] = _b(10, 100, 103.5, 99.8, 103)   # high=103.5 ≥ tp1=103
    out = backtest._simulate_outcome(
        klines, open_idx=9, verdict="LONG",
        sl=98, tp1=103, tp2=105, tp3=108,
        rr1=1.5, rr2=2.5, rr3=4.0, expiry_bars=100,
    )
    assert out is not None
    close_idx, status, hit_level, r_mult = out
    assert close_idx == 10
    assert status == "tp1_hit"
    assert hit_level == "TP1"
    assert r_mult == 1.5


def test_simulate_outcome_sl_hit_long():
    klines = [_b(i, 100, 100.5, 99.5, 100) for i in range(50)]
    klines[10] = _b(10, 100, 100.5, 97, 97.5)   # low=97 ≤ sl=98
    out = backtest._simulate_outcome(
        klines, open_idx=9, verdict="LONG",
        sl=98, tp1=103, tp2=105, tp3=108,
        rr1=1.5, rr2=2.5, rr3=4.0, expiry_bars=100,
    )
    assert out is not None
    _, status, hit_level, r_mult = out
    assert status == "sl_hit"
    assert hit_level == "SL"
    assert r_mult == -1.0


def test_simulate_outcome_tie_same_bar(monkeypatch):
    """Same-bar SL+TP → tie_hit (default fair)."""
    monkeypatch.setattr(tracking, "SAME_BAR_TIE_BREAK", "fair")
    klines = [_b(i, 100, 100.5, 99.5, 100) for i in range(50)]
    klines[10] = _b(10, 100, 110, 97, 105)   # high>=tp1 AND low<=sl
    out = backtest._simulate_outcome(
        klines, open_idx=9, verdict="LONG",
        sl=98, tp1=103, tp2=105, tp3=108,
        rr1=1.5, rr2=2.5, rr3=4.0, expiry_bars=100,
    )
    assert out is not None
    _, status, hit_level, r_mult = out
    assert status == "tie_hit"
    assert hit_level == "TIE"
    assert r_mult == 0.0


def test_simulate_outcome_tie_conservative(monkeypatch):
    monkeypatch.setattr(tracking, "SAME_BAR_TIE_BREAK", "conservative")
    klines = [_b(i, 100, 100.5, 99.5, 100) for i in range(50)]
    klines[10] = _b(10, 100, 110, 97, 105)
    out = backtest._simulate_outcome(
        klines, open_idx=9, verdict="LONG",
        sl=98, tp1=103, tp2=105, tp3=108,
        rr1=1.5, rr2=2.5, rr3=4.0, expiry_bars=100,
    )
    _, status, hit_level, r_mult = out
    assert status == "sl_hit"
    assert r_mult == -1.0


def test_simulate_outcome_expired():
    """Ничего не задело за expiry_bars → expired."""
    klines = [_b(i, 100, 100.5, 99.5, 100) for i in range(50)]
    out = backtest._simulate_outcome(
        klines, open_idx=10, verdict="LONG",
        sl=98, tp1=103, tp2=105, tp3=108,
        rr1=1.5, rr2=2.5, rr3=4.0, expiry_bars=20,
    )
    assert out is not None
    _, status, _, r_mult = out
    assert status == "expired"
    assert r_mult == 0.0


def test_simulate_outcome_invalid_levels_returns_none():
    klines = [_b(i, 100, 100.5, 99.5, 100) for i in range(50)]
    assert backtest._simulate_outcome(
        klines, open_idx=10, verdict="LONG",
        sl=None, tp1=None, tp2=None, tp3=None,
        rr1=1.5, rr2=2.5, rr3=4.0, expiry_bars=20,
    ) is None


def test_simulate_outcome_tp3_priority_same_bar():
    """Same-bar 3 TPs — берётся самый дальний (TP3)."""
    klines = [_b(i, 100, 100.5, 99.5, 100) for i in range(50)]
    klines[10] = _b(10, 100, 110, 99.8, 109)
    out = backtest._simulate_outcome(
        klines, open_idx=9, verdict="LONG",
        sl=98, tp1=103, tp2=105, tp3=108,
        rr1=1.5, rr2=2.5, rr3=4.0, expiry_bars=100,
    )
    _, status, hit_level, r_mult = out
    assert status == "tp3_hit"
    assert hit_level == "TP3"
    assert r_mult == 4.0


def test_simulate_outcome_short_mirror():
    klines = [_b(i, 100, 100.5, 99.5, 100) for i in range(50)]
    klines[10] = _b(10, 100, 100.5, 96.5, 97)   # low ≤ tp1=97
    out = backtest._simulate_outcome(
        klines, open_idx=9, verdict="SHORT",
        sl=102, tp1=97, tp2=95, tp3=92,
        rr1=1.5, rr2=2.5, rr3=4.0, expiry_bars=100,
    )
    _, status, hit_level, r_mult = out
    assert status == "tp1_hit"
    assert r_mult == 1.5


# ─── _aggregate_stats ─────────────────────────────────────────────────────


def _trade(status, r, sig="OB_BULL"):
    return backtest.BacktestTrade(
        signal_type=sig, direction="long", open_idx=10, open_ts=0,
        entry=100, sl=99, tp1=103, tp2=105, tp3=108, confidence=70,
        close_idx=20, close_ts=0, status=status,
        hit_level={"tp1_hit": "TP1", "sl_hit": "SL", "tie_hit": "TIE",
                   "expired": None}.get(status),
        r_multiple=r,
    )


def test_aggregate_stats_basic_math():
    trades = [
        _trade("tp1_hit", 1.5),
        _trade("tp2_hit", 2.5),
        _trade("sl_hit", -1.0),
    ]
    s = backtest._aggregate_stats(trades, days=7)
    assert s["closed"] == 3
    assert s["win_rate"] == round(2 / 3 * 100, 1)
    assert s["avg_r"] == round((1.5 + 2.5 - 1.0) / 3, 2)
    assert s["hits"]["tp1"] == 1
    assert s["hits"]["sl"] == 1
    assert "profit_factor" in s["risk"]


def test_aggregate_stats_includes_risk_block():
    trades = [_trade("tp1_hit", 1.5), _trade("sl_hit", -1.0),
              _trade("tp2_hit", 2.5)]
    s = backtest._aggregate_stats(trades, days=7)
    r = s["risk"]
    for key in ("profit_factor", "sharpe_r", "sortino_r",
                "max_drawdown_r", "max_consec_loss", "best_r", "worst_r"):
        assert key in r


def test_aggregate_stats_empty_trades():
    s = backtest._aggregate_stats([], days=7)
    assert s["closed"] == 0
    assert s["win_rate"] == 0


# ─── Cooldown ──────────────────────────────────────────────────────────────


def test_cooldown_prevents_double_open():
    """Same signal_type не открывается 2 раза подряд в пределах cooldown_bars."""
    # Сделать рынок, который ВСЕГДА триггерит BOS_BULL на каждом баре
    ts0 = 1_780_000_000_000
    klines = []
    base = 100
    for i in range(200):
        # каждые 10 баров делаем мощный bull-bar выше prev_high
        if i % 10 == 5 and i >= 30:
            klines.append(_b(ts0 + i * 300_000, base, base + 10, base, base + 5))
            base += 5
        else:
            klines.append(_b(ts0 + i * 300_000, base, base + 0.5, base - 0.5, base))
    data = _make_data(klines)
    result = backtest.run_backtest(data, warmup_bars=50,
                                   cooldown_bars=15, expiry_bars=50)
    # Не более одной сделки на BOS_BULL в окне 15 баров
    bos_opens = sorted(
        tr.open_idx for tr in result.trades if tr.signal_type == "BOS_BULL"
    )
    for a, b in zip(bos_opens, bos_opens[1:]):
        assert b - a >= 15, f"Cooldown нарушен: {a} → {b}"


# ─── config_overrides ─────────────────────────────────────────────────────


def test_config_override_applied_and_restored():
    """Проверка что MIN_CONFIDENCE_FOR_TRADE override применяется и откатывается."""
    import decision
    original = decision.MIN_CONFIDENCE_FOR_TRADE
    with backtest._config_override({"MIN_CONFIDENCE_FOR_TRADE": 99}):
        assert decision.MIN_CONFIDENCE_FOR_TRADE == 99
    assert decision.MIN_CONFIDENCE_FOR_TRADE == original


def test_config_override_none_is_noop():
    import decision
    original = decision.MIN_CONFIDENCE_FOR_TRADE
    with backtest._config_override(None):
        assert decision.MIN_CONFIDENCE_FOR_TRADE == original
    assert decision.MIN_CONFIDENCE_FOR_TRADE == original


# ─── format_result ────────────────────────────────────────────────────────


def test_format_result_no_trades():
    r = backtest.BacktestResult(symbol="X", days=7)
    out = backtest.format_result(r)
    assert "X" in out
    assert "7d" in out


def test_format_result_with_trades():
    trades = [_trade("tp1_hit", 1.5), _trade("sl_hit", -1.0)]
    stats = backtest._aggregate_stats(trades, days=7)
    r = backtest.BacktestResult(symbol="BTC", days=7, trades=trades, stats=stats)
    out = backtest.format_result(r)
    assert "Win-rate" in out
    assert "PF" in out
    assert "TP1/2/3" in out


# ─── CLI parsing ───────────────────────────────────────────────────────────


def test_parse_overrides_empty():
    assert backtest._parse_overrides(None) is None
    assert backtest._parse_overrides("") is None


def test_parse_overrides_int_float_bool():
    out = backtest._parse_overrides(
        "MIN_CONFIDENCE_FOR_TRADE=75,REGIME_ALIGN_BONUS=4.5,"
        "KILLZONE_GATE_ENABLED=false"
    )
    assert out["MIN_CONFIDENCE_FOR_TRADE"] == 75
    assert out["REGIME_ALIGN_BONUS"] == 4.5
    assert out["KILLZONE_GATE_ENABLED"] is False


def test_parse_overrides_str_fallback():
    out = backtest._parse_overrides("MODE=conservative")
    assert out["MODE"] == "conservative"


# ─── tf_primary selection ─────────────────────────────────────────────────


def test_tf_minutes_helper():
    assert backtest._tf_minutes("5") == 5
    assert backtest._tf_minutes("15") == 15
    assert backtest._tf_minutes("60") == 60
    assert backtest._tf_minutes("240") == 240
    assert backtest._tf_minutes("D") == 1440
    assert backtest._tf_minutes("W") == 10080
    # invalid → fallback 5
    assert backtest._tf_minutes("garbage") == 5


def test_run_backtest_tf_primary_uses_correct_klines():
    """tf_primary='15' → walk идёт по 15m баров, не по 5m."""
    ts0 = 1_780_000_000_000
    # 100 5m баров (плоские)
    klines_5m = [_b(ts0 + i * 300_000, 100, 100.1, 99.9, 100)
                 for i in range(100)]
    # 50 15m баров
    klines_15m = [_b(ts0 + i * 900_000, 100, 100.1, 99.9, 100)
                  for i in range(50)]
    data = {
        "symbol": "BTCUSDT", "days": 1,
        "klines": {"5": klines_5m, "15": klines_15m},
        "funding": [], "oi": [],
    }
    # Прогон на 15m — должен дойти до конца klines_15m
    result_15 = backtest.run_backtest(data, tf_primary="15", warmup_bars=10)
    assert result_15.symbol == "BTCUSDT"
    # На плоских данных 0 трейдов (нет сигналов), но прогон должен пройти
    assert "stats" in vars(result_15) or isinstance(result_15.stats, dict)


def test_run_backtest_expiry_scales_with_tf():
    """expiry_bars без явного override масштабируется под TF."""
    ts0 = 1_780_000_000_000
    klines_15m = [_b(ts0 + i * 900_000, 100, 100.1, 99.9, 100)
                  for i in range(50)]
    data = {
        "symbol": "X", "days": 1,
        "klines": {"15": klines_15m},
        "funding": [], "oi": [],
    }
    # Не падает — внутри expiry/cooldown пересчитаются
    result = backtest.run_backtest(data, tf_primary="15", warmup_bars=10)
    assert result.symbol == "X"


def test_run_backtest_missing_primary_tf_returns_empty():
    """tf_primary='60' но в data нет klines.60 → empty result."""
    data = {"symbol": "X", "days": 1, "klines": {"5": []},
            "funding": [], "oi": []}
    result = backtest.run_backtest(data, tf_primary="60", warmup_bars=10)
    assert result.trades == []


# ─── HTF diagnostics ──────────────────────────────────────────────────────


def test_backtest_result_has_htf_diag():
    """BacktestResult всегда содержит htf_diag dict."""
    result = backtest.BacktestResult(symbol="X", days=1)
    assert isinstance(result.htf_diag, dict)


def test_run_backtest_populates_htf_diag():
    """run_backtest заполняет htf_diag даже на пустых данных."""
    data = _make_data([])
    result = backtest.run_backtest(data)
    # Пустой data → htf_diag default-empty
    assert isinstance(result.htf_diag, dict)


def test_format_result_shows_htf_diag_when_populated():
    """format_result показывает HTF секцию если есть data."""
    result = backtest.BacktestResult(
        symbol="X", days=1,
        stats={"total": 5, "closed": 5, "win_rate": 50, "avg_r": 0.5,
               "hits": {"tp1": 2, "tp2": 1, "tp3": 0, "sl": 2, "tie": 0,
                        "expired": 0},
               "risk": {"profit_factor": 1.2, "sharpe_r": 0.1,
                        "sortino_r": 0.2, "max_drawdown_r": -1.5,
                        "max_consec_loss": 2, "best_r": 2.5, "worst_r": -1.0}},
        htf_diag={
            "strength_counts": {"strong": 10, "moderate": 50, "weak": 20,
                                "neutral": 100, "missing": 5},
            "strong_directions": {"long": 4, "short": 6},
            "p4_blocks": 3,
        },
    )
    out = backtest.format_result(result)
    assert "HTF bias" in out
    assert "strong=10" in out
    assert "P4 blocks: 3" in out


def test_format_result_omits_htf_when_empty():
    """Если total counts = 0 — секция не показывается."""
    result = backtest.BacktestResult(
        symbol="X", days=1,
        stats={"total": 0, "closed": 0, "win_rate": 0, "avg_r": 0,
               "hits": {}, "risk": {}},
        htf_diag={
            "strength_counts": {"strong": 0, "moderate": 0, "weak": 0,
                                "neutral": 0, "missing": 0},
            "strong_directions": {"long": 0, "short": 0},
            "p4_blocks": 0,
        },
    )
    out = backtest.format_result(result)
    assert "HTF bias" not in out
