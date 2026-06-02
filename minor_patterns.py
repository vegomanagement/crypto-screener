"""
minor_patterns.py — minor SMC паттерны (Этап 12 фаза 5).

Реализует:
  • Inside Candle Breakout — свеча с lower-high И higher-low, чем
    предыдущая. PDF: «Expect an explosive price movement after an inside
    candle». Сигнал срабатывает на breakout СЛЕДУЮЩЕЙ свечой (вверх/вниз).
  • Rejection Block (RB) — свеча с непропорционально длинным верхним
    (bearish RB) или нижним (bullish RB) wick. PDF: «Bodies of the candle
    matter» — уровень body_high (bear) / body_low (bull) выступает
    resistance/support. Сигнал — когда цена возвращается к этому уровню.

Pure stdlib. Klines: dict {"o","h","l","c","v"}.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "InsideBreakout",
    "RejectionBlock",
    "find_inside_breakouts",
    "find_rejection_blocks",
    "latest_inside_breakout",
    "latest_rejection_test",
    "DEFAULT_LOOKBACK",
    "DEFAULT_MAX_BARS_AGO",
    "DEFAULT_RB_WICK_RATIO",
    "DEFAULT_MIN_BODY_ATR",
]

DEFAULT_LOOKBACK       = 50
DEFAULT_MAX_BARS_AGO   = 5
DEFAULT_RB_WICK_RATIO  = 2.0    # верхний wick должен быть >= 2× нижнего (bear RB)
DEFAULT_MIN_BODY_ATR   = 0.3    # минимальный body относительно ATR


# ─── ATR helper (без зависимости от bt_market) ────────────────────────────


def _atr(klines: list, window: int = 20) -> float:
    seg = klines[-window:] if len(klines) > window else klines
    if len(seg) < 2:
        return seg[0]["h"] - seg[0]["l"] if seg else 0.0
    trs = []
    prev_c = seg[0]["c"]
    for bar in seg[1:]:
        trs.append(max(bar["h"] - bar["l"],
                       abs(bar["h"] - prev_c),
                       abs(bar["l"] - prev_c)))
        prev_c = bar["c"]
    return sum(trs) / len(trs) if trs else 0.0


# ─── Inside Candle Breakout ───────────────────────────────────────────────


@dataclass(frozen=True)
class InsideBreakout:
    direction:    str    # "bull" | "bear"
    inside_idx:   int    # индекс inside свечи
    breakout_idx: int    # индекс свечи, пробившей inside range
    inside_high:  float
    inside_low:   float
    breakout_close: float


def find_inside_breakouts(
    klines: list,
    lookback: int = DEFAULT_LOOKBACK,
) -> list[InsideBreakout]:
    """
    Inside candle = high[i] < high[i-1] AND low[i] > low[i-1].
    Breakout: следующая (или одна из 1-2 следующих) свеча пробивает
    inside_high (вверх → bull) или inside_low (вниз → bear) по close.
    """
    if len(klines) < 3:
        return []
    out: list[InsideBreakout] = []
    start = max(1, len(klines) - lookback)
    for i in range(start, len(klines) - 1):
        prev = klines[i - 1]
        cur  = klines[i]
        if not (cur["h"] < prev["h"] and cur["l"] > prev["l"]):
            continue
        # Breakout — поиск в next 1-2 баров
        for j in range(i + 1, min(len(klines), i + 3)):
            if klines[j]["c"] > cur["h"]:
                out.append(InsideBreakout(
                    direction="bull", inside_idx=i, breakout_idx=j,
                    inside_high=float(cur["h"]), inside_low=float(cur["l"]),
                    breakout_close=float(klines[j]["c"]),
                ))
                break
            if klines[j]["c"] < cur["l"]:
                out.append(InsideBreakout(
                    direction="bear", inside_idx=i, breakout_idx=j,
                    inside_high=float(cur["h"]), inside_low=float(cur["l"]),
                    breakout_close=float(klines[j]["c"]),
                ))
                break
    out.sort(key=lambda x: x.breakout_idx)
    return out


def latest_inside_breakout(
    klines: list,
    lookback: int = DEFAULT_LOOKBACK,
    max_bars_ago: int = DEFAULT_MAX_BARS_AGO,
) -> InsideBreakout | None:
    if not klines:
        return None
    events = find_inside_breakouts(klines, lookback)
    if not events:
        return None
    last = events[-1]
    if (len(klines) - 1) - last.breakout_idx > max_bars_ago:
        return None
    return last


# ─── Rejection Block ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class RejectionBlock:
    direction:    str   # "bull" | "bear" (signal direction)
    candle_idx:   int   # индекс RB-свечи (с длинным wick)
    body_high:    float
    body_low:     float
    wick_high:    float  # full high of candle (for bear RB)
    wick_low:     float  # full low (for bull RB)
    test_idx:     int   # индекс свечи, которая ретестит body level


def find_rejection_blocks(
    klines: list,
    lookback: int = DEFAULT_LOOKBACK,
    wick_ratio: float = DEFAULT_RB_WICK_RATIO,
    min_body_atr: float = DEFAULT_MIN_BODY_ATR,
) -> list[RejectionBlock]:
    """
    Bearish RB:
      • Свеча с upper_wick >= wick_ratio × lower_wick (price отвергнут вверху)
      • Body >= min_body_atr × ATR (не доджи)
      • Позже какой-то бар имеет high >= body_high — retest как resistance

    Bullish RB — зеркально (lower_wick >= wick_ratio × upper).
    """
    if len(klines) < 5:
        return []
    atr = _atr(klines)
    if atr <= 0:
        return []
    out: list[RejectionBlock] = []
    start = max(0, len(klines) - lookback)
    for i in range(start, len(klines) - 1):
        bar = klines[i]
        o, c, h, lo = bar["o"], bar["c"], bar["h"], bar["l"]
        body_high = max(o, c)
        body_low  = min(o, c)
        body      = body_high - body_low
        if body < min_body_atr * atr:
            continue
        upper_wick = h - body_high
        lower_wick = body_low - lo
        # Защита от деления: нужна минимальная база для отношения
        eps = atr * 0.05

        # Bearish RB: верхний wick доминирует
        if upper_wick >= wick_ratio * max(lower_wick, eps) and upper_wick > eps:
            for j in range(i + 1, len(klines)):
                if klines[j]["h"] >= body_high:
                    out.append(RejectionBlock(
                        direction="bear", candle_idx=i,
                        body_high=float(body_high), body_low=float(body_low),
                        wick_high=float(h), wick_low=float(lo),
                        test_idx=j,
                    ))
                    break

        # Bullish RB: нижний wick доминирует
        if lower_wick >= wick_ratio * max(upper_wick, eps) and lower_wick > eps:
            for j in range(i + 1, len(klines)):
                if klines[j]["l"] <= body_low:
                    out.append(RejectionBlock(
                        direction="bull", candle_idx=i,
                        body_high=float(body_high), body_low=float(body_low),
                        wick_high=float(h), wick_low=float(lo),
                        test_idx=j,
                    ))
                    break

    out.sort(key=lambda x: x.test_idx)
    return out


def latest_rejection_test(
    klines: list,
    lookback: int = DEFAULT_LOOKBACK,
    max_bars_ago: int = DEFAULT_MAX_BARS_AGO,
) -> RejectionBlock | None:
    if not klines:
        return None
    events = find_rejection_blocks(klines, lookback)
    if not events:
        return None
    last = events[-1]
    if (len(klines) - 1) - last.test_idx > max_bars_ago:
        return None
    return last
