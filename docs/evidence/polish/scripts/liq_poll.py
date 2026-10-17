"""Keep the liquidation collector leased for N seconds and save the final aggregates (live restart check)."""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:5096/api/positioning?period=1h&owner=polish&exchange={}&symbol={}"
seconds, out = int(sys.argv[1]), sys.argv[2]
SYMBOLS = sys.argv[3].split(",") if len(sys.argv) > 3 else ["BTCUSDT"]
end = time.time() + seconds
snap = {}
while True:
    for venue in ("binance", "bybit"):
        for sym in SYMBOLS:
            with urllib.request.urlopen(BASE.format(venue, sym), timeout=30) as r:
                snap[f"{venue}:{sym}"] = json.load(r)["liquidations"]
    if time.time() >= end:
        break
    time.sleep(50)
json.dump({"at_ms": int(time.time() * 1000), "liquidations": snap}, open(out, "w"), indent=1)
for v, s in snap.items():
    w = (s.get("windows") or {}).get("24h") or {}
    print(v, "24h", {k: w.get(k) for k in ("long_count", "short_count", "total_usd", "covered_ms")}, "spans", s["coverage"]["spans"])
