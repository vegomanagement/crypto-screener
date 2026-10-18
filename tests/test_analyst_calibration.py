"""Calibrated split (baseline cap / conflict rule), EMA ribbon periods and the backtest replay."""
import itertools
import math

import pytest

import analyst
import analyst_backtest as ab
from analyst import SPLIT_CONFIG, compute_split, indicators_section, normalize_ema_periods
from tests.test_analyst import providers


def F(direction, strength, field="indicators.close"):
    return {"factor": f"{direction}{strength}", "direction": direction, "strength": strength,
            "source_fields": [field]}


def tally(long_pts, short_pts):
    """Factors adding up to the given long/short points (strength-1 factors)."""
    return [F("long", 1, f"a.{i}") for i in range(long_pts)] + [F("short", 1, f"b.{i}") for i in range(short_pts)]


STRONG_LONG_BASE = {"long_score": 95, "short_score": 5, "long_verdict": "LONG", "short_verdict": "SKIP"}
STRONG_SHORT_BASE = {"long_score": 5, "short_score": 95, "long_verdict": "SKIP", "short_verdict": "SHORT"}


# ── FIX 1: the baseline cannot flip a clear factor majority ────────────────

def test_six_long_vs_thirteen_short_with_a_maxed_long_baseline_is_not_long():
    # The live case: factors 6 long vs 13 short points, confluence baseline +1.00.
    s = compute_split(tally(6, 13), STRONG_LONG_BASE)
    assert s["agent_net"] == pytest.approx(-7 / 19, abs=1e-4)
    assert s["baseline_raw"] == 1.0 and s["baseline_net"] == SPLIT_CONFIG["baseline_cap"]
    assert s["conflict"] and s["weights"]["baseline"] == 0.0
    assert s["bias"] == "short" and s["long_pct"] < 50
    # The old blend (w 0.65/0.35, no cap, renormalised) gave +0.11 → 58% long.
    old = 0.65 * (-7 / 19) + 0.35 * 1.0
    assert round(100 / (1 + math.exp(-3 * old))) == 58


@pytest.mark.parametrize("long_pts,short_pts", [(lp, sp) for lp in range(0, 14) for sp in range(0, 14) if lp + sp])
def test_split_sign_never_opposes_the_factor_tally(long_pts, short_pts):
    for base in (STRONG_LONG_BASE, STRONG_SHORT_BASE, None):
        s = compute_split(tally(long_pts, short_pts), base)
        if long_pts > short_pts:
            assert s["bias"] != "short" and s["net"] >= 0
        elif short_pts > long_pts:
            assert s["bias"] != "long" and s["net"] <= 0
        else:
            assert s["bias"] == "neutral"


def test_clear_tally_ignores_the_baseline_and_a_near_tie_is_never_flipped():
    clear = tally(4, 1)        # agent_net = 3/6 = 0.5 ≥ conflict_min_agent
    s = compute_split(clear, STRONG_SHORT_BASE)
    assert s["conflict"] and s["net"] == pytest.approx(SPLIT_CONFIG["w_agent"] * 0.5)
    weak = tally(4, 3)         # agent_net = 1/7 ≈ 0.143: no conflict rule, the baseline only shifts
    s = compute_split(weak, STRONG_SHORT_BASE)
    assert not s["conflict"] and not s["flip_guard"] and 0 < s["net"] < SPLIT_CONFIG["w_agent"] / 7
    tie = tally(10, 9)         # agent_net = 1/19: the capped baseline would flip it → guard → 0
    s = compute_split(tie, STRONG_SHORT_BASE)
    assert s["flip_guard"] and s["net"] == 0.0 and s["bias"] == "neutral" and s["long_pct"] == 50


def test_baseline_alone_never_leaves_the_neutral_band():
    cfg = SPLIT_CONFIG
    assert cfg["k"] * cfg["w_baseline"] * cfg["baseline_cap"] < math.log(55 / 45)
    for ls, ss, lv, sv in itertools.product((0, 30, 70, 100), (0, 30, 70, 100), ("WAIT", "LONG", "SKIP"),
                                            ("WAIT", "SHORT", "SKIP")):
        s = compute_split([], {"long_score": ls, "short_score": ss, "long_verdict": lv, "short_verdict": sv})
        assert s["bias"] == "neutral" and abs(s["p_long"] - 50) < cfg["neutral_band"]


def test_symmetry_bounds_and_rounding_over_grid():
    cfg = SPLIT_CONFIG
    hi_net = cfg["w_agent"] + cfg["w_baseline"] * cfg["baseline_cap"]
    for lp, sp in itertools.product(range(0, 9, 2), range(0, 9, 3)):
        for ls, ss, lv, sv in itertools.product((10, 50, 90), (10, 50, 90), ("WAIT", "LONG"), ("WAIT", "SHORT")):
            base = {"long_score": ls, "short_score": ss, "long_verdict": lv, "short_verdict": sv}
            mirror = {"long_score": ss, "short_score": ls, "long_verdict": "LONG" if sv == "SHORT" else "WAIT",
                      "short_verdict": "SHORT" if lv == "LONG" else "WAIT"}
            a, b = compute_split(tally(lp, sp), base), compute_split(tally(sp, lp), mirror)
            assert a["long_pct"] == b["short_pct"] and a["p_long"] == pytest.approx(100 - b["p_long"])
            assert a["long_pct"] + a["short_pct"] == 100 and abs(a["net"]) <= hi_net + 1e-9
            lo_p, hi_p = 100 / (1 + math.exp(cfg["k"] * hi_net)), 100 / (1 + math.exp(-cfg["k"] * hi_net))
            assert lo_p - 1e-3 <= a["p_long"] <= hi_p + 1e-3  # p_long is rounded to 3 decimals


def test_neutral_band_edges():
    band = SPLIT_CONFIG["neutral_band"]
    s = compute_split(tally(1, 0), None)          # agent_net 1/6 → p ≈ 60.9
    assert s["bias"] == "long" and s["p_long"] - 50 >= band
    s = compute_split(tally(7, 6), None)          # agent_net 1/13 → p ≈ 55.2
    assert (s["bias"] == "neutral") == (abs(s["p_long"] - 50) < band)


# ── FIX 2: EMA ribbon periods follow the chart ─────────────────────────────

def candles(n=260):
    return [{"ts": i * 900_000, "close_ts": (i + 1) * 900_000, "o": 100 + i, "h": 101 + i,
             "l": 99 + i, "c": 100 + i, "v": 1.0} for i in range(n)]


def test_indicators_default_to_the_chart_ribbon_7_14_21():
    ind = indicators_section(candles())
    assert ind["ema_periods"] == [7, 14, 21] and ind["ema_stack"] == "bullish"
    closes = [c["c"] for c in candles()]
    assert ind["ema_slow"] == pytest.approx(analyst.ema(closes, 21))
    assert "ema28" not in ind and "ema_stack_7_14_28" not in ind


def test_custom_periods_sorted_and_validated():
    ind = indicators_section(candles(), (55, 9, 21))
    assert ind["ema_periods"] == [9, 21, 55]
    assert ind["ema_fast"] == pytest.approx(analyst.ema([c["c"] for c in candles()], 9))
    assert indicators_section(candles(), (9, 21))["ema_slow"] is not None
    assert indicators_section(candles(), (9,))["ema_stack"] is None
    assert indicators_section(candles(50), (9, 21, 400))["ema_slow"] is None
    for bad in ([1], [401], [7, 14, 21, 28], [7.5], ["7"], [], True, "7,14"):
        with pytest.raises(ValueError):
            normalize_ema_periods(bad)
    assert normalize_ema_periods(None) == (7, 14, 21)


def test_snapshot_uses_requested_periods_and_reads_legacy_shape():
    snap = analyst.build_snapshot("binance", "BTCUSDT", "15", providers(), ema_periods=[9, 21, 55])
    assert snap["indicators"]["ema_periods"] == [9, 21, 55]
    assert [p for p, _ in analyst.ema_ribbon(snap["indicators"])] == [9, 21, 55]
    legacy = {"ema7": 1.0, "ema14": 2.0, "ema28": 3.0, "ema_stack_7_14_28": "bearish"}
    assert analyst.ema_ribbon(legacy) == [(7, 1.0), (14, 2.0), (28, 3.0)]


# ── TASK 3: the replay never sees the future ───────────────────────────────

def htf_series(n_hours=24 * 60, start=0):
    hour = 3_600_000

    def series(step, count):
        return [{"ts": start + i * step, "close_ts": start + (i + 1) * step, "o": 100.0 + i % 7,
                 "h": 110.0 + i % 3, "l": 90.0 - i % 3, "c": 100.5 + i % 5, "v": 10.0 + i % 3}
                for i in range(count)]
    return {"60": series(hour, n_hours), "240": series(4 * hour, n_hours // 4), "D": series(24 * hour, n_hours // 24)}


def test_closed_upto_excludes_candles_closing_after_as_of():
    rows = htf_series()["60"]
    as_of = rows[100]["close_ts"] + 1_800_000          # mid-way through bar 101
    out = ab.closed_upto(rows, as_of, 50)
    assert out[-1] is rows[100] and len(out) == 50 and all(c["close_ts"] <= as_of for c in out)


def test_historical_market_is_identical_whatever_happens_later():
    import screener as sc
    htf = htf_series()
    as_of = htf["60"][24 * 40]["close_ts"]
    funding = [{"time": t, "rate": 0.0001 * (1 if t <= as_of else 50)} for t in range(0, 24 * 60 * 3_600_000, 8 * 3_600_000)]
    fg = [{"time": d * 86_400_000, "value": 40 if d * 86_400_000 <= as_of else 99} for d in range(60)]
    before = ab.historical_market(sc, as_of, 100.0, htf, funding=funding, fear_greed=fg)
    # Poison every candle/funding/F&G point after as_of: the replay must not change.
    poisoned = {tf: [c if c["close_ts"] <= as_of else {**c, "h": 1e9, "l": 1e-9, "c": 1e6, "v": 1e9}
                     for c in rows] for tf, rows in htf.items()}
    funding2 = [r if r["time"] <= as_of else {**r, "rate": 0.9} for r in funding]
    fg2 = [r if r["time"] <= as_of else {**r, "value": 1} for r in fg]
    after = ab.historical_market(sc, as_of, 100.0, poisoned, funding=funding2, fear_greed=fg2)
    for k in ("cvd", "vp", "ema_biases", "turtle_1h", "turtle_4h", "indicators", "vwap", "pivots",
              "fr_history", "bybit", "macro", "session"):
        assert before[k] == after[k], k
    assert all(c["close_ts"] <= as_of for rows in before["_klines"].values() for c in rows)
    assert before["bybit"]["funding"] == 0.0001 and before["macro"]["fg_value"] == 40
    assert ab.baseline_at(sc, as_of, 100.0, htf, funding=funding, fear_greed=fg) == \
        ab.baseline_at(sc, as_of, 100.0, poisoned, funding=funding2, fear_greed=fg2)


def test_historical_snapshot_ends_at_the_bar_and_names_what_it_cannot_replay():
    import screener as sc
    htf = htf_series()
    chart = [{**c, "source": "binance"} for c in htf["60"]]
    as_of = chart[24 * 40]["close_ts"]
    snap = ab.historical_snapshot(sc, "binance", "BTCUSDT", "60", chart, as_of, htf)
    assert snap["candles"]["last_close_ts"] == as_of and snap["generated_at"] == as_of
    assert snap["price"]["value"] == chart[24 * 40]["c"]
    for name in ("derivatives", "orderbook", "footprint", "positioning", "funding"):
        assert snap[name] is None and snap["missing"][name]
    assert snap["baseline"] is not None and snap["indicators"]["ema_periods"] == [7, 14, 21]


def test_wilson_and_bucket_summary():
    assert ab.wilson(0, 0) == (None, None)
    lo, hi = ab.wilson(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)
    rows = [{"b": 0.8, "touch": "up"}, {"b": 0.8, "touch": "down"}, {"b": 0.8, "touch": "up"},
            {"b": -0.3, "touch": "down"}, {"b": 0.0, "touch": "timeout"}]
    out = {r["bucket"]: r for r in ab.summarize(rows, key="b")}
    assert out["[+0.5, +1]"]["hits"] == 2 and out["[+0.5, +1]"]["hit_rate_decided"] == pytest.approx(2 / 3, abs=1e-4)
    assert out["[-0.5, -0.2]"]["lean"] == "short" and out["[-0.5, -0.2]"]["hits"] == 1
    assert out["0 (no lean)"]["lean"] is None and out["0 (no lean)"]["timeout"] == 1
