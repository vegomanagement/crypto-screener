"""analyst.py: snapshot assembly with injected fakes (no network)."""
import json
import math
from pathlib import Path

import pytest

import analyst
import funding

FUNDING_RAW = json.loads((Path(__file__).parent / "fixtures" / "funding" / "live_raw.json").read_text())
POSITIONING_SNAP = json.loads((Path(__file__).parent / "fixtures" / "positioning" /
                               "snapshot_binance_BTCUSDT.json").read_text())

START = 1_700_000_000_000
STEP = 900_000


def make_candles(n=120, base=100.0, slope=0.05):
    out = []
    for i in range(n):
        o = base + i * slope + math.sin(i / 4) * 2
        c = o + math.cos(i / 3)
        out.append({"ts": START + i * STEP, "close_ts": START + (i + 1) * STEP - 1,
                    "o": o, "h": max(o, c) + 0.6, "l": min(o, c) - 0.6, "c": c, "v": 10 + i % 7,
                    "source": "binance"})
    return out


def fake_market(price=105.0):
    k1h = [{"o": 100 + i * .1, "h": 101 + i * .1, "l": 99 + i * .1, "c": 100.5 + i * .1, "v": 5}
           for i in range(60)]
    return {"price": price, "change_24h": 1.2, "bybit": {"funding": 0.0001, "oi_chg": 0.5},
            "cvd": {"trend": "up", "price_trend": "up", "divergence": False},
            "macro": {"fg_value": 40, "fg_label": "Fear"},
            "indicators": {"atr": 1.2, "rsi": 50},
            "_klines": {"60": k1h, "240": k1h, "D": k1h}}


def providers(**over):
    candles = make_candles()
    base = {
        "clock": lambda: START + 200 * STEP,
        "chart": lambda e, s, i: {"source": e, "candles": candles,
                                  "zones": [{"type": "fvg", "direction": "bull", "top": 104.0,
                                             "bottom": 103.0, "start_ts": START}]},
        "market": lambda s: fake_market(),
        "context": lambda e, s: {"exchange": e, "as_of": START, "funding_rate": "0.0001",
                                 "open_interest": "1234", "mark_price": "105", "index_price": "105",
                                 "warnings": []},
        "footprint": lambda e, s, i: {"exchange": e, "as_of": START, "bars": [
            {"open_time": START, "delta": "-3.5", "total": "40", "poc": "104.5",
             "coverage": "complete", "forming": False}]},
        "depth": lambda e, s: {"exchange": e, "time": START, "bids": [[104.9, 3.0], [104.8, 1.0]],
                               "asks": [[105.0, 1.0], [105.1, 1.0]]},
        "confluence": lambda sig, m, mtf: (40, ["f1"]) if "BULL" in sig else (20, ["f2"]),
        "funding": lambda e, s: {**funding.binance(
            "BTCUSDT", FUNDING_RAW["binance_premiumIndex_BTCUSDT"], FUNDING_RAW["binance_fundingRate_BTCUSDT"],
            FUNDING_RAW["binance_fundingInfo_subset"], START),
            "cross_venue": funding.cross_venue(FUNDING_RAW["hl_predictedFundings_subset"], "BTC", START)},
        "positioning": lambda e, s, p: {**POSITIONING_SNAP, "exchange": e, "symbol": s, "period": p},
    }
    base.update(over)
    return base


def test_indicators_match_reference():
    assert analyst.ema([1, 2, 3], 3) == 2
    assert analyst.ema([1, 2], 3) is None
    assert analyst.rsi([1, 2, 3, 4], 3) == 100.0
    assert analyst.atr(make_candles(10), 14) is None


def test_full_snapshot_has_sources_and_no_missing():
    snap = analyst.build_snapshot("binance", "BTCUSDT", "15", providers())
    assert snap["missing"] == {}
    for name in ("candles", "indicators", "structure", "fvg", "order_blocks", "session",
                 "liquidity", "htf_bias", "regime", "flow", "derivatives", "footprint",
                 "orderbook", "baseline", "funding", "positioning"):
        assert snap[name] is not None, name
        assert snap[name]["source"] and snap[name]["ts"], name
    assert snap["orderbook"]["imbalance"] == pytest.approx((4 - 2) / 6, rel=1e-3)
    assert snap["baseline"]["long_pct"] + snap["baseline"]["short_pct"] == 100
    assert snap["baseline"]["long_pct"] == 67
    assert snap["candles"]["closed_only"] is True
    fund = snap["funding"]
    assert fund["predicted"]["rate"] == funding.fmt(funding.dec(
        FUNDING_RAW["binance_premiumIndex_BTCUSDT"]["lastFundingRate"]))
    assert fund["last_settled"]["rate"] and fund["interval_hours"] == 8
    assert {r["venue"] for r in fund["cross_venue"]} == {"binance", "bybit", "hyperliquid"}
    assert analyst.resolve_path(snap, "funding.predicted.rate")
    assert "funding_rate" not in snap["derivatives"]
    assert snap["price"]["value"] == pytest.approx(make_candles()[-1]["c"])


def test_failed_providers_are_null_with_reason():
    def boom(*a):
        raise RuntimeError("venue down")
    snap = analyst.build_snapshot("binance", "BTCUSDT", "240",
                                  providers(market=boom, depth=boom, context=boom, funding=boom))
    for name in ("liquidity", "htf_bias", "regime", "flow", "orderbook", "derivatives",
                 "baseline", "footprint", "funding"):
        assert snap[name] is None
        assert snap["missing"][name]
    assert "venue down" in snap["missing"]["orderbook"]
    assert "240" not in analyst.FOOTPRINT_INTERVALS and "footprint" in snap["missing"]
    assert snap["indicators"]["ema7"] is not None


def test_chart_failure_raises():
    def boom(*a):
        raise RuntimeError("no candles")
    with pytest.raises(ValueError, match="chart candles unavailable"):
        analyst.build_snapshot("binance", "BTCUSDT", "15", providers(chart=boom))


def test_hyperliquid_hip3_symbol_has_no_market_mapping():
    assert analyst.market_symbol("hyperliquid", "xyz:TSLA") is None
    assert analyst.market_symbol("hyperliquid", "BTC") == "BTCUSDT"
    assert analyst.market_symbol("bybit", "1000PEPEUSDT") == "1000PEPEUSDT"
    assert analyst.market_symbol("binance", "BTCUSDC") is None


def test_resolve_path_and_bounds():
    snap = analyst.build_snapshot("binance", "BTCUSDT", "15", providers())
    assert analyst.resolve_path(snap, "fvg.nearest.0.top")
    assert analyst.resolve_path(snap, "fvg.nearest[0].top")
    assert not analyst.resolve_path(snap, "fvg.nearest.9.top")
    assert not analyst.resolve_path(snap, "indicators.macd")
    lo, hi = analyst.price_bounds(snap)
    atr = snap["indicators"]["atr14"]
    assert lo == pytest.approx(snap["candles"]["window_low"] - 3 * atr)
    assert hi == pytest.approx(snap["candles"]["window_high"] + 3 * atr)
    h1 = analyst.snapshot_hash(snap)
    snap["generated_at"] += 1
    assert analyst.snapshot_hash(snap) == h1
