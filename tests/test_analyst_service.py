"""analyst_hermes / analyst_service / analyst_store: validation, retry, cache, fallback, maths."""
import json
import threading
import time

import pytest

import analyst
import analyst_hermes as ah
from analyst_service import AnalystService, RateLimited, SnapshotUnavailable, baseline_analysis
from analyst_store import AnalysisStore, evaluate_outcome, evaluate_pending, summarize
from tests.test_analyst import providers


@pytest.fixture
def snap():
    return analyst.build_snapshot("binance", "BTCUSDT", "15", providers())


def good(snap, **over):
    price = snap["fvg"]["nearest"][0]["top"]
    raw = {"bias": "long", "long_pct": 62, "short_pct": 38, "confidence": "medium",
           "horizon": "4-12 bars of 15m",
           "levels": [{"type": "support", "price": price, "reason": "fvg",
                       "source_fields": ["fvg.nearest.0.top"]}],
           "scenarios": [{"name": "up", "trigger": "hold", "target": "x", "invalidation": "y"}],
           "key_factors": [{"text": "rsi", "source_fields": ["indicators.rsi14"]}],
           "risks": ["chop"], "summary_ru": "Лонг"}
    raw.update(over)
    return raw


def test_validate_accepts_good_output(snap):
    out, warnings = ah.validate(good(snap), snap)
    assert warnings == []
    assert out["long_pct"] == 62 and out["levels"][0]["type"] == "support"


@pytest.mark.parametrize("over", [{"long_pct": 60, "short_pct": 30}, {"long_pct": 60.5, "short_pct": 39.5},
                                  {"long_pct": True, "short_pct": 99}, {"bias": "up"},
                                  {"long_pct": 110, "short_pct": -10}])
def test_validate_rejects_bad_core(snap, over):
    with pytest.raises(ValueError):
        ah.validate(good(snap, **over), snap)


def test_validate_drops_bad_items(snap):
    hi = snap["candles"]["window_high"] + 10 * snap["indicators"]["atr14"]
    raw = good(snap, levels=[
        {"type": "support", "price": hi, "source_fields": ["indicators.close"]},
        {"type": "support", "price": float("nan"), "source_fields": ["indicators.close"]},
        {"type": "support", "price": snap["indicators"]["close"], "source_fields": ["made.up"]},
        {"type": "moon", "price": snap["indicators"]["close"], "source_fields": ["indicators.close"]},
        {"type": "target", "price": snap["indicators"]["close"], "source_fields": ["indicators.close"]},
    ], key_factors=[{"text": "x", "source_fields": ["nope.field"]}], summary_ru="я" * 500)
    out, warnings = ah.validate(raw, snap)
    assert [lv["type"] for lv in out["levels"]] == ["target"]
    assert out["key_factors"] == []
    assert len(out["summary_ru"]) == 400
    joined = " ".join(warnings)
    assert "outside snapshot window" in joined and "finite" in joined
    assert "unknown source_fields" in joined and "invalid type" in joined and "truncated" in joined


def test_unanchored_level_is_flagged(snap):
    far = snap["indicators"]["close"] + 2 * snap["indicators"]["atr14"]
    out, warnings = ah.validate(good(snap, levels=[
        {"type": "support", "price": far, "source_fields": ["indicators.close"]}]), snap)
    assert out["levels"][0]["unanchored"] is True and "not near" in warnings[0]


def test_extract_json_tolerates_fences():
    assert ah.extract_json('```json\n{"a":1}\n```') == {"a": 1}
    assert ah.extract_json('Here: {"a":1} ok') == {"a": 1}
    with pytest.raises(ValueError):
        ah.extract_json("no json")


def test_repair_retry(snap):
    replies = iter(["not json at all", json.dumps(good(snap))])
    calls = []

    def send(messages, deadline=None):
        calls.append(messages)
        return next(replies), "hermes-agent"
    out = ah.ask_hermes(snap, send)
    assert out["attempts"] == 2 and out["analysis"]["bias"] == "long"
    assert "invalid" in calls[1][-1]["content"]


def test_repair_retry_gives_up_after_one(snap):
    out = ah.ask_hermes(snap, lambda m, deadline=None: ('{"bias":"x"}', "m"))
    assert out["analysis"] is None and out["attempts"] == 2 and out["error"]


def test_prompt_contains_snapshot_and_rules(snap):
    msgs = ah.build_messages(snap)
    assert "ONLY" in msgs[0]["content"] and "tools" in msgs[0]["content"]
    assert '"schema":"hermes-analyst/1"' in msgs[1]["content"]


def test_config_from_env_never_defaults_url():
    assert ah.config_from_env({}) is None
    cfg = ah.config_from_env({"HERMES_API_URL": "http://127.0.0.1:8642/v1/", "HERMES_API_KEY": "k"})
    assert cfg["url"] == "http://127.0.0.1:8642/v1" and cfg["model"] == "hermes-agent"


def test_transport_unconfigured_raises():
    with pytest.raises(ah.HermesUnavailable):
        ah.make_transport(None)([])


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def service(send, clock=None, rate=(6, 60), store=None, **over):
    return AnalystService(providers(**over), send, store, clock=clock or Clock(), rate=rate)


def test_cache_and_force(snap):
    n = []

    def send(m, deadline=None):
        n.append(1)
        return json.dumps(good(snap)), "hermes-agent"
    clock = Clock()
    svc = service(send, clock)
    first = svc.analyze("binance", "BTCUSDT", "15")
    assert first["status"] == "ok" and first["cached"] is False and first["source"] == "hermes"
    assert svc.analyze("binance", "BTCUSDT", "15")["cached"] is True and len(n) == 1
    clock.t += 10
    assert svc.analyze("binance", "BTCUSDT", "15", force=True)["cached"] is False and len(n) == 2
    clock.t += 61
    svc.analyze("binance", "BTCUSDT", "15")
    assert len(n) == 3


def test_one_in_flight_per_key(snap):
    gate, n = threading.Event(), []

    def send(m, deadline=None):
        n.append(1)
        gate.wait(2)
        return json.dumps(good(snap)), "m"
    svc = AnalystService(providers(), send, clock=time.time)
    results = []
    threads = [threading.Thread(target=lambda: results.append(svc.analyze("binance", "BTCUSDT", "15")))
               for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join()
    assert len(n) == 1 and sum(r["cached"] for r in results) == 3


def test_rate_limit(snap):
    clock = Clock()
    svc = service(lambda m, deadline=None: (json.dumps(good(snap)), "m"), clock, rate=(2, 60))
    svc.analyze("binance", "A1USDT", "15")
    svc.analyze("binance", "A2USDT", "15")
    with pytest.raises(RateLimited) as err:
        svc.analyze("binance", "A3USDT", "15")
    assert 1 <= err.value.retry_after <= 61
    clock.t += 61
    assert svc.analyze("binance", "A3USDT", "15")["status"] == "ok"


def test_fallback_when_hermes_down(snap):
    def send(m, deadline=None):
        raise ah.HermesUnavailable("gateway unreachable: URLError")
    store = AnalysisStore(":memory:")
    out = service(send, store=store).analyze("binance", "BTCUSDT", "15")
    assert out["status"] == "hermes_unavailable" and out["source"] == "baseline"
    assert out["analysis"]["long_pct"] == snap["baseline"]["long_pct"]
    assert out["analysis"]["confidence"] == "low"
    assert store.stats()["total"] == 1 and out["id"] == 1
    _, warnings = ah.validate(out["analysis"], snap)  # baseline levels pass the same validator
    assert not [w for w in warnings if "dropped" in w]


def test_invalid_output_falls_back(snap):
    out = service(lambda m, deadline=None: ("garbage", "m")).analyze("binance", "BTCUSDT", "15")
    assert out["status"] == "invalid_output" and out["source"] == "baseline"
    assert out["raw_output"] == ["garbage", "garbage"]


def test_unconfigured_service_uses_baseline(snap):
    svc = AnalystService(providers(), None, configured=False, clock=Clock())
    out = svc.analyze("binance", "BTCUSDT", "15")
    assert out["status"] == "hermes_unavailable" and "not configured" in out["warnings"][0]


def test_snapshot_failure_raises():
    def boom(*a):
        raise RuntimeError("down")
    with pytest.raises(SnapshotUnavailable):
        service(lambda m, deadline=None: ("", "m"), chart=boom).analyze("binance", "BTCUSDT", "15")


def test_baseline_without_market_is_neutral(snap):
    snap = dict(snap, baseline=None, liquidity=None)
    out = baseline_analysis(snap)
    assert out["long_pct"] == 50 and out["bias"] == "neutral"


def test_evaluate_outcome_maths():
    bars = [{"h": 101, "l": 99.5}, {"h": 102.1, "l": 100}]
    assert evaluate_outcome("long", 100, 2, bars, 12) == "win"  # decisive hit before horizon ends
    assert evaluate_outcome("long", 100, 5, bars, 12) is None  # no hit and horizon not complete
    assert evaluate_outcome("long", 100, 2, bars + [{"h": 100, "l": 100}] * 10, 12) == "win"
    assert evaluate_outcome("short", 100, 2, bars, 2) == "loss"
    assert evaluate_outcome("short", 100, 0.4, bars, 2) == "ambiguous"
    assert evaluate_outcome("long", 100, 5, bars, 2) == "timeout"
    assert evaluate_outcome("neutral", 100, 1, bars, 2) is None
    assert evaluate_outcome("short", 100, 1, [{"h": 100.5, "l": 98.9}], 1) == "win"


def test_store_evaluate_and_stats():
    store = AnalysisStore(":memory:")
    base = {"exchange": "binance", "symbol": "BTCUSDT", "interval": "15", "atr": 1.0,
            "bar_close_ts": 0, "horizon_bars": 2, "source": "hermes", "status": "ok"}
    store.record({**base, "ts": 1, "price": 100, "bias": "long", "long_pct": 65, "short_pct": 35})
    store.record({**base, "ts": 2, "price": 100, "bias": "short", "long_pct": 40, "short_pct": 60})
    store.record({**base, "ts": 3, "price": 100, "bias": "neutral", "long_pct": 50, "short_pct": 50})
    store.record({**base, "ts": 4, "price": 100, "bias": "long", "long_pct": 80, "short_pct": 20,
                  "bar_close_ts": 10 ** 12})
    bars = [{"h": 101.5, "l": 99.8}, {"h": 100, "l": 100}]
    n = evaluate_pending(store, lambda *a: bars, 10 ** 7, lambda i: 900_000)
    assert n == 2
    stats = store.stats("binance", "BTCUSDT", "15")
    assert stats["total"] == 4 and stats["evaluated"] == 2 and stats["pending"] == 1
    assert stats["overall"] == {"n": 2, "win": 1, "loss": 1, "ambiguous": 0, "timeout": 0,
                                "hit_rate": 0.5}
    assert stats["by_conviction"]["60-70"]["n"] == 2
    assert store.stats("bybit")["total"] == 0


def test_summarize_empty():
    assert summarize([])["overall"]["hit_rate"] is None
