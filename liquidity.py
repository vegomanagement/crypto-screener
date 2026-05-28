"""
liquidity.py — карта ликвидности (smart money / order-flow слой).

Крупный игрок мыслит не индикаторами, а ликвидностью: где лежат кластеры
стопов (equal highs/lows, prior day/week H/L, круглые уровни, pivot-уровни,
объёмные ноды). Цена гравитирует к этим пулам и часто разворачивается ПОСЛЕ
их снятия (sweep).

Модуль строит карту пулов вокруг текущей цены и даёт движку:
  • target(direction) — ближайший сильный пул В сторону сделки (магнит для TP)
  • overhead_block(direction, atr) — сильный пул прямо НА пути (риск снятия+разворота)
  • build_liquidity_map(market) — основная сборка из market dict

Без внешних зависимостей (только stdlib) — для лёгких тестов.
Все «свечи» — dict {"o","h","l","c","v"}.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ─── Параметры ────────────────────────────────────────────────────────────

SWING_LOOKBACK   = 2       # fractal: N баров слева/справа для swing-точки
CLUSTER_TOL_PCT  = 0.0015  # 0.15% — сливаем близкие уровни в один пул
NEAR_PCT         = 0.004   # 0.4% — цена «у уровня»
WEEK_DAYS        = 7       # prior week H/L из последних N daily-баров

# Метод BigBeluga (Dynamic Liquidity HeatMap): вес пула по объёму +
# untapped pruning (живая, не снятая ликвидность).
PRUNE_UNTAPPED      = True  # отбрасывать пулы, пройденные ценой после формирования
VOL_STRENGTH_BONUS  = 2     # макс. бонус к strength за высокий объём свинга
SWING_VOL_WINDOW    = 5     # окно суммирования объёма вокруг свинга (как vol=sum(10))

# Базовая сила пула по типу (1-5)
KIND_STRENGTH = {
    "PWH": 4, "PWL": 4,    # prior week high/low
    "PDH": 3, "PDL": 3,    # prior day high/low
    "EQH": 4, "EQL": 4,    # equal highs/lows (кластер свингов)
    "VAH": 3, "VAL": 3, "POC": 3,
    "R2": 3, "S2": 3, "R3": 2, "S3": 2, "R1": 2, "S1": 2,
    "round": 2,
}


@dataclass
class Pool:
    price:    float
    kind:     str        # EQH, EQL, PDH, PDL, PWH, PWL, VAH/VAL/POC, R1.., round
    side:     str        # "buyside" (выше цены) | "sellside" (ниже цены)
    strength: int        # 1-5
    dist_pct: float      # знаковое расстояние от цены, %  (выше = +)
    touches:  int = 1
    volume:   float = 0.0  # накопленный объём свинга (вес ликвидности)

    def label(self) -> str:
        return f"{self.kind}@{self.price:g}"


@dataclass
class LiquidityMap:
    price: float
    pools: list = field(default_factory=list)

    # ─── выборки ──────────────────────────────────────────────────────────
    def above(self) -> list:
        return sorted((p for p in self.pools if p.price > self.price),
                      key=lambda p: p.price)

    def below(self) -> list:
        return sorted((p for p in self.pools if p.price < self.price),
                      key=lambda p: -p.price)

    def nearest_above(self, min_strength: int = 1):
        for p in self.above():
            if p.strength >= min_strength:
                return p
        return None

    def nearest_below(self, min_strength: int = 1):
        for p in self.below():
            if p.strength >= min_strength:
                return p
        return None

    def target(self, direction: str, min_strength: int = 3):
        """Ближайший сильный пул в сторону сделки — магнит для TP."""
        if direction == "long":
            return self.nearest_above(min_strength)
        if direction == "short":
            return self.nearest_below(min_strength)
        return None

    def liquidity_poc(self):
        """Пул с максимальным объёмом — плотнейшая ликвидность (POC-магнит)."""
        with_vol = [p for p in self.pools if p.volume > 0]
        if not with_vol:
            return None
        return max(with_vol, key=lambda p: p.volume)

    def overhead_block(self, direction: str, atr: float, max_atr: float = 0.8):
        """
        Сильный (>=4) пул прямо на пути сделки в пределах max_atr×ATR:
        вход рискован — цена может снять пул и развернуться.
        Возвращает Pool или None.
        """
        if atr <= 0:
            return None
        cand = self.nearest_above(4) if direction == "long" else (
            self.nearest_below(4) if direction == "short" else None)
        if cand is None:
            return None
        if abs(cand.price - self.price) <= max_atr * atr:
            return cand
        return None

    def summary(self, limit: int = 4) -> str:
        """Короткая строка для LLM/Telegram."""
        ups   = self.above()[:limit]
        downs = self.below()[:limit]
        u = " ".join(f"{p.kind}{p.price:g}(s{p.strength})" for p in ups)
        d = " ".join(f"{p.kind}{p.price:g}(s{p.strength})" for p in downs)
        return f"↑ {u or '—'} | ↓ {d or '—'}"


# ─── Swing detection ──────────────────────────────────────────────────────

def find_swing_highs(candles: list, lb: int = SWING_LOOKBACK) -> list:
    """Индексы локальных максимумов (fractal): high выше lb соседей с каждой стороны."""
    out = []
    n = len(candles)
    for i in range(lb, n - lb):
        h = candles[i]["h"]
        if all(h >= candles[i - j]["h"] for j in range(1, lb + 1)) and \
           all(h >= candles[i + j]["h"] for j in range(1, lb + 1)):
            out.append(i)
    return out


def find_swing_lows(candles: list, lb: int = SWING_LOOKBACK) -> list:
    out = []
    n = len(candles)
    for i in range(lb, n - lb):
        low = candles[i]["l"]
        if all(low <= candles[i - j]["l"] for j in range(1, lb + 1)) and \
           all(low <= candles[i + j]["l"] for j in range(1, lb + 1)):
            out.append(i)
    return out


def cluster_levels(levels: list, tol_pct: float = CLUSTER_TOL_PCT) -> list:
    """
    Сливает близкие уровни в кластеры. Возвращает [(avg_price, count)],
    отсортировано по убыванию count (сильнее = больше касаний).
    """
    if not levels:
        return []
    levels = sorted(levels)
    clusters = []          # list of [sum, count, ref_price]
    for lv in levels:
        placed = False
        for c in clusters:
            if abs(lv - c[2]) <= c[2] * tol_pct:
                c[0] += lv
                c[1] += 1
                c[2] = c[0] / c[1]
                placed = True
                break
        if not placed:
            clusters.append([lv, 1, lv])
    out = [(round(c[2], 8), c[1]) for c in clusters]
    out.sort(key=lambda x: -x[1])
    return out


def cluster_with_volume(items: list, tol_pct: float = CLUSTER_TOL_PCT) -> list:
    """
    Кластеризация уровней с агрегацией объёма (метод BigBeluga).
    items: [(price, volume)]. Возвращает [(avg_price, count, total_vol)],
    отсортировано по убыванию count.
    """
    if not items:
        return []
    items = sorted(items, key=lambda x: x[0])
    clusters = []          # [sum_price, count, ref_price, sum_vol]
    for price, vol in items:
        placed = False
        for c in clusters:
            if abs(price - c[2]) <= c[2] * tol_pct:
                c[0] += price
                c[1] += 1
                c[2] = c[0] / c[1]
                c[3] += vol
                placed = True
                break
        if not placed:
            clusters.append([price, 1, price, vol])
    out = [(round(c[2], 8), c[1], c[3]) for c in clusters]
    out.sort(key=lambda x: -x[1])
    return out


def _swing_volume(candles: list, i: int, win: int = SWING_VOL_WINDOW) -> float:
    """Суммарный объём в окне вокруг свинг-точки (вес ликвидности)."""
    a = max(0, i - win)
    b = min(len(candles), i + win + 1)
    return sum(c["v"] for c in candles[a:b])


def untapped_swing_highs(candles: list, lb: int = SWING_LOOKBACK) -> list:
    """
    Swing highs, которые цена НЕ пробила после формирования (живая ликвидность).
    Возвращает [(price, volume)].
    """
    out = []
    n = len(candles)
    for i in find_swing_highs(candles, lb):
        h = candles[i]["h"]
        if PRUNE_UNTAPPED and any(candles[j]["h"] > h for j in range(i + 1, n)):
            continue
        out.append((h, _swing_volume(candles, i)))
    return out


def untapped_swing_lows(candles: list, lb: int = SWING_LOOKBACK) -> list:
    out = []
    n = len(candles)
    for i in find_swing_lows(candles, lb):
        low = candles[i]["l"]
        if PRUNE_UNTAPPED and any(candles[j]["l"] < low for j in range(i + 1, n)):
            continue
        out.append((low, _swing_volume(candles, i)))
    return out


def _strength_with_volume(base: int, count: int, vol: float,
                          max_vol: float) -> int:
    """strength = база + бонус за касания + бонус за объём (cap 5)."""
    vbonus = round((vol / max_vol) * VOL_STRENGTH_BONUS) if max_vol > 0 else 0
    return min(5, base + (count - 1) + vbonus)


# ─── Round numbers ──────────────────────────────────────────────────────────

def _round_step(price: float) -> float:
    p = abs(price)
    if p >= 10000:
        return 1000.0
    if p >= 1000:
        return 100.0
    if p >= 100:
        return 10.0
    if p >= 10:
        return 1.0
    if p >= 1:
        return 0.1
    if p >= 0.1:
        return 0.01
    return 0.001


def nearby_round_levels(price: float) -> list:
    """Ближайшие круглые уровни выше и ниже цены."""
    step = _round_step(price)
    if step <= 0:
        return []
    below = (price // step) * step
    above = below + step
    out = []
    if below > 0 and below != price:
        out.append(round(below, 8))
    if above != price:
        out.append(round(above, 8))
    return out


# ─── Сборка карты ───────────────────────────────────────────────────────────

def _add_pool(pools: list, price: float, kind: str, cur: float,
              strength: int, touches: int = 1, volume: float = 0.0) -> None:
    if price <= 0 or cur <= 0:
        return
    side = "buyside" if price > cur else "sellside"
    dist = (price / cur - 1) * 100
    pools.append(Pool(price=round(price, 8), kind=kind, side=side,
                      strength=strength, dist_pct=round(dist, 3),
                      touches=touches, volume=volume))


def build_liquidity_map(market: dict) -> LiquidityMap:
    """
    Строит карту ликвидности из market dict. Использует уже собранные
    данные (klines, pivots, vp) — без новых API-вызовов.
    """
    price = float(market.get("price", 0) or 0)
    lmap = LiquidityMap(price=price, pools=[])
    if price <= 0:
        return lmap

    klines = market.get("_klines", {}) or {}
    k1h = klines.get("60") or []
    kD  = klines.get("D") or []

    # 1) Equal highs/lows из swing-точек 1h (кластеры = пулы стопов).
    #    Метод BigBeluga: только untapped (живая) ликвидность + вес по объёму.
    if len(k1h) >= 2 * SWING_LOOKBACK + 2:
        highs = untapped_swing_highs(k1h)
        lows  = untapped_swing_lows(k1h)
        hc = cluster_with_volume(highs)
        lc = cluster_with_volume(lows)
        max_vol = max((v for _, _, v in hc + lc), default=0.0)
        for lv, cnt, vol in hc:
            strength = _strength_with_volume(KIND_STRENGTH["EQH"], cnt, vol,
                                             max_vol)
            _add_pool(lmap.pools, lv, "EQH", price, strength,
                      touches=cnt, volume=vol)
        for lv, cnt, vol in lc:
            strength = _strength_with_volume(KIND_STRENGTH["EQL"], cnt, vol,
                                             max_vol)
            _add_pool(lmap.pools, lv, "EQL", price, strength,
                      touches=cnt, volume=vol)

    # 2) Prior day H/L
    if len(kD) >= 2:
        _add_pool(lmap.pools, kD[-2]["h"], "PDH", price, KIND_STRENGTH["PDH"])
        _add_pool(lmap.pools, kD[-2]["l"], "PDL", price, KIND_STRENGTH["PDL"])

    # 3) Prior week H/L (последние WEEK_DAYS завершённых дней, без текущего)
    if len(kD) >= WEEK_DAYS + 1:
        week = kD[-(WEEK_DAYS + 1):-1]
        _add_pool(lmap.pools, max(c["h"] for c in week), "PWH", price,
                  KIND_STRENGTH["PWH"])
        _add_pool(lmap.pools, min(c["l"] for c in week), "PWL", price,
                  KIND_STRENGTH["PWL"])

    # 4) Pivot R/S
    piv = market.get("pivots", {}) or {}
    for k in ("R1", "R2", "R3", "S1", "S2", "S3"):
        if piv.get(k):
            _add_pool(lmap.pools, piv[k], k, price, KIND_STRENGTH.get(k, 2))

    # 5) Volume Profile ноды
    vp = market.get("vp", {}) or {}
    for k in ("VAH", "VAL", "POC"):
        key = k.lower()
        if vp.get(key):
            _add_pool(lmap.pools, vp[key], k, price, KIND_STRENGTH[k])

    # 6) Круглые уровни
    for lv in nearby_round_levels(price):
        _add_pool(lmap.pools, lv, "round", price, KIND_STRENGTH["round"])

    return lmap
