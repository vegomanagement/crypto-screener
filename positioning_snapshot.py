"""Assemble a per-symbol positioning snapshot from raw venue responses (pure; no I/O).

``raw`` maps a request key to the decoded JSON payload or to an Exception raised while
fetching it. Failures never abort the snapshot: the affected section becomes null + reason.
"""
from __future__ import annotations

import positioning as P
from positioning_signals import derive_signals

BN = "binance"
BY = "bybit"
HL = "hyperliquid"

SOURCES = {
    "top_account": "GET fapi.binance.com/futures/data/topLongShortAccountRatio",
    "top_position": "GET fapi.binance.com/futures/data/topLongShortPositionRatio",
    "global_account": "GET fapi.binance.com/futures/data/globalLongShortAccountRatio",
    "taker": "GET fapi.binance.com/futures/data/takerlongshortRatio",
    "oi_hist": "GET fapi.binance.com/futures/data/openInterestHist (1h)",
    "oi_now": "GET fapi.binance.com/fapi/v1/openInterest",
    "basis": "GET fapi.binance.com/futures/data/basis (PERPETUAL)",
    "premium": "GET fapi.binance.com/fapi/v1/premiumIndex",
    "klines": "GET fapi.binance.com/fapi/v1/klines (1h)",
    "by_account_ratio": "GET api.bybit.com/v5/market/account-ratio",
    "by_oi_hist": "GET api.bybit.com/v5/market/open-interest (1h)",
    "by_ticker": "GET api.bybit.com/v5/market/tickers",
    "by_klines": "GET api.bybit.com/v5/market/kline (60)",
    "hl_ctxs": "POST api.hyperliquid.xyz/info metaAndAssetCtxs",
}
NO_TOP = "venue publishes no top-trader ratio (only Binance does)"


def _get(raw, key, fn, *args):
    """(normalised, error_reason)."""
    payload = raw.get(key)
    if payload is None:
        return None, "not fetched"
    if isinstance(payload, Exception):
        return None, f"fetch failed: {str(payload)[:160]}"
    try:
        return fn(payload, *args), None
    except (P.NormaliseError, KeyError, TypeError, IndexError) as exc:
        return None, f"unexpected response: {str(exc)[:160]}"


def _ratio_metric(raw, key, fn, venue, period, source_key=None):
    rows, err = _get(raw, key, fn)
    src = SOURCES[source_key or key]
    if err or not rows:
        return P.missing(err or "empty response", source=src, venue=venue, period=period)
    last = rows[-1]
    return P.metric(last["ratio"], source=src, venue=venue, timestamp=last["ts"], period=period,
                    long=last["long"], short=last["short"], series=P.series(rows, "ratio"),
                    long_series=P.series(rows, "long"))


def _oi_section(venue, period, current, hist, klines, err, cur_src, mark):
    if current is None:
        cur = P.missing(err or "current OI unavailable", source=cur_src, venue=venue)
    else:
        usd = P.q(current["oi"] * mark, P.Q2) if mark is not None and current["oi"] is not None else None
        cur = P.metric(current["oi"], source=cur_src, venue=venue, timestamp=current.get("ts"),
                       period="live", unit="base asset (one side)", value_usd=usd,
                       oi_both_sides=current.get("oi_both_sides"))
    if hist:
        changes = {}
        for win, entry in P.window_changes(hist, "oi", price_rows=klines).items():
            if entry is None:
                changes[win] = P.missing("history does not reach this window", source=SOURCES["oi_hist"]
                                         if venue == BN else SOURCES["by_oi_hist"], venue=venue, period=win)
            else:
                changes[win] = P.metric(entry["pct"], source=(SOURCES["oi_hist"] if venue == BN else
                                        SOURCES["by_oi_hist"]), venue=venue, timestamp=entry["to_ts"],
                                        period=win, unit="%", from_ts=entry["from_ts"],
                                        from_oi=entry["from"], to_oi=entry["to"], price_pct=entry["price_pct"])
        series = P.series(hist, "oi")
    else:
        reason = ("Hyperliquid has no public OI history endpoint" if venue == HL
                  else "OI history unavailable")
        changes = {w: P.missing(reason, source=cur_src, venue=venue, period=w) for w in ("1h", "4h", "24h")}
        series = []
    return {"current": cur, "changes": changes, "series": series}


def build_snapshot(exchange, symbol, period, raw, *, now_ms, coin=None,
                   liquidations=None, whales=None, flows=None):
    snap = {"exchange": exchange, "symbol": symbol, "period": period, "generated_at": now_ms,
            "coin": coin, "warnings": []}
    if exchange == BN:
        snap["top_traders"] = {
            "account": _ratio_metric(raw, "top_account", P.binance_ratio_rows, BN, period),
            "position": _ratio_metric(raw, "top_position", P.binance_ratio_rows, BN, period)}
        snap["crowd"] = _ratio_metric(raw, "global_account", P.binance_ratio_rows, BN, period)
        rows, err = _get(raw, "taker", P.binance_taker_rows)
        if rows:
            last = rows[-1]
            snap["taker"] = P.metric(last["ratio"], source=SOURCES["taker"], venue=BN, timestamp=last["ts"],
                                     period=period, buy_volume=last["buy"], sell_volume=last["sell"],
                                     unit="base asset", series=P.series(rows, "ratio"))
        else:
            snap["taker"] = P.missing(err or "empty response", source=SOURCES["taker"], venue=BN, period=period)
        premium, perr = _get(raw, "premium", P.binance_premium)
        basis_rows, berr = _get(raw, "basis", P.binance_basis_rows)
        hist, herr = _get(raw, "oi_hist", P.binance_oi_hist)
        klines, _ = _get(raw, "klines", P.binance_klines)
        current, cerr = _get(raw, "oi_now", P.binance_open_interest)
        mark = premium["mark"] if premium else None
        snap["open_interest"] = _oi_section(BN, period, current, hist, klines,
                                            cerr or herr, SOURCES["oi_now"], mark)
        if basis_rows:
            b = basis_rows[-1]
            snap["basis"] = P.metric(b["basis"], source=SOURCES["basis"], venue=BN, timestamp=b["ts"],
                                     period=period, basis_rate=b["basis_rate"], futures_price=b["futures_price"],
                                     index_price=b["index_price"],
                                     funding_rate=premium["funding_rate"] if premium else None,
                                     funding_source=SOURCES["premium"], mark=mark,
                                     next_funding=premium["next_funding"] if premium else None)
        else:
            snap["basis"] = P.missing(berr or "empty response", source=SOURCES["basis"], venue=BN, period=period)
        if perr:
            snap["warnings"].append(f"premiumIndex: {perr}")
    elif exchange == BY:
        snap["top_traders"] = {"account": P.missing(NO_TOP, source="bybit", venue=BY, period=period),
                               "position": P.missing(NO_TOP, source="bybit", venue=BY, period=period)}
        snap["crowd"] = _ratio_metric(raw, "by_account_ratio", P.bybit_account_ratio, BY, period)
        snap["taker"] = P.missing("Bybit has no public taker buy/sell ratio endpoint", source="bybit",
                                  venue=BY, period=period)
        ticker, terr = _get(raw, "by_ticker", P.bybit_ticker)
        hist, herr = _get(raw, "by_oi_hist", P.bybit_open_interest)
        klines, _ = _get(raw, "by_klines", P.bybit_klines)
        snap["open_interest"] = _oi_section(BY, period, ticker, hist, klines, terr or herr,
                                            SOURCES["by_ticker"], ticker["mark"] if ticker else None)
        if ticker:
            snap["basis"] = P.metric(ticker["mark"] - ticker["index"], source=SOURCES["by_ticker"] +
                                     " (markPrice − indexPrice)", venue=BY, timestamp=ticker["ts"], period="live",
                                     basis_rate=P.q((ticker["mark"] - ticker["index"]) / ticker["index"], P.Q4 / 100)
                                     if ticker["index"] else None, mark=ticker["mark"], index_price=ticker["index"],
                                     funding_rate=ticker["funding_rate"], funding_source=SOURCES["by_ticker"],
                                     next_funding=ticker["next_funding"])
        else:
            snap["basis"] = P.missing(terr, source=SOURCES["by_ticker"], venue=BY)
    else:
        none = "Hyperliquid publishes no long/short ratio"
        snap["top_traders"] = {"account": P.missing(NO_TOP, source=HL, venue=HL, period=period),
                               "position": P.missing(NO_TOP, source=HL, venue=HL, period=period)}
        snap["crowd"] = P.missing(none, source=HL, venue=HL, period=period)
        snap["taker"] = P.missing("Hyperliquid has no public taker ratio endpoint", source=HL, venue=HL)
        ctx, err = _get(raw, "hl_ctxs", P.hl_asset_ctx, coin or symbol)
        cur = {"oi": ctx["oi"], "ts": now_ms} if ctx else None
        snap["open_interest"] = _oi_section(HL, period, cur, None, None, err, SOURCES["hl_ctxs"],
                                            ctx["mark"] if ctx else None)
        if ctx:
            snap["basis"] = P.metric(ctx["mark"] - ctx["oracle"], source=SOURCES["hl_ctxs"] +
                                     " (markPx − oraclePx)", venue=HL, timestamp=now_ms, period="live",
                                     premium=ctx["premium"], mark=ctx["mark"], index_price=ctx["oracle"],
                                     funding_rate=ctx["funding_rate"], funding_source=SOURCES["hl_ctxs"],
                                     funding_interval="1h")
        else:
            snap["basis"] = P.missing(err, source=SOURCES["hl_ctxs"], venue=HL)
    snap["liquidations"] = liquidations
    snap["whales"] = whales
    snap["exchange_flows"] = flows
    snap["signals"] = derive_signals(snap)
    return snap
