#!/usr/bin/env python3
"""Re-runnable calibration backtest for the Hermes analyst split (stdlib + this repo only).

  python scripts/backtest_analyst.py fetch     # Binance klines/funding + alternative.me F&G → cache
  python scripts/backtest_analyst.py baseline  # replay baseline_net bar by bar, score, bucket
  python scripts/backtest_analyst.py hermes --n 12   # small live Hermes sample on past closed bars
  python scripts/backtest_analyst.py score     # score that sample under the old and current split

Scoring = analyst_store.evaluate_outcome: +1 ATR(14, chart interval) in the lean's direction
before −1 ATR against, within EVAL_HORIZON_BARS bars of the chart interval. Every replayed
input closed at or before the evaluated bar's close (analyst_backtest.closed_upto).
Results: docs/evidence/analyst-calibration/. The cache stays under .data/ (git-ignored).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import analyst_backtest as ab  # noqa: E402

FAPI = "https://fapi.binance.com"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
BNB_IV = {"15": "15m", "60": "1h", "240": "4h", "D": "1d"}
IV_MS = {"15": 900_000, "60": 3_600_000, "240": 14_400_000, "D": 86_400_000}
DAY = 86_400_000
EVAL_DAYS = {"15": 90, "60": 180}          # evaluated window per chart interval
WARMUP_DAYS = {"15": 4, "60": 12, "240": 45, "D": 55}
EVIDENCE = ROOT / "docs" / "evidence" / "analyst-calibration"
CACHE = ROOT / ".data" / "analyst-backtest"


def get_json(url, params, tries=4):
    full = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(tries):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": "crypto-screener-backtest"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except Exception:  # noqa: BLE001
            if attempt == tries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    return None


def fetch_klines(symbol, iv, start_ms, end_ms):
    """Closed Binance USDT-M klines [start, end), paginated 1500 per request."""
    out, cursor = [], start_ms
    while cursor < end_ms:
        rows = get_json(FAPI + "/fapi/v1/klines", {"symbol": symbol, "interval": BNB_IV[iv],
                                                   "startTime": cursor, "endTime": end_ms - 1,
                                                   "limit": 1500})
        if not rows:
            break
        for r in rows:
            ts = int(r[0])
            if ts + IV_MS[iv] <= end_ms:
                out.append({"ts": ts, "close_ts": ts + IV_MS[iv], "o": float(r[1]), "h": float(r[2]),
                            "l": float(r[3]), "c": float(r[4]), "v": float(r[5])})
        cursor = int(rows[-1][0]) + IV_MS[iv]
        time.sleep(0.15)
    dedup = {c["ts"]: c for c in out}
    return [dedup[k] for k in sorted(dedup)]


def fetch_funding(symbol, start_ms, end_ms):
    out, cursor = [], start_ms
    while cursor < end_ms:
        rows = get_json(FAPI + "/fapi/v1/fundingRate", {"symbol": symbol, "startTime": cursor,
                                                        "endTime": end_ms, "limit": 1000})
        if not rows:
            break
        out += [{"time": int(r["fundingTime"]), "rate": float(r["fundingRate"])} for r in rows]
        if len(rows) < 1000:
            break
        cursor = int(rows[-1]["fundingTime"]) + 1
    return sorted({r["time"]: r for r in out}.values(), key=lambda r: r["time"])


def cmd_fetch(args):
    CACHE.mkdir(parents=True, exist_ok=True)
    end = (int(time.time() * 1000) // IV_MS["60"]) * IV_MS["60"]
    horizon = max(EVAL_DAYS.values())
    meta = {"fetched_at": int(time.time() * 1000), "end_ms": end, "symbols": {}}
    for sym in args.symbols:
        data = {}
        for iv in ("15", "60", "240", "D"):
            days = (EVAL_DAYS.get(iv, horizon)) + WARMUP_DAYS[iv]
            data[iv] = fetch_klines(sym, iv, end - days * DAY, end)
        data["funding"] = fetch_funding(sym, end - (horizon + 30) * DAY, end)
        (CACHE / f"{sym}.json").write_text(json.dumps(data))
        meta["symbols"][sym] = {k: len(v) for k, v in data.items()}
        print(sym, meta["symbols"][sym], flush=True)
    fg = get_json("https://api.alternative.me/fng/", {"limit": 400, "format": "json"})["data"]
    rows = sorted(({"time": int(r["timestamp"]) * 1000, "value": int(r["value"])} for r in fg),
                  key=lambda r: r["time"])
    (CACHE / "fear_greed.json").write_text(json.dumps(rows))
    meta["fear_greed_points"] = len(rows)
    (CACHE / "meta.json").write_text(json.dumps(meta, indent=1))


def load(sym):
    data = json.loads((CACHE / f"{sym}.json").read_text())
    return data, json.loads((CACHE / "fear_greed.json").read_text())


def replay(sc, sym, iv, data, fg, every=1):
    """One row per evaluated bar: raw baseline scores, ATR, and the first ±1 ATR touch."""
    from analyst import EVAL_HORIZON_BARS, atr
    chart, rows = data[iv], []
    htf = {"60": data["60"], "240": data["240"], "D": data["D"]}
    first_eval = chart[-1]["close_ts"] - EVAL_DAYS[iv] * DAY
    for i in range(299, len(chart) - EVAL_HORIZON_BARS, every):
        bar = chart[i]
        if bar["close_ts"] < first_eval:
            continue
        a = atr(chart[i - 299:i + 1])
        touch = ab.first_touch(bar["c"], a, chart[i + 1:i + 1 + EVAL_HORIZON_BARS], EVAL_HORIZON_BARS)
        base = ab.baseline_at(sc, bar["close_ts"], bar["c"], htf, funding=data["funding"],
                              fear_greed=fg)
        rows.append({"symbol": sym, "interval": iv, "close_ts": bar["close_ts"], "price": bar["c"],
                     "atr": a, "touch": touch, "long_score": base["long_score"],
                     "short_score": base["short_score"], "long_verdict": base["long_verdict"],
                     "short_verdict": base["short_verdict"]})
    return rows


CONFIGS = {  # baseline_net variants compared on the same replayed rows
    "current (gain 2.0, verdict ±0.5)": {"score_gain": 2.0, "verdict_bonus": 0.5},
    "gain 1.0, verdict ±0.25": {"score_gain": 1.0, "verdict_bonus": 0.25},
    "scores only (gain 2.0, no verdict)": {"score_gain": 2.0, "verdict_bonus": 0.0},
}


def analyse(rows):
    from analyst import baseline_net
    out = {}
    for name, cfg in CONFIGS.items():
        scored = [{**r, "b": baseline_net(r, cfg)} for r in rows if r["touch"]]
        lean = [r for r in scored if r["b"] and r["touch"] in ("up", "down")]
        hits = sum(r["touch"] == ("up" if r["b"] > 0 else "down") for r in lean)
        out[name] = {"lean_overall": {"decided": len(lean), "hits": hits,
                                      "hit_rate": round(hits / len(lean), 4) if lean else None,
                                      "ci95": ab.wilson(hits, len(lean))},
                     "share_abs_ge_0_5": round(sum(abs(r["b"]) >= 0.5 for r in scored) / len(scored), 4)
                     if scored else None,
                     "share_abs_eq_1": round(sum(abs(r["b"]) >= 1.0 for r in scored) / len(scored), 4)
                     if scored else None,
                     "buckets": ab.summarize(scored, key="b"),
                     "non_overlapping_every_12th": ab.summarize(scored[::12], key="b")}
    decided = [r for r in rows if r["touch"] in ("up", "down")]
    up = sum(r["touch"] == "up" for r in decided)
    out["base_rate"] = {"n_scored": sum(1 for r in rows if r["touch"]), "decided": len(decided),
                        "p_up_first": round(up / len(decided), 4) if decided else None,
                        "p_up_ci95": ab.wilson(up, len(decided))}
    return out


def cmd_baseline(args):
    logging.disable(logging.WARNING)
    import screener as sc
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    all_rows, started = [], time.time()
    if args.reuse:
        all_rows = json.loads((CACHE / "baseline_rows.json").read_text())
    for sym in [] if args.reuse else args.symbols:
        data, fg = load(sym)
        for iv in args.intervals:
            t0 = time.time()
            rows = replay(sc, sym, iv, data, fg, every=args.every)
            all_rows += rows
            print(f"{sym} {iv}: {len(rows)} bars in {time.time() - t0:.0f}s", flush=True)
    if not args.reuse:
        (CACHE / "baseline_rows.json").write_text(json.dumps(all_rows))
    meta = json.loads((CACHE / "meta.json").read_text())
    groups = {"all": all_rows}
    for iv in args.intervals:
        groups[f"interval {iv}"] = [r for r in all_rows if r["interval"] == iv]
    for sym in args.symbols:
        groups[sym] = [r for r in all_rows if r["symbol"] == sym]
    report = {"generated_at": int(time.time() * 1000), "runtime_s": round(time.time() - started),
              "data": meta, "eval_days": EVAL_DAYS, "replay_notes": ab.REPLAY_NOTES,
              "rule": "+1 ATR14 in the lean's direction before -1 ATR against within 12 bars "
                      "(analyst_store.evaluate_outcome); ambiguous = both in one bar",
              "groups": {k: analyse(v) for k, v in groups.items()}}
    (EVIDENCE / "baseline_backtest.json").write_text(json.dumps(report, indent=1))
    print("wrote", EVIDENCE / "baseline_backtest.json")


def cmd_hermes(args):
    """Small live Hermes sample: snapshots rebuilt as of past bar closes, scored immediately."""
    sys.path.insert(0, str(Path.home() / ".hermes" / "cache" / "scratch"))
    try:  # optional local helper: loads HERMES_API_URL/KEY into os.environ, prints nothing
        import hermes_env
        hermes_env.load()
    except ImportError:
        pass  # otherwise HERMES_API_URL / HERMES_API_KEY must already be in the environment
    logging.disable(logging.WARNING)
    import screener as sc
    from analyst import EVAL_HORIZON_BARS, compute_split
    from analyst_hermes import HermesUnavailable, ask_hermes, config_from_env, make_transport
    send = make_transport(config_from_env())
    out_path = EVIDENCE / "hermes_sample.json"
    done = json.loads(out_path.read_text()) if out_path.exists() else []
    plan = [(s, iv, k) for k in range(args.n) for s, iv in
            ((("BTCUSDT", "15"), ("ETHUSDT", "15"), ("SOLUSDT", "15"),
              ("BTCUSDT", "60"), ("ETHUSDT", "60"), ("SOLUSDT", "60"))[k % 6],)]
    for sym, iv, k in plan[len(done):]:
        data, fg = load(sym)
        chart = data[iv]
        # Bars far enough back that the 12-bar horizon has closed; spaced out to avoid overlap.
        i = len(chart) - 1 - EVAL_HORIZON_BARS - 3 * EVAL_HORIZON_BARS * (k // 6 + 1) - 7 * (k % 6)
        as_of = chart[i]["close_ts"]
        htf = {"60": data["60"], "240": data["240"], "D": data["D"]}
        snap = ab.historical_snapshot(sc, "binance", sym, iv, chart, as_of, htf,
                                      funding=data["funding"], fear_greed=fg)
        assert snap["candles"]["last_close_ts"] == as_of
        try:
            res = ask_hermes(snap, send, timeout=90)
        except HermesUnavailable as exc:
            print("hermes unavailable:", exc)
            break
        a = snap["indicators"]["atr14"]
        touch = ab.first_touch(chart[i]["c"], a, chart[i + 1:i + 1 + EVAL_HORIZON_BARS], EVAL_HORIZON_BARS)
        factors = (res["analysis"] or {}).get("factor_scores") or []
        rec = {"symbol": sym, "interval": iv, "as_of": as_of, "price": chart[i]["c"], "atr": a,
               "touch": touch, "status": "ok" if res["analysis"] else "invalid_output",
               "agent_bias": (res["analysis"] or {}).get("agent_bias"), "factors": factors,
               "baseline": snap["baseline"], "model": res["model"], "latency_ms": res["latency_ms"],
               "missing": snap["missing"], "split_old": compute_split(factors, snap["baseline"])}
        done.append(rec)
        out_path.write_text(json.dumps(done, indent=1))
        sp = rec["split_old"]
        print(f"{sym} {iv} as_of={as_of} {rec['status']} L{sp['agent_long_points']}/S{sp['agent_short_points']} "
              f"a={sp['agent_net']} b={sp['baseline_net']} -> {sp['bias']} {sp['long_pct']}% touch={touch}",
              flush=True)


def cmd_score(_args):
    """Score the Hermes sample under the old split (recorded at run time) and the current one."""
    from analyst import SPLIT_CONFIG, compute_split
    rows = json.loads((EVIDENCE / "hermes_sample.json").read_text())
    agent_only = {**SPLIT_CONFIG, "w_baseline": 0.0}
    table, tallies = [], {"old": {}, "new": {}, "agent_only": {}, "agent_bias": {}}
    for r in rows:
        if r["status"] != "ok":
            continue
        new = compute_split(r["factors"], r["baseline"])
        biases = {"old": r["split_old"]["bias"], "new": new["bias"],
                  "agent_only": compute_split(r["factors"], None, agent_only)["bias"],
                  "agent_bias": r["agent_bias"]}
        outcome = {k: (None if b not in ("long", "short") or r["touch"] is None else
                       "win" if r["touch"] == ("up" if b == "long" else "down") else
                       "loss" if r["touch"] in ("up", "down") else r["touch"]) for k, b in biases.items()}
        for k, o in outcome.items():
            tallies[k][o or "no call"] = tallies[k].get(o or "no call", 0) + 1
        sp = r["split_old"]
        table.append({"symbol": r["symbol"], "interval": r["interval"], "as_of": r["as_of"],
                      "long_points": sp["agent_long_points"], "short_points": sp["agent_short_points"],
                      "agent_net": sp["agent_net"], "baseline_net_old": sp["baseline_net"],
                      "baseline_net_new": new["baseline_net"], "old": f"{sp['bias']} {sp['long_pct']}/{sp['short_pct']}",
                      "new": f"{new['bias']} {new['long_pct']}/{new['short_pct']}", "agent_bias": r["agent_bias"],
                      "touch": r["touch"], "outcome": outcome,
                      "old_opposes_tally": (sp["agent_net"] or 0) * sp["net"] < 0 or
                                           (abs(sp["agent_net"] or 0) >= 0.15 and sp["bias"] == "neutral"),
                      "missing": sorted(r["missing"])})
    out = {"n": len(table), "tallies": tallies, "rows": table,
           "note": "indicative only: small n, overlapping market regime, single model run per bar"}
    (EVIDENCE / "hermes_sample_scored.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(tallies, indent=1))
    for t in table:
        print(t["symbol"], t["interval"], f"L{t['long_points']}/S{t['short_points']}", "a", t["agent_net"],
              "b_old", t["baseline_net_old"], "| old", t["old"], "| new", t["new"], "| agent", t["agent_bias"],
              "| touch", t["touch"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    b = sub.add_parser("baseline")
    b.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    b.add_argument("--intervals", nargs="+", default=["15", "60"])
    b.add_argument("--every", type=int, default=1, help="evaluate every Nth bar (speed)")
    b.add_argument("--reuse", action="store_true", help="re-bucket the cached replay rows")
    h = sub.add_parser("hermes")
    h.add_argument("--n", type=int, default=12)
    sub.add_parser("score", help="score the Hermes sample under the old and the current split")
    args = ap.parse_args()
    {"fetch": cmd_fetch, "baseline": cmd_baseline, "hermes": cmd_hermes, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    main()
