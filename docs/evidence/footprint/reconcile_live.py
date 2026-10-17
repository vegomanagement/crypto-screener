"""Live reconciliation of /api/footprint data against venue klines (real public APIs).

Run from the repo root:  python docs/evidence/footprint/reconcile_live.py
Writes reconcile-*.json next to this file. Uses the production FootprintService (same code
path as GET /api/footprint), only with a larger per-call budget, then compares each
closed bar with the venue's own kline: Binance volume (k[5]), takerBuyBaseAssetVolume (k[9])
and number of trades (k[8]); Bybit kline volume; Hyperliquid candle v / n.
"""
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import exchange_markets  # noqa: E402
from footprint_data import FootprintService  # noqa: E402

OUT = Path(__file__).resolve().parent


def binance(symbol, interval, label, bars):
    svc = FootprintService(max_requests=55, binance_rate=(55, 60_000))
    rounds = []
    while True:
        t0 = time.time()
        out = svc.footprint("binance", symbol, interval, limit=bars, include_forming=False)
        rounds.append({"requests": out["requests"], "complete": out["coverage"]["complete"],
                       "partial": out["coverage"]["partial"], "missing": out["coverage"]["missing"],
                       "truncated": out["truncated"], "seconds": round(time.time() - t0, 1)})
        print(label, rounds[-1], flush=True)
        if not out["truncated"]:
            break
        time.sleep(61)  # let the rolling 60 req/min aggTrades window drain
    mapped = {"1": "1m", "5": "5m", "15": "15m"}[interval]
    first, last = out["bars"][0]["open_time"], out["bars"][-1]["open_time"]
    klines = exchange_markets._request("binance", "/fapi/v1/klines", params={
        "symbol": symbol, "interval": mapped, "startTime": first, "endTime": last, "limit": 1000})
    by_open = {int(k[0]): k for k in klines}
    rows, ok = [], 0
    for bar in out["bars"]:
        k = by_open[bar["open_time"]]
        checks = {
            "volume": Decimal(bar["total"]) == Decimal(k[5]),
            "taker_buy": Decimal(bar["buy_vol"]) == Decimal(k[9]),
            "levels_sum": sum(Decimal(lv["bid"]) + Decimal(lv["ask"]) for lv in bar["levels"]) == Decimal(bar["total"]),
        }
        info = {  # reported, not part of the volume acceptance
            "trades_equal": bar["trade_count"] == int(k[8]),
            "ohlc_equal": [Decimal(bar[x]) for x in ("open", "high", "low", "close")] == [Decimal(v) for v in k[1:5]],
            "service_kline_check": bar["kline_check"]["status"],
        }
        exact = all(checks.values()) and bar["coverage"] == "complete"
        ok += exact
        if not exact:
            info["boundary_aggregates"] = straddles(symbol, bar["open_time"], bar["close_time"])
        rows.append({"open_time": bar["open_time"], "coverage": bar["coverage"],
                     "footprint_total": bar["total"], "kline_volume": k[5],
                     "footprint_ask": bar["buy_vol"], "kline_taker_buy": k[9],
                     "footprint_trades": bar["trade_count"], "kline_trades": int(k[8]),
                     "levels": len(bar["levels"]), "poc": bar["poc"], "delta": bar["delta"], **checks, **info})
    report = {"venue": "binance", "symbol": symbol, "interval": mapped, "captured_at_ms": out["as_of"],
              "tick_size": out["tick_size"], "group_ticks": out["group_ticks"], "bars": len(rows),
              "bars_volume_and_taker_buy_exact": ok,
              "bars_trade_count_equal": sum(r["trades_equal"] for r in rows),
              "bars_ohlc_equal": sum(r["ohlc_equal"] for r in rows),
              "service_flags_agree": all((r["service_kline_check"] == "exact") == (r["volume"] and r["taker_buy"]) for r in rows),
              "fetch_rounds": rounds, "rows": rows}
    report["mismatch_pairs"] = mismatch_pairs(rows)
    report["window_sum_volume_exact"] = (sum(Decimal(r["footprint_total"]) for r in rows)
                                         == sum(Decimal(r["kline_volume"]) for r in rows))
    report["window_sum_taker_buy_exact"] = (sum(Decimal(r["footprint_ask"]) for r in rows)
                                            == sum(Decimal(r["kline_taker_buy"]) for r in rows))
    (OUT / f"reconcile-binance-{symbol}-{mapped}.json").write_text(json.dumps(report, indent=1))
    print(f"{label}: {ok}/{len(rows)} closed bars exact (volume + taker buy); trades equal "
          f"{report['bars_trade_count_equal']}/{len(rows)}; OHLC equal {report['bars_ohlc_equal']}/{len(rows)}; "
          f"service flags agree: {report['service_flags_agree']}")
    return report


def mismatch_pairs(rows):
    """Adjacent mismatching bars whose differences cancel: boundary attribution, not lost trades."""
    pairs, used = [], set()
    for left, right in zip(rows, rows[1:]):
        if left["volume"] and right["volume"] or left["open_time"] in used:
            continue
        dv = [Decimal(r["footprint_total"]) - Decimal(r["kline_volume"]) for r in (left, right)]
        db = [Decimal(r["footprint_ask"]) - Decimal(r["kline_taker_buy"]) for r in (left, right)]
        if dv[0] != 0 and dv[0] + dv[1] == 0:
            used.update((left["open_time"], right["open_time"]))
            pairs.append({"left": left["open_time"], "right": right["open_time"],
                          "volume_shift": format(dv[0].normalize(), "f"), "taker_buy_shift": format(db[0].normalize(), "f"),
                          "pair_volume_sum_exact": True, "pair_taker_buy_sum_exact": db[0] + db[1] == 0})
    return pairs


def straddles(symbol, open_ms, close_ms):
    """Multi-fill aggregates within 1s of either boundary: candidates for cross-bar attribution."""
    found = []
    for edge in (open_ms, close_ms + 1):
        rows = exchange_markets._request("binance", "/fapi/v1/aggTrades", params={
            "symbol": symbol, "startTime": edge - 1000, "endTime": edge - 1, "limit": 1000})
        found += [{"edge": edge, "a": r["a"], "T": r["T"], "p": r["p"], "q": r["q"], "fills": r["l"] - r["f"] + 1,
                   "m": r["m"]} for r in rows if r["l"] > r["f"] and edge - r["T"] <= 50]
    return found


def bybit_and_hyperliquid():
    svc = FootprintService()
    now = int(time.time() * 1000)
    reports = {}
    by = svc.footprint("bybit", "BTCUSDT", "1", limit=10)
    k = exchange_markets._request("bybit", "/v5/market/kline", params={
        "category": "linear", "symbol": "BTCUSDT", "interval": "1", "limit": 12})["result"]["list"]
    kvol = {int(r[0]): r[5] for r in k}
    reports["bybit"] = {"symbol": "BTCUSDT", "interval": "1m", "as_of": by["as_of"], "coverage": by["coverage"],
                        "warnings": by["warnings"], "rows": [
        {"open_time": b["open_time"], "coverage": b["coverage"], "forming": b["forming"], "footprint_total": b["total"],
         "kline_volume": kvol.get(b["open_time"]),
         "volume_equal": kvol.get(b["open_time"]) is not None and Decimal(b["total"]) == Decimal(kvol[b["open_time"]])}
        for b in by["bars"]]}
    hl = svc.footprint("hyperliquid", "BTC", "1", limit=10)
    candles = exchange_markets._request("hyperliquid", "/info", payload={"type": "candleSnapshot", "req": {
        "coin": "BTC", "interval": "1m", "startTime": now - 12 * 60_000, "endTime": now}})
    cv = {int(c["t"]): c for c in candles}
    reports["hyperliquid"] = {"symbol": "BTC", "interval": "1m", "as_of": hl["as_of"], "coverage": hl["coverage"],
                              "tick_size": hl["tick_size"], "warnings": hl["warnings"], "rows": [
        {"open_time": b["open_time"], "coverage": b["coverage"], "forming": b["forming"], "footprint_total": b["total"],
         "footprint_trades": b["trade_count"], "candle_v": cv.get(b["open_time"], {}).get("v"),
         "candle_n": cv.get(b["open_time"], {}).get("n")} for b in hl["bars"]]}
    for venue, rep in reports.items():
        (OUT / f"coverage-{venue}.json").write_text(json.dumps(rep, indent=1))
        print(venue, rep["coverage"], [(r["coverage"], r.get("volume_equal")) for r in rep["rows"]])
    return reports


if __name__ == "__main__":
    which = sys.argv[1:] or ["btc", "eth", "others"]
    if "btc" in which:
        binance("BTCUSDT", "1", "BTCUSDT 1m", 40)
    if "eth" in which:
        binance("ETHUSDT", "5", "ETHUSDT 5m", 30)
    if "others" in which:
        bybit_and_hyperliquid()
