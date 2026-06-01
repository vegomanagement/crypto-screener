"""Тесты bt_compare.py — comparison + parameter sweep."""

import bt_compare


def _b(ts, o, h, lo, c, v=100.0):
    return {"ts": ts, "o": o, "h": h, "l": lo, "c": c, "v": v}


def _flat_data():
    """Плоские свечи → 0 trades, но run_backtest должен отработать без ошибок."""
    ts0 = 1_780_000_000_000
    klines = [_b(ts0 + i * 300_000, 100, 100.1, 99.9, 100) for i in range(200)]
    return {"symbol": "BTCUSDT", "days": 1,
            "klines": {"5": klines}, "funding": [], "oi": []}


# ─── Config dataclass / _to_config ────────────────────────────────────────


def test_config_as_dict():
    c = bt_compare.Config(name="baseline", overrides={"X": 5})
    d = c.as_dict()
    assert d == {"name": "baseline", "overrides": {"X": 5}}


def test_to_config_passthrough():
    c = bt_compare.Config(name="x")
    assert bt_compare._to_config(c) is c


def test_to_config_from_dict():
    c = bt_compare._to_config({"name": "y", "overrides": {"A": 1}})
    assert isinstance(c, bt_compare.Config)
    assert c.name == "y"
    assert c.overrides == {"A": 1}


def test_to_config_invalid_raises():
    import pytest
    with pytest.raises(TypeError):
        bt_compare._to_config(123)


# ─── compare ───────────────────────────────────────────────────────────────


def test_compare_runs_each_config():
    data = _flat_data()
    configs = [
        bt_compare.Config(name="baseline"),
        bt_compare.Config(name="strict",
                          overrides={"MIN_CONFIDENCE_FOR_TRADE": 95}),
    ]
    result = bt_compare.compare(data, configs, warmup_bars=50)
    assert result.symbol == "BTCUSDT"
    assert len(result.results) == 2
    assert len(result.configs) == 2
    # На плоских данных у обоих 0 trades
    assert result.results[0].stats.get("total", 0) == 0


def test_compare_accepts_dicts():
    data = _flat_data()
    configs = [
        {"name": "a", "overrides": {}},
        {"name": "b", "overrides": {"X": 1}},
    ]
    result = bt_compare.compare(data, configs, warmup_bars=50)
    assert len(result.results) == 2
    assert result.configs[0].name == "a"
    assert result.configs[1].name == "b"


def test_compare_passes_overrides_to_backtest():
    """Проверка что overrides из Config реально достигают backtest."""
    data = _flat_data()
    cfg = bt_compare.Config(name="x",
                            overrides={"MIN_CONFIDENCE_FOR_TRADE": 88})
    result = bt_compare.compare(data, [cfg], warmup_bars=50)
    # config_overrides сохраняется в результате
    assert result.results[0].config_overrides == {"MIN_CONFIDENCE_FOR_TRADE": 88}


# ─── param_sweep ───────────────────────────────────────────────────────────


def test_param_sweep_cartesian_product():
    data = _flat_data()
    grid = {"A": [1, 2], "B": [10, 20, 30]}
    result = bt_compare.param_sweep(data, grid, warmup_bars=50)
    # 2 × 3 = 6 комбинаций
    assert len(result.configs) == 6
    # Все имена уникальны
    names = [c.name for c in result.configs]
    assert len(set(names)) == 6
    # Каждое имя содержит оба ключа
    for name in names:
        assert "A=" in name and "B=" in name


def test_param_sweep_empty_grid():
    data = _flat_data()
    result = bt_compare.param_sweep(data, {})
    assert result.configs == []
    assert result.results == []


def test_param_sweep_single_param():
    data = _flat_data()
    grid = {"MIN_CONFIDENCE_FOR_TRADE": [60, 65, 70]}
    result = bt_compare.param_sweep(data, grid, warmup_bars=50)
    assert len(result.configs) == 3
    vals = sorted(c.overrides["MIN_CONFIDENCE_FOR_TRADE"]
                  for c in result.configs)
    assert vals == [60, 65, 70]


def test_param_sweep_with_baseline():
    """Baseline настройки применяются к КАЖДОМУ конфигу sweep'а."""
    data = _flat_data()
    grid = {"A": [1, 2]}
    baseline = {"BASE_KEY": 999}
    result = bt_compare.param_sweep(data, grid, baseline=baseline,
                                    warmup_bars=50)
    for cfg in result.configs:
        assert cfg.overrides.get("BASE_KEY") == 999


def test_param_sweep_baseline_doesnt_leak_to_others():
    """Изменение baseline в одной итерации не должно влиять на другие."""
    data = _flat_data()
    grid = {"A": [1, 2]}
    baseline = {"X": 100}
    result = bt_compare.param_sweep(data, grid, baseline=baseline,
                                    warmup_bars=50)
    # Оба конфига имеют свои отдельные dicts
    assert result.configs[0].overrides is not result.configs[1].overrides
    assert result.configs[0].overrides["X"] == 100
    assert result.configs[1].overrides["X"] == 100


# ─── format_comparison ────────────────────────────────────────────────────


def test_format_comparison_empty():
    res = bt_compare.ComparisonResult(symbol="X", days=1, configs=[], results=[])
    assert bt_compare.format_comparison(res) == "(no configs)"


def test_format_comparison_includes_all_configs():
    data = _flat_data()
    configs = [
        bt_compare.Config(name="cfg_a"),
        bt_compare.Config(name="cfg_b", overrides={"X": 5}),
    ]
    result = bt_compare.compare(data, configs, warmup_bars=50)
    out = bt_compare.format_comparison(result)
    assert "cfg_a" in out
    assert "cfg_b" in out
    assert "WinR" in out
    assert "Sharpe" in out


def test_format_comparison_truncates_long_names():
    res = bt_compare.ComparisonResult(
        symbol="X", days=1,
        configs=[bt_compare.Config(name="very_long_config_name_" * 5)],
        results=[bt_compare.backtest.BacktestResult(symbol="X", days=1)],
    )
    out = bt_compare.format_comparison(res, max_name_len=20)
    # Имя обрезано
    long_name = "very_long_config_name_" * 5
    assert long_name not in out
    # Префикс должен быть
    assert long_name[:15] in out


# ─── _parse_value ─────────────────────────────────────────────────────────


def test_parse_value_int():
    assert bt_compare._parse_value("42") == 42


def test_parse_value_float():
    assert bt_compare._parse_value("3.14") == 3.14


def test_parse_value_bool():
    assert bt_compare._parse_value("true") is True
    assert bt_compare._parse_value("FALSE") is False


def test_parse_value_string_fallback():
    assert bt_compare._parse_value("conservative") == "conservative"


# ─── _parse_configs ───────────────────────────────────────────────────────


def test_parse_configs_single():
    out = bt_compare._parse_configs("baseline:{}")
    assert len(out) == 1
    assert out[0].name == "baseline"
    assert out[0].overrides == {}


def test_parse_configs_multiple():
    out = bt_compare._parse_configs(
        "baseline:{};strict:{MIN_CONFIDENCE_FOR_TRADE=75,KILLZONE_GATE_ENABLED=false}"
    )
    assert len(out) == 2
    assert out[0].name == "baseline"
    assert out[1].name == "strict"
    assert out[1].overrides["MIN_CONFIDENCE_FOR_TRADE"] == 75
    assert out[1].overrides["KILLZONE_GATE_ENABLED"] is False


def test_parse_configs_no_overrides_section():
    out = bt_compare._parse_configs("just_name")
    assert len(out) == 1
    assert out[0].name == "just_name"
    assert out[0].overrides == {}


# ─── _parse_grid ──────────────────────────────────────────────────────────


def test_parse_grid_single_key():
    grid = bt_compare._parse_grid("MIN_CONFIDENCE_FOR_TRADE=60,65,70")
    assert grid == {"MIN_CONFIDENCE_FOR_TRADE": [60, 65, 70]}


def test_parse_grid_multi_key():
    grid = bt_compare._parse_grid(
        "MIN_CONFIDENCE_FOR_TRADE=60,70 REGIME_ALIGN_BONUS=0,4,8"
    )
    assert grid == {
        "MIN_CONFIDENCE_FOR_TRADE": [60, 70],
        "REGIME_ALIGN_BONUS":       [0, 4, 8],
    }


def test_parse_grid_mixed_types():
    grid = bt_compare._parse_grid("X=1.5,2.5 Y=true,false")
    assert grid["X"] == [1.5, 2.5]
    assert grid["Y"] == [True, False]


def test_parse_grid_empty():
    assert bt_compare._parse_grid("") == {}
