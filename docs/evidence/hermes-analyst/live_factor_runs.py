"""Live factor-scored analyst runs against a local server (127.0.0.1:5090).

Writes factor_run_<exchange>_<symbol>_<interval>[_<n>].json per run and factor_summary.json.
The server reads the Hermes key from its own environment; this script never sees it.
Usage: python live_factor_runs.py [base_url] [tag]
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5090"
TAG = sys.argv[2] if len(sys.argv) > 2 else ""
OUT = Path(__file__).parent
RUNS = [("binance", "BTCUSDT", "15"), ("binance", "BTCUSDT", "60"), ("binance", "ETHUSDT", "15"),
        ("binance", "SOLUSDT", "5"), ("bybit", "ETHUSDT", "15"), ("bybit", "SOLUSDT", "15"),
        ("hyperliquid", "BTC", "15"), ("hyperliquid", "SOL", "60")]
if len(sys.argv) > 3:  # optional explicit list: exchange:symbol:interval,...
    RUNS = [tuple(x.split(":")) for x in sys.argv[3].split(",")]


def post(exchange, symbol, interval):
    body = json.dumps({"exchange": exchange, "symbol": symbol, "interval": interval, "force": True}).encode()
    req = urllib.request.Request(BASE + "/api/agent/analysis", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


summary_path = OUT / "factor_summary.json"
summary = json.loads(summary_path.read_text()) if summary_path.exists() else []
for exchange, symbol, interval in RUNS:
    started = time.time()
    code, data = post(exchange, symbol, interval)
    if code == 429:
        time.sleep(int(data.get("retry_after_s", 30)) + 1)
        started = time.time()
        code, data = post(exchange, symbol, interval)
    wall = round(time.time() - started, 1)
    name = f"factor_run_{exchange}_{symbol}_{interval}{'_' + TAG if TAG else ''}.json"
    (OUT / name).write_text(json.dumps(data, indent=1, ensure_ascii=False))
    if code != 200:
        row = {"exchange": exchange, "symbol": symbol, "interval": interval, "http": code, "error": data}
    else:
        a, sp = data["analysis"], data["analysis"]["split"]
        fs = a["factor_scores"]
        row = {"exchange": exchange, "symbol": symbol, "interval": interval, "tag": TAG, "file": name,
               "status": data["status"], "wall_s": wall, "model_latency_ms": data["latency_ms"],
               "snapshot_ms": data["snapshot_ms"], "attempts": data["attempts"],
               "long_pct": a["long_pct"], "short_pct": a["short_pct"], "p_long": sp["p_long"],
               "computed_bias": a["bias"], "agent_bias": a["agent_bias"],
               "agent_net": sp["agent_net"], "baseline_net": sp["baseline_net"], "net": sp["net"],
               "factors": len(fs), "factors_long": sum(f["direction"] == "long" for f in fs),
               "factors_short": sum(f["direction"] == "short" for f in fs),
               "points_long": sp["agent_long_points"], "points_short": sp["agent_short_points"],
               "funding_factors": sum(any(s.startswith("funding.") for s in f["source_fields"]) for f in fs),
               "disagreement": (a.get("disagreement") or {}).get("severity"),
               "levels": len(a["levels"]), "warnings": data["warnings"],
               "missing": sorted(data["freshness"]["missing"])}
    summary.append(row)
    print(json.dumps({k: row.get(k) for k in ("exchange", "symbol", "interval", "status", "http", "p_long",
                                               "long_pct", "computed_bias", "agent_bias", "factors",
                                               "funding_factors", "model_latency_ms", "disagreement")}),
          flush=True)
    summary_path.write_text(json.dumps(summary, indent=1, ensure_ascii=False))
