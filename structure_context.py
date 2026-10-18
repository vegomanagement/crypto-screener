"""Wyckoff + order-flow context blocks for the analyst snapshot and GET /api/agent/structure.

Pure: takes already-fetched chart candles (and optionally the footprint payload). Signals are
DESCRIPTIVE: the event backtest (scripts/backtest_signals.py, docs/WYCKOFF_SIGNALS_RESULT.md)
found no candidate with a robust out-of-sample edge, so every signal carries
``edge: "none proven"`` and its measured out-of-sample stats from ``signal_stats.json``.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import signal_eval as se
import signal_features as sf
import wyckoff

STATS_PATH = Path(__file__).with_name("signal_stats.json")
STATS_TF = {"5": "5m", "15": "15m", "60": "1h"}
RECENT = 24          # bars: events this recent are listed
MAX_EVENTS = 12
# Live variants: the simplest parameter set of each candidate, no HTF filter (the chart window
# is too short for the 4h EMA 50). Signals needing per-bar taker delta are listed as unavailable.
LIVE = (("sweep_reclaim", {"N": 20}), ("failed_auction", {"N": 20}), ("vwap_reentry", {"k": 2.0}),
        ("va_reentry", {}), ("wyckoff_spring", {}), ("wyckoff_sos", {}))
NEEDS_DELTA = ("sweep_delta", "cvd_divergence", "absorption", "exhaustion", "va_reentry_delta")


def _r(x, d=8):
    return None if x is None else float(f"{x:.{d}g}")


@lru_cache(maxsize=1)
def load_stats():
    try:
        return json.loads(STATS_PATH.read_text())
    except (OSError, ValueError):
        return None


def merge_footprint_delta(candles, footprint):
    """Copy the footprint's per-bar delta onto matching closed candles (by open time)."""
    bars = {b.get("open_ts", b.get("open_time")): b.get("delta") for b in
            ((footprint or {}).get("bars") or []) if not b.get("forming")}
    out, used = [], 0
    for c in candles:
        d = bars.get(c["ts"])
        if d is not None:
            out.append({**c, "delta": float(d)})
            used += 1
        else:
            out.append(c)
    return out, used


def wyckoff_section(candles, footprint=None):
    merged, used = merge_footprint_delta(candles, footprint)
    w = wyckoff.analyze(merged)
    w["events"] = w["events"][-MAX_EVENTS:]
    w.pop("params", None)
    w["source"] = "wyckoff.py over the chart's closed candles (volume, spread, close position" + (
        f"; footprint delta on the last {used} bars)" if used else "; no per-bar delta)")
    w["delta_bars"] = used
    w["note"] = "deterministic rules; 'unclear' is reported rather than forcing a schematic"
    return w


def _stat(tf, name, params):
    stats = load_stats()
    if not stats or tf is None:
        return None
    return (stats.get("variants") or {}).get(f"{tf}|{json.dumps([name, params, 'none'])}")


def signals_section(candles, interval):
    """Candidate events of the last RECENT bars with entry/stop/target and their OOS stats."""
    if len(candles) < 60:
        raise ValueError(f"only {len(candles)} candles")
    s = sf.prepare(candles)
    n, tf = len(candles), STATS_TF.get(str(interval))
    wy = se.wyckoff_events(candles)
    events = []
    for name, params in LIVE:
        if name in se.WYCKOFF:
            lc, sc = se.WYCKOFF[name]
            evs = sorted([(i, 1) for i, _ in wy.get(lc, [])] + [(i, -1) for i, _ in wy.get(sc, [])])
        else:
            evs = se.rule_events(s, name, params)
        for i, d in se.apply(evs):
            a = s["atr"][i]
            if i < n - RECENT or not a:
                continue
            entry = s["c"][i]
            events.append({"signal": name, "params": params, "direction": "long" if d > 0 else "short",
                           "ts": s["ts"][i], "known_at": s["close_ts"][i], "bars_ago": n - 1 - i,
                           "entry": _r(entry), "stop": _r(entry - d * a), "target": _r(entry + d * 1.5 * a),
                           "atr": _r(a), "stats": _stat(tf, name, params), "edge": "none proven"})
    events.sort(key=lambda e: e["bars_ago"])
    prof = sf.volume_profile([c["h"] for c in candles[-120:]], [c["l"] for c in candles[-120:]],
                             [c["v"] for c in candles[-120:]])
    vw, sg, cnt = sf.session_vwap(s)
    stats = load_stats() or {}
    price = s["c"][-1]
    return {
        "source": "signal_features.py over the chart's closed candles; stats: "
                  "scripts/backtest_signals.py (Binance BTC/ETH/SOL, last 30% out-of-sample)",
        "ts": candles[-1]["close_ts"], "edge": "none proven",
        "verdict": "no candidate passed the out-of-sample test after fees and multiple-testing "
                   "correction; events are context, not trade signals",
        "passed": stats.get("passed") or [], "stats_timeframe": tf,
        "stats_note": None if tf else f"not backtested on interval {interval} (only 5m/15m/1h)",
        "rule": stats.get("rule"), "stats_generated": stats.get("generated"),
        "events": events[:MAX_EVENTS],
        "unavailable": {k: "needs per-bar taker delta; chart candles carry none" for k in NEEDS_DELTA},
        "volume_profile": None if not prof else {
            "bars": min(120, n), "poc": _r(prof["poc"]), "vah": _r(prof["vah"]), "val": _r(prof["val"]),
            "hvn": [_r(x) for x in sorted(prof["hvn"], key=lambda x: abs(x - price))[:3]],
            "lvn": [_r(x) for x in sorted(prof["lvn"], key=lambda x: abs(x - price))[:3]],
            "price_vs_value": "above" if price > prof["vah"] else "below" if price < prof["val"] else "inside"},
        "session_vwap": None if vw[-1] is None else {
            "vwap": _r(vw[-1]), "sigma": _r(sg[-1]), "bars_into_session": cnt[-1],
            "upper_2s": _r(vw[-1] + 2 * sg[-1]), "lower_2s": _r(vw[-1] - 2 * sg[-1]),
            "z": _r((price - vw[-1]) / sg[-1], 4) if sg[-1] else None,
            "session": "UTC day"},
    }


def structure_block(candles, interval, footprint=None):
    """Both blocks, each None + reason on failure (never raises)."""
    out, missing = {}, {}
    for key, fn, args in (("wyckoff", wyckoff_section, (candles, footprint)),
                          ("signals", signals_section, (candles, interval))):
        try:
            out[key] = fn(*args)
        except Exception as exc:  # noqa: BLE001 — recorded, never invented
            out[key] = None
            missing[key] = f"{type(exc).__name__}: {exc}"[:160]
    out["missing"] = missing
    return out
