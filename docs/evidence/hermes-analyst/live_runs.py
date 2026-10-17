"""Live analyst runs against a local server (127.0.0.1:5090). Writes one JSON per run.

The server reads the Hermes key from its own environment; this script never sees it.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5090"
OUT = Path(__file__).parent
RUNS = [("binance", "BTCUSDT", "15"), ("binance", "ETHUSDT", "60"), ("bybit", "SOLUSDT", "15")]

summary = []
for exchange, symbol, interval in RUNS:
    body = json.dumps({"exchange": exchange, "symbol": symbol, "interval": interval, "force": True}).encode()
    req = urllib.request.Request(BASE + "/api/agent/analysis", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    started = time.time()
    with urllib.request.urlopen(req, timeout=150) as resp:
        data = json.loads(resp.read())
    wall = round(time.time() - started, 1)
    (OUT / f"run_{exchange}_{symbol}_{interval}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False))
    a = data["analysis"]
    summary.append({"exchange": exchange, "symbol": symbol, "interval": interval, "status": data["status"],
                    "wall_s": wall, "model_latency_ms": data["latency_ms"], "snapshot_ms": data["snapshot_ms"],
                    "attempts": data["attempts"], "bias": a["bias"], "long_pct": a["long_pct"],
                    "short_pct": a["short_pct"], "confidence": a["confidence"], "levels": len(a["levels"]),
                    "warnings": data["warnings"], "missing": data["freshness"]["missing"]})
    print(json.dumps(summary[-1], ensure_ascii=False))
(OUT / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
