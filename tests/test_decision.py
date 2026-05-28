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
