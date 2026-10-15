"""Standalone regressions closing independent-review gaps (no commit yet)."""
from unittest.mock import patch

import bt_data
import bt_market
import tracking


def test_gap_through_sl_long_charges_real_slippage_not_flat_minus_1r():
    klines = [{"ts": 0, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1},
              {"ts": 300000, "o": 100, "h": 100, "l": 99, "c": 99.5, "v": 1},
              {"ts": 600000, "o": 96, "h": 96, "l": 90, "c": 91, "v": 1}]
    result = tracking._simulate_execution(
        klines, 0, "LONG", 98, 110, None, None, None, None, None, 5, entry=100)
    assert result[1] == "sl_hit" and result[3] == -2.0


def test_gap_through_sl_short_charges_real_slippage_not_flat_minus_1r():
    klines = [{"ts": 0, "o": 100, "h": 100, "l": 100, "c": 100, "v": 1},
              {"ts": 300000, "o": 100, "h": 101, "l": 100, "c": 100.5, "v": 1},
              {"ts": 600000, "o": 104, "h": 110, "l": 104, "c": 109, "v": 1}]
    result = tracking._simulate_execution(
        klines, 0, "SHORT", 102, 90, None, None, None, None, None, 5, entry=100)
    assert result[1] == "sl_hit" and result[3] == -2.0


def test_compute_change_24h_is_not_quadratic_when_caller_reuses_ts_list():
    """The hot path (bt_market.build_market_at) must reuse one ts_list per
    backtest run instead of rebuilding it on every bar; this is what the
    independent reviewer's O(n^2) finding was actually about. Wall-clock
    timing at microsecond scale is too noisy, so this tracks actual slicing
    of klines_5m instead of racing the clock."""
    class _TrackedKlines(list):
        slice_sizes: list[int] = []

        def __getitem__(self, key):
            if isinstance(key, slice):
                self.slice_sizes.append(len(super().__getitem__(key)))
            return super().__getitem__(key)

    klines = _TrackedKlines({"ts": i * 300000, "c": 100 + i * 0.01}
                             for i in range(8000))
    ts_list = [b["ts"] for b in klines]
    for idx in (10, 4000, 7999):
        bt_market.compute_change_24h(klines, idx, ts_list=ts_list)
    assert klines.slice_sizes == [], (
        "compute_change_24h sliced klines_5m despite receiving a reusable "
        f"ts_list (slice sizes seen: {klines.slice_sizes}); this is exactly "
        "the O(n) per-call cost the reviewer flagged as O(n^2) over a full "
        "backtest run"
    )


def test_build_market_at_passes_the_cached_ts_list_into_change_24h():
    """build_market_at must not let compute_change_24h rebuild its ts_list
    from scratch on every bar of a backtest run (reviewer finding)."""
    klines_5m = [{"ts": i * 300000, "o": 100, "h": 101, "l": 99,
                  "c": 100 + i * 0.001, "v": 1} for i in range(400)]
    data = {"klines": {"5": klines_5m}}
    with patch.object(bt_data, "_now_ms", return_value=400 * 300000), \
            patch.object(bt_market, "compute_change_24h",
                         wraps=bt_market.compute_change_24h) as spy:
        bt_market.build_market_at(data, 399, tf_primary="5")
    assert spy.call_args.kwargs.get("ts_list") is not None, (
        "compute_change_24h called without a reusable ts_list: "
        "degrades to O(n^2) over a full backtest run")
