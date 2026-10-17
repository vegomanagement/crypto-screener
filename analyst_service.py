"""Analyst orchestration: snapshot → Hermes (or deterministic fallback) → validate → log.

One in-flight request per (exchange, symbol, interval), a 60 s result cache and a
global sliding-window rate limit on fresh analyses.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque

from analyst import (
    EVAL_HORIZON_BARS,
    build_snapshot,
    compute_split,
    disagreement,
    snapshot_hash,
)
from analyst_hermes import HermesUnavailable, ask_hermes

CACHE_TTL_S = 60
RATE_LIMIT = (6, 60)  # fresh analyses per seconds, all keys together
RAW_KEEP_CHARS = 6000


class RateLimited(RuntimeError):
    def __init__(self, retry_after):
        super().__init__("analyst rate limit reached")
        self.retry_after = retry_after


class SnapshotUnavailable(RuntimeError):
    pass


def _level(kind, price, reason, field):
    return {"type": kind, "price": float(price), "reason": reason, "source_fields": [field]}


def baseline_analysis(snapshot):
    """Deterministic analysis from the confluence baseline and snapshot levels only."""
    base = snapshot.get("baseline") or {}
    levels = []
    st = snapshot.get("structure") or {}
    if st.get("last_swing_high"):
        levels.append(_level("resistance", st["last_swing_high"]["price"], "last confirmed swing high",
                             "structure.last_swing_high.price"))
    if st.get("last_swing_low"):
        levels.append(_level("support", st["last_swing_low"]["price"], "last confirmed swing low",
                             "structure.last_swing_low.price"))
    price = (snapshot.get("price") or {}).get("value")
    pools = (snapshot.get("liquidity") or {}).get("pools") or []
    above = [(i, p) for i, p in enumerate(pools) if price and p["price"] > price]
    below = [(i, p) for i, p in enumerate(pools) if price and p["price"] < price]
    for group in (above[:1], below[:1]):
        for i, p in group:
            levels.append(_level("liquidity", p["price"], f"{p['kind']} pool ({p['side']})",
                                 f"liquidity.pools.{i}.price"))
    for i, z in enumerate(((snapshot.get("fvg") or {}).get("nearest") or [])[:1]):
        levels.append(_level("poi", (z["top"] + z["bottom"]) / 2,
                             f"midpoint of nearest {z['direction']} FVG", f"fvg.nearest.{i}"))
    summary = (f"Детерминированная оценка (без Hermes): confluence long {base.get('long_score')} "
               f"/ short {base.get('short_score')}.") if base else \
        "Детерминированная оценка недоступна: нет данных fetch_market."
    return {"agent_bias": None, "confidence": "low",
            "horizon": f"{EVAL_HORIZON_BARS} bars of {snapshot['interval']}",
            "levels": levels, "scenarios": [], "factor_scores": [],
            "risks": ["Baseline only: the language-model analyst did not answer."],
            "summary_ru": summary[:400]}


def apply_split(analysis, snapshot):
    """Attach the server-computed split (and the agent cross-check) to a validated analysis."""
    split = compute_split(analysis.get("factor_scores") or [], snapshot.get("baseline"))
    clash = disagreement(analysis.get("agent_bias"), split)
    return {**analysis, "bias": split["bias"], "long_pct": split["long_pct"],
            "short_pct": split["short_pct"], "split": split, "disagreement": clash}


class AnalystService:
    def __init__(self, providers, send, store=None, *, clock=time.time,
                 ttl=CACHE_TTL_S, rate=RATE_LIMIT, configured=True):
        self.providers, self.send, self.store = providers, send, store
        self.clock, self.ttl, self.rate, self.configured = clock, ttl, rate, configured
        self._cache, self._locks, self._calls = {}, {}, deque()
        self._lock = threading.Lock()

    def _cached(self, key):
        hit = self._cache.get(key)
        if hit and self.clock() - hit[0] < self.ttl:
            return {**hit[1], "cached": True, "age_s": round(self.clock() - hit[0], 1)}
        return None

    def _take_rate_slot(self):
        limit, window = self.rate
        now = self.clock()
        with self._lock:
            while self._calls and now - self._calls[0] >= window:
                self._calls.popleft()
            if len(self._calls) >= limit:
                raise RateLimited(max(1, int(window - (now - self._calls[0])) + 1))
            self._calls.append(now)

    def analyze(self, exchange, symbol, interval, *, force=False):
        key = (exchange, symbol, interval)
        with self._lock:
            if not force and (hit := self._cached(key)):
                return hit
            key_lock = self._locks.setdefault(key, threading.Lock())
        with key_lock:  # one in-flight analysis per key; waiters reuse its result
            with self._lock:
                hit = self._cached(key)
            if hit and (not force or hit["age_s"] < 5):
                return hit
            self._take_rate_slot()
            result = self._run(exchange, symbol, interval)
            with self._lock:
                self._cache[key] = (self.clock(), result)
            return {**result, "cached": False, "age_s": 0.0}

    def _run(self, exchange, symbol, interval):
        started = time.monotonic()
        try:
            snap = build_snapshot(exchange, symbol, interval, self.providers)
        except Exception as exc:  # noqa: BLE001
            raise SnapshotUnavailable(str(exc)[:200]) from None
        snapshot_ms = int((time.monotonic() - started) * 1000)
        warnings, hermes, status = [], None, "ok"
        if not self.configured:
            status = "hermes_unavailable"
            warnings.append("Hermes gateway not configured (HERMES_API_URL); baseline shown")
        else:
            try:
                hermes = ask_hermes(snap, self.send)
                warnings.extend(hermes["warnings"])
                if hermes["analysis"] is None:
                    status = "invalid_output"
            except HermesUnavailable as exc:
                status = "hermes_unavailable"
                warnings.append(f"Hermes unavailable: {exc}; baseline shown")
        analysis = apply_split(hermes["analysis"] if status == "ok" else baseline_analysis(snap), snap)
        if analysis["disagreement"]:
            warnings.append("disagreement: " + analysis["disagreement"]["text"])
        source = "hermes" if status == "ok" else "baseline"
        if snap.get("baseline") is None and source == "baseline":
            warnings.append("Deterministic baseline unavailable: " +
                            str(snap["missing"].get("baseline", "unknown")))
        result = {
            "status": status, "source": source, "exchange": exchange, "symbol": symbol,
            "interval": interval, "generated_at": snap["generated_at"],
            "price": snap["price"], "analysis": analysis, "warnings": warnings,
            "baseline": snap.get("baseline"),
            "model": hermes["model"] if hermes else None,
            "latency_ms": hermes["latency_ms"] if hermes else None,
            "attempts": hermes["attempts"] if hermes else 0,
            "snapshot_ms": snapshot_ms, "snapshot_hash": snapshot_hash(snap),
            "snapshot_bytes": len(json.dumps(snap, separators=(",", ":"), default=str)),
            "positioning_bytes": (len(json.dumps(snap["positioning"], separators=(",", ":"), default=str))
                                  if snap.get("positioning") else 0),
            "freshness": {"last_close_ts": snap["candles"]["last_close_ts"],
                          "sections": {k: (v or {}).get("ts") for k, v in snap.items()
                                       if isinstance(v, dict) and "source" in v},
                          "missing": snap["missing"],
                          "positioning_missing": (snap.get("positioning") or {}).get("missing")},
            "raw_output": [r[:RAW_KEEP_CHARS] for r in (hermes or {}).get("raw", [])],
            "evaluation": {"horizon_bars": EVAL_HORIZON_BARS,
                           "rule": "±1 ATR from the analysis price, bias side first"},
        }
        if self.store is not None:
            result["id"] = self.store.record({
                "ts": snap["generated_at"], "exchange": exchange, "symbol": symbol,
                "interval": interval, "price": snap["price"]["value"],
                "atr": (snap.get("indicators") or {}).get("atr14"),
                "bar_close_ts": snap["candles"]["last_close_ts"],
                "long_pct": analysis["long_pct"], "short_pct": analysis["short_pct"],
                "bias": analysis["bias"], "confidence": analysis["confidence"],
                "source": source, "status": status, "levels": analysis["levels"],
                "model": result["model"], "latency_ms": result["latency_ms"],
                "snapshot_hash": result["snapshot_hash"], "horizon_bars": EVAL_HORIZON_BARS,
                "p_long": analysis["split"]["p_long"], "net": analysis["split"]["net"],
                "factors": analysis["factor_scores"], "agent_bias": analysis["agent_bias"],
                "disagreement": (analysis["disagreement"] or {}).get("severity")})
        return result
