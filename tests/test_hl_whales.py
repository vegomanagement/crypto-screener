"""Hyperliquid whale tracker: streaming leaderboard parse, aggregation maths, change detection."""
import io
import json
from decimal import Decimal
from pathlib import Path

import pytest

from hl_whale_tracker import WhaleTracker
from hl_whales import aggregate_coin, detect_changes, parse_leaderboard_stream, parse_positions
from positioning import NormaliseError

FIX = Path(__file__).parent / "fixtures" / "positioning"


def load(name):
    return json.loads((FIX / name).read_text())


def pos(szi, entry, value, liq=None):
    return {"szi": Decimal(szi), "entry": Decimal(entry), "value": Decimal(value),
            "liq": Decimal(liq) if liq else None, "upnl": Decimal("0"), "leverage": 3}


def test_leaderboard_stream_parse_tiny_chunks_matches_full_parse():
    raw = (FIX / "hl_leaderboard_head.json").read_bytes()
    rows, total = parse_leaderboard_stream(io.BytesIO(raw), top_n=3, chunk_size=7)
    full = load("hl_leaderboard_head.json")["leaderboardRows"]
    expected = sorted(full, key=lambda r: Decimal(r["accountValue"]), reverse=True)[:3]
    assert total == len(full) == 6
    assert [r["address"] for r in rows] == [r["ethAddress"] for r in expected]
    assert rows[0]["account_value"] == Decimal(expected[0]["accountValue"])


def test_leaderboard_stream_rejects_truncated_input():
    raw = (FIX / "hl_leaderboard_head.json").read_bytes()[:-40]
    with pytest.raises(NormaliseError):
        parse_leaderboard_stream(io.BytesIO(raw), top_n=3, chunk_size=512)


def test_parse_positions_real_clearinghouse_state():
    positions = parse_positions(load("hl_clearinghouseState_a.json"))
    assert positions["BTC"]["szi"] == Decimal("12.44424")
    assert positions["BTC"]["liq"] is None
    assert positions["ETH"]["szi"] == Decimal("-18.18") and positions["ETH"]["liq"] == Decimal("89148.7251200414")
    assert parse_positions(load("hl_clearinghouseState_b.json")) == {}


def test_aggregate_coin_notional_counts_and_weighted_entry():
    states = {"0xa": {"BTC": pos("2", "100", "210", "50")}, "0xb": {"BTC": pos("1", "130", "105")},
              "0xc": {"BTC": pos("-3", "110", "315", "150")}, "0xd": {"ETH": pos("1", "1", "1")}}
    agg = aggregate_coin(states, "BTC")
    assert agg["long_notional"] == "315" and agg["short_notional"] == "315"
    assert agg["long_count"] == 2 and agg["short_count"] == 1
    assert agg["avg_entry_long"] == "110"     # (2*100 + 1*130) / 3
    assert agg["avg_entry_short"] == "110"
    assert agg["long_share"] == "0.5" and agg["net_notional"] == "0"
    assert [lv["liquidation_px"] for lv in agg["liquidation_levels"]] == ["150", "50"]


def test_detect_changes_kinds_and_skips_unpolled_accounts():
    prev = {"0xa": {"BTC": pos("1", "100", "100")}, "0xb": {"ETH": pos("2", "10", "20")},
            "0xc": {"BTC": pos("-1", "100", "100")}, "0xd": {"SOL": pos("10", "1", "10")},
            "0xgone": {"BTC": pos("5", "100", "500")}}
    cur = {"0xa": {"BTC": pos("1.5", "100", "150")}, "0xb": {},
           "0xc": {"BTC": pos("2", "100", "200")}, "0xd": {"SOL": pos("10.1", "1", "10.1"), "BTC": pos("1", "1", "1")}}
    kinds = {(c["address"], c["coin"]): c["kind"] for c in detect_changes(prev, cur, 1)}
    assert kinds == {("0xa", "BTC"): "increased", ("0xb", "ETH"): "closed", ("0xc", "BTC"): "flipped",
                     ("0xd", "BTC"): "opened"}   # SOL +1% is below the 5% threshold; 0xgone not polled
    inc = [c for c in detect_changes(prev, cur, 1) if c["kind"] == "increased"][0]
    assert inc["delta_notional"] == "50"


class FakeHttp:
    def __init__(self, script):
        self.script, self.calls = script, []

    def request_json(self, url, body=None, **kw):
        self.calls.append(body["user"])
        return self.script.pop(0)


def test_tracker_three_polls_detects_changes_and_caches_leaderboard():
    lb = (FIX / "hl_leaderboard_head.json").read_bytes()
    opened = []

    def open_lb():
        opened.append(1)
        return io.BytesIO(lb)

    a = load("hl_clearinghouseState_a.json")
    empty = load("hl_clearinghouseState_b.json")
    flat = json.loads(json.dumps(a))
    flat["assetPositions"] = [p for p in flat["assetPositions"] if p["position"]["coin"] != "BTC"]
    script = [a, empty, a, empty, flat, empty]
    http = FakeHttp(script)
    t = [1000.0]
    tracker = WhaleTracker(http, top_n=2, open_leaderboard=open_lb, clock=lambda: t[0])
    for _ in range(3):
        assert tracker.poll_once()["ok"] == 2
        t[0] += 61
    assert len(opened) == 1                       # cached for an hour
    assert tracker.polls == 3
    view = tracker.view("BTC")
    assert view["last_poll"]["changes_detected"] == 1
    assert view["changes"][0]["kind"] == "closed" and view["changes"][0]["coin"] == "BTC"
    assert view["label"].startswith("Hyperliquid top-2 accounts by account value")
    assert view["aggregate"]["long_count"] == 0


def test_tracker_poll_survives_failures_and_leases_expire():
    class Boom:
        def request_json(self, *a, **k):
            raise RuntimeError("api.hyperliquid.xyz: HTTP 429")

    t = [0.0]
    tracker = WhaleTracker(Boom(), top_n=2, lease_ms=1000, clock=lambda: t[0],
                           open_leaderboard=lambda: io.BytesIO((FIX / "hl_leaderboard_head.json").read_bytes()))
    poll = tracker.poll_once()
    assert poll["ok"] == 0 and poll["failed"] == 2 and "429" in poll["errors"][0]["error"]
    tracker.watch("page-1")
    assert tracker.wanted()
    t[0] += 2
    assert not tracker.wanted()
    assert WhaleTracker(Boom()).view("BTC")["reason"].startswith("whale tracker warming up")
