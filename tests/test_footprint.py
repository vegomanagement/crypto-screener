"""Pure footprint aggregation: exact tick math, venue side mapping, analytics."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

import footprint as fp

FIXTURE = Path(__file__).parent / "fixtures" / "footprint_parity.json"


def T(time, price, qty, side, count=1, ident=None):
    return fp.Trade(time_ms=time, price=Decimal(price), qty=Decimal(qty), side=side,
                    count=count, ident=ident)


# ── tick math ────────────────────────────────────────────────────────────────

def test_bucket_index_is_exact_decimal_floor_without_float_drift():
    step = Decimal("0.1")
    # 0.3 / 0.1 in float is 2.9999999999999996 → floor would give 2.
    assert fp.bucket_index(Decimal("0.3"), step) == 3
    assert fp.bucket_index(Decimal("0.39"), step) == 3
    assert fp.bucket_index(Decimal("81510.70"), Decimal("0.10")) == 815107
    assert fp.bucket_index(Decimal("81510.79"), Decimal("0.5")) == 163021
    assert fp.bucket_price(163021, Decimal("0.5")) == Decimal("81510.5")


def test_decimal_parsing_rejects_float_noise_bool_and_nonfinite():
    assert fp.dec("81510.70") == Decimal("81510.70")
    assert fp.dec(5) == Decimal(5)
    for bad in (True, None, "nan", "inf", "-1", "", "1e400000", object()):
        with pytest.raises(ValueError):
            fp.dec(bad)
    with pytest.raises(ValueError):
        fp.dec(0.1)  # floats are never accepted; venues send decimal strings


def test_group_ticks_must_be_positive_integer():
    acc = fp.BarAccumulator(open_ms=0, interval_ms=60_000, tick_size=Decimal("0.1"))
    with pytest.raises(ValueError):
        fp.grouped_levels(acc, 0)
    with pytest.raises(ValueError):
        fp.grouped_levels(acc, True)


# ── venue side mapping ───────────────────────────────────────────────────────

def test_binance_buyer_maker_is_seller_aggressor_bid():
    sell = fp.normalize_binance({"a": 10, "p": "100.0", "q": "0.5", "f": 3, "l": 5,
                                 "T": 1000, "m": True})
    buy = fp.normalize_binance({"a": 11, "p": "100.1", "q": "0.25", "f": 6, "l": 6,
                                "T": 1001, "m": False})
    assert (sell.side, sell.count, sell.ident) == ("bid", 3, 10)
    assert (buy.side, buy.count, buy.qty) == ("ask", 1, Decimal("0.25"))


def test_bybit_taker_side_rest_and_ws_shapes():
    rest = fp.normalize_bybit({"execId": "x", "price": "100.5", "size": "2", "side": "Buy",
                               "time": "1700000000001"})
    ws = fp.normalize_bybit({"i": "y", "p": "100.4", "v": "1", "S": "Sell", "T": 1700000000002})
    assert (rest.side, rest.time_ms, rest.ident) == ("ask", 1700000000001, "x")
    assert (ws.side, ws.price, ws.ident) == ("bid", Decimal("100.4"), "y")
    with pytest.raises(ValueError):
        fp.normalize_bybit({"execId": "z", "price": "1", "size": "1", "side": "None", "time": "1"})


def test_hyperliquid_b_is_buy_aggressor_a_is_seller_aggressor():
    b = fp.normalize_hyperliquid({"px": "81528.0", "sz": "0.02", "side": "B", "time": 5, "tid": 1})
    a = fp.normalize_hyperliquid({"px": "81528.0", "sz": "0.03", "side": "A", "time": 6, "tid": 2})
    assert (b.side, a.side) == ("ask", "bid")


def test_hyperliquid_tick_follows_five_significant_figures_and_sz_decimals():
    assert fp.hyperliquid_tick(Decimal("81528"), 5) == Decimal("1")
    assert fp.hyperliquid_tick(Decimal("3012.4"), 4) == Decimal("0.1")
    assert fp.hyperliquid_tick(Decimal("0.0123"), 0) == Decimal("0.000001")
    assert fp.hyperliquid_tick(Decimal("0.0123"), 5) == Decimal("0.1") ** 1  # max decimals 6-5=1
    assert fp.hyperliquid_tick(Decimal("12.345"), 2) == Decimal("0.001")
    assert fp.hyperliquid_tick(Decimal("1.2345"), 2) == Decimal("0.0001")


# ── accumulation ─────────────────────────────────────────────────────────────

def test_accumulator_sums_exact_and_rejects_out_of_window_trades():
    acc = fp.BarAccumulator(open_ms=60_000, interval_ms=60_000, tick_size=Decimal("0.1"))
    assert acc.add(T(60_000, "100.0", "0.1", "ask")) is True
    assert acc.add(T(60_001, "100.0", "0.2", "ask")) is True
    assert acc.add(T(119_999, "100.1", "0.3", "bid", count=4)) is True
    assert acc.add(T(120_000, "100.1", "9", "bid")) is False  # next bar
    assert acc.add(T(59_999, "100.1", "9", "bid")) is False
    levels = fp.grouped_levels(acc, 1)
    assert [(lv["price"], lv["bid"], lv["ask"], lv["bid_trades"], lv["ask_trades"]) for lv in levels] == [
        (Decimal("100.0"), Decimal(0), Decimal("0.3"), 0, 2),
        (Decimal("100.1"), Decimal("0.3"), Decimal(0), 4, 0),
    ]
    assert acc.trade_count == 6
    assert (acc.open, acc.high, acc.low, acc.close) == (
        Decimal("100.0"), Decimal("100.1"), Decimal("100.0"), Decimal("100.1"))


def test_grouping_floors_to_tick_times_group():
    acc = fp.BarAccumulator(open_ms=0, interval_ms=60_000, tick_size=Decimal("0.1"))
    for price in ("100.0", "100.4", "100.5", "100.9", "101.0"):
        acc.add(T(1, price, "1", "ask"))
    grouped = fp.grouped_levels(acc, 5)
    assert [(lv["price"], lv["ask"]) for lv in grouped] == [
        (Decimal("100.0"), Decimal(2)), (Decimal("100.5"), Decimal(2)), (Decimal("101.0"), Decimal(1))]


# ── analytics ────────────────────────────────────────────────────────────────

def lv(index, bid, ask):
    return {"index": index, "price": Decimal(index), "bid": Decimal(bid), "ask": Decimal(ask),
            "bid_trades": 0, "ask_trades": 0}


def analyze(levels, **settings):
    return fp.analyze(levels, Decimal(1), {**fp.DEFAULT_SETTINGS, **settings})


def test_totals_delta_and_delta_pct():
    out = analyze([lv(1, "3", "1"), lv(2, "1", "5")])
    assert (out["total"], out["buy_vol"], out["sell_vol"], out["delta"]) == (
        Decimal(10), Decimal(6), Decimal(4), Decimal(2))
    assert out["delta_pct"] == Decimal("20")
    assert analyze([lv(1, "2", "1")])["delta_pct"] == Decimal("-33.33")
    assert analyze([])["delta_pct"] is None


def test_poc_ties_take_lower_price_and_delta_poc_threshold():
    out = analyze([lv(1, "2", "2"), lv(2, "4", "0"), lv(3, "0", "4")])
    assert out["poc"] == Decimal(1)  # all levels trade 4; the lowest price wins
    assert out["delta_poc"] == Decimal(2)  # |−4| ties |+4|, lower price wins
    assert analyze([lv(1, "2", "2"), lv(2, "4", "0")], min_delta=Decimal(5))["delta_poc"] is None
    assert analyze([lv(1, "2", "2")])["delta_poc"] is None  # zero delta is never a delta POC


def test_value_area_expands_from_poc_by_larger_neighbour_including_empty_levels():
    # volumes by index: 1:5 2:0 3:10 4:30 5:20 6:5  total 70, 70% → 49
    levels = [lv(1, "5", "0"), lv(3, "5", "5"), lv(4, "15", "15"), lv(5, "10", "10"), lv(6, "0", "5")]
    out = analyze(levels)
    assert out["poc"] == Decimal(4)
    # 30 → +20 (idx5)=50 ≥ 49 → stop
    assert (out["va_low"], out["va_high"]) == (Decimal(4), Decimal(5))
    out = analyze(levels, va_pct=90)  # 63: 50 → up 5 (idx6) vs down 10 (idx3): take 10 → 60 → up 5 vs down 0 → 65
    assert (out["va_low"], out["va_high"]) == (Decimal(3), Decimal(6))
    out = analyze(levels, va_pct=100)
    assert (out["va_low"], out["va_high"]) == (Decimal(1), Decimal(6))


def test_value_area_equal_neighbours_go_up_first():
    out = analyze([lv(1, "1", "0"), lv(2, "5", "5"), lv(3, "1", "0")], va_pct=90)
    assert (out["va_low"], out["va_high"]) == (Decimal(2), Decimal(3))


def test_diagonal_imbalance_compares_ask_at_p_with_bid_one_level_below():
    # idx1 bid 1 ask 0 | idx2 bid 2 ask 3 | idx3 bid 0 ask 6
    levels = [lv(1, "1", "0"), lv(2, "2", "3"), lv(3, "0", "6")]
    out = analyze(levels)
    # buy@2: ask 3 vs bid@1 1 → 300% ≥ 300 → yes; buy@3: ask 6 vs bid@2 2 → 300% yes
    # sell@1: bid 1 vs ask@2 3 → no; sell@2: bid 2 vs ask@3 6 → no
    assert out["imbalances"] == [
        {"price": Decimal(2), "side": "buy", "ratio": Decimal("300")},
        {"price": Decimal(3), "side": "buy", "ratio": Decimal("300")},
    ]
    assert analyze(levels, imbalance_ratio=301)["imbalances"] == []
    assert analyze(levels, imbalance_min_diff=Decimal("2.5"))["imbalances"] == [
        {"price": Decimal(3), "side": "buy", "ratio": Decimal("300")}]


def test_include_zero_controls_infinite_ratio_imbalances_and_edges_are_skipped():
    levels = [lv(1, "0", "4"), lv(2, "5", "0"), lv(3, "0", "0")]
    # buy@2: ask 0 → never. sell@2: bid 5 vs ask@3 0 → only with include_zero.
    # buy@1 is the bottom edge: no level below inside the bar → skipped.
    assert analyze(levels)["imbalances"] == []
    assert analyze(levels, include_zero=True)["imbalances"] == [
        {"price": Decimal(2), "side": "sell", "ratio": None}]
    # sell@1: bid 0 → never; ask@1=4 vs bid@0 (outside bar) → never.


def test_stacked_imbalance_requires_n_consecutive_levels():
    levels = [lv(i, "1", "4") for i in range(1, 6)]  # buy@2..5 all 400%
    out = analyze(levels)
    assert out["stacked"] == [{"side": "buy", "from": Decimal(2), "to": Decimal(5), "count": 4}]
    assert analyze(levels, stacked_min=5)["stacked"] == []
    # buy flags at 2,3 and 5,6; level 4 (ask 1 vs bid 1) breaks the run.
    gap = [lv(1, "1", "1"), lv(2, "1", "4"), lv(3, "1", "4"), lv(4, "1", "1"),
           lv(5, "1", "4"), lv(6, "1", "4")]
    assert analyze(gap, stacked_min=2)["stacked"] == [
        {"side": "buy", "from": Decimal(2), "to": Decimal(3), "count": 2},
        {"side": "buy", "from": Decimal(5), "to": Decimal(6), "count": 2}]
    assert analyze(gap, stacked_min=3)["stacked"] == []


def test_trades_input_uses_counts_not_volume():
    levels = [{"index": 1, "price": Decimal(1), "bid": Decimal(9), "ask": Decimal(0),
               "bid_trades": 1, "ask_trades": 0},
              {"index": 2, "price": Decimal(2), "bid": Decimal(0), "ask": Decimal(1),
               "bid_trades": 0, "ask_trades": 5}]
    out = fp.analyze(levels, Decimal(1), {**fp.DEFAULT_SETTINGS, "input": "trades"})
    assert (out["total"], out["poc"], out["delta"]) == (Decimal(6), Decimal(2), Decimal(4))


def test_bar_payload_is_json_with_exact_decimal_strings_and_coverage():
    acc = fp.BarAccumulator(open_ms=60_000, interval_ms=60_000, tick_size=Decimal("0.10"))
    acc.add(T(60_000, "0.30", "0.001", "ask"))
    acc.add(T(60_500, "0.20", "0.002", "bid"))
    bar = fp.bar_payload(acc, 1, coverage="complete", source="binance")
    json.dumps(bar)
    assert bar["time"] == 60 and bar["close_time"] == 119_999
    assert bar["levels"][0] == {"price": "0.2", "bid": "0.002", "ask": "0", "trades": 1,
                                "bid_trades": 1, "ask_trades": 0}
    assert (bar["total"], bar["delta"], bar["coverage"]) == ("0.003", "-0.001", "complete")
    with pytest.raises(ValueError):
        fp.bar_payload(acc, 1, coverage="interpolated", source="binance")


def test_auto_group_picks_nice_multiple_bounding_levels():
    assert fp.auto_group_ticks([500], target_levels=60) == 10
    assert fp.auto_group_ticks([30], target_levels=60) == 1
    assert fp.auto_group_ticks([], target_levels=60) == 1
    assert fp.auto_group_ticks([12000], target_levels=60) == 200


# ── shared parity fixture (same file is asserted by tests/footprint.test.cjs) ─

def test_shared_parity_fixture_matches_python_engine():
    cases = json.loads(FIXTURE.read_text())["cases"]
    assert len(cases) >= 3
    for case in cases:
        acc = fp.BarAccumulator(open_ms=case["open_ms"], interval_ms=case["interval_ms"],
                                tick_size=Decimal(case["tick_size"]))
        for t in case["trades"]:
            acc.add(fp.Trade(time_ms=t["time"], price=Decimal(t["price"]), qty=Decimal(t["qty"]),
                             side=t["side"], count=t.get("count", 1)))
        settings = fp.settings_from_json(case.get("settings", {}))
        got = fp.bar_payload(acc, case["group_ticks"], coverage="complete", source="fixture",
                             settings=settings)
        for key, expected in case["expected"].items():
            assert got[key] == expected, (case["name"], key)
