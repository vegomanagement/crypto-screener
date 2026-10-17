"""Positioning block of the Hermes analyst snapshot: present / missing / cold collectors, validator, prompt."""
import json
from pathlib import Path

import analyst
import analyst_hermes
import analyst_positioning as ap
from agent_api import app_positioning
from positioning_api import PositioningService
from tests.test_analyst import POSITIONING_SNAP, providers

FIX = Path(__file__).parent / "fixtures" / "positioning"


def snap_with(**over):
    return analyst.build_snapshot("binance", "BTCUSDT", "15", providers(**over))


def test_period_matches_chart_interval():
    assert [ap.period_for(i) for i in ("1", "5", "15", "30", "60", "240", "D", "W")] == \
        ["5m", "5m", "15m", "30m", "1h", "4h", "1d", "1d"]
    assert ap.period_for("5s") is None
    calls = []
    snap = analyst.build_snapshot("binance", "BTCUSDT", "240", providers(
        positioning=lambda e, s, p: calls.append((e, s, p)) or {**POSITIONING_SNAP, "period": p}))
    assert calls == [("binance", "BTCUSDT", "4h")]
    assert snap["positioning"]["period"] == "4h"


def test_block_present_with_sources_and_bounded_size():
    pos = snap_with()["positioning"]
    assert pos["source"] and pos["ts"] and pos["exchange"] == "binance"
    assert pos["top_traders"]["account"]["long_share"] == 0.6437
    assert pos["top_traders"]["position"]["long_share"] == 0.6341
    assert pos["crowd"]["long_share"] == 0.6268 and pos["crowd"]["source"].endswith("globalLongShortAccountRatio")
    assert pos["taker"]["value"] == 0.7939
    for win in ("1h", "4h", "24h"):
        row = pos["open_interest"][f"change_{win}"]
        assert row["value"] is not None and row["source"] and row["ts"], win
    assert pos["basis"]["value"] is not None and "funding_rate" not in pos["basis"]
    assert "funding" not in pos["signals"]  # already snapshot.funding: no double counting
    assert pos["signals"]["taker_flow"]["state"] == "sellers_aggressive"
    liq = pos["liquidations"]
    assert liq["lower_bound"] is True and liq["label"].startswith("LOWER BOUND")
    assert liq["windows"]["4h"]["complete_coverage"] is False and liq["windows"]["4h"]["covered_min"] == 120
    w = pos["whales"]
    assert w["aggregate"]["long_count"] == 1 and w["aggregate"]["long_notional"] > 0
    assert w["recent_changes"][0]["kind"] == "increased" and w["ts"]
    assert any("vaults and market makers" in c for c in pos["caveats"])
    assert any("LOWER BOUND" in c for c in pos["caveats"])
    text = json.dumps(pos, separators=(",", ":"))
    assert len(text) < 6000
    assert "series" not in text and "0xf5d81a" not in text  # no series, no addresses
    assert pos["missing"] == {}


def test_hyperliquid_and_missing_values_stay_null_with_reason():
    raw = json.loads((FIX / "snapshot_binance_BTCUSDT.json").read_text())
    raw["top_traders"]["account"] = {"value": None, "source": "x", "venue": "hyperliquid",
                                     "reason": "only Binance publishes top-trader ratios"}
    raw["taker"] = None
    raw["liquidations"] = {"venue": "hyperliquid", "symbol": "BTC", "value": None,
                           "reason": "Hyperliquid has no public liquidation stream", "lower_bound": None}
    pos = ap.positioning_section(raw, "15")
    assert pos["top_traders"]["account"]["value"] is None
    assert pos["top_traders"]["account"]["reason"] == "only Binance publishes top-trader ratios"
    assert pos["taker"]["value"] is None and pos["missing"]["taker"]
    assert pos["liquidations"]["windows"] is None and pos["liquidations"]["collector"] == "n/a"
    assert pos["missing"]["liquidations"] == "Hyperliquid has no public liquidation stream"


def test_provider_failure_and_unsupported_symbol_are_missing():
    def boom(*a):
        raise RuntimeError("venue down")
    snap = snap_with(positioning=boom)
    assert snap["positioning"] is None and "venue down" in snap["missing"]["positioning"]
    snap = analyst.build_snapshot("hyperliquid", "xyz:GOLD", "15", providers())
    assert snap["positioning"] is None and "no data" in snap["missing"]["positioning"]
    snap = analyst.build_snapshot("binance", "BTCUSDT", "15", {k: v for k, v in providers().items()
                                                             if k != "positioning"})
    assert snap["missing"]["positioning"] == "positioning provider not configured"


class NeverStart:
    """Collector/tracker doubles that fail the test if an analysis tries to start them."""
    polls = 0
    top_n = 50

    def watch(self, *a, **k):
        raise AssertionError("analysis must not subscribe")

    def ensure_running(self):
        raise AssertionError("analysis must not start a collector")

    def status(self, venue=None):
        return {"connected": False, "running": False, "subscribed": [], "watched": False}


def test_cold_collectors_are_not_started_and_say_so(monkeypatch):
    import positioning_data
    monkeypatch.setattr(positioning_data, "fetch_raw", lambda http, e, s, p: {})

    class Flows:
        def exchange_flows(self, coin, now_ms=None):
            return {"available": False, "value": None, "reason": "requires CoinGlass API key (paid)"}

    from liquidations_store import LiquidationStore
    svc = PositioningService(http=object(), store=LiquidationStore(clock_ms=lambda: 10**12),
                             collector=NeverStart(), whales=NeverStart(), coinglass=Flows(),
                             clock_ms=lambda: 10**12)

    class App:
        extensions = {"positioning": {"get_service": lambda: svc}}
    raw = app_positioning(App())("binance", "BTCUSDT", "15m")
    pos = ap.positioning_section(raw, "15")
    assert pos["liquidations"]["collector"] == "cold" and pos["liquidations"]["windows"] is None
    assert "not started by an analysis" in pos["missing"]["liquidations"]
    assert pos["whales"]["tracker"] == "cold" and pos["whales"]["aggregate"] is None
    assert "does not start the tracker" in pos["missing"]["whales"]
    assert pos["crowd"]["value"] is None and pos["missing"]["crowd"]


def test_validator_accepts_positioning_fields_and_drops_null_ones():
    snap = snap_with()
    snap["positioning"]["taker"]["value"] = None
    raw = {"bias": "short", "confidence": "low", "horizon": "4 bars", "summary_ru": "x",
           "factor_scores": [
               {"factor": "crowd long-heavy", "direction": "short", "strength": 2,
                "source_fields": ["positioning.crowd.long_share"]},
               {"factor": "whales long", "direction": "long", "strength": 2,
                "source_fields": ["positioning.whales.aggregate.long_notional"]},
               {"factor": "longs flushed (lower bound)", "direction": "long", "strength": 1,
                "source_fields": ["positioning.liquidations.windows.1h.long_usd"]},
               {"factor": "OI vs price", "direction": "short", "strength": 1,
                "source_fields": ["positioning.signals.oi_price_4h.state",
                                  "positioning.open_interest.change_4h.price_pct"]},
               {"factor": "taker", "direction": "short", "strength": 2, "source_fields": ["positioning.taker"]},
               {"factor": "made up", "direction": "long", "strength": 2,
                "source_fields": ["positioning.top_traders.vip.long_share"]}]}
    warnings = []
    factors = analyst_hermes.validate_factors(raw["factor_scores"], snap, warnings)
    cited = [f["source_fields"][0] for f in factors]
    assert cited == ["positioning.crowd.long_share", "positioning.whales.aggregate.long_notional",
                     "positioning.liquidations.windows.1h.long_usd", "positioning.signals.oi_price_4h.state"]
    assert any("null (missing) positioning" in w for w in warnings)
    assert any("unknown source_fields" in w for w in warnings)


def test_prompt_asks_for_positioning_on_both_sides_with_caveats():
    text = analyst_hermes.SCHEMA_TEXT
    assert "snapshot.positioning" in text and "BOTH" in text
    for field in ("positioning.top_traders.position.long_share", "positioning.crowd.long_share",
                  "positioning.taker.value", "positioning.whales.aggregate.long_notional",
                  "positioning.liquidations.windows.1h.long_usd"):
        assert field in text
    assert "LOWER BOUND" in text and "vaults and market makers" in text
    msgs = analyst_hermes.build_messages(snap_with())
    assert '"positioning":{' in msgs[1]["content"]


def test_service_reports_snapshot_and_positioning_size():
    from analyst_service import AnalystService
    from tests.test_analyst_service import Clock
    svc = AnalystService(providers(), None, configured=False, clock=Clock())
    out = svc.analyze("binance", "BTCUSDT", "15")
    assert 0 < out["positioning_bytes"] < 6000 < out["snapshot_bytes"]
    assert out["freshness"]["positioning_missing"] == {}
    assert out["freshness"]["sections"]["positioning"]
