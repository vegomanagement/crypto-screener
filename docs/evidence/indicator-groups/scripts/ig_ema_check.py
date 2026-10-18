"""Independent EMA 7/14/21 (SMA seed) from Binance REST closes over the same 300 bars the pane used."""
import json
import sys
import requests
E = sys.argv[1]
models = json.load(open(f"{E}/live-models.json"))
out = []
for m in models:
    iv = {"15": "15m", "60": "1h", "5": "5m"}[m["interval"]]
    rows = requests.get("https://fapi.binance.com/fapi/v1/klines", params={"symbol": m["symbol"], "interval": iv, "startTime": m["firstBar"] * 1000, "endTime": m["lastBar"] * 1000, "limit": 500}, timeout=20).json()
    closes = [float(r[4]) for r in rows]
    res = []
    for period, (t, ui) in zip((7, 14, 21), m["ribbon"]):
        v = sum(closes[:period]) / period
        a = 2 / (period + 1)
        for c in closes[period:]:
            v = a * c + (1 - a) * v
        res.append({"period": period, "ui": ui, "rest": v, "abs_diff": abs(ui - v)})
    out.append({"case": f"{m['symbol']} {iv}", "bars_rest": len(rows), "bars_ui": m["bars"], "ema": res})
    print(out[-1]["case"], len(rows), [f"EMA{r['period']} ui={r['ui']:.4f} rest={r['rest']:.4f} diff={r['abs_diff']:.2e}" for r in res])
json.dump(out, open(f"{E}/ema-crosscheck.json", "w"), indent=1)
