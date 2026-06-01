"""
bt_walkforward.py — walk-forward analysis (Этап 13 фаза 5/5).

Защита от overfitting: разбиваем исторические данные на rolling windows,
для каждого либо просто прогоняем backtest (stability check), либо
оптимизируем параметры на train-части и тестируем на test-части.

Два режима:

1. walk_forward(data, config_overrides, n_windows)
   Sequential split → backtest на каждом окне с одинаковыми настройками.
   Показывает «стабильна ли стратегия» (winrate растёт/держится/падает по
   окнам).

2. walk_forward_optimize(data, grid, n_windows, train_test_ratio)
   В каждом окне: split train/test → param_sweep на train → лучший
   конфиг прогоняется на test. Out-of-sample результаты надёжнее.

CLI:
  python -m bt_walkforward BTC 30 --windows 4
  python -m bt_walkforward BTC 60 --windows 6 \
    --optimize "MIN_CONFIDENCE_FOR_TRADE=60,65,70 REGIME_ALIGN_BONUS=0,4,8"
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Callable

import backtest
import bt_compare
import bt_data

__all__ = [
    "WindowResult",
    "WalkForwardResult",
    "walk_forward",
    "walk_forward_optimize",
    "format_walkforward",
    "slice_data_window",
]


@dataclass
class WindowResult:
    """Результат одного окна walk-forward."""
    window_idx:        int
    start_idx:         int      # начало 5m среза (включительно)
    end_idx:           int      # конец 5m среза (включительно)
    bars:              int
    best_config:       dict | None = None    # для optimize-режима — лучший конфиг
    train_stats:       dict | None = None    # метрики train-фазы (если optimize)
    test_stats:        dict | None = None    # метрики test-фазы / просто backtest
    test_result:       backtest.BacktestResult | None = None


@dataclass
class WalkForwardResult:
    symbol:  str
    days:    int
    mode:    str           # "stability" | "optimize"
    windows: list = field(default_factory=list)   # list[WindowResult]
    grid:    dict | None = None  # для optimize режима


# ─── Helpers ───────────────────────────────────────────────────────────────


def slice_data_window(data: dict, start_idx: int, end_idx: int,
                      *, tf_primary: str = "5") -> dict:
    """
    Возвращает копию data dict, но с klines обрезанными по [start_idx, end_idx]
    в primary TF. Klines других TF фильтруются по ts (slice до того же
    временного диапазона).
    Funding/OI тоже фильтруются по ts.
    """
    primary = data["klines"].get(tf_primary) or []
    if not primary or start_idx >= len(primary):
        return {"symbol": data.get("symbol", "?"), "days": data.get("days", 0),
                "klines": {}, "funding": [], "oi": []}

    start_ts = primary[start_idx]["ts"]
    end_idx_clamped = min(end_idx, len(primary) - 1)
    end_ts = primary[end_idx_clamped]["ts"]

    sliced_klines = {}
    for tf, kl in data["klines"].items():
        sliced_klines[tf] = [k for k in kl if start_ts <= k["ts"] <= end_ts]

    return {
        "symbol":  data.get("symbol", "?"),
        "days":    data.get("days", 0),
        "klines":  sliced_klines,
        "funding": [f for f in (data.get("funding") or [])
                    if start_ts <= f["ts"] <= end_ts],
        "oi":      [o for o in (data.get("oi") or [])
                    if start_ts <= o["ts"] <= end_ts],
    }


def _window_bounds(total_bars: int, n_windows: int) -> list[tuple[int, int]]:
    """
    Sequential split на n_windows кусков примерно равной длины.
    Возвращает list[(start_idx, end_idx)].
    """
    if n_windows <= 0 or total_bars <= 0:
        return []
    n_windows = min(n_windows, total_bars)
    window_size = total_bars // n_windows
    out = []
    for w in range(n_windows):
        start = w * window_size
        end = (w + 1) * window_size - 1 if w < n_windows - 1 else total_bars - 1
        out.append((start, end))
    return out


# ─── Mode 1: stability ────────────────────────────────────────────────────


def walk_forward(
    data: dict,
    *,
    n_windows: int = 4,
    config_overrides: dict | None = None,
    warmup_bars: int = backtest.DEFAULT_WARMUP_BARS,
    expiry_bars: int = backtest.DEFAULT_EXPIRY_BARS,
    cooldown_bars: int = backtest.DEFAULT_COOLDOWN_BARS,
    default_conf_score: int = backtest.DEFAULT_CONF_SCORE,
    progress: Callable[[str], None] | None = None,
) -> WalkForwardResult:
    """
    Stability mode: data разбивается на n_windows последовательных кусков,
    в каждом — backtest с одинаковыми config_overrides. Возвращает per-window
    результаты — видно, держится ли winrate / Sharpe по разным периодам.
    """
    primary = data["klines"].get("5") or []
    bounds = _window_bounds(len(primary), n_windows)
    windows: list[WindowResult] = []

    for w_idx, (start, end) in enumerate(bounds):
        if progress:
            progress(f"window {w_idx + 1}/{len(bounds)}: bars {start}-{end}")
        sliced = slice_data_window(data, start, end)
        res = backtest.run_backtest(
            sliced,
            warmup_bars=warmup_bars,
            expiry_bars=expiry_bars,
            cooldown_bars=cooldown_bars,
            default_conf_score=default_conf_score,
            config_overrides=config_overrides,
        )
        windows.append(WindowResult(
            window_idx=w_idx,
            start_idx=start,
            end_idx=end,
            bars=end - start + 1,
            best_config=config_overrides,
            test_stats=res.stats,
            test_result=res,
        ))

    return WalkForwardResult(
        symbol=data.get("symbol", "?"),
        days=data.get("days", 0),
        mode="stability",
        windows=windows,
    )


# ─── Mode 2: train/test optimize ───────────────────────────────────────────


def _best_config_by_metric(comparison: bt_compare.ComparisonResult,
                           metric: str = "avg_r") -> tuple[dict, dict]:
    """
    Выбирает лучший конфиг из ComparisonResult по metric (avg_r / win_rate /
    pf / sharpe). Возвращает (overrides_dict, stats_dict).
    """
    if not comparison.results:
        return ({}, {})
    best_idx = 0
    best_val = -float("inf")
    for i, res in enumerate(comparison.results):
        s = res.stats or {}
        if metric in ("avg_r", "win_rate"):
            v = s.get(metric, -float("inf"))
        else:
            r = s.get("risk", {}) or {}
            v = r.get(metric, -float("inf"))
            if v == "∞":
                v = float("inf")
        if not isinstance(v, (int, float)):
            v = -float("inf")
        if v > best_val:
            best_val = v
            best_idx = i
    best_cfg = comparison.configs[best_idx]
    return (best_cfg.overrides or {}, comparison.results[best_idx].stats)


def walk_forward_optimize(
    data: dict,
    grid: dict,
    *,
    n_windows: int = 4,
    train_test_ratio: float = 0.7,
    metric: str = "avg_r",
    warmup_bars: int = backtest.DEFAULT_WARMUP_BARS,
    expiry_bars: int = backtest.DEFAULT_EXPIRY_BARS,
    cooldown_bars: int = backtest.DEFAULT_COOLDOWN_BARS,
    default_conf_score: int = backtest.DEFAULT_CONF_SCORE,
    progress: Callable[[str], None] | None = None,
) -> WalkForwardResult:
    """
    Optimize mode: в каждом окне делим на train/test, param_sweep на train,
    лучший конфиг → backtest на test. Out-of-sample результаты.

    train_test_ratio: 0.7 = 70% train, 30% test
    metric: критерий выбора best — 'avg_r', 'win_rate', 'profit_factor',
            'sharpe_r', 'sortino_r'
    """
    primary = data["klines"].get("5") or []
    bounds = _window_bounds(len(primary), n_windows)
    windows: list[WindowResult] = []

    for w_idx, (w_start, w_end) in enumerate(bounds):
        w_bars = w_end - w_start + 1
        train_bars = int(w_bars * train_test_ratio)
        train_end = w_start + train_bars - 1
        test_start = train_end + 1

        if test_start > w_end:
            # Окно слишком короткое для split
            continue

        if progress:
            progress(f"window {w_idx + 1}/{len(bounds)}: "
                     f"train [{w_start}, {train_end}] "
                     f"test [{test_start}, {w_end}]")

        train_data = slice_data_window(data, w_start, train_end)
        test_data  = slice_data_window(data, test_start, w_end)

        train_cmp = bt_compare.param_sweep(
            train_data, grid,
            warmup_bars=warmup_bars, expiry_bars=expiry_bars,
            cooldown_bars=cooldown_bars,
            default_conf_score=default_conf_score,
        )
        best_overrides, train_stats = _best_config_by_metric(train_cmp, metric)

        test_res = backtest.run_backtest(
            test_data,
            warmup_bars=warmup_bars, expiry_bars=expiry_bars,
            cooldown_bars=cooldown_bars,
            default_conf_score=default_conf_score,
            config_overrides=best_overrides or None,
        )

        windows.append(WindowResult(
            window_idx=w_idx,
            start_idx=w_start,
            end_idx=w_end,
            bars=w_bars,
            best_config=best_overrides,
            train_stats=train_stats,
            test_stats=test_res.stats,
            test_result=test_res,
        ))

    return WalkForwardResult(
        symbol=data.get("symbol", "?"),
        days=data.get("days", 0),
        mode="optimize",
        windows=windows,
        grid=grid,
    )


# ─── Formatting ────────────────────────────────────────────────────────────


def format_walkforward(result: WalkForwardResult) -> str:
    lines = [
        f"=== Walk-forward: {result.symbol} ({result.days}d, "
        f"{result.mode}, {len(result.windows)} windows) ==="
    ]
    if result.mode == "optimize":
        lines.append(f"Grid: {result.grid}")

    header = f"{'Win':<4} {'Bars':>5} {'Trades':>7} {'WinR%':>6} {'AvgR':>6} {'PF':>6} {'Sharpe':>7}"
    if result.mode == "optimize":
        header += "   Best Config"
    lines.append(header)
    lines.append("-" * len(header))

    for w in result.windows:
        s = w.test_stats or {}
        r = s.get("risk", {}) or {}
        line = (f"{w.window_idx:<4} {w.bars:>5} "
                f"{s.get('total', 0):>7} "
                f"{s.get('win_rate', 0):>6} "
                f"{s.get('avg_r', 0):>+6.2f} "
                f"{r.get('profit_factor', 0):>6} "
                f"{r.get('sharpe_r', 0):>+7.2f}")
        if result.mode == "optimize" and w.best_config:
            line += "   " + ",".join(f"{k}={v}" for k, v in w.best_config.items())
        lines.append(line)

    # Aggregate summary
    if result.windows:
        win_rates = [w.test_stats.get("win_rate", 0) for w in result.windows
                     if w.test_stats]
        avg_rs    = [w.test_stats.get("avg_r", 0) for w in result.windows
                     if w.test_stats]
        if win_rates:
            lines.append("")
            lines.append(f"Avg WinR%: {sum(win_rates) / len(win_rates):.1f} · "
                         f"Avg AvgR: {sum(avg_rs) / len(avg_rs):+.2f}")
            lines.append(f"WinR% range: {min(win_rates):.1f} → {max(win_rates):.1f}")

    return "\n".join(lines)


# ─── CLI ──────────────────────────────────────────────────────────────────


def _cli() -> int:
    p = argparse.ArgumentParser(
        prog="bt_walkforward",
        description="Walk-forward анализ стратегии (stability / optimize)",
    )
    p.add_argument("symbol")
    p.add_argument("days", type=int)
    p.add_argument("--tfs", default="5,15,60,240,D")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--no-funding", action="store_true")
    p.add_argument("--no-oi", action="store_true")
    p.add_argument("--windows", type=int, default=4)
    p.add_argument("--warmup", type=int, default=backtest.DEFAULT_WARMUP_BARS)
    p.add_argument("--expiry", type=int, default=backtest.DEFAULT_EXPIRY_BARS)
    p.add_argument("--cooldown", type=int, default=backtest.DEFAULT_COOLDOWN_BARS)
    p.add_argument("--conf", type=int, default=backtest.DEFAULT_CONF_SCORE)
    p.add_argument("--optimize", default=None,
                   help="Activates optimize mode. Grid: KEY=v1,v2 KEY2=v3,v4")
    p.add_argument("--ratio", type=float, default=0.7,
                   help="Train/test ratio (default 0.7)")
    p.add_argument("--metric", default="avg_r",
                   choices=["avg_r", "win_rate", "profit_factor",
                            "sharpe_r", "sortino_r"])
    p.add_argument("--config", default=None,
                   help="Stability mode: общие overrides KEY=VAL,KEY2=VAL2")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    print(f"Fetching {args.symbol} {args.days}d ...", flush=True)
    data = bt_data.fetch_all(
        args.symbol, args.days,
        tfs=[t.strip() for t in args.tfs.split(",")],
        fetch_funding_data=not args.no_funding,
        fetch_oi_data=not args.no_oi,
        cache=not args.no_cache,
    )

    def _log(msg: str) -> None:
        print(f"[walk-forward] {msg}", flush=True)

    if args.optimize:
        grid = bt_compare._parse_grid(args.optimize)
        result = walk_forward_optimize(
            data, grid,
            n_windows=args.windows, train_test_ratio=args.ratio,
            metric=args.metric,
            warmup_bars=args.warmup, expiry_bars=args.expiry,
            cooldown_bars=args.cooldown,
            default_conf_score=args.conf,
            progress=_log,
        )
    else:
        overrides = None
        if args.config:
            overrides = {}
            for pair in args.config.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    overrides[k.strip()] = bt_compare._parse_value(v)
        result = walk_forward(
            data,
            n_windows=args.windows,
            config_overrides=overrides,
            warmup_bars=args.warmup, expiry_bars=args.expiry,
            cooldown_bars=args.cooldown,
            default_conf_score=args.conf,
            progress=_log,
        )

    if args.json:
        payload = {
            "symbol":  result.symbol,
            "days":    result.days,
            "mode":    result.mode,
            "grid":    result.grid,
            "windows": [
                {
                    "window_idx":  w.window_idx,
                    "start_idx":   w.start_idx,
                    "end_idx":     w.end_idx,
                    "bars":        w.bars,
                    "best_config": w.best_config,
                    "train_stats": w.train_stats,
                    "test_stats":  w.test_stats,
                }
                for w in result.windows
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print()
        print(format_walkforward(result))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
