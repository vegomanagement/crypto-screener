"""Derived positioning labels: thresholds, formulas and missing-input handling."""
from positioning_signals import (crowd_extreme, funding, liquidation_imbalance, oi_price, taker_flow,
                                 top_vs_crowd, whale_bias)


def test_top_vs_crowd_formula_and_states():
    s = top_vs_crowd({"long": "0.70", "value": "2.3333"}, {"long": "0.60", "value": "1.5"})
    assert s["state"] == "top_more_long" and s["value"] == "10"
    assert s["inputs"]["ratio_diff"] == "0.8333"
    assert top_vs_crowd({"long": "0.40", "value": "1"}, {"long": "0.50", "value": "1"})["state"] == "top_more_short"
    assert top_vs_crowd({"long": "0.52", "value": "1"}, {"long": "0.50", "value": "1"})["state"] == "aligned"
    missing = top_vs_crowd({"long": None}, {"long": "0.5"})
    assert missing["state"] is None and missing["reason"]
    assert "not a trade signal" in s["note"]


def test_oi_price_quadrants():
    assert oi_price({"value": "2", "price_pct": "1"})["state"] == "longs_building"
    assert oi_price({"value": "2", "price_pct": "-1"})["state"] == "shorts_building"
    assert oi_price({"value": "-2", "price_pct": "1"})["state"] == "short_covering"
    assert oi_price({"value": "-2", "price_pct": "-1"})["state"] == "long_unwinding"
    assert oi_price({"value": "0.5", "price_pct": "-1"})["state"] == "no_clear_change"
    assert oi_price({"value": None, "price_pct": "1"})["state"] is None


def test_taker_crowd_funding_thresholds():
    assert taker_flow({"value": "1.10"})["state"] == "buyers_aggressive"
    assert taker_flow({"value": "0.90"})["state"] == "sellers_aggressive"
    assert taker_flow({"value": "1.0"})["state"] == "balanced"
    assert crowd_extreme({"long": "0.7154"})["state"] == "crowd_heavily_long"
    assert crowd_extreme({"long": "0.30"})["state"] == "crowd_heavily_short"
    assert funding({"funding_rate": "-0.0001"})["state"] == "shorts_pay"
    assert funding(None)["state"] is None


def test_whale_and_liquidation_labels():
    agg = {"aggregate": {"long_notional": "600", "short_notional": "400"}}
    assert whale_bias(agg)["state"] == "whales_net_long" and whale_bias(agg)["value"] == "0.6"
    assert whale_bias({"aggregate": None})["state"] is None
    liq = {"windows": {"1h": {"long_usd": "80", "short_usd": "20"}}}
    assert liquidation_imbalance(liq)["state"] == "longs_flushed"
    zero = {"windows": {"1h": {"long_usd": "0", "short_usd": "0"}}}
    assert liquidation_imbalance(zero)["state"] == "none_recorded"
    assert liquidation_imbalance({"reason": "Hyperliquid has no public liquidation stream"})["reason"].startswith("Hyperliquid")
