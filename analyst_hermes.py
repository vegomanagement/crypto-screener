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
MAX_LEVELS, MAX_SCENARIOS, MAX_FACTORS, MAX_RISKS = 12, 4, 12, 6
DIRECTIONS = ("long", "short")
SERVER_BLENDED = ("baseline",)  # sections the split already uses; citing them would double-count
STRENGTHS = (1, 2, 3)


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
{"bias":"long|short|neutral","confidence":"low|medium|high","horizon":"<e.g. 4-12 bars of 15m>",
 "factor_scores":[{"factor":"<short evidence>","direction":"long|short","strength":1|2|3,
                   "source_fields":["<dotted path in snapshot>"]}],
 "levels":[{"type":"support|resistance|entry|stop|target|liquidity|poi","price":<number>,
            "reason":"<short>","source_fields":["<dotted path in snapshot>"]}],
 "scenarios":[{"name":"<short>","trigger":"<text>","target":"<text>","invalidation":"<text>"}],
 "risks":["<short>"],
 "summary_ru":"<Russian, at most 400 characters>"}
Rules:
- Do NOT output any percentage, probability or long/short split anywhere (no long_pct, no
  short_pct, no "%" odds). The server computes the split from your factor_scores.
- factor_scores: 6-12 separate pieces of evidence. Look for evidence on BOTH sides and list
  every real one, bullish and bearish; do not pad either side. strength 1 = weak, 2 = clear,
  3 = strong/decisive. Each factor cites its own fields; never cite the same field set twice.
- Funding: when snapshot.funding is present, add a factor (or say in risks why it is neutral)
  from funding.last_settled.rate vs funding.predicted.rate, funding.annualized_predicted and
  funding.cross_venue / funding.cross_venue_annualized_spread. Positive funding = longs pay
  (crowded longs), negative = shorts pay. funding.predicted is a live estimate that changes until
  settlement; funding.last_settled is final.
- Positioning: when snapshot.positioning is present, look for positioning evidence on BOTH
  sides (e.g. crowd long-heavy vs top traders, taker sellers vs OI building) and add 1-3 factors
  citing leaf values such as positioning.top_traders.position.long_share,
  positioning.crowd.long_share, positioning.taker.value, positioning.open_interest.change_4h.value
  with .price_pct, positioning.basis.value, positioning.signals.<id>.state,
  positioning.liquidations.windows.1h.long_usd, positioning.whales.aggregate.long_notional.
  Never cite a null value (see positioning.missing). Caveats: liquidations are a LOWER BOUND
  (check covered_min / complete_coverage; strength 1 when coverage is partial), and HL whales
  are top-N accounts by account value that include vaults and market makers (strength at most
  2). Positioning labels are descriptive, not trade signals.
- Do not cite baseline.* in factor_scores: the server blends that deterministic baseline in
  itself, so citing it would count it twice.
- bias is your own reading and is used only as a cross-check against the computed split.
- Every source_fields entry must be an existing dotted path in the snapshot, e.g.
  "indicators.rsi14", "structure.last_swing_high.price", "fvg.nearest.0.top",
  "liquidity.pools.2.price", "orderbook.imbalance", "funding.predicted.rate". candles.recent
  rows are arrays in candles.recent_fields order, so cite e.g. "candles.recent.23.3" (low).
- Level prices must come from snapshot values or be derived from them (within the candle
  window +/- 3 ATR).
Keep the whole reply under 3000 characters: at most 12 factor_scores, 8 levels, 3 scenarios,
4 risks, every text field under 80 characters."""


def build_messages(snapshot):
    data = json.dumps(snapshot, separators=(",", ":"), default=str)
    user = (f"Analyse {snapshot['exchange']} {snapshot['symbol']} interval {snapshot['interval']} "
            f"(minutes unless D/W/M). Outcomes are later scored over "
            f"{snapshot.get('evaluation_horizon_bars')} bars by whether price moves 1 ATR in the "
            f"direction computed from your factor_scores before 1 ATR against.\n{SCHEMA_TEXT}\nSNAPSHOT:\n{data}")
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
        raise ValueError("no text")  # noqa: TRY004 — callers handle ValueError
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        value = json.loads(stripped)
    except ValueError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object in reply") from None
        value = json.loads(stripped[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("reply JSON is not an object")  # noqa: TRY004
    return value


def close_truncated(text):
    """Append missing ``]``/``}`` when a JSON object was cut off between tokens.

    Returns None when the cut happened inside a string or the text is not an
    unfinished object — those are not repaired (the model is asked again).
    """
    start = text.find("{")
    if start < 0:
        return None
    stack, in_str, escape = [], False, False
    for ch in text[start:]:
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and (not stack or stack.pop() != ch):
            return None
    if in_str or not stack:
        return None
    body = text[start:].rstrip().rstrip(",")
    return body + "".join(reversed(stack))


def parse_reply(text):
    """(object, notes): strict parse first, then one bracket-closing repair for truncation."""
    try:
        return extract_json(text), []
    except ValueError as exc:
        closed = close_truncated(re.sub(r"^```(?:json)?\s*", "", (text or "").strip()))
        if closed is None:
            raise
        try:
            value = json.loads(closed)
        except ValueError:
            raise exc from None
        if not isinstance(value, dict):
            raise exc from None
        return value, ["reply was truncated; missing closing brackets were added before validation"]


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


def _is_null(snapshot, path):
    """True when a cited path holds null, or a metric envelope whose ``value`` is null."""
    value = _lookup(snapshot, path)
    return value is None or (isinstance(value, dict) and "value" in value and value["value"] is None)


def validate_factors(items, snapshot, warnings):
    """Keep factors with a known direction, integer strength 1-3 and resolvable source_fields.

    A factor whose source_fields set equals one already kept is a duplicate (the same
    evidence counted twice) and is dropped, whatever its direction or wording. Factors
    citing ``baseline.*`` are dropped too: the server blends the baseline in itself, and so
    are factors citing a null (missing) ``positioning.*`` value. ``positioning.*`` paths are accepted like any
    other existing snapshot path.
    """
    if not isinstance(items, list):
        warnings.append("factor_scores missing or not a list")
        return []
    out, seen = [], set()
    for i, item in enumerate(items[:MAX_FACTORS * 2]):
        where = f"factor_scores[{i}]"
        if not isinstance(item, dict):
            warnings.append(f"{where}: dropped, not an object")
            continue
        name = _text(item.get("factor"), 120)
        direction, strength = item.get("direction"), item.get("strength")
        if name is None or direction not in DIRECTIONS:
            warnings.append(f"{where}: dropped, needs factor text and direction long|short")
            continue
        if type(strength) is not int or strength not in STRENGTHS:
            warnings.append(f"{where}: dropped, strength must be integer 1-3")
            continue
        sources = _sources(item, snapshot, where, warnings)
        if sources is None:
            continue
        if any(x.split(".")[0].split("[")[0] in SERVER_BLENDED for x in sources):
            warnings.append(f"{where}: dropped, cites the baseline that the server already blends in")
            continue
        nulls = [x for x in sources if x.startswith("positioning.") and _is_null(snapshot, x)]
        if nulls:
            warnings.append(f"{where}: dropped, cites null (missing) positioning fields {nulls[:3]}")
            continue
        key = frozenset(sources)
        if key in seen:
            warnings.append(f"{where}: dropped, duplicate of an earlier factor citing {sorted(key)[:3]}")
            continue
        if len(out) >= MAX_FACTORS:
            warnings.append(f"{where}: dropped, more than {MAX_FACTORS} factors")
            continue
        seen.add(key)
        out.append({"factor": name, "direction": direction, "strength": strength,
                    "source_fields": sources})
    return out


def validate(raw, snapshot):
    """Return (analysis, warnings). Raises ValueError when the bias or every factor is unusable.

    The analysis has no percentages: ``analyst.compute_split`` derives them from
    ``factor_scores`` plus the snapshot baseline (see analyst_service).
    """
    warnings = []
    bias = raw.get("bias")
    if bias not in BIASES:
        raise ValueError(f"bias must be one of {BIASES}")
    ignored = [k for k in ("long_pct", "short_pct", "probability", "p_long") if k in raw]
    if ignored:
        warnings.append(f"model sent {ignored}; ignored, the split is computed server-side")
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
    factors = validate_factors(raw.get("factor_scores"), snapshot, warnings)
    if not factors:
        raise ValueError("factor_scores must contain at least one valid factor "
                         "{factor, direction long|short, strength 1-3, source_fields}")
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
    return {"agent_bias": bias, "confidence": confidence,
            "horizon": _text(raw.get("horizon"), 60) or "", "levels": levels,
            "scenarios": scenarios, "factor_scores": factors, "risks": risks,
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
            raw_obj, notes = parse_reply(text)
            analysis, warnings = validate(raw_obj, snapshot)
            warnings = notes + warnings
            return {"analysis": analysis, "warnings": warnings, "raw": raws, "model": model,
                    "attempts": attempt + 1, "latency_ms": int((clock() - started) * 1000)}
        except ValueError as exc:
            error = str(exc)[:200]
        if attempt == 0:
            if clock() >= deadline - 2:
                break
            messages = messages + [
                {"role": "assistant", "content": text[:4000]},
                {"role": "user", "content": f"Your reply was invalid: {error}. It may have been cut "
                                            "off: reply again with ONLY the corrected strict JSON "
                                            "object and keep every text field short."}]
    return {"analysis": None, "warnings": [f"model output invalid after repair: {error}"],
            "raw": raws, "model": model, "attempts": len(raws), "error": error,
            "latency_ms": int((clock() - started) * 1000)}
