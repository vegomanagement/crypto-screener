"""MTF layer: reconciliation rules, SMC sync/divergence, dominant TF, verdict, level confluence,
cache, ladder, snapshot bound and HTF no-look-ahead (backtest data + swing model)."""
import json
import math
import sys
from pathlib import Path

import mtf
import mtf_block as B
import mtf_rules as R
import smc
import swing_model as sm
from tests.mtf_fixtures import H1, T0, brk, live_btc_case, read, wy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mtf_data  # noqa: E402


def ids(hits):
    return [h["id"] for h in hits]


def test_live_btc_1h_distribution_vs_15m_spring():
    m15, h1 = live_btc_case()
    hits = R.wyckoff_rules("15", m15, "60", h1)
    w1 = next(h for h in hits if h["id"] == "W1")
    assert w1["kind"] == "conflict" and w1["position"] == "at_htf_support"
    assert "counter-trend bounce inside a top" in w1["text"]
    assert "1h close above 82900.5 would invalidate the 1h distribution and align with the 15m accumulation" in w1["resolve"]
    w3 = next(h for h in hits if h["id"] == "W3")
    # Spring 80751 < 1h support 80780, and the 1h bar closed back inside (81099.5): inside a 1h
    # distribution this is a test of its edge (conflict), not an HTF Spring yet.
    assert w3["kind"] == "conflict" and "test of its edge" in w3["text"]


def test_w3_breakdown_when_htf_closes_below_and_pending_without_htf_close():
    m15, h1 = live_btc_case(htf_close_after_spring=80600.0)
    w3 = next(h for h in R.wyckoff_rules("15", m15, "60", h1) if h["id"] == "W3")
    assert "breakdown, not a Spring" in w3["text"]
    m15, h1 = live_btc_case()
    h1["_candles"] = []
    w3 = next(h for h in R.wyckoff_rules("15", m15, "60", h1) if h["id"] == "W3")
    assert w3["kind"] == "note" and "pending" in w3["text"]
    # HTF accumulation: the reclaimed LTF spring is an HTF Spring candidate (aligned)
    m15, h1 = live_btc_case()
    h1["wyckoff"] = wy("accumulation", "B", 80780.0, 82900.5)
    w3 = next(h for h in R.wyckoff_rules("15", m15, "60", h1) if h["id"] == "W3")
    assert w3["kind"] == "aligned" and "1h Spring candidate" in w3["text"]


def test_w2_accumulation_at_htf_support_in_uptrend_is_aligned():
    lo = read("15", wyckoff=wy("accumulation", "B", 100.0, 110.0), atr=1.0)
    hi = read("60", bias="bull", score=4, wyckoff=wy("accumulation", "D", 100.2, 130.0), atr=3.0)
    hits = R.wyckoff_rules("15", lo, "60", hi)
    assert "W2" in ids(hits) and next(h for h in hits if h["id"] == "W2")["kind"] == "aligned"
    assert "W1" not in ids(hits)


def test_w4_trend_without_range_and_b1_bias_conflict():
    lo = read("15", bias="bear", score=-4, wyckoff=wy("distribution", "B", 100, 110))
    hi = read("60", bias="bull", score=4)
    hits = R.wyckoff_rules("15", lo, "60", hi)
    assert {"W4c", "B1"} <= set(ids(hits))
    lo["wyckoff"] = wy("accumulation", "B", 100, 110)
    assert next(h for h in R.wyckoff_rules("15", lo, "60", hi) if h["id"] == "W4")["kind"] == "aligned"


def test_w5_ltf_range_outside_htf_range():
    lo = read("15", wyckoff=wy("accumulation", "B", 140, 150))
    hi = read("60", wyckoff=wy("distribution", "B", 100, 130), atr=2.0)
    assert "W5" in ids(R.wyckoff_rules("15", lo, "60", hi))


def test_smc_divergence_pullback_and_propagation():
    hi = read("240", trend="bear", breaks=[brk("BOS", "bear", 80000, T0 - 50 * H1)],
              swings=[{"kind": "H", "price": 84000.0}, {"kind": "L", "price": 79000.0}])
    lo = read("60", trend="bull", breaks=[brk("BOS", "bear", 80500, T0 - 40 * H1), brk("CHoCH", "bull", 81733, T0 - 5 * H1)])
    p = R.smc_pair("60", lo, "240", hi)
    assert p["status"] == "divergence" and p["divergence"] and "pullback" in p["text"]
    assert any("4h close above 84000" in r for r in p["resolve"])
    # the HTF later prints a CHoCH in the LTF direction → reversal propagated
    hi2 = read("240", trend="bull", breaks=[brk("BOS", "bear", 80000, T0 - 50 * H1), brk("CHoCH", "bull", 83000, T0 - H1)])
    assert R.smc_pair("60", lo, "240", hi2)["status"] == "reversal_propagated"
    # the LTF breaks back in the HTF direction → the pullback ended
    lo3 = read("60", trend="bear", breaks=[brk("CHoCH", "bull", 81733, T0 - 5 * H1), brk("CHoCH", "bear", 81000, T0 - H1)])
    assert R.smc_pair("60", lo3, "240", hi)["status"] == "pullback_ended"
    lo4 = read("60", trend="bull", breaks=[brk("BOS", "bull", 81000, T0 - H1)])
    assert R.smc_pair("60", lo4, "240", hi)["status"] == "counter_trend"


def test_sync_tf_is_lowest_tf_of_the_aligned_chain():
    reads = {"240": read("240", trend="bear"), "60": read("60", trend="bear"), "15": read("15", trend="bull"),
             "5": read("5", trend="bear")}
    assert R.sync(list(reads), reads) == ("60", "15")
    reads["240"] = read("240", trend="none")
    assert R.sync(list(reads), reads) == (None, None)
    reads["240"] = read("240", trend="bull")            # top two disagree: nothing synchronised
    assert R.sync(list(reads), reads) == (None, None)


def test_dominant_is_highest_clear_tf_and_verdicts():
    reads = {"D": read("D", score=1), "240": read("240", bias="bear", score=-3), "60": read("60", bias="bear", score=-4),
             "15": read("15", bias="bull", score=3)}
    assert B.dominant(list(reads), reads, "60") == "240"
    assert B.verdict(list(reads), reads, "60") == ("aligned_short", "240")
    assert B.verdict(list(reads), reads, "15") == ("mixed_counter_trend", "240")
    reads["D"] = read("D", wyckoff=wy("distribution", "A", 1, 2, confidence=0.8))
    assert B.verdict(list(reads), reads, "60") == ("mixed_counter_trend", "D") or B.dominant(list(reads), reads, "60") == "D"
    flat = {tf: read(tf) for tf in ("240", "60")}
    assert B.verdict(list(flat), flat, "60") == ("unclear", None)
    rng = {"240": read("240", wyckoff=wy("accumulation", "B", 1, 2)), "60": read("60")}
    assert B.verdict(list(rng), rng, "60") == ("range_bound", "240")


def test_level_clustering_weights_htf_and_counts_tfs():
    reads = {"240": read("240", levels=[{"price": 100.0, "kind": "TR support", "tf": "240"}]),
             "60": read("60", levels=[{"price": 100.3, "kind": "swing low", "tf": "60"},
                                      {"price": 110.0, "kind": "POC", "tf": "60"}]),
             "15": read("15", levels=[{"price": 99.8, "kind": "VAL", "tf": "15"},
                                      {"price": 110.2, "kind": "VWAP", "tf": "15"}])}
    out = B.merge_levels(reads, ref_atr=1.0, price=105.0)
    top = out[0]
    assert top["n_tfs"] == 3 and top["tfs"] == ["4h", "1h", "15m"] and top["side"] == "below"
    expected = (100.0 * 3 + 100.3 * 2 + 99.8 * 1.5) / 6.5
    assert abs(top["price"] - expected) < 1e-6 and top["score"] == 6.5
    assert out[1]["n_tfs"] == 2 and abs(out[1]["price"] - 110.0) < 0.2
    assert len(B.merge_levels(reads, ref_atr=0.1, price=105.0)) == 5   # tight tolerance: no merge


def series(n, step, base=100.0, t0=T0):
    out = []
    for i in range(n):
        c = base + 6 * math.sin(i / 9) + 3 * math.sin(i / 2.3) + i * 0.01
        o = base + 6 * math.sin((i - 1) / 9) + 3 * math.sin((i - 1) / 2.3) + (i - 1) * 0.01
        out.append({"ts": t0 + i * step, "close_ts": t0 + (i + 1) * step, "o": o, "h": max(o, c) + 0.4,
                    "l": min(o, c) - 0.4, "c": c, "v": 10 + (i % 5)})
    return out


def test_service_caches_per_last_closed_bar_and_ladder():
    assert mtf.ladder_for("15") == ["5", "15", "60", "240", "D"]
    assert mtf.ladder_for("60") == ["15", "60", "240", "D", "W"]
    assert mtf.ladder_for("30") == ["15", "30", "60", "240", "D"]
    calls = []
    now = {"t": (T0 + 400 * H1) / 1000}
    data = series(400, H1)

    def fetch(e, s, tf):
        calls.append(tf)
        return {"candles": data}
    svc = mtf.MtfService(fetch, clock=lambda: now["t"])
    svc.one("binance", "BTCUSDT", "60", (7, 14, 21))
    svc.one("binance", "BTCUSDT", "60", (7, 14, 21))
    assert calls == ["60"] and svc.stats["hits"] == 1        # same last closed bar → no refetch
    now["t"] += 3600
    svc.one("binance", "BTCUSDT", "60", (7, 14, 21))
    assert calls == ["60", "60"]                              # a new bar closed → refetch


def test_snapshot_block_is_bounded_and_citable():
    data = series(300, H1)
    svc = mtf.MtfService(lambda e, s, tf: {"candles": data}, clock=lambda: (T0 + 300 * H1) / 1000)
    block = mtf.build_block(svc, "binance", "BTCUSDT", "15")
    c = B.compact(block)
    size = len(json.dumps(c, separators=(",", ":")))
    assert size < 8000, size
    import analyst
    snap = {"mtf": c}
    assert analyst.resolve_path(snap, "mtf.verdict") and analyst.resolve_path(snap, "mtf.tfs.1h.smc_trend")
    assert set(c) >= {"verdict", "dominant_tf", "sync_tf", "conflicts", "levels", "setups", "edge"}


def test_counter_trend_warning_only_against_dominant():
    c = {"dominant_tf": "4h", "verdict": "aligned_short", "tfs": {"4h": {"bias": "bear"}}}
    assert "counter-trend vs HTF" in B.counter_trend_warning(c, "long")
    assert B.counter_trend_warning(c, "short") is None and B.counter_trend_warning(None, "long") is None


def test_htf_bars_are_complete_and_closed_as_of_the_ltf_bar():
    m15 = series(96 * 3, 900_000, t0=1_790_000_000_000 - 1_790_000_000_000 % 86_400_000)
    h1 = mtf_data.aggregate(m15, "60", 900_000)
    h4 = mtf_data.aggregate(m15, "240", 900_000)
    assert len(h1) == 72 and len(h4) == 18
    idx = mtf_data.htf_index(m15, h4)
    for j, k in enumerate(idx):
        if k >= 0:
            assert h4[k]["close_ts"] <= m15[j]["close_ts"]           # closed by the LTF close
            assert k + 1 >= len(h4) or h4[k + 1]["close_ts"] > m15[j]["close_ts"]
    assert idx[14] == -1 and idx[15] == 0 and idx[30] == 0 and idx[31] == 1     # the 4h bar exists only once closed
    assert mtf_data.aggregate(m15[:-1], "240", 900_000)[-1]["ts"] == h4[-2]["ts"]  # incomplete dropped


def walk(n, step, seed=7):
    import random
    rnd, out, c = random.Random(seed), [], 100.0
    for i in range(n):
        o = c
        c = o * (1 + rnd.gauss(0, 0.004))
        out.append({"ts": T0 - T0 % 86_400_000 + i * step, "close_ts": T0 - T0 % 86_400_000 + (i + 1) * step,
                    "o": o, "h": max(o, c) * (1 + abs(rnd.gauss(0, 0.001))),
                    "l": min(o, c) * (1 - abs(rnd.gauss(0, 0.001))), "c": c, "v": 1 + rnd.random()})
    return out


def test_swing_model_no_look_ahead_poisoned_htf_and_ltf():
    ltf = walk(6000, 900_000)
    htf = mtf_data.aggregate(ltf, "240", 900_000)
    trades, _ = sm.scan(ltf, htf, filtered=False)
    assert trades, "fixture must produce trades"
    cut = len(ltf) * 2 // 3
    t_cut = ltf[cut]["close_ts"]
    bad_l = ltf[:cut + 1] + [dict(b, h=b["h"] * 2, l=b["l"] * 0.5, c=b["c"] * 1.5) for b in ltf[cut + 1:]]
    bad_h = [b if b["close_ts"] <= t_cut else dict(b, h=b["h"] * 3, l=b["l"] / 3, c=b["c"] * 2) for b in htf]
    t2, _ = sm.scan(bad_l, bad_h, filtered=False)
    done = lambda ts: [(t["i_signal"], t["entry"], t["stop"], t["r"]) for t in ts if t["i_exit"] <= cut]  # noqa: E731
    assert done(trades) == done(t2)
    sig = lambda ts: [(t["i_signal"], t["entry"], t["stop"], t["target"]) for t in ts if t["i_signal"] <= cut]  # noqa: E731
    assert sig(trades) == sig(t2)
    h = sm.htf_arrays(htf)
    assert len(h["trend"]) == len(htf) and smc.EXTERNAL == (5, 5)


def test_filtered_model_and_htf_sync_arrays_ignore_the_future():
    ltf = walk(6000, 900_000, seed=11)
    htf = mtf_data.aggregate(ltf, "240", 900_000)
    cut = 4000
    t_cut = ltf[cut]["close_ts"]
    bad_l = ltf[:cut + 1] + [dict(b, h=b["h"] * 2, l=b["l"] * 0.5) for b in ltf[cut + 1:]]
    bad_h = [b if b["close_ts"] <= t_cut else dict(b, h=b["h"] * 3, l=b["l"] / 3, c=b["c"] * 2) for b in htf]
    a, _ = sm.scan(ltf, htf)
    b, _ = sm.scan(bad_l, bad_h)
    key = lambda ts: [(t["i_signal"], t["d"], t["entry"], t["r"]) for t in ts if t["i_exit"] <= cut]  # noqa: E731
    assert key(a) == key(b)
    k = mtf_data.htf_index(ltf, htf)[cut]
    h1, h2 = sm.htf_arrays(htf), sm.htf_arrays(bad_h)
    assert h1["trend"][:k + 1] == h2["trend"][:k + 1]
    assert h1["lo"][:k + 1] == h2["lo"][:k + 1] and h1["hi"][:k + 1] == h2["hi"][:k + 1]
    # the per-bar HTF trend used for the sync is the trend of HTF bars CLOSED by the LTF bar
    t_ltf = [smc.structure(htf, 5, 5)["trend_at"][i] if i >= 0 else 0 for i in mtf_data.htf_index(ltf, htf)]
    t_bad = [smc.structure(bad_h, 5, 5)["trend_at"][i] if i >= 0 else 0 for i in mtf_data.htf_index(bad_l, bad_h)]
    assert t_ltf[:cut + 1] == t_bad[:cut + 1]
