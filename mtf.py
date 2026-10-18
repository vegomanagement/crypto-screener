"""Multi-timeframe (MTF) ladder: fetch, per-TF analysis and cache (pure analysis + injected fetch).

``analyze_tf(candles, tf)`` reads ONE timeframe from its closed candles: trend/bias (EMA ribbon
with the chart's periods, EMA 200 and its slope, SMC external structure, HH/HL), SMC swing
structure (smc.py, external 5/5 + internal 2/2), the Wyckoff read, the volume profile and a
window VWAP, and the key levels. ``MtfService.ladder`` fetches the ladder in parallel with a
bounded pool and one total deadline, caching per (venue, symbol, tf, last closed bar) so an
unchanged timeframe is neither refetched nor recomputed. Reconciliation is in mtf_rules.py.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import signal_features as sf
import smc
import wyckoff

LADDER = ("5", "15", "60", "240", "D", "W")
TF_LABEL = {"1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m", "60": "1h", "120": "2h",
            "240": "4h", "D": "1D", "W": "1W"}
WEIGHT = {"5": 1.0, "15": 1.5, "30": 1.75, "60": 2.0, "120": 2.5, "240": 3.0, "D": 4.0, "W": 5.0}
CANDLES = 300          # same window as the chart/Wyckoff section (analyst.CANDLE_LIMIT)
PROFILE_BARS = 120
DEADLINE_S = 20
POOL = 4
BIAS_CLEAR = 3         # |bias score| ≥ 3 of 5 votes = a clear read


def tf_minutes(tf):
    return {"D": 1440, "W": 10080}.get(tf) or int(tf)


def ladder_for(interval, up=3, down=1):
    """The pane's interval, up to ``up`` higher ladder TFs and ``down`` lower ones (ascending)."""
    tfs = sorted(set(LADDER) | {interval}, key=tf_minutes)
    k = tfs.index(interval)
    return tfs[max(0, k - down):k + up + 1]


def ema_series(values, period):
    out, e, k = [None] * len(values), None, 2 / (period + 1)
    for i, v in enumerate(values):
        if i == period - 1:
            e = sum(values[:period]) / period
        elif i >= period:
            e += k * (v - e)
        out[i] = e
    return out


def _r(x):
    return None if x is None else float(f"{x:.10g}")


def _break(b):
    return None if not b else {k: b.get(k) for k in ("kind", "dir", "level", "ts", "confirm_ts", "close",
                                                     "initial", "swing_ts", "leg_price")}


def smc_view(candles, tf, ext=smc.EXTERNAL, internal=smc.INTERNAL):
    """Compact SMC read of one TF as of its last closed bar."""
    n, price = len(candles), candles[-1]["c"]
    e = smc.structure(candles, *ext, tf=tf)
    i = smc.structure(candles, *internal, tf=tf, zones=False)
    last = n - 1

    def zones(items):
        live = [dict({k: z[k] for k in ("dir", "ts", "top", "bottom", "confirm_ts", "rule")},
                     state=smc.zone_state(z, last)) for z in items]
        live = [z for z in live if z["state"] != "invalidated"]
        live.sort(key=lambda z: min(abs(price - z["top"]), abs(price - z["bottom"])))
        return live[:3]
    sw = [{k: s[k] for k in ("kind", "label", "price", "ts", "confirm_ts")} for s in e["swings"][-6:]]
    return {"tf": tf, "trend": {1: "bull", -1: "bear", 0: "none"}[e["trend"]],
            "internal_trend": {1: "bull", -1: "bear", 0: "none"}[i["trend"]],
            "last_break": _break(e["breaks"][-1] if e["breaks"] else None),
            "last_internal_break": _break(i["breaks"][-1] if i["breaks"] else None),
            "breaks": [_break(b) for b in e["breaks"][-4:]],
            "swings": sw, "sweeps": [{k: s[k] for k in ("side", "level", "price", "ts", "confirm_ts")}
                                     for s in (e["sweeps"] + i["sweeps"])[-3:]],
            "dealing_range": smc.dealing_range(candles, e["swings"]),
            "order_blocks": zones(e["obs"]), "fvgs": zones(e["fvgs"]),
            "params": {"external": list(ext), "internal": list(internal)},
            "_ext": e, "_int": i}


def hh_hl(sw):
    """+1 when the last two swing highs AND lows rise (HH+HL), −1 when both fall, else 0."""
    hs = [s["price"] for s in sw if s["kind"] == "H"][-2:]
    ls = [s["price"] for s in sw if s["kind"] == "L"][-2:]
    if len(hs) < 2 or len(ls) < 2:
        return 0
    return 1 if hs[1] > hs[0] and ls[1] > ls[0] else -1 if hs[1] < hs[0] and ls[1] < ls[0] else 0


def bias(candles, periods, smc_trend, sw, atr):
    """Five votes (±1/0): EMA ribbon stack, close vs EMA 200, EMA 200 slope over 10 bars
    (flat when |Δ| < 0.1 ATR), SMC external trend, HH/HL of the last two swings."""
    closes = [c["c"] for c in candles]
    ribbon = [ema_series(closes, p)[-1] for p in periods]
    e200 = ema_series(closes, 200)
    votes = {}
    if len(ribbon) >= 2 and None not in ribbon:
        votes["ema_stack"] = (1 if all(a > b for a, b in zip(ribbon, ribbon[1:]))
                              else -1 if all(a < b for a, b in zip(ribbon, ribbon[1:])) else 0)
    if e200[-1] is not None:
        votes["close_vs_ema200"] = 1 if closes[-1] > e200[-1] else -1
        if len(e200) > 10 and e200[-11] is not None and atr:
            dlt = e200[-1] - e200[-11]
            votes["ema200_slope"] = 0 if abs(dlt) < 0.1 * atr else (1 if dlt > 0 else -1)
    votes["smc_trend"] = smc_trend
    votes["hh_hl"] = hh_hl(sw)
    score = sum(votes.values())
    return {"score": score, "votes": votes, "max": len(votes),
            "bias": "bull" if score >= BIAS_CLEAR else "bear" if score <= -BIAS_CLEAR else "neutral",
            "clear": abs(score) >= BIAS_CLEAR, "ema_periods": list(periods),
            "ema": [_r(x) for x in ribbon], "ema200": _r(e200[-1]),
            "ema200_slope": None if len(e200) <= 10 or e200[-11] is None or e200[-1] is None
            else _r(e200[-1] - e200[-11]),
            "rule": f"5 votes (EMA {'/'.join(map(str, periods))} stack, close vs EMA 200, EMA 200 "
                    f"slope 10 bars, SMC external trend, HH/HL); |score| ≥ {BIAS_CLEAR} = clear"}


def wyckoff_view(candles):
    w = wyckoff.analyze(candles)
    ev = w["events"][-1] if w["events"] else None
    return {"status": w["status"], "schematic": w["schematic"], "phase": w["phase"],
            "confidence": w["confidence"], "tr": w["tr"], "creek": w["creek"], "ice": w["ice"],
            "reason": w["reason"],
            "events": [{k: e[k] for k in ("event", "price", "ts", "known_at")} for e in w["events"][-6:]],
            "last_event": None if not ev else {k: ev[k] for k in ("event", "price", "ts", "known_at")}}


def profile_view(candles):
    w = candles[-PROFILE_BARS:]
    p = sf.volume_profile([c["h"] for c in w], [c["l"] for c in w], [c["v"] for c in w])
    vol = sum(c["v"] for c in w)
    vwap = sum((c["h"] + c["l"] + c["c"]) / 3 * c["v"] for c in w) / vol if vol else None
    price = candles[-1]["c"]
    out = {"bars": len(w), "vwap": _r(vwap),
           "vwap_position": None if vwap is None else ("above" if price > vwap else "below")}
    if p:
        out.update(poc=_r(p["poc"]), vah=_r(p["vah"]), val=_r(p["val"]),
                   position="above" if price > p["vah"] else "below" if price < p["val"] else "inside")
    return out


def levels_of(tf, a):
    """Key levels of one TF: [{price, kind}] (kinds name the source)."""
    out = []

    def add(p, kind):
        if isinstance(p, (int, float)):
            out.append({"price": float(p), "kind": kind, "tf": tf})
    w = a.get("wyckoff") or {}
    if w.get("tr"):
        add(w["tr"].get("support"), "TR support")
        add(w["tr"].get("resistance"), "TR resistance")
    s = a.get("smc") or {}
    for sw in [x for x in s.get("swings", []) if x["kind"] == "H"][-1:]:
        add(sw["price"], f"swing high ({sw['label'] or 'first'})")
    for sw in [x for x in s.get("swings", []) if x["kind"] == "L"][-1:]:
        add(sw["price"], f"swing low ({sw['label'] or 'first'})")
    dr = s.get("dealing_range") or {}
    add(dr.get("eq"), "equilibrium")
    p = a.get("profile") or {}
    for k in ("poc", "vah", "val", "vwap"):
        add(p.get(k), k.upper())
    add((a.get("bias") or {}).get("ema200"), "EMA 200")
    for z in (s.get("order_blocks") or [])[:1]:
        if z["state"] == "unmitigated":
            add(z["top"] if z["dir"] == "bull" else z["bottom"], f"{z['dir']} OB edge")
    return out


def analyze_tf(candles, tf, periods=(7, 14, 21)):
    """Full read of one TF from its closed candles (raises on < 60 candles)."""
    if len(candles) < 60:
        raise ValueError(f"only {len(candles)} closed candles")
    from analyst import atr as atr14
    a = atr14(candles)
    s = smc_view(candles, tf)
    ext = s.pop("_ext")
    s.pop("_int")
    out = {"tf": tf, "label": TF_LABEL.get(tf, tf), "bars": len(candles), "ts": candles[-1]["close_ts"],
           "last_bar_ts": candles[-1]["ts"], "price": _r(candles[-1]["c"]), "atr14": _r(a),
           "smc": s, "bias": bias(candles, periods, ext["trend"], ext["swings"], a),
           "wyckoff": wyckoff_view(candles), "profile": profile_view(candles)}
    out["levels"] = levels_of(tf, out)
    return out


class MtfService:
    """Ladder fetch + per-TF cache. ``fetch(exchange, symbol, tf)`` returns a chart snapshot."""

    def __init__(self, fetch, clock=time.time, deadline_s=DEADLINE_S, pool=POOL):
        self.fetch, self.clock, self.deadline_s, self.pool = fetch, clock, deadline_s, pool
        self._cache, self._lock = {}, threading.Lock()
        self.stats = {"fetches": 0, "hits": 0}

    def _expected_close(self, tf):
        from chart_data import _bounds
        return _bounds(int(self.clock() * 1000), tf)[0]   # open of the forming bar = last close

    def one(self, exchange, symbol, tf, periods):
        key = (exchange, symbol, tf, tuple(periods))
        with self._lock:
            hit = self._cache.get(key)
        if hit and hit["ts"] >= self._expected_close(tf):
            self.stats["hits"] += 1
            return hit
        self.stats["fetches"] += 1
        chart = self.fetch(exchange, symbol, tf)
        candles = chart["candles"]
        if hit and candles and hit["ts"] == candles[-1]["close_ts"]:
            return hit                    # venue has not published a newer closed bar yet
        a = analyze_tf(candles, tf, periods)
        a["_candles"] = candles
        with self._lock:
            if len(self._cache) > 512:
                self._cache.clear()
            self._cache[key] = a
        return a

    def ladder(self, exchange, symbol, interval, periods=(7, 14, 21), up=3, down=1):
        tfs = ladder_for(interval, up, down)
        out, missing = {}, {}
        pool = ThreadPoolExecutor(max_workers=min(self.pool, len(tfs)))
        try:
            futs = {tf: pool.submit(self.one, exchange, symbol, tf, periods) for tf in tfs}
            deadline = time.monotonic() + self.deadline_s
            for tf, fut in futs.items():
                try:
                    out[tf] = fut.result(timeout=max(0.0, deadline - time.monotonic()))
                except FutureTimeout:
                    missing[tf] = f"timed out after {self.deadline_s}s"
                except Exception as exc:  # noqa: BLE001 — recorded, never invented
                    missing[tf] = f"{type(exc).__name__}: {exc}"[:160]
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return tfs, out, missing


_DEFAULT = {}
_DEFAULT_LOCK = threading.Lock()


def default_service():
    """One process-wide service over the analyst's chart provider (shared by /api/agent/mtf and
    the analyst snapshot, so both reuse the same per-TF cache)."""
    with _DEFAULT_LOCK:
        if "svc" not in _DEFAULT:
            def fetch(exchange, symbol, tf):
                from chart_data import build_chart_snapshot
                from exchange_markets import fetch_exchange_candles
                return build_chart_snapshot(symbol, tf, CANDLES,
                                            lambda s, i, n: fetch_exchange_candles(exchange, s, i, n),
                                            exchange=exchange)
            _DEFAULT["svc"] = MtfService(fetch)
        return _DEFAULT["svc"]


def build_block(service, exchange, symbol, interval, periods=(7, 14, 21)):
    import mtf_block
    tfs, reads, missing = service.ladder(exchange, symbol, interval, periods)
    if interval not in reads:
        raise ValueError(f"pane timeframe unavailable: {missing.get(interval, 'unknown')}")
    return mtf_block.build(tfs, reads, missing, interval)
