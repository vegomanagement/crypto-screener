"""Unit tests for decision.py — deterministic trading verdict engine."""

import pytest

from decision import (
    CONFLUENCE_WAIT_THRESHOLD,
    MIN_RR_FOR_TRADE,
    format_decision_header,
    make_decision,
    parse_direction,
)


@pytest.fixture(autouse=True)
def _disable_p3_gates(monkeypatch):
    """
    Этап 10 фаза 3 P3-гейты (killzone + structure) по умолчанию ВКЛЮЧЕНЫ в
    проде, но в тестах они флапали бы по now()/нехватке klines. Отключаем
    их для всех тестов; P3-специфичные тесты включают обратно вручную.
    """
    import decision as d_mod
    monkeypatch.setattr(d_mod, "KILLZONE_GATE_ENABLED", False)
    monkeypatch.setattr(d_mod, "STRUCTURE_GATE_ENABLED", False)


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


def test_confluence_exactly_at_threshold_passes_confluence_gate():
    """
    Confluence ровно на CONFLUENCE_WAIT_THRESHOLD проходит confluence-гейт
    (не WAIT). Но финальный verdict определяется ещё и MIN_CONFIDENCE_FOR_TRADE,
    который может быть выше — тогда SKIP, но не WAIT-by-confluence.
    """
    d = make_decision(
        "BOS_BULL",
        42500.0,
        _market(),
        {"aligned": 3, "total": 3},
        CONFLUENCE_WAIT_THRESHOLD,
        ["CVD ✅"],
    )
    # Главное: не "WAIT — мало confluence" причина
    assert "Confluence" not in d.get("reason", "") or d["verdict"] != "WAIT"


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

    confluence=70, штрафы: RSI div bearish (12) + MACD bear (8) = 20
    → confidence = 50 < 65 (новый floor) → SKIP. Вето всего 2 (< MAX=3),
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
        confluence_score=70,
        confluence_factors=["CVD ✅"],
    )
    assert d["confidence"] < MIN_CONFIDENCE_FOR_TRADE
    assert d["verdict"] == "SKIP"
    assert d["entry"] is None
    assert str(MIN_CONFIDENCE_FOR_TRADE) in d["reason"]


def test_confidence_just_above_floor_still_trades():
    """confidence ровно на новом пороге (>=65) — сделка проходит."""
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
        confluence_score=65,
        confluence_factors=["CVD ✅", "MTF ✅"],
    )
    # нет вето + нет klines (smart-money silent) → confidence = 65 >= 65 → LONG
    assert d["confidence"] == 65
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
    """RSI overbought штрафует на 20. С confluence=90 → confidence=70 ≥ 65."""
    d = make_decision(
        signal_type="BOS_BULL",
        price=42500.0,
        market=_market(indicators={
            "atr": 200.0, "rsi": 82,
            "macd": {"trend": "bull"}, "rsi_div": "none",
        }),
        mtf={"aligned": 2, "total": 3},
        confluence_score=90,
        confluence_factors=["CVD ✅"],
    )
    assert d["verdict"] == "LONG"
    # confidence = 90 − 20 = 70 (smart-money silent без klines)
    assert d["confidence"] == 70
    assert any("RSI" in r for r in d["veto_reasons"])


def test_funding_overheated_against_long_is_veto():
    """Funding штрафует на 10. veto_reason записывается даже когда verdict SKIP."""
    d = make_decision(
        signal_type="BOS_BULL",
        price=42500.0,
        market=_market(bybit={"funding": 0.001}),  # +0.1%, well above 0.05%
        mtf={"aligned": 2, "total": 3},
        confluence_score=70,
        confluence_factors=[],
    )
    assert any("Funding" in r and "лонги" in r for r in d["veto_reasons"])
    assert d["confidence"] == 60  # 70 - 10. Verdict SKIP (60 < 65), но veto в списке.


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


def test_format_shows_killzone_when_in_window():
    """Если P3-гейт записал killzone in=True — показать его в шапке."""
    d = {
        "verdict": "LONG", "direction": "long",
        "entry": {"min": 100, "max": 101}, "sl": 95.0,
        "tp1": 105, "tp2": 110, "tp3": 115,
        "rr1": 1.5, "rr2": 2.5, "rr3": 4.0,
        "confidence": 70, "veto_reasons": [], "key_factors": [],
        "atr": 1.0, "reason": "",
        "killzone": {"in": True, "name": "London"},
    }
    s = format_decision_header(d)
    assert "Killzone" in s
    assert "London" in s


def test_format_omits_killzone_when_not_in_window():
    """killzone in=False (или нет ключа) — не выводим строку."""
    d_no_kz = {
        "verdict": "LONG", "direction": "long",
        "entry": {"min": 100, "max": 101}, "sl": 95.0,
        "tp1": 105, "tp2": 110, "tp3": 115,
        "rr1": 1.5, "rr2": 2.5, "rr3": 4.0,
        "confidence": 70, "veto_reasons": [], "key_factors": [],
        "atr": 1.0, "reason": "",
    }
    s = format_decision_header(d_no_kz)
    assert "Killzone" not in s


def test_format_shows_structure_when_confirmed():
    """P3-гейт подтвердил slом структуры → показать BOS/CHoCH 5m+15m."""
    d = {
        "verdict": "LONG", "direction": "long",
        "entry": {"min": 100, "max": 101}, "sl": 95.0,
        "tp1": 105, "tp2": 110, "tp3": 115,
        "rr1": 1.5, "rr2": 2.5, "rr3": 4.0,
        "confidence": 70, "veto_reasons": [], "key_factors": [],
        "atr": 1.0, "reason": "",
        "structure": {"available": True, "confirmed": True,
                      "direction": "bull",
                      "kind_5m": "BOS", "kind_15m": "CHOCH"},
    }
    s = format_decision_header(d)
    assert "Структура" in s
    assert "BOS" in s and "CHOCH" in s


def test_format_omits_structure_when_unavailable_or_unconfirmed():
    """structure available=False (graceful fallback) — строки нет."""
    base_d = {
        "verdict": "LONG", "direction": "long",
        "entry": {"min": 100, "max": 101}, "sl": 95.0,
        "tp1": 105, "tp2": 110, "tp3": 115,
        "rr1": 1.5, "rr2": 2.5, "rr3": 4.0,
        "confidence": 70, "veto_reasons": [], "key_factors": [],
        "atr": 1.0, "reason": "",
    }
    # Нет структуры вообще
    assert "Структура" not in format_decision_header(base_d)
    # Структура есть, но available=False
    d2 = {**base_d, "structure": {"available": False}}
    assert "Структура" not in format_decision_header(d2)


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


def test_smart_money_regime_aligned_long_gets_context():
    """
    BOS_BULL в accumulation regime: получает regime/liquidity контекст.
    После калибровки бонусы asymmetric и могут не превышать penalties
    (overhead block), поэтому проверяем только КОНТЕКСТ + что verdict
    не сломался, а не строгое > confluence.
    """
    m = _market_full()
    d = make_decision("BOS_BULL", 92.0, m, {"aligned": 3, "total": 3}, 70,
                      ["CVD ✅"])
    assert "regime" in d
    assert d["regime"]["phase"] == "accumulation"
    assert d["liquidity"]
    # Verdict либо LONG (если bonuses перекрыли penalties), либо SKIP
    # (если pool overhead дал штраф). В обоих случаях direction long.
    assert d["direction"] == "long"


def test_smart_money_regime_conflict_short_penalized():
    """
    BOS_BEAR против accumulation regime — гейт P2 жёстко WAIT'ит (retest-bear
    + regime bias=long). До гейта confidence уже занижен штрафами.
    """
    m = _market_full()
    d = make_decision("BOS_BEAR", 92.0, m, {"aligned": 1, "total": 3}, 60,
                      ["CVD ✅"])
    assert d["regime"]["phase"] == "accumulation"
    # P2-гейт ловит: BOS_BEAR + regime bias long → WAIT
    assert d["verdict"] == "WAIT"
    assert "P2 gate" in d["reason"] or "против" in d["reason"].lower()


def test_smart_money_missing_klines_is_safe():
    # market без _klines не должен ломать движок
    m = _market(atr=200.0)
    d = make_decision("BOS_BULL", 42500.0, m, {"aligned": 3, "total": 3}, 70,
                      ["CVD ✅"])
    assert d["verdict"] in ("LONG", "SHORT", "WAIT", "SKIP")
    assert "regime" in d  # слой отработал даже на пустых данных


# ─── P1: smart-money асимметрия (бонусы gated, штрафы всегда) ──────────────


def test_smart_money_bonus_gated_by_low_confluence():
    """
    При confluence < SMART_MONEY_BONUS_MIN_CONFLUENCE (60) бонусы regime/zone
    НЕ начисляются — слабая база не должна разгоняться smart-money'ем в торг.
    """
    from decision import SMART_MONEY_BONUS_MIN_CONFLUENCE

    m = _market_full()  # accumulation regime + discount — обычно даёт бонус
    # Берём confluence ниже floor, но выше CONFLUENCE_WAIT_THRESHOLD
    cs = SMART_MONEY_BONUS_MIN_CONFLUENCE - 1
    d = make_decision("BOS_BULL", 92.0, m, {"aligned": 3, "total": 3}, cs,
                      ["CVD ✅"])
    # Бонусов не дали → confidence не выше базового minus штрафы
    # (penalty может быть от overhead pool — confidence может быть ниже cs)
    assert d["confidence"] <= cs
    # При confidence < 65 (MIN_CONFIDENCE_FOR_TRADE) → SKIP
    # Подтверждает, что bonus-gating не дал "вытянуть" слабый сигнал в торг
    assert d["verdict"] in ("SKIP", "WAIT")


def test_smart_money_penalty_still_applies_at_low_confluence():
    """Штрафы smart-money применяются всегда, даже при низкой confluence."""
    # accumulation regime — даст штраф для BOS_BEAR (regime conflict)
    m = _market_full()
    cs = 55  # на пределе CONFLUENCE_WAIT_THRESHOLD, ниже SMART_MONEY floor
    d = make_decision("BOS_BEAR", 92.0, m, {"aligned": 1, "total": 3}, cs,
                      ["CVD ✅"])
    # confidence занижен штрафом regime-conflict
    assert d["confidence"] < cs
    # либо WAIT по P2-гейту, либо SKIP по floor
    assert d["verdict"] in ("WAIT", "SKIP")


# ─── P2: structural gate retest vs regime ───────────────────────────────────


def test_p2_gate_blocks_fvg_bull_in_bearish_regime():
    """
    FVG_BULL когда regime bias=short → жёсткий WAIT (retest продолжения
    против тренда). Подделываем regime через monkey-patched classify_regime.
    """
    import decision as d_mod

    class FakeReg:
        phase = "distribution"
        bias = "short"
        zone = "premium"
        positioning = "balanced"
        range_state = "normal"
        confidence = 70
        notes = []
        def summary(self): return "fake distribution"

    # monkey-patch на время вызова
    orig = d_mod.classify_regime
    d_mod.classify_regime = lambda _m: FakeReg()
    try:
        d = make_decision(
            "FVG_BULL_5M", 100.0,
            _market_full(),
            {"aligned": 3, "total": 3}, 75, ["CVD ✅"],
        )
    finally:
        d_mod.classify_regime = orig

    assert d["verdict"] == "WAIT"
    assert "P2 gate" in d["reason"]
    assert d["entry"] is None


def test_p2_gate_blocks_bos_bear_in_bullish_regime():
    """BOS_BEAR когда regime bias=long → жёсткий WAIT."""
    import decision as d_mod

    class FakeReg:
        phase = "accumulation"
        bias = "long"
        zone = "discount"
        positioning = "balanced"
        range_state = "normal"
        confidence = 70
        notes = []
        def summary(self): return "fake accumulation"

    orig = d_mod.classify_regime
    d_mod.classify_regime = lambda _m: FakeReg()
    try:
        d = make_decision(
            "BOS_BEAR_15M", 100.0,
            _market_full(),
            {"aligned": 3, "total": 3}, 75, ["CVD ✅"],
        )
    finally:
        d_mod.classify_regime = orig

    assert d["verdict"] == "WAIT"
    assert "P2 gate" in d["reason"]


def test_p2_gate_does_not_block_liq_sweep_l_in_bearish_regime():
    """
    LIQ_SWEEP_L — контр-трендовый разворотный сигнал. Гейт НЕ должен его
    блокировать в bear regime (это его рабочий контекст).
    """
    import decision as d_mod

    class FakeReg:
        phase = "distribution"
        bias = "short"
        zone = "premium"
        positioning = "balanced"
        range_state = "normal"
        confidence = 70
        notes = []
        def summary(self): return "fake distribution"

    orig = d_mod.classify_regime
    d_mod.classify_regime = lambda _m: FakeReg()
    try:
        d = make_decision(
            "LIQ_SWEEP_L_5M", 100.0,
            _market_full(),
            {"aligned": 3, "total": 3}, 75, ["CVD ✅"],
        )
    finally:
        d_mod.classify_regime = orig

    # Гейт не сработал — verdict не WAIT-by-P2 (но может быть WAIT/SKIP
    # по другим причинам). Главное: причина не P2.
    assert "P2 gate" not in d.get("reason", "")


def test_p2_gate_silent_when_regime_neutral():
    """В neutral regime гейт НЕ срабатывает, даже для retest-сигналов."""
    import decision as d_mod

    class FakeReg:
        phase = "neutral"
        bias = "neutral"
        zone = "equilibrium"
        positioning = "balanced"
        range_state = "normal"
        confidence = 40
        notes = []
        def summary(self): return "fake neutral"

    orig = d_mod.classify_regime
    d_mod.classify_regime = lambda _m: FakeReg()
    try:
        d = make_decision(
            "FVG_BULL_5M", 100.0,
            _market_full(),
            {"aligned": 3, "total": 3}, 75, ["CVD ✅"],
        )
    finally:
        d_mod.classify_regime = orig

    assert "P2 gate" not in d.get("reason", "")


def test_p2_gate_silent_when_no_regime_data():
    """
    Без _klines (smart-money silent) regime в base не пишется → гейт молчит.
    Сигнал торгуется по обычным правилам.
    """
    m = _market(atr=200.0)  # без _klines
    d = make_decision("FVG_BULL_5M", 42500.0, m,
                      {"aligned": 3, "total": 3}, 75, ["CVD ✅"])
    assert "P2 gate" not in d.get("reason", "")


def test_is_retest_helpers():
    """Sanity-check на prefix-match для retest-классификаторов."""
    from decision import _is_retest_bear, _is_retest_bull

    assert _is_retest_bull("FVG_BULL")
    assert _is_retest_bull("FVG_BULL_5M")
    assert _is_retest_bull("BOS_BULL_1H")
    assert _is_retest_bull("EMA_CROSS_BULL")
    assert not _is_retest_bull("LIQ_SWEEP_L")
    assert not _is_retest_bull("RSI_DIV_BULL")

    assert _is_retest_bear("FVG_BEAR_15M")
    assert _is_retest_bear("BOS_BEAR")
    assert not _is_retest_bear("LIQ_SWEEP_H")
    assert not _is_retest_bear(None)


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


# ─── Этап 10 фаза 3: killzone + structure hard gate ────────────────────────


from datetime import datetime, timezone  # noqa: E402


def _enable_killzone(monkeypatch, on=True):
    import decision as d_mod
    monkeypatch.setattr(d_mod, "KILLZONE_GATE_ENABLED", on)


def _enable_structure(monkeypatch, on=True):
    import decision as d_mod
    monkeypatch.setattr(d_mod, "STRUCTURE_GATE_ENABLED", on)


def _ts_in_killzone():
    """Дата-время гарантированно внутри London killzone (08:30 UTC)."""
    return datetime(2026, 5, 28, 8, 30, tzinfo=timezone.utc)


def _ts_outside_killzone():
    """Дата-время гарантированно вне всех killzone (06:00 UTC)."""
    return datetime(2026, 5, 28, 6, 0, tzinfo=timezone.utc)


def _bullish_klines():
    """Серия с подтверждённым bull-сломом в конце (используем для 5m+15m)."""
    prices = [10, 10, 10, 11, 12, 15, 12, 11, 10, 10, 10, 16]
    return [{"o": p, "h": p, "l": p, "c": p, "v": 100} for p in prices]


def _bearish_klines():
    prices = [20, 20, 20, 19, 18, 15, 18, 19, 20, 20, 20, 14]
    return [{"o": p, "h": p, "l": p, "c": p, "v": 100} for p in prices]


def test_p3_killzone_gate_blocks_outside_window(monkeypatch):
    _enable_killzone(monkeypatch)
    m = _market()
    m["ts"] = _ts_outside_killzone()
    d = make_decision("BOS_BULL", 42500.0, m,
                      {"aligned": 3, "total": 3}, 78, ["CVD ✅"])
    assert d["verdict"] == "WAIT"
    assert "killzone gate" in d["reason"]
    assert d.get("killzone", {}).get("in") is False


def test_p3_killzone_gate_passes_inside_window(monkeypatch):
    _enable_killzone(monkeypatch)
    m = _market()
    m["ts"] = _ts_in_killzone()
    d = make_decision("BOS_BULL", 42500.0, m,
                      {"aligned": 3, "total": 3}, 78, ["CVD ✅"])
    # Killzone OK, structure отключен → должен пройти как LONG
    assert d["verdict"] == "LONG"
    assert d["killzone"]["in"] is True
    assert d["killzone"]["name"] == "London"


def test_p3_structure_gate_blocks_without_confirmation(monkeypatch):
    _enable_structure(monkeypatch)
    m = _market()
    # klines есть, но без подтверждённого слома
    flat = [{"o": 1, "h": 1, "l": 1, "c": 1, "v": 100} for _ in range(20)]
    m["_klines"] = {"5": flat, "15": flat}
    d = make_decision("BOS_BULL", 42500.0, m,
                      {"aligned": 3, "total": 3}, 78, ["CVD ✅"])
    assert d["verdict"] == "WAIT"
    assert "structure gate" in d["reason"]


def test_p3_structure_gate_passes_with_aligned_break(monkeypatch):
    _enable_structure(monkeypatch)
    m = _market()
    m["_klines"] = {"5": _bullish_klines(), "15": _bullish_klines()}
    d = make_decision("BOS_BULL", 42500.0, m,
                      {"aligned": 3, "total": 3}, 78, ["CVD ✅"])
    assert d["verdict"] == "LONG"
    assert d["structure"]["confirmed"] is True
    assert d["structure"]["direction"] == "bull"


def test_p3_structure_gate_blocks_wrong_direction(monkeypatch):
    _enable_structure(monkeypatch)
    m = _market()
    # Слом структуры bear, а сигнал long — расхождение
    m["_klines"] = {"5": _bearish_klines(), "15": _bearish_klines()}
    d = make_decision("BOS_BULL", 42500.0, m,
                      {"aligned": 3, "total": 3}, 78, ["CVD ✅"])
    assert d["verdict"] == "WAIT"
    assert "расхождение" in d["reason"]


def test_p3_structure_gate_graceful_when_klines_missing(monkeypatch):
    _enable_structure(monkeypatch)
    m = _market()  # _market() не задаёт _klines
    d = make_decision("BOS_BULL", 42500.0, m,
                      {"aligned": 3, "total": 3}, 78, ["CVD ✅"])
    # Нет данных → гейт молчит, торгуем как обычно
    assert d["verdict"] == "LONG"
    assert d["structure"]["available"] is False


def test_p3_both_gates_pass_together(monkeypatch):
    _enable_killzone(monkeypatch)
    _enable_structure(monkeypatch)
    m = _market()
    m["ts"] = _ts_in_killzone()
    m["_klines"] = {"5": _bullish_klines(), "15": _bullish_klines()}
    d = make_decision("BOS_BULL", 42500.0, m,
                      {"aligned": 3, "total": 3}, 78, ["CVD ✅"])
    assert d["verdict"] == "LONG"
    assert d["killzone"]["in"] is True
    assert d["structure"]["confirmed"] is True


def test_p3_killzone_subgate_short_circuits_structure(monkeypatch):
    """Killzone проверяется первым — если вне окна, structure не дойдёт."""
    _enable_killzone(monkeypatch)
    _enable_structure(monkeypatch)
    m = _market()
    m["ts"] = _ts_outside_killzone()
    # klines не задаём — даже если бы structure дошёл, упал бы
    d = make_decision("BOS_BULL", 42500.0, m,
                      {"aligned": 3, "total": 3}, 78, ["CVD ✅"])
    assert d["verdict"] == "WAIT"
    assert "killzone gate" in d["reason"]
    # structure ключ не появился (subgate не выполнился)
    assert "structure" not in d


def test_p3_disabled_by_default(monkeypatch):
    """С дефолтными флагами (KILLZONE_GATE_ENABLED=False, STRUCTURE=False)
    через autouse-fixture P3 молчит."""
    m = _market()
    m["ts"] = _ts_outside_killzone()
    # без _klines, но и без _ts — структура и killzone не сработают
    d = make_decision("BOS_BULL", 42500.0, m,
                      {"aligned": 3, "total": 3}, 78, ["CVD ✅"])
    assert d["verdict"] == "LONG"  # гейты выключены autouse-fixture'ом
