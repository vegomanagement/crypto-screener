"""Cross-check live Opens / previous H-L and Sessions against each venue's own REST klines.

Bybit: /v5/market/kline (D, W, M, 15, 60). Hyperliquid: candleSnapshot 1d/15m. Hyperliquid's
native 1w opens Thursday 00:00 UTC and its 1M is 30-day buckets, so Monday weeks and calendar
months are checked against HL's own 1d klines (and the native 1w/1M are recorded to show why).
Binance: /fapi/v1/klines 1d/1w/1M. Usage: python venue_crosscheck.py <evidence dir>
"""
import json
import sys
import time
import urllib.request
from datetime import UTC, datetime

OUT = sys.argv[1]
DAY = 86400


def get(url, payload=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def bybit(symbol, interval, **kw):
    q = "&".join(f"{k}={v}" for k, v in {"category": "linear", "symbol": symbol, "interval": interval, "limit": 1000, **kw}.items())
    rows = get(f"https://api.bybit.com/v5/market/kline?{q}")["result"]["list"]
    return {int(r[0]) // 1000: {"o": float(r[1]), "h": float(r[2]), "l": float(r[3])} for r in rows}


def binance(symbol, interval, **kw):
    q = "&".join(f"{k}={v}" for k, v in {"symbol": symbol, "interval": interval, "limit": 1000, **kw}.items())
    rows = get(f"https://fapi.binance.com/fapi/v1/klines?{q}")
    return {int(r[0]) // 1000: {"o": float(r[1]), "h": float(r[2]), "l": float(r[3])} for r in rows}


def hl(coin, interval, start, end):
    rows = get("https://api.hyperliquid.xyz/info", {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
                                                                                      "startTime": start * 1000, "endTime": end * 1000}})
    return {int(r["t"]) // 1000: {"o": float(r["o"]), "h": float(r["h"]), "l": float(r["l"])} for r in rows}


def monday(t):
    d = t // DAY * DAY
    return d - datetime.fromtimestamp(d, UTC).weekday() * DAY


def month_start(t):
    d = datetime.fromtimestamp(t, UTC)
    return int(datetime(d.year, d.month, 1, tzinfo=UTC).timestamp())


def from_days(days, start, end):
    """Venue's own complete daily klines [start, end) → o/h/l (None if any day is missing)."""
    keys = list(range(start, end, DAY))
    if not all(k in days for k in keys):
        return None
    return {"o": days[keys[0]]["o"], "h": max(days[k]["h"] for k in keys), "l": min(days[k]["l"] for k in keys)}


def refs(case):
    ex, sym, now = case["exchange"], case["symbol"], int(time.time())
    if ex == "bybit":
        return {"D": bybit(sym, "D"), "W": bybit(sym, "W", limit=200), "M": bybit(sym, "M", limit=100),
                "intraday": bybit(sym, case["interval"], end=case["lastBar"] * 1000 + 999)}
    if ex == "binance":
        return {"D": binance(sym, "1d"), "W": binance(sym, "1w", limit=200), "M": binance(sym, "1M", limit=100),
                "intraday": binance(sym, f"{case['interval']}m" if case["interval"] != "60" else "1h", endTime=case["lastBar"] * 1000 + 999)}
    days = hl(sym, "1d", now - 800 * DAY, now)
    out = {"D": days, "W": {}, "M": {}, "nativeW": hl(sym, "1w", now - 120 * DAY, now), "nativeM": hl(sym, "1M", now - 400 * DAY, now),
           "intraday": hl(sym, "15m" if case["interval"] == "15" else "1h", case["firstBar"] - 3600, case["lastBar"] + 1)}
    for t in sorted(days):
        w, m = monday(t), month_start(t)
        nxt = datetime.fromtimestamp(m, UTC)
        m_end = int(datetime(nxt.year + (nxt.month == 12), nxt.month % 12 + 1, 1, tzinfo=UTC).timestamp())
        if w not in out["W"]:
            agg = from_days(days, w, w + 7 * DAY) or ({"o": days[w]["o"], "h": None, "l": None} if w in days else None)
            if agg:
                out["W"][w] = agg
        if m not in out["M"]:
            agg = from_days(days, m, m_end) or ({"o": days[m]["o"], "h": None, "l": None} if m in days else None)
            if agg:
                out["M"][m] = agg
    return out


def main():
    models = json.load(open(f"{OUT}/venues-live-models.json"))
    report, failures = [], 0
    for case in models:
        r = refs(case)
        rows = []
        for lv in case["opens"]:
            if lv["kind"] == "open":
                tf = {"D": "D", "W": "W", "M": "M", "3M": "M", "6M": "M", "Y": "M"}[lv["key"]]
                ref, t, field = r[tf].get(lv["startTime"]), lv["startTime"], "o"
            else:
                tf = {"pd": "D", "pw": "W", "pm": "M"}[lv["key"][:2]]
                ref, t = r[tf].get(lv["sourceTime"]), lv["sourceTime"]
                field = "h" if lv["key"].endswith("h") else "l"
            expected = ref and ref[field]
            ok = expected is not None and abs(expected - lv["price"]) < 1e-9
            failures += not ok
            rows.append({"level": lv["name"], "current": lv["current"], "ui": lv["price"], "venue": expected, "kline": f"{tf} {t}",
                         "kline_utc": datetime.fromtimestamp(t, UTC).strftime("%a %Y-%m-%d"), "match": ok})
        sess = []
        intraday = r["intraday"]
        for inst in case["sessions"]:
            inside = sorted(k for k in intraday if inst["start"] <= k < inst["end"] and case["firstBar"] <= k <= case["lastBar"])
            if not inside:
                continue
            hi, lo = max(intraday[k]["h"] for k in inside), min(intraday[k]["l"] for k in inside)
            ok = hi == inst["high"] and lo == inst["low"] and inside[0] == inst["firstTime"] and inside[-1] == inst["lastTime"]
            item = {"session": inst["name"], "start_utc": datetime.fromtimestamp(inst["start"], UTC).isoformat(), "bars": len(inside),
                    "ui_high": inst["high"], "venue_high": hi, "ui_low": inst["low"], "venue_low": lo, "match": ok}
            if isinstance(inst.get("or"), dict):
                o = inst["or"]
                or_bars = [k for k in inside if k <= o["lastOrTime"]]
                ot, ob = max(intraday[k]["h"] for k in or_bars), min(intraday[k]["l"] for k in or_bars)
                item["opening_range"] = {"bars": o["bars"], "venue_bars": len(or_bars), "ui_top": o["top"], "venue_top": ot, "ui_btm": o["btm"], "venue_btm": ob,
                                         "last_or_utc": datetime.fromtimestamp(o["lastOrTime"], UTC).strftime("%H:%M")}
                ok = ok and ot == o["top"] and ob == o["btm"] and len(or_bars) == o["bars"]
                item["match"] = ok
            failures += not ok
            sess.append(item)
        extra = {}
        if case["exchange"] == "hyperliquid":
            extra["native_1w_open_weekdays"] = sorted({datetime.fromtimestamp(t, UTC).strftime("%a") for t in r["nativeW"]})
            extra["native_1M_open_days"] = [datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d") for t in sorted(r["nativeM"])[-4:]]
        report.append({"case": f"{case['exchange']} {case['symbol']} {case['interval']}m", "lastBar": case["lastBar"], "levels": rows, "sessions": sess,
                       "warnings": case["warnings"], "htfErrors": case.get("htfErrors"), **extra})
    json.dump({"failures": failures, "report": report}, open(f"{OUT}/venues-crosscheck.json", "w"), indent=1)
    for item in report:
        lv, ss = item["levels"], item["sessions"]
        print(item["case"], "levels", sum(x["match"] for x in lv), "/", len(lv), "sessions", sum(x["match"] for x in ss), "/", len(ss),
              {k: item[k] for k in ("native_1w_open_weekdays", "native_1M_open_days") if k in item})
        for x in lv:
            if not x["match"] or x["current"]:
                print("   ", x["level"], x["ui"], "vs", x["venue"], x["kline_utc"], "OK" if x["match"] else "MISMATCH")
        for x in ss:
            if not x["match"]:
                print("    SESSION MISMATCH", x)
        if item["warnings"]:
            print("    warnings:", item["warnings"][:300])
    print("failures", failures)


if __name__ == "__main__":
    main()
