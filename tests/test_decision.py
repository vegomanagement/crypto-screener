"""Unit tests for decision.py — deterministic trading verdict engine."""

import pytest

from decision import (
    CONFLUENCE_WAIT_THRESHOLD,
    MIN_RR_FOR_TRADE,
    format_decision_header,
    make_decision,
    parse_direction,
)


# ─── Helpers ──────────────────────────────────────────────────────────────


def _market(atr: float = 200.0, **overrides) -> dict:
    """Build a minimal market dict; override any keys via kwargs."""
    base = {
        "indicators": {
            "atr": atr,
            "atr_pct": 0.47,
            "rsi": 55,
            "macd": {"trend": "bull", "cross": "none"},
            "rsi_div": "none",
        },
        "bybit": {"funding": 0.0001},
        "turtle_1h": {},
        "turtle_4h": {},
    }
    base.update(overrides)
    return base


# ─── parse_direction ──────────────────────────────────────────────────────


@pytest.mark.parametrize("signal,expected", [
    ("BOS_BULL",     "long"),
    ("OB_BULL",      "long"),
    ("LIQ_SWEEP_L",  "long"),
    ("BOS_BEAR",     "short"),
    ("CHOCH_BEAR",   "short"),
    ("LIQ_SWEEP_H",  "short"),
    ("DAILY_OPEN",   "neutral"),
    ("ICT_KILLZONE", "neutral"),
    ("",             "neutral"),
    (None,           "neutral"),
])
def test_parse_direction(signal, expected):
    assert parse_direction(signal) == expected


# ─── Happy paths: LONG / SHORT ────────────────────────────────────────────


def test_clean_long_emits_verdict_with_levels():
    d = make_decision(
        signal_type="BOS_BULL",
        price=42500.0,
        market=_market(),
        mtf={"aligned": 3, "total": 3},
        confluence_score=78,
        confluence_factors=["CVD ✅ подтверждает", "MTF ✅ все 3 ТФ", "VP ✅ POC"],
    )
    assert d["verdict"] == "LONG"
    assert d["direction"] == "long"
    assert d["entry"]["min"] < 42500 < d["entry"]["max"]
    assert d["sl"] < d["entry"]["min"]
    assert d["tp1"] < d["tp2"] < d["tp3"]
    # ATR=200, sl_dist=1.0×ATR, tp at 1.5/2.5/4.0×ATR → RR = 1.5/2.5/4.0
    assert d["rr1"] == 1.5
    assert d["rr2"] == 2.5
    assert d["rr3"] == 4.0
    assert d["confidence"] == 78  # no vetoes triggered
    assert len(d["key_factors"]) == 3


def test_clean_short_mirrors_long():
    d = make_decision(
        signal_type="CHOCH_BEAR",
        price=42500.0,
        market=_market(
            indicators={
                "atr": 200.0, "rsi": 45,
                "macd": {"trend": "bear", "cross": "none"},
                "rsi_div": "none",
            },
            bybit={"funding": 0.0001},
        ),
        mtf={"aligned": 2, "total": 3},
        confluence_score=68,
        confluence_factors=["MACD ✅ медвежий", "FR ✅ лонги переплачивают"],
    )
    assert d["verdict"] == "SHORT"
    assert d["sl"] > d["entry"]["max"]
    assert d["tp1"] > d["tp2"] > d["tp3"]  # descending for SHORT
    assert d["rr1"] == 1.5


def test_low_cap_altcoin_keeps_precision():
    d = make_decision(
        signal_type="OB_BULL",
        price=0.5234,
        market=_market(atr=0.0042),
        mtf={"aligned": 2, "total": 3},
        confluence_score=65,
        confluence_factors=["CVD ✅", "MTF ✅"],
    )
    assert d["verdict"] == "LONG"
    assert d["rr1"] == 1.5
    # Ensure SL/TP are not rounded to zero
    assert d["sl"] > 0
    assert d["tp1"] > d["entry"]["max"]


# ─── WAIT verdicts ────────────────────────────────────────────────────────


def test_low_confluence_returns_wait_without_levels():
    d = make_decision("BOS_BULL", 42500.0, _market(), {}, 45, [])
    assert d["verdict"] == "WAIT"
    assert d["entry"] is None
    assert d["sl"] is None
    assert d["tp1"] is None
    assert "Confluence 45" in d["reason"]


def test_confluence_exactly_at_threshold_is_wait_or_trade():
    # threshold is strict <, so == threshold should pass to trade
    d = make_decision(
        "BOS_BULL",
        42500.0,
        _market(),
        {"aligned": 3, "total": 3},
        CONFLUENCE_WAIT_THRESHOLD,
        ["CVD ✅"],
    )
    assert d["verdict"] == "LONG"


def test_many_vetoes_force_wait():
    d = make_decision(
        signal_type="BOS_BULL",
        price=42500.0,
        market=_market(
            indicators={
                "atr": 200.0,
                "rsi": 82,
                "macd": {"trend": "bear"},
                "rsi_div": "bearish",
            },
            bybit={"funding": 0.0008},
            turtle_1h={"zone": "extreme_upper"},
            turtle_4h={},
        ),
        mtf={"aligned": 0, "total": 3},
        confluence_score=70,
        confluence_factors=["CVD ✅"],
    )
    assert d["verdict"] == "WAIT"
    assert len(d["veto_reasons"]) >= 3
    assert d["entry"] is None  # levels stripped on WAIT
    # confidence should be heavily penalized
    assert d["confidence"] < 70


def test_low_final_confidence_skips_despite_passing_confluence():
    """
    Confluence проходит порог (>=55), но вето снижают финальный confidence
    ниже MIN_CONFIDENCE_FOR_TRADE — раньше слался LONG, теперь SKIP.

    confluence=58, штрафы: RSI div bearish (12) + MACD bear (8) = 20
    → confidence = 38 < 50 → SKIP. Вето всего 2 (< MAX_CONTRADICTIONS=3),
    поэтому до confidence-гейта не отсекается на vetoes.
    """
    from decision import MIN_CONFIDENCE_FOR_TRADE

    d = make_decision(
        signal_type="BOS_BULL",
        price=42500.0,
        market=_market(
            indicators={
                "atr": 200.0,
                "rsi": 55,
                "macd": {"trend": "bear"},
                "rsi_div": "bearish",
            },
            bybit={"funding": 0.0001},
        ),
        mtf={"aligned": 2, "total": 3},
        confluence_score=58,
        confluence_factors=["CVD ✅"],
    )
    assert d["confidence"] < MIN_CONFIDENCE_FOR_TRADE
    assert d["verdict"] == "SKIP"
    assert d["entry"] is None
    assert str(MIN_CONFIDENCE_FOR_TRADE) in d["reason"]


def test_confidence_just_above_floor_still_trades():
    """confidence ровно на пороге (>=50) — сделка проходит."""
    d = make_decision(
        signal_type="BOS_BULL",
        price=42500.0,
        market=_market(
            indicators={
                "atr": 200.0, "rsi": 55,
                "macd": {"trend": "bull"},
                "rsi_div": "none",
            },
            bybit={"funding": 0.0001},
        ),
        mtf={"aligned": 3, "total": 3},
        confluence_score=58,
        confluence_factors=["CVD ✅", "MTF ✅"],
    )
    # нет вето → confidence = 58 >= 50 → LONG
    assert d["confidence"] == 58
    assert d["verdict"] == "LONG"


def test_neutral_signal_returns_wait():
    d = make_decision("DAILY_OPEN", 42500.0, _market(), {}, 50, [])
    assert d["verdict"] == "WAIT"
    assert d["direction"] == "neutral"
    assert d["entry"] is None


# ─── SKIP verdicts ────────────────────────────────────────────────────────


def test_zero_atr_returns_skip():
    d = make_decision("BOS_BULL", 42500.0, _market(atr=0), {}, 70, [])
    assert d["verdict"] == "SKIP"
    assert "ATR" in d["reason"]


def test_skip_by_rr_strips_levels(monkeypatch):
    """
    SKIP-by-RR должен очищать entry/sl/tp как и WAIT (ревью-фикс).
    С дефолтными ATR-коэффициентами RR(TP1) всегда = 1.5, так что
    SKIP-by-RR недостижим — поэтому temporarily патчим коэффициент.
    """
    import decision as d_mod
    monkeypatch.setattr(d_mod, "ATR_TP1_DIST", 0.5)  # → RR1 = 0.5/1.0 = 0.5

    d = d_mod.make_decision("BOS_BULL", 42500.0,
                            _market(atr=200.0),
                            {"aligned": 2, "total": 3},
                            70, [])
    assert d["verdict"] == "SKIP"
    assert d["entry"] is None
    assert d["sl"] is None
    assert d["tp1"] is None
    assert d["rr1"] is None


def test_zero_price_returns_skip():
    d = make_decision("BOS_BULL", 0, _market(), {}, 70, [])
    assert d["verdict"] == "SKIP"


# ─── Veto penalty math ────────────────────────────────────────────────────


def test_rsi_extreme_penalizes_confidence():
    d = make_decision(
        signal_type="BOS_BULL",
        price=42500.0,
        market=_market(indicators={
            "atr": 200.0, "rsi": 82,
            "macd": {"trend": "bull"}, "rsi_div": "none",
        }),
        mtf={"aligned": 2, "total": 3},
        confluence_score=70,
        confluence_factors=["CVD ✅"],
    )
    assert d["verdict"] == "LONG"
    # Only one veto (RSI overbought) → confidence = 70 − 20 = 50
    assert d["confidence"] == 50
    assert any("RSI" in r for r in d["veto_reasons"])


def test_funding_overheated_against_long_is_veto():
    d = make_decision(
        signal_type="BOS_BULL",
        price=42500.0,
        market=_market(bybit={"funding": 0.001}),  # +0.1%, well above 0.05%
        mtf={"aligned": 2, "total": 3},
        confluence_score=70,
        confluence_factors=[],
    )
    assert any("Funding" in r and "лонги" in r for r in d["veto_reasons"])
    assert d["confidence"] == 60  # 70 - 10


# ─── format_decision_header ───────────────────────────────────────────────


def test_format_long_contains_all_levels():
    d = make_decision(
        "BOS_BULL", 42500.0, _market(),
        {"aligned": 3, "total": 3}, 78,
        ["CVD ✅ подтверждает", "MTF ✅ все 3 ТФ"],
    )
    s = format_decision_header(d)
    assert "LONG" in s
    assert "Entry" in s
    assert "TP1" in s and "TP2" in s and "TP3" in s
    assert "SL" in s
    assert "Confidence" in s
    assert "За:" in s


def test_format_wait_omits_levels_and_shows_reason():
    d = make_decision("BOS_BULL", 42500.0, _market(), {}, 40, [])
    s = format_decision_header(d)
    assert "WAIT" in s
    assert "Entry" not in s
    assert "TP1" not in s
    assert "💬" in s  # reason marker


def test_format_skip_shows_reason():
    d = make_decision("BOS_BULL", 42500.0, _market(atr=0), {}, 70, [])
    s = format_decision_header(d)
    assert "SKIP" in s
    assert "ATR" in s


# ─── RR floor ─────────────────────────────────────────────────────────────


def test_min_rr_constant_is_respected():
    # All happy-path tests achieve RR=1.5 which equals MIN_RR_FOR_TRADE.
    # This sanity-checks the relationship.
    assert MIN_RR_FOR_TRADE <= 1.5


# ─── Smart-money слой (liquidity + regime) ─────────────────────────────────


def _c(o, h, low, c, v=100.0):
    return {"o": o, "h": h, "l": low, "c": c, "v": v}


def _market_full(direction_setup="accumulation"):
    """
    Полный market dict с klines для smart-money слоя.
    accumulation: цена у низа диапазона + CVD вверх (bias long).
    """
    older  = [_c(100, 120, 80, 100) for _ in range(20)]
    recent = [_c(92, 94, 90, 92) for _ in range(20)]
    k1h = older + recent
    return {
        "price": 92.0,
        "_klines": {"60": k1h, "240": [], "D": [
            _c(95, 122, 78, 100), _c(100, 110, 88, 92), _c(92, 95, 90, 92)]},
        "cvd": {"trend": "up", "price_trend": "down", "divergence": True},
        "vp": {"poc": 100.0, "vah": 115.0, "val": 85.0},
        "pivots": {"R1": 105.0, "S1": 88.0},
        "bybit": {"funding": 0.0, "oi_chg": 0.0},
        "ls_ratio": {}, "liquidations": {}, "change_24h": -2.0,
        "indicators": {"atr": 3.0, "rsi": 45,
                       "macd": {"trend": "bull"}, "rsi_div": "none"},
    }


def test_smart_money_regime_aligned_long_gets_bonus_and_context():
    m = _market_full()
    d = make_decision("BOS_BULL", 92.0, m, {"aligned": 3, "total": 3}, 60,
                      ["CVD ✅"])
    # regime accumulation (bias long) совпал → должен быть контекст
    assert "regime" in d
    assert d["regime"]["phase"] == "accumulation"
    assert d["liquidity"]  # строка карты ликвидности
    # confidence поднялся выше базового confluence 60 за счёт режима/discount
    assert d["confidence"] > 60
    assert d["verdict"] == "LONG"


def test_smart_money_regime_conflict_short_penalized():
    # шорт против фазы накопления → штраф + риск в veto_reasons
    m = _market_full()
    d = make_decision("BOS_BEAR", 92.0, m, {"aligned": 1, "total": 3}, 60,
                      ["CVD ✅"])
    assert d["regime"]["phase"] == "accumulation"
    # либо отвергнут в SKIP (confidence упал ниже 50), либо есть риск-нота
    assert d["confidence"] < 60
    assert any("режим" in r.lower() or "discount" in r.lower()
               for r in d["veto_reasons"])


def test_smart_money_missing_klines_is_safe():
    # market без _klines не должен ломать движок
    m = _market(atr=200.0)
    d = make_decision("BOS_BULL", 42500.0, m, {"aligned": 3, "total": 3}, 60,
                      ["CVD ✅"])
    assert d["verdict"] in ("LONG", "SHORT", "WAIT", "SKIP")
    assert "regime" in d  # слой отработал даже на пустых данных


# ─── Liquidity-aware levels (Этап 8) ───────────────────────────────────────

from decision import apply_liquidity_levels  # noqa: E402
from liquidity import LiquidityMap, Pool  # noqa: E402


def _atr_levels_long(price=100.0, atr=2.0):
    """ATR-уровни для long: SL=price-2, TP1/2/3 = +3/+5/+8 (как _compute_levels)."""
    from decision import _compute_levels
    return _compute_levels(price, atr, "long")


def test_liq_levels_noop_without_map():
    lv = _atr_levels_long()
    out = apply_liquidity_levels(lv, None, 100.0, 2.0, "long")
    assert out == lv


def test_liq_levels_sl_moves_beyond_pool_long():
    lv = _atr_levels_long(price=100.0, atr=2.0)   # ATR SL = 98.0
    # сильный пул поддержки на 97.5 → SL должен уйти ЗА него (ниже 97.5)
    lmap = LiquidityMap(price=100.0, pools=[
        Pool(price=97.5, kind="PWL", side="sellside", strength=4, dist_pct=-2.5),
        Pool(price=108.0, kind="PWH", side="buyside", strength=4, dist_pct=8.0),
    ])
    out = apply_liquidity_levels(lv, lmap, 100.0, 2.0, "long")
    assert out["sl"] < 97.5            # за пул
    assert (100.0 - out["sl"]) <= 2.0 * 2.0 + 1e-6  # в пределах cap


def test_liq_levels_sl_cap_respected():
    lv = _atr_levels_long(price=100.0, atr=2.0)
    # пул слишком далеко (90) → SL за него = 89.5, риск 10.5 > cap 4.0 → ATR SL
    lmap = LiquidityMap(price=100.0, pools=[
        Pool(price=90.0, kind="PWL", side="sellside", strength=4, dist_pct=-10.0),
        Pool(price=108.0, kind="PWH", side="buyside", strength=4, dist_pct=8.0),
    ])
    out = apply_liquidity_levels(lv, lmap, 100.0, 2.0, "long")
    assert out["sl"] == lv["sl"]       # cap → fallback на ATR SL


def test_liq_levels_tp_frontruns_pool_long():
    lv = _atr_levels_long(price=100.0, atr=2.0)
    # пул на 106 → TP должен быть чуть НИЖЕ (front-run): 106 - 0.15*2 = 105.7
    lmap = LiquidityMap(price=100.0, pools=[
        Pool(price=106.0, kind="EQH", side="buyside", strength=4, dist_pct=6.0),
        Pool(price=96.0, kind="EQL", side="sellside", strength=4, dist_pct=-4.0),
    ])
    out = apply_liquidity_levels(lv, lmap, 100.0, 2.0, "long")
    assert out["tp1"] < 106.0
    assert abs(out["tp1"] - (106.0 - 0.15 * 2.0)) < 0.05


def test_liq_levels_monotonic_tps():
    lv = _atr_levels_long(price=100.0, atr=2.0)
    lmap = LiquidityMap(price=100.0, pools=[
        Pool(price=104.0, kind="EQH", side="buyside", strength=4, dist_pct=4.0),
        Pool(price=110.0, kind="PWH", side="buyside", strength=4, dist_pct=10.0),
        Pool(price=96.0, kind="EQL", side="sellside", strength=4, dist_pct=-4.0),
    ])
    out = apply_liquidity_levels(lv, lmap, 100.0, 2.0, "long")
    assert out["tp1"] < out["tp2"] < out["tp3"]


def test_liq_levels_guardrail_reverts_when_rr_too_low():
    lv = _atr_levels_long(price=100.0, atr=2.0)
    # widen SL via far-ish pool (within cap) AND only a very-close TP pool
    # → rr1 below MIN_RR → полный откат на ATR levels
    lmap = LiquidityMap(price=100.0, pools=[
        Pool(price=96.2, kind="PWL", side="sellside", strength=4, dist_pct=-3.8),
        Pool(price=100.4, kind="EQH", side="buyside", strength=4, dist_pct=0.4),
    ])
    out = apply_liquidity_levels(lv, lmap, 100.0, 2.0, "long")
    # TP1 front-run от 100.4 даёт крошечный RR → guardrail откатывает всё
    assert out == lv


def test_liq_levels_short_mirror():
    from decision import _compute_levels
    lv = _compute_levels(100.0, 2.0, "short")   # SL=102, TP1=97
    lmap = LiquidityMap(price=100.0, pools=[
        Pool(price=102.5, kind="PWH", side="buyside", strength=4, dist_pct=2.5),
        Pool(price=94.0, kind="EQL", side="sellside", strength=4, dist_pct=-6.0),
    ])
    out = apply_liquidity_levels(lv, lmap, 100.0, 2.0, "short")
    assert out["sl"] > 102.5           # SL за пул сверху
    assert out["tp1"] > 94.0           # front-run пула снизу
    assert out["tp1"] < 100.0          # в сторону шорта
