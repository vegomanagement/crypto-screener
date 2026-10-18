"""Live funding check: /api/funding (server on 127.0.0.1:5090) vs the raw venue endpoints, field by field.

Raw endpoints are fetched right before and right after the server call; a live (predicted) value that
moves in between is reported as 'moved' with all three values rather than as a match or mismatch.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5090"
OUT = Path(__file__).parent / "funding_live_compare.json"
HL = "https://api.hyperliquid.xyz/info"


def get(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {},
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def raw(exchange, symbol):
    if exchange == "binance":
        p = get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}")
        h = get(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=3")
        info = [r for r in get("https://fapi.binance.com/fapi/v1/fundingInfo") if r["symbol"] == symbol]
        last = max(h, key=lambda r: r["fundingTime"])
        return {"predicted.rate": p["lastFundingRate"], "predicted.settles_at": p["nextFundingTime"],
                "last_settled.rate": last["fundingRate"], "last_settled.time": last["fundingTime"],
                "interval_hours": info[0]["fundingIntervalHours"] if info else "unlisted→8 default",
                "cap": info[0]["adjustedFundingRateCap"] if info else None, "mark_price": p["markPrice"]}
    if exchange == "bybit":
        t = get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}")["result"]["list"][0]
        h = get(f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={symbol}&limit=3")
        last = max(h["result"]["list"], key=lambda r: int(r["fundingRateTimestamp"]))
        return {"predicted.rate": t["fundingRate"], "predicted.settles_at": int(t["nextFundingTime"]),
                "last_settled.rate": last["fundingRate"], "last_settled.time": int(last["fundingRateTimestamp"]),
                "interval_hours": int(t["fundingIntervalHour"]), "cap": t["fundingCap"], "mark_price": t["markPrice"]}
    m = get(HL, {"type": "metaAndAssetCtxs"})
    i = [u["name"] for u in m[0]["universe"]].index(symbol)
    h = get(HL, {"type": "fundingHistory", "coin": symbol, "startTime": int(time.time() * 1000) - 3 * 3600_000})
    last = max(h, key=lambda r: r["time"])
    return {"predicted.rate": m[1][i]["funding"], "predicted.settles_at": (int(time.time() * 1000) // 3600_000 + 1) * 3600_000,
            "last_settled.rate": last["fundingRate"], "last_settled.time": last["time"], "interval_hours": 1,
            "cap": None, "mark_price": m[1][i]["markPx"]}


def pick(d, path):
    for part in path.split("."):
        d = (d or {}).get(part)
    return d.get("value") if isinstance(d, dict) and "value" in d else d


def same(a, b):
    if a is None or b is None:
        return a is None and b is None
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except ArithmeticError:
        return str(a) == str(b)


results = []
for exchange, symbol in [("binance", "BTCUSDT"), ("binance", "SOLUSDT"), ("bybit", "BTCUSDT"), ("bybit", "SOLUSDT"),
                         ("hyperliquid", "BTC"), ("hyperliquid", "SOL")]:
    before = raw(exchange, symbol)
    api = get(f"{BASE}/api/funding?" + urllib.parse.urlencode({"exchange": exchange, "symbol": symbol}))
    after = raw(exchange, symbol)
    fields = {}
    for key in before:
        ours = pick(api, key)
        if key == "interval_hours" and isinstance(before[key], str):
            verdict = "match" if ours == 8 and "default 8h" in api["interval_hours"]["source"] else "MISMATCH"
        elif same(ours, before[key]) or same(ours, after[key]):
            verdict = "match"
        elif key in ("predicted.rate", "mark_price") and not same(before[key], after[key]):
            verdict = "moved"
        else:
            verdict = "MISMATCH"
        fields[key] = {"api": ours, "raw_before": before[key], "raw_after": after[key], "verdict": verdict}
    cv = api.get("cross_venue") or {}
    row = {"exchange": exchange, "symbol": symbol, "fields": fields,
           "annualized": api["annualized"], "cross_venue": cv.get("rows"), "missing": api["missing"],
           "predicted_note": api["predicted"]["note"] if api.get("predicted") else None}
    results.append(row)
    print(exchange, symbol, {k: v["verdict"] for k, v in fields.items()},
          "cross:", [(r["venue"], r["rate"], r["stale"]) for r in cv.get("rows") or []])
OUT.write_text(json.dumps({"captured_at_ms": int(time.time() * 1000), "results": results}, indent=1))
bad = [(r["exchange"], r["symbol"], k) for r in results for k, v in r["fields"].items() if v["verdict"] == "MISMATCH"]
print("mismatches:", bad)
