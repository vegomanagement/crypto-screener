"""Strategies for portfolio_engine.run (causal: indicators at index i use bars ≤ i only).

Rules and grids: docs/STRATEGY_PREREG.md §3. Every strategy exposes ``decide(i, book) → (orders,
stops)``; ``allow(i) → (long_ok, short_ok)`` is the regime hook (C).
"""
from __future__ import annotations

import bisect
import math
import random
import statistics as stx
import zlib
from datetime import UTC, datetime

DAY = 86_400_000
ALWAYS = (True, True)


def _allow_all(i):
    return ALWAYS


class Ind:
    """Per-coin indicator arrays aligned to market days (None until warm)."""

    def __init__(self, mkt, emas=(50, 100), dcs=(20, 55, 10, 27)):
        self.mkt, n = mkt, len(mkt.days)
        self.atr, self.sig, self.ema, self.dchi, self.dclo, self.nbars = {}, {}, {}, {}, {}, {}
        for s in mkt.syms:
            idx = [i for i in range(n) if mkt.c[s][i] is not None]
            h = [mkt.h[s][i] for i in idx]
            lo = [mkt.l[s][i] for i in idx]
            c = [mkt.c[s][i] for i in idx]
            m = len(idx)

            def scatter(vals, idx=idx):
                full = [None] * n
                for j, i in enumerate(idx):
                    full[i] = vals[j]
                return full
            tr = [max(h[j] - lo[j], abs(h[j] - c[j - 1]), abs(lo[j] - c[j - 1])) if j else h[j] - lo[j]
                  for j in range(m)]
            self.atr[s] = scatter([sum(tr[j - 19:j + 1]) / 20 if j >= 19 else None for j in range(m)])
            lr = [math.log(c[j] / c[j - 1]) if j else 0.0 for j in range(m)]
            self.sig[s] = scatter([stx.stdev(lr[j - 19:j + 1]) if j >= 20 else None for j in range(m)])
            self.nbars[s] = scatter(list(range(1, m + 1)))
            self.ema[s] = {}
            for p in emas:
                vals, e, a = [None] * m, None, 2 / (p + 1)
                for j in range(m):
                    if j == p - 1:
                        e = sum(c[:p]) / p
                    elif j >= p:
                        e = a * c[j] + (1 - a) * e
                    vals[j] = e
                self.ema[s][p] = scatter(vals)
            self.dchi[s], self.dclo[s] = {}, {}
            for p in dcs:
                self.dchi[s][p] = scatter([max(h[j - p:j]) if j >= p else None for j in range(m)])
                self.dclo[s][p] = scatter([min(lo[j - p:j]) if j >= p else None for j in range(m)])


def daily_funding(mkt):
    """{sym: [sum of prints in (open_i, close_i] or None]} — None before the first print."""
    out = {}
    for s in mkt.syms:
        f = mkt.fund.get(s)
        arr = [None] * len(mkt.days)
        if f is not None and f[0]:
            first = f[0][0]
            for i, t in enumerate(mkt.days):
                if mkt.c[s][i] is not None and t + DAY >= first:
                    arr[i] = mkt.funding_sum(s, t, t + DAY)
        out[s] = arr
    return out


def _pct_rank(vals, j, win):
    """Share of the trailing ``win`` values (incl. j) ≤ vals[j]; None while fewer exist."""
    if j < win - 1:
        return None
    w = vals[j - win + 1:j + 1]
    return sum(x <= vals[j] for x in w) / win


class Regime:
    """R1 trend, R2 vol, R3 funding, R4 = R1 ∧ R2 (prereg §3 C). allow(i) → (long_ok, short_ok)."""

    def __init__(self, mkt, kind, btc="BTCUSDT"):
        self.kind, n = kind, len(mkt.days)
        idx = [i for i in range(n) if mkt.c[btc][i] is not None]
        c = [mkt.c[btc][i] for i in idx]
        self.r1, self.r2, self.r3 = [None] * n, [None] * n, [None] * n
        lr = [math.log(c[j] / c[j - 1]) if j else 0.0 for j in range(len(c))]
        vol = [stx.stdev(lr[j - 29:j + 1]) if j >= 30 else None for j in range(len(c))]
        first_vol = next((j for j, v in enumerate(vol) if v is not None), len(c))
        vv = vol[first_vol:]
        for j, i in enumerate(idx):
            if j >= 199:
                self.r1[i] = c[j] > sum(c[j - 199:j + 1]) / 200
            if j >= first_vol:
                p = _pct_rank(vv, j - first_vol, 365)
                self.r2[i] = None if p is None else p <= 0.80
        fd = daily_funding(mkt)
        sums = {s: [None] * n for s in mkt.syms}
        for s in mkt.syms:
            a = fd[s]
            for i in range(6, n):
                w = a[i - 6:i + 1]
                if all(x is not None for x in w):
                    sums[s][i] = sum(w)
        agg_i, agg = [], []
        for i in range(n):
            xs = [sums[s][i] for s in mkt.syms if sums[s][i] is not None]
            if xs:
                agg_i.append(i)
                agg.append(sum(xs) / len(xs))
        for j, i in enumerate(agg_i):
            p = _pct_rank(agg, j, 365)
            self.r3[i] = None if p is None else (p > 0.80, p < 0.20)

    def allow(self, i):
        r1, r2, r3 = self.r1[i], self.r2[i], self.r3[i]
        t1 = ALWAYS if r1 is None else ((True, False) if r1 else (False, True))
        t2 = ALWAYS if r2 is None else ((True, True) if r2 else (False, False))
        if self.kind == "R1":
            return t1
        if self.kind == "R2":
            return t2
        if self.kind == "R3":
            return ALWAYS if r3 is None else (not r3[0], not r3[1])
        return (t1[0] and t2[0], t1[1] and t2[1])


def _gross_scale(bk, new_w, cap, closing=()):
    cur = sum(abs(bk.weight(s)) for s in bk.mkt.syms if s not in closing)
    tot = sum(abs(w) for w in new_w.values())
    if not tot or cur + tot <= cap:
        return 1.0
    return max(0.0, cap - cur) / tot


def weekday(ts):
    return datetime.fromtimestamp(ts / 1000, tz=UTC).weekday()


def crc_rng(*parts):
    return random.Random(zlib.crc32("|".join(map(str, parts)).encode()))


class SmcZones:
    """4h OB/FVG zones per coin (swing_model.htf_arrays) and the 4h bar closed at each day close."""

    def __init__(self, mkt, bars4h_by_sym):
        import swing_model as sm
        self.H, self.k_at, self.conf = {}, {}, {}
        for s in mkt.syms:
            b4 = [b for b in bars4h_by_sym.get(s, []) if b["close_ts"] <= mkt.days[-1] + DAY]
            if len(b4) < 60:
                continue
            H = sm.htf_arrays(b4)
            H["zones"] = [z for z in H["zones"] if z["kind"] in ("ob", "fvg")]
            self.H[s] = H
            self.conf[s] = [z["confirm_i"] for z in H["zones"]]
            cts = H["close_ts"]
            self.k_at[s] = [bisect.bisect_right(cts, t + DAY) - 1 for t in mkt.days]

    def limit_price(self, s, i, d, close, stop):
        """Nearest unmitigated zone edge in discount (d=+1) / premium (d=−1) between stop and close."""
        import smc
        H = self.H.get(s)
        if H is None:
            return None
        k = self.k_at[s][i]
        if k < 0 or H["lo"][k] is None or H["hi"][k] is None:
            return None
        eq = (H["lo"][k] + H["hi"][k]) / 2
        j = bisect.bisect_right(self.conf[s], k)
        best = None
        for z in H["zones"][max(0, j - 2000):j]:
            if z["confirm_i"] < k - smc.ZONE_MAX_AGE or (z["dir"] == "bull") != (d > 0):
                continue
            if smc.zone_state(z, k) != "unmitigated":
                continue
            if d > 0 and z["top"] <= eq and stop < z["top"] < close:
                best = z["top"] if best is None else max(best, z["top"])
            elif d < 0 and z["bottom"] >= eq and close < z["bottom"] < stop:
                best = z["bottom"] if best is None else min(best, z["bottom"])
        return best


class Trend:
    """A (and A+SMC with ``smc`` = 'market' | 'skip' fallback and ``zones`` = SmcZones)."""

    def __init__(self, mkt, ind, entry, n, ls, k, risk=0.01, cap=0.30, gross=2.0, allow=None,
                 smc=None, zones=None, wait=2):
        self.mkt, self.ind, self.entry, self.n, self.ls, self.k = mkt, ind, entry, n, ls, k
        self.risk, self.cap, self.gross, self.allow = risk, cap, gross, allow or _allow_all
        self.smc, self.zones, self.wait = smc, zones, wait
        self.st = {s: {"phase": "flat", "d": 0, "arm": {1: True, -1: True}} for s in mkt.syms}

    def _ref(self, s, i):
        if self.entry == "ema":
            e = self.ind.ema[s][self.n][i]
            return None if e is None else (e, e, e, e)
        hi, lo = self.ind.dchi[s][self.n][i], self.ind.dclo[s][self.n][i]
        xh, xl = self.ind.dchi[s][self.n // 2][i], self.ind.dclo[s][self.n // 2][i]
        return None if None in (hi, lo, xh, xl) else (hi, lo, xh, xl)

    def _sig(self, s, d, c, ref):
        if self.entry == "ema":
            return self.st[s]["arm"][d] and ((c > ref[0]) if d > 0 else (c < ref[0]))
        return (c > ref[0]) if d > 0 else (c < ref[1])

    def _exit(self, d, c, ref):
        if self.entry == "ema":
            return c < ref[0] if d > 0 else c > ref[0]
        return c < ref[3] if d > 0 else c > ref[2]

    def _trail(self, st, d, h, lo, a):
        if d > 0:
            st["ext"] = max(st["ext"], h)
            st["trail"] = max(st["trail"], st["ext"] - self.k * a)
        else:
            st["ext"] = min(st["ext"], lo)
            st["trail"] = min(st["trail"], st["ext"] + self.k * a)

    def decide(self, i, bk):
        mkt, ind = self.mkt, self.ind
        orders, stops, new = {}, {}, {}
        lo_ok, sh_ok = self.allow(i)
        ok = {1: lo_ok, -1: sh_ok}
        for s in mkt.syms:
            if not mkt.has(s, i):
                continue
            st = self.st[s]
            c, h, lo, a = mkt.c[s][i], mkt.h[s][i], mkt.l[s][i], ind.atr[s][i]
            ref = self._ref(s, i)
            if a is None or ref is None:
                continue
            if self.entry == "ema":
                if c <= ref[0]:
                    st["arm"][1] = True
                if c >= ref[0]:
                    st["arm"][-1] = True
            d = st["d"]
            if st["phase"] == "pos" and not bk.qty[s]:
                if bk.closed_today.get(s) == "stop":
                    st["arm"][d] = False
                st.update(phase="flat", d=0)
            elif st["phase"] == "pending":
                if bk.qty[s]:
                    st["phase"] = "pos"
                elif s in bk.closed_today:                  # limit filled and stopped the same day
                    st["arm"][d] = False
                    st.update(phase="flat", d=0)
            d = st["d"]
            if st["phase"] in ("pos", "pending"):
                self._trail(st, d, h, lo, a)
                broke = (c < st["trail"]) if d > 0 else (c > st["trail"])
                flip = self.ls and ok[-d] and self._sig(s, -d, c, ref)
                if st["phase"] == "pending":
                    if broke or self._exit(d, c, ref) or not ok[d]:
                        if broke:
                            st["arm"][d] = False
                        st.update(phase="flat", d=0)
                    elif i >= st["expires"]:
                        if self.smc == "market":
                            orders[s] = {"w": st["w"], "type": "market", "stop": st["trail"], "risk": st["rf"],
                                         "tag": "fallback"}
                            st["phase"] = "pos"
                        else:
                            st["arm"][d] = False
                            st.update(phase="flat", d=0)
                    else:
                        orders[s] = {"w": st["w"], "type": "limit", "price": st["px"], "stop": st["trail"],
                                     "risk": st["rf"], "tag": "smc"}
                    if st["phase"] != "flat" or not flip:
                        continue
                elif self._exit(d, c, ref) or not ok[d]:
                    orders[s] = {"w": 0.0, "tag": "regime" if not ok[d] else "opposite"}
                    st.update(phase="flat", d=0)
                    if not flip:
                        continue
                else:
                    stops[s] = st["trail"]
                    continue
            for d in ((1, -1) if self.ls else (1,)):
                if ok[d] and self._sig(s, d, c, ref):
                    rf = self.k * a / c
                    new[s] = (d, min(self.cap, self.risk / rf), rf, c - d * self.k * a, c, h, lo)
                    break
        scale = _gross_scale(bk, {s: v[1] for s, v in new.items()}, self.gross,
                             closing=[s for s, o in orders.items() if o["w"] == 0.0])
        for s, (d, w, rf, stop0, c, h, lo) in new.items():
            w = d * w * scale
            if abs(w) < 1e-6:
                continue
            st = self.st[s]
            st.update(d=d, ext=c, trail=stop0, w=w, rf=rf)
            px = None
            if self.smc and self.zones is not None:
                px = self.zones.limit_price(s, i, d, c, stop0)
            if px is not None:
                st.update(phase="pending", px=px, expires=i + self.wait)
                orders[s] = {"w": w, "type": "limit", "price": px, "stop": stop0, "risk": rf, "tag": "smc"}
            elif self.smc == "skip":
                st["arm"][d] = False
                st.update(phase="flat", d=0)
            else:
                st["phase"] = "pos"
                orders[s] = {"w": w, "type": "market", "stop": stop0, "risk": rf, "tag": "entry"}
        return orders, stops


def _rebalance(bk, targets, band=0.01, risk=None):
    """Market orders moving the book to ``targets`` (absent held coins → 0); |Δw| < band skipped."""
    orders = {}
    for s in sorted(set(targets) | {s for s, q in bk.qty.items() if q}):
        tgt, cur = targets.get(s, 0.0), bk.weight(s)
        if (tgt == 0.0 and cur != 0.0) or abs(tgt - cur) >= band:
            orders[s] = {"w": tgt, "type": "market", "risk": (risk or {}).get(s), "tag": "rebalance"}
    return orders


class Momentum:
    """B: weekly cross-sectional momentum (mode 'long' | 'abs' | 'ls'); ``seed`` → random ranks."""

    def __init__(self, mkt, ind, L, K, mode, allow=None, seed=None):
        self.mkt, self.ind, self.L, self.K, self.mode = mkt, ind, L, K, mode
        self.allow, self.seed, self.prev_ok = allow or _allow_all, seed, None

    def targets(self, i):
        mkt, ind, lb = self.mkt, self.ind, 7 * self.L
        rng = None if self.seed is None else crc_rng(self.seed, i)
        el = []
        for s in mkt.syms:
            if not mkt.has(s, i) or not mkt.has(s, i - lb) or (ind.nbars[s][i] or 0) < max(lb + 1, 60):
                continue
            sg = ind.sig[s][i]
            if not sg:
                continue
            r = mkt.c[s][i] / mkt.c[s][i - lb] - 1
            el.append((rng.random() if rng else r, r, s, sg))
        el.sort(key=lambda x: -x[0])
        lo_ok, sh_ok = self.allow(i)
        book = 0.5 if self.mode == "ls" else 1.0
        tg = {}
        top = el[:self.K]
        if top and lo_ok:
            inv = sum(1 / x[3] for x in top)
            for _, r, s, sg in top:
                if self.mode != "abs" or r > 0:
                    tg[s] = book * (1 / sg) / inv
        if self.mode == "ls" and sh_ok and len(el) >= 2 * self.K:
            bot = el[-self.K:]
            inv = sum(1 / x[3] for x in bot)
            for _, r, s, sg in bot:
                tg[s] = -book * (1 / sg) / inv
        return tg

    def decide(self, i, bk):
        ok = self.allow(i)
        changed = self.prev_ok is not None and ok != self.prev_ok
        self.prev_ok = ok
        if weekday(self.mkt.days[i]) != 6 and not changed:
            return {}, {}
        risk = {s: (self.ind.atr[s][i] or 0) / self.mkt.c[s][i] for s in self.mkt.syms if self.mkt.has(s, i)}
        return _rebalance(bk, self.targets(i), risk=risk), {}


def funding_z(mkt, W):
    """{sym: [z of the day's funding sum vs the last W days' sums (incl. today)]}."""
    fd, out = daily_funding(mkt), {}
    for s in mkt.syms:
        vals = [(i, x) for i, x in enumerate(fd[s]) if x is not None]
        z = [None] * len(mkt.days)
        xs = [x for _, x in vals]
        for j, (i, x) in enumerate(vals):
            if j >= W - 1:
                w = xs[j - W + 1:j + 1]
                sd = stx.stdev(w)
                if sd > 0:
                    z[i] = (x - sum(w) / W) / sd
        out[s] = z
    return out


class FundingTS:
    """D time-series: fade |z| > 2, hold H days, optional EMA50 trend filter."""

    def __init__(self, mkt, ind, z, H, trend, allow=None, thr=2.0, gross=1.5):
        self.mkt, self.ind, self.z, self.H, self.trend = mkt, ind, z, H, trend
        self.allow, self.thr, self.gross = allow or _allow_all, thr, gross
        self.entry_at = {}

    def decide(self, i, bk):
        mkt, orders, new = self.mkt, {}, {}
        ok = dict(zip((1, -1), self.allow(i)))
        for s in mkt.syms:
            if bk.qty[s]:
                if i >= self.entry_at.get(s, i) + self.H:
                    orders[s] = {"w": 0.0, "tag": "time"}
                continue
            z, sg, c = self.z[s][i], self.ind.sig[s][i], mkt.c[s][i]
            if z is None or not sg or abs(z) <= self.thr:
                continue
            d = -1 if z > 0 else 1
            e = self.ind.ema[s][50][i]
            if not ok[d] or (self.trend and (e is None or (c > e) != (d > 0))):
                continue
            new[s] = d * min(0.25, 0.005 / sg)
        scale = _gross_scale(bk, new, self.gross, closing=list(orders))
        for s, w in new.items():
            if abs(w * scale) > 1e-6:
                orders[s] = {"w": w * scale, "type": "market", "tag": "funding",
                             "risk": (self.ind.atr[s][i] or 0) / mkt.c[s][i]}
                self.entry_at[s] = i
        return orders, {}


class FundingXS:
    """D cross-sectional: every H days short the 3 highest funding z, long the 3 lowest."""

    def __init__(self, mkt, ind, z, H, n=3):
        self.mkt, self.ind, self.z, self.H, self.n = mkt, ind, z, H, n

    def decide(self, i, bk):
        if (self.mkt.days[i] // DAY) % self.H:
            return {}, {}
        el = sorted((self.z[s][i], s) for s in self.mkt.syms
                    if self.mkt.has(s, i) and self.z[s][i] is not None)
        tg = {}
        if len(el) >= 2 * self.n:
            for _, s in el[:self.n]:
                tg[s] = 0.5 / self.n
            for _, s in el[-self.n:]:
                tg[s] = -0.5 / self.n
        risk = {s: (self.ind.atr[s][i] or 0) / self.mkt.c[s][i] for s in tg}
        return _rebalance(bk, tg, risk=risk), {}


class Schedule:
    """Random-entry baseline: fixed (sym, fill_day, exit_day, signed weight) trades."""

    def __init__(self, trades):
        self.delta = {}
        for s, a, b, w in trades:
            self.delta.setdefault(a, []).append((s, w))
            self.delta.setdefault(b, []).append((s, -w))
        self.tgt = {}

    def decide(self, i, bk):
        orders = {}
        for s, dw in self.delta.get(i + 1, ()):
            self.tgt[s] = self.tgt.get(s, 0.0) + dw
            orders[s] = {"w": 0.0 if abs(self.tgt[s]) < 1e-9 else self.tgt[s], "type": "market", "tag": "random"}
        return orders, {}


class BuyHold:
    """Benchmarks: one coin bought once (``sym``) or equal weight rebalanced monthly (``sym=None``)."""

    def __init__(self, mkt, sym=None):
        self.mkt, self.sym, self.done = mkt, sym, False

    def decide(self, i, bk):
        if self.sym:
            if self.done or not self.mkt.has(self.sym, i):
                return {}, {}
            self.done = True
            return {self.sym: {"w": 1.0, "type": "market"}}, {}
        nxt = datetime.fromtimestamp((self.mkt.days[i] + DAY) / 1000, tz=UTC)
        if self.done and nxt.day != 1:
            return {}, {}
        self.done = True
        el = [s for s in self.mkt.syms if self.mkt.has(s, i)]
        return _rebalance(bk, {s: 1.0 / len(el) for s in el}, band=0.0), {}
