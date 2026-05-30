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

import killzones
from liquidity import build_liquidity_map
from regime import classify_regime
from structure import confirmed_break_5m_15m

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
# Поднят 50→65 после калибровки по /stats 30д: bucket 75+ показал 8% winrate
# из-за систематической переоценки smart-money слоем, а bucket 50-59 — 20%.
# 65 отрезает оба низкокачественных хвоста; bucket 60-74 (56% wr) проходит.
MIN_CONFIDENCE_FOR_TRADE  = 65

# ─── Smart-money слой (liquidity map + regime) ────────────────────────────
# Корректировки confidence от order-flow контекста.
# После калибровки по /stats — АСИММЕТРИЧНЫЕ: бонусы вполовину, штрафы
# усилены, и бонусы начисляются только при подтверждённой confluence
# (>= SMART_MONEY_BONUS_MIN_CONFLUENCE). Это исключает накрутку слабых
# сетапов smart-money'ем в 75+ bucket с последующим сливом.
REGIME_ALIGN_BONUS      = 4    # было 8 — сигнал совпал с фазой рынка
REGIME_CONFLICT_PENALTY = 18   # было 12 — сигнал против фазы рынка
PREMIUM_DISCOUNT_BONUS  = 3    # было 6 — лонг в discount / шорт в premium
PREMIUM_DISCOUNT_PENALTY = 14  # было 8 — лонг в premium / шорт в discount
OVERHEAD_LIQ_PENALTY    = 16   # было 10 — сильный пул на пути входа

# Бонусы smart-money начисляются только если базовая confluence уже
# поддерживает сигнал. Иначе только штрафы (асимметрия в сторону осторожности).
SMART_MONEY_BONUS_MIN_CONFLUENCE = 60

# ─── Структурный гейт «retest-сигнал против режима» (P2) ──────────────────
# По /stats 30д FVG_BULL/BOS_BULL/EMA_CROSS_BULL = 48 сделок, средний R=-1.0R
# (0-7% winrate). Это retest-сетапы продолжения: в нисходящем тренде они
# систематически сливают. Гейт жёстко переводит в WAIT, если direction
# сигнала противоречит bias режима. Для контр-трендовых разворотных
# сигналов (LIQ_SWEEP, RSI_DIV) гейт НЕ срабатывает — это другой класс.
RETEST_BULL_PREFIXES = ("FVG_BULL", "BOS_BULL", "EMA_CROSS_BULL")
RETEST_BEAR_PREFIXES = ("FVG_BEAR", "BOS_BEAR", "EMA_CROSS_BEAR")

# ─── Этап 10 фаза 3: killzone + structure hard gate ───────────────────────
# По требованиям: сигнал торгуем только если он попал в узкое ICT killzone
# окно (Asia / London / NY AM / London Close) И на 5m+15m подтверждён слом
# структуры в ту же сторону. Без обоих подтверждений — жёсткий WAIT.
# Graceful fallback: если klines для 5m/15m недоступны — структурный гейт
# молчит (не блокирует), чтобы не валить сделки на временной нехватке данных.
KILLZONE_GATE_ENABLED  = True
STRUCTURE_GATE_ENABLED = True
STRUCTURE_SWING_LENGTH = 5
STRUCTURE_MAX_BARS_AGO_5M  = 20
STRUCTURE_MAX_BARS_AGO_15M = 10

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
                                    confidence, confluence_score=confluence_score,
                                    lmap=lmap)
    base["confidence"] = confidence

    # P2: жёсткий WAIT, если retest-сигнал противоречит bias режима.
    gate_reason = _regime_structural_gate(
        signal_type, direction, base.get("regime"))
    if gate_reason:
        base["verdict"] = "WAIT"
        base["reason"]  = gate_reason
        _strip_levels(base)
        return base

    # P3 (Этап 10 фаза 3): killzone + structure hard gate.
    gate_reason = _killzone_structure_gate(direction, market, base)
    if gate_reason:
        base["verdict"] = "WAIT"
        base["reason"]  = gate_reason
        _strip_levels(base)
        return base

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
                       confluence_score: int = 0,
                       lmap=None) -> int:
    """
    Накладывает order-flow контекст на сделку:
      • regime (накопление/распределение) — bias за/против сигнала
      • premium/discount — лонг дешевле в discount, шорт в premium
      • overhead liquidity — сильный пул прямо на пути = риск снятия+разворота
      • liquidity target — ближайший магнит для TP (для показа/LLM)

    Асимметрия (после калибровки): бонусы (regime-align, premium/discount)
    начисляются только если confluence_score >= SMART_MONEY_BONUS_MIN_CONFLUENCE.
    Штрафы (regime-conflict, premium long / discount short, overhead) —
    всегда. Это исключает накрутку слабых сигналов smart-money'ем.

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
    apply_bonus = confluence_score >= SMART_MONEY_BONUS_MIN_CONFLUENCE

    # 1) Режим за/против сигнала. Штраф — всегда; бонус — только при
    # подтверждённой confluence (>= SMART_MONEY_BONUS_MIN_CONFLUENCE).
    if reg.bias != "neutral":
        scaled = round((reg.confidence / 100) *
                       (REGIME_ALIGN_BONUS if reg.bias == direction
                        else REGIME_CONFLICT_PENALTY))
        if reg.bias == direction:
            if apply_bonus:
                adj += scaled
                factors.append(f"Режим ✅ {reg.phase} совпадает с сигналом")
        else:
            adj -= scaled
            risks.append(f"Режим ⚠️ {reg.phase} против сигнала "
                         f"(крупный игрок {reg.bias})")

    # 2) Premium / Discount. Бонусы — gated, штрафы — всегда.
    if reg.zone == "discount" and direction == "long":
        if apply_bonus:
            adj += PREMIUM_DISCOUNT_BONUS
            factors.append("Цена в discount — выгодная зона набора (long)")
    elif reg.zone == "premium" and direction == "short":
        if apply_bonus:
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

    # 5) Liquidity POC — плотнейшая по объёму ликвидность (метод BigBeluga)
    poc = lmap.liquidity_poc()
    if poc is not None:
        base["liq_poc"] = {"price": poc.price, "kind": poc.kind,
                           "side": poc.side, "volume": poc.volume}

    # Дедуп и обрезка для аккуратного вывода
    base["key_factors"] = list(dict.fromkeys(factors))[:5]
    base["veto_reasons"] = list(dict.fromkeys(risks))[:5]

    return max(0, min(100, confidence + adj))


# ─── Структурный гейт «retest против режима» (P2) ─────────────────────────

def _is_retest_bull(signal_type: str) -> bool:
    s = (signal_type or "").upper()
    return any(s.startswith(p) for p in RETEST_BULL_PREFIXES)


def _is_retest_bear(signal_type: str) -> bool:
    s = (signal_type or "").upper()
    return any(s.startswith(p) for p in RETEST_BEAR_PREFIXES)


def _killzone_structure_gate(direction: str, market: dict,
                             base: dict) -> str | None:
    """
    Этап 10 фаза 3: жёсткий WAIT, если LONG/SHORT сигнал НЕ удовлетворяет:
      • попадание в ICT killzone-окно (узкое окно повышенной активности), И
      • подтверждённый слом структуры (BOS/CHoCH) на 5m+15m в ту же сторону.

    Каждый сабгейт включается отдельным флагом. Сохраняет диагностику в
    base['killzone'] и base['structure'] для LLM/логов.

    Graceful fallback: при отсутствии klines/timestamp сабгейт молчит —
    лучше пропустить сделку, чем заблокировать её из-за временной нехватки.
    """
    if direction not in ("long", "short"):
        return None

    # — killzone subgate —
    if KILLZONE_GATE_ENABLED:
        ts = market.get("ts")  # tz-aware datetime; None → now() в killzones
        kz = killzones.active_killzone(ts)
        base["killzone"] = {
            "in": kz is not None,
            "name": kz.name if kz else None,
        }
        if kz is None:
            return ("Вне ICT killzone-окна — жёсткий WAIT "
                    "(Этап 10 P3 killzone gate)")

    # — structure subgate —
    if STRUCTURE_GATE_ENABLED:
        klines = market.get("_klines", {}) or {}
        k5  = klines.get("5")  or []
        k15 = klines.get("15") or []
        if not k5 or not k15:
            # Нет данных на одном из ТФ — graceful, не блокируем
            base["structure"] = {"available": False}
            return None
        confirmed = confirmed_break_5m_15m(
            k5, k15,
            swing_length=STRUCTURE_SWING_LENGTH,
            max_bars_ago_5m=STRUCTURE_MAX_BARS_AGO_5M,
            max_bars_ago_15m=STRUCTURE_MAX_BARS_AGO_15M,
        )
        expected = "bull" if direction == "long" else "bear"
        if confirmed is None:
            base["structure"] = {"available": True, "confirmed": False}
            return ("Нет подтверждённого слома структуры на 5m+15m — "
                    "жёсткий WAIT (Этап 10 P3 structure gate)")
        if confirmed["direction"] != expected:
            base["structure"] = {
                "available": True, "confirmed": True,
                "direction": confirmed["direction"],
            }
            return (f"Слом структуры идёт {confirmed['direction']}, "
                    f"сигнал {direction} — расхождение направлений, "
                    "жёсткий WAIT (Этап 10 P3 structure gate)")
        # Подтверждено и направление совпадает
        base["structure"] = {
            "available": True, "confirmed": True,
            "direction": confirmed["direction"],
            "kind_5m":  confirmed["kind_5m"],
            "kind_15m": confirmed["kind_15m"],
        }

    return None


def _regime_structural_gate(signal_type: str, direction: str,
                            regime: dict | None) -> str | None:
    """
    Возвращает причину жёсткого WAIT, если retest-сигнал продолжения тренда
    противоречит bias режима. Иначе None.

    Срабатывает только когда регим уверенно противоположен направлению —
    нейтральный режим / отсутствие данных НЕ блокирует сделку.
    Контр-трендовые сетапы (LIQ_SWEEP, RSI_DIV) НЕ блокируются — их класс
    подразумевает работу против тренда.
    """
    if regime is None:
        return None
    bias = regime.get("bias")
    phase = regime.get("phase", "?")
    if direction == "long" and bias == "short" and _is_retest_bull(signal_type):
        return (f"Режим {phase} (bias short) против retest-bull сигнала "
                f"{signal_type} — жёсткий WAIT (P2 gate)")
    if direction == "short" and bias == "long" and _is_retest_bear(signal_type):
        return (f"Режим {phase} (bias long) против retest-bear сигнала "
                f"{signal_type} — жёсткий WAIT (P2 gate)")
    return None


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
