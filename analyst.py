"""Real-data snapshot of one chart for the Hermes analyst (pure, provider-injected).

Every section carries ``source`` and ``ts`` (UTC ms). A section that cannot be
built is ``None`` and its reason is recorded in ``snapshot["missing"]``; nothing
is ever invented. Network access only happens inside the injected providers.
"""
from __future__ import annotations

import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC
from itertools import pairwise

SCHEMA = "hermes-analyst/1"
CANDLE_LIMIT = 300
RECENT_BARS = 24
PROVIDER_TIMEOUT_S = 25
FOOTPRINT_INTERVALS = ("1", "3", "5", "15", "30", "60")
EVAL_HORIZON_BARS = 12


# ── pure indicators ─────────────────────────────────────────────────────────

def ema(values, period):
    """EMA seeded with the SMA of the first ``period`` values; None if too short."""
    if period < 1 or len(values) < period:
        return None
    k = 2.0 / (period + 1)
    value = sum(values[:period]) / period
    for x in values[period:]:
        value = x * k + value * (1 - k)
    return value


def atr(candles, period=14):
    """Wilder ATR over closed candles; None if fewer than period+1 bars."""
    if len(candles) < period + 1:
        return None
    trs = [max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"]))
           for p, c in pairwise(candles)]
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def rsi(closes, period=14):
    """Wilder RSI; None if fewer than period+1 closes."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for a, b in pairwise(closes):
        gains.append(max(b - a, 0.0))
        losses.append(max(a - b, 0.0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for g, loss in zip(gains[period:], losses[period:]):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + loss) / period
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    return 100 - 100 / (1 + avg_g / avg_l)


def _r(value, digits=8):
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    return float(f"{value:.{digits}g}")


def _f(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


# ── section builders (each takes already-fetched data) ─────────────────────

def candles_section(chart, recent=RECENT_BARS):
    candles = chart["candles"]
    if len(candles) < 30:
        raise ValueError(f"only {len(candles)} closed candles")
    last = candles[-1]
    return {
        "source": f"{chart.get('source')} closed candles via exchange_markets/chart_data",
        "ts": last["close_ts"], "count": len(candles), "first_ts": candles[0]["ts"],
        "last_close_ts": last["close_ts"], "closed_only": True,
        "window_high": _r(max(c["h"] for c in candles)),
        "window_low": _r(min(c["l"] for c in candles)),
        "recent_fields": ["open_ts", "o", "h", "l", "c", "v"],
        "recent": [[c["ts"], _r(c["o"]), _r(c["h"]), _r(c["l"]), _r(c["c"]), _r(c["v"], 6)]
                   for c in candles[-recent:]],
    }


def indicators_section(candles):
    closes = [c["c"] for c in candles]
    e = {p: _r(ema(closes, p)) for p in (7, 14, 28, 200)}
    stack = "mixed"
    if None not in (e[7], e[14], e[28]):
        if e[7] > e[14] > e[28]:
            stack = "bullish"
        elif e[7] < e[14] < e[28]:
            stack = "bearish"
    return {"source": "analyst.py from the same closed candles", "ts": candles[-1]["close_ts"],
            "ema7": e[7], "ema14": e[14], "ema28": e[28], "ema200": e[200],
            "ema_stack_7_14_28": stack, "atr14": _r(atr(candles)), "rsi14": _r(rsi(closes), 4),
            "close": _r(closes[-1])}


def structure_section(candles):
    from structure import detect_structure
    state = detect_structure(candles)

    def swing(s):
        return None if s is None else {"price": _r(s.price), "ts": candles[s.index]["ts"]}
    events = [{"kind": e.kind, "direction": e.direction, "level": _r(e.level),
               "ts": candles[e.at]["ts"]} for e in state.events[-5:]]
    return {"source": "structure.detect_structure (swing length default)",
            "ts": candles[-1]["close_ts"], "trend": state.trend,
            "last_swing_high": swing(state.last_swing_high),
            "last_swing_low": swing(state.last_swing_low), "recent_events": events}


def fvg_section(chart, price, limit=6):
    zones = [z for z in chart.get("zones") or [] if z.get("type") == "fvg"]
    zones.sort(key=lambda z: min(abs(price - z["top"]), abs(price - z["bottom"])))
    return {"source": "chart_data._fvg_zones (unfilled, wick mitigation)",
            "ts": chart["candles"][-1]["close_ts"], "total_unfilled": len(zones),
            "nearest": [{"direction": z["direction"], "top": _r(z["top"]),
                         "bottom": _r(z["bottom"]), "start_ts": z["start_ts"]}
                        for z in zones[:limit]]}


def order_blocks_section(candles, limit=6):
    from order_blocks import find_order_blocks
    blocks = [b for b in find_order_blocks(candles, lookback=120) if not b.mitigated]
    blocks = blocks[-limit:]
    return {"source": "order_blocks.find_order_blocks (lookback 120, unmitigated)",
            "ts": candles[-1]["close_ts"],
            "unmitigated": [{"direction": b.direction, "high": _r(b.high), "low": _r(b.low),
                             "ts": candles[b.candle_idx]["ts"]} for b in blocks]}


# ── sections from screener.fetch_market (Bybit-first USDT aggregate) ───────

MARKET_SOURCE = "screener.fetch_market (Bybit-first USDT aggregate, not venue-pinned)"


def market_sections(market, now_ms):
    """Liquidity pools, HTF bias, regime and flow from one fetch_market dict."""
    from htf_bias import compute_htf_bias
    from liquidity import build_liquidity_map
    from regime import classify_regime
    out = {}
    lmap = build_liquidity_map(market)
    pools = sorted(lmap.pools, key=lambda p: abs(p.dist_pct))[:10]
    out["liquidity"] = {"source": f"liquidity.build_liquidity_map over {MARKET_SOURCE}",
                        "ts": now_ms, "reference_price": _r(lmap.price),
                        "pools": [{"kind": p.kind, "price": _r(p.price), "side": p.side,
                                   "strength": p.strength, "dist_pct": _r(p.dist_pct, 4)}
                                  for p in pools]}
    bias = compute_htf_bias(market)
    out["htf_bias"] = {"source": f"htf_bias.compute_htf_bias (60/240/D) over {MARKET_SOURCE}",
                       "ts": now_ms, "direction": bias.direction, "strength": bias.strength,
                       "zones": bias.zones}
    reg = classify_regime(market)
    out["regime"] = {"source": f"regime.classify_regime over {MARKET_SOURCE}", "ts": now_ms,
                     "phase": reg.phase, "bias": reg.bias, "zone": reg.zone,
                     "positioning": reg.positioning, "range_state": reg.range_state,
                     "pos_in_range": _r(reg.pos_in_range, 4), "confidence": reg.confidence}
    cvd, macro, bybit = market.get("cvd") or {}, market.get("macro") or {}, market.get("bybit") or {}
    out["flow"] = {
        "source": MARKET_SOURCE, "ts": now_ms,
        "cvd_1h": ({"trend": cvd.get("trend"), "price_trend": cvd.get("price_trend"),
                    "divergence": cvd.get("divergence"),
                    "method": "estimated from 1h candle shape (screener.compute_cvd), not trades"}
                   if cvd.get("trend") not in (None, "unknown") else None),
        "fear_greed": ({"value": macro.get("fg_value"), "label": macro.get("fg_label"),
                        "source": "alternative.me via screener.get_macro"}
                       if macro.get("fg_value") is not None else None),
        "bybit_oi_change_pct": _r(bybit.get("oi_chg"), 4) if "oi_chg" in bybit else None,
        "change_24h_pct": _r(market.get("change_24h"), 4),
    }
    return out


def derivatives_section(ctx):
    """Venue-native funding/OI from exchange_markets.get_exchange_context."""
    # Funding lives in the dedicated ``funding`` section (settled vs predicted).
    return {"source": f"exchange_markets.get_exchange_context ({ctx.get('exchange')})",
            "ts": ctx.get("as_of"),
            "open_interest": _f(ctx.get("open_interest")),
            "open_interest_unit": ctx.get("open_interest_unit"),
            "mark_price": _f(ctx.get("mark_price")), "index_price": _f(ctx.get("index_price")),
            "ema_bias": ctx.get("ema_bias"), "warnings": list(ctx.get("warnings") or [])[:4]}


def funding_section(fd):
    """Settled vs predicted funding from funding_data.FundingService (exact decimal strings)."""
    def block(name, *keys):
        b = fd.get(name)
        return {k: b.get(k) for k in keys} if b else None
    ann = fd.get("annualized") or {}
    cv = fd.get("cross_venue") or {}
    return {"source": f"funding_data.FundingService ({fd.get('exchange')} public endpoints)",
            "ts": fd.get("as_of"),
            "last_settled": block("last_settled", "rate", "time"),
            "predicted": block("predicted", "rate", "settles_at", "note"),
            "interval_hours": (fd.get("interval_hours") or {}).get("value"),
            "annualized_last_settled": ann.get("last_settled"),
            "annualized_predicted": ann.get("predicted"),
            "annualized_formula": ann.get("formula"),
            "cap": (fd.get("cap") or {}).get("value"), "floor": (fd.get("floor") or {}).get("value"),
            "premium": (fd.get("premium") or {}).get("value"),
            "trend_last_settlements": fd.get("trend"),
            "cross_venue": [{k: r.get(k) for k in ("venue", "rate", "interval_hours", "annualized", "stale")}
                            for r in cv.get("rows") or []] or None,
            "cross_venue_annualized_spread": cv.get("annualized_spread"),
            "missing": fd.get("missing") or {}}


def footprint_section(fp, bars=6):
    closed = [b for b in fp.get("bars") or [] if not b.get("forming")][-bars:]
    if not closed:
        raise ValueError("no closed footprint bars")
    return {"source": f"footprint_data.FootprintService ({fp.get('exchange')} trades)",
            "ts": fp.get("as_of"),
            "coverage_note": ("complete = every trade of the bar; partial = only recent trades "
                              "the venue still serves (Bybit/Hyperliquid)"),
            "bars": [{"open_ts": b["open_time"], "delta": _f(b.get("delta")),
                      "total": _f(b.get("total")), "poc": _f(b.get("poc")),
                      "coverage": b.get("coverage")} for b in closed]}


def orderbook_section(book, top_n=20):
    bids, asks = book["bids"][:top_n], book["asks"][:top_n]
    bid_qty, ask_qty = sum(q for _, q in bids), sum(q for _, q in asks)
    total = bid_qty + ask_qty
    return {"source": f"liquidity_data.fetch_depth_snapshot ({book.get('exchange')} REST)",
            "ts": book.get("time") or book.get("observed_at"), "levels_per_side": top_n,
            "best_bid": _r(bids[0][0]), "best_ask": _r(asks[0][0]),
            "bid_qty": _r(bid_qty, 6), "ask_qty": _r(ask_qty, 6),
            "imbalance": _r((bid_qty - ask_qty) / total, 4) if total else None,
            "imbalance_note": "(bid-ask)/(bid+ask) native quantity, one sampled snapshot",
            "bid_wall": _r(max(bids, key=lambda r: r[1])[0]),
            "ask_wall": _r(max(asks, key=lambda r: r[1])[0])}


def session_section(now_ms):
    from datetime import datetime

    from killzones import active_killzone, describe
    ts = datetime.fromtimestamp(now_ms / 1000, tz=UTC)
    zone = active_killzone(ts)
    return {"source": "killzones.py (UTC windows)", "ts": now_ms,
            "active_killzone": zone.name if zone else None, "describe": describe(ts)}


def baseline_section(market, confluence, now_ms):
    """Deterministic long/short scores from compute_confluence_score + decision.make_decision."""
    from decision import make_decision
    price = _f(market.get("price")) or 0.0
    out = {"source": "screener.compute_confluence_score + decision.make_decision "
                     f"over {MARKET_SOURCE}", "ts": now_ms}
    for side, signal in (("long", "BASELINE_BULL"), ("short", "BASELINE_BEAR")):
        score, factors = confluence(signal, market, {})
        verdict = make_decision(signal, price, market, {}, score, factors)
        out[f"{side}_score"] = int(score)
        out[f"{side}_verdict"] = verdict.get("verdict")
        out[f"{side}_confidence"] = verdict.get("confidence")
        out[f"{side}_factors"] = [str(f) for f in factors[:8]]
    total = max(out["long_score"], 0) + max(out["short_score"], 0)
    long_pct = round(100 * max(out["long_score"], 0) / total) if total else 50
    out["long_pct"], out["short_pct"] = long_pct, 100 - long_pct
    return out


# ── assembly ───────────────────────────────────────────────────────────────

def market_symbol(exchange, symbol):
    """USDT symbol for screener.fetch_market, or None when it has no honest mapping."""
    import re
    if exchange == "hyperliquid":
        if ":" in symbol or not re.fullmatch(r"[A-Z0-9]{2,20}", symbol):
            return None
        symbol = symbol + "USDT"
    return symbol if re.fullmatch(r"[A-Z0-9]{2,30}USDT", symbol) else None


def default_providers(*, market=None, confluence=None, funding=None):
    """Real providers. ``market``/``confluence`` come from screener (passed in, no import cycle)."""
    import time

    def chart(exchange, symbol, interval):
        from chart_data import build_chart_snapshot
        from exchange_markets import fetch_exchange_candles
        return build_chart_snapshot(
            symbol, interval, CANDLE_LIMIT,
            lambda s, i, n: fetch_exchange_candles(exchange, s, i, n), exchange=exchange)

    def context(exchange, symbol):
        from exchange_markets import get_exchange_context
        return get_exchange_context(exchange, symbol)

    state = {}

    def footprint(exchange, symbol, interval):
        from footprint_data import FootprintService
        state.setdefault("fp", FootprintService())
        return state["fp"].footprint(exchange, symbol, interval, limit=6, include_forming=False)

    def depth(exchange, symbol):
        from liquidity_data import fetch_depth_snapshot
        return fetch_depth_snapshot(exchange, symbol, 50)

    def funding_default(exchange, symbol):
        from funding_data import FundingService
        state.setdefault("funding", FundingService())
        return state["funding"].get(exchange, symbol)

    return {"chart": chart, "market": market, "context": context, "footprint": footprint,
            "depth": depth, "confluence": confluence, "funding": funding or funding_default,
            "clock": lambda: int(time.time() * 1000)}


def _reason(exc):
    text = f"{type(exc).__name__}: {exc}"
    return text[:160]


def build_snapshot(exchange, symbol, interval, providers):
    """Gather every section in parallel; raise only if the chart candles are unavailable."""
    now = providers["clock"]()
    missing = {}
    msym = market_symbol(exchange, symbol)
    jobs = {"chart": (providers["chart"], (exchange, symbol, interval)),
            "context": (providers.get("context"), (exchange, symbol)),
            "depth": (providers.get("depth"), (exchange, symbol)),
            "funding": (providers.get("funding"), (exchange, symbol))}
    if interval in FOOTPRINT_INTERVALS:
        jobs["footprint"] = (providers.get("footprint"), (exchange, symbol, interval))
    else:
        missing["footprint"] = f"footprint is only built for {', '.join(FOOTPRINT_INTERVALS)}m"
    if msym and providers.get("market"):
        jobs["market"] = (providers["market"], (msym,))
    else:
        missing["market"] = "no USDT mapping or market provider for this venue symbol"
    results = {}
    pool = ThreadPoolExecutor(max_workers=len(jobs))
    try:
        futures = {k: pool.submit(fn, *args) for k, (fn, args) in jobs.items() if fn}
        for key, fut in futures.items():
            try:
                results[key] = fut.result(timeout=PROVIDER_TIMEOUT_S)
            except FutureTimeout:
                missing[key] = f"provider timed out after {PROVIDER_TIMEOUT_S}s"
            except Exception as exc:  # noqa: BLE001 — each provider failure is recorded, never invented
                missing[key] = _reason(exc)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if "chart" not in results:
        raise ValueError(f"chart candles unavailable: {missing.get('chart', 'unknown')}")
    chart = results["chart"]
    candles = chart["candles"]
    snap = {"schema": SCHEMA, "exchange": exchange, "symbol": symbol, "interval": interval,
            "generated_at": now, "evaluation_horizon_bars": EVAL_HORIZON_BARS}

    def section(name, fn, *args):
        try:
            snap[name] = fn(*args)
        except Exception as exc:  # noqa: BLE001
            snap[name] = None
            missing[name] = _reason(exc)

    section("candles", candles_section, chart)
    if snap["candles"] is None:
        raise ValueError(f"chart candles unusable: {missing['candles']}")
    section("indicators", indicators_section, candles)
    price = candles[-1]["c"]
    snap["price"] = {"value": _r(price), "source": "last closed candle close",
                     "ts": candles[-1]["close_ts"]}
    section("structure", structure_section, candles)
    section("fvg", fvg_section, chart, price)
    section("order_blocks", order_blocks_section, candles)
    section("session", session_section, now)
    market = results.get("market")
    names = ("liquidity", "htf_bias", "regime", "flow")
    if market:
        try:
            snap.update(market_sections(market, now))
        except Exception as exc:  # noqa: BLE001
            for n in names:
                snap[n], missing[n] = None, _reason(exc)
    else:
        for n in names:
            snap[n] = None
            missing[n] = f"market data unavailable: {missing.get('market', 'empty response')}"
    for name, fn in (("derivatives", derivatives_section), ("footprint", footprint_section),
                     ("orderbook", orderbook_section), ("funding", funding_section)):
        key = {"derivatives": "context", "orderbook": "depth"}.get(name, name)
        if key in results:
            section(name, fn, results[key])
        else:
            snap[name] = None
            missing.setdefault(name, missing.get(key, "not requested"))
    if market and providers.get("confluence"):
        section("baseline", baseline_section, market, providers["confluence"], now)
    else:
        snap["baseline"] = None
        missing["baseline"] = "needs fetch_market data and the confluence engine"
    missing.pop("context", None)
    missing.pop("depth", None)
    missing.pop("chart", None)
    snap["missing"] = dict(sorted(missing.items()))
    return snap


def snapshot_hash(snapshot):
    payload = json.dumps({k: v for k, v in snapshot.items() if k != "generated_at"},
                         sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def resolve_path(snapshot, path):
    """True when a dotted path (``a.b.0.c`` or ``a.b[0].c``) exists in the snapshot."""
    if not isinstance(path, str) or not path or len(path) > 120:
        return False
    node = snapshot
    for part in path.replace("[", ".").replace("]", "").split("."):
        if part == "":
            return False
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return False
    return True


def price_bounds(snapshot):
    """Allowed level price range: window low/high ± 3×ATR."""
    c = snapshot.get("candles") or {}
    ind = snapshot.get("indicators") or {}
    lo, hi, a = c.get("window_low"), c.get("window_high"), ind.get("atr14") or 0.0
    if lo is None or hi is None:
        return None
    return lo - 3 * a, hi + 3 * a
