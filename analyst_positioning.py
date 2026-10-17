"""Compact ``positioning`` block of the Hermes analyst snapshot (pure; no I/O).

Built from one ``positioning_api.PositioningService.snapshot`` result for the pane's venue,
symbol and a period matched to the chart interval. Series, address lists and coverage spans
are dropped so the block stays bounded (a few KB). Every metric keeps ``source`` and ``ts``;
a missing value stays ``null`` with its ``reason`` and is also listed in ``missing``.

The analyst never starts the liquidation collector or the whale tracker: the provider asks
for the snapshot with ``watch=False``, so cold collectors simply yield null + reason.
"""
from __future__ import annotations

import math

# chart interval (analyst/chart_data values) → positioning period (positioning.PERIODS)
PERIOD_FOR_INTERVAL = {
    "1": "5m", "3": "5m", "5": "5m", "15": "15m", "30": "30m",
    "60": "1h", "120": "1h", "180": "1h", "240": "4h", "480": "4h", "720": "4h",
    "D": "1d", "2D": "1d", "3D": "1d", "W": "1d", "M": "1d",
}
MAX_CHANGES = 5
SKIP_SIGNALS = ("funding",)
WHALE_SOURCE = "GET stats-data.hyperliquid.xyz/Mainnet/leaderboard + POST api.hyperliquid.xyz/info clearinghouseState"
MAX_LARGEST = 3
CAVEATS = (
    "liquidations are a LOWER BOUND: only the time the collector was connected is counted and "
    "Binance pushes at most one liquidation per symbol per second",
    "whales = Hyperliquid top-N accounts by account value; they include vaults and market makers, "
    "so their net side is not pure directional conviction",
    "ratios are completed-period venue data and can lag one period",
)


def period_for(interval):
    """Positioning period for a chart interval, or None when there is no honest match."""
    return PERIOD_FOR_INTERVAL.get(interval)


def _n(value, digits=8):
    """Exact decimal string / number → float with ``digits`` significant digits, else None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return float(f"{out:.{digits}g}") if math.isfinite(out) else None


def _metric(m, missing, path, **extra_keys):
    """Flatten a positioning ``metric()`` envelope; record null values with their reason."""
    if not isinstance(m, dict):
        missing[path] = "not provided by the positioning snapshot"
        return {"value": None, "reason": missing[path]}
    out = {"value": _n(m.get("value")), "source": m.get("source"), "ts": m.get("timestamp"),
           "period": m.get("period")}
    for key, src in extra_keys.items():
        out[key] = _n(m.get(src))
    if out["value"] is None:
        out["reason"] = m.get("reason") or "no data returned"
        missing[path] = out["reason"]
    return out


def _ratio(m, missing, path):
    return _metric(m, missing, path, long_share=("long"), short_share=("short"))


def _oi(oi, missing):
    oi = oi or {}
    out = {"current": _metric(oi.get("current"), missing, "open_interest.current", value_usd="value_usd")}
    if isinstance(oi.get("current"), dict):
        out["current"]["unit"] = oi["current"].get("unit")
    for win in ("1h", "4h", "24h"):
        ch = (oi.get("changes") or {}).get(win)
        row = _metric(ch, missing, f"open_interest.change_{win}", price_pct="price_pct")
        row["unit"] = "%"
        if isinstance(ch, dict):
            row["from_ts"] = ch.get("from_ts")
        out[f"change_{win}"] = row
    return out


def _signals(items, missing):
    out = {}
    for s in items or []:
        # funding is already snapshot.funding; repeating it here would invite double counting
        if not isinstance(s, dict) or not s.get("id") or s["id"] in SKIP_SIGNALS:
            continue
        out[s["id"]] = {"state": s.get("state"), "value": _n(s.get("value"), 6), "label": s.get("label")}
        if s.get("state") is None:
            out[s["id"]]["reason"] = s.get("reason") or "inputs missing"
            missing[f"signals.{s['id']}"] = out[s["id"]]["reason"]
    return out


def _liquidations(liq, missing):
    if not isinstance(liq, dict):
        missing["liquidations"] = "not provided"
        return {"windows": None, "reason": missing["liquidations"]}
    col = liq.get("collector")
    if not isinstance(col, dict):
        state = "n/a"
    elif ((col.get("running") or col.get("connected"))
          and liq.get("symbol") in (col.get("subscribed") or [liq.get("symbol")])):
        state = "running"
    else:
        state = "cold"
    out = {"label": liq.get("label"), "lower_bound": liq.get("lower_bound"), "source": liq.get("source"),
           "ts": liq.get("timestamp"), "collector": state}
    windows = liq.get("windows")
    if not windows:
        reason = liq.get("reason") or "no liquidation coverage"
        if state == "cold":
            reason += "; collector is cold and is not started by an analysis (open the Positioning panel)"
        out.update(windows=None, reason=reason)
        missing["liquidations"] = reason
        return out
    out["windows"] = {
        w: {"long_usd": _n(x.get("long_usd")), "short_usd": _n(x.get("short_usd")),
            "long_count": x.get("long_count"), "short_count": x.get("short_count"),
            "covered_min": round((x.get("covered_ms") or 0) / 60000, 1),
            "window_min": round((x.get("window_ms") or 0) / 60000),
            "complete_coverage": bool(x.get("complete_coverage"))}
        for w, x in windows.items() if isinstance(x, dict)}
    out["largest"] = [{"side": e.get("side"), "usd": _n(e.get("notional_usd")), "price": _n(e.get("price")),
                       "ts": e.get("ts")} for e in (liq.get("largest") or [])[:MAX_LARGEST]]
    return out


def _whales(w, missing):
    if not isinstance(w, dict):
        missing["whales"] = "not provided"
        return {"aggregate": None, "reason": missing["whales"]}
    status = w.get("status") or {}
    poll = w.get("last_poll") or {}
    tracker = "running" if status.get("running") else ("stopped" if poll else "cold")
    out = {"label": w.get("label"), "venue": "hyperliquid", "source": w.get("source") or WHALE_SOURCE,
           "ts": w.get("timestamp") or poll.get("finished_at"), "tracker": tracker,
           "caveat": "includes vaults and market makers; changes only seen between polls (~60 s)"}
    a = w.get("aggregate")
    if not a:
        reason = w.get("reason") or "whale tracker has no poll yet"
        if tracker != "running":
            reason += "; the analysis does not start the tracker (open the Positioning panel)"
        out.update(aggregate=None, reason=reason)
        missing["whales"] = reason
        return out
    out["poll"] = {"number": poll.get("poll_number"), "ok": poll.get("ok"), "requested": poll.get("requested")}
    out["aggregate"] = {k: _n(a.get(k)) for k in ("long_notional", "short_notional", "net_notional",
                                                     "long_share", "avg_entry_long", "avg_entry_short")}
    out["aggregate"].update(long_count=a.get("long_count"), short_count=a.get("short_count"))
    changes = [c for c in (w.get("changes") or []) if isinstance(c, dict)]
    kinds = {}
    for c in changes:
        kinds[c.get("kind")] = kinds.get(c.get("kind"), 0) + 1
    out["changes_count_by_kind"] = kinds
    out["recent_changes"] = [{"ts": c.get("ts"), "kind": c.get("kind"), "side": c.get("side"),
                              "from_size": _n(c.get("from_szi")), "to_size": _n(c.get("to_szi")),
                              "delta_usd": _n(c.get("delta_notional"))} for c in changes[:MAX_CHANGES]]
    if not out["aggregate"]["long_count"] and not out["aggregate"]["short_count"]:
        missing["whales"] = "no tracked top-N account holds this coin"
    return out


def positioning_section(pos, interval):
    """Compact, citable block from one positioning snapshot (see module docstring)."""
    if not isinstance(pos, dict) or pos.get("error"):
        raise ValueError(f"positioning snapshot unavailable: {(pos or {}).get('error', 'empty')}")
    missing = {}
    tt = pos.get("top_traders") or {}
    basis = pos.get("basis")
    block = {
        "source": f"positioning_api.PositioningService ({pos.get('exchange')} public endpoints)",
        "ts": pos.get("generated_at"), "exchange": pos.get("exchange"), "coin": pos.get("coin"),
        "period": pos.get("period"), "period_note": f"chart interval {interval} → positioning period "
                                                     f"{pos.get('period')}",
        "top_traders": {"account": _ratio(tt.get("account"), missing, "top_traders.account"),
                        "position": _ratio(tt.get("position"), missing, "top_traders.position")},
        "crowd": _ratio(pos.get("crowd"), missing, "crowd"),
        "taker": _metric(pos.get("taker"), missing, "taker", buy_volume="buy_volume", sell_volume="sell_volume"),
        "open_interest": _oi(pos.get("open_interest"), missing),
        "basis": _metric(basis, missing, "basis", basis_rate="basis_rate", futures_price="futures_price",
                         index_price="index_price"),
        "signals": _signals(pos.get("signals"), missing),
        "liquidations": _liquidations(pos.get("liquidations"), missing),
        "whales": _whales(pos.get("whales"), missing),
        "caveats": list(CAVEATS),
        "note": "descriptive positioning data, not trade signals",
    }
    block["missing"] = dict(sorted(missing.items()))
    return block
