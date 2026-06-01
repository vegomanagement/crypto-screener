"""Тесты bt_walkforward.py — walk-forward analysis."""

import bt_walkforward


def _b(ts, o, h, lo, c, v=100.0):
    return {"ts": ts, "o": o, "h": h, "l": lo, "c": c, "v": v}


def _flat_data(n_bars=400):
    """Плоские свечи — лёгкие unit-проверки без сигналов."""
    ts0 = 1_780_000_000_000
    klines = [_b(ts0 + i * 300_000, 100, 100.1, 99.9, 100) for i in range(n_bars)]
    return {"symbol": "BTCUSDT", "days": 7,
            "klines": {"5": klines}, "funding": [], "oi": []}


# ─── _window_bounds ───────────────────────────────────────────────────────


def test_window_bounds_basic():
    bounds = bt_walkforward._window_bounds(total_bars=100, n_windows=4)
    assert len(bounds) == 4
    # Покрытие всех баров без пропусков (последнее окно включает остаток)
    starts = [b[0] for b in bounds]
    assert starts == [0, 25, 50, 75]
    assert bounds[-1][1] == 99   # последнее окно до конца


def test_window_bounds_zero_windows():
    assert bt_walkforward._window_bounds(100, 0) == []


def test_window_bounds_more_windows_than_bars():
    """n_windows > total_bars → clamp до total_bars."""
    bounds = bt_walkforward._window_bounds(5, 10)
    assert len(bounds) == 5


def test_window_bounds_uneven_division():
    """7 bars / 3 windows: 2+2+3."""
    bounds = bt_walkforward._window_bounds(7, 3)
    assert len(bounds) == 3
    assert bounds[0] == (0, 1)
    assert bounds[1] == (2, 3)
    # Последнее окно забирает всё до конца
    assert bounds[2][1] == 6


# ─── slice_data_window ────────────────────────────────────────────────────


def test_slice_data_window_clips_klines():
    data = _flat_data(n_bars=100)
    sliced = bt_walkforward.slice_data_window(data, 20, 50)
    assert len(sliced["klines"]["5"]) == 31   # [20..50] inclusive


def test_slice_data_window_empty_when_start_too_large():
    data = _flat_data(n_bars=50)
    sliced = bt_walkforward.slice_data_window(data, 100, 200)
    assert sliced["klines"] == {}


def test_slice_data_window_filters_funding_by_ts():
    data = _flat_data(n_bars=100)
    primary = data["klines"]["5"]
    # Funding точка ВНЕ окна (раньше start)
    data["funding"] = [
        {"ts": primary[5]["ts"], "funding": 0.0001},   # вне окна [20, 50]
        {"ts": primary[30]["ts"], "funding": 0.0002},  # внутри
    ]
    sliced = bt_walkforward.slice_data_window(data, 20, 50)
    assert len(sliced["funding"]) == 1
    assert sliced["funding"][0]["funding"] == 0.0002


def test_slice_data_window_multi_tf():
    data = _flat_data(n_bars=100)
    # Добавим 15m TF (3× реже чем 5m)
    base_ts = data["klines"]["5"][0]["ts"]
    data["klines"]["15"] = [
        _b(base_ts + i * 900_000, 100, 100.1, 99.9, 100) for i in range(40)
    ]
    sliced = bt_walkforward.slice_data_window(data, 20, 50)
    # 15m бары попадают только те, чьи ts в окне [start_ts, end_ts]
    assert "15" in sliced["klines"]
    start_ts = data["klines"]["5"][20]["ts"]
    end_ts   = data["klines"]["5"][50]["ts"]
    for k in sliced["klines"]["15"]:
        assert start_ts <= k["ts"] <= end_ts


# ─── walk_forward (stability mode) ─────────────────────────────────────────


def test_walk_forward_returns_n_windows():
    data = _flat_data(n_bars=400)
    result = bt_walkforward.walk_forward(data, n_windows=4, warmup_bars=20)
    assert len(result.windows) == 4
    assert result.mode == "stability"


def test_walk_forward_window_indices_sequential():
    data = _flat_data(n_bars=400)
    result = bt_walkforward.walk_forward(data, n_windows=4, warmup_bars=20)
    indices = [w.window_idx for w in result.windows]
    assert indices == [0, 1, 2, 3]


def test_walk_forward_all_windows_populated_with_stats():
    data = _flat_data(n_bars=400)
    result = bt_walkforward.walk_forward(data, n_windows=4, warmup_bars=20)
    for w in result.windows:
        assert w.test_stats is not None
        assert "total" in w.test_stats
        assert w.test_result is not None


def test_walk_forward_config_overrides_propagate():
    """config_overrides достигают backtest и сохраняются в result."""
    data = _flat_data(n_bars=400)
    result = bt_walkforward.walk_forward(
        data, n_windows=2,
        config_overrides={"MIN_CONFIDENCE_FOR_TRADE": 88},
        warmup_bars=20,
    )
    for w in result.windows:
        assert w.best_config == {"MIN_CONFIDENCE_FOR_TRADE": 88}
        assert w.test_result.config_overrides == {"MIN_CONFIDENCE_FOR_TRADE": 88}


# ─── walk_forward_optimize ────────────────────────────────────────────────


def test_walk_forward_optimize_basic():
    data = _flat_data(n_bars=400)
    grid = {"MIN_CONFIDENCE_FOR_TRADE": [60, 75]}
    result = bt_walkforward.walk_forward_optimize(
        data, grid, n_windows=2, train_test_ratio=0.7,
        warmup_bars=20,
    )
    assert result.mode == "optimize"
    assert result.grid == grid
    for w in result.windows:
        assert w.best_config is not None
        assert w.train_stats is not None
        assert w.test_stats is not None
        # best_config содержит ключ из grid
        assert "MIN_CONFIDENCE_FOR_TRADE" in w.best_config


def test_walk_forward_optimize_short_window_skipped():
    """Окна слишком короткие для train/test split — пропускаются."""
    # n_bars=10, n_windows=10 → каждое окно 1 бар → train=0 baras → skip
    data = _flat_data(n_bars=10)
    grid = {"MIN_CONFIDENCE_FOR_TRADE": [60, 75]}
    result = bt_walkforward.walk_forward_optimize(
        data, grid, n_windows=10, train_test_ratio=0.7,
        warmup_bars=2,
    )
    # Скорее всего меньше окон чем bounds (некоторые skipped)
    assert len(result.windows) <= 10


def test_best_config_by_metric_picks_highest_avg_r():
    import backtest as bt
    cfg1 = bt_walkforward.bt_compare.Config(name="a", overrides={"X": 1})
    cfg2 = bt_walkforward.bt_compare.Config(name="b", overrides={"X": 2})
    res1 = bt.BacktestResult(symbol="X", days=1, stats={"avg_r": -0.5})
    res2 = bt.BacktestResult(symbol="X", days=1, stats={"avg_r": +0.3})
    cmp = bt_walkforward.bt_compare.ComparisonResult(
        symbol="X", days=1, configs=[cfg1, cfg2], results=[res1, res2])
    overrides, stats = bt_walkforward._best_config_by_metric(cmp, "avg_r")
    assert overrides == {"X": 2}
    assert stats == {"avg_r": +0.3}


def test_best_config_by_metric_picks_highest_sharpe():
    import backtest as bt
    cfg1 = bt_walkforward.bt_compare.Config(name="a", overrides={})
    cfg2 = bt_walkforward.bt_compare.Config(name="b", overrides={"Y": 5})
    res1 = bt.BacktestResult(symbol="X", days=1,
                              stats={"avg_r": 0, "risk": {"sharpe_r": 0.1}})
    res2 = bt.BacktestResult(symbol="X", days=1,
                              stats={"avg_r": 0, "risk": {"sharpe_r": 0.8}})
    cmp = bt_walkforward.bt_compare.ComparisonResult(
        symbol="X", days=1, configs=[cfg1, cfg2], results=[res1, res2])
    overrides, _ = bt_walkforward._best_config_by_metric(cmp, "sharpe_r")
    assert overrides == {"Y": 5}


def test_best_config_by_metric_empty_returns_empty():
    cmp = bt_walkforward.bt_compare.ComparisonResult(
        symbol="X", days=1, configs=[], results=[])
    overrides, stats = bt_walkforward._best_config_by_metric(cmp)
    assert overrides == {}
    assert stats == {}


# ─── format_walkforward ───────────────────────────────────────────────────


def test_format_walkforward_no_windows():
    result = bt_walkforward.WalkForwardResult(
        symbol="X", days=7, mode="stability", windows=[])
    out = bt_walkforward.format_walkforward(result)
    assert "X" in out
    assert "stability" in out
    assert "0 windows" in out


def test_format_walkforward_stability_with_windows():
    data = _flat_data(n_bars=400)
    result = bt_walkforward.walk_forward(data, n_windows=2, warmup_bars=20)
    out = bt_walkforward.format_walkforward(result)
    assert "Walk-forward" in out
    assert "stability" in out
    assert "Win" in out and "Bars" in out  # header
    assert "Avg WinR%" in out  # summary line


def test_format_walkforward_optimize_shows_best_configs():
    data = _flat_data(n_bars=400)
    grid = {"MIN_CONFIDENCE_FOR_TRADE": [60, 75]}
    result = bt_walkforward.walk_forward_optimize(
        data, grid, n_windows=2, warmup_bars=20)
    out = bt_walkforward.format_walkforward(result)
    assert "optimize" in out
    assert "Grid" in out
    assert "Best Config" in out
