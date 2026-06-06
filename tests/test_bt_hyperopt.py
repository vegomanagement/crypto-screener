"""
Тесты bt_hyperopt.py — Optuna-based hyperparameter optimization.

Pure-helper тесты (search-space, metrics, formatting) запускаются без optuna.
Интеграционные тесты с optuna skip'аются если модуль не установлен.
"""

from __future__ import annotations

import json
import os

import pytest

import bt_hyperopt as bh


# ─── extract_metric ───────────────────────────────────────────────────────


def test_extract_metric_avg_r():
    assert bh.extract_metric({"avg_r": 1.5}, "avg_r") == 1.5


def test_extract_metric_avg_r_net():
    assert bh.extract_metric({"avg_r_net": 1.3}, "avg_r_net") == 1.3


def test_extract_metric_win_rate():
    assert bh.extract_metric({"win_rate": 42.0}, "win_rate") == 42.0


def test_extract_metric_profit_factor_from_risk_block():
    assert bh.extract_metric({"risk": {"profit_factor": 1.62}},
                             "profit_factor") == 1.62


def test_extract_metric_sharpe_from_risk():
    assert bh.extract_metric({"risk": {"sharpe_r": 0.5}}, "sharpe_r") == 0.5


def test_extract_metric_infinity_passthrough():
    assert bh.extract_metric({"risk": {"profit_factor": "∞"}},
                             "profit_factor") == float("inf")


def test_extract_metric_missing_returns_neg_inf():
    assert bh.extract_metric({}, "profit_factor") == float("-inf")
    assert bh.extract_metric({"avg_r": None}, "avg_r") == float("-inf")


def test_extract_metric_expectancy_falls_back_to_avg_r_net():
    assert bh.extract_metric({"avg_r": 1.0, "avg_r_net": 0.9},
                             "expectancy") == 0.9


def test_extract_metric_expectancy_falls_back_to_avg_r():
    assert bh.extract_metric({"avg_r": 1.0}, "expectancy") == 1.0


def test_extract_metric_invalid_type():
    assert bh.extract_metric({"avg_r": "garbage"}, "avg_r") == float("-inf")


# ─── build_search_space ───────────────────────────────────────────────────


def test_default_search_space_has_expected_keys():
    space = bh.DEFAULT_SEARCH_SPACE
    assert "MIN_CONFIDENCE_FOR_TRADE" in space
    assert "KILLZONE_GATE_ENABLED" in space
    assert "HTF_BIAS_GATE_ENABLED" in space
    assert space["KILLZONE_GATE_ENABLED"] == ("bool",)
    assert space["MIN_CONFIDENCE_FOR_TRADE"][0] == "int"


def test_default_search_space_includes_tp_sl_multipliers():
    """TP/SL multipliers критичны для решения negative avgR_net — должны
    быть в default search-space."""
    space = bh.DEFAULT_SEARCH_SPACE
    for key in ("ATR_SL_DIST", "ATR_TP1_DIST", "ATR_TP2_DIST", "ATR_TP3_DIST"):
        assert key in space, f"{key} missing from DEFAULT_SEARCH_SPACE"
        assert space[key][0] == "float", f"{key} should be float-search"
    # TP1 max < TP2 max < TP3 max — иерархия типичных значений
    assert space["ATR_TP1_DIST"][2] <= space["ATR_TP2_DIST"][2]
    assert space["ATR_TP2_DIST"][2] <= space["ATR_TP3_DIST"][2]
    # SL range разумный (не слишком широкий)
    assert space["ATR_SL_DIST"][1] >= 0.5
    assert space["ATR_SL_DIST"][2] <= 3.0


def test_build_search_space_no_overrides_returns_default_copy():
    s = bh.build_search_space()
    assert s == bh.DEFAULT_SEARCH_SPACE
    # должен быть копией, не тем же объектом
    assert s is not bh.DEFAULT_SEARCH_SPACE


def test_build_search_space_with_overrides_merges():
    custom = {"MIN_CONFIDENCE_FOR_TRADE": ("int", 70, 90),
              "NEW_PARAM": ("float", 0.0, 1.0)}
    s = bh.build_search_space(custom)
    assert s["MIN_CONFIDENCE_FOR_TRADE"] == ("int", 70, 90)
    assert s["NEW_PARAM"] == ("float", 0.0, 1.0)
    # дефолтные ключи сохраняются
    assert "KILLZONE_GATE_ENABLED" in s


def test_build_search_space_preserves_defaults_dict():
    """Мутация результата не должна затрагивать DEFAULT_SEARCH_SPACE."""
    before = dict(bh.DEFAULT_SEARCH_SPACE)
    s = bh.build_search_space({"X": ("int", 0, 1)})
    s.pop("MIN_CONFIDENCE_FOR_TRADE", None)
    assert bh.DEFAULT_SEARCH_SPACE == before


# ─── _sample_params (без optuna): mock trial ──────────────────────────────


class _MockTrial:
    """Мок Optuna-trial: возвращает middle-of-range или [0] категорию."""
    def __init__(self):
        self.calls = []
        self.number = 0

    def suggest_int(self, key, low, high):
        self.calls.append(("int", key, low, high))
        return (low + high) // 2

    def suggest_float(self, key, low, high):
        self.calls.append(("float", key, low, high))
        return (low + high) / 2

    def suggest_categorical(self, key, choices):
        self.calls.append(("cat", key, tuple(choices)))
        return choices[0]


def test_sample_params_int():
    space = {"MIN_CONFIDENCE_FOR_TRADE": ("int", 60, 80)}
    trial = _MockTrial()
    p = bh._sample_params(trial, space)
    assert p["MIN_CONFIDENCE_FOR_TRADE"] == 70
    assert trial.calls[0] == ("int", "MIN_CONFIDENCE_FOR_TRADE", 60, 80)


def test_sample_params_float():
    space = {"X": ("float", 0.0, 1.0)}
    p = bh._sample_params(_MockTrial(), space)
    assert p["X"] == 0.5


def test_sample_params_bool_categorical():
    space = {"FLAG": ("bool",)}
    trial = _MockTrial()
    p = bh._sample_params(trial, space)
    assert p["FLAG"] is True
    assert trial.calls[0] == ("cat", "FLAG", (True, False))


def test_sample_params_cat_list():
    space = {"MODE": ("cat", ["a", "b", "c"])}
    p = bh._sample_params(_MockTrial(), space)
    assert p["MODE"] == "a"


def test_sample_params_unknown_kind_raises():
    space = {"X": ("garbage", 1, 2)}
    with pytest.raises(ValueError):
        bh._sample_params(_MockTrial(), space)


def test_sample_params_skips_empty_spec():
    space = {"X": (), "Y": ("int", 1, 10)}
    p = bh._sample_params(_MockTrial(), space)
    assert "X" not in p
    assert p["Y"] == 5


# ─── format_hyperopt ──────────────────────────────────────────────────────


def _trial(idx, params, val, n=20, penalized=False):
    return bh.HyperoptTrial(
        trial_idx=idx, params=params, metric="profit_factor",
        metric_value=val, stats={}, n_closed=n, penalized=penalized,
    )


def test_format_hyperopt_no_valid_trials():
    r = bh.HyperoptResult(
        symbol="X", days=30, metric="profit_factor",
        n_trials=10, min_trades=10,
        trials=[_trial(0, {}, bh.TRIAL_PENALTY_VALUE, n=2, penalized=True)],
    )
    out = bh.format_hyperopt(r)
    assert "Нет валидных trials" in out


def test_format_hyperopt_basic_output():
    trials = [
        _trial(0, {"MIN_CONFIDENCE_FOR_TRADE": 65}, 1.62),
        _trial(1, {"MIN_CONFIDENCE_FOR_TRADE": 70}, 1.85),
        _trial(2, {"MIN_CONFIDENCE_FOR_TRADE": 75}, 1.30),
    ]
    r = bh.HyperoptResult(
        symbol="BTC", days=30, metric="profit_factor",
        n_trials=3, min_trades=10,
        trials=trials,
        best_params={"MIN_CONFIDENCE_FOR_TRADE": 70},
        best_value=1.85,
        best_stats={"closed": 50, "win_rate": 30.0, "avg_r": 0.5,
                    "avg_r_net": 0.42,
                    "risk": {"profit_factor": 1.85, "max_drawdown_r": -5.0}},
    )
    out = bh.format_hyperopt(r)
    assert "Hyperopt: BTC" in out
    assert "Best profit_factor" in out
    assert "MIN_CONFIDENCE_FOR_TRADE" in out
    assert "Top-3 trials" in out
    assert "closed=50" in out


def test_format_hyperopt_shows_penalized_count():
    trials = [
        _trial(0, {"X": 1}, 1.5, n=20),
        _trial(1, {"X": 2}, bh.TRIAL_PENALTY_VALUE, n=2, penalized=True),
        _trial(2, {"X": 3}, bh.TRIAL_PENALTY_VALUE, n=1, penalized=True),
    ]
    r = bh.HyperoptResult(
        symbol="X", days=7, metric="profit_factor",
        n_trials=3, min_trades=10, trials=trials,
        best_params={"X": 1}, best_value=1.5, best_stats={},
    )
    out = bh.format_hyperopt(r)
    assert "[penalty]" in out
    assert "2/3" in out


def test_format_hyperopt_with_walkforward():
    r = bh.HyperoptResult(
        symbol="X", days=60, metric="profit_factor",
        n_trials=10, min_trades=10,
        trials=[_trial(0, {"X": 1}, 1.5)],
        best_params={"X": 1}, best_value=1.5, best_stats={},
        walkforward={
            "n_windows": 2, "n_valid_oos": 2, "mean_oos": 1.2,
            "windows": [
                {"window_idx": 0, "train_metric": 1.8, "oos_value": 1.3,
                 "test_stats": {"closed": 25}},
                {"window_idx": 1, "train_metric": 1.6, "oos_value": 1.1,
                 "test_stats": {"closed": 18}},
            ],
        },
    )
    out = bh.format_hyperopt(r)
    assert "Walk-forward OOS validation" in out
    assert "Windows: 2" in out
    assert "mean OOS" in out
    assert "w0:" in out and "w1:" in out


def test_fmt_value_handles_inf_and_none():
    assert bh._fmt_value(None) == "—"
    assert bh._fmt_value(float("inf")) == "∞"
    assert bh._fmt_value(float("-inf")) == "-∞"
    assert bh._fmt_value(True) == "Y"
    assert bh._fmt_value(False) == "N"


# ─── _require_optuna ──────────────────────────────────────────────────────


def test_require_optuna_either_imports_or_raises_clear_error():
    """Если optuna установлен — возвращает модуль. Если нет — ImportError
    с понятным сообщением."""
    try:
        m = bh._require_optuna()
        # Optuna установлен — должен быть модуль optuna
        assert m.__name__ == "optuna"
    except ImportError as e:
        assert "optuna не установлен" in str(e)


# ─── hyperopt: интеграция через mock backtest (требует optuna) ────────────


def test_hyperopt_invalid_metric_raises():
    """ValueError ДО попытки импорта optuna — works without optuna."""
    with pytest.raises(ValueError, match="metric"):
        bh.hyperopt({"symbol": "X", "days": 1, "klines": {"5": []}},
                    metric="bogus", n_trials=1)


def test_hyperopt_with_mock_backtest(monkeypatch):
    """
    Интеграционный: подменяем backtest.run_backtest на мок и проверяем
    что hyperopt корректно собирает trials и выбирает best.
    """
    pytest.importorskip("optuna")

    import backtest as bt

    call_idx = {"n": 0}

    def fake_run(data, **kwargs):
        """Мок: возвращает PF, зависящий от MIN_CONFIDENCE — peak в 70."""
        overrides = kwargs.get("config_overrides") or {}
        mc = overrides.get("MIN_CONFIDENCE_FOR_TRADE", 65)
        # PF = parabola с пиком в 70
        pf = 2.0 - 0.01 * (mc - 70) ** 2
        call_idx["n"] += 1
        return bt.BacktestResult(
            symbol="X", days=1, trades=[], stats={
                "closed": 30, "win_rate": 35.0, "avg_r": 0.5,
                "avg_r_net": 0.4,
                "risk": {"profit_factor": pf, "max_drawdown_r": -2.0,
                         "sharpe_r": 0.3, "sortino_r": 0.5},
            },
        )

    monkeypatch.setattr(bt, "run_backtest", fake_run)

    # минимальный space — только MIN_CONFIDENCE
    space = {"MIN_CONFIDENCE_FOR_TRADE": ("int", 60, 80)}
    result = bh.hyperopt(
        {"symbol": "X", "days": 1, "klines": {"5": []}},
        n_trials=20, metric="profit_factor", search_space=space,
        seed=42, min_trades=10,
    )

    assert call_idx["n"] == 20
    assert result.n_trials == 20
    assert len(result.trials) == 20
    # Best должен быть близок к 70
    assert result.best_params is not None
    best_mc = result.best_params["MIN_CONFIDENCE_FOR_TRADE"]
    assert 65 <= best_mc <= 75


def test_hyperopt_penalizes_low_trade_count(monkeypatch):
    pytest.importorskip("optuna")

    import backtest as bt

    def fake_run(data, **kwargs):
        # Всегда возвращаем только 2 closed trades
        return bt.BacktestResult(
            symbol="X", days=1, trades=[], stats={
                "closed": 2, "win_rate": 100.0, "avg_r": 5.0,
                "risk": {"profit_factor": "∞"},
            },
        )

    monkeypatch.setattr(bt, "run_backtest", fake_run)
    result = bh.hyperopt(
        {"symbol": "X", "days": 1, "klines": {"5": []}},
        n_trials=5, metric="profit_factor",
        search_space={"X": ("int", 0, 10)},
        min_trades=10, seed=42,
    )

    # Все trials penalized → best_params None
    assert all(t.penalized for t in result.trials)
    assert result.best_params is None
    assert result.best_value is None


def test_hyperopt_caps_infinity_metric(monkeypatch):
    """PF=∞ обрабатывается без падения; cap на 1e6."""
    pytest.importorskip("optuna")

    import backtest as bt

    def fake_run(data, **kwargs):
        return bt.BacktestResult(
            symbol="X", days=1, trades=[], stats={
                "closed": 50, "win_rate": 80.0, "avg_r": 2.0,
                "risk": {"profit_factor": "∞"},
            },
        )

    monkeypatch.setattr(bt, "run_backtest", fake_run)
    result = bh.hyperopt(
        {"symbol": "X", "days": 1, "klines": {"5": []}},
        n_trials=3, metric="profit_factor",
        search_space={"X": ("int", 0, 10)},
        min_trades=10, seed=42,
    )

    assert result.best_params is not None
    assert result.best_value == 1e6


# ─── fixed_params ─────────────────────────────────────────────────────────


def test_hyperopt_fixed_params_applied_to_every_trial(monkeypatch):
    """fixed_params попадают в config_overrides каждого backtest-вызова."""
    pytest.importorskip("optuna")
    import backtest as bt

    seen_overrides: list[dict] = []

    def fake_run(data, **kwargs):
        seen_overrides.append(kwargs.get("config_overrides") or {})
        return bt.BacktestResult(
            symbol="X", days=1, trades=[], stats={
                "closed": 30, "win_rate": 35.0, "avg_r": 0.5,
                "risk": {"profit_factor": 1.5},
            },
        )

    monkeypatch.setattr(bt, "run_backtest", fake_run)
    bh.hyperopt(
        {"symbol": "X", "days": 1, "klines": {"5": []}},
        n_trials=5, metric="profit_factor",
        search_space={"MIN_CONFIDENCE_FOR_TRADE": ("int", 60, 80)},
        fixed_params={"KILLZONE_GATE_ENABLED": False,
                      "HTF_BIAS_GATE_ENABLED": False},
        min_trades=10, seed=42,
    )

    assert len(seen_overrides) == 5
    for ovr in seen_overrides:
        assert ovr["KILLZONE_GATE_ENABLED"] is False
        assert ovr["HTF_BIAS_GATE_ENABLED"] is False
        # MIN_CONFIDENCE_FOR_TRADE сэмплируется → должен быть в overrides
        assert "MIN_CONFIDENCE_FOR_TRADE" in ovr


def test_hyperopt_fixed_params_removed_from_search_space(monkeypatch):
    """Параметр, попавший в fixed_params, не сэмплируется Optuna'ой."""
    pytest.importorskip("optuna")
    import backtest as bt

    def fake_run(data, **kwargs):
        return bt.BacktestResult(
            symbol="X", days=1, trades=[], stats={
                "closed": 30, "win_rate": 35.0, "avg_r": 0.5,
                "risk": {"profit_factor": 1.5},
            },
        )

    monkeypatch.setattr(bt, "run_backtest", fake_run)
    result = bh.hyperopt(
        {"symbol": "X", "days": 1, "klines": {"5": []}},
        n_trials=3, metric="profit_factor",
        search_space={"MIN_CONFIDENCE_FOR_TRADE": ("int", 60, 80),
                      "KILLZONE_GATE_ENABLED": ("bool",)},
        fixed_params={"KILLZONE_GATE_ENABLED": True},
        min_trades=10, seed=42,
    )
    # Во всех trials KILLZONE_GATE_ENABLED должен быть True (fixed)
    for t in result.trials:
        assert t.params["KILLZONE_GATE_ENABLED"] is True


# ─── dump_result_json ─────────────────────────────────────────────────────


def test_dump_result_json_roundtrip(tmp_path):
    r = bh.HyperoptResult(
        symbol="BTC", days=30, metric="profit_factor",
        n_trials=2, min_trades=10,
        trials=[
            _trial(0, {"X": 1}, 1.5, n=20),
            _trial(1, {"X": 2}, 1.8, n=22),
        ],
        best_params={"X": 2}, best_value=1.8,
        best_stats={"closed": 22, "win_rate": 50.0},
    )
    path = os.path.join(str(tmp_path), "hopt.json")
    bh.dump_result_json(r, path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data["symbol"] == "BTC"
    assert data["best_params"]["X"] == 2
    assert len(data["trials"]) == 2


# ─── hyperopt_walkforward ─────────────────────────────────────────────────


def test_hyperopt_walkforward_with_mocked_backtest(monkeypatch):
    """End-to-end walk-forward на минимальном fake-data."""
    pytest.importorskip("optuna")

    import backtest as bt

    def fake_run(data, **kwargs):
        overrides = kwargs.get("config_overrides") or {}
        mc = overrides.get("MIN_CONFIDENCE_FOR_TRADE", 65)
        return bt.BacktestResult(
            symbol="X", days=1, trades=[], stats={
                "closed": 30, "win_rate": 35.0, "avg_r": 0.5,
                "risk": {"profit_factor": 1.5 + (mc - 65) * 0.01,
                         "max_drawdown_r": -2.0},
            },
        )

    monkeypatch.setattr(bt, "run_backtest", fake_run)

    # минимальный data с 100 5m баров
    ts0 = 1_780_000_000_000
    klines_5m = [{"ts": ts0 + i * 300_000, "o": 100, "h": 101,
                  "l": 99, "c": 100, "v": 100} for i in range(100)]
    data = {"symbol": "X", "days": 1,
            "klines": {"5": klines_5m}, "funding": [], "oi": []}

    result = bh.hyperopt_walkforward(
        data, n_windows=2, n_trials=5, metric="profit_factor",
        search_space={"MIN_CONFIDENCE_FOR_TRADE": ("int", 60, 80)},
        min_trades=5, seed=42,
    )

    assert result["metric"] == "profit_factor"
    assert result["n_windows"] >= 1
    assert len(result["windows"]) >= 1
    assert result["mean_oos"] is not None
