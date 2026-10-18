"""Strategy registry for the paper journal (forward test) — the pluggable part.

Interface (what a strategy plugs in)
------------------------------------
    fn(candles_by_tf: dict[str, list[candle]], params: dict) -> list[setup]

``candles_by_tf`` maps a timeframe code (``market_candles.INTERVAL_MS`` keys: '240', 'D', ...) to
CLOSED candles ``{ts, close_ts, o, h, l, c, v}`` oldest first, from the strategy's
``history_start`` to the last closed bar. The function must be deterministic and causal (its
output for a prefix of the candles must equal what it says about that prefix when given more):
the journal calls it on every cycle with the whole history and keeps only what is new.

Each returned setup is a dict:
    key          str, stable identity of the idea across cycles (e.g. the POI zone)
    status       'resting' (order planned, not filled) | 'open' (filled, still running) | 'closed'
    side         'long' | 'short'
    knowable_ts  ms, the bar close at which the resting setup became knowable (resting only)
    entry, stop, target   planned prices (resting) or the actual fill/stop/target (open/closed)
    entry_type   'maker' (limit fill) | 'taker' (market fill)
    fill_ts      ms, open time of the bar that filled (open/closed); fill_close_ts = its close
    be_ts        ms or None, close of the bar at which the stop moved to break-even
    exit_ts, exit_reason ('target' | 'stop' | 'timeout'), r (gross R), partial, partial_ts  (closed)
    inputs       JSON-able snapshot of what the decision used (zone, HTF bar, ATR, trend, ...)
The journal adds everything else: forward-only recording, warm-up tags, lifecycle, costs, stats.

Versioning: a strategy version = sha256(frozen params JSON + code version); the code version
hashes the source of the modules/functions listed in ``code``. Any change creates a new version
in the store; old versions and their rows are never edited.

Register a new strategy (e.g. the trend/momentum research) with ``register(...)`` at import time
of a module listed in ``PLUGIN_MODULES`` (env PAPER_STRATEGY_MODULES, comma-separated).
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os
from datetime import UTC, datetime

import smc
import swing_model as sm

REGISTRY: dict = {}
DEFAULT_UNIVERSE = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "ZECUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT", "1000PEPEUSDT",
                    "SUIUSDT", "NEARUSDT", "ADAUSDT", "LINKUSDT", "WLDUSDT", "AVAXUSDT", "UNIUSDT", "FILUSDT",
                    "BCHUSDT", "LTCUSDT", "AAVEUSDT", "DASHUSDT")       # docs/SWING_HTF_RESULT.md §1


def universe(env=None):
    raw = (env if env is not None else os.environ).get("PAPER_UNIVERSE", "")
    syms = [s.strip().upper() for s in raw.split(",") if s.strip()]
    return syms or list(DEFAULT_UNIVERSE)


def code_version(code):
    """sha256 over the source of every module / function in ``code`` (order-independent)."""
    parts = []
    for obj in code:
        name = getattr(obj, "__qualname__", None) or getattr(obj, "__name__", repr(obj))
        mod = getattr(obj, "__module__", None) or name
        parts.append(f"{mod}:{name}\n" + inspect.getsource(obj))
    return hashlib.sha256("\n".join(sorted(parts)).encode()).hexdigest()[:16]


def canonical(params):
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def params_hash(params, code_ver):
    return hashlib.sha256((canonical(params) + "|" + code_ver).encode()).hexdigest()[:16]


def register(strategy_id, fn, params, *, title, timeframes, lifecycle_tf, code=(), expectation=None,
             description="", history_start=None):
    """Add (or replace, same process) a strategy. ``timeframes``: the TFs ``fn`` needs;
    ``lifecycle_tf``: the TF whose closed bars drive the cycle (the entry TF)."""
    params = json.loads(canonical(params))          # frozen copy, JSON-normalised
    cv = code_version(tuple(code) + (fn,))
    REGISTRY[strategy_id] = {
        "id": strategy_id, "title": title, "fn": fn, "params": params, "timeframes": list(timeframes),
        "lifecycle_tf": lifecycle_tf, "code_version": cv, "hash": params_hash(params, cv),
        "expectation": expectation, "description": description,
        "history_start": history_start if history_start is not None else params.get("history_start"),
    }
    return REGISTRY[strategy_id]


def load_plugins(env=None):
    raw = (env if env is not None else os.environ).get("PAPER_STRATEGY_MODULES", "")
    for name in [m.strip() for m in raw.split(",") if m.strip()]:
        importlib.import_module(name)


# ─── SMC swing model: 1d → 4h, resting limit at the POI edge, POI stop, 2 R + break-even ─────────

def poi_key(poi):
    return f"{poi['kind']}:{poi['dir']}:{poi['ts']}:{poi['top']:.10g}:{poi['bottom']:.10g}"


def _side(d):
    return "long" if d > 0 else "short"


def smc_swing(candles_by_tf, params):
    """swing_model.scan (the backtest's own scanner) → journal setups."""
    ltf, htf = candles_by_tf[params["ltf"]], candles_by_tf[params["htf"]]
    if len(htf) < 2 or len(ltf) < 2:
        return []
    trades, state = sm.scan(ltf, htf, params["scan"])
    out = []

    def trade(t, status):
        be = t.get("be_i")
        row = {"key": poi_key(t["poi"]), "status": status, "side": _side(t["d"]), "entry": t["entry"],
               "stop": t["stop"], "target": t["target"], "entry_type": t["entry_type"], "fill_ts": t["fill_ts"],
               "fill_close_ts": ltf[t["i_fill"]]["close_ts"],
               "be_ts": None if be is None else ltf[be]["close_ts"],
               "inputs": {"poi": t["poi"], "htf_close_ts": htf[t["htf_k"]]["close_ts"], "risk": t["risk"],
                          "risk_atr": t.get("risk_atr")}}
        if status == "closed":
            pi = t.get("partial_i")
            row.update(exit_ts=t["exit_ts"], exit_reason=t["exit"], r=t["r"], partial=bool(t["partial"]),
                       partial_ts=None if pi is None else ltf[pi]["close_ts"])
        return row

    out += [trade(t, "closed") for t in trades if t["r"] is not None]
    if state.get("mode") == "trade" and state["t"].get("exit") == "open":
        out.append(trade(state["t"], "open"))
    for o in state.get("resting", ()):
        poi = o["poi"]
        out.append({"key": poi_key(poi), "status": "resting", "side": _side(o["d"]), "knowable_ts": o["knowable_ts"],
                    "entry": o["edge"], "stop": o["stop"], "target": o["target"], "entry_type": "maker",
                    "inputs": {"poi": poi, "zone_confirm_ts": o["zone_confirm_ts"],
                               "eligible_from_ts": o["eligible_from_ts"], "htf_close_ts": o["htf_close_ts"],
                               "htf_atr": o["htf_atr"], "trend": o["trend"], "range_lo": o["range_lo"],
                               "range_hi": o["range_hi"], "ltf_close_ts": ltf[-1]["close_ts"],
                               "ltf_close": ltf[-1]["c"], "sweep_required": o["sweep_required"]}})
    return out


SMC_ID = "smc_1d4h_limit_poi_2r_be"
SMC_PARAMS = {
    "htf": "D", "ltf": "240",
    "history_start": int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000),   # as the backtest
    "scan": {**sm.DEFAULTS, "poi": "any", "idm": "none", "sweep": False, "entry": "limit", "stop": "poi",
             "target": "r2", "be": True},
    "smc": {"external": list(smc.EXTERNAL), "internal": list(smc.INTERNAL), "ob_lookback": smc.OB_LOOKBACK,
            "zone_max_age": smc.ZONE_MAX_AGE},
}
SMC_EXPECTATION = {
    "oos_net_r": 0.136, "oos_ci": [0.017, 0.263], "oos_n": 422, "oos_hit": 0.35, "is_net_r": -0.016, "is_n": 739,
    "source": "docs/SWING_HTF_RESULT.md (OOS 2024-10-03 → 2026-10-18, 20 coins, net of fees and funding)",
    "caveat": "post-hoc pick, flat in-sample; failed Holm and yearly folds — this forward test is the check",
}


def _register_builtin():
    import signal_features as sf
    register(SMC_ID, smc_swing, SMC_PARAMS, title="SMC 1d→4h · limit at POI edge · POI stop · 2R + BE after 1R",
             timeframes=("D", "240"), lifecycle_tf="240", code=(sm, smc, sf.atr, poi_key, _side),
             expectation=SMC_EXPECTATION,
             description="HTF (1d) trend + unmitigated OB/FVG in discount/premium; resting limit at the zone edge "
                         "on 4h, stop beyond the zone ± 0.1 × 1d ATR14, target 2 R, stop to entry after 1 R, "
                         "exit after 300 bars. Same scanner as scripts/backtest_swing_universe.py.")


_register_builtin()
