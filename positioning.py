"""Pure normalisers and aggregators for public positioning data (no I/O at import or call).

Every metric is a dict carrying ``value`` (exact decimal string or None), ``source``
(venue endpoint), ``venue``, ``timestamp`` (ms, exchange-provided where available),
``period`` and ``reason`` (why the value is missing, else None). Decimal strings keep the
exchange's exact digits; derived ratios are quantised and say so in ``source``.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN

PERIODS = ("5m", "15m", "30m", "1h", "4h", "1d")
BYBIT_PERIOD = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1d"}
HOUR_MS = 3_600_000
Q4 = Decimal("0.0001")
Q2 = Decimal("0.01")


class NormaliseError(ValueError):
    """Raw response did not have the documented shape."""


def dec(value):
    """Exact Decimal from an exchange string/number; '' and None -> None."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise NormaliseError(f"not a decimal: {value!r}") from exc
    if not out.is_finite():
        raise NormaliseError(f"non-finite decimal: {value!r}")
    return out


def fmt(value):
    """Decimal -> plain string without exponent; None stays None."""
    if value is None:
        return None
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def q(value, places=Q4):
    return None if value is None else value.quantize(places, rounding=ROUND_HALF_EVEN)


def metric(value, *, source, venue, timestamp=None, period=None, reason=None, **extra):
    out = {"value": fmt(value) if isinstance(value, Decimal) else value, "source": source,
           "venue": venue, "timestamp": timestamp, "period": period,
           "reason": reason if value is None else None}
    for key, item in extra.items():
        out[key] = fmt(item) if isinstance(item, Decimal) else item
    if value is None and not out["reason"]:
        out["reason"] = "no data returned"
    return out


def missing(reason, *, source, venue, period=None):
    return metric(None, source=source, venue=venue, period=period, reason=reason)


def _ts(value):
    try:
        ts = int(value)
    except (TypeError, ValueError) as exc:
        raise NormaliseError(f"bad timestamp {value!r}") from exc
    if ts <= 0:
        raise NormaliseError("bad timestamp")
    return ts


def _rows(payload):
    if not isinstance(payload, list):
        if isinstance(payload, dict) and "code" in payload:
            raise NormaliseError(f"binance error {payload.get('code')}: {str(payload.get('msg'))[:120]}")
        raise NormaliseError("expected a JSON array")
    return payload


# ---------- Binance USDT-M /futures/data and /fapi ----------

def binance_ratio_rows(payload):
    """top/global long-short account or position ratio rows, ascending by time."""
    rows = [{"ts": _ts(r["timestamp"]), "long": dec(r["longAccount"]),
             "short": dec(r["shortAccount"]), "ratio": dec(r["longShortRatio"])}
            for r in _rows(payload)]
    return sorted(rows, key=lambda r: r["ts"])


def binance_taker_rows(payload):
    rows = [{"ts": _ts(r["timestamp"]), "ratio": dec(r["buySellRatio"]),
             "buy": dec(r["buyVol"]), "sell": dec(r["sellVol"])} for r in _rows(payload)]
    return sorted(rows, key=lambda r: r["ts"])


def binance_oi_hist(payload):
    rows = [{"ts": _ts(r["timestamp"]), "oi": dec(r["sumOpenInterest"]),
             "oi_value": dec(r["sumOpenInterestValue"])} for r in _rows(payload)]
    return sorted(rows, key=lambda r: r["ts"])


def binance_open_interest(payload):
    if not isinstance(payload, dict) or "openInterest" not in payload:
        raise NormaliseError("unexpected openInterest shape")
    return {"ts": _ts(payload["time"]), "oi": dec(payload["openInterest"])}


def binance_basis_rows(payload):
    rows = [{"ts": _ts(r["timestamp"]), "basis": dec(r["basis"]), "basis_rate": dec(r["basisRate"]),
             "futures_price": dec(r["futuresPrice"]), "index_price": dec(r["indexPrice"])}
            for r in _rows(payload)]
    return sorted(rows, key=lambda r: r["ts"])


def binance_premium(payload):
    if not isinstance(payload, dict) or "markPrice" not in payload:
        raise NormaliseError("unexpected premiumIndex shape")
    return {"ts": _ts(payload["time"]), "mark": dec(payload["markPrice"]),
            "index": dec(payload["indexPrice"]), "funding_rate": dec(payload["lastFundingRate"]),
            "next_funding": payload.get("nextFundingTime")}


def binance_klines(payload):
    """[open_time, open, high, low, close, ...] -> {ts, open, close}, ascending."""
    rows = [{"ts": _ts(k[0]), "open": dec(k[1]), "close": dec(k[4])} for k in _rows(payload)]
    return sorted(rows, key=lambda r: r["ts"])


# ---------- Bybit v5 ----------

def bybit_list(payload):
    if not isinstance(payload, dict) or payload.get("retCode") != 0:
        code = payload.get("retCode") if isinstance(payload, dict) else None
        msg = payload.get("retMsg") if isinstance(payload, dict) else None
        raise NormaliseError(f"bybit error {code}: {str(msg)[:120]}")
    items = (payload.get("result") or {}).get("list")
    if not isinstance(items, list):
        raise NormaliseError("bybit result.list missing")
    return items


def bybit_account_ratio(payload):
    """buyRatio/sellRatio are account shares; ratio = buy/sell (computed, 4 dp)."""
    rows = []
    for r in bybit_list(payload):
        buy, sell = dec(r["buyRatio"]), dec(r["sellRatio"])
        rows.append({"ts": _ts(r["timestamp"]), "long": buy, "short": sell,
                     "ratio": q(buy / sell) if buy is not None and sell else None})
    return sorted(rows, key=lambda r: r["ts"])


def bybit_open_interest(payload):
    """openInterest = both sides summed (Bybit docs); singleOpenInterest = one side."""
    rows = []
    for r in bybit_list(payload):
        both = dec(r["openInterest"])
        single = dec(r.get("singleOpenInterest"))
        rows.append({"ts": _ts(r["timestamp"]), "oi": single if single is not None else both,
                     "oi_both_sides": both, "single_side": single is not None})
    return sorted(rows, key=lambda r: r["ts"])


def bybit_ticker(payload):
    items = bybit_list(payload)
    if not items:
        raise NormaliseError("bybit ticker empty")
    t = items[0]
    single = dec(t.get("singleOpenInterest"))
    return {"ts": _ts(payload.get("time")), "mark": dec(t["markPrice"]), "index": dec(t["indexPrice"]),
            "last": dec(t["lastPrice"]), "funding_rate": dec(t.get("fundingRate")),
            "next_funding": t.get("nextFundingTime"),
            "oi": single if single is not None else dec(t["openInterest"]),
            "oi_both_sides": dec(t["openInterest"])}


def bybit_klines(payload):
    rows = [{"ts": _ts(k[0]), "open": dec(k[1]), "close": dec(k[4])} for k in bybit_list(payload)]
    return sorted(rows, key=lambda r: r["ts"])


# ---------- Hyperliquid ----------

def hl_asset_ctx(payload, coin):
    """metaAndAssetCtxs -> ctx for coin (index aligned with meta.universe)."""
    if not isinstance(payload, list) or len(payload) != 2:
        raise NormaliseError("unexpected metaAndAssetCtxs shape")
    universe = payload[0].get("universe") if isinstance(payload[0], dict) else None
    ctxs = payload[1]
    if not isinstance(universe, list) or not isinstance(ctxs, list):
        raise NormaliseError("unexpected metaAndAssetCtxs shape")
    for asset, ctx in zip(universe, ctxs):
        if asset.get("name") == coin:
            return {"mark": dec(ctx.get("markPx")), "oracle": dec(ctx.get("oraclePx")),
                    "oi": dec(ctx.get("openInterest")), "funding_rate": dec(ctx.get("funding")),
                    "premium": dec(ctx.get("premium")), "prev_day_px": dec(ctx.get("prevDayPx"))}
    raise NormaliseError(f"coin {coin} not in Hyperliquid universe")


# ---------- aggregation maths ----------

def pct_change(old, new):
    """(new - old) / old * 100, quantised to 0.01; None when old is 0/None."""
    if old is None or new is None or old == 0:
        return None
    return q((new - old) / old * 100, Q2)


def row_at(rows, ts):
    """Row with the exact timestamp, else None (no interpolation)."""
    for r in rows:
        if r["ts"] == ts:
            return r
    return None


def window_changes(rows, key, hours=(1, 4, 24), price_rows=None):
    """Change of rows[key] from (latest_ts - h) to latest_ts, plus price change at the same stamps.

    Price uses kline *open* at the same timestamps so both series are sampled at the same instants.
    """
    out = {}
    if not rows:
        return {f"{h}h": None for h in hours}
    last = rows[-1]
    for h in hours:
        then = row_at(rows, last["ts"] - h * HOUR_MS)
        entry = None
        if then is not None:
            entry = {"from_ts": then["ts"], "to_ts": last["ts"], "from": then[key], "to": last[key],
                     "pct": pct_change(then[key], last[key]), "price_pct": None}
            if price_rows:
                p0, p1 = row_at(price_rows, then["ts"]), row_at(price_rows, last["ts"])
                if p0 and p1:
                    entry["price_pct"] = pct_change(p0["open"], p1["open"])
        out[f"{h}h"] = entry
    return out


def series(rows, key, limit=30):
    return [[r["ts"], fmt(r[key])] for r in rows[-limit:] if r.get(key) is not None]
