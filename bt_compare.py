"""
bt_compare.py — сравнение бектестов + parameter sweep (Этап 13 фаза 4/5).

Запускает backtest.run_backtest несколько раз с разными config-наборами
и собирает результаты в единую таблицу для сравнения. Используется для:
  • Найти оптимальные веса decision-констант без месяца прод-данных
  • Проверить эффект отдельных гейтов (P3 killzone on/off, MIN_CONFIDENCE
    50 vs 65 vs 75, REGIME bonuses 0% / 50% / 100% etc.)
  • Сравнить две стратегии side-by-side (baseline vs new feature)

API:
  • compare(data, configs) — список конфигов → ComparisonResult
  • param_sweep(data, grid) — cartesian product param values
  • format_comparison(result) — pretty-print таблица

CLI:
  python -m bt_compare BTCUSDT 30 \
    --configs "baseline:{},strict:{MIN_CONFIDENCE_FOR_TRADE:75}"
  python -m bt_compare BTCUSDT 30 \
    --grid "MIN_CONFIDENCE_FOR_TRADE=60,65,70 REGIME_ALIGN_BONUS=0,4,8"
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import dataclass, field

import backtest
import bt_data

__all__ = [
    "Config",
    "ComparisonResult",
    "compare",
    "param_sweep",
    "format_comparison",
]


@dataclass
class Config:
    """Одна конфигурация для прогона backtest."""
    name:      str
    overrides: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"name": self.name, "overrides": dict(self.overrides)}


@dataclass
class ComparisonResult:
    """Результаты сравнения нескольких backtests."""
    symbol:  str
    days:    int
    configs: list[Config]
    results: list[backtest.BacktestResult]


# ─── Comparison ────────────────────────────────────────────────────────────


def compare(
    data: dict,
    configs: list[Config | dict],
    *,
    tf_primary: str = "5",
    warmup_bars: int = backtest.DEFAULT_WARMUP_BARS,
    expiry_bars: int | None = None,
    cooldown_bars: int | None = None,
    default_conf_score: int = backtest.DEFAULT_CONF_SCORE,
) -> ComparisonResult:
    """
    Запускает backtest для каждого Config, возвращает ComparisonResult.
    Принимает Config объекты или dicts формата {"name": ..., "overrides": ...}.
    tf_primary прокидывается в run_backtest (5/15/60/240/D).
    """
    cfg_objs = [_to_config(c) for c in configs]
    results = []
    for cfg in cfg_objs:
        res = backtest.run_backtest(
            data,
            tf_primary=tf_primary,
            warmup_bars=warmup_bars,
            expiry_bars=expiry_bars,
            cooldown_bars=cooldown_bars,
            default_conf_score=default_conf_score,
            config_overrides=cfg.overrides or None,
        )
        results.append(res)
    return ComparisonResult(
        symbol=data.get("symbol", "?"),
        days=data.get("days", 0),
        configs=cfg_objs,
        results=results,
    )


# ─── Parameter sweep ───────────────────────────────────────────────────────


def param_sweep(
    data: dict,
    grid: dict,
    *,
    baseline: dict | None = None,
    warmup_bars: int = backtest.DEFAULT_WARMUP_BARS,
    expiry_bars: int = backtest.DEFAULT_EXPIRY_BARS,
    cooldown_bars: int = backtest.DEFAULT_COOLDOWN_BARS,
    default_conf_score: int = backtest.DEFAULT_CONF_SCORE,
) -> ComparisonResult:
    """
    Cartesian product над grid: {key: [val1, val2, ...]} → каждый комбо
    становится Config'ом и прогоняется.

    baseline (опционально) — общие настройки, поверх которых grid varying.
    Имя конфига генерируется как "key1=val1,key2=val2" для уникальности.
    """
    if not grid:
        return ComparisonResult(symbol=data.get("symbol", "?"),
                                days=data.get("days", 0),
                                configs=[], results=[])

    keys = list(grid.keys())
    value_lists = [grid[k] for k in keys]
    base = dict(baseline or {})

    configs: list[Config] = []
    for combo in itertools.product(*value_lists):
        overrides = dict(base)
        name_parts = []
        for k, v in zip(keys, combo):
            overrides[k] = v
            name_parts.append(f"{k}={v}")
        configs.append(Config(name=",".join(name_parts), overrides=overrides))

    return compare(
        data, configs,
        warmup_bars=warmup_bars, expiry_bars=expiry_bars,
        cooldown_bars=cooldown_bars,
        default_conf_score=default_conf_score,
    )


# ─── Formatting ────────────────────────────────────────────────────────────


_METRIC_COLS = [
    ("trades",   "Trades"),
    ("closed",   "Closed"),
    ("win_rate", "WinR%"),
    ("avg_r",    "AvgR"),
    ("pf",       "PF"),
    ("sharpe",   "Sharpe"),
    ("sortino",  "Sortino"),
    ("max_dd",   "MaxDD"),
    ("best",     "Best"),
    ("worst",    "Worst"),
]


def _row_for_result(res: backtest.BacktestResult) -> dict:
    s = res.stats or {}
    r = s.get("risk", {}) or {}
    return {
        "trades":   s.get("total", 0),
        "closed":   s.get("closed", 0),
        "win_rate": s.get("win_rate", 0),
        "avg_r":    s.get("avg_r", 0),
        "pf":       r.get("profit_factor", 0),
        "sharpe":   r.get("sharpe_r", 0),
        "sortino":  r.get("sortino_r", 0),
        "max_dd":   r.get("max_drawdown_r", 0),
        "best":     r.get("best_r", 0),
        "worst":    r.get("worst_r", 0),
    }


def _fmt_cell(val) -> str:
    if isinstance(val, float):
        return f"{val:+.2f}" if val != int(val) else f"{val:+.1f}"
    if isinstance(val, int):
        return str(val)
    return str(val)


def format_comparison(result: ComparisonResult, *,
                      max_name_len: int = 32) -> str:
    """
    Pretty-print таблица: одна строка на конфиг, колонки — ключевые метрики.
    """
    if not result.configs:
        return "(no configs)"

    rows = [_row_for_result(r) for r in result.results]

    name_w = min(max_name_len,
                 max(len(c.name) for c in result.configs) if result.configs else 8)
    cell_w = 9

    lines = [f"=== Comparison: {result.symbol} ({result.days}d) ==="]
    header = "Config".ljust(name_w) + " | " + " ".join(
        h.rjust(cell_w) for _, h in _METRIC_COLS)
    lines.append(header)
    lines.append("-" * len(header))

    for cfg, row in zip(result.configs, rows):
        name = cfg.name[:max_name_len]
        cells = [_fmt_cell(row[k]).rjust(cell_w) for k, _ in _METRIC_COLS]
        lines.append(name.ljust(name_w) + " | " + " ".join(cells))

    # HTF P4 diagnostics — для каждого конфига показать сколько P4 заблокировал
    any_htf = any(r.htf_diag for r in result.results)
    if any_htf:
        lines.append("")
        lines.append("HTF P4 diagnostics:")
        for cfg, res in zip(result.configs, result.results):
            d = res.htf_diag or {}
            sc = d.get("strength_counts") or {}
            sd = d.get("strong_directions") or {}
            p4b = d.get("p4_blocks", 0)
            total = sum(sc.values())
            if total == 0:
                continue
            lines.append(
                f"  {cfg.name[:max_name_len]:<{name_w}} | "
                f"strong={sc.get('strong',0)} (L={sd.get('long',0)}, "
                f"S={sd.get('short',0)}) · "
                f"mod={sc.get('moderate',0)} · "
                f"weak={sc.get('weak',0)} · "
                f"neut={sc.get('neutral',0)} · "
                f"P4-blocks={p4b}"
            )

    return "\n".join(lines)


# ─── CLI helpers ───────────────────────────────────────────────────────────


def _to_config(c: Config | dict) -> Config:
    if isinstance(c, Config):
        return c
    if isinstance(c, dict):
        return Config(name=c.get("name", "?"),
                      overrides=c.get("overrides") or {})
    raise TypeError(f"Unsupported config type: {type(c)}")


def _parse_value(s: str):
    """Coerce строки в int/float/bool."""
    s = s.strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _parse_configs(s: str) -> list[Config]:
    """
    Формат:
      name1:{KEY=VAL,KEY2=VAL2};name2:{KEY3=VAL3}

    Например: "baseline:{};strict:{MIN_CONFIDENCE_FOR_TRADE=75}"
    """
    out: list[Config] = []
    for entry in s.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            out.append(Config(name=entry, overrides={}))
            continue
        name, body = entry.split(":", 1)
        name = name.strip()
        body = body.strip().lstrip("{").rstrip("}")
        ov: dict = {}
        if body:
            for pair in body.split(","):
                pair = pair.strip()
                if "=" not in pair:
                    continue
                k, v = pair.split("=", 1)
                ov[k.strip()] = _parse_value(v)
        out.append(Config(name=name, overrides=ov))
    return out


def _parse_grid(s: str) -> dict:
    """
    Формат: "KEY1=v1,v2,v3 KEY2=v4,v5" → {KEY1: [v1,v2,v3], KEY2: [v4,v5]}
    """
    grid: dict = {}
    for chunk in s.split():
        if "=" not in chunk:
            continue
        k, vals = chunk.split("=", 1)
        grid[k.strip()] = [_parse_value(v) for v in vals.split(",")]
    return grid


def _cli() -> int:
    p = argparse.ArgumentParser(
        prog="bt_compare",
        description="Сравнить backtest конфиги и сделать parameter sweep",
    )
    p.add_argument("symbol")
    p.add_argument("days", type=int)
    p.add_argument("--tfs", default="5,15,60,240,D")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--no-funding", action="store_true")
    p.add_argument("--no-oi", action="store_true")
    p.add_argument("--warmup", type=int, default=backtest.DEFAULT_WARMUP_BARS)
    p.add_argument("--expiry", type=int, default=backtest.DEFAULT_EXPIRY_BARS)
    p.add_argument("--cooldown", type=int, default=backtest.DEFAULT_COOLDOWN_BARS)
    p.add_argument("--conf", type=int, default=backtest.DEFAULT_CONF_SCORE)
    p.add_argument("--configs", default=None,
                   help="Configs: name1:{KEY=VAL,...};name2:{...}")
    p.add_argument("--grid", default=None,
                   help="Sweep grid: KEY1=v1,v2 KEY2=v3,v4")
    p.add_argument("--baseline", default=None,
                   help="Общие overrides для sweep: KEY=VAL,KEY2=VAL2")
    p.add_argument("--json", action="store_true",
                   help="Вывести JSON вместо таблицы")
    args = p.parse_args()

    if not args.configs and not args.grid:
        print("Нужен либо --configs либо --grid", file=sys.stderr)
        return 2

    print(f"Fetching {args.symbol} {args.days}d ...", flush=True)
    data = bt_data.fetch_all(
        args.symbol, args.days,
        tfs=[t.strip() for t in args.tfs.split(",")],
        fetch_funding_data=not args.no_funding,
        fetch_oi_data=not args.no_oi,
        cache=not args.no_cache,
    )

    if args.grid:
        baseline = {}
        if args.baseline:
            for pair in args.baseline.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    baseline[k.strip()] = _parse_value(v)
        result = param_sweep(
            data, _parse_grid(args.grid), baseline=baseline,
            warmup_bars=args.warmup, expiry_bars=args.expiry,
            cooldown_bars=args.cooldown,
            default_conf_score=args.conf,
        )
    else:
        configs = _parse_configs(args.configs)
        result = compare(
            data, configs,
            warmup_bars=args.warmup, expiry_bars=args.expiry,
            cooldown_bars=args.cooldown,
            default_conf_score=args.conf,
        )

    if args.json:
        payload = {
            "symbol": result.symbol,
            "days":   result.days,
            "configs": [c.as_dict() for c in result.configs],
            "rows":    [_row_for_result(r) for r in result.results],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print()
        print(format_comparison(result))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
