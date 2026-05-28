"""
regime.py — рыночный режим: накопление / распределение + позиционирование.

Отвечает на вопрос «где набирать, а где скидывать» как крупный игрок:
  • Накопление — крупный покупатель абсорбирует продажи у низов диапазона:
    цена прижата вниз, диапазон сжимается, но CVD растёт (покупки не двигают
    цену = кто-то крупный набирает). → bias long.
  • Распределение — зеркально у верхов: цена держится, CVD падает (раздача в
    силу). → bias short.
  • Markup / Markdown — фаза тренда (расширение диапазона + поток в сторону).

Плюс «кто в ловушке» (positioning) из OI / funding / L-S / ликвидаций:
  • Цена↓ + OI↑ + лонги перегружены + funding>0 → trapped_longs (топливо вниз).
  • Цена↑ + OI↑ + шорты перегружены + funding<0 → trapped_shorts (топливо вверх).

Premium / Discount (ICT): позиция цены в дилинг-диапазоне относительно 50%.
Лонг предпочтительнее в discount, шорт — в premium.

Без внешних зависимостей. Свечи — dict {"o","h","l","c","v"}.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ─── Параметры ────────────────────────────────────────────────────────────

RANGE_LOOKBACK     = 80     # баров 1h для дилинг-диапазона
COMPRESSION_WINDOW = 14     # окно для сравнения «свежий» vs «старый» диапазон
COMPRESSION_RATIO  = 0.75   # recent_range < 0.75×old_range → сжатие
EXPANSION_RATIO    = 1.30   # recent_range > 1.30×old_range → расширение

PREMIUM_THRESHOLD  = 0.62   # позиция в диапазоне > 0.62 → premium
DISCOUNT_THRESHOLD = 0.38   # < 0.38 → discount

FUNDING_CROWDED    = 0.0003 # |funding| выше → сторона перегружена
LS_CROWDED         = 58.0   # доля лонгов/шортов % выше → перекос


@dataclass
class Regime:
    phase:        str              # accumulation/distribution/markup/markdown/neutral
    bias:         str              # long/short/neutral
    zone:         str              # premium/discount/equilibrium
    positioning:  str              # trapped_longs/trapped_shorts/balanced
    range_state:  str              # compressing/expanding/normal
    pos_in_range: float            # 0..1 (0=низ диапазона, 1=верх)
    confidence:   int              # 0-100 сила прочтения режима
    notes:        list = field(default_factory=list)

    def summary(self) -> str:
        return (f"{self.phase} · {self.zone} · {self.positioning} "
                f"(bias {self.bias}, conf {self.confidence})")


# ─── Вспомогательные расчёты ──────────────────────────────────────────────

def dealing_range(candles: list, lookback: int = RANGE_LOOKBACK) -> dict:
    """Дилинг-диапазон: hi/lo/eq и позиция цены (0=низ, 1=верх)."""
    seg = candles[-lookback:] if len(candles) > lookback else candles
    if not seg:
        return {"hi": 0, "lo": 0, "eq": 0, "pos": 0.5}
    hi = max(c["h"] for c in seg)
    lo = min(c["l"] for c in seg)
    price = candles[-1]["c"]
    rng = hi - lo
    pos = (price - lo) / rng if rng > 0 else 0.5
    return {"hi": hi, "lo": lo, "eq": (hi + lo) / 2,
            "pos": max(0.0, min(1.0, pos))}


def range_state(candles: list, window: int = COMPRESSION_WINDOW) -> str:
    """Сжимается / расширяется / норма — по сравнению диапазонов окон."""
    if len(candles) < window * 2:
        return "normal"
    recent = candles[-window:]
    older  = candles[-2 * window:-window]
    r_rng = max(c["h"] for c in recent) - min(c["l"] for c in recent)
    o_rng = max(c["h"] for c in older) - min(c["l"] for c in older)
    if o_rng <= 0:
        return "normal"
    ratio = r_rng / o_rng
    if ratio < COMPRESSION_RATIO:
        return "compressing"
    if ratio > EXPANSION_RATIO:
        return "expanding"
    return "normal"


def _zone_from_pos(pos: float) -> str:
    if pos >= PREMIUM_THRESHOLD:
        return "premium"
    if pos <= DISCOUNT_THRESHOLD:
        return "discount"
    return "equilibrium"


def classify_positioning(market: dict) -> tuple:
    """
    Возвращает (positioning, notes[]). Определяет «кто в ловушке» из
    OI / funding / L-S / ликвидаций.
    """
    notes = []
    bybit = market.get("bybit", {}) or {}
    ls    = market.get("ls_ratio", {}) or {}
    liqs  = market.get("liquidations", {}) or {}

    funding   = float(bybit.get("funding", 0) or 0)
    oi_chg    = float(bybit.get("oi_chg", 0) or 0)
    chg_24h   = float(market.get("change_24h", 0) or 0)
    long_pct  = ls.get("bnb_long") or ls.get("bybit_long")

    longs_crowded  = (long_pct is not None and long_pct >= LS_CROWDED) or \
                     funding > FUNDING_CROWDED
    shorts_crowded = (long_pct is not None and long_pct <= (100 - LS_CROWDED)) or \
                     funding < -FUNDING_CROWDED

    # Цена падает, лонги перегружены, позиции растут → лонги в ловушке
    if chg_24h < 0 and longs_crowded and oi_chg >= 0:
        notes.append("Лонги в ловушке: цена↓ + OI↑ + funding/перекос лонгов")
        return "trapped_longs", notes
    if chg_24h > 0 and shorts_crowded and oi_chg >= 0:
        notes.append("Шорты в ловушке: цена↑ + OI↑ + funding/перекос шортов")
        return "trapped_shorts", notes

    # Подсказка от ликвидаций (кого недавно вынесли)
    dom = liqs.get("liq_dom")
    if dom == "long" and liqs.get("liq_total_usd", 0) > 0:
        notes.append("Недавно вынесли лонги (flush) — топливо для отскока")
    elif dom == "short" and liqs.get("liq_total_usd", 0) > 0:
        notes.append("Недавно вынесли шорты — топливо для отката")

    return "balanced", notes


# ─── Основной классификатор ───────────────────────────────────────────────

def classify_regime(market: dict) -> Regime:
    """
    Главная функция: определяет фазу рынка, зону, позиционирование и bias.
    Использует CVD + дилинг-диапазон + сжатие + позиционирование.
    """
    klines = market.get("_klines", {}) or {}
    k1h = klines.get("60") or []
    cvd = market.get("cvd", {}) or {}

    notes = []
    dr = dealing_range(k1h)
    pos = dr["pos"]
    zone = _zone_from_pos(pos)
    rstate = range_state(k1h)

    cvd_trend   = cvd.get("trend", "unknown")
    price_trend = cvd.get("price_trend", "unknown")
    divergence  = cvd.get("divergence", False)

    positioning, pos_notes = classify_positioning(market)
    notes.extend(pos_notes)

    phase = "neutral"
    bias = "neutral"
    conf = 40

    cvd_up = cvd_trend == "up"
    cvd_dn = cvd_trend == "down"

    # Накопление: discount + (сжатие или CVD-абсорбция) + поток вверх/дивергенция
    if zone == "discount" and (cvd_up or divergence) and rstate != "expanding":
        phase, bias, conf = "accumulation", "long", 65
        notes.append("Накопление у низов: цена прижата, поток покупок абсорбируется")
        if rstate == "compressing":
            conf += 10
            notes.append("Диапазон сжимается — подготовка к импульсу")

    # Распределение: premium + (сжатие или CVD-слабость)
    elif zone == "premium" and (cvd_dn or divergence) and rstate != "expanding":
        phase, bias, conf = "distribution", "short", 65
        notes.append("Распределение у верхов: цена держится, поток слабеет (раздача)")
        if rstate == "compressing":
            conf += 10
            notes.append("Диапазон сжимается — подготовка к развороту/импульсу")

    # Markup: расширение + поток вверх + цена вверх
    elif rstate == "expanding" and cvd_up and price_trend == "up":
        phase, bias, conf = "markup", "long", 60
        notes.append("Markup: трендовое расширение вверх, поток подтверждает")

    # Markdown
    elif rstate == "expanding" and cvd_dn and price_trend == "down":
        phase, bias, conf = "markdown", "short", 60
        notes.append("Markdown: трендовое расширение вниз, поток подтверждает")

    else:
        notes.append(f"Нейтрально: zone={zone}, CVD={cvd_trend}, range={rstate}")

    # Позиционирование усиливает уверенность в контр-сторону ловушки
    if positioning == "trapped_longs" and bias == "short":
        conf = min(100, conf + 8)
    elif positioning == "trapped_shorts" and bias == "long":
        conf = min(100, conf + 8)

    return Regime(
        phase=phase, bias=bias, zone=zone, positioning=positioning,
        range_state=rstate, pos_in_range=round(pos, 3),
        confidence=min(100, conf), notes=notes,
    )
