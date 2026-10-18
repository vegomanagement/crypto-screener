"""Historical replay of the analyst's deterministic baseline, as of one bar close.

Rebuilds the ``screener.fetch_market`` dict from closed candles only (every candle
must have closed at or before ``as_of_ms``), runs the production confluence +
decision code on it (``analyst.baseline_section`` → ``analyst.baseline_net``) and
scores the lean with the evaluator's rule (``analyst_store.evaluate_outcome``).

Inputs that the live market dict takes from live endpoints are reconstructed
from history where a public history exists and are otherwise left out (the
production functions then treat them as neutral) — see ``REPLAY_NOTES``.
No network access here: the data is passed in (scripts/backtest_analyst.py fetches it).
"""
from __future__ import annotations

import bisect
import math
from datetime import UTC, datetime

# Same windows as screener.fetch_market: 250 × 1h, 250 × 4h, 50 × 1D closed candles.
HTF_LIMITS = {"60": 250, "240": 250, "D": 50}
HTF_MS = {"60": 3_600_000, "240": 14_400_000, "D": 86_400_000}

REPLAY_NOTES = {
    "price": "close of the evaluated bar (live: Bybit last price at request time)",
    "klines": "Binance USDT-M closed 1h/4h/1D candles that closed at or before the bar close "
              "(live: Bybit-first closed candles)",
    "funding": "last SETTLED Binance funding rate at or before the bar close "
               "(live: Bybit predicted rate); trend from the last 8 settlements",
    "fear_greed": "alternative.me daily value whose day started at or before the bar close",
    "session/killzone/vwap": "computed for the bar close time",
    "left out (neutral in the scoring code)": "Bybit OI change, Hyperliquid book ratio, "
        "taker long/short ratio, liquidations, Deribit options, BTC correlation — no public "
        "history that covers the whole window",
}


class FutureDataError(ValueError):
    """A candle that closes after the as-of time reached the replay."""


_KEYS = {}


def _close_keys(candles):
    """close_ts list per candle list (cached: the replay bisects the same lists ~10^4 times)."""
    tag = (id(candles), len(candles), candles[-1]["close_ts"] if candles else None)
    if tag not in _KEYS:
        if len(_KEYS) > 64:
            _KEYS.clear()
        _KEYS[tag] = [c["close_ts"] for c in candles]
    return _KEYS[tag]


def closed_upto(candles, as_of_ms, limit):
    """Last ``limit`` candles with ``close_ts <= as_of_ms`` (candles sorted by ts)."""
    keys = _close_keys(candles)
    end = bisect.bisect_right(keys, as_of_ms)
    out = candles[max(0, end - limit):end]
    if out and out[-1]["close_ts"] > as_of_ms:  # defensive: unsorted input
        raise FutureDataError("candle closes after as_of")
    return out


def settled_upto(rows, as_of_ms, key="time"):
    """Rows (sorted by ``key``) with ``row[key] <= as_of_ms``."""
    keys = [r[key] for r in rows]
    return rows[:bisect.bisect_right(keys, as_of_ms)]


def funding_history(rates):
    """Same trend rule as screener._funding_history over the last 8 settled rates (oldest first)."""
    if len(rates) < 4:
        return {}
    pct = [r * 100 for r in rates[-8:]]
    half = len(pct) // 2
    diff = sum(pct[half:]) / (len(pct) - half) - sum(pct[:half]) / half
    trend = "rising" if diff > 0.001 else ("falling" if diff < -0.001 else "neutral")
    return {"rates": [round(r, 4) for r in pct], "current": round(pct[-1], 4),
            "trend": trend, "diff": round(diff, 4)}


def _fg_label(v):
    return ("Extreme Fear" if v < 25 else "Fear" if v < 45 else "Neutral" if v < 55
            else "Greed" if v < 75 else "Extreme Greed")


def historical_market(sc, as_of_ms, price, htf, *, funding=None, fear_greed=None):
    """fetch_market-shaped dict as of ``as_of_ms`` from candles that closed by then.

    ``sc`` is the screener module (its pure indicator functions are reused, so the replay
    scores exactly like production). ``htf`` maps "60"/"240"/"D" to sorted candle lists with
    ``close_ts``; ``funding`` is ``[{"time", "rate"}]`` settled rates, ``fear_greed``
    ``[{"time", "value"}]`` daily points. Anything later than ``as_of_ms`` is ignored.
    """
    k = {tf: closed_upto(htf.get(tf) or [], as_of_ms, n) for tf, n in HTF_LIMITS.items()}
    for tf, rows in k.items():
        if rows and rows[-1]["close_ts"] > as_of_ms:
            raise FutureDataError(f"{tf} candle closes after as_of")
    k1h, k4h, kD = k["60"], k["240"], k["D"]
    now = datetime.fromtimestamp(as_of_ms / 1000, tz=UTC)
    change_24h = 0.0
    if len(k1h) >= 25 and k1h[-25]["c"]:
        change_24h = (k1h[-1]["c"] / k1h[-25]["c"] - 1) * 100
    rates = [r["rate"] for r in settled_upto(funding or [], as_of_ms)]
    bybit = {"price": price, "change_24h": change_24h}
    if rates:
        bybit["funding"] = rates[-1]
    macro = {}
    fg = settled_upto(fear_greed or [], as_of_ms)
    if fg:
        v = int(fg[-1]["value"])
        macro = {"fg_value": v, "fg_label": _fg_label(v), "fg_icon": ""}
    return {
        "bybit": bybit, "hl": {}, "price": price, "change_24h": change_24h,
        "fr_divergence": 0.0, "fr_divergence_signal": False,
        "cvd": sc.compute_cvd(k1h), "vp": sc.compute_volume_profile(k1h),
        "ema_biases": sc.get_ema_biases(k1h, k4h, kD),
        "turtle_1h": sc.compute_turtle_zone(k1h), "turtle_4h": sc.compute_turtle_zone(k4h),
        "indicators": sc.compute_indicators(k1h), "vwap": sc.compute_vwap(k1h, now=now),
        "pivots": sc.compute_pivot_points(kD), "fr_history": funding_history(rates),
        "liquidity": {}, "ls_ratio": {}, "liquidations": {}, "options": {}, "macro": macro,
        "session": sc.get_session(now), "btc_corr": None, "ts": now,
        "_klines": {"60": k1h, "240": k4h, "D": kD},
    }


def baseline_at(sc, as_of_ms, price, htf, **kw):
    """(snapshot.baseline section, baseline_net under the current SPLIT_CONFIG raw rule)."""
    from analyst import baseline_section
    market = historical_market(sc, as_of_ms, price, htf, **kw)
    return baseline_section(market, sc.compute_confluence_score, as_of_ms)


NOT_REPLAYABLE = "not reconstructible as of the bar close (historical replay)"


def _not_replayable(*_args):
    raise ValueError(NOT_REPLAYABLE)


def historical_snapshot(sc, exchange, symbol, interval, chart_candles, as_of_ms, htf, *,
                        funding=None, fear_greed=None, ema_periods=None):
    """analyst.build_snapshot as of a past bar close, from data available at that close.

    Chart candles, indicators, structure, FVG, order blocks, session, liquidity/HTF/regime/
    flow and the baseline are rebuilt from candles closed by ``as_of_ms``; funding keeps
    only the last settled rate. Venue context (mark/index/OI), the order book, footprint
    and positioning have no reliable as-of history and are reported missing with a reason.
    """
    from analyst import CANDLE_LIMIT, build_snapshot
    from chart_data import build_chart_snapshot

    rows = [{**c, "source": exchange} for c in closed_upto(chart_candles, as_of_ms, CANDLE_LIMIT)]

    def chart(_exchange, sym, iv):
        return build_chart_snapshot(sym, iv, CANDLE_LIMIT, lambda s, i, n: rows[-n:],
                                    as_of_ms, exchange=exchange)

    def market(_sym):
        return historical_market(sc, as_of_ms, rows[-1]["c"], htf, funding=funding,
                                 fear_greed=fear_greed)

    settled = settled_upto(funding or [], as_of_ms)

    def funding_provider(_exchange, _sym):
        if not settled:
            raise ValueError(NOT_REPLAYABLE)
        last = settled[-1]
        return {"exchange": exchange, "as_of": as_of_ms,
                "last_settled": {"rate": repr(last["rate"]), "time": last["time"]},
                "predicted": None, "interval_hours": {"value": 8},
                "trend": None, "missing": {"predicted": "live estimate; " + NOT_REPLAYABLE,
                                           "cross_venue": NOT_REPLAYABLE}}

    providers = {"chart": chart, "market": market, "context": _not_replayable,
                 "depth": _not_replayable, "footprint": _not_replayable,
                 "funding": funding_provider, "confluence": sc.compute_confluence_score,
                 "positioning": None, "clock": lambda: as_of_ms}
    snap = build_snapshot(exchange, symbol, interval, providers, ema_periods=ema_periods)
    snap["missing"]["positioning"] = "positioning history: " + NOT_REPLAYABLE
    snap["replay"] = {"as_of": as_of_ms, "notes": REPLAY_NOTES}
    return snap


def first_touch(entry, atr_value, bars, horizon):
    """'up' / 'down' / 'ambiguous' / 'timeout' / None — evaluator rule seen from the long side."""
    from analyst_store import evaluate_outcome
    out = evaluate_outcome("long", entry, atr_value, bars, horizon)
    return {"win": "up", "loss": "down"}.get(out or "", out)


def wilson(k, n, z=1.96):
    """95% Wilson score interval for k successes in n trials; (None, None) when n == 0."""
    if n == 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return round(centre - half, 4), round(centre + half, 4)


BUCKETS = ((-1.01, -0.5), (-0.5, -0.2), (-0.2, -1e-9), (-1e-9, 1e-9), (1e-9, 0.2),
           (0.2, 0.5), (0.5, 1.01))


def bucket_label(lo, hi):
    if lo < 0 < hi:
        return "0 (no lean)"
    fmt = lambda x: "-1" if x < -1 else "+1" if x > 1 else ("0" if abs(x) < 1e-6 else f"{x:+.1f}")  # noqa: E731
    return f"[{fmt(lo)}, {fmt(hi)}]"


def summarize(rows, key="baseline_net"):
    """Per-bucket counts of first touches and the hit rate of the lean's direction.

    ``rows`` are dicts with ``key`` and ``touch``. hit = touch in the lean's direction;
    hit_rate_decided = hits / (up + down); hit_rate_all = hits / every scored row
    (the store's definition: ambiguous and timeout count as non-hits).
    """
    out = []
    for lo, hi in BUCKETS:
        items = [r for r in rows if r[key] is not None and lo <= r[key] < hi]
        up = sum(r["touch"] == "up" for r in items)
        down = sum(r["touch"] == "down" for r in items)
        amb = sum(r["touch"] == "ambiguous" for r in items)
        tmo = sum(r["touch"] == "timeout" for r in items)
        n = up + down + amb + tmo
        side = "long" if lo > 0 else "short" if hi < 0 else None
        hits = up if side == "long" else down if side == "short" else None
        rec = {"bucket": bucket_label(lo, hi), "lean": side, "n": n, "up_first": up,
               "down_first": down, "ambiguous": amb, "timeout": tmo,
               "p_up_first_decided": round(up / (up + down), 4) if up + down else None,
               "p_up_ci95": wilson(up, up + down)}
        if hits is not None:
            rec.update(hits=hits,
                       hit_rate_decided=round(hits / (up + down), 4) if up + down else None,
                       hit_ci95_decided=wilson(hits, up + down),
                       hit_rate_all=round(hits / n, 4) if n else None,
                       hit_ci95_all=wilson(hits, n))
        out.append(rec)
    return out
