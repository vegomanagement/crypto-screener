"""Live run against the :5092 positioning server: keeps leases alive, logs whale polls and liquidations.

Usage: python positioning_live_run.py <minutes>
Writes JSON lines to docs/evidence/positioning/live_run.jsonl.
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:5092"
OUT = "/Users/maxbelov/Projects/wt/positioning-data/docs/evidence/positioning/live_run.jsonl"
MARKETS = [(v, s) for v in ("binance", "bybit") for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")]
minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 12


def get(path):
    t0 = time.time()
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read()), round(time.time() - t0, 3)


end = time.time() + minutes * 60
seen_polls = 0
with open(OUT, "a") as fh:
    fh.write(json.dumps({"event": "start", "at": int(time.time() * 1000), "minutes": minutes}) + "\n")
    while time.time() < end:
        for coin in ("BTC", "ETH"):
            w, dt = get(f"/api/positioning/whales?coin={coin}&owner=live-run")
            if w.get("last_poll") and w["last_poll"]["poll_number"] > seen_polls and coin == "ETH":
                seen_polls = w["last_poll"]["poll_number"]
            fh.write(json.dumps({"event": "whales", "at": int(time.time() * 1000), "coin": coin, "latency_s": dt,
                                 "polls": w.get("polls"), "last_poll": w.get("last_poll"),
                                 "leaderboard": w.get("leaderboard"),
                                 "aggregate": {k: v for k, v in (w.get("aggregate") or {}).items()
                                               if k not in ("positions", "liquidation_levels")},
                                 "changes": w.get("changes", [])[:10], "status": w.get("status")}) + "\n")
        for venue, sym in MARKETS:
            s, dt = get(f"/api/positioning?exchange={venue}&symbol={sym}&period=1h&owner=live-run")
            liq = s.get("liquidations") or {}
            fh.write(json.dumps({"event": "liq", "at": int(time.time() * 1000), "venue": venue, "symbol": sym,
                                 "latency_s": dt, "windows": liq.get("windows"), "largest": liq.get("largest", [])[:3],
                                 "collector": liq.get("collector"), "coverage": liq.get("coverage")}) + "\n")
        fh.flush()
        print(time.strftime("%H:%M:%S"), "whale polls", seen_polls, flush=True)
        time.sleep(30)
    fh.write(json.dumps({"event": "end", "at": int(time.time() * 1000)}) + "\n")
