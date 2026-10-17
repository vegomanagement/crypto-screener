"""Hermes gateway client, prompt and strict output validation for the chart analyst.

The gateway is OpenAI-compatible (``POST {HERMES_API_URL}/chat/completions``).
URL and key are read from the environment only and never logged or returned.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.request

from analyst import price_bounds, resolve_path

ANCHORED_TYPES = ("support", "resistance", "liquidity", "poi")


def _lookup(snapshot, path):
    node = snapshot
    for part in path.replace("[", ".").replace("]", "").split("."):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def anchored(snapshot, price, sources):
    """True when a cited numeric value is within 0.25 ATR of the level price (or none is numeric)."""
    tol = max(0.25 * ((snapshot.get("indicators") or {}).get("atr14") or 0), abs(price) * 1e-6)
    values = [v for v in (_lookup(snapshot, s) for s in sources)
              if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return not values or any(abs(v - price) <= tol for v in values)

TIMEOUT_S = 60
DEFAULT_MODEL = "hermes-agent"
BIASES = ("long", "short", "neutral")
CONFIDENCES = ("low", "medium", "high")
LEVEL_TYPES = ("support", "resistance", "entry", "stop", "target", "liquidity", "poi")
MAX_LEVELS, MAX_SCENARIOS, MAX_FACTORS, MAX_RISKS = 12, 4, 8, 6


class HermesUnavailable(RuntimeError):
    """Gateway not configured, unreachable, timed out or returned a non-200."""


SYSTEM_PROMPT = (
    "You are a crypto chart analyst inside a read-only research screener. "
    "Use ONLY the JSON snapshot the user sends. Do not call tools, do not browse, do not "
    "use memory or outside knowledge of current prices. If a section is null, it is missing "
    "(see snapshot.missing) and must not be guessed. Reply with ONE strict JSON object and "
    "nothing else: no prose, no markdown fences."
)

SCHEMA_TEXT = """Return exactly this JSON shape:
{"bias":"long|short|neutral","long_pct":<int>,"short_pct":<int>,
 "confidence":"low|medium|high","horizon":"<e.g. 4-12 bars of 15m>",
 "levels":[{"type":"support|resistance|entry|stop|target|liquidity|poi","price":<number>,
            "reason":"<short>","source_fields":["<dotted path in snapshot>"]}],
 "scenarios":[{"name":"<short>","trigger":"<text>","target":"<text>","invalidation":"<text>"}],
 "key_factors":[{"text":"<short>","source_fields":["<dotted path>"]}],
 "risks":["<short>"],
 "summary_ru":"<Russian, at most 400 characters>"}
Rules: long_pct and short_pct are integers 0-100 that sum to 100 (your estimate, not a
calibrated probability). Every source_fields entry must be an existing dotted path in the
snapshot, e.g. "indicators.rsi14", "structure.last_swing_high.price", "fvg.nearest.0.top",
"liquidity.pools.2.price", "orderbook.imbalance". Level prices must come from snapshot
values or be derived from them (within the candle window +/- 3 ATR). At most 12 levels,
4 scenarios, 8 key_factors, 6 risks."""


def build_messages(snapshot):
    data = json.dumps(snapshot, separators=(",", ":"), default=str)
    user = (f"Analyse {snapshot['exchange']} {snapshot['symbol']} interval {snapshot['interval']} "
            f"(minutes unless D/W/M). Outcomes are later scored over "
            f"{snapshot.get('evaluation_horizon_bars')} bars by whether price moves 1 ATR in your "
            f"bias direction before 1 ATR against.\n{SCHEMA_TEXT}\nSNAPSHOT:\n{data}")
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def config_from_env(env=None):
    env = os.environ if env is None else env
    url = (env.get("HERMES_API_URL") or "").strip().rstrip("/")
    key = (env.get("HERMES_API_KEY") or "").strip()
    model = (env.get("HERMES_MODEL") or DEFAULT_MODEL).strip()
    if not url or not re.match(r"https?://", url):
        return None
    return {"url": url, "key": key, "model": model}


def make_transport(config, timeout=TIMEOUT_S):
    """Return ``send(messages) -> (text, model)`` over urllib; raises HermesUnavailable."""
    def send(messages, deadline=None):
        if config is None:
            raise HermesUnavailable("HERMES_API_URL is not configured")
        remaining = timeout if deadline is None else max(1.0, deadline - time.monotonic())
        body = json.dumps({"model": config["model"], "messages": messages,
                           "temperature": 0.2, "stream": False}).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if config["key"]:
            headers["Authorization"] = "Bearer " + config["key"]
        req = urllib.request.Request(config["url"] + "/chat/completions", data=body,
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=remaining) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise HermesUnavailable(f"gateway HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HermesUnavailable(f"gateway unreachable: {type(exc).__name__}") from None
        except ValueError:
            raise HermesUnavailable("gateway returned non-JSON") from None
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise HermesUnavailable("gateway response has no message content") from None
        return (text or ""), str(payload.get("model") or config["model"])
    return send


def extract_json(text):
    """Parse a JSON object from model text (tolerates fences / leading prose)."""
    if not isinstance(text, str):
        raise ValueError("no text")
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        value = json.loads(stripped)
    except ValueError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object in reply") from None
        value = json.loads(stripped[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("reply JSON is not an object")
    return value


def _text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _sources(item, snapshot, where, warnings):
    raw = item.get("source_fields")
    if not isinstance(raw, list) or not raw:
        warnings.append(f"{where}: dropped, no source_fields")
        return None
    bad = [s for s in raw if not resolve_path(snapshot, s)]
    if bad:
        warnings.append(f"{where}: dropped, unknown source_fields {bad[:3]}")
        return None
    return [str(s) for s in raw[:6]]


def validate(raw, snapshot):
    """Return (analysis, warnings). Raises ValueError when the core verdict is unusable."""
    warnings = []
    bias = raw.get("bias")
    if bias not in BIASES:
        raise ValueError(f"bias must be one of {BIASES}")
    lp, sp = raw.get("long_pct"), raw.get("short_pct")
    if not all(type(v) is int and 0 <= v <= 100 for v in (lp, sp)) or lp + sp != 100:
        raise ValueError("long_pct/short_pct must be integers 0-100 summing to 100")
    confidence = raw.get("confidence") if raw.get("confidence") in CONFIDENCES else None
    if confidence is None:
        warnings.append("confidence missing or invalid; set to low")
        confidence = "low"
    bounds = price_bounds(snapshot)
    levels = []
    for i, item in enumerate((raw.get("levels") or [])[:MAX_LEVELS * 2]):
        where = f"levels[{i}]"
        if not isinstance(item, dict) or item.get("type") not in LEVEL_TYPES:
            warnings.append(f"{where}: dropped, invalid type")
            continue
        price = item.get("price")
        if isinstance(price, str):
            try:
                price = float(price)
            except ValueError:
                price = None
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price):
            warnings.append(f"{where}: dropped, price not a finite number")
            continue
        if bounds is None or not bounds[0] <= price <= bounds[1]:
            warnings.append(f"{where}: dropped, price {price} outside snapshot window ±3 ATR")
            continue
        sources = _sources(item, snapshot, where, warnings)
        if sources is None:
            continue
        if len(levels) < MAX_LEVELS:
            level = {"type": item["type"], "price": float(price),
                     "reason": _text(item.get("reason"), 160) or "", "source_fields": sources}
            if item["type"] in ANCHORED_TYPES and not anchored(snapshot, float(price), sources):
                level["unanchored"] = True
                warnings.append(f"{where}: price {price} is not near any cited value; flagged")
            levels.append(level)
    factors = []
    for i, item in enumerate((raw.get("key_factors") or [])[:MAX_FACTORS * 2]):
        where = f"key_factors[{i}]"
        text = _text(item.get("text"), 200) if isinstance(item, dict) else None
        if text is None:
            warnings.append(f"{where}: dropped, no text")
            continue
        sources = _sources(item, snapshot, where, warnings)
        if sources is not None and len(factors) < MAX_FACTORS:
            factors.append({"text": text, "source_fields": sources})
    scenarios = []
    for i, item in enumerate((raw.get("scenarios") or [])[:MAX_SCENARIOS * 2]):
        fields = {k: _text(item.get(k), 200) if isinstance(item, dict) else None
                  for k in ("name", "trigger", "target", "invalidation")}
        if not fields["name"] or not fields["trigger"]:
            warnings.append(f"scenarios[{i}]: dropped, needs name and trigger")
        elif len(scenarios) < MAX_SCENARIOS:
            scenarios.append({k: v or "" for k, v in fields.items()})
    risks = [r for r in (_text(x, 200) for x in (raw.get("risks") or [])[:MAX_RISKS]) if r]
    summary = _text(raw.get("summary_ru"), 2000) or ""
    if len(summary) > 400:
        warnings.append(f"summary_ru truncated from {len(summary)} to 400 chars")
        summary = summary[:400]
    return {"bias": bias, "long_pct": lp, "short_pct": sp, "confidence": confidence,
            "horizon": _text(raw.get("horizon"), 60) or "", "levels": levels,
            "scenarios": scenarios, "key_factors": factors, "risks": risks,
            "summary_ru": summary}, warnings


def ask_hermes(snapshot, send, *, timeout=TIMEOUT_S, clock=time.monotonic):
    """One request plus at most one repair retry. Returns a dict with raw + validated output.

    Raises HermesUnavailable for transport failures; returns ``analysis=None`` with
    ``error`` when the model output stays invalid after the repair attempt.
    """
    deadline = clock() + timeout
    messages = build_messages(snapshot)
    started = clock()
    raws, model, error = [], None, None
    for attempt in range(2):
        text, model = send(messages, deadline=deadline)
        raws.append(text)
        try:
            analysis, warnings = validate(extract_json(text), snapshot)
            return {"analysis": analysis, "warnings": warnings, "raw": raws, "model": model,
                    "attempts": attempt + 1, "latency_ms": int((clock() - started) * 1000)}
        except ValueError as exc:
            error = str(exc)[:200]
        if attempt == 0:
            if clock() >= deadline - 2:
                break
            messages = messages + [
                {"role": "assistant", "content": text[:4000]},
                {"role": "user", "content": f"Your reply was invalid: {error}. Reply again with "
                                            "ONLY the corrected strict JSON object."}]
    return {"analysis": None, "warnings": [f"model output invalid after repair: {error}"],
            "raw": raws, "model": model, "attempts": len(raws), "error": error,
            "latency_ms": int((clock() - started) * 1000)}
