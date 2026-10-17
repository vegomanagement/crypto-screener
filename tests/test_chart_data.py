"""Offline contract tests for the read-only chart workspace."""
import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest


def bar(ts, duration=60_000, low=9, high=11, source="bybit"):
    from market_candles import INTERVAL_MS
    interval = next(value for value, ms in INTERVAL_MS.items() if ms == duration)
    return {"ts": ts, "close_ts": ts + duration, "o": low, "h": high,
            "l": low, "c": high, "v": 2, "source": source, "symbol": "BTCUSDT",
            "interval": interval, "closed": True}


def snapshot(rows, interval="1", limit=10, as_of_ms=10**15):
    from chart_data import build_chart_snapshot
    calls = []

    def fetch(symbol, base_interval, count):
        calls.append((symbol, base_interval, count))
        return rows

    result = build_chart_snapshot("BTCUSDT", interval, limit, fetch, as_of_ms)
    return result, calls


def test_native_closed_candles_preserve_provenance_without_mutation():
    rows = [bar(0), bar(60_000), bar(120_000)]
    result, calls = snapshot(rows, limit=2, as_of_ms=120_000)
    assert result["symbol"] == "BTCUSDT"
    assert result["interval"] == "1"
    assert result["source"] == "bybit"
    assert result["closed_only"] is True
    assert result["candles"] == rows[:2]
    assert result["warnings"] == []
    assert calls == [("BTCUSDT", "1", 2)]
    assert len(rows) == 3


def test_menu_contains_all_ui_intervals_and_trade_built_seconds():
    from chart_data import list_chart_intervals
    menu = list_chart_intervals()
    assert [x["value"] for x in menu] == [
        "1s", "5s", "15s", "30s", "1", "3", "5", "10", "12", "15", "30", "45", "60",
        "120", "180", "240", "480", "720", "D", "2D", "3D", "W", "M"]
    seconds = menu[:4]
    assert [x["label"] for x in seconds] == ["1s", "5s", "15s", "30s"]
    assert all(x["available"] and x["source"] == "trades" and x["reason"] for x in seconds)
    assert all(x["available"] and x["label"] and "source" not in x for x in menu[4:])


@pytest.mark.parametrize("symbol,interval,limit", [
    ("BTCUSDT", "30s", 10), ("BTCUSDT", "wat", 10),
    ("BTC/USDT", "1", 10), ("btcusdt", "1", 10),
    ("BTCUSD", "1", 10), ("USDT", "1", 10),
    ("BTCUSDT", "1", 0), ("BTCUSDT", "1", True),
])
def test_invalid_requests_reject_before_fetch(symbol, interval, limit):
    from chart_data import build_chart_snapshot

    def forbidden(*args):
        pytest.fail("invalid request must not fetch")

    with pytest.raises(ValueError):
        build_chart_snapshot(symbol, interval, limit, forbidden, 1000)

def utc(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp() * 1000)


@pytest.mark.parametrize("interval,base,minutes,ratio", [
    ("10", "5", 5, 2), ("12", "3", 3, 4), ("45", "15", 15, 3),
    ("180", "60", 60, 3), ("480", "60", 60, 8), ("720", "60", 60, 12),
    ("2D", "D", 1440, 2), ("3D", "D", 1440, 3),
])
def test_complete_utc_aggregation_and_base_selection(interval, base, minutes, ratio):
    duration = minutes * 60_000
    rows = [bar(i * duration, duration) for i in range(ratio * 2)]
    result, calls = snapshot(rows, interval, limit=2)
    assert calls == [("BTCUSDT", base, min(1000, 3 * ratio))]
    assert len(result["candles"]) == 2
    first = result["candles"][0]
    assert first["ts"] == 0
    assert first["close_ts"] == duration * ratio
    assert (first["o"], first["h"], first["l"], first["c"], first["v"]) == (
        9, 11, 9, 11, 2 * ratio)
    assert first["interval"] == interval
    assert first["source"] == "bybit"


def test_partial_buckets_and_missing_subbars_are_never_invented():
    rows = [bar(i * 300_000, 300_000) for i in (1, 2, 3, 4, 6)]
    result, _ = snapshot(rows, "10", limit=5)
    assert [x["ts"] for x in result["candles"]] == [600_000]
    assert result["warnings"]


def test_incomplete_trailing_bucket_is_removed_at_asof():
    rows = [bar(i * 300_000, 300_000) for i in range(4)]
    result, _ = snapshot(rows, "10", as_of_ms=900_000)
    assert [x["ts"] for x in result["candles"]] == [0]


def test_calendar_months_use_leap_february_and_exclude_partial_edges():
    day = 86_400_000
    start, end = utc("2024-01-15"), utc("2024-04-03")
    rows = [bar(t, day) for t in range(start, end, day)]
    result, calls = snapshot(rows, "M", limit=3, as_of_ms=end)
    assert calls == [("BTCUSDT", "D", 124)]
    assert [(x["ts"], x["close_ts"], x["v"]) for x in result["candles"]] == [
        (utc("2024-02-01"), utc("2024-03-01"), 58),
        (utc("2024-03-01"), utc("2024-04-01"), 62)]
    assert result["warnings"]


def test_request_is_bounded_and_does_not_pad_output():
    result, calls = snapshot([bar(0, 3_600_000)], "720", limit=1000)
    assert calls[0][2] == 1000
    assert result["candles"] == []
    assert result["source"] == "bybit"
    assert result["warnings"]


@pytest.mark.parametrize("asof", [True, 1.5, float("nan"), "1000"])
def test_invalid_asof_rejected_before_fetch(asof):
    from chart_data import build_chart_snapshot
    with pytest.raises(ValueError):
        build_chart_snapshot("BTCUSDT", "1", 10,
                             lambda *args: pytest.fail("must not fetch"), asof)


@pytest.mark.parametrize("change", [
    {"source": "binance"}, {"source": None}, {"symbol": "ETHUSDT"},
    {"interval": "5"},
    {"close_ts": 100_000}, {"h": float("nan")}, {"closed": False},
    {"ts": 1, "close_ts": 60_001},
])
def test_malformed_or_mixed_provenance_fails_closed(change):
    rows = [bar(0), {**bar(60_000), **change}]
    with pytest.raises(ValueError):
        snapshot(rows)


def test_duplicate_timestamps_fail_closed():
    with pytest.raises(ValueError):
        snapshot([bar(0), bar(0)])


def test_empty_result_has_no_invented_provider():
    result, _ = snapshot([])
    assert result["source"] is None
    assert result["candles"] == []
    assert result["warnings"]


def test_bull_fvg_is_created_at_third_open_after_third_closes():
    rows = [bar(0, low=8, high=10), bar(60_000, low=9, high=14),
            bar(120_000, low=12, high=15)]
    result, _ = snapshot(rows, as_of_ms=179_999)
    assert result["zones"] == []
    result, _ = snapshot(rows, as_of_ms=180_000)
    assert result["zones"] == [{"type": "fvg", "direction": "bull", "top": 12,
                                     "bottom": 10, "start_ts": 120_000, "end_ts": 180_000,
                                     "idx": 2, "mitigated": False}]


@pytest.mark.parametrize("direction,first,third,later,expected", [
    ("bull", (8, 10), (12, 15), (11, 14), (10, 11)),
    ("bear", (12, 15), (8, 10), (9, 11), (11, 12)),
])
def test_partial_fill_shrinks_directional_residual(direction, first, third, later, expected):
    rows = [bar(0, low=first[0], high=first[1]), bar(60_000, low=9, high=14),
            bar(120_000, low=third[0], high=third[1]),
            bar(180_000, low=later[0], high=later[1])]
    result, _ = snapshot(rows)
    zone = next(x for x in result["zones"] if x["idx"] == 2)
    assert zone["direction"] == direction
    assert (zone["bottom"], zone["top"]) == expected
    assert zone["end_ts"] == 240_000


@pytest.mark.parametrize("first,third,later", [
    ((8, 10), (12, 15), (10, 14)), ((12, 15), (8, 10), (9, 12)),
])
def test_fully_filled_zones_are_hidden(first, third, later):
    rows = [bar(0, low=first[0], high=first[1]), bar(60_000, low=9, high=14),
            bar(120_000, low=third[0], high=third[1]),
            bar(180_000, low=later[0], high=later[1])]
    result, _ = snapshot(rows)
    assert not any(zone["idx"] == 2 for zone in result["zones"])


def test_fvg_scans_entire_snapshot_not_only_latest_eighty_bars():
    rows = [bar(0, low=8, high=10), bar(60_000, low=9, high=14)]
    rows += [bar(i * 60_000, low=12, high=15) for i in range(2, 150)]
    result, _ = snapshot(rows, limit=150)
    assert result["zones"][0]["start_ts"] == 120_000
    assert result["zones"][0]["end_ts"] == 9_000_000


def test_zone_count_is_bounded_and_recent_creation_order_is_stable():
    rows = [bar(i * 60_000, low=10 + i * 3, high=11 + i * 3)
            for i in range(250)]
    result, _ = snapshot(rows, limit=250)
    zones = result["zones"]
    assert len(zones) == 100
    assert [z["idx"] for z in zones] == list(range(150, 250))


def test_no_fvg_across_missing_bars_or_touching_prices():
    result, _ = snapshot([bar(0, low=8, high=10), bar(60_000),
                          bar(180_000, low=12, high=14)])
    assert result["zones"] == []
    result, _ = snapshot([bar(0, low=8, high=10), bar(60_000),
                          bar(120_000, low=10, high=14)])
    assert result["zones"] == []


def test_unclosed_future_bar_cannot_mitigate_fvg():
    rows = [bar(0, low=8, high=10), bar(60_000, low=9, high=14),
            bar(120_000, low=12, high=15), bar(180_000, low=8, high=14)]
    result, _ = snapshot(rows, as_of_ms=180_000)
    assert result["zones"][0]["top"] == 12


def test_chart_module_exists():
    path = Path(__file__).parents[1] / "chart_data.py"
    assert path.is_file(), "chart_data aggregation module is missing"
    assert importlib.util.spec_from_file_location("chart_data", path)
