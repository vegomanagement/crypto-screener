"""Breaker blocks, inducement (IDM), equal highs/lows; HTF swing-model variants on 1d→4h / 1w→1d:
deterministic rules, confirmation times and no look-ahead (poisoned futures)."""
import math
import sys
from pathlib import Path

import pytest

import smc
import swing_model as sm
from tests.test_mtf import walk
from tests.test_smc import bars, zigzag

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mtf_data  # noqa: E402

DAY = 86_400_000


def test_breaker_is_a_failed_ob_with_flipped_polarity():
    # up-leg → bull BOS with an OB; then a selloff closes below the OB and breaks structure down
    path = zigzag([100, 106, 98, 99], step=1.0) + [99.5, 104, 109, 113, 112, 111, 110, 103, 100, 98.5] + \
        zigzag([98.5, 94, 90, 88, 91, 89, 86, 84], step=1.0)
    c = bars(path, wick=0.2)
    st = smc.structure(c, 2, 2)
    assert st["breakers"], "fixture must produce a breaker"
    for z in st["breakers"]:
        ob = next(o for o in st["obs"] if o["i"] == z["i"] and o["confirm_i"] == z["ob_confirm_i"])
        assert z["dir"] != ob["dir"] and (z["top"], z["bottom"]) == (ob["top"], ob["bottom"])
        q = z["through_i"]
        closes_through = (lambda x: c[x]["c"] < ob["bottom"]) if ob["dir"] == "bull" else (lambda x: c[x]["c"] > ob["top"])
        assert closes_through(q) and not any(closes_through(x) for x in range(ob["confirm_i"] + 1, q))
        brk = next(b for b in st["breaks"] if b["i"] == z["break_i"])
        assert brk["dir"] == z["dir"] and abs(brk["i"] - q) <= smc.BREAKER_WINDOW and brk["i"] > ob["confirm_i"]
        assert z["confirm_i"] == max(q, brk["i"]) and z["confirm_ts"] == c[z["confirm_i"]]["close_ts"]
        if z["mitigated_i"] is not None:
            m = c[z["mitigated_i"]]
            assert (m["h"] >= z["bottom"]) if z["dir"] == "bear" else (m["l"] <= z["top"])


def test_inducement_is_the_first_internal_pullback_after_the_break_and_its_sweep():
    path = zigzag([100, 110, 104, 116, 112, 118, 113.5, 121, 108], step=1.0)
    c = bars(path, wick=0.2)
    ext = smc.structure(c, 3, 3)
    internal = smc.structure(c, *smc.INTERNAL, zones=False)["swings"]
    idm = smc.inducements(c, ext["breaks"], internal)
    assert idm
    for x in idm:
        b = next(b for b in ext["breaks"] if b["i"] == x["break_i"])
        kind = "L" if b["dir"] == "bull" else "H"
        first = min((s for s in internal if s["kind"] == kind and s["i"] > b["i"]), key=lambda s: s["i"])
        assert (x["i"], x["price"], x["confirm_i"]) == (first["i"], first["price"], first["confirm_i"])
        if x["swept_i"] is not None:
            through = (lambda q: c[q]["l"] < x["price"]) if kind == "L" else (lambda q: c[q]["h"] > x["price"])
            assert x["swept_i"] > x["confirm_i"] and through(x["swept_i"])
            assert not any(through(q) for q in range(x["confirm_i"] + 1, x["swept_i"]))


def test_equal_highs_lows_pool_within_atr_tolerance():
    path = zigzag([100, 110, 104, 110.02, 103, 109.98, 101, 112], step=0.5)
    c = bars(path, wick=0.05)
    internal = smc.structure(c, 2, 2, zones=False)["swings"]
    pools = smc.equal_levels(c, internal)
    eqh = [p for p in pools if p["kind"] == "EQH"]
    assert eqh, "two highs 0.02 apart must form an EQH pool"
    atr = smc.atr14(c)
    for p in eqh:
        hi = [s for s in internal if s["kind"] == "H" and s["i"] in (p["first_i"], p["i"])]
        assert len(hi) == 2 and abs(hi[0]["price"] - hi[1]["price"]) <= smc.EQ_TOL_ATR * atr[p["i"]]
        assert p["price"] == max(s["price"] for s in hi)
        assert all(x["h"] <= p["price"] for x in c[p["first_i"] + 1:p["i"]])
        assert p["swept_i"] is not None and c[p["swept_i"]]["h"] > p["price"]   # the final rally takes it
    assert not [p for p in smc.equal_levels(c, internal, tol=0.0) if p["kind"] == "EQH"]   # 0 tolerance: none


@pytest.mark.parametrize("cut", [150, 260, 333])
def test_breaker_idm_eq_no_look_ahead_poisoned_future(cut):
    path = [100 + 8 * math.sin(i / 6) + i * 0.05 + 2 * math.sin(i / 1.7) + 3 * math.sin(i / 23) for i in range(420)]
    c = bars(path)
    bad = c[:cut + 1] + [dict(x, h=x["h"] * 3, l=x["l"] / 3, c=x["c"] * 2) for x in c[cut + 1:]]
    t = c[cut]["close_ts"]

    def view(cs):
        st = smc.structure(cs, 3, 3)
        internal = smc.structure(cs, *smc.INTERNAL, zones=False)["swings"]
        cap = lambda v: v if v is not None and v <= cut else None  # noqa: E731 — state as of the cut
        brk = [(z["i"], z["dir"], z["top"], z["bottom"], cap(z["mitigated_i"]), cap(z["invalidated_i"]))
               for z in smc.as_of(st["breakers"], t)]
        idm = [(x["i"], x["price"], x["break_i"], cap(x["swept_i"])) for x in
               smc.as_of(smc.inducements(cs, st["breaks"], internal), t)]
        eq = [(p["kind"], p["price"], p["first_i"], p["i"], cap(p["swept_i"])) for p in
              smc.as_of(smc.equal_levels(cs, internal), t)]
        return brk, idm, eq

    a, b = view(c), view(bad)
    assert a == b
    assert any(a), "fixture must produce at least one breaker / IDM / pool before the cut"


VARIANTS = [{}, {"poi": "breaker"}, {"poi": "ob", "idm": "idm"}, {"poi": "any", "idm": "eq", "sweep": True},
            {"entry": "close", "stop": "poi", "target": "r2", "be": True}, {"entry": "limit", "stop": "poi"},
            {"poi": "fvg", "entry": "limit", "stop": "poi", "target": "r3"}]


def _fixture(pair):
    if pair == "1w>1d":
        ltf = walk(2400, DAY, seed=5)
        ltf = [dict(b, ts=b["ts"] - b["ts"] % DAY, close_ts=b["ts"] - b["ts"] % DAY + DAY) for b in ltf]
        return ltf, mtf_data.aggregate(ltf, "W", DAY)
    h1 = walk(24 * 900, 3_600_000, seed=9)
    return mtf_data.aggregate(h1, "240", 3_600_000), mtf_data.aggregate(h1, "D", 3_600_000)


@pytest.mark.parametrize("pair", ["1d>4h", "1w>1d"])
def test_htf_variants_no_look_ahead_and_closed_htf_candles(pair):
    ltf, htf = _fixture(pair)
    if pair == "1w>1d":
        assert htf and all(((b["ts"] // DAY) + 3) % 7 == 0 for b in htf)   # weeks open Monday 00:00 UTC
        assert all(b["close_ts"] - b["ts"] == 7 * DAY for b in htf)
    cut = len(ltf) * 2 // 3
    t_cut = ltf[cut]["close_ts"]
    bad_l = ltf[:cut + 1] + [dict(b, h=b["h"] * 2, l=b["l"] * 0.5, c=b["c"] * 1.5) for b in ltf[cut + 1:]]
    bad_h = [b if b["close_ts"] <= t_cut else dict(b, h=b["h"] * 3, l=b["l"] / 3, c=b["c"] * 2) for b in htf]
    total = 0
    for p in VARIANTS:
        for filt in (True, False):
            a, _ = sm.scan(ltf, htf, p, filtered=filt)
            b, _ = sm.scan(bad_l, bad_h, p, filtered=filt)
            total += len(a)
            done = lambda ts: [(t["i_signal"], t["entry"], t["stop"], t["target"], t["r"]) for t in ts if t["i_exit"] <= cut]  # noqa: E731
            assert done(a) == done(b), (p, filt)
            for t in a:
                # every HTF bar the decision used was CLOSED: by the signal bar's close (CHoCH
                # entries) or by the fill bar's OPEN (resting POI-edge limits)
                ref = ltf[t["i_signal"]]["ts"] if p.get("entry") == "limit" else ltf[t["i_signal"]]["close_ts"]
                assert htf[t["htf_k"]]["close_ts"] <= ref
                assert t["d"] * (t["entry"] - t["stop"]) > 0 and t["i_fill"] >= t["i_signal"]
    assert total > 0, "fixtures must produce trades"


def test_default_variant_is_the_original_model():
    ltf = walk(6000, 900_000)
    htf = mtf_data.aggregate(ltf, "240", 900_000)
    a, _ = sm.scan(ltf, htf)
    b, _ = sm.scan(ltf, htf, {"poi": "any", "idm": "none", "sweep": False, "entry": "retest", "stop": "choch",
                              "target": "liq", "be": False})
    assert [(t["i_signal"], t["r"]) for t in a] == [(t["i_signal"], t["r"]) for t in b] and a
