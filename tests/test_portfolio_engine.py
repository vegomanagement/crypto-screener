"""portfolio_engine + portfolio_strategies: timing, costs, funding sign, sizing, stops, causality."""
import math
import random

import pytest

import portfolio_engine as pe
import portfolio_strategies as ps

DAY = pe.DAY
T0 = 1_577_836_800_000  # 2020-01-01


def walk(n, seed, p0=100.0, vol=0.03):
    rnd, out, p = random.Random(seed), [], p0
    for i in range(n):
        o = p
        c = o * math.exp(rnd.gauss(0.001, vol))
        h, lo = max(o, c) * (1 + abs(rnd.gauss(0, vol / 2))), min(o, c) * (1 - abs(rnd.gauss(0, vol / 2)))
        out.append({"ts": T0 + i * DAY, "close_ts": T0 + (i + 1) * DAY, "o": o, "h": h, "l": lo, "c": c, "v": 1.0})
        p = c
    return out


def flat_bars(prices, t0=T0):
    return [{"ts": t0 + i * DAY, "o": o, "h": h, "l": lo, "c": c} for i, (o, h, lo, c) in enumerate(prices)]


class Script:
    """Strategy returning pre-set orders/stops per decision index, recording what it saw."""

    def __init__(self, plan, stops=None):
        self.plan, self.stops, self.seen = plan, stops or {}, []

    def decide(self, i, bk):
        self.seen.append((i, bk.i, dict(bk.qty)))
        return self.plan.get(i, {}), self.stops.get(i, {})


NOCOST = pe.Config(taker=0.0, slip=0.0, maker=0.0, funding=False)


def test_signal_at_close_fills_at_next_open():
    bars = flat_bars([(10, 11, 9, 10), (12, 13, 11, 12), (14, 15, 13, 14)])
    st = Script({0: {"X": {"w": 1.0}}})
    res = pe.run(pe.Market({"X": bars}), st, NOCOST)
    t = res["trades"][0]
    assert t["entry_i"] == 1 and t["entry_px"] == 12          # open of t+1, not close of t
    assert st.seen[0][1] == 0 and st.seen[0][2]["X"] == 0     # decided at close 0 while flat
    assert res["equity"][2] == pytest.approx(1 + (14 - 12) / 12)


def test_funding_sign_long_pays_short_receives():
    bars = flat_bars([(10, 10, 10, 10)] * 4)
    fund = {"X": [{"time": T0 + DAY + 8 * 3_600_000, "rate": 0.001}]}
    cfg = pe.Config(taker=0.0, slip=0.0, maker=0.0)
    for w, sign in ((1.0, -1), (-1.0, 1)):
        res = pe.run(pe.Market({"X": bars}, fund), Script({0: {"X": {"w": w}}}), cfg)
        assert sum(res["funding"]) == pytest.approx(sign * 0.001)   # rate × |qty| × close = 0.001 × 0.1 × 10
        assert res["equity"][-1] == pytest.approx(1 + sign * 0.001)


def test_funding_print_at_midnight_charged_to_the_day_before():
    bars = flat_bars([(10, 10, 10, 10)] * 4)
    fund = {"X": [{"time": T0 + 2 * DAY, "rate": 0.002}]}     # 00:00 print between day 1 and day 2
    cfg = pe.Config(taker=0.0, slip=0.0, maker=0.0)
    res = pe.run(pe.Market({"X": bars}, fund), Script({0: {"X": {"w": 1.0}}, 1: {"X": {"w": 0.0}}}), cfg)
    assert sum(res["funding"]) == pytest.approx(-0.002)       # held through it, closed at open of day 2


def test_rebalancing_costs():
    bars = flat_bars([(10, 10, 10, 10)] * 4)
    cfg = pe.Config(funding=False)
    res = pe.run(pe.Market({"X": bars}), Script({0: {"X": {"w": 0.5}}, 1: {"X": {"w": 1.0}}}), cfg)
    rate = cfg.taker + cfg.slip
    assert res["fees"][1] == pytest.approx(0.5 * rate)
    assert res["fees"][2] == pytest.approx((1 - 0.5 * rate - 0.5) * rate, rel=1e-9)
    assert res["turnover"][1] == pytest.approx(0.5)


def test_stop_intraday_gap_and_limit_same_day():
    bars = flat_bars([(10, 10, 10, 10), (10, 10.5, 9.5, 10), (10, 10.2, 8.5, 9), (9, 9, 9, 9)])
    res = pe.run(pe.Market({"X": bars}), Script({0: {"X": {"w": 1.0, "stop": 9.0}}}), NOCOST)
    t = res["trades"][0]
    assert t["why"] == "stop" and t["exit_i"] == 2 and t["exit_px"] == 9.0
    gap = flat_bars([(10, 10, 10, 10), (10, 10, 10, 10), (8, 8.5, 7.5, 8), (8, 8, 8, 8)])
    t = pe.run(pe.Market({"X": gap}), Script({0: {"X": {"w": 1.0, "stop": 9.0}}}), NOCOST)["trades"][0]
    assert t["exit_px"] == 8                                   # gapped through: filled at the open
    lim = flat_bars([(10, 10, 10, 10), (10, 10, 8.5, 9.5), (9.5, 9.5, 9.5, 9.5)])
    res = pe.run(pe.Market({"X": lim}), Script({0: {"X": {"w": 1.0, "type": "limit", "price": 9.5,
                                                          "stop": 9.0}}}), pe.Config(funding=False))
    t = res["trades"][0]
    assert t["kind"] == "limit" and t["entry_px"] == 9.5 and t["why"] == "stop" and t["exit_px"] == 9.0
    assert t["fees"] == pytest.approx(1 / 9.5 * (9.5 * 0.0002 + 9.0 * 0.0006))


def test_limit_not_reached_does_not_fill():
    bars = flat_bars([(10, 10, 10, 10), (10, 10.5, 9.8, 10), (10, 10, 10, 10)])
    res = pe.run(pe.Market({"X": bars}), Script({0: {"X": {"w": 1.0, "type": "limit", "price": 9.5}}}), NOCOST)
    assert res["trades"] == [] and res["equity"][-1] == 1.0


def test_delisted_coin_closed_at_last_close():
    a = flat_bars([(10, 10, 10, 10)] * 5)
    b = flat_bars([(10, 10, 10, 10), (10, 10, 10, 10), (10, 10, 10, 12)])
    res = pe.run(pe.Market({"A": a, "B": b}), Script({0: {"B": {"w": 1.0}}}), NOCOST)
    t = res["trades"][0]
    assert t["why"] == "delisted" and t["exit_i"] == 2 and t["exit_px"] == 12


def test_trend_vol_sizing_and_initial_stop():
    bars = walk(200, 3)
    mkt = pe.Market({"X": bars})
    ind = ps.Ind(mkt)
    st = ps.Trend(mkt, ind, "dc", 20, False, 3.0, risk=0.01, cap=10.0, gross=10.0)
    bk = pe.Book(mkt)
    for i in range(25, 200):
        orders, _ = st.decide(i, bk)
        if orders:
            o = orders["X"]
            a, c = ind.atr["X"][i], mkt.c["X"][i]
            assert c > ind.dchi["X"][20][i]
            assert o["w"] == pytest.approx(0.01 / (3 * a / c))
            assert o["stop"] == pytest.approx(c - 3 * a)
            break
    else:
        pytest.fail("no breakout in the sample")


def test_trend_cap_per_coin():
    bars = walk(200, 3, vol=0.002)
    mkt = pe.Market({"X": bars})
    st = ps.Trend(mkt, ps.Ind(mkt), "dc", 20, False, 3.0)
    res = pe.run(mkt, st, NOCOST)
    assert res["trades"] and max(res["gross"]) <= 0.30 * 1.2


def _strategies(mkt, ind):
    z = ps.funding_z(mkt, 30)
    reg = ps.Regime(mkt, "R4", btc="AAA")
    return [ps.Trend(mkt, ind, "ema", 50, True, 3.0, allow=reg.allow),
            ps.Trend(mkt, ind, "dc", 20, True, 5.0),
            ps.Momentum(mkt, ind, 2, 2, "ls", allow=ps.Regime(mkt, "R3", btc="AAA").allow),
            ps.FundingTS(mkt, ind, z, 3, True),
            ps.FundingXS(mkt, ind, z, 1, n=1)]


def _market(poison_after=None):
    syms = {"AAA": walk(700, 1), "BBB": walk(700, 2), "CCC": walk(650, 5), "DDD": walk(700, 9)}
    rnd = random.Random(11)
    fund = {s: [{"time": T0 + k * 8 * 3_600_000, "rate": rnd.gauss(0.0001, 0.0003)} for k in range(2100)]
            for s in syms}
    if poison_after is not None:
        cut = T0 + (poison_after + 1) * DAY
        for bars in syms.values():
            for b in bars:
                if b["ts"] >= cut:
                    b.update(o=b["o"] * 3, h=b["h"] * 5, l=b["l"] * 0.2, c=b["c"] * 0.3)
        for rows in fund.values():
            for r in rows:
                if r["time"] > cut:
                    r["rate"] = 0.05
    return pe.Market(syms, fund)


def test_poisoned_future_identical_history():
    k = 520
    clean, dirty = _market(), _market(poison_after=k)
    a_all = _strategies(clean, ps.Ind(clean))
    b_all = _strategies(dirty, ps.Ind(dirty))
    for a, b in zip(a_all, b_all):
        ra, rb = pe.run(clean, a), pe.run(dirty, b)
        assert ra["equity"][:k + 1] == rb["equity"][:k + 1], type(a).__name__
        ta = [(t["sym"], t["entry_i"], t["entry_px"], t["exit_i"]) for t in ra["trades"]
              if t["exit_i"] is not None and t["exit_i"] <= k]
        tb = [(t["sym"], t["entry_i"], t["entry_px"], t["exit_i"]) for t in rb["trades"]
              if t["exit_i"] is not None and t["exit_i"] <= k]
        assert ta == tb and ta, type(a).__name__


def test_truncated_market_drops_future_bars_and_funding():
    bars = walk(10, 1)
    m = pe.Market({"X": bars}, {"X": [{"time": T0 + 9 * DAY, "rate": 1.0}]}, end_ms=T0 + 5 * DAY)
    assert len(m.days) == 5 and m.funding_sum("X", 0, T0 + 20 * DAY) == 0.0


def test_regime_allow_shapes():
    mkt = _market()
    for kind in ("R1", "R2", "R3", "R4"):
        r = ps.Regime(mkt, kind, btc="AAA")
        vals = {r.allow(i) for i in range(len(mkt.days))}
        assert vals <= {(True, True), (True, False), (False, True), (False, False)}
    r1 = ps.Regime(mkt, "R1", btc="AAA")
    assert r1.allow(10) == (True, True) and r1.allow(400) in {(True, False), (False, True)}


def test_stats_drawdown_peryear_dsr():
    import portfolio_stats as pst
    eq = [1.0, 1.2, 0.9, 1.0, 1.3, 1.1]
    ts = [T0 + i * DAY for i in range(6)]
    mdd, dur = pst.drawdown(eq, ts)
    assert mdd == pytest.approx(0.25) and dur == 3
    yr = pst.per_year([1.0, 1.1, 1.21], [T0 - 2 * DAY, T0 - DAY, T0])
    assert yr == {"2019": 0.1, "2020": 0.1}
    rnd = random.Random(1)
    rets = [rnd.gauss(0.002, 0.02) for _ in range(1000)]
    assert pst.dsr(rets, 100, 0.0005) < pst.psr(rets)
    assert pst.holm({"a": 0.01, "b": 0.04}) == {"a": 0.02, "b": 0.04}


def _h4(seed, n_days, poison_after=None):
    rnd, out, p = random.Random(seed), [], 100.0
    for k in range(n_days * 6):
        o = p
        c = o * math.exp(rnd.gauss(0.0003, 0.012))
        h, lo = max(o, c) * (1 + abs(rnd.gauss(0, 0.005))), min(o, c) * (1 - abs(rnd.gauss(0, 0.005)))
        t = T0 + k * 4 * 3_600_000
        b = {"ts": t, "close_ts": t + 4 * 3_600_000, "o": o, "h": h, "l": lo, "c": c, "v": 1.0}
        if poison_after is not None and t >= T0 + (poison_after + 1) * DAY:
            b.update(h=h * 4, l=lo * 0.25, c=c * 0.5)
        out.append(b)
        p = c
    return out


def _daily_from(h4):
    out = []
    for j in range(0, len(h4), 6):
        g = h4[j:j + 6]
        out.append({"ts": g[0]["ts"], "o": g[0]["o"], "h": max(b["h"] for b in g), "l": min(b["l"] for b in g),
                    "c": g[-1]["c"]})
    return out


def test_poisoned_future_smc_execution():
    k, n = 300, 420
    runs = []
    for poison in (None, k):
        h4 = {s: _h4(i + 3, n, poison) for i, s in enumerate(("AAA", "BBB"))}
        mkt = pe.Market({s: _daily_from(b) for s, b in h4.items()})
        zones = ps.SmcZones(mkt, h4)
        st = ps.Trend(mkt, ps.Ind(mkt), "ema", 50, True, 3.0, smc="market", zones=zones)
        runs.append(pe.run(mkt, st))
    a, b = runs
    assert a["equity"][:k + 1] == b["equity"][:k + 1]
    ta = [(t["sym"], t["entry_i"], t["kind"]) for t in a["trades"] if t["exit_i"] is not None and t["exit_i"] <= k]
    tb = [(t["sym"], t["entry_i"], t["kind"]) for t in b["trades"] if t["exit_i"] is not None and t["exit_i"] <= k]
    assert ta == tb and any(x[2] == "limit" for x in ta)
