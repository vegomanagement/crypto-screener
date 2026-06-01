"""
htf_bias.py — Top-down PDA bias gate по канону ICT (Этап 12 фаза 2).

PDF SMC говорит: «If HTF is bullish but LTF is bearish, expect price to move
into HTF discount» — HTF диктует. Этот модуль определяет HTF bias из
multi-TF zone agreement и используется decision.py как hard-gate против
сигналов, идущих ПРОТИВ HTF.

Алгоритм:
  1. Для каждого HTF (60m, 240m, D) вычисляем dealing_range и zone
     ('premium' / 'discount' / 'equilibrium').
  2. Подсчёт голосов:
       • discount → vote long
       • premium  → vote short
       • equilibrium → no vote
  3. Bias:
       • strong  — 3/3 голосов в одну сторону (high-conviction)
       • moderate — 2/3 в одну сторону
       • weak     — 1/3 (один TF выделяется)
       • neutral  — все голоса распылены или нет данных

Hard-gate срабатывает только при strong против direction.
Moderate просто пишется в decision dict как контекст (для LLM/диагностики).

Использует regime.dealing_range и пороги PREMIUM/DISCOUNT_THRESHOLD —
не дублирует, а композирует.
"""

from __future__ import annotations

from dataclasses import dataclass

from regime import (
    DISCOUNT_THRESHOLD,
    PREMIUM_THRESHOLD,
    dealing_range,
)

__all__ = [
    "HTFBias",
    "compute_htf_bias",
    "HTF_TIMEFRAMES",
    "MIN_BARS_PER_TF",
]

# TF в priority-порядке (сначала длиннее → больше веса).
HTF_TIMEFRAMES = ("D", "240", "60")
MIN_BARS_PER_TF = 20   # меньше — TF считаем «без данных»


@dataclass
class HTFBias:
    """Композитный HTF bias."""
    strength:   str                  # "strong" | "moderate" | "weak" | "neutral"
    direction:  str                  # "long" | "short" | "neutral"
    zones:      dict                 # {tf: 'premium'/'discount'/'equilibrium'/'unknown'}
    votes_long: int                  # сколько TF голосуют long (discount zone)
    votes_short: int                 # сколько TF голосуют short (premium)
    available_tfs: list              # TF с достаточными данными


def _zone_of(klines: list) -> str:
    """Вернуть зону для klines: 'premium' / 'discount' / 'equilibrium' /
    'unknown'."""
    if len(klines) < MIN_BARS_PER_TF:
        return "unknown"
    dr = dealing_range(klines)
    pos = dr.get("pos", 0.5)
    if pos >= PREMIUM_THRESHOLD:
        return "premium"
    if pos <= DISCOUNT_THRESHOLD:
        return "discount"
    return "equilibrium"


def compute_htf_bias(market: dict) -> HTFBias:
    """
    Композитный HTF bias из multi-TF zone agreement (60 + 240 + D klines).
    Если ни один TF не доступен — возвращает neutral bias.
    """
    klines_by_tf = (market.get("_klines") or {})

    zones: dict[str, str] = {}
    available: list[str] = []
    for tf in HTF_TIMEFRAMES:
        kl = klines_by_tf.get(tf) or []
        z = _zone_of(kl)
        zones[tf] = z
        if z != "unknown":
            available.append(tf)

    votes_long  = sum(1 for tf in available if zones[tf] == "discount")
    votes_short = sum(1 for tf in available if zones[tf] == "premium")
    n = len(available)

    if n == 0:
        return HTFBias(strength="neutral", direction="neutral",
                       zones=zones, votes_long=0, votes_short=0,
                       available_tfs=[])

    if votes_long == n and n >= 2:
        strength, direction = "strong", "long"
    elif votes_short == n and n >= 2:
        strength, direction = "strong", "short"
    elif votes_long >= 2 and votes_short == 0:
        strength, direction = "moderate", "long"
    elif votes_short >= 2 and votes_long == 0:
        strength, direction = "moderate", "short"
    elif votes_long > 0 and votes_short == 0:
        strength, direction = "weak", "long"
    elif votes_short > 0 and votes_long == 0:
        strength, direction = "weak", "short"
    else:
        strength, direction = "neutral", "neutral"

    return HTFBias(
        strength=strength,
        direction=direction,
        zones=zones,
        votes_long=votes_long,
        votes_short=votes_short,
        available_tfs=available,
    )
