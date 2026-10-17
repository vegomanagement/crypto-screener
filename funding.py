"""Pure funding-rate normaliser for Binance USDT-M, Bybit linear and Hyperliquid.

Inputs are the raw public JSON bodies; outputs keep rates as exact decimal
strings. Every block carries ``source``; anything unavailable is ``None`` with a
reason in ``missing``. ``predicted`` is the venue's live estimate for the
current period and keeps changing until it settles at ``settles_at``.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext

HOUR_MS = 3_600_000
PREDICTED_NOTE = "live estimate for the current period; changes until it settles"
VENUE_KEYS = {"BinPerp": "binance", "BybitPerp": "bybit", "HlPerp": "hyperliquid"}
ANNUALIZE = "rate × 24 / interval_hours × 365"


def dec(value):
    """Finite Decimal from a JSON string/number, else None (never from a bool)."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return out if out.is_finite() else None


def fmt(value):
    """Plain (non-scientific) decimal string with trailing zeros removed."""
    if value is None:
        return None
    s = format(value, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def annualized(rate, interval_hours):
    """Simple (non-compounded) annualisation as an exact decimal string."""
    r, h = dec(rate), dec(interval_hours)
    if r is None or h is None or h <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 28
        return fmt((r * 24 / h * 365).quantize(Decimal("1e-10")))


def _ms(value):
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def _hours(value):
    d = dec(value)
    return int(d) if d is not None and d > 0 and d == int(d) else None


def premium(mark, index):
    m, i = dec(mark), dec(index)
    if m is None or i is None or i == 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 28
        return fmt(((m - i) / i).quantize(Decimal("1e-10")))


def trend(history, n=10):
    """Summary of the last ``n`` settled rates (oldest → newest)."""
    rates = [dec(h["rate"]) for h in history[-n:] if dec(h.get("rate")) is not None]
    if len(rates) < 2:
        return None
    with localcontext() as ctx:
        ctx.prec = 28
        mean = sum(rates) / len(rates)
        k = max(1, len(rates) // 3)
        head, tail = sum(rates[:k]) / k, sum(rates[-k:]) / k
        scale = max((abs(r) for r in rates), default=Decimal(0))
        eps = scale * Decimal("0.1")
        direction = "rising" if tail - head > eps else "falling" if head - tail > eps else "flat"
        return {"n": len(rates), "mean": fmt(mean.quantize(Decimal("1e-12"))),
                "min": fmt(min(rates)), "max": fmt(max(rates)),
                "positive": sum(1 for r in rates if r > 0), "direction": direction,
                "method": f"mean of last {k} vs first {k} settlements, ±10% of max |rate| = flat"}


def finish(out, interval_hours, interval_source, now_ms):
    """Fill interval/annualised blocks and the missing map; common to all venues."""
    missing = out.setdefault("missing", {})
    if interval_hours:
        out["interval_hours"] = {"value": interval_hours, "source": interval_source}
    else:
        out["interval_hours"] = None
        missing["interval_hours"] = interval_source or "not published"
    ann = {"formula": ANNUALIZE, "source": "computed"}
    for key in ("last_settled", "predicted"):
        block = out.get(key)
        ann[key] = annualized(block["rate"], interval_hours) if block and interval_hours else None
    out["annualized"] = ann
    pred = out.get("predicted")
    if pred and pred.get("settles_at"):
        pred["settles_in_ms"] = max(0, pred["settles_at"] - now_ms)
    hist = (out.get("history") or {}).get("items") or []
    out["trend"] = trend(hist)
    if out["trend"] is None:
        missing.setdefault("trend", "fewer than 2 settlements in history")
    for key in ("last_settled", "predicted", "history", "mark_price", "index_price", "premium",
                "cap", "floor"):
        if out.get(key) is None:
            missing.setdefault(key, "not available from the venue response")
    out["missing"] = dict(sorted(missing.items()))
    return out


def _value(value, source):
    return None if value is None else {"value": value, "source": source}


def _history(items, source):
    return {"source": source, "items": items} if items else None


# ── venues ──────────────────────────────────────────────────────────────────

def binance(symbol, premium_index, history_rows, info_rows, now_ms):
    """premiumIndex + fundingRate history + fundingInfo (list; may lack the symbol)."""
    out = {"exchange": "binance", "symbol": symbol, "as_of": now_ms, "missing": {}}
    p = premium_index if isinstance(premium_index, dict) and premium_index.get("symbol") == symbol else None
    if p is None:
        out["missing"]["premium_index"] = "GET /fapi/v1/premiumIndex failed or symbol mismatch"
    src = "binance GET /fapi/v1/premiumIndex"
    rate = dec((p or {}).get("lastFundingRate"))
    out["predicted"] = ({"rate": fmt(rate), "settles_at": _ms(p.get("nextFundingTime")),
                         "source": f"{src} lastFundingRate / nextFundingTime", "note": PREDICTED_NOTE}
                        if rate is not None else None)
    out["mark_price"] = _value(fmt(dec((p or {}).get("markPrice"))), f"{src} markPrice")
    out["index_price"] = _value(fmt(dec((p or {}).get("indexPrice"))), f"{src} indexPrice")
    prem = premium((p or {}).get("markPrice"), (p or {}).get("indexPrice"))
    out["premium"] = _value(prem, "computed (mark − index) / index from premiumIndex")
    items = sorted(({"rate": fmt(dec(r.get("fundingRate"))), "time": _ms(r.get("fundingTime"))}
                    for r in history_rows or []
                    if isinstance(r, dict) and r.get("symbol") == symbol and dec(r.get("fundingRate")) is not None),
                   key=lambda r: r["time"] or 0)
    out["history"] = _history(items[-30:], "binance GET /fapi/v1/fundingRate")
    out["last_settled"] = ({**items[-1], "source": "binance GET /fapi/v1/fundingRate (latest settlement)"}
                           if items else None)
    info = next((r for r in info_rows or [] if isinstance(r, dict) and r.get("symbol") == symbol), None)
    if info:
        hours, isrc = _hours(info.get("fundingIntervalHours")), "binance GET /fapi/v1/fundingInfo fundingIntervalHours"
        out["cap"] = _value(fmt(dec(info.get("adjustedFundingRateCap"))), "binance fundingInfo adjustedFundingRateCap")
        out["floor"] = _value(fmt(dec(info.get("adjustedFundingRateFloor"))), "binance fundingInfo adjustedFundingRateFloor")
    else:
        hours = 8 if info_rows is not None else None
        isrc = ("default 8h: symbol not listed in /fapi/v1/fundingInfo (Binance lists only adjusted symbols)"
                if hours else "GET /fapi/v1/fundingInfo failed")
        out["cap"] = out["floor"] = None
        out["missing"]["cap"] = out["missing"]["floor"] = "not listed in /fapi/v1/fundingInfo"
    if hours and len(items) >= 2 and items[-1]["time"] and items[-2]["time"]:
        observed = round((items[-1]["time"] - items[-2]["time"]) / HOUR_MS)
        if observed != hours:
            out["missing"]["interval_check"] = f"last two settlements are {observed}h apart vs {hours}h"
    return finish(out, hours, isrc, now_ms)


def bybit(symbol, tickers, history, now_ms):
    """/v5/market/tickers (predicted) + /v5/market/funding/history (settled)."""
    out = {"exchange": "bybit", "symbol": symbol, "as_of": now_ms, "missing": {}}
    t = None
    if isinstance(tickers, dict) and tickers.get("retCode") == 0:
        t = next((r for r in tickers.get("result", {}).get("list") or [] if r.get("symbol") == symbol), None)
    if t is None:
        out["missing"]["tickers"] = "GET /v5/market/tickers failed or symbol missing"
    t = t or {}
    src = "bybit GET /v5/market/tickers"
    rate = dec(t.get("fundingRate"))
    out["predicted"] = ({"rate": fmt(rate), "settles_at": _ms(t.get("nextFundingTime")),
                         "source": f"{src} fundingRate / nextFundingTime", "note": PREDICTED_NOTE}
                        if rate is not None else None)
    out["mark_price"] = _value(fmt(dec(t.get("markPrice"))), f"{src} markPrice")
    out["index_price"] = _value(fmt(dec(t.get("indexPrice"))), f"{src} indexPrice")
    out["premium"] = _value(premium(t.get("markPrice"), t.get("indexPrice")),
                            "computed (mark − index) / index from tickers")
    cap = dec(t.get("fundingCap"))
    out["cap"] = _value(fmt(cap), f"{src} fundingCap")
    out["floor"] = _value(fmt(-cap) if cap is not None else None,
                          f"{src} fundingCap (Bybit documents it as the upper and lower limit)")
    rows = []
    if isinstance(history, dict) and history.get("retCode") == 0:
        rows = history.get("result", {}).get("list") or []
    else:
        out["missing"]["history"] = "GET /v5/market/funding/history failed"
    items = sorted(({"rate": fmt(dec(r.get("fundingRate"))), "time": _ms(r.get("fundingRateTimestamp"))}
                    for r in rows if r.get("symbol") == symbol and dec(r.get("fundingRate")) is not None),
                   key=lambda r: r["time"] or 0)
    out["history"] = _history(items[-30:], "bybit GET /v5/market/funding/history")
    out["last_settled"] = ({**items[-1], "source": "bybit GET /v5/market/funding/history (latest)"}
                           if items else None)
    hours = _hours(t.get("fundingIntervalHour"))
    return finish(out, hours, f"{src} fundingIntervalHour" if hours else "fundingIntervalHour missing", now_ms)


def hyperliquid(coin, meta_ctxs, history_rows, now_ms):
    """metaAndAssetCtxs (current hourly predicted) + fundingHistory (settled, hourly)."""
    out = {"exchange": "hyperliquid", "symbol": coin, "as_of": now_ms, "missing": {}}
    ctx = None
    if isinstance(meta_ctxs, list) and len(meta_ctxs) == 2:
        names = [u.get("name") for u in (meta_ctxs[0] or {}).get("universe") or []]
        if coin in names and names.index(coin) < len(meta_ctxs[1]):
            ctx = meta_ctxs[1][names.index(coin)]
    if ctx is None:
        out["missing"]["asset_ctx"] = "coin not found in metaAndAssetCtxs"
    ctx = ctx or {}
    src = "hyperliquid POST /info metaAndAssetCtxs"
    rate = dec(ctx.get("funding"))
    next_hour = (now_ms // HOUR_MS + 1) * HOUR_MS
    out["predicted"] = ({"rate": fmt(rate), "settles_at": next_hour,
                         "source": f"{src} funding; settles hourly on the hour (UTC)",
                         "note": PREDICTED_NOTE} if rate is not None else None)
    out["mark_price"] = _value(fmt(dec(ctx.get("markPx"))), f"{src} markPx")
    out["index_price"] = _value(fmt(dec(ctx.get("oraclePx"))), f"{src} oraclePx (oracle, not an index)")
    out["premium"] = _value(fmt(dec(ctx.get("premium"))), f"{src} premium")
    out["cap"] = out["floor"] = None
    out["missing"]["cap"] = out["missing"]["floor"] = "not published by the info API"
    items = sorted(({"rate": fmt(dec(r.get("fundingRate"))), "time": _ms(r.get("time"))}
                    for r in history_rows or []
                    if isinstance(r, dict) and r.get("coin") == coin and dec(r.get("fundingRate")) is not None),
                   key=lambda r: r["time"] or 0)
    out["history"] = _history(items[-30:], "hyperliquid POST /info fundingHistory")
    out["last_settled"] = ({**items[-1], "source": "hyperliquid POST /info fundingHistory (latest)"}
                           if items else None)
    return finish(out, 1, "Hyperliquid funds hourly", now_ms)


def hl_coin(exchange, symbol):
    """Coin key used by Hyperliquid's predictedFundings for a venue symbol (heuristic)."""
    if exchange == "hyperliquid":
        return symbol
    base = symbol[:-4] if symbol.endswith("USDT") else None
    if not base:
        return None
    return "k" + base[4:] if base.startswith("1000") and len(base) > 4 else base


def cross_venue(predicted_fundings, coin, now_ms):
    """Rows for Binance/Bybit/HL from POST /info predictedFundings (or None)."""
    if not coin or not isinstance(predicted_fundings, list):
        return None
    entry = next((e for e in predicted_fundings
                  if isinstance(e, list) and len(e) == 2 and e[0] == coin), None)
    if entry is None:
        return None
    rows = []
    for venue_key, data in entry[1] or []:
        if venue_key not in VENUE_KEYS or not isinstance(data, dict):
            continue
        hours, settles = _hours(data.get("fundingIntervalHours")), _ms(data.get("nextFundingTime"))
        rate = fmt(dec(data.get("fundingRate")))
        rows.append({"venue": VENUE_KEYS[venue_key], "rate": rate, "interval_hours": hours,
                     "settles_at": settles, "annualized": annualized(rate, hours) if rate else None,
                     "stale": bool(settles and settles < now_ms)})
    anns = [dec(r["annualized"]) for r in rows if r["annualized"] is not None]
    spread = fmt(max(anns) - min(anns)) if len(anns) >= 2 else None
    return {"source": "hyperliquid POST /info predictedFundings", "coin": coin, "rows": rows,
            "annualized_spread": spread,
            "note": "predicted rates as published by Hyperliquid for each venue; stale = settlement time already passed"}
