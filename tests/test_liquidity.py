"""Unit tests for liquidity.py — liquidity map / pools."""

from liquidity import (
    KIND_STRENGTH,
    LiquidityMap,
    Pool,
    build_liquidity_map,
    cluster_levels,
    find_swing_highs,
    find_swing_lows,
    nearby_round_levels,
)


def _c(o, h, low, c, v=100.0):
    return {"o": o, "h": h, "l": low, "c": c, "v": v}


# ─── Swing detection ──────────────────────────────────────────────────────


def test_find_swing_highs_basic():
    # bar index 3 is a clear local max (high=110)
    candles = [_c(100, 101, 99, 100), _c(100, 103, 99, 101),
               _c(101, 105, 100, 104), _c(104, 110, 103, 108),
               _c(108, 106, 104, 105), _c(105, 104, 102, 103)]
    highs = find_swing_highs(candles, lb=2)
    assert 3 in highs


def test_find_swing_lows_basic():
    candles = [_c(100, 101, 99, 100), _c(100, 100, 97, 98),
               _c(98, 99, 90, 92), _c(92, 95, 91, 94),
               _c(94, 96, 93, 95), _c(95, 97, 94, 96)]
    lows = find_swing_lows(candles, lb=2)
    assert 2 in lows


# ─── Clustering ───────────────────────────────────────────────────────────


def test_cluster_levels_merges_near():
    # 100.0, 100.1, 100.05 within 0.15% → one cluster of 3
    out = cluster_levels([100.0, 100.1, 100.05, 200.0], tol_pct=0.0015)
    counts = {round(p): c for p, c in out}
    assert counts[100] == 3
    assert counts[200] == 1


def test_cluster_levels_sorted_by_strength():
    out = cluster_levels([100.0, 100.05, 200.0])
    # most-touched cluster first
    assert out[0][1] == 2


# ─── Round numbers ────────────────────────────────────────────────────────


def test_nearby_round_levels_btc():
    lv = nearby_round_levels(42500.0)
    assert 42000.0 in lv and 43000.0 in lv


def test_nearby_round_levels_lowcap():
    # price 0.5234 → step 0.01 → bracketing round levels 0.52 / 0.53
    lv = nearby_round_levels(0.5234)
    assert any(abs(x - 0.52) < 1e-9 for x in lv)
    assert any(abs(x - 0.53) < 1e-9 for x in lv)


# ─── Map building ─────────────────────────────────────────────────────────


def _market_with_klines():
    # Build 1h klines with swing high ~110 (above price) and low ~90 (below)
    k1h = []
    pattern = [100, 103, 110, 106, 102, 98, 90, 94, 99, 101]
    for i, base in enumerate(pattern * 3):
        k1h.append(_c(base, base + 2, base - 2, base + 0.5, 100 + i))
    # current price near 101
    k1h[-1] = _c(100, 102, 99, 101)
    kD = [_c(95, 112, 88, 100), _c(100, 108, 92, 101), _c(101, 105, 95, 101)]
    return {
        "price": 101.0,
        "_klines": {"60": k1h, "240": [], "D": kD},
        "pivots": {"R1": 105.0, "R2": 109.0, "S1": 96.0, "S2": 92.0},
        "vp": {"poc": 100.0, "vah": 107.0, "val": 94.0},
    }


def test_build_map_produces_pools_both_sides():
    lmap = build_liquidity_map(_market_with_klines())
    assert isinstance(lmap, LiquidityMap)
    assert lmap.above(), "expected buyside pools above price"
    assert lmap.below(), "expected sellside pools below price"


def test_build_map_includes_prior_day_levels():
    lmap = build_liquidity_map(_market_with_klines())
    kinds = {p.kind for p in lmap.pools}
    assert "PDH" in kinds
    assert "PDL" in kinds


def test_build_map_includes_pivots_and_vp():
    lmap = build_liquidity_map(_market_with_klines())
    kinds = {p.kind for p in lmap.pools}
    assert "R2" in kinds and "S2" in kinds
    assert "VAH" in kinds and "VAL" in kinds


def test_pool_side_assignment():
    lmap = build_liquidity_map(_market_with_klines())
    for p in lmap.pools:
        if p.price > lmap.price:
            assert p.side == "buyside"
        else:
            assert p.side == "sellside"


def test_target_picks_directional_pool():
    lmap = build_liquidity_map(_market_with_klines())
    tlong = lmap.target("long", min_strength=3)
    tshort = lmap.target("short", min_strength=3)
    if tlong:
        assert tlong.price > lmap.price
    if tshort:
        assert tshort.price < lmap.price


def test_overhead_block_detects_close_strong_pool():
    lmap = LiquidityMap(price=100.0, pools=[
        Pool(price=100.5, kind="PWH", side="buyside", strength=4, dist_pct=0.5),
    ])
    # ATR=2 → 100.5 within 0.8*2=1.6 of 100 → block
    block = lmap.overhead_block("long", atr=2.0)
    assert block is not None and block.kind == "PWH"


def test_overhead_block_ignores_far_pool():
    lmap = LiquidityMap(price=100.0, pools=[
        Pool(price=120.0, kind="PWH", side="buyside", strength=4, dist_pct=20.0),
    ])
    assert lmap.overhead_block("long", atr=2.0) is None


def test_overhead_block_ignores_weak_pool():
    lmap = LiquidityMap(price=100.0, pools=[
        Pool(price=100.3, kind="round", side="buyside", strength=2, dist_pct=0.3),
    ])
    assert lmap.overhead_block("long", atr=2.0) is None


def test_empty_market_returns_empty_map():
    lmap = build_liquidity_map({"price": 0})
    assert lmap.pools == []


def test_kind_strength_table_sane():
    assert KIND_STRENGTH["PWH"] >= KIND_STRENGTH["PDH"] >= KIND_STRENGTH["R1"]
