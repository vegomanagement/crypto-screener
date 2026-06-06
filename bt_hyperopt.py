"""
bt_hyperopt.py — Optuna-based hyperparameter optimization для decision-pipeline.

Цель: автоматически найти лучшие значения decision-констант
(MIN_CONFIDENCE_FOR_TRADE, штрафы veto, флаги гейтов и т.д.) вместо ручного
grid-sweep. Optuna использует TPE (Tree-structured Parzen Estimator) — гораздо
эффективнее brute-force на больших search-space.

Защита от overfitting:
  • Penalty за слишком мало сделок (`min_trades`): trial с n<min_trades
    получает -inf, чтобы Optuna не «выигрывал» нулём сделок.
  • `hyperopt_walkforward` — out-of-sample валидация на rolling окнах.

API:
  • hyperopt(data, n_trials, metric, search_space) → HyperoptResult
  • hyperopt_walkforward(data, n_windows, train_test_ratio) → walkforward dict
  • format_hyperopt(result, top_n) → pretty-print
  • DEFAULT_SEARCH_SPACE — разумный дефолт

CLI:
  python -m bt_hyperopt BTCUSDT 60 --trials 100 --metric pf
  python -m bt_hyperopt BTCUSDT 60 --trials 50 --walkforward --windows 3

Тесты совместимы с отсутствием optuna — интеграционные skip'аются через
pytest.importorskip, чистые helpers (search-space parsing, metrics, formatting)
покрыты без optuna.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Callable

import backtest
import bt_data
import bt_walkforward

__all__ = [
    "HyperoptTrial",
    "HyperoptResult",
    "hyperopt",
    "hyperopt_walkforward",
    "format_hyperopt",
    "extract_metric",
    "build_search_space",
    "DEFAULT_SEARCH_SPACE",
    "DEFAULT_N_TRIALS",
    "DEFAULT_METRIC",
    "DEFAULT_MIN_TRADES",
    "VALID_METRICS",
]

DEFAULT_N_TRIALS    = 50
DEFAULT_METRIC      = "profit_factor"
DEFAULT_MIN_TRADES  = 10
TRIAL_PENALTY_VALUE = -1e6   # значение objective для невалидных trials

# Метрики, доступные для оптимизации. Все «выше = лучше».
VALID_METRICS = (
    "profit_factor",   # PF (net of fees если в stats)
    "sharpe_r",        # Sharpe ratio
    "sortino_r",       # Sortino ratio
    "avg_r",           # средний R на трейд
    "avg_r_net",       # средний R за вычетом fees
    "win_rate",        # winrate %
    "expectancy",      # = win_rate * avg_win + (1-win_rate) * avg_loss
)

# Search space: ключ = имя константы в decision.py, значение =
#   ("int", low, high)   — Optuna suggest_int
#   ("float", low, high) — Optuna suggest_float
#   ("bool",)            — Optuna suggest_categorical([True, False])
#   ("cat", [v1, v2])    — Optuna suggest_categorical
DEFAULT_SEARCH_SPACE: dict = {
    "MIN_CONFIDENCE_FOR_TRADE":   ("int", 55, 80),
    "CONFLUENCE_WAIT_THRESHOLD":  ("int", 45, 70),
    "KILLZONE_GATE_ENABLED":      ("bool",),
    "HTF_BIAS_GATE_ENABLED":      ("bool",),
    "STRUCTURE_GATE_ENABLED":     ("bool",),
    "RSI_VETO_PENALTY":           ("int", 10, 30),
    "MTF_AGAINST_PENALTY":        ("int", 5, 25),
    "FUNDING_VETO_PENALTY":       ("int", 5, 20),
    "MACD_VETO_PENALTY":          ("int", 5, 15),
    "RSI_DIV_VETO_PENALTY":       ("int", 8, 20),
}


@dataclass
class HyperoptTrial:
    """Один trial — результат прогона backtest с конкретным набором params."""
    trial_idx:    int
    params:       dict
    metric:       str
    metric_value: float
    stats:        dict
    n_closed:     int
    penalized:    bool = False     # True если попали в min_trades penalty


@dataclass
class HyperoptResult:
    """Итог hyperopt-сессии."""
    symbol:        str
    days:          int
    metric:        str
    n_trials:      int
    min_trades:    int
    trials:        list = field(default_factory=list)   # list[HyperoptTrial]
    best_params:   dict | None = None
    best_value:    float | None = None
    best_stats:    dict | None = None
    walkforward:   dict | None = None    # out-of-sample валидация (опц.)


# ─── Helpers (без optuna) ─────────────────────────────────────────────────


def build_search_space(overrides: dict | None = None) -> dict:
    """
    Возвращает search-space, перекрывая DEFAULT_SEARCH_SPACE пользовательскими.
    overrides формата {"KEY": ("int", low, high), ...}.
    """
    space = dict(DEFAULT_SEARCH_SPACE)
    if overrides:
        space.update(overrides)
    return space


def extract_metric(stats: dict, metric: str) -> float:
    """
    Извлекает метрику из stats-dict (формат backtest._aggregate_stats).
    Возвращает -inf для отсутствующих / невалидных значений.
    Спец-case 'expectancy' вычисляется из hits/avg_r при отсутствии.
    """
    if not stats:
        return float("-inf")

    if metric in ("avg_r", "avg_r_net", "win_rate"):
        v = stats.get(metric)
    elif metric == "expectancy":
        v = _compute_expectancy(stats)
    else:
        r = stats.get("risk") or {}
        v = r.get(metric)

    if v is None:
        return float("-inf")
    if v == "∞":
        return float("inf")
    if not isinstance(v, (int, float)):
        return float("-inf")
    return float(v)


def _compute_expectancy(stats: dict) -> float | None:
    """
    Expectancy ≈ avg_r на трейд (синоним для удобства). Если нет — None.
    """
    return stats.get("avg_r_net", stats.get("avg_r"))


def _sample_params(trial, search_space: dict) -> dict:
    """
    Сэмплирует один набор параметров из search_space через Optuna trial.
    Поддерживает 'int', 'float', 'bool', 'cat'.
    """
    out: dict = {}
    for key, spec in search_space.items():
        if not spec:
            continue
        kind = spec[0]
        if kind == "int":
            out[key] = trial.suggest_int(key, int(spec[1]), int(spec[2]))
        elif kind == "float":
            out[key] = trial.suggest_float(key, float(spec[1]), float(spec[2]))
        elif kind == "bool":
            out[key] = trial.suggest_categorical(key, [True, False])
        elif kind == "cat":
            out[key] = trial.suggest_categorical(key, list(spec[1]))
        else:
            raise ValueError(f"Unknown search-space spec for {key}: {spec}")
    return out


def _require_optuna():
    """Импортирует optuna или бросает понятный ImportError."""
    try:
        import optuna  # noqa: F401
        return optuna
    except ImportError as e:
        raise ImportError(
            "optuna не установлен. Поставьте: pip install optuna"
        ) from e


# ─── hyperopt: главная функция ────────────────────────────────────────────


def hyperopt(
    data: dict,
    *,
    n_trials:           int = DEFAULT_N_TRIALS,
    metric:             str = DEFAULT_METRIC,
    search_space:       dict | None = None,
    fixed_params:       dict | None = None,
    seed:               int | None = 42,
    min_trades:         int = DEFAULT_MIN_TRADES,
    warmup_bars:        int = backtest.DEFAULT_WARMUP_BARS,
    expiry_bars:        int | None = None,
    cooldown_bars:      int | None = None,
    default_conf_score: int = backtest.DEFAULT_CONF_SCORE,
    taker_fee_pct:      float = backtest.DEFAULT_TAKER_FEE_PCT,
    progress:           Callable[[str], None] | None = None,
) -> HyperoptResult:
    """
    Прогоняет n_trials Optuna-trials через backtest.run_backtest, выбирает
    лучший по metric. Возвращает HyperoptResult с топом trials.

    metric: см. VALID_METRICS
    min_trades: trials с closed < min_trades получают penalty (защита от
                overfitting "0 трейдов = бесконечный PF")
    fixed_params: dict ключей которые НЕ ищутся (берутся как константы во
                  всех trials). Удаляются из search_space. Полезно
                  зафиксировать гипотезу: «оптимизируй штрафы при HTF=False».
    """
    if metric not in VALID_METRICS:
        raise ValueError(
            f"metric={metric!r}, ожидалось одно из {VALID_METRICS}")

    optuna = _require_optuna()

    space = build_search_space(search_space)
    # Убираем fixed_params из space — их Optuna не сэмплирует
    if fixed_params:
        space = {k: v for k, v in space.items() if k not in fixed_params}
    fixed = dict(fixed_params) if fixed_params else {}
    trials_log: list[HyperoptTrial] = []

    def objective(trial) -> float:
        params = _sample_params(trial, space)
        # Накладываем fixed-params: они константны во всех trials
        merged = {**fixed, **params}
        res = backtest.run_backtest(
            data,
            warmup_bars=warmup_bars,
            expiry_bars=expiry_bars,
            cooldown_bars=cooldown_bars,
            default_conf_score=default_conf_score,
            config_overrides=merged,
            taker_fee_pct=taker_fee_pct,
            collect_signals=False,
        )
        closed = (res.stats or {}).get("closed", 0)
        value = extract_metric(res.stats or {}, metric)
        penalized = closed < min_trades
        if penalized or value == float("-inf"):
            stored_value = TRIAL_PENALTY_VALUE
        elif value == float("inf"):
            # ∞ PF — обычно от 0 убытков на 1-2 трейдах. Cap высоким числом.
            stored_value = 1e6
        else:
            stored_value = value

        trials_log.append(HyperoptTrial(
            trial_idx=trial.number,
            params=dict(merged),
            metric=metric,
            metric_value=float(stored_value),
            stats=dict(res.stats or {}),
            n_closed=int(closed),
            penalized=penalized,
        ))
        if progress:
            progress(
                f"trial {trial.number + 1}/{n_trials}: "
                f"closed={closed} {metric}={stored_value:.3f}"
                + (" [penalized]" if penalized else "")
            )
        return stored_value

    sampler = optuna.samplers.TPESampler(seed=seed) if seed is not None else None
    study = optuna.create_study(direction="maximize", sampler=sampler)
    # Suppress optuna's per-trial logger spam — отображаем через progress callback
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study.optimize(objective, n_trials=n_trials)

    # Найти best среди НЕ-penalized
    valid = [t for t in trials_log if not t.penalized
             and t.metric_value > TRIAL_PENALTY_VALUE]
    if valid:
        best = max(valid, key=lambda t: t.metric_value)
        best_params, best_value, best_stats = best.params, best.metric_value, best.stats
    else:
        best_params = best_value = best_stats = None

    return HyperoptResult(
        symbol=data.get("symbol", "?"),
        days=data.get("days", 0),
        metric=metric,
        n_trials=n_trials,
        min_trades=min_trades,
        trials=trials_log,
        best_params=best_params,
        best_value=best_value,
        best_stats=best_stats,
    )


# ─── Walk-forward валидация ──────────────────────────────────────────────


def hyperopt_walkforward(
    data: dict,
    *,
    n_windows:          int = 3,
    train_test_ratio:   float = 0.7,
    n_trials:           int = 30,
    metric:             str = DEFAULT_METRIC,
    search_space:       dict | None = None,
    fixed_params:       dict | None = None,
    seed:               int | None = 42,
    min_trades:         int = DEFAULT_MIN_TRADES,
    warmup_bars:        int = backtest.DEFAULT_WARMUP_BARS,
    expiry_bars:        int | None = None,
    cooldown_bars:      int | None = None,
    default_conf_score: int = backtest.DEFAULT_CONF_SCORE,
    taker_fee_pct:      float = backtest.DEFAULT_TAKER_FEE_PCT,
    progress:           Callable[[str], None] | None = None,
) -> dict:
    """
    Out-of-sample валидация: на каждом окне hyperopt на train-части,
    backtest с найденными params на test-части. Возвращает dict с
    per-window результатами + усреднённую OOS-метрику.

    Защита от overfitting сильнее чем у обычного hyperopt: если стратегия
    «выучила» шум train-окна, она провалится на test.
    """
    primary = data["klines"].get("5") or []
    bounds = bt_walkforward._window_bounds(len(primary), n_windows)

    per_window: list[dict] = []
    oos_values: list[float] = []

    for w_idx, (w_start, w_end) in enumerate(bounds):
        w_bars = w_end - w_start + 1
        train_bars = int(w_bars * train_test_ratio)
        train_end = w_start + train_bars - 1
        test_start = train_end + 1
        if test_start > w_end:
            continue

        if progress:
            progress(
                f"window {w_idx + 1}/{len(bounds)}: "
                f"train [{w_start}, {train_end}] test [{test_start}, {w_end}]"
            )

        train_data = bt_walkforward.slice_data_window(data, w_start, train_end)
        test_data  = bt_walkforward.slice_data_window(data, test_start, w_end)

        train_hopt = hyperopt(
            train_data,
            n_trials=n_trials, metric=metric, search_space=search_space,
            fixed_params=fixed_params,
            seed=seed, min_trades=min_trades, warmup_bars=warmup_bars,
            expiry_bars=expiry_bars, cooldown_bars=cooldown_bars,
            default_conf_score=default_conf_score, taker_fee_pct=taker_fee_pct,
        )

        best_params = train_hopt.best_params or {}
        test_res = backtest.run_backtest(
            test_data,
            warmup_bars=warmup_bars,
            expiry_bars=expiry_bars,
            cooldown_bars=cooldown_bars,
            default_conf_score=default_conf_score,
            config_overrides=best_params or None,
            taker_fee_pct=taker_fee_pct,
            collect_signals=False,
        )
        oos_value = extract_metric(test_res.stats or {}, metric)
        if oos_value not in (float("inf"), float("-inf")):
            oos_values.append(float(oos_value))

        per_window.append({
            "window_idx":  w_idx,
            "train_bars":  train_bars,
            "test_bars":   w_end - test_start + 1,
            "best_params": best_params,
            "train_metric": train_hopt.best_value,
            "test_stats":  test_res.stats,
            "oos_value":   oos_value if oos_value != float("-inf") else None,
        })

    mean_oos = sum(oos_values) / len(oos_values) if oos_values else None
    return {
        "metric":      metric,
        "n_windows":   len(per_window),
        "windows":     per_window,
        "mean_oos":    mean_oos,
        "n_valid_oos": len(oos_values),
    }


# ─── Pretty-print ─────────────────────────────────────────────────────────


def _fmt_value(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "Y" if v else "N"
    if isinstance(v, float):
        if v == float("inf"):
            return "∞"
        if v == float("-inf"):
            return "-∞"
        return f"{v:+.3f}"
    return str(v)


def format_hyperopt(result: HyperoptResult, *, top_n: int = 10) -> str:
    """Pretty-print: best params + top-N trials по metric."""
    lines = [
        f"=== Hyperopt: {result.symbol} ({result.days}d) ===",
        f"Metric: {result.metric} · trials: {result.n_trials} · "
        f"min_trades: {result.min_trades}",
    ]

    if result.best_params is None:
        lines.append("\nНет валидных trials (все попали в penalty).")
        return "\n".join(lines)

    lines.append(f"\nBest {result.metric}: "
                 f"{_fmt_value(result.best_value)}")
    lines.append("Best params:")
    for k, v in sorted(result.best_params.items()):
        lines.append(f"  {k:<32} = {_fmt_value(v)}")

    bs = result.best_stats or {}
    if bs:
        risk = bs.get("risk") or {}
        lines.append(
            f"\nBest config stats: closed={bs.get('closed', 0)} · "
            f"WR={bs.get('win_rate', 0)}% · "
            f"avgR={bs.get('avg_r', 0):+.2f} "
            f"(net {bs.get('avg_r_net', 0):+.2f}) · "
            f"PF={risk.get('profit_factor')} · "
            f"MaxDD={risk.get('max_drawdown_r')}R"
        )

    # Top-N trials
    valid = [t for t in result.trials if not t.penalized]
    valid.sort(key=lambda t: t.metric_value, reverse=True)
    if valid:
        lines.append(f"\nTop-{min(top_n, len(valid))} trials:")
        for t in valid[:top_n]:
            param_str = ", ".join(f"{k}={_fmt_value(v)}"
                                  for k, v in sorted(t.params.items()))
            lines.append(
                f"  #{t.trial_idx:>3} {result.metric}={_fmt_value(t.metric_value)} "
                f"n={t.n_closed} · {param_str[:140]}"
            )

    n_penalized = sum(1 for t in result.trials if t.penalized)
    if n_penalized:
        lines.append(
            f"\n[penalty] {n_penalized}/{len(result.trials)} trials "
            f"с closed<{result.min_trades} — отброшены"
        )

    if result.walkforward:
        wf = result.walkforward
        lines.append("\n=== Walk-forward OOS validation ===")
        lines.append(
            f"Windows: {wf.get('n_windows', 0)} · valid OOS: "
            f"{wf.get('n_valid_oos', 0)} · mean OOS "
            f"{result.metric}: {_fmt_value(wf.get('mean_oos'))}"
        )
        for w in (wf.get("windows") or []):
            ts = w.get("test_stats") or {}
            lines.append(
                f"  w{w['window_idx']}: train={_fmt_value(w.get('train_metric'))} "
                f"→ test={_fmt_value(w.get('oos_value'))} · "
                f"closed={ts.get('closed', 0)}"
            )

    return "\n".join(lines)


# ─── Dump ─────────────────────────────────────────────────────────────────


def dump_result_json(result: HyperoptResult, path: str) -> None:
    """Сохраняет полный HyperoptResult в JSON."""
    payload = asdict(result)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)


# ─── CLI ──────────────────────────────────────────────────────────────────


def _cli() -> int:
    p = argparse.ArgumentParser(
        prog="bt_hyperopt",
        description="Optuna hyperopt для decision-pipeline",
    )
    p.add_argument("symbol")
    p.add_argument("days", type=int)
    p.add_argument("--trials", type=int, default=DEFAULT_N_TRIALS)
    p.add_argument("--metric", default=DEFAULT_METRIC,
                   choices=list(VALID_METRICS))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--min-trades", type=int, default=DEFAULT_MIN_TRADES)
    p.add_argument("--warmup", type=int, default=backtest.DEFAULT_WARMUP_BARS)
    p.add_argument("--taker-fee", type=float,
                   default=backtest.DEFAULT_TAKER_FEE_PCT)
    p.add_argument("--no-funding", action="store_true")
    p.add_argument("--no-oi", action="store_true")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--tfs", default="5,15,60,240,D")
    p.add_argument("--walkforward", action="store_true",
                   help="Запустить walk-forward OOS validation вместо single-shot")
    p.add_argument("--windows", type=int, default=3,
                   help="Окон для walk-forward")
    p.add_argument("--train-ratio", type=float, default=0.7)
    p.add_argument("--dump-json", default=None)
    p.add_argument("--top-n", type=int, default=10)
    args = p.parse_args()

    print(f"Fetching {args.symbol} {args.days}d ...", flush=True)
    data = bt_data.fetch_all(
        args.symbol, args.days,
        tfs=[t.strip() for t in args.tfs.split(",")],
        fetch_funding_data=not args.no_funding,
        fetch_oi_data=not args.no_oi,
        cache=not args.no_cache,
    )

    progress = lambda m: print(f"[hyperopt] {m}", flush=True)  # noqa: E731

    if args.walkforward:
        result = hyperopt(   # baseline run + walkforward attached
            data,
            n_trials=args.trials, metric=args.metric, seed=args.seed,
            min_trades=args.min_trades, warmup_bars=args.warmup,
            taker_fee_pct=args.taker_fee, progress=progress,
        )
        wf = hyperopt_walkforward(
            data, n_windows=args.windows, train_test_ratio=args.train_ratio,
            n_trials=args.trials, metric=args.metric, seed=args.seed,
            min_trades=args.min_trades, warmup_bars=args.warmup,
            taker_fee_pct=args.taker_fee, progress=progress,
        )
        result.walkforward = wf
    else:
        result = hyperopt(
            data,
            n_trials=args.trials, metric=args.metric, seed=args.seed,
            min_trades=args.min_trades, warmup_bars=args.warmup,
            taker_fee_pct=args.taker_fee, progress=progress,
        )

    print()
    print(format_hyperopt(result, top_n=args.top_n))
    if args.dump_json:
        dump_result_json(result, args.dump_json)
        print(f"\n[dump] saved → {args.dump_json}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
