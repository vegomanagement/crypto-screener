"""Factor-scored long/short split: factor validation, scoring maths, disagreement, stats."""
import itertools
import json
import math

import pytest

import analyst
import analyst_hermes as ah
from analyst import (
    SPLIT_CONFIG,
    agent_net,
    baseline_net,
    compute_split,
    disagreement,
    round_split,
)
from analyst_service import AnalystService
from analyst_store import AnalysisStore, split_stats
from tests.test_analyst import providers
from tests.test_analyst_service import Clock, good


@pytest.fixture
def snap():
    return analyst.build_snapshot("binance", "BTCUSDT", "15", providers())


def F(direction, strength, *fields):
    return {"factor": f"{direction}{strength}", "direction": direction, "strength": strength,
            "source_fields": list(fields) or ["indicators.close"]}


BASE = {"long_score": 70, "short_score": 70, "long_verdict": "WAIT", "short_verdict": "WAIT"}


# ── factor validation ──────────────────────────────────────────────────────

def test_factor_validation_drops_bad_and_duplicate(snap):
    warnings = []
    items = [
        F("long", 2, "indicators.rsi14"),
        F("short", 3, "indicators.rsi14"),                      # same field set → duplicate
        F("long", 1, "orderbook.imbalance", "indicators.close"),
        F("short", 1, "indicators.close", "orderbook.imbalance"),  # same set, other order → duplicate
        F("long", 2, "made.up.field"),                          # unknown field
        F("long", 2, "indicators.rsi14", "made.up"),            # one unknown field drops it all
        {**F("long", 2, "indicators.atr14"), "strength": 4},
        {**F("long", 2, "indicators.atr14"), "strength": 2.0},
        {**F("long", 2, "indicators.atr14"), "strength": True},
        {**F("long", 2, "indicators.atr14"), "direction": "up"},
        {**F("long", 2, "indicators.atr14"), "factor": " "},
        {**F("long", 2, "indicators.atr14"), "source_fields": []},
        "not an object",
        F("short", 1, "funding.predicted.rate"),
        F("short", 2, "baseline.short_score"),                  # baseline is blended server-side
        F("long", 1, "indicators.ema200", "baseline.long_verdict"),
    ]
    out = ah.validate_factors(items, snap, warnings)
    assert [(f["direction"], f["strength"], f["source_fields"]) for f in out] == [
        ("long", 2, ["indicators.rsi14"]),
        ("long", 1, ["orderbook.imbalance", "indicators.close"]),
        ("short", 1, ["funding.predicted.rate"])]
    text = " ".join(warnings)
    assert text.count("duplicate") == 2 and text.count("unknown source_fields") == 2
    assert text.count("strength must be integer") == 3 and "not an object" in text
    assert text.count("baseline that the server already blends") == 2


def test_factor_validation_caps_at_twelve(snap):
    fields = [f"candles.recent.{i}" for i in range(20)]
    warnings = []
    out = ah.validate_factors([F("long", 1, f) for f in fields], snap, warnings)
    assert len(out) == ah.MAX_FACTORS == 12 and "more than 12" in warnings[-1]


def test_model_percentages_are_ignored(snap):
    out, warnings = ah.validate(good(snap, long_pct=38, short_pct=62), snap)
    assert "long_pct" not in out and "ignored" in warnings[0]


def test_prompt_forbids_percentages_and_asks_both_sides_and_funding(snap):
    text = ah.build_messages(snap)[1]["content"]
    assert "Do NOT output any percentage" in text and "BOTH sides" in text
    for field in ("funding.last_settled.rate", "funding.predicted.rate", "funding.annualized_predicted",
                  "funding.cross_venue"):
        assert field in text
    assert "Do not cite baseline.*" in text
    assert "38" not in ah.SCHEMA_TEXT and "long_pct" not in ah.SCHEMA_TEXT.split("Rules:")[0]


# ── scoring maths ──────────────────────────────────────────────────────────

def mirror(factors):
    return [{**f, "direction": "short" if f["direction"] == "long" else "long"} for f in factors]


def mirror_base(b):
    swap = {"LONG": "SHORT", "SHORT": "LONG"}
    return {"long_score": b["short_score"], "short_score": b["long_score"],
            "long_verdict": swap.get(b["short_verdict"], b["short_verdict"]),
            "short_verdict": swap.get(b["long_verdict"], b["long_verdict"])}


@pytest.mark.parametrize("factors,base", [
    ([F("long", 3), F("short", 1)], BASE),
    ([F("long", 1)], {**BASE, "long_score": 90, "short_verdict": "SHORT"}),
    ([F("short", 2), F("short", 2), F("long", 3)], {**BASE, "long_verdict": "LONG"}),
    ([], {**BASE, "short_score": 20}),
    ([F("long", 3)] * 12, None),
])
def test_split_is_symmetric(factors, base):
    a = compute_split(factors, base)
    b = compute_split(mirror(factors), mirror_base(base) if base else None)
    assert a["long_pct"] == b["short_pct"] and a["short_pct"] == b["long_pct"]
    assert a["p_long"] == pytest.approx(100 - b["p_long"])
    assert {a["bias"], b["bias"]} in ({"neutral"}, {"long", "short"})


def test_split_bounds_and_sum_over_grid():
    k = SPLIT_CONFIG["k"]
    lo, hi = 100 / (1 + math.exp(k)), 100 / (1 + math.exp(-k))
    seen = set()
    for combo in itertools.product([None, "long", "short"], [None, 1, 2, 3], repeat=2):
        factors = [F(combo[i], combo[i + 1]) for i in (0, 2) if combo[i] and combo[i + 1]]
        for ls, ss, lv, sv in itertools.product((0, 40, 90), (0, 40, 90), ("WAIT", "LONG"), ("WAIT", "SHORT")):
            s = compute_split(factors, {"long_score": ls, "short_score": ss, "long_verdict": lv,
                                        "short_verdict": sv})
            assert s["long_pct"] + s["short_pct"] == 100
            assert lo - 1e-9 <= s["p_long"] <= hi + 1e-9 and -1 <= s["net"] <= 1
            seen.add(s["long_pct"])
    assert len(seen) > 15  # the output really varies with the evidence


def test_round_split_half_away_from_fifty():
    assert round_split(50.0) == (50, 50)
    assert round_split(52.5) == (53, 47) and round_split(47.5) == (47, 53)
    assert round_split(54.49) == (54, 46) and round_split(99.9) == (100, 0)
    for p in [x / 10 for x in range(1001)]:
        a, b = round_split(p), round_split(100 - p)
        assert sum(a) == 100 and a == (b[1], b[0])


def test_neutral_band():
    band = SPLIT_CONFIG["neutral_band"]
    assert compute_split([], None)["bias"] == "neutral" and compute_split([], None)["long_pct"] == 50
    # agent_net = 1/6 ≈ 0.167 → p ≈ 62 → long; a balanced pair stays neutral
    assert compute_split([F("long", 1)], None)["bias"] == "long"
    s = compute_split([F("long", 2), F("short", 2)], None)
    assert s["bias"] == "neutral" and abs(s["p_long"] - 50) < band
    weak = compute_split([F("long", 3), F("short", 2), F("short", 1, "indicators.rsi14")],
                         {**BASE, "long_score": 72})
    assert abs(weak["p_long"] - 50) < band and weak["bias"] == "neutral"


def test_agent_net_normalisation():
    assert agent_net([]) is None
    assert agent_net([F("long", 3)]) == pytest.approx(0.5)  # floor of agent_min_total = 6
    assert agent_net([F("long", 3)] * 4) == 1.0
    assert agent_net([F("long", 3)] * 2 + [F("short", 2)] * 3) == pytest.approx(0.0)


def test_baseline_blending():
    cfg = SPLIT_CONFIG
    assert baseline_net(None) is None and baseline_net({"long_score": None}) is None
    assert baseline_net(BASE) == 0.0
    assert baseline_net({**BASE, "long_verdict": "LONG"}) == pytest.approx(cfg["verdict_bonus"])
    assert baseline_net({**BASE, "long_score": 90, "short_score": 30}) == cfg["baseline_cap"]  # capped
    factors = [F("long", 3), F("long", 3, "indicators.rsi14")]       # agent_net = 1
    bearish = {**BASE, "long_score": 30, "short_score": 90}         # baseline_net = -cap
    s = compute_split(factors, bearish)
    # clear factor tally against the baseline → baseline weight scaled by conflict_scale
    assert s["conflict"] and s["weights"]["baseline"] == cfg["w_baseline"] * cfg["conflict_scale"]
    assert s["net"] == pytest.approx(cfg["w_agent"] - cfg["w_baseline"] * cfg["conflict_scale"] * cfg["baseline_cap"])
    assert s["agent_net"] == 1.0 and s["baseline_net"] == -cfg["baseline_cap"]
    agreeing = compute_split(factors, mirror_base(bearish))
    assert not agreeing["conflict"]
    assert agreeing["net"] == pytest.approx(cfg["w_agent"] + cfg["w_baseline"] * cfg["baseline_cap"])
    only_agent = compute_split(factors, None)
    assert only_agent["net"] == pytest.approx(cfg["w_agent"]) and only_agent["weights"]["baseline"] == 0.0
    only_base = compute_split([], bearish)  # Hermes failed: the baseline alone stays neutral
    assert only_base["net"] == pytest.approx(-cfg["w_baseline"] * cfg["baseline_cap"])
    assert only_base["bias"] == "neutral" and only_base["weights"]["agent"] == 0.0


# ── disagreement ───────────────────────────────────────────────────────────

def test_disagreement_warning():
    long_split = compute_split([F("long", 3)] * 3, None)
    assert disagreement("long", long_split) is None
    d = disagreement("short", long_split)
    assert d and d["severity"] == "opposite" and "compute long" in d["text"]
    assert disagreement("neutral", long_split)["severity"] == "neutral_mismatch"
    assert disagreement(None, long_split) is None


def test_service_reports_disagreement_and_logs_factors(snap):
    raw = good(snap, bias="short")  # factors are net long, the agent says short
    store = AnalysisStore(":memory:")
    svc = AnalystService(providers(), lambda m, deadline=None: (json.dumps(raw), "m"), store, clock=Clock())
    out = svc.analyze("binance", "BTCUSDT", "15")
    a = out["analysis"]
    assert a["agent_bias"] == "short" and a["bias"] == "long"
    assert a["disagreement"]["severity"] == "opposite"
    assert any(w.startswith("disagreement:") for w in out["warnings"])
    stats = store.stats()
    assert stats["split"]["n"] == 1 and stats["split"]["factors_per_hermes_run"] == 3
    assert stats["split"]["disagreements"]["opposite"] == 1
    assert stats["split"]["p_long_min"] == pytest.approx(a["split"]["p_long"], abs=0.01)


def test_split_stats_distribution_and_legacy():
    def row(p, fields, src="hermes", dis=None):
        factors = [{"factor": "x", "direction": "long", "strength": 1, "source_fields": fields}]
        lp = None if p is None else round(p)
        return (src, "long", lp, None if lp is None else 100 - lp, None, p, json.dumps(factors), dis)
    rows = [row(38.0, ["indicators.rsi14"]), row(61.2, ["funding.predicted.rate"]),
            row(55.0, ["funding.cross_venue"], dis="neutral_mismatch"), row(50.0, [], src="baseline"),
            ("hermes", "short", 38, 62, None, None, None, None)]
    s = split_stats(rows)
    assert s["n"] == 4 and s["legacy_without_p_long"] == 1 and s["hermes_runs"] == 3
    assert s["hermes_runs_citing_funding"] == 2 and s["distinct_long_pct"] == 4
    assert s["p_long_min"] == 38.0 and s["p_long_max"] == 61.2
    assert s["histogram"] == {"30-40": 1, "50-60": 2, "60-70": 1}
    assert s["disagreements"] == {"opposite": 0, "neutral_mismatch": 1}
    assert split_stats([])["p_long_mean"] is None


def test_store_migrates_old_file(tmp_path):
    import sqlite3

    from analyst_store import SCHEMA
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO agent_analyses (ts, exchange, symbol, interval, source, status, bias, long_pct,"
                 " short_pct) VALUES (1, 'binance', 'BTCUSDT', '15', 'hermes', 'ok', 'short', 38, 62)")
    conn.commit()
    conn.close()
    store = AnalysisStore(path)
    stats = store.stats()
    assert stats["total"] == 1 and stats["split"]["legacy_without_p_long"] == 1
