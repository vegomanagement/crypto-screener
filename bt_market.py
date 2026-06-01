"""
bt_market.py — построение historical market dict для бектеста.

На вход: dict от bt_data.fetch_all(symbol, days) — klines (multi-TF) +
funding + OI. На выход: market dict в shape, ожидаемом make_decision /
liquidity.py / regime.py / killzones (P3 gate). То есть бэктест может
прогонять весь decision-pipeline на исторических данных.

Восстанавливаются:
  • indicators (ATR Wilder, RSI, MACD trend) из klines
  • CVD-proxy (cumulative volume × sign(close-open)) → trend/divergence
  • EMA biases per TF (EMA9 vs EMA21)
  • change_24h (по 288 5m-барам)
  • pivots PDH/PDL/P/R1/S1 (нужен 'D' TF в data)
  • funding/oi из исторических endpoints
  • ts — timestamp бара (для killzone-гейта)

Стабится в neutral/empty (нет надёжных историч. данных):
  • vp (volume profile) — нужны intraday tick data
  • ls_ratio (long/short ratio)
  • liquidations
  • turtle_1h/4h zones (специфичный алгоритм, отложен)
  • rsi_div (детект дивергенции — нетривиально, можно добавить позже)

Pure stdlib. Klines: {"ts", "o", "h", "l", "c", "v"}.
"""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = [
    "compute_atr",
    "compute_rsi",
    "compute_ema",
    "compute_macd",
    "compute_cvd_proxy",
    "compute_ema_biases",
    "compute_change_24h",
    "compute_pivots",
    "funding_at",
    "oi_change_at",
    "build_market_at",
    "ATR_PERIOD",
    "RSI_PERIOD",
]

# ─── Параметры (match prod conventions) ───────────────────────────────────
ATR_PERIOD     = 14
RSI_PERIOD     = 14
MACD_FAST      = 12
MACD_SLOW      = 26
MACD_SIGNAL    = 9
EMA_FAST       = 9
EMA_SLOW       = 21
EMA_BIAS_DEADBAND = 0.001  # 0.1% — нейтральная зона


# ─── Indicators ───────────────────────────────────────────────────────────


def compute_atr(klines: list, period: int = ATR_PERIOD) -> float:
    """
    Wilder's ATR over period. Возвращает 0.0 если данных не хватает.
    """
    if len(klines) < period + 1:
        return 0.0
    trs = []
    prev_close = klines[0]["c"]
    for bar in klines[1:]:
        tr = max(
            bar["h"] - bar["l"],
            abs(bar["h"] - prev_close),
            abs(bar["l"] - prev_close),
        )
        trs.append(tr)
        prev_close = bar["c"]
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return float(atr)


def compute_rsi(klines: list, period: int = RSI_PERIOD) -> float:
    """Standard Wilder RSI. Возвращает 50.0 если данных не хватает."""
    if len(klines) < period + 1:
        return 50.0
    closes = [b["c"] for b in klines]
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def compute_ema(values: list, period: int) -> list:
    """Standard EMA по списку значений. Возвращает len(values) точек."""
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [float(values[0])]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def compute_macd(
    klines: list,
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal: int = MACD_SIGNAL,
) -> dict:
    """
    MACD trend: 'bull' (MACD line > Signal line), 'bear' (ниже),
    'neutral' (данных мало).
    """
    closes = [b["c"] for b in klines]
    if len(closes) < slow + signal:
        return {"trend": "neutral", "cross": "none"}
    ema_fast = compute_ema(closes, fast)
    ema_slow = compute_ema(closes, slow)
    macd_line   = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = compute_ema(macd_line, signal)
    trend = "bull" if macd_line[-1] > signal_line[-1] else "bear"
    return {"trend": trend, "cross": "none"}


# ─── CVD proxy (без tick data — derive из volume × sign) ──────────────────


def compute_cvd_proxy(klines: list, lookback: int = 50) -> dict:
    """
    CVD-proxy: cumulative (volume × sign), где sign = +1 (close ≥ open) /
    -1 (close < open). Возвращает {trend, price_trend, divergence}.

    Это упрощение — настоящий CVD требует tick-by-tick данных по
    aggressor side. Наш proxy достаточно показателен для regime-гейта.
    """
    if not klines:
        return {"trend": "unknown", "price_trend": "unknown",
                "divergence": False}

    seg = klines[-lookback:] if len(klines) > lookback else klines
    if len(seg) < 5:
        return {"trend": "unknown", "price_trend": "unknown",
                "divergence": False}

    cvd_series = []
    cum = 0.0
    for bar in seg:
        sign = 1 if bar["c"] >= bar["o"] else -1
        cum += bar["v"] * sign
        cvd_series.append(cum)

    q = max(1, len(cvd_series) // 4)
    cvd_start_avg   = sum(cvd_series[:q]) / q
    cvd_end_avg     = sum(cvd_series[-q:]) / q
    cvd_trend       = "up" if cvd_end_avg > cvd_start_avg else "down"

    closes = [b["c"] for b in seg]
    price_start_avg = sum(closes[:q]) / q
    price_end_avg   = sum(closes[-q:]) / q
    price_trend     = "up" if price_end_avg > price_start_avg else "down"

    divergence = (cvd_trend != price_trend)
    return {"trend": cvd_trend, "price_trend": price_trend,
            "divergence": divergence}


# ─── EMA biases per TF ─────────────────────────────────────────────────────


def compute_ema_biases(klines: list) -> str:
    """
    Возвращает 'bull' / 'bear' / 'neutral' на основе EMA9 vs EMA21
    (с deadband EMA_BIAS_DEADBAND для нейтрализации шума).
    """
    closes = [b["c"] for b in klines]
    if len(closes) < EMA_SLOW + 5:
        return "neutral"
    ema_f = compute_ema(closes, EMA_FAST)
    ema_s = compute_ema(closes, EMA_SLOW)
    if ema_s[-1] <= 0:
        return "neutral"
    ratio = ema_f[-1] / ema_s[-1]
    if ratio > 1 + EMA_BIAS_DEADBAND:
        return "bull"
    if ratio < 1 - EMA_BIAS_DEADBAND:
        return "bear"
    return "neutral"


# ─── Change 24h ────────────────────────────────────────────────────────────


def compute_change_24h(klines_5m: list, current_idx: int) -> float:
    """
    % изменение цены за 24h (=288 5m-баров). 0.0 при недостаточной истории.
    """
    if current_idx < 288 or current_idx >= len(klines_5m):
        return 0.0
    cur  = klines_5m[current_idx]["c"]
    past = klines_5m[current_idx - 288]["c"]
    if past <= 0:
        return 0.0
    return (cur - past) / past * 100.0


# ─── Pivots ────────────────────────────────────────────────────────────────


def compute_pivots(klines_daily: list, current_idx: int) -> dict:
    """
    Pivots от ПРЕДЫДУЩЕЙ дневной свечи (классическая формула):
      P  = (H + L + C) / 3
      R1 = 2P - L,  S1 = 2P - H
    Плюс PDH/PDL (prior day high/low) для liquidity-карты.
    """
    if current_idx < 1 or current_idx >= len(klines_daily):
        return {}
    prev = klines_daily[current_idx - 1]
    high, low, close = prev["h"], prev["l"], prev["c"]
    p  = (high + low + close) / 3
    r1 = 2 * p - low
    s1 = 2 * p - high
    return {"PDH": float(high), "PDL": float(low),
            "P": float(p), "R1": float(r1), "S1": float(s1)}


# ─── Funding / OI lookup ──────────────────────────────────────────────────


def funding_at(funding_history: list, ts_ms: int) -> float:
    """Последнее funding значение ≤ ts_ms (или 0.0)."""
    last = 0.0
    for f in funding_history:
        if f["ts"] <= ts_ms:
            last = f["funding"]
        else:
            break
    return float(last)


def oi_change_at(oi_history: list, ts_ms: int, lookback_hours: int = 24) -> float:
    """
    % изменение OI за последние lookback_hours от ts_ms.
    OI history — точки с интервалом 1h (default из bt_data).
    """
    relevant = [o for o in oi_history if o["ts"] <= ts_ms]
    if len(relevant) < lookback_hours + 1:
        return 0.0
    cur  = relevant[-1]["oi"]
    past = relevant[-(lookback_hours + 1)]["oi"]
    if past <= 0:
        return 0.0
    return (cur - past) / past * 100.0


# ─── Главный builder ──────────────────────────────────────────────────────


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _slice_klines_up_to(klines: list, ts_ms: int) -> list:
    """Бары с ts ≤ ts_ms (предполагает отсортированный по ts вход)."""
    out = []
    for k in klines:
        if k["ts"] <= ts_ms:
            out.append(k)
        else:
            break
    return out


def build_market_at(
    data: dict,
    idx: int,
    *,
    tf_primary: str = "5",
) -> dict:
    """
    Собирает market-snapshot на момент 5m-бара под индексом idx.

    data — результат bt_data.fetch_all(symbol, days).
    Возвращает dict в shape, эквивалентном prod fetch_market — пригодный
    для make_decision, build_liquidity_map, classify_regime, killzone-гейта.

    Стабленные поля (vp/ls_ratio/liquidations/turtle/rsi_div) — нейтральные,
    не блокируют движок.
    """
    klines_5m = data["klines"].get(tf_primary) or []
    if not klines_5m or idx < 0 or idx >= len(klines_5m):
        return {}

    cur_ts = klines_5m[idx]["ts"]

    sliced: dict[str, list] = {}
    for tf, kl in data["klines"].items():
        sliced[tf] = _slice_klines_up_to(kl, cur_ts)

    primary = sliced.get(tf_primary) or []
    if not primary:
        return {}

    # Indicators
    atr  = compute_atr(primary[-30:] if len(primary) >= 30 else primary)
    rsi  = compute_rsi(primary[-(RSI_PERIOD + 5):] if len(primary) >= RSI_PERIOD + 5 else primary)
    macd = compute_macd(primary)

    cvd = compute_cvd_proxy(primary, lookback=50)

    ema_biases = {tf: compute_ema_biases(kl) for tf, kl in sliced.items() if kl}

    chg_24h = compute_change_24h(primary, len(primary) - 1)

    pivots = {}
    daily = sliced.get("D") or []
    if daily and len(daily) > 1:
        pivots = compute_pivots(daily, len(daily) - 1)

    funding = funding_at(data.get("funding") or [], cur_ts)
    oi_chg  = oi_change_at(data.get("oi") or [], cur_ts)

    return {
        "symbol": data.get("symbol", "?"),
        "price":  float(klines_5m[idx]["c"]),
        "ts":     _ms_to_dt(cur_ts),
        "indicators": {
            "atr":      atr,
            "atr_pct":  (atr / klines_5m[idx]["c"] * 100) if klines_5m[idx]["c"] else 0.0,
            "rsi":      rsi,
            "macd":     macd,
            "rsi_div":  "none",
        },
        "bybit": {
            "funding": funding,
            "oi_chg":  oi_chg,
        },
        "cvd":          cvd,
        "ema_biases":   ema_biases,
        "_klines":      sliced,
        "change_24h":   chg_24h,
        "pivots":       pivots,
        "vp":           {},
        "ls_ratio":     {},
        "liquidations": {},
        "turtle_1h":    {},
        "turtle_4h":    {},
    }
