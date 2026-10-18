"""Daily delta-neutral carry simulator (stdlib) — rules in docs/CARRY_PREREG.md §3.

A *pair* = a long leg and a short leg of the same coin, equal units (1:1 hedge in units):
  spot–perp   long spot (fully paid, notional N = C·L/(L+1)) + short perp margined with C/(L+1);
  perp–perp   long perp on venue A + short perp on venue B, each margined at 1x with C/2 (N = C/2).
Timeline of day i (UTC days): 1. trades decided at the close of i−1 fill at open_i (taker + slip on
|Δq| per leg; a re-split moves cash between legs for free); 2. liquidation: a perp leg whose equity
at the day's adverse extreme (high for a short, low for a long) falls below maintenance × notional
is lost (equity → 0) and the pair is closed at the next open; 3. funding prints with open_i <
time ≤ close_i: flow = −q × rate × close (longs pay positive rates); 4. marks at the closes (basis =
the two legs' closes, so spread changes are in the P&L); a coin whose data ends is closed at its last
close; 5. rebalance flags: a perp leg outside [0.5, 1.5] × its target share of the pair's value is
re-split at the next open; 6. ``decide(i)`` → target weights {pair_id: w} (None = no change).
Equity = free cash + Σ legs (cash + q × close); returns are on TOTAL capital.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field

DAY = 86_400_000


@dataclass
class Costs:
    spot: float = 0.0010
    perp: dict = field(default_factory=lambda: {"binance": 0.0005, "bybit": 0.00055, "hyperliquid": 0.00045})
    slip: float = 0.0001
    mm_major: float = 0.005            # maintenance margin, BTC/ETH
    mm_other: float = 0.01


class Inst:
    """Daily OHLC aligned to ``days`` (None where missing) and an optional funding index."""

    def __init__(self, days, bars, kind, venue, funding=None):
        self.kind, self.venue = kind, venue
        pos = {t: i for i, t in enumerate(days)}
        n = len(days)
        self.o, self.h, self.l, self.c = ([None] * n for _ in range(4))
        for b in bars:
            i = pos.get(b["ts"])
            if i is not None:
                self.o[i], self.h[i], self.l[i], self.c[i] = b["o"], b["h"], b["l"], b["c"]
        idx = [i for i in range(n) if self.c[i] is not None]
        self.first, self.last = (idx[0], idx[-1]) if idx else (None, None)
        self.fts, self.fcum = [], []
        acc = 0.0
        for r in funding or ():
            acc += r["rate"]
            self.fts.append(r["time"])
            self.fcum.append(acc)

    def has(self, i):
        return self.c[i] is not None

    def fsum(self, a, b):
        i, j = bisect.bisect_right(self.fts, a), bisect.bisect_right(self.fts, b)
        return (self.fcum[j - 1] if j else 0.0) - (self.fcum[i - 1] if i else 0.0)


@dataclass
class PairSpec:
    pid: str
    coin: str
    long: Inst
    short: Inst
    L: float = 1.0

    def split(self, C):
        """(notional, long-leg capital, short-leg capital) for pair capital C."""
        if self.long.kind == "spot":
            n = C * self.L / (self.L + 1)
            return n, n, C / (self.L + 1)
        return C / 2, C / 2, C / 2


def simulate(days, pairs, decide, costs=None, start_i=0, end_i=None, band=(0.5, 1.5)):
    costs = costs or Costs()
    end_i = len(days) if end_i is None else end_i
    legs = {p.pid: {"ql": 0.0, "qs": 0.0, "cl": 0.0, "cs": 0.0} for p in pairs.values()}
    target, pending, mark = {}, {}, {}
    free = 1.0
    out = {"ts": [], "equity": [], "funding": [], "fees": [], "deployed": [], "rebalances": 0, "liquidations": [],
           "trades": 0}

    def px(inst, i, k):
        v = getattr(inst, k)[i]
        return v if v is not None else mark.get(id(inst))

    def fee_rate(inst):
        return (costs.spot if inst.kind == "spot" else costs.perp[inst.venue]) + costs.slip

    def value(pid, i, k):
        p, g = pairs[pid], legs[pid]
        return g["cl"] + g["ql"] * (px(p.long, i, k) or 0) + g["cs"] + g["qs"] * (px(p.short, i, k) or 0)

    def reset(pid, C, i, po_l, po_s, k="o"):
        """Move pair ``pid`` to capital C at its ``k`` prices (po_l, po_s); returns the fee paid."""
        nonlocal free
        p, g = pairs[pid], legs[pid]
        free += value(pid, i, k)
        n, al, as_ = p.split(C) if C > 0 else (0.0, 0.0, 0.0)
        q = n / po_l if C > 0 else 0.0
        fee = abs(q - g["ql"]) * po_l * fee_rate(p.long) + abs(-q - g["qs"]) * po_s * fee_rate(p.short)
        if abs(q - g["ql"]) > 1e-12:
            out["trades"] += 1
        free -= C
        g.update(ql=q, qs=-q, cl=al - q * po_l - fee / 2, cs=as_ + q * po_s - fee / 2)
        if C <= 0:
            free += g["cl"] + g["cs"]
            g.update(cl=0.0, cs=0.0)
        return fee

    for i in range(start_i, end_i):
        t0 = days[i]
        e_open = free + sum(value(pid, i, "o") for pid in legs)
        day_fee = day_fund = 0.0
        for pid, w in sorted(pending.items()):                                      # 1. open
            p = pairs[pid]
            if not (p.long.has(i) and p.short.has(i)):
                continue
            C = value(pid, i, "o") if w == "resplit" else w * e_open
            day_fee += reset(pid, C, i, p.long.o[i], p.short.o[i])
            del pending[pid]
        for pid, g in legs.items():                                                 # 2. liquidation
            p = pairs[pid]
            if not g["ql"]:
                continue
            for side, inst, q, ck in (("long", p.long, g["ql"], "cl"), ("short", p.short, g["qs"], "cs")):
                if inst.kind == "spot" or not inst.has(i):
                    continue
                adv = inst.l[i] if q > 0 else inst.h[i]
                mm = costs.mm_major if p.coin in ("BTCUSDT", "ETHUSDT") else costs.mm_other
                if g[ck] + q * adv < mm * abs(q) * adv:
                    g[ck], g["ql" if side == "long" else "qs"] = 0.0, 0.0
                    out["liquidations"].append({"pair": pid, "ts": t0, "leg": side})
                    pending[pid], target[pid] = 0.0, 0.0
        for pid, g in legs.items():                                                 # 3. funding
            p = pairs[pid]
            for inst, q, ck in ((p.long, g["ql"], "cl"), (p.short, g["qs"], "cs")):
                if inst.kind == "perp" and q and inst.has(i):
                    flow = -q * inst.fsum(t0, t0 + DAY) * inst.c[i]
                    g[ck] += flow
                    day_fund += flow
        for p in pairs.values():                                                    # 4. marks, data end
            for inst in (p.long, p.short):
                if inst.has(i):
                    mark[id(inst)] = inst.c[i]
            g = legs[p.pid]
            if (g["ql"] or g["qs"]) and i in (p.long.last, p.short.last) and i < end_i - 1:
                day_fee += reset(p.pid, 0.0, i, px(p.long, i, "c"), px(p.short, i, "c"), "c")
                target.pop(p.pid, None)
        if i == end_i - 1:                                                          # end of run: close all
            for pid, g in legs.items():
                if g["ql"] or g["qs"]:
                    day_fee += reset(pid, 0.0, i, px(pairs[pid].long, i, "c"), px(pairs[pid].short, i, "c"), "c")
        e = free + sum(value(pid, i, "c") for pid in legs)
        out["ts"].append(t0)
        out["equity"].append(e)
        out["funding"].append(day_fund)
        out["fees"].append(day_fee)
        out["deployed"].append(sum(value(pid, i, "c") for pid, g in legs.items() if g["ql"]) / e if e > 0 else 0.0)
        if i == end_i - 1:
            break
        for pid, g in legs.items():                                                 # 5. rebalance flags
            p = pairs[pid]
            if not g["ql"] or pid in pending:
                continue
            v = value(pid, i, "c")
            n, al, as_ = p.split(v)
            for inst, q, ck, share in ((p.long, g["ql"], "cl", al), (p.short, g["qs"], "cs", as_)):
                eq = g[ck] + q * px(inst, i, "c")
                if inst.kind == "perp" and not (band[0] * share <= eq <= band[1] * share):
                    pending[pid] = "resplit"
                    out["rebalances"] += 1
                    break
        new = decide(i)                                                             # 6. decide
        if new is not None:
            new = dict(new)
            force = new.pop("__force__", False)          # re-target every listed pair to w × equity
            for pid in sorted(set(new) | set(target)):
                w = new.get(pid, 0.0)
                if abs(w - target.get(pid, 0.0)) > 1e-12 or (force and w > 0):
                    pending[pid] = w
                    target[pid] = w
    return out
