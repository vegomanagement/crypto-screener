"""Cross-check /ui grouped-indicator numbers against Binance USDⓈ-M futures REST klines.

Usage: python ig_crosscheck.py <evidence dir>  (reads live-models.json, writes crosscheck.json)
"""
import json
import sys

import requests

OUT = sys.argv[1]
FAPI = "https://fapi.binance.com/fapi/v1/klines"
TF = {"D": "1d", "W": "1w", "M": "1M", "3M": "1M", "Y": "1M"}
IV = {"15": "15m", "60": "1h", "5": "5m"}


def klines(symbol, interval, **params):
    response = requests.get(FAPI, params={"symbol": symbol, "interval": interval, **params}, timeout=20)
    response.raise_for_status()
    return [{"t": int(r[0]) // 1000, "o": float(r[1]), "h": float(r[2]), "l": float(r[3])} for r in response.json()]


def main():
    models = json.load(open(f"{OUT}/live-models.json"))
    report, failures = [], 0
    cache = {}
    for case in models:
        sym = case["symbol"]
        for tf in ("1d", "1w", "1M"):
            if (sym, tf) not in cache:
                cache[(sym, tf)] = {k["t"]: k for k in klines(sym, tf, limit=40 if tf != "1d" else 400)}
        rows = []
        for level in case["opens"]:
            if level["kind"] == "open":
                tf = TF[level["key"]]
                ref = cache[(sym, tf)].get(level["startTime"])
                field, expected = "open", ref and ref["o"]
                ref_time = level["startTime"]
            else:
                tf = {"pd": "1d", "pw": "1w", "pm": "1M"}[level["key"][:2]]
                ref = cache[(sym, tf)].get(level["sourceTime"])
                field = "high" if level["key"].endswith("h") else "low"
                expected = ref and (ref["h"] if field == "high" else ref["l"])
                ref_time = level["sourceTime"]
            ok = expected is not None and abs(expected - level["price"]) < 1e-9
            failures += not ok
            rows.append({"level": level["name"], "kind": level["kind"], "current": level["current"], "ui": level["price"],
                         "binance": expected, "binance_kline": f"{tf} {ref_time}", "field": field, "match": ok})
        # Sessions: high/low = max/min over Binance intraday klines whose open time is inside the window.
        intraday = klines(sym, IV[case["interval"]], limit=500, endTime=case["lastBar"] * 1000 + 999)
        sess_rows = []
        for inst in case["sessions"]:
            inside = [k for k in intraday if inst["start"] <= k["t"] < inst["end"] and k["t"] <= case["lastBar"]]
            if not inside or inside[0]["t"] < case["firstBar"]:
                inside = [k for k in inside if k["t"] >= case["firstBar"]]
            hi, lo = max(k["h"] for k in inside), min(k["l"] for k in inside)
            ok = abs(hi - inst["high"]) < 1e-9 and abs(lo - inst["low"]) < 1e-9 and inside[0]["t"] == inst["firstTime"] and inside[-1]["t"] == inst["lastTime"]
            failures += not ok
            sess_rows.append({"session": inst["name"], "start": inst["start"], "end": inst["end"], "bars": len(inside), "ui_high": inst["high"], "binance_high": hi,
                              "ui_low": inst["low"], "binance_low": lo, "ended": inst["ended"], "match": ok})
        report.append({"case": f"{sym} {case['interval']}m", "lastBar": case["lastBar"], "levels": rows, "sessions": sess_rows,
                       "warnings": case["warnings"]})
    json.dump({"failures": failures, "report": report}, open(f"{OUT}/crosscheck.json", "w"), indent=1)
    for item in report:
        lv, ss = item["levels"], item["sessions"]
        print(item["case"], "levels", sum(r["match"] for r in lv), "/", len(lv), "sessions", sum(r["match"] for r in ss), "/", len(ss))
        for r in lv:
            if r["current"]:
                print("   ", r["level"], r["ui"], "vs", r["binance"], r["binance_kline"], "OK" if r["match"] else "MISMATCH")
    print("failures", failures)


if __name__ == "__main__":
    main()
