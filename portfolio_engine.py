"""Daily portfolio engine for perp strategies (stdlib, causal) — see docs/STRATEGY_PREREG.md §2.

Timeline of day i (bars aligned on UTC days, ``Market.days[i]`` = the day's open time):
  1. open    market orders decided at the close of day i−1 fill at open_i (taker + slippage);
  2. limits  one-day limit orders: a buy fills at open_i when open_i ≤ price, else at the price when
             low_i ≤ price (maker, no slippage); sells mirror;
  3. stops   a long with a resting stop exits at min(open_i, stop) when low_i ≤ stop (taker +
             slippage); a position filled by a limit today whose range also reached the stop is
             stopped (worst case);
  4. funding every position pays −sign × rate × |qty| × close_i for each print with
             open_i < time ≤ close_i (stopped-today positions: prints in the first half of the day);
  5. close   marks, delisting (last bar of a coin → close at close_i), equity E_i;
  6. decide  ``strategy.decide(i, book)`` sees only data up to i and returns (orders, stops) for day
             i+1; quantity = weight × E_i / fill price.
Accounting is linear (USDT-margined): equity = 1 + Σ_coins (coin cash flows + qty × mark).
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass

DAY = 86_400_000
H8 = 8 * 3_600_000


@dataclass
class Config:
    taker: float = 0.0005
    slip: float = 0.0001
    maker: float = 0.0002
    fund_fallback: float = 0.0001      # per 8 h, charged against the position (both sides)
    funding: bool = True               # False for spot-like benchmarks


class Market:
    """Aligned daily OHLC per symbol (None where the coin has no bar) and funding prints."""

    def __init__(self, bars_by_sym, funding_by_sym=None, end_ms=None, start_ms=None):
        keep = {s: [b for b in bars if (end_ms is None or b["ts"] + DAY <= end_ms)
                    and (start_ms is None or b["ts"] >= start_ms)] for s, bars in bars_by_sym.items()}
        self.days = sorted({b["ts"] for bars in keep.values() for b in bars})
        pos = {t: i for i, t in enumerate(self.days)}
        n = len(self.days)
        self.syms = sorted(keep)
        self.o, self.h, self.l, self.c, self.v = {}, {}, {}, {}, {}
        self.first, self.last = {}, {}
        for s in self.syms:
            arr = {k: [None] * n for k in "ohlcv"}
            for b in keep[s]:
                i = pos[b["ts"]]
                for k in "ohlc":
                    arr[k][i] = b[k]
                arr["v"][i] = b.get("qv", b.get("v"))
            self.o[s], self.h[s], self.l[s], self.c[s], self.v[s] = (arr[k] for k in "ohlcv")
            idx = [i for i in range(n) if arr["c"][i] is not None]
            if idx:
                self.first[s], self.last[s] = idx[0], idx[-1]
        self.fund = {}
        for s in self.syms:
            rows = (funding_by_sym or {}).get(s)
            if rows is None:
                self.fund[s] = None
                continue
            rows = [r for r in rows if end_ms is None or r["time"] <= end_ms]
            ts, cum, acc = [], [], 0.0
            for r in rows:
                acc += r["rate"]
                ts.append(r["time"])
                cum.append(acc)
            self.fund[s] = (ts, cum)

    def has(self, s, i):
        return 0 <= i < len(self.days) and self.c[s][i] is not None

    def funding_sum(self, s, a, b):
        """Sum of funding rates with a < time ≤ b; None when the venue has no history."""
        f = self.fund.get(s)
        if f is None:
            return None
        ts, cum = f
        i, j = bisect.bisect_right(ts, a), bisect.bisect_right(ts, b)
        return (cum[j - 1] if j else 0.0) - (cum[i - 1] if i else 0.0)


class Book:
    """What a strategy may see at the close of day i: positions, equity, open trades."""

    def __init__(self, mkt):
        self.mkt, self.i, self.equity = mkt, -1, 1.0
        self.qty = {s: 0.0 for s in mkt.syms}
        self.mark = {s: None for s in mkt.syms}
        self.stops = {}
        self.open = {}                   # sym → open trade record
        self.closed_today = {}           # sym → exit reason of a trade closed on day i

    def weight(self, s):
        m = self.mark[s]
        return 0.0 if not m or not self.equity else self.qty[s] * m / self.equity

    def gross(self):
        return sum(abs(self.weight(s)) for s in self.mkt.syms)


def run(mkt, strategy, cfg=None, start_i=0, end_i=None):
    """Simulate ``strategy`` over day indices [start_i, end_i). Returns a result dict."""
    cfg = cfg or Config()
    n = len(mkt.days) if end_i is None else end_i
    bk = Book(mkt)
    cash = {s: 0.0 for s in mkt.syms}
    trades, orders, out = [], {}, {"ts": [], "equity": [], "ret": [], "fees": [], "funding": [],
                                   "turnover": [], "gross": []}
    day = {"fees": 0.0, "fund": 0.0, "turn": 0.0}

    def trade(s, new_q, px, rate, i, why, meta=None):
        q = bk.qty[s]
        if q and new_q and (q > 0) != (new_q > 0):
            trade(s, 0.0, px, rate, i, why)
            q = 0.0
        dq = new_q - q
        if abs(dq) < 1e-15:
            return
        fee = abs(dq) * px * rate
        if q == 0:
            meta = meta or {}
            bk.open[s] = {"sym": s, "side": 1 if new_q > 0 else -1, "entry_i": i, "entry_px": px,
                          "cash0": cash[s], "notional": abs(new_q) * px, "risk": meta.get("risk"),
                          "fees": 0.0, "funding": 0.0, "kind": meta.get("kind", "market"),
                          "tag": meta.get("tag")}
        cash[s] -= dq * px + fee
        day["fees"] += fee
        day["turn"] += abs(dq) * px
        bk.open[s]["fees"] += fee
        bk.qty[s] = new_q
        if new_q == 0:
            t = bk.open.pop(s)
            t.update(exit_i=i, exit_px=px, why=why, pnl=cash[s] - t.pop("cash0"))
            trades.append(t)
            bk.stops.pop(s, None)
            bk.closed_today[s] = why

    def fund(s, q, a, b, px):
        if not cfg.funding or not q:
            return
        f = mkt.funding_sum(s, a, b)
        if f is None:
            flow = -abs(q) * px * cfg.fund_fallback * (b - a) / H8
        else:
            flow = -(1 if q > 0 else -1) * f * abs(q) * px
        cash[s] += flow
        day["fund"] += flow
        if s in bk.open:
            bk.open[s]["funding"] += flow

    for i in range(start_i, n):
        day.update(fees=0.0, fund=0.0, turn=0.0)
        bk.closed_today = {}
        t0 = mkt.days[i]
        e_prev = bk.equity
        stopped = {}
        limit_filled = set()
        for s, od in orders.items():                       # 1–2. market + limit orders
            if not mkt.has(s, i):
                continue
            o, lo, hi = mkt.o[s][i], mkt.l[s][i], mkt.h[s][i]
            kind = od.get("type", "market")
            if kind == "market":
                trade(s, od["w"] * e_prev / o, o, cfg.taker + cfg.slip, i, od.get("tag", "signal"), od)
            else:
                p = od["price"]
                buy = od["w"] * e_prev / p > bk.qty[s]
                px = (o if o <= p else p if lo <= p else None) if buy else (o if o >= p else p if hi >= p else None)
                if px is not None:
                    trade(s, od["w"] * e_prev / px, px, cfg.maker, i, od.get("tag", "limit"), dict(od, kind="limit"))
                    limit_filled.add(s)
            if bk.qty[s] and od.get("stop") is not None:
                bk.stops[s] = od["stop"]
        for s, st in list(bk.stops.items()):               # 3. stops
            q = bk.qty[s]
            if not q or not mkt.has(s, i):
                continue
            o, lo, hi = mkt.o[s][i], mkt.l[s][i], mkt.h[s][i]
            if q > 0 and lo <= st:
                px = min(o, st) if s not in limit_filled else min(st, bk.open[s]["entry_px"])
            elif q < 0 and hi >= st:
                px = max(o, st) if s not in limit_filled else max(st, bk.open[s]["entry_px"])
            else:
                continue
            stopped[s] = (q, px)
            fund(s, q, t0, t0 + DAY // 2, px)
            trade(s, 0.0, px, cfg.taker + cfg.slip, i, "stop")
        for s in mkt.syms:                                 # 4–5. funding, marks, delisting
            if mkt.has(s, i):
                bk.mark[s] = mkt.c[s][i]
            if bk.qty[s]:
                fund(s, bk.qty[s], t0, t0 + DAY, bk.mark[s])
                if mkt.last.get(s) == i:
                    trade(s, 0.0, bk.mark[s], cfg.taker + cfg.slip, i, "delisted")
        bk.i = i
        bk.equity = 1.0 + sum(cash[s] + bk.qty[s] * (bk.mark[s] or 0.0) for s in mkt.syms)
        out["ts"].append(t0)
        out["equity"].append(bk.equity)
        out["ret"].append(bk.equity / e_prev - 1.0 if out["ret"] else 0.0)
        out["fees"].append(day["fees"])
        out["funding"].append(day["fund"])
        out["turnover"].append(day["turn"])
        out["gross"].append(bk.gross())
        if i < n - 1:                                      # 6. decide for day i+1
            orders, new_stops = strategy.decide(i, bk)
            for s, st in (new_stops or {}).items():
                if bk.qty[s]:
                    bk.stops[s] = st
    for t in bk.open.values():                             # still open at the end: mark to market
        s = t["sym"]
        t.update(exit_i=None, exit_px=bk.mark[s], why="open",
                 pnl=cash[s] + bk.qty[s] * (bk.mark[s] or 0.0) - t["cash0"])
        t.pop("cash0", None)
    res = dict(out)
    res["trades"] = trades + list(bk.open.values())
    res["coin_pnl"] = {s: cash[s] + bk.qty[s] * (bk.mark[s] or 0.0) for s in mkt.syms}
    return res
