"""Signal registry, HTF filter, funding events and outcome scoring (pure; see signal_features)."""
from __future__ import annotations

import bisect
import math

import signal_features as sf

# name → (long-side rule, needs delta, parameter grid). Grids are deliberately small: every
# combination is one tested variant and counts toward the multiple-testing correction.
RULES = {
    "sweep_reclaim": (sf._sweep, False, [{"N": 20}, {"N": 50}]),
    "sweep_delta": (sf._sweep, True, [{"N": 20, "delta": True}, {"N": 50, "delta": True}]),
    "failed_auction": (sf._failed_auction, False, [{"N": 20}, {"N": 50}]),
    "cvd_divergence": (sf._cvd_div, True, [{"N": 20, "tau": 0.0}, {"N": 20, "tau": 0.05},
                                           {"N": 50, "tau": 0.0}]),
    "absorption": (sf._absorption, True, [{"R": 1.5}, {"R": 2.0}]),
    "exhaustion": (sf._exhaustion, True, [{"f": 0.5}, {"f": 0.3}]),
    "vwap_reentry": (sf._vwap, False, [{"k": 2.0}, {"k": 2.5}]),
    "va_reentry": (sf._va, False, [{}]),
    "va_reentry_delta": (sf._va, True, [{"delta": True}]),
}
WYCKOFF = {"wyckoff_spring": ("Spring", "UTAD"), "wyckoff_sos": ("SOS", "SOW")}
FILTERS = ("none", "htf")
COOLDOWN = 12  # bars: a new event of the same signal within 12 bars of the last one is dropped


def rule_events(s, name, params):
    """[(i, +1|-1)] for a RULES signal: long rule on the series, short rule on the mirror."""
    fn, needs_delta, _grid = RULES[name]
    if needs_delta and not s["has_delta"]:
        return []
    out = [(i, 1) for i in fn(s, **params)] + [(i, -1) for i in fn(sf._neg(s), **params)]
    return sorted(out)


def wyckoff_events(candles, mirror_too=True):
    """{(code): [(known_i, dir)]} from wyckoff._chain over the whole series (causal: known_i)."""
    import wyckoff as wy
    out = {}
    for mirror in ((False, True) if mirror_too else (False,)):
        x = wy._Series(candles, mirror, wy.PARAMS)
        sign = -1 if mirror else 1
        for st in wy._chain(x, wy.PARAMS):
            for e in st["events"]:
                code = (wy.DIST_LABELS if mirror else wy.ACC_LABELS)[e["key"]][0]
                out.setdefault(code, []).append((e["known_i"], sign))
    return out


def funding_events(s, rates, q=0.05, window=270):
    """Contrarian events on the first bar whose last settled funding is in the q-tail."""
    times = [r["time"] for r in rates]
    out, prev_state = [], 0
    for i, t in enumerate(s["close_ts"]):
        k = bisect.bisect_right(times, t) - 1
        state = 0
        if k >= window:
            hist = sorted(r["rate"] for r in rates[k - window:k])
            lo, hi = hist[int(q * window)], hist[int((1 - q) * window) - 1]
            r = rates[k]["rate"]
            state = 0 if lo >= hi else 1 if r <= lo else -1 if r >= hi else 0
        if state and state != prev_state:
            out.append((i, state))
        prev_state = state
    return out


def htf_bias(s, htf_ms=14_400_000, period=50, slope=6):
    """Per-bar 4h bias (+1/−1/0) from completed 4h bars built out of this series."""
    closes, ends, cur = [], [], None
    for i, ts in enumerate(s["ts"]):
        start = ts // htf_ms * htf_ms
        if cur is not None and start != cur:
            closes.append(s["c"][i - 1])
            ends.append(cur + htf_ms)
        cur = start
    ema, k, vals = None, 2 / (period + 1), []
    for j, c in enumerate(closes):
        ema = (sum(closes[:period]) / period if j == period - 1 else
               None if j < period - 1 else ema + k * (c - ema))
        vals.append(ema)
    bias = [0] * len(s["c"])
    for i, t in enumerate(s["close_ts"]):
        j = bisect.bisect_right(ends, t) - 1
        if j >= period + slope and vals[j] is not None and vals[j - slope] is not None:
            up, dn = closes[j] > vals[j] and vals[j] > vals[j - slope], \
                closes[j] < vals[j] and vals[j] < vals[j - slope]
            bias[i] = 1 if up else -1 if dn else 0
    return bias


def apply(events, bias=None, cooldown=COOLDOWN):
    """Optional HTF filter, then the per-signal cooldown (keeps the first of a cluster)."""
    out, last = [], -10**9
    for i, d in events:
        if bias is not None and bias[i] != d:
            continue
        if i - last < cooldown:
            continue
        out.append((i, d))
        last = i
    return out


def score(s, i, d, horizon_atr=12, horizon_rr=48, targets=(1.5, 2.0), fee=sf.FEE_PER_SIDE):
    """Outcomes of one event entered at c[i] with risk a = atr[i] (None if not scorable)."""
    a, entry, n = s["atr"][i], s["c"][i], len(s["c"])
    if not a or a <= 0 or i + horizon_rr >= n:
        return None
    up, dn = entry + a, entry - a
    rule = "timeout"
    for k in range(i + 1, i + 1 + horizon_atr):
        hu, hd = s["h"][k] >= up, s["l"][k] <= dn
        if hu and hd:
            rule = "ambiguous"
            break
        if hu or hd:
            rule = "win" if (hu and d > 0) or (hd and d < 0) else "loss"
            break
    out = {"atr_rule": rule, "fee_r": 2 * fee * entry / a}
    for t in targets:
        tgt, stop, r = entry + d * t * a, entry - d * a, None
        for k in range(i + 1, i + 1 + horizon_rr):
            hit_t = s["h"][k] >= tgt if d > 0 else s["l"][k] <= tgt
            hit_s = s["l"][k] <= stop if d > 0 else s["h"][k] >= stop
            if hit_s:          # stop first when both fall in one bar (conservative)
                r = -1.0
                break
            if hit_t:
                r = t
                break
        if r is None:
            r = d * (s["c"][i + horizon_rr] - entry) / a
        out[f"r{t:g}"] = r
    return out


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(mid - half, 4), round(mid + half, 4)]


def z_for(alpha):
    """Two-sided normal quantile for ``alpha`` (bisection on erfc; stdlib only)."""
    lo, hi = 0.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if math.erfc(mid / math.sqrt(2)) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def summarize(outcomes, key="r1.5", net=True, z=1.96):
    """n, ±1 ATR hit rates (decided and all) with Wilson CIs, expectancy in R with a normal CI."""
    n = len(outcomes)
    if not n:
        return {"n": 0}
    wins = sum(o["atr_rule"] == "win" for o in outcomes)
    losses = sum(o["atr_rule"] == "loss" for o in outcomes)
    rs = [o[key] - (o["fee_r"] if net else 0.0) for o in outcomes]
    mean = sum(rs) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rs) / (n - 1)) if n > 1 else 0.0
    half = z * sd / math.sqrt(n) if n > 1 else None
    rr_wins = sum(o[key] > 0 for o in outcomes)
    return {"n": n, "hit_rate_decided": round(wins / (wins + losses), 4) if wins + losses else None,
            "hit_ci95_decided": wilson(wins, wins + losses), "hit_rate_all": round(wins / n, 4),
            "rr_win_rate": round(rr_wins / n, 4), "rr_win_ci95": wilson(rr_wins, n),
            "expectancy_r": round(mean, 4),
            "expectancy_ci": None if half is None else [round(mean - half, 4), round(mean + half, 4)],
            "mean_fee_r": round(sum(o["fee_r"] for o in outcomes) / n, 4)}
