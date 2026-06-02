"""
backtest.py — replay engine для historical strategy validation.

Walk через 5m klines, на каждом баре:
  1. bt_market.build_market_at(idx) → snapshot market dict
  2. Детект сигналов (legacy detect_signals + patterns + order_blocks)
  3. Для каждого: make_decision → verdict + levels
  4. Если LONG/SHORT: открываем simulated trade
  5. Walk forward 5m свечей → first hit SL/TP/expired
  6. Запись в trade log

Output: BacktestResult с trades + aggregated stats (формат /stats).

CLI:
  python -m backtest BTCUSDT 30 [--config KEY=VAL,...] [--cooldown N]
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from dataclasses import dataclass, field
from typing import Iterator

import bt_data
import bt_market
import order_blocks
import patterns
import tracking
from decision import make_decision

__all__ = [
    "BacktestTrade",
    "BacktestResult",
    "run_backtest",
    "format_result",
    "DEFAULT_WARMUP_BARS",
    "DEFAULT_EXPIRY_BARS",
    "DEFAULT_COOLDOWN_BARS",
    "DEFAULT_CONF_SCORE",
]

DEFAULT_WARMUP_BARS  = 100       # пропуск первых N 5m баров (нужны для indicators)
DEFAULT_EXPIRY_BARS  = 2016      # 7d × 288 5m bars
DEFAULT_COOLDOWN_BARS = 12       # ~1h на 5m: cooldown per (symbol, signal_type)
DEFAULT_CONF_SCORE   = 70        # base confluence для make_decision (выше WAIT-threshold)


@dataclass
class BacktestTrade:
    """Одна замоделированная сделка."""
    signal_type:  str
    direction:    str
    open_idx:     int
    open_ts:      int
    entry:        float
    sl:           float
    tp1:          float
    tp2:          float
    tp3:          float
    confidence:   int
    close_idx:    int | None  = None
    close_ts:     int | None  = None
    status:       str         = "open"
    hit_level:    str | None  = None
    r_multiple:   float       = 0.0


@dataclass
class BacktestResult:
    symbol:           str
    days:             int
    trades:           list      = field(default_factory=list)
    skipped_count:    int       = 0
    stats:            dict      = field(default_factory=dict)
    config_overrides: dict | None = None


# ─── Local detect_signals (без impo screener.py — оно тянет config.py) ────


def _detect_signals_minimal(candles: list) -> list[str]:
    """
    Минимальный набор детекторов, аналогичных screener.detect_signals.
    Копия чистой логики, без зависимостей от config.
    """
    if len(candles) < 22:
        return []

    lookback = 20
    prev      = candles[-lookback - 1: -1]
    last      = candles[-1]
    signals: list[str] = []

    prev_high = max(x["h"] for x in prev)
    prev_low  = min(x["l"] for x in prev)
    close_now = last["c"]
    trend_up  = candles[-lookback - 1]["c"] < candles[-2]["c"]

    # BOS / CHoCH
    if close_now > prev_high:
        signals.append("BOS_BULL" if trend_up else "CHOCH_BULL")
    elif close_now < prev_low:
        signals.append("BOS_BEAR" if not trend_up else "CHOCH_BEAR")

    # FVG (3-candle gap)
    if len(candles) >= 3:
        c2, c0 = candles[-3], candles[-1]
        if c0["l"] > c2["h"]:
            signals.append("FVG_BULL")
        elif c0["h"] < c2["l"]:
            signals.append("FVG_BEAR")

    # Liquidity Sweep (intra-candle reclaim)
    if last["h"] > prev_high and last["c"] < prev_high:
        signals.append("LIQ_SWEEP_H")
    if last["l"] < prev_low and last["c"] > prev_low:
        signals.append("LIQ_SWEEP_L")

    return signals


def _detect_all_signals(klines_5m_so_far: list) -> list[str]:
    """Aggregator: legacy detect + sweep+reclaim + Order Blocks."""
    detected = _detect_signals_minimal(klines_5m_so_far)

    sr = patterns.latest_sweep_reclaim(klines_5m_so_far)
    if sr is not None:
        detected.append("SWEEP_RECLAIM_BULL" if sr.direction == "bull"
                        else "SWEEP_RECLAIM_BEAR")

    ob = order_blocks.latest_ob_test(klines_5m_so_far)
    if ob is not None:
        detected.append("OB_BULL" if ob.direction == "bull" else "OB_BEAR")

    return detected


# ─── TP/SL outcome simulation ─────────────────────────────────────────────


def _simulate_outcome(
    klines_5m: list,
    open_idx:  int,
    verdict:   str,
    sl:        float | None,
    tp1:       float | None,
    tp2:       float | None,
    tp3:       float | None,
    rr1:       float | None,
    rr2:       float | None,
    rr3:       float | None,
    expiry_bars: int,
) -> tuple[int, str, str | None, float] | None:
    """
    Walk klines после open_idx до first hit SL/TP или expiry.
    Уважает tracking.SAME_BAR_TIE_BREAK (default 'fair' → 0R tie).

    Returns (close_idx, status, hit_level, r_multiple) или None при невалидных
    уровнях.
    """
    if sl is None or tp1 is None:
        return None

    tps = []
    if tp1 is not None:
        tps.append(("TP1", tp1, rr1 or 1.5))
    if tp2 is not None:
        tps.append(("TP2", tp2, rr2 or 2.5))
    if tp3 is not None:
        tps.append(("TP3", tp3, rr3 or 4.0))

    end = min(len(klines_5m), open_idx + 1 + expiry_bars)

    for j in range(open_idx + 1, end):
        bar = klines_5m[j]
        low, high = bar["l"], bar["h"]

        if verdict == "LONG":
            sl_hit = low <= sl
            tp_cands = [(lvl, p, r) for (lvl, p, r) in tps if high >= p]
        elif verdict == "SHORT":
            sl_hit = high >= sl
            tp_cands = [(lvl, p, r) for (lvl, p, r) in tps if low <= p]
        else:
            return None

        # Same-bar tie: respect tracking.SAME_BAR_TIE_BREAK
        if sl_hit and tp_cands:
            if tracking.SAME_BAR_TIE_BREAK == "conservative":
                return (j, "sl_hit", "SL", -1.0)
            return (j, "tie_hit", "TIE", 0.0)
        if sl_hit:
            return (j, "sl_hit", "SL", -1.0)
        if tp_cands:
            tp_cands.sort(key=lambda x: x[2], reverse=True)
            level, _, rr = tp_cands[0]
            return (j, f"{level.lower()}_hit", level, float(rr))

    return (end - 1, "expired", None, 0.0)


# ─── Config overrides (monkeypatch decision constants) ────────────────────


@contextlib.contextmanager
def _config_override(overrides: dict | None) -> Iterator[None]:
    """
    Временно перебивает константы в модуле `decision` на время run_backtest.
    Поддерживаются ключи как имена атрибутов модуля.
    """
    if not overrides:
        yield
        return
    import decision
    saved: dict[str, object] = {}
    for k, v in overrides.items():
        if hasattr(decision, k):
            saved[k] = getattr(decision, k)
            setattr(decision, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            setattr(decision, k, v)


# ─── Aggregator: stats в стиле tracking.compute_stats ─────────────────────


def _aggregate_stats(trades: list, days: int) -> dict:
    from collections import defaultdict

    by_status = defaultdict(int)
    by_signal = defaultdict(lambda: {"n": 0, "wins": 0, "r_sum": 0.0})
    closed_r: list[float] = []

    for tr in trades:
        by_status[tr.status] += 1
        if tr.status == "open":
            continue
        r = tr.r_multiple if tr.r_multiple is not None else 0.0
        closed_r.append(r)
        is_win = r > 0
        by_signal[tr.signal_type]["n"]     += 1
        by_signal[tr.signal_type]["r_sum"] += r
        if is_win:
            by_signal[tr.signal_type]["wins"] += 1

    closed_n   = len(closed_r)
    total_wins = sum(1 for r in closed_r if r > 0)
    win_rate   = (total_wins / closed_n * 100) if closed_n else 0
    avg_r      = (sum(closed_r) / closed_n) if closed_n else 0

    pf      = tracking._profit_factor(closed_r) if closed_r else 0.0
    sharpe  = tracking._sharpe_r(closed_r)      if closed_r else 0.0
    sortino = tracking._sortino_r(closed_r)     if closed_r else 0.0
    max_dd  = tracking._max_drawdown_r(closed_r) if closed_r else 0.0
    consec  = tracking._max_consec_loss(closed_r) if closed_r else 0
    best_r  = max(closed_r) if closed_r else 0.0
    worst_r = min(closed_r) if closed_r else 0.0

    equity = []
    cum = 0.0
    for r in closed_r:
        cum += r
        equity.append(round(cum, 2))

    by_signal_summary = []
    for k, v in by_signal.items():
        n = v["n"]
        if n == 0:
            continue
        wr = round(v["wins"] / n * 100, 1)
        ar = round(v["r_sum"] / n, 2)
        by_signal_summary.append((k, n, wr, ar))
    by_signal_summary.sort(key=lambda x: -x[1])

    return {
        "days":     days,
        "total":    len(trades),
        "open":     by_status.get("open", 0),
        "closed":   closed_n,
        "win_rate": round(win_rate, 1),
        "avg_r":    round(avg_r, 2),
        "hits": {
            "tp1":     by_status.get("tp1_hit", 0),
            "tp2":     by_status.get("tp2_hit", 0),
            "tp3":     by_status.get("tp3_hit", 0),
            "sl":      by_status.get("sl_hit",  0),
            "tie":     by_status.get("tie_hit", 0),
            "expired": by_status.get("expired", 0),
        },
        "risk": {
            "profit_factor":   round(pf, 2) if pf != float("inf") else "∞",
            "sharpe_r":        round(sharpe, 2),
            "sortino_r":       (round(sortino, 2)
                                if sortino != float("inf") else "∞"),
            "max_drawdown_r":  round(max_dd, 2),
            "max_consec_loss": consec,
            "best_r":          round(best_r, 2),
            "worst_r":         round(worst_r, 2),
        },
        "equity":    equity,
        "by_signal": by_signal_summary,
    }


# ─── Главный entry point ──────────────────────────────────────────────────


def _tf_minutes(tf: str) -> int:
    """Конвертация TF в минуты для масштабирования expiry/cooldown."""
    s = str(tf).upper()
    if s == "D":
        return 1440
    if s == "W":
        return 10080
    if s == "M":
        return 30 * 1440
    try:
        return int(s)
    except ValueError:
        return 5


def run_backtest(
    data: dict,
    *,
    tf_primary:        str = "5",
    warmup_bars:       int = DEFAULT_WARMUP_BARS,
    expiry_bars:       int | None = None,
    cooldown_bars:     int | None = None,
    config_overrides:  dict | None = None,
    default_conf_score: int = DEFAULT_CONF_SCORE,
    progress_each:     int | None = None,
) -> BacktestResult:
    """
    Главный entry. data — из bt_data.fetch_all(symbol, days).
    Симулирует прогон всего decision-pipeline на свечах primary TF.

    tf_primary: '5' | '15' | '60' | '240' | 'D' — на каком TF идёт walk-bar.
    По умолчанию 5m (ICT-канон). При выборе другого TF expiry/cooldown
    масштабируются автоматически (7d expiry и 1h cooldown в реальном
    времени, независимо от tf_primary), если не переопределены явно.
    """
    # Auto-scale expiry/cooldown под выбранный TF (в реальном времени)
    tf_min = _tf_minutes(tf_primary)
    if expiry_bars is None:
        expiry_bars = (DEFAULT_EXPIRY_BARS * 5) // tf_min   # 7d → bars
    if cooldown_bars is None:
        cooldown_bars = max(1, (DEFAULT_COOLDOWN_BARS * 5) // tf_min)  # 1h → bars

    klines_primary = data.get("klines", {}).get(tf_primary) or []
    symbol         = data.get("symbol", "?")
    days           = data.get("days", 0)

    if not klines_primary:
        return BacktestResult(symbol=symbol, days=days)

    trades: list[BacktestTrade] = []
    skipped = 0
    last_signal_idx: dict[tuple, int] = {}

    with _config_override(config_overrides):
        for idx in range(warmup_bars, len(klines_primary)):
            klines_so_far = klines_primary[:idx + 1]

            market = bt_market.build_market_at(data, idx, tf_primary=tf_primary)
            if not market:
                continue

            detected = _detect_all_signals(klines_so_far)

            for sig_type in detected:
                key = (symbol, sig_type)
                if key in last_signal_idx and (idx - last_signal_idx[key]) < cooldown_bars:
                    continue
                last_signal_idx[key] = idx

                price = klines_primary[idx]["c"]
                d = make_decision(
                    signal_type=sig_type,
                    price=price,
                    market=market,
                    mtf={},
                    confluence_score=default_conf_score,
                    confluence_factors=[],
                )

                if d["verdict"] not in ("LONG", "SHORT"):
                    skipped += 1
                    continue

                entry = (
                    ((d["entry"]["min"] + d["entry"]["max"]) / 2)
                    if d.get("entry") else price
                )

                outcome = _simulate_outcome(
                    klines_primary, idx, d["verdict"],
                    d["sl"], d["tp1"], d["tp2"], d["tp3"],
                    d.get("rr1"), d.get("rr2"), d.get("rr3"),
                    expiry_bars,
                )
                if outcome is None:
                    skipped += 1
                    continue
                close_idx, status, hit_level, r_mult = outcome
                close_ts = klines_primary[close_idx]["ts"] if close_idx < len(klines_primary) else klines_primary[-1]["ts"]

                trades.append(BacktestTrade(
                    signal_type=sig_type,
                    direction=d["direction"],
                    open_idx=idx,
                    open_ts=klines_primary[idx]["ts"],
                    entry=float(entry),
                    sl=float(d["sl"]),
                    tp1=float(d["tp1"]),
                    tp2=float(d["tp2"]),
                    tp3=float(d["tp3"]),
                    confidence=int(d.get("confidence", 0)),
                    close_idx=close_idx,
                    close_ts=close_ts,
                    status=status,
                    hit_level=hit_level,
                    r_multiple=r_mult,
                ))

            if progress_each and idx % progress_each == 0:
                print(f"[backtest] {symbol} idx={idx}/{len(klines_primary)} "
                      f"trades={len(trades)} skipped={skipped}", flush=True)

    stats = _aggregate_stats(trades, days)
    return BacktestResult(
        symbol=symbol,
        days=days,
        trades=trades,
        skipped_count=skipped,
        stats=stats,
        config_overrides=config_overrides,
    )


# ─── Pretty-print summary ─────────────────────────────────────────────────


def format_result(result: BacktestResult) -> str:
    s = result.stats
    lines = [f"=== Backtest: {result.symbol} ({result.days}d) ==="]
    lines.append(f"Trades: {s.get('total', 0)} · "
                 f"closed: {s.get('closed', 0)} · "
                 f"skipped: {result.skipped_count}")
    if s.get("closed"):
        lines.append(f"Win-rate: {s['win_rate']}% · Avg R: {s['avg_r']:+.2f}")
        r = s.get("risk", {})
        lines.append(f"PF: {r.get('profit_factor')} · "
                     f"Sharpe: {r.get('sharpe_r')} · "
                     f"Sortino: {r.get('sortino_r')}")
        lines.append(f"Max DD: {r.get('max_drawdown_r')}R · "
                     f"Consec losses: {r.get('max_consec_loss')}")
        hits = s.get("hits", {})
        lines.append(f"TP1/2/3: {hits.get('tp1',0)}/"
                     f"{hits.get('tp2',0)}/{hits.get('tp3',0)} · "
                     f"SL: {hits.get('sl',0)} · "
                     f"Tie: {hits.get('tie',0)} · "
                     f"Expired: {hits.get('expired',0)}")
        if s.get("by_signal"):
            lines.append("\nBy signal type:")
            for st, n, wr, ar in s["by_signal"][:10]:
                lines.append(f"  {st:<22} n={n:>4} wr={wr:>5.1f}% avgR={ar:+.2f}")
    if result.config_overrides:
        lines.append(f"\nConfig overrides: {result.config_overrides}")
    return "\n".join(lines)


# ─── CLI ──────────────────────────────────────────────────────────────────


def _parse_overrides(s: str | None) -> dict | None:
    """Парсит `KEY=VAL,KEY2=VAL2` → dict. Значения интерпретируются как
    int/float/bool/str (попытки type-coerce)."""
    if not s:
        return None
    out: dict[str, object] = {}
    for pair in s.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        k, v = k.strip(), v.strip()
        if v.lower() in ("true", "false"):
            out[k] = (v.lower() == "true")
        else:
            try:
                if "." in v:
                    out[k] = float(v)
                else:
                    out[k] = int(v)
            except ValueError:
                out[k] = v
    return out or None


def _cli() -> int:
    p = argparse.ArgumentParser(
        prog="backtest", description="Replay strategy на исторических данных",
    )
    p.add_argument("symbol")
    p.add_argument("days", type=int)
    p.add_argument("--tfs", default="5,15,60,240,D")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--warmup", type=int, default=DEFAULT_WARMUP_BARS)
    p.add_argument("--expiry", type=int, default=DEFAULT_EXPIRY_BARS)
    p.add_argument("--cooldown", type=int, default=DEFAULT_COOLDOWN_BARS)
    p.add_argument("--conf", type=int, default=DEFAULT_CONF_SCORE,
                   help="Default confluence score для make_decision")
    p.add_argument("--config", default=None,
                   help="Override decision constants: KEY=VAL,KEY2=VAL2")
    p.add_argument("--no-funding", action="store_true")
    p.add_argument("--no-oi", action="store_true")
    p.add_argument("--progress", type=int, default=500,
                   help="Print progress каждые N баров")
    args = p.parse_args()

    print(f"Fetching {args.symbol} {args.days}d ...", flush=True)
    data = bt_data.fetch_all(
        args.symbol, args.days,
        tfs=[t.strip() for t in args.tfs.split(",")],
        fetch_funding_data=not args.no_funding,
        fetch_oi_data=not args.no_oi,
        cache=not args.no_cache,
    )

    overrides = _parse_overrides(args.config)
    print(f"Replaying {len(data['klines'].get('5', []))} 5m bars "
          f"(warmup={args.warmup}) ...", flush=True)
    result = run_backtest(
        data,
        warmup_bars=args.warmup,
        expiry_bars=args.expiry,
        cooldown_bars=args.cooldown,
        default_conf_score=args.conf,
        config_overrides=overrides,
        progress_each=args.progress,
    )
    print()
    print(format_result(result))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
