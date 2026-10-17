"""Live fallback check: real market data, Hermes deliberately unconfigured → deterministic baseline."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from flask import Flask

import screener
from agent_api import register_agent_routes

app = Flask(__name__)
register_agent_routes(app, market=screener.fetch_market, confluence=screener.compute_confluence_score,
                      store_path=Path(tempfile.mkdtemp()) / "a.sqlite3", env={})
r = app.test_client().post("/api/agent/analysis", json={"exchange": "binance", "symbol": "BTCUSDT", "interval": "15"})
d = r.get_json()
out = {"http": r.status_code, "status": d["status"], "source": d["source"], "warnings": d["warnings"],
       "analysis": d["analysis"], "baseline_scores": {k: d["baseline"][k] for k in
                                                     ("long_score", "short_score", "long_verdict", "short_verdict")}}
(Path(__file__).parent / "fallback_binance_BTCUSDT_15.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
print(json.dumps({k: out[k] for k in ("http", "status", "source", "warnings", "baseline_scores")}, ensure_ascii=False))
print(out["analysis"]["long_pct"], out["analysis"]["bias"], len(out["analysis"]["levels"]))
