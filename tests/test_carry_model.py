"""Carry simulator (carry_model.py): fees on both legs, funding sign, basis P&L, re-split, liquidation,
data end — checked against hand calculations."""
from __future__ import annotations

import carry_model as cm

DAY = cm.DAY
T0 = 1_700_000_000_000 // DAY * DAY


def bars(prices, highs=None):
    return [{"ts": T0 + k * DAY, "o": o, "h": (highs or {}).get(k, max(o, c)), "l": min(o, c), "c": c}
            for k, (o, c) in enumerate(prices)]


def setup(spot, perp, funding=(), L=1.0, coin="XUSDT", highs=None):
    days = [T0 + k * DAY for k in range(len(spot))]
    s = cm.Inst(days, bars(spot), "spot", "binance")
    p = cm.Inst(days, bars(perp, highs), "perp", "binance", funding)
    return days, {"X": cm.PairSpec("X", coin, s, p, L)}


def always(i):
    return {"X": 1.0}


def test_entry_fees_funding_and_exit_by_hand():
    flat = [(100.0, 100.0)] * 4
    fund = [{"time": T0 + 1 * DAY + 8 * 3_600_000, "rate": 0.001}]           # one print on day 1
    days, pairs = setup(flat, flat, fund)
    c = cm.Costs()
    res = cm.simulate(days, pairs, always, c)
    q = 0.5 / 100                                                         # L = 1: notional = half the capital
    entry_fee = q * 100 * (c.spot + c.slip) + q * 100 * (c.perp["binance"] + c.slip)
    assert abs(res["equity"][1] - (1 - entry_fee + 0.001 * q * 100)) < 1e-15
    assert abs(res["funding"][1] - 0.001 * q * 100) < 1e-15                # the short receives a positive rate
    assert abs(res["equity"][-1] - (1 - 2 * entry_fee + 0.001 * q * 100)) < 1e-12      # closed at the end
    assert res["trades"] == 2 and res["liquidations"] == []


def test_basis_widening_is_marked_to_market():
    spot = [(100.0, 100.0)] * 3
    perp = [(100.0, 100.0), (100.0, 100.0), (100.0, 102.0)]
    days, pairs = setup(spot, perp)
    res = cm.simulate(days, pairs, always, cm.Costs(spot=0, perp={"binance": 0}, slip=0), end_i=3)
    q = 0.005
    # day 2 close: the perp is 2 above spot → short leg −2 × q; then closed at the end at those closes
    assert abs(res["equity"][2] - (1 - 2 * q)) < 1e-15


def test_short_leg_resplit_after_a_pump_and_liquidation_at_3x():
    up = [(100.0, 100.0), (100.0, 100.0), (100.0, 160.0), (160.0, 160.0), (160.0, 160.0)]
    days, pairs = setup(up, up, L=1.0)
    res = cm.simulate(days, pairs, always, cm.Costs())
    assert res["rebalances"] == 1 and res["liquidations"] == []           # short equity 0.4 × target → re-split
    days, pairs = setup(up, up, L=3.0, highs={2: 140.0})
    res = cm.simulate(days, pairs, always, cm.Costs())
    assert res["liquidations"] and res["liquidations"][0]["leg"] == "short"   # margin 1/3 of notional, +40 % high
    # the spot leg keeps its gain but the perp margin (¼ of capital) is lost: equity well below 1
    assert res["equity"][2] < 1 - 0.25 + 0.75 * 0.6 + 1e-9


def test_pair_closed_when_data_ends_and_force_retarget():
    spot = [(100.0, 100.0)] * 6
    perp = [(100.0, 100.0)] * 3
    days, pairs = setup(spot, perp + [(None, None)] * 0)
    pairs["X"].short = cm.Inst(days, bars(perp), "perp", "binance")
    res = cm.simulate(days, pairs, always, cm.Costs())
    assert res["deployed"][2] == 0.0 and res["deployed"][1] > 0.49          # closed at the perp's last close
    calls = []

    def monthly(i):
        calls.append(i)
        return {"X": 1.0, "__force__": i == 2}
    days, pairs = setup([(100.0, 100.0)] * 5, [(100.0, 100.0)] * 5)
    res = cm.simulate(days, pairs, monthly, cm.Costs())
    assert res["trades"] == 3                     # entry, forced re-target to w × (lower) equity, exit
    assert 0 < res["fees"][3] < 1e-5 < res["fees"][1]                     # the re-target only trades the drift
