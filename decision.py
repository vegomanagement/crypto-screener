"""
decision.py — детерминистский торговый движок.

Принимает сырой сигнал TradingView + рыночный контекст + confluence score
и выдаёт ЖЁСТКИЙ verdict с уровнями Entry/SL/TP/RR.

LLM на следующем этапе будет ТОЛЬКО объяснять verdict, не менять его —
это устраняет противоречия в выводе.

Формат уровней: ATR-based, риск якорим к цене на момент сигнала.
  entry_zone = price ± 0.3 × ATR     (зона для лимитной заявки)
  sl         = price ∓ 1.0 × ATR     (risk = 1.0 × ATR)
  tp1/tp2/tp3 = price ± 1.5 / 2.5 / 4.0 × ATR
  → RR(TP1) = 1.5,  RR(TP2) = 2.5,  RR(TP3) = 4.0
"""

from typing import List

from liquidity import build_liquidity_map
from regime import classify_regime

# ─── ATR коэффициенты ─────────────────────────────────────────────────────
ATR_ENTRY_ZONE = 0.3   # ширина entry zone (для лимитной заявки)
ATR_SL_DIST    = 1.0   # SL от цены — определяет величину риска
ATR_TP1_DIST   = 1.5   # → RR = 1.5
ATR_TP2_DIST   = 2.5   # → RR = 2.5
ATR_TP3_DIST   = 4.0   # → RR = 4.0

# ─── Veto / гейтинг пороги ────────────────────────────────────────────────
CONFLUENCE_WAIT_THRESHOLD = 55
MIN_RR_FOR_TRADE          = 1.5
MAX_CONTRADICTIONS        = 3

# Минимальный ФИНАЛЬНЫЙ confidence (после вычета штрафов вето) для торговли.
# Раньше гейт был только по confluence_score, поэтому сильно завотированные
# сигналы (confluence 56, штрафы 28 → confidence 28) всё равно слались как
# LONG/SHORT. Теперь итоговый confidence ниже порога → SKIP (молча).
MIN_CONFIDENCE_FOR_TRADE  = 50

# ─── Smart-money слой (liquidity map + regime) ────────────────────────────
# Корректировки confidence от order-flow контекста. Все — настраиваемые,
# калибруются через /stats (по confidence-бакетам).
REGIME_ALIGN_BONUS      = 8    # сигнал совпал с фазой рынка (накопл/распред)
REGIME_CONFLICT_PENALTY = 12   # сигнал против фазы рынка
PREMIUM_DISCOUNT_BONUS  = 6    # лонг в discount / шорт в premium
PREMIUM_DISCOUNT_PENALTY = 8   # лонг в premium / шорт в discount
OVERHEAD_LIQ_PENALTY    = 10   # сильный пул ликвидности прямо на пути входа

# ─── Liquidity-aware levels (Этап 8) ──────────────────────────────────────
# Двигаем TP/SL к карте ликвидности с жёсткими guardrails.
LIQUIDITY_LEVELS_ENABLED = True
TP_FRONTRUN_ATR    = 0.15   # TP не доходя TP_FRONTRUN×ATR до пула (front-run)
SL_BUFFER_ATR      = 0.25   # SL за пул + SL_BUFFER×ATR (sweep-safe)
SL_MAX_ATR         = 2.0    # cap: риск не дальше SL_MAX×ATR от entry
MIN_POOL_STRENGTH_TP = 3    # пул для TP должен быть не слабее
MIN_POOL_STRENGTH_SL = 3    # пул для SL должен быть не слабее
MIN_TP_GAP_ATR     = 0.3    # минимальный зазор между TP при монотонизации

# Veto штрафы к confidence
RSI_OVERBOUGHT_LONG  = 75
RSI_OVERSOLD_SHORT   = 25
RSI_VETO_PENALTY     = 20

MTF_AGAINST_PENALTY  = 15

FUNDING_OVERHEATED   = 0.0005  # 0.05%
FUNDING_VETO_PENALTY = 10

MACD_VETO_PENALTY    = 8
RSI_DIV_VETO_PENALTY = 12
TZ_EXTREME_PENALTY   = 10

# ─── Парсинг направления из типа сигнала ─────────────────────────────────
LONG_TOKENS  = ("BULL", "LONG", "SWEEP_L", "EQL")
SHORT_TOKENS = ("BEAR", "SHORT", "SWEEP_H", "EQH")


def parse_direction(signal_type: str) -> str:
    s = (signal_type or "").upper()
    if any(t in s for t in LONG_TOKENS):
        return "long"
    if any(t in s for t in SHORT_TOKENS):
        return "short"
    return "neutral"


# ─── Основной движок ──────────────────────────────────────────────────────

def make_decision(
    signal_type: str,
    price: float,
    market: dict,
    mtf: dict,
    confluence_score: int,
    confluence_factors: List[str],
) -> dict:
    """
    Возвращает структурированный verdict:

      verdict:    "LONG" | "SHORT" | "WAIT" | "SKIP"
      direction:  "long" | "short" | "neutral"
      entry:      {"min": float, "max": float} | None
      sl, tp1-3:  float | None
      rr1-3:      float | None
      confidence: int 0-100
      veto_reasons: list[str]
      key_factors:  list[str]
      atr:        float
      reason:     str
    """
    direction = parse_direction(signal_type)
    indic     = market.get("indicators", {}) or {}
    atr       = float(indic.get("atr", 0) or 0)

    base = {
        "verdict":      "WAIT",
        "direction":    direction,
        "entry":        None,
        "sl":           None,
        "tp1": None, "tp2": None, "tp3": None,
        "rr1": None, "rr2": None, "rr3": None,
        "confidence":   0,
        "veto_reasons": [],
        "key_factors":  [],
        "atr":          atr,
        "reason":       "",
    }

    if direction == "neutral":
        base["reason"] = "Сигнал без направления (info-only)"
        return base

    if atr <= 0 or price <= 0:
        base["verdict"] = "SKIP"
        base["reason"]  = "Нет ATR/цены — невозможно рассчитать риск"
        return base

    levels = _compute_levels(price, atr, direction)
    # Liquidity-aware levels: двигаем TP/SL к карте ликвидности (Этап 8).
    lmap = _safe_liquidity_map(market)
    levels = apply_liquidity_levels(levels, lmap, price, atr, direction)
    base.update(levels)

    vetoes = _collect_vetoes(direction, market, mtf)
    base["veto_reasons"] = [v["text"] for v in vetoes]

    penalty    = sum(v["penalty"] for v in vetoes)
    confidence = max(0, min(100, int(confluence_score) - penalty))
    base["confidence"]  = confidence
    base["key_factors"] = _extract_key_factors(confluence_factors, limit=3)

    rr1 = levels.get("rr1") or 0
    if rr1 < MIN_RR_FOR_TRADE:
        base["verdict"] = "SKIP"
        base["reason"]  = f"RR до TP1 = {rr1:.2f} < {MIN_RR_FOR_TRADE}"
        _strip_levels(base)
        return base

    if confluence_score < CONFLUENCE_WAIT_THRESHOLD:
        base["verdict"] = "WAIT"
        base["reason"]  = (f"Confluence {confluence_score}/100 < "
                           f"{CONFLUENCE_WAIT_THRESHOLD} — мало подтверждений")
        _strip_levels(base)
        return base

    if len(vetoes) >= MAX_CONTRADICTIONS:
        base["verdict"] = "WAIT"
        base["reason"]  = (f"{len(vetoes)} противоречий — лучше переждать "
                           f"подтверждение")
        _strip_levels(base)
        return base

    # Smart-money слой: liquidity map + regime корректируют confidence
    # ДО финального порога, чтобы режим/ликвидность могли отсечь сделку.
    # Переиспользуем уже построенную карту ликвидности (lmap).
    confidence = _apply_smart_money(base, market, direction, atr, price,
                                    confidence, lmap=lmap)
    base["confidence"] = confidence

    if confidence < MIN_CONFIDENCE_FOR_TRADE:
        base["verdict"] = "SKIP"
        base["reason"]  = (f"Confidence {confidence}/100 < "
                           f"{MIN_CONFIDENCE_FOR_TRADE} — слишком слабый сигнал")
        _strip_levels(base)
        return base

    base["verdict"] = "LONG" if direction == "long" else "SHORT"
    base["reason"]  = (f"Confluence {confluence_score}/100 · "
                       f"RR(TP1)={rr1:.2f} · confidence {confidence}/100")
    return base


# ─── Smart-money слой ──────────────────────────────────────────────────────

def _apply_smart_money(base: dict, market: dict, direction: str,
                       atr: float, price: float, confidence: int,
                       lmap=None) -> int:
    """
    Накладывает order-flow контекст на сделку:
      • regime (накопление/распределение) — bias за/против сигнала
      • premium/discount — лонг дешевле в discount, шорт в premium
      • overhead liquidity — сильный пул прямо на пути = риск снятия+разворота
      • liquidity target — ближайший магнит для TP (для показа/LLM)

    lmap — уже построенная карта ликвидности (переиспользуем из make_decision).
    Возвращает скорректированный confidence. Заполняет base['regime'],
    base['liquidity'], base['liq_target'] и добавляет факторы/риски.
    Безопасна: при нехватке данных (нет klines) ничего не ломает.
    """
    try:
        if lmap is None:
            lmap = build_liquidity_map(market)
        reg = classify_regime(market)
    except Exception:
        return confidence

    factors = base.setdefault("key_factors", [])
    risks   = base.setdefault("veto_reasons", [])

    # Структурный контекст для отображения и LLM-нарратива
    base["regime"] = {
        "phase":       reg.phase,
        "bias":        reg.bias,
        "zone":        reg.zone,
        "positioning": reg.positioning,
        "range_state": reg.range_state,
        "confidence":  reg.confidence,
        "summary":     reg.summary(),
        "notes":       reg.notes[:3],
    }
    base["liquidity"] = lmap.summary()

    adj = 0

    # 1) Режим за/против сигнала
    if reg.bias != "neutral":
        scaled = round((reg.confidence / 100) *
                       (REGIME_ALIGN_BONUS if reg.bias == direction
                        else REGIME_CONFLICT_PENALTY))
        if reg.bias == direction:
            adj += scaled
            factors.append(f"Режим ✅ {reg.phase} совпадает с сигналом")
        else:
            adj -= scaled
            risks.append(f"Режим ⚠️ {reg.phase} против сигнала "
                         f"(крупный игрок {reg.bias})")

    # 2) Premium / Discount
    if reg.zone == "discount" and direction == "long":
        adj += PREMIUM_DISCOUNT_BONUS
        factors.append("Цена в discount — выгодная зона набора (long)")
    elif reg.zone == "premium" and direction == "short":
        adj += PREMIUM_DISCOUNT_BONUS
        factors.append("Цена в premium — выгодная зона раздачи (short)")
    elif reg.zone == "premium" and direction == "long":
        adj -= PREMIUM_DISCOUNT_PENALTY
        risks.append("Лонг в premium-зоне — дорого, риск раздачи сверху")
    elif reg.zone == "discount" and direction == "short":
        adj -= PREMIUM_DISCOUNT_PENALTY
        risks.append("Шорт в discount-зоне — дёшево, риск набора снизу")

    # 3) Overhead liquidity — сильный пул прямо на пути
    block = lmap.overhead_block(direction, atr)
    if block is not None:
        adj -= OVERHEAD_LIQ_PENALTY
        risks.append(f"Пул ликвидности {block.label()} на пути — "
                     f"риск снятия и разворота")

    # 4) Liquidity target — магнит для TP (информативно)
    tgt = lmap.target(direction)
    if tgt is not None:
        base["liq_target"] = {"price": tgt.price, "kind": tgt.kind,
                              "strength": tgt.strength,
                              "dist_pct": tgt.dist_pct}
        factors.append(f"TP-магнит: {tgt.label()} (ликвидность)")

    # Дедуп и обрезка для аккуратного вывода
    base["key_factors"] = list(dict.fromkeys(factors))[:5]
    base["veto_reasons"] = list(dict.fromkeys(risks))[:5]

    return max(0, min(100, confidence + adj))


# ─── Liquidity-aware levels ────────────────────────────────────────────────

def _safe_liquidity_map(market: dict):
    """build_liquidity_map с защитой: None при ошибке/нехватке данных."""
    try:
        lmap = build_liquidity_map(market)
        return lmap if lmap.pools else None
    except Exception:
        return None


def _enforce_monotonic(tps: list, direction: str, anchor: float,
                       min_gap: float) -> list:
    """
    Гарантирует строгий порядок TP в сторону сделки с минимальным зазором.
    Для long: tp1 < tp2 < tp3 и все > anchor. Для short — зеркально.
    """
    out = []
    prev = anchor
    for t in tps:
        if t is None:
            t = prev + (min_gap if direction == "long" else -min_gap)
        if direction == "long":
            t = max(t, prev + min_gap)
        else:
            t = min(t, prev - min_gap)
        out.append(t)
        prev = t
    return out


def apply_liquidity_levels(levels: dict, lmap, price: float, atr: float,
                           direction: str) -> dict:
    """
    Корректирует SL/TP к карте ликвидности:
      • SL — за противоположный пул + буфер, но не дальше SL_MAX×ATR (cap).
      • TP — front-run сильных пулов в сторону сделки (не доходя до пула).
    Пересчитывает RR. Guardrail: если итоговый RR(TP1) < MIN_RR_FOR_TRADE —
    полный откат на исходные ATR-уровни.

    Чистая функция: при отсутствии пулов/данных возвращает levels без изменений.
    """
    if not LIQUIDITY_LEVELS_ENABLED or atr <= 0 or lmap is None:
        return levels

    out = dict(levels)
    digits = _price_digits(price)
    entry = levels.get("entry") or {}
    e_mid = ((entry.get("min", price) + entry.get("max", price)) / 2
             if entry else price)

    # ── SL: за противоположный пул, capped ───────────────────────────────
    sl = levels.get("sl")
    if direction == "long":
        pool = lmap.nearest_below(MIN_POOL_STRENGTH_SL)
        if pool is not None:
            cand = pool.price - SL_BUFFER_ATR * atr
            if cand < e_mid and (e_mid - cand) <= SL_MAX_ATR * atr:
                sl = round(cand, digits)
    else:
        pool = lmap.nearest_above(MIN_POOL_STRENGTH_SL)
        if pool is not None:
            cand = pool.price + SL_BUFFER_ATR * atr
            if cand > e_mid and (cand - e_mid) <= SL_MAX_ATR * atr:
                sl = round(cand, digits)
    out["sl"] = sl

    risk = abs(e_mid - sl) if sl is not None else 0

    # ── TP: front-run сильных пулов в сторону сделки ─────────────────────
    pools_dir = lmap.above() if direction == "long" else lmap.below()
    fr = TP_FRONTRUN_ATR * atr
    cand_tps = []
    for p in pools_dir:
        if p.strength < MIN_POOL_STRENGTH_TP:
            continue
        t = p.price - fr if direction == "long" else p.price + fr
        beyond = (t > e_mid) if direction == "long" else (t < e_mid)
        if beyond:
            cand_tps.append(round(t, digits))
        if len(cand_tps) == 3:
            break

    if cand_tps:
        atr_tps = [levels.get("tp1"), levels.get("tp2"), levels.get("tp3")]
        merged = [cand_tps[i] if i < len(cand_tps) else atr_tps[i]
                  for i in range(3)]
        merged = _enforce_monotonic(merged, direction, e_mid,
                                    MIN_TP_GAP_ATR * atr)
        out["tp1"], out["tp2"], out["tp3"] = (round(x, digits) for x in merged)

    # ── Пересчёт RR ──────────────────────────────────────────────────────
    if risk > 0:
        for i, key in enumerate(("tp1", "tp2", "tp3"), start=1):
            tp = out.get(key)
            out[f"rr{i}"] = round(abs(tp - e_mid) / risk, 2) if tp else None

    # ── Guardrail: не ухудшаем сделку ниже MIN_RR ────────────────────────
    if (out.get("rr1") or 0) < MIN_RR_FOR_TRADE:
        return levels

    return out


# ─── Расчёт уровней (ATR-based) ───────────────────────────────────────────

def _compute_levels(price: float, atr: float, direction: str) -> dict:
    zone_d = atr * ATR_ENTRY_ZONE
    sl_d   = atr * ATR_SL_DIST
    t1_d   = atr * ATR_TP1_DIST
    t2_d   = atr * ATR_TP2_DIST
    t3_d   = atr * ATR_TP3_DIST

    digits = _price_digits(price)

    def r(x):
        return round(x, digits)

    entry_min = r(price - zone_d)
    entry_max = r(price + zone_d)

    if direction == "long":
        sl   = r(price - sl_d)
        tp1  = r(price + t1_d)
        tp2  = r(price + t2_d)
        tp3  = r(price + t3_d)
        risk = price - sl
    else:
        sl   = r(price + sl_d)
        tp1  = r(price - t1_d)
        tp2  = r(price - t2_d)
        tp3  = r(price - t3_d)
        risk = sl - price

    if risk <= 0:
        return {
            "entry": {"min": entry_min, "max": entry_max},
            "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "rr1": 0.0, "rr2": 0.0, "rr3": 0.0,
        }

    return {
        "entry": {"min": entry_min, "max": entry_max},
        "sl":  sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "rr1": round(abs(tp1 - price) / risk, 2),
        "rr2": round(abs(tp2 - price) / risk, 2),
        "rr3": round(abs(tp3 - price) / risk, 2),
    }


def _strip_levels(base: dict) -> None:
    """Для WAIT/SKIP убираем Entry/SL/TP — нечего показывать."""
    for k in ("entry", "sl", "tp1", "tp2", "tp3", "rr1", "rr2", "rr3"):
        base[k] = None


# ─── Сбор противоречий ────────────────────────────────────────────────────

def _collect_vetoes(direction: str, market: dict, mtf: dict) -> list:
    vetoes = []
    indic  = market.get("indicators", {}) or {}

    rsi = indic.get("rsi")
    if rsi is not None:
        if direction == "long" and rsi > RSI_OVERBOUGHT_LONG:
            vetoes.append({
                "text": f"RSI {rsi:.0f} перекуплен (>{RSI_OVERBOUGHT_LONG})",
                "penalty": RSI_VETO_PENALTY,
            })
        elif direction == "short" and rsi < RSI_OVERSOLD_SHORT:
            vetoes.append({
                "text": f"RSI {rsi:.0f} перепродан (<{RSI_OVERSOLD_SHORT})",
                "penalty": RSI_VETO_PENALTY,
            })

    if mtf and mtf.get("aligned") == 0:
        total = mtf.get("total", 3)
        vetoes.append({
            "text": f"MTF: все {total} ТФ против направления",
            "penalty": MTF_AGAINST_PENALTY,
        })

    bybit = market.get("bybit", {}) or {}
    fr    = bybit.get("funding")
    if fr is not None:
        if direction == "long" and fr > FUNDING_OVERHEATED:
            vetoes.append({
                "text": f"Funding {fr*100:+.3f}% — лонги перегреты",
                "penalty": FUNDING_VETO_PENALTY,
            })
        elif direction == "short" and fr < -FUNDING_OVERHEATED:
            vetoes.append({
                "text": f"Funding {fr*100:+.3f}% — шорты перегреты",
                "penalty": FUNDING_VETO_PENALTY,
            })

    macd = indic.get("macd", {}) or {}
    trend = macd.get("trend")
    if direction == "long" and trend == "bear":
        vetoes.append({"text": "MACD: медвежий тренд",
                       "penalty": MACD_VETO_PENALTY})
    elif direction == "short" and trend == "bull":
        vetoes.append({"text": "MACD: бычий тренд",
                       "penalty": MACD_VETO_PENALTY})

    rsi_div = indic.get("rsi_div", "none")
    if direction == "long" and rsi_div == "bearish":
        vetoes.append({"text": "RSI медвежья дивергенция",
                       "penalty": RSI_DIV_VETO_PENALTY})
    elif direction == "short" and rsi_div == "bullish":
        vetoes.append({"text": "RSI бычья дивергенция",
                       "penalty": RSI_DIV_VETO_PENALTY})

    for tf_name, tz_key in [("1H", "turtle_1h"), ("4H", "turtle_4h")]:
        tz = market.get(tz_key, {}) or {}
        z  = tz.get("zone", "")
        if direction == "long" and z == "extreme_upper":
            vetoes.append({
                "text": f"TZ {tf_name}: extreme upper — цена перегрета",
                "penalty": TZ_EXTREME_PENALTY,
            })
        elif direction == "short" and z == "extreme_lower":
            vetoes.append({
                "text": f"TZ {tf_name}: extreme lower — цена перепродана",
                "penalty": TZ_EXTREME_PENALTY,
            })

    return vetoes


# ─── Топ-факторы «за» направление ─────────────────────────────────────────

def _extract_key_factors(factors: list, limit: int = 3) -> list:
    if not factors:
        return []
    positive = [f for f in factors if "✅" in f]
    return positive[:limit]


# ─── Helpers для форматирования цены ──────────────────────────────────────

def _price_digits(price: float) -> int:
    p = abs(price)
    if p >= 1000:
        return 2
    if p >= 10:
        return 3
    if p >= 1:
        return 4
    if p >= 0.01:
        return 5
    return 7


def _fmt_price(p) -> str:
    if p is None:
        return "—"
    ap = abs(p)
    if ap >= 1000:
        return f"{p:,.2f}"
    if ap >= 10:
        return f"{p:,.3f}"
    if ap >= 1:
        return f"{p:,.4f}"
    if ap >= 0.01:
        return f"{p:.5f}"
    return f"{p:.7f}"


# ─── Telegram-форматирование шапки решения ────────────────────────────────

VERDICT_EMOJI = {
    "LONG":  "🟢",
    "SHORT": "🔴",
    "WAIT":  "⚪",
    "SKIP":  "⏭️",
}

VERDICT_TITLE = {
    "LONG":  "LONG",
    "SHORT": "SHORT",
    "WAIT":  "WAIT — переждать",
    "SKIP":  "SKIP — не торговать",
}


def format_decision_header(decision: dict) -> str:
    """
    Короткая шапка для Telegram-сообщения.
    LONG/SHORT — Entry/SL/TP/RR/Confidence + ключевые факторы.
    WAIT/SKIP  — причина + список противоречий (если есть).
    """
    v   = decision.get("verdict", "WAIT")
    em  = VERDICT_EMOJI.get(v, "❔")
    lab = VERDICT_TITLE.get(v, v)

    if v in ("WAIT", "SKIP"):
        lines = [f"{em} <b>{lab}</b>",
                 f"💬 {decision.get('reason','')}"]
        if decision.get("veto_reasons"):
            lines.append("⚠️ Против: " + " · ".join(decision["veto_reasons"][:3]))
        return "\n".join(lines)

    entry = decision.get("entry") or {}
    e_min = entry.get("min")
    e_max = entry.get("max")

    rr1   = decision.get("rr1") or 0
    conf  = decision.get("confidence", 0)

    lines = [
        (f"{em} <b>{lab}</b>  ·  RR(TP1): <b>{rr1}</b>  ·  "
         f"Confidence: <b>{conf}/100</b>"),
        f"📍 Entry: <code>{_fmt_price(e_min)} — {_fmt_price(e_max)}</code>",
        f"🛑 SL:    <code>{_fmt_price(decision.get('sl'))}</code>",
        (f"🎯 TP1:   <code>{_fmt_price(decision.get('tp1'))}</code>  "
         f"(RR {decision.get('rr1')})"),
        (f"🎯 TP2:   <code>{_fmt_price(decision.get('tp2'))}</code>  "
         f"(RR {decision.get('rr2')})"),
        (f"🎯 TP3:   <code>{_fmt_price(decision.get('tp3'))}</code>  "
         f"(RR {decision.get('rr3')})"),
    ]
    reg = decision.get("regime")
    if reg and reg.get("phase") and reg["phase"] != "neutral":
        phase_ru = {
            "accumulation": "🟢 Накопление",
            "distribution": "🔴 Распределение",
            "markup":       "📈 Markup",
            "markdown":     "📉 Markdown",
        }.get(reg["phase"], reg["phase"])
        lines.append(f"🏦 Режим: <b>{phase_ru}</b> · зона {reg.get('zone','?')}"
                     f" · {reg.get('positioning','?')}")
    lt = decision.get("liq_target")
    if lt:
        lines.append(f"🧲 Магнит ликвидности: <code>{_fmt_price(lt['price'])}</code>"
                     f" ({lt['kind']}, {lt['dist_pct']:+.2f}%)")
    if decision.get("key_factors"):
        lines.append("✅ За: " + " · ".join(decision["key_factors"][:3]))
    if decision.get("veto_reasons"):
        lines.append("⚠️ Риски: " + " · ".join(decision["veto_reasons"][:3]))
    return "\n".join(lines)
