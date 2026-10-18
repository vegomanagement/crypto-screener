"""Signal feature definitions on hand fixtures, scoring, and the poisoned-future (no look-ahead) test."""
import math
import random

import signal_eval as se
import signal_features as sf
import wyckoff
from tests.wyckoff_fixtures import accumulation

STEP = 3_600_000


def bars(spec):
    """spec rows: (o, h, l, c, v[, delta])."""
    out = []
    for i, row in enumerate(spec):
        o, h, low, c, v = row[:5]
        b = {"ts": i * STEP, "close_ts": (i + 1) * STEP, "o": o, "h": h, "l": low, "c": c, "v": v}
        if len(row) > 5:
            b["delta"] = row[5]
        out.append(b)
    return out


def flat(n, price=100.0, v=10.0, delta=0.0):
    return [(price, price + 1, price - 1, price, v, delta) for _ in range(n)]


def test_sweep_reclaim_and_delta_variant():
    spec = flat(25) + [(100, 100.5, 98.0, 99.8, 10, 4.0)]   # wick under 99, close back above
    s = sf.prepare(bars(spec))
    assert list(sf._sweep(s, N=20)) == [25]
    assert list(sf._sweep(s, N=20, delta=True)) == [25]
    spec[-1] = (100, 100.5, 98.0, 99.8, 10, -4.0)
    assert list(sf._sweep(sf.prepare(bars(spec)), N=20, delta=True)) == []
    spec[-1] = (100, 100.5, 98.0, 98.5, 10, 4.0)               # closed below the old low
    assert list(sf._sweep(sf.prepare(bars(spec)), N=20)) == []
    short = se.rule_events(sf.prepare(bars(flat(25) + [(100, 102.0, 99.5, 100.2, 10, -3.0)])),
                           "sweep_delta", {"N": 20, "delta": True})
    assert short == [(25, -1)]


def test_failed_auction_needs_a_long_tail_closing_high():
    spec = flat(25) + [(99.6, 100.0, 97.0, 99.9, 10, 0)]
    assert list(sf._failed_auction(sf.prepare(bars(spec)))) == [25]
    spec[-1] = (99.6, 100.0, 97.0, 98.0, 10, 0)
    assert list(sf._failed_auction(sf.prepare(bars(spec)))) == []


def test_cvd_divergence_new_low_with_positive_delta():
    spec = [(100 - k * 0.1, 100.5 - k * 0.1, 99.5 - k * 0.1, 100 - k * 0.1, 10, 3.0) for k in range(25)]
    s = sf.prepare(bars(spec))
    assert 24 in list(sf._cvd_div(s, N=20, tau=0.0))
    spec = [(o, h, low, c, v, -3.0) for o, h, low, c, v, _ in spec]
    assert list(sf._cvd_div(sf.prepare(bars(spec)), N=20)) == []


def test_absorption_high_volume_narrow_bar_with_selling_that_does_not_follow_through():
    spec = flat(15) + [(100 - k, 100.5 - k, 99 - k - 0.5, 99 - k, 10, -2) for k in range(10)]
    last = spec[-1][3]
    spec.append((last, last + 0.3, last - 0.3, last + 0.1, 40, -10))   # rv 4, body 0.1, d/v -0.25
    s = sf.prepare(bars(spec))
    assert list(sf._absorption(s, R=2.0)) == [len(spec) - 1]
    spec[-1] = (last, last + 0.3, last - 0.3, last + 0.1, 40, 10)      # buyers → not absorption
    assert list(sf._absorption(sf.prepare(bars(spec)), R=2.0)) == []


def test_session_vwap_and_bands_by_hand():
    spec = [(10, 11, 9, 10, 1), (20, 21, 19, 20, 3)]
    vw, sg, cnt = sf.session_vwap(sf.prepare(bars(spec)))
    assert vw[0] == 10 and math.isclose(vw[1], (10 * 1 + 20 * 3) / 4)
    assert math.isclose(sg[1], math.sqrt((100 * 1 + 400 * 3) / 4 - 17.5 ** 2)) and cnt == [1, 2]


def test_volume_profile_poc_value_area_and_nodes():
    h = [101, 101, 101, 101, 110]
    low = [100, 100, 100, 100, 100]
    v = [10, 10, 10, 10, 10]
    p = sf.volume_profile(h, low, v, d=[1, 1, 1, 1, -5], bins=10)
    assert 100 <= p["poc"] <= 101 and p["val"] == 100
    assert p["vah"] <= 102 + 1e-9 and math.isclose(sum(b["volume"] for b in p["bins"]), 50)
    assert math.isclose(sum(b["delta"] for b in p["bins"]), -1)


def test_score_rules():
    spec = flat(30) + [(100, 101, 99, 100, 10, 0)] + [(100, 101.2 + 0.4 * k, 99.5, 101 + 0.4 * k, 10, 0)
                                                      for k in range(60)]
    s = sf.prepare(bars(spec))
    o = se.score(s, 30, 1)
    assert o["atr_rule"] == "win" and o["r1.5"] == 1.5 and o["r2"] == 2.0
    assert se.score(s, 30, -1)["atr_rule"] == "loss" and se.score(s, 30, -1)["r1.5"] == -1.0
    assert se.score(s, len(spec) - 5, 1) is None                      # not enough future bars
    st = se.summarize([o, se.score(s, 30, -1)], net=False)
    assert st["n"] == 2 and st["hit_rate_decided"] == 0.5 and math.isclose(st["expectancy_r"], 0.25)


def test_cooldown_and_htf_filter():
    assert se.apply([(10, 1), (15, 1), (23, -1), (30, 1)]) == [(10, 1), (23, -1)]
    assert se.apply([(10, 1), (40, -1)], bias=[1] * 50) == [(10, 1)]


def _random_series(n=1500, seed=7):
    rnd, price, out = random.Random(seed), 100.0, []
    for i in range(n):
        c = price * (1 + rnd.gauss(0, 0.006))
        h, low = max(price, c) * (1 + abs(rnd.gauss(0, 0.002))), min(price, c) * (1 - abs(rnd.gauss(0, 0.002)))
        v = rnd.uniform(50, 150) * (4 if rnd.random() < 0.03 else 1)
        out.append({"ts": i * STEP, "close_ts": (i + 1) * STEP, "o": price, "h": h, "l": low, "c": c,
                    "v": v, "delta": rnd.uniform(-0.6, 0.6) * v})
        price = c
    return out


def _poison(candles, t):
    """Replace every bar after index t with garbage: decisions at ≤ t must not change."""
    bad = [dict(b) for b in candles]
    for b in bad[t + 1:]:
        b.update(o=1e6, h=2e6, l=1.0, c=5e5, v=1e9, delta=-1e9)
    return bad


def test_no_look_ahead_signals_and_wyckoff_events_with_poisoned_future():
    rows = _random_series()
    acc, _ = accumulation(with_delta=True)
    shift = acc[-1]["close_ts"]
    for candles in (rows, acc + [{**b, "ts": b["ts"] + shift, "close_ts": b["close_ts"] + shift}
                                 for b in rows[:300]]):
        full = sf.prepare(candles)
        bias_full = se.htf_bias(full)
        wy_full = se.wyckoff_events(candles)
        for t in (400, 777, 1100):
            if t >= len(candles) - 1:
                continue
            cut = sf.prepare(_poison(candles, t))
            for name, (_fn, _d, grid) in se.RULES.items():
                for params in grid:
                    a = [e for e in se.rule_events(full, name, params) if e[0] <= t]
                    b = [e for e in se.rule_events(cut, name, params) if e[0] <= t]
                    assert a == b, (name, params, t)
            assert se.htf_bias(cut)[:t + 1] == bias_full[:t + 1]
            wy_cut = se.wyckoff_events(_poison(candles, t))
            for code in set(wy_full) | set(wy_cut):
                assert sorted(e for e in wy_full.get(code, []) if e[0] <= t) == \
                    sorted(e for e in wy_cut.get(code, []) if e[0] <= t), (code, t)
            snap = wyckoff.analyze(candles[:t + 1])
            assert all(e["known_at"] <= candles[t]["close_ts"] for e in snap["events"])


def test_funding_events_use_only_settled_rates():
    s = sf.prepare(bars(flat(400)))
    rnd = random.Random(3)
    rates = [{"time": k * STEP, "rate": rnd.uniform(5e-5, 1.5e-4)} for k in range(300)] + \
        [{"time": 300 * STEP, "rate": 0.01}]
    ev = se.funding_events(s, rates, q=0.05)
    assert ev and ev[-1] == (299, -1)  # bar 299 closes at 300·STEP, when the extreme rate settled
    assert all(i >= 269 for i, _ in ev)  # needs 270 settled rates of history
    assert (298, -1) not in ev
