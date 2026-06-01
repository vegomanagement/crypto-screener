"""
order_blocks.py — детект ICT Order Blocks (Этап 12, фаза 1).

Order Block (OB) по канону ICT (Smart Money Concepts):
  • Bullish OB — последняя downclose-свеча перед импульсом ВВЕРХ.
    «Last down candle that has the most range between open to close,
    near support, validated when the high is traded through by a later
    formed candle» (PDF SMC ICT).
  • Bearish OB — зеркально, последняя upclose-свеча перед импульсом ВНИЗ.

Жизненный цикл OB:
  1. Formation — кандидат: downclose (для bull) или upclose (для bear) с
     достаточным body относительно ATR.
  2. Validation — позже идёт бар, пробивающий high (для bull) или low
     (для bear) — это означает, что OB «отгрузил» order flow в сторону.
  3. Mitigation — цена возвращается в body OB. Это и есть entry trigger
     по канону PDF: «when price trades higher away from the Bullish OB
     and then return to the OB high».
  4. Invalidation — close ниже OB low (для bull) → OB больше не работает.

API:
  • find_order_blocks(klines, ...) — все валидированные OB в lookback окне
  • latest_ob_test(klines, ...) — OB, который цена тестирует прямо сейчас
    (для генерации realtime сигнала)

Pure stdlib + zero deps. Klines: dict {"o","h","l","c","v"}.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "OrderBlock",
    "find_order_blocks",
    "latest_ob_test",
    "DEFAULT_LOOKBACK",
    "DEFAULT_MIN_BODY_ATR",
    "DEFAULT_VALIDATION_WINDOW",
    "DEFAULT_ATR_WINDOW",
]

DEFAULT_LOOKBACK           = 50   # сколько баров сканировать на OB
DEFAULT_MIN_BODY_ATR       = 0.5  # body OB >= 0.5×ATR (отсеять мелкие свечи)
DEFAULT_VALIDATION_WINDOW  = 10   # за сколько баров должен подтвердиться OB
DEFAULT_ATR_WINDOW         = 20   # окно для ATR-proxy


@dataclass(frozen=True)
class OrderBlock:
    """Идентифицированный Order Block."""
    direction:   str    # "bull" — buy zone (downclose pre-up impulse)
                        # "bear" — sell zone (upclose pre-down impulse)
    candle_idx:  int    # индекс OB-свечи в klines
    high:        float  # high OB-свечи
    low:         float  # low OB-свечи
    open:        float
    close:       float
    body_high:   float  # max(open, close)
    body_low:    float  # min(open, close)
    body_atr:    float  # размер body в единицах ATR (для качества)
    validated:   bool   # пробит high/low более поздним баром
    mitigated:   bool   # цена возвращалась в body или закрылась за low/high


def _atr_proxy(klines: list, window: int) -> float:
    """Простой ATR-proxy: средний true range за последние window баров."""
    if not klines:
        return 0.0
    seg = klines[-window:] if len(klines) > window else klines
    if len(seg) < 2:
        return seg[0]["h"] - seg[0]["l"] if seg else 0.0
    trs = []
    prev_close = seg[0]["c"]
    for bar in seg[1:]:
        tr = max(
            bar["h"] - bar["l"],
            abs(bar["h"] - prev_close),
            abs(bar["l"] - prev_close),
        )
        trs.append(tr)
        prev_close = bar["c"]
    return sum(trs) / len(trs) if trs else 0.0


def _validation_idx_bull(klines: list, ob_idx: int, ob_high: float,
                         window: int) -> int | None:
    """Индекс ПЕРВОГО бара, пробивающего ob_high (validation бар), или None."""
    end = min(len(klines), ob_idx + 1 + window)
    for j in range(ob_idx + 1, end):
        if klines[j]["h"] > ob_high:
            return j
    return None


def _validation_idx_bear(klines: list, ob_idx: int, ob_low: float,
                         window: int) -> int | None:
    """Индекс ПЕРВОГО бара, пробивающего ob_low (validation бар), или None."""
    end = min(len(klines), ob_idx + 1 + window)
    for j in range(ob_idx + 1, end):
        if klines[j]["l"] < ob_low:
            return j
    return None


def _is_mitigated_bull(klines: list, start_idx: int,
                       body_low: float, body_high: float,
                       ob_low: float) -> bool:
    """
    Bullish OB mitigated, если ПОСЛЕ validation:
      • цена вернулась в body (overlap бара с [body_low, body_high]), ИЛИ
      • close ниже OB low (invalidation — тоже «отработан»)
    start_idx должен быть БАРОМ ПОСЛЕ validation, иначе сам leg-up
    «вмитигирует» свой собственный OB.
    """
    for j in range(start_idx, len(klines)):
        bar = klines[j]
        if bar["l"] <= body_high and bar["h"] >= body_low:
            return True
        if bar["c"] < ob_low:
            return True
    return False


def _is_mitigated_bear(klines: list, start_idx: int,
                       body_low: float, body_high: float,
                       ob_high: float) -> bool:
    """Bearish OB mitigated после validation: цена вернулась в body или close > high."""
    for j in range(start_idx, len(klines)):
        bar = klines[j]
        if bar["h"] >= body_low and bar["l"] <= body_high:
            return True
        if bar["c"] > ob_high:
            return True
    return False


def find_order_blocks(
    klines: list,
    lookback: int = DEFAULT_LOOKBACK,
    min_body_atr: float = DEFAULT_MIN_BODY_ATR,
    validation_window: int = DEFAULT_VALIDATION_WINDOW,
    atr_window: int = DEFAULT_ATR_WINDOW,
) -> list[OrderBlock]:
    """
    Поиск всех validated Order Blocks в последних `lookback` барах.
    Возвращает OBs с пометками validated/mitigated; фильтрацию неотработанных
    делает caller (см. latest_ob_test).
    """
    if len(klines) < validation_window + 2:
        return []

    atr = _atr_proxy(klines, atr_window)
    if atr <= 0:
        return []

    obs: list[OrderBlock] = []
    start = max(0, len(klines) - lookback)
    # OB-свеча должна оставлять хотя бы validation_window баров после себя
    end_scan = len(klines) - 1

    for i in range(start, end_scan):
        bar = klines[i]
        o, c, h, lo = bar["o"], bar["c"], bar["h"], bar["l"]
        body = abs(c - o)
        if body < min_body_atr * atr:
            continue

        body_high = max(o, c)
        body_low  = min(o, c)

        if c < o:
            # Bullish OB candidate: downclose
            val_idx = _validation_idx_bull(klines, i, h, validation_window)
            if val_idx is None:
                continue
            mit = _is_mitigated_bull(klines, val_idx + 1,
                                     body_low, body_high, lo)
            obs.append(OrderBlock(
                direction="bull", candle_idx=i,
                high=float(h), low=float(lo),
                open=float(o), close=float(c),
                body_high=float(body_high), body_low=float(body_low),
                body_atr=round(body / atr, 2),
                validated=True, mitigated=mit,
            ))
        elif c > o:
            # Bearish OB candidate: upclose
            val_idx = _validation_idx_bear(klines, i, lo, validation_window)
            if val_idx is None:
                continue
            mit = _is_mitigated_bear(klines, val_idx + 1,
                                     body_low, body_high, h)
            obs.append(OrderBlock(
                direction="bear", candle_idx=i,
                high=float(h), low=float(lo),
                open=float(o), close=float(c),
                body_high=float(body_high), body_low=float(body_low),
                body_atr=round(body / atr, 2),
                validated=True, mitigated=mit,
            ))

    return obs


def latest_ob_test(
    klines: list,
    lookback: int = DEFAULT_LOOKBACK,
    min_body_atr: float = DEFAULT_MIN_BODY_ATR,
    validation_window: int = DEFAULT_VALIDATION_WINDOW,
) -> OrderBlock | None:
    """
    Самый свежий UNMITIGATED OB, который ТЕКУЩИЙ (последний) бар тестирует.

    Test (entry trigger по PDF):
      • Bullish OB: low текущего бара ≤ body_high OB И close выше OB.low.
      • Bearish OB: high текущего бара ≥ body_low OB И close ниже OB.high.

    Mitigation проверяется ТОЛЬКО по предыдущим барам (klines[:-1]) — иначе
    сам entry-бар, заходящий в body, мгновенно помечал бы OB как mitigated.
    """
    if len(klines) < 2:
        return None

    # Считаем OBs по истории БЕЗ последнего бара
    obs = find_order_blocks(klines[:-1], lookback, min_body_atr,
                            validation_window)
    if not obs:
        return None

    last = klines[-1]
    last_low   = last["l"]
    last_high  = last["h"]
    last_close = last["c"]

    candidates = [ob for ob in obs if not ob.mitigated]
    for ob in reversed(candidates):
        if ob.direction == "bull":
            if last_low <= ob.body_high and last_close > ob.low:
                return ob
        else:
            if last_high >= ob.body_low and last_close < ob.high:
                return ob
    return None
