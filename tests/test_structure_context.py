"""structure_context: snapshot blocks (wyckoff, signals), the analyst wiring and the API route."""
import analyst
import analyst_hermes
import structure_context as sc
from tests.test_analyst import START, make_candles, providers
from tests.wyckoff_fixtures import accumulation


def test_wyckoff_section_merges_footprint_delta_and_is_compact():
    rows, _ = accumulation()
    fp = {"bars": [{"open_time": rows[-1]["ts"], "delta": "12.5", "forming": False},
                   {"open_time": rows[-1]["ts"] + 1, "delta": "1", "forming": True}]}
    w = sc.wyckoff_section(rows, fp)
    assert w["delta_bars"] == 1 and "footprint delta on the last 1 bars" in w["source"]
    assert w["phase"] == "E" and "params" not in w and len(w["events"]) <= sc.MAX_EVENTS
    assert w["ts"] == rows[-1]["close_ts"]


def test_signals_section_labels_no_edge_and_has_entry_stop_target():
    rows, _ = accumulation()
    out = sc.signals_section(rows, "60")
    assert out["edge"] == "none proven" and out["stats_timeframe"] == "1h"
    assert set(out["unavailable"]) == set(sc.NEEDS_DELTA)
    for e in out["events"]:
        assert e["edge"] == "none proven" and e["bars_ago"] < sc.RECENT
        sign = 1 if e["direction"] == "long" else -1
        assert sign * (e["target"] - e["entry"]) > 0 > sign * (e["stop"] - e["entry"])
    vp = out["volume_profile"]
    assert vp["val"] <= vp["poc"] <= vp["vah"] and vp["price_vs_value"] in ("above", "below", "inside")
    assert out["session_vwap"]["session"] == "UTC day"
    assert sc.signals_section(rows, "240")["stats_note"].startswith("not backtested")


def test_stats_file_is_loaded_and_matches_live_variants():
    stats = sc.load_stats()
    assert stats and stats["rule"] and isinstance(stats["passed"], list)
    for name, params in sc.LIVE:
        assert sc._stat("1h", name, params) is not None, name


def test_snapshot_carries_wyckoff_and_signals_and_hermes_may_cite_them():
    snap = analyst.build_snapshot("binance", "BTCUSDT", "15", providers())
    assert snap["missing"] == {}
    assert snap["wyckoff"]["source"] and snap["wyckoff"]["ts"]
    assert snap["signals"]["source"] and snap["signals"]["edge"] == "none proven"
    assert snap["wyckoff"]["delta_bars"] == 1  # footprint bar at START matches the first candle
    assert analyst.resolve_path(snap, "wyckoff.status")
    warnings = []
    kept = analyst_hermes.validate_factors(
        [{"factor": "no clear Wyckoff range", "direction": "short", "strength": 1,
          "source_fields": ["wyckoff.status"]}], snap, warnings)
    assert len(kept) == 1 and not warnings
    assert "wyckoff.phase" in analyst_hermes.SCHEMA_TEXT


def test_structure_block_never_raises():
    out = sc.structure_block(make_candles(20), "15")
    assert out["signals"] is None and "only 20 candles" in out["missing"]["signals"]
    assert out["wyckoff"]["status"] == "unclear"


def test_structure_route(tmp_path):
    from flask import Flask

    from agent_api import register_agent_routes
    app = Flask(__name__)
    candles = make_candles(200)
    register_agent_routes(app, store_path=tmp_path / "a.sqlite3",
                          structure_chart=lambda e, s, i: {"source": e, "candles": candles})
    client = app.test_client()
    r = client.get("/api/agent/structure?exchange=binance&symbol=BTCUSDT&interval=15")
    assert r.status_code == 200
    body = r.get_json()
    assert body["wyckoff"]["bars"] == 200 and body["signals"]["edge"] == "none proven"
    assert body["last_close_ts"] == candles[-1]["close_ts"] and body["interval"] == "15"
    assert client.get("/api/agent/structure?exchange=nope&symbol=X&interval=15").status_code == 400
    assert START  # fixture import used
