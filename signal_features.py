"""Order-flow and structure features as causal per-bar arrays and EVENT signals (pure, stdlib).

Every value at bar ``i`` uses bars ``0..i`` only (the bar's close is the decision time);
an event at ``i`` is entered at ``c[i]`` with ``atr[i]`` as the risk unit.

Delta: ``d[i]`` = taker-buy volume − taker-sell volume of the bar. In the backtest it comes
from Binance kline field 9 (taker buy base volume): d = 2·tb − v. That is exact per bar but
has no price levels; stacked imbalances need trades per price (see scripts/backtest_signals.py
``footprint`` sample). Signals that need delta are skipped when it is missing.

Definitions (direction shown for the long side; short is the mirror image):
  sweep_reclaim   l[i] < min(l[i-N..i-1]) and c[i] > that low and clv[i] ≥ 0.5
                  (liquidity grab under the prior N-bar low, closed back above it)
  sweep_delta     sweep_reclaim and d[i] > 0 (buyers were the aggressors on the reclaim)
  cvd_divergence  c[i] is the lowest close of N+1 bars while Σd over the last N bars
                  ≥ τ·Σv (price at a new low, cumulative delta net positive)
  absorption      prior 10-bar change ≤ −1.5 ATR, rv[i] ≥ R (effort), |c−o| ≤ 0.3 ATR (no
                  result), d[i]/v[i] ≤ −0.1 (aggressive selling) yet clv[i] ≥ 0.4
  exhaustion      c[i] lowest close of 21 bars after 3 down closes, rv[i] < 1 and
                  |d[i]/v[i]| < f·mean|d/v| of the 3 previous bars (selling dried up)
  failed_auction  l[i] < min(l[i-N..i-1]) with lower wick ≥ 60% of the range and
                  clv[i] ≥ 0.65 (excess / rejection tail at the range extreme)
  vwap_reentry    UTC-day session VWAP ± k·σ (volume-weighted σ of typical price),
                  ≥ 6 bars into the session; c[i-1] < VWAP − kσ and c[i] ≥ VWAP − kσ
  va_reentry      volume profile of the 120 bars ending at the last multiple of 20 bars
                  (bins of 1/40 of the range, 70% value area); c[i-1] < VAL ≤ c[i]
  va_reentry_delta  va_reentry and d[i] > 0
  wyckoff_spring  wyckoff.py Spring (long) / UTAD (short) at the bar where the rule fired
  wyckoff_sos     wyckoff.py SOS (long) / SOW (short)
  funding_extreme last settled funding ≤ the q-quantile of the previous 270 settlements
                  (≈90 days) — contrarian long (≥ 1−q → short); fires on the first bar of the run
Filter ``htf``: keep only events in the direction of the 4h bias (last closed 4h close vs its
EMA 50, and EMA 50 vs 6 bars earlier; both agreeing, else no bias → event dropped).
"""
from __future__ import annotations

import math
from collections import deque

FEE_PER_SIDE = 0.0005  # Binance USDT-M VIP0 taker fee


def atr(h, low, c, period=14):
    n, out = len(c), [None] * len(c)
    if n <= period:
        return out
    tr = [h[0] - low[0]] + [max(h[i] - low[i], abs(h[i] - c[i - 1]), abs(low[i] - c[i - 1]))
                            for i in range(1, n)]
    value = sum(tr[1:period + 1]) / period
    out[period] = value
    for i in range(period + 1, n):
        value = (value * (period - 1) + tr[i]) / period
        out[i] = value
    return out


def rolling_prev(values, n, fn):
    """out[i] = fn(values[i-n..i-1]) (min or max) via a monotonic deque; None while i < n."""
    out, dq = [None] * len(values), deque()
    better = (lambda a, b: a <= b) if fn is min else (lambda a, b: a >= b)
    for i, value in enumerate(values):
        while dq and dq[0] < i - n:
            dq.popleft()
        if i >= n:
            out[i] = values[dq[0]]
        while dq and better(value, values[dq[-1]]):
            dq.pop()
        dq.append(i)
    return out


def prepare(candles):
    s = {k: [b[k] for b in candles] for k in ("ts", "o", "h", "l", "c", "v")}
    s["close_ts"] = [b.get("close_ts", b["ts"]) for b in candles]
    s["d"] = [b.get("delta") for b in candles]
    s["has_delta"] = all(x is not None for x in s["d"]) and bool(candles)
    s["atr"] = atr(s["h"], s["l"], s["c"])
    n, v = len(candles), s["v"]
    rv, run = [None] * n, sum(v[:20])
    for i in range(20, n):
        rv[i] = v[i] / (run / 20) if run > 0 else None
        run += v[i] - v[i - 20]
    s["rv"] = rv
    s["clv"] = [0.5 if h == lo else (c - lo) / (h - lo) for h, lo, c in zip(s["h"], s["l"], s["c"])]
    s["_cache"] = {}
    return s


def _prev(s, key, n, fn):
    tag = (key, n, fn.__name__)
    if tag not in s["_cache"]:
        s["_cache"][tag] = rolling_prev(s[key], n, fn)
    return s["_cache"][tag]


def _neg(s):
    """Price-mirrored view (h↔−l, c→−c, d→−d) so every rule is written once for longs."""
    if "_neg" not in s:
        m = {"ts": s["ts"], "close_ts": s["close_ts"], "v": s["v"], "atr": s["atr"], "rv": s["rv"],
             "o": [-x for x in s["o"]], "c": [-x for x in s["c"]], "h": [-x for x in s["l"]],
             "l": [-x for x in s["h"]], "d": [None if x is None else -x for x in s["d"]],
             "has_delta": s["has_delta"], "clv": [1 - x for x in s["clv"]], "_cache": {}}
        s["_neg"] = m
    return s["_neg"]


# ── long-side rules; each yields bar indexes ────────────────────────────────

def _sweep(s, N=20, delta=False, **_):
    low = _prev(s, "l", N, min)
    for i in range(N, len(s["c"])):
        if s["l"][i] < low[i] < s["c"][i] and s["clv"][i] >= 0.5 and \
                (not delta or (s["d"][i] or 0) > 0):
            yield i


def _failed_auction(s, N=20, **_):
    low = _prev(s, "l", N, min)
    for i in range(N, len(s["c"])):
        rng = s["h"][i] - s["l"][i]
        if rng > 0 and s["l"][i] < low[i] and \
                (min(s["o"][i], s["c"][i]) - s["l"][i]) >= 0.6 * rng and s["clv"][i] >= 0.65:
            yield i


def _cvd_div(s, N=20, tau=0.0, **_):
    c, d, v = s["c"], s["d"], s["v"]
    low = _prev(s, "c", N, min)
    sd = sv = 0.0
    for i in range(len(c)):
        sd += d[i]
        sv += v[i]
        if i >= N:
            sd -= d[i - N]
            sv -= v[i - N]
            if c[i] < low[i] and sv > 0 and sd >= tau * sv:
                yield i


def _absorption(s, R=2.0, **_):
    c, a = s["c"], s["atr"]
    for i in range(11, len(c)):
        ai, rv = a[i - 1], s["rv"][i]
        if not ai or rv is None or rv < R or not s["v"][i]:
            continue
        if c[i - 1] - c[i - 11] <= -1.5 * ai and abs(c[i] - s["o"][i]) <= 0.3 * ai \
                and s["d"][i] / s["v"][i] <= -0.1 and s["clv"][i] >= 0.4:
            yield i


def _exhaustion(s, f=0.5, **_):
    c, d, v = s["c"], s["d"], s["v"]
    low = _prev(s, "c", 20, min)
    for i in range(23, len(c)):
        if not (c[i] < low[i] and c[i] < c[i - 1] < c[i - 2] < c[i - 3] and (s["rv"][i] or 9) < 1):
            continue
        prev = [abs(d[k]) / v[k] for k in (i - 1, i - 2, i - 3) if v[k]]
        if v[i] and prev and abs(d[i]) / v[i] < f * sum(prev) / len(prev):
            yield i


def session_vwap(s):
    """(vwap, sigma, bars_into_session) per bar for UTC-day sessions."""
    n = len(s["c"])
    vw, sg, k = [None] * n, [None] * n, [0] * n
    day, pv = None, None
    for i in range(n):
        dday = s["ts"][i] // 86_400_000
        if dday != day:
            day, pv, p2v, vol, cnt = dday, 0.0, 0.0, 0.0, 0
        tp = (s["h"][i] + s["l"][i] + s["c"][i]) / 3
        pv += tp * s["v"][i]
        p2v += tp * tp * s["v"][i]
        vol += s["v"][i]
        cnt += 1
        if vol > 0:
            vw[i] = pv / vol
            sg[i] = math.sqrt(max(0.0, p2v / vol - vw[i] ** 2))
        k[i] = cnt
    return vw, sg, k


def _vwap(s, k=2.0, **_):
    if "_vwap" not in s:
        s["_vwap"] = session_vwap(s)
    vw, sg, cnt = s["_vwap"]
    for i in range(1, len(s["c"])):
        if cnt[i] >= 6 and cnt[i - 1] >= 5 and vw[i] and sg[i] and vw[i - 1] and sg[i - 1]:
            if s["c"][i - 1] < vw[i - 1] - k * sg[i - 1] and s["c"][i] >= vw[i] - k * sg[i]:
                yield i


def volume_profile(h, low, v, d=None, bins=40, va=0.7):
    """POC / VAH / VAL / HVN / LVN (+ delta per bin) from bars, volume spread evenly over each range."""
    top, bot = max(h), min(low)
    if top <= bot:
        return None
    step = (top - bot) / bins
    vol, dl = [0.0] * bins, [0.0] * bins
    for k in range(len(h)):
        lo_b = min(bins - 1, int((low[k] - bot) / step))
        hi_b = min(bins - 1, int((h[k] - bot) / step))
        share = v[k] / (hi_b - lo_b + 1)
        dshare = 0.0 if d is None or d[k] is None else d[k] / (hi_b - lo_b + 1)
        for b in range(lo_b, hi_b + 1):
            vol[b] += share
            dl[b] += dshare
    poc = max(range(bins), key=lambda b: vol[b])
    total, lo_i, hi_i, acc = sum(vol), poc, poc, vol[poc]
    while acc < va * total and (lo_i > 0 or hi_i < bins - 1):
        up = vol[hi_i + 1] if hi_i < bins - 1 else -1
        dn = vol[lo_i - 1] if lo_i > 0 else -1
        if up >= dn:
            hi_i += 1
            acc += up
        else:
            lo_i -= 1
            acc += dn
    mid = lambda b: bot + (b + 0.5) * step  # noqa: E731
    mean = total / bins
    peaks = [b for b in range(1, bins - 1) if vol[b] >= vol[b - 1] and vol[b] >= vol[b + 1]]
    troughs = [b for b in range(1, bins - 1) if vol[b] <= vol[b - 1] and vol[b] <= vol[b + 1]]
    return {"poc": mid(poc), "vah": bot + (hi_i + 1) * step, "val": bot + lo_i * step,
            "step": step, "low": bot, "high": top,
            "hvn": [mid(b) for b in peaks if vol[b] >= 1.5 * mean],
            "lvn": [mid(b) for b in troughs if vol[b] <= 0.5 * mean and lo_i <= b <= hi_i],
            "delta_poc": None if d is None else mid(max(range(bins), key=lambda b: abs(dl[b]))),
            "bins": [{"price": mid(b), "volume": vol[b], "delta": None if d is None else dl[b]}
                     for b in range(bins)]}


def _profiles(s, window=120, stride=20):
    """val/vah per bar from the profile of the ``window`` bars ending at the last stride point."""
    if "_va" not in s:
        n = len(s["c"])
        val, vah = [None] * n, [None] * n
        cur = None
        for i in range(n):
            if cur is not None:
                val[i], vah[i] = cur
            if i + 1 >= window and (i + 1) % stride == 0:
                p = volume_profile(s["h"][i - window + 1:i + 1], s["l"][i - window + 1:i + 1],
                                   s["v"][i - window + 1:i + 1])
                cur = None if p is None else (p["val"], p["vah"])
        s["_va"] = (val, vah)
    return s["_va"]


def _va(s, delta=False, **_):
    val, _vah = _profiles(s)
    c = s["c"]
    for i in range(1, len(c)):
        if val[i] is not None and val[i - 1] is not None and c[i - 1] < val[i - 1] <= c[i] \
                and (not delta or (s["d"][i] or 0) > 0):
            yield i
