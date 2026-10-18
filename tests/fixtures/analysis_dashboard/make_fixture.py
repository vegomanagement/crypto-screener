"""Regenerate analysis_fixture.json: an /api/agent/analysis response (incl. snapshot) built
from the deterministic test providers in tests/test_analyst.py (no network).
Run: python tests/fixtures/analysis_dashboard/make_fixture.py
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import analyst  # noqa: E402
from tests.test_analyst_service import good, service  # noqa: E402

snap = analyst.build_snapshot("binance", "BTCUSDT", "15", __import__("tests.test_analyst", fromlist=["providers"]).providers())
out = service(lambda m, deadline=None: (json.dumps(good(snap)), "hermes-agent")).analyze("binance", "BTCUSDT", "15")
path = pathlib.Path(__file__).with_name("analysis_fixture.json")
path.write_text(json.dumps(out, indent=1, default=str, ensure_ascii=False))
print(path, len(path.read_text()))
