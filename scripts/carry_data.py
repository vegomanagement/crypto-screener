#!/usr/bin/env python3
"""Historical data for scripts/carry_analysis.py (stdlib only, public endpoints, cached in scratch).

    python scripts/carry_data.py            # fetch what is missing → $CARRY_RAW/carry.json.gz

* Binance perp daily bars + funding: reused from the strategy-research cache
  (``~/.hermes/cache/scratch/strategy-research/bars.json.gz``, built by backtest_portfolio.py data).
* Binance spot 1d klines: ``/api/v3/klines`` (PEPEUSDT × 1000 → units of 1000PEPEUSDT).
* Bybit funding: ``/v5/market/funding/history`` (linear, paged backwards with endTime, 200 rows),
  Bybit linear 1d klines ``/v5/market/kline`` (BTC/ETH only, for the cross-venue variants).
* Hyperliquid funding: POST /info ``fundingHistory`` (hourly, 500 rows per page, from 2023-05),
  1d candles ``candleSnapshot`` (BTC/ETH). 1000PEPE ↔ kPEPE.
Each source is cached as its own JSON file, so an interrupted run resumes where it stopped.
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = Path(os.environ.get("CARRY_RAW", Path.home() / ".hermes/cache/scratch/carry"))
PORTFOLIO_RAW = Path(os.environ.get("PORTFOLIO_RAW", Path.home() / ".hermes/cache/scratch/strategy-research"))
UNIVERSE = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "ZECUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT", "1000PEPEUSDT", "SUIUSDT",
            "NEARUSDT", "ADAUSDT", "LINKUSDT", "WLDUSDT", "AVAXUSDT", "UNIUSDT", "FILUSDT", "BCHUSDT", "LTCUSDT",
            "AAVEUSDT", "DASHUSDT")
XV = ("BTCUSDT", "ETHUSDT")
END = int(datetime(2026, 10, 18, tzinfo=UTC).timestamp() * 1000)     # last closed day + 1 (as the research)
START = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)
DAY = 86_400_000
HL = "https://api.hyperliquid.xyz/info"


def spot_symbol(sym):
    """(Binance spot symbol, price multiplier to perp units)."""
    return ("PEPEUSDT", 1000.0) if sym == "1000PEPEUSDT" else (sym, 1.0)


def hl_coin(sym):
    base = sym[:-4]
    return "kPEPE" if base == "1000PEPE" else base


def http(url, body=None, tries=5):
    data = None if body is None else json.dumps(body).encode()
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                         headers={"Content-Type": "application/json", "User-Agent": "crypto-screener-carry"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 418, 500, 502, 503, 504) or k == tries - 1:
                raise
            time.sleep(5 * (k + 1))
        except (urllib.error.URLError, TimeoutError, OSError):
            if k == tries - 1:
                raise
            time.sleep(3 * (k + 1))


def cached(name, loader):
    path = RAW / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text())
    val = loader()
    RAW.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(val))
    return val


def binance_spot(sym):
    ssym, mult = spot_symbol(sym)
    out, cur = [], START
    while cur < END:
        rows = http(f"https://api.binance.com/api/v3/klines?symbol={ssym}&interval=1d&startTime={cur}&endTime={END - 1}"
                    f"&limit=1000")
        time.sleep(0.3)
        if not rows:
            break
        out += [{"ts": int(r[0]), "o": float(r[1]) * mult, "h": float(r[2]) * mult, "l": float(r[3]) * mult,
                 "c": float(r[4]) * mult, "qv": float(r[7])} for r in rows if int(r[6]) < END]
        if len(rows) < 1000:
            break
        cur = int(rows[-1][0]) + DAY
    return out


def bybit_funding(sym):
    out, end = {}, END
    while True:
        r = http(f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={sym}&endTime={end}&limit=200")
        time.sleep(0.15)
        rows = (r.get("result") or {}).get("list") or []
        if r.get("retCode") != 0 or not rows:
            break
        for x in rows:
            out[int(x["fundingRateTimestamp"])] = float(x["fundingRate"])
        first = min(int(x["fundingRateTimestamp"]) for x in rows)
        if first <= START or len(rows) < 200:
            break
        end = first - 1
    return [{"time": t, "rate": out[t]} for t in sorted(out) if START <= t < END]


def bybit_klines(sym):
    out, end = {}, END - 1
    while True:
        r = http(f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=D&end={end}&limit=1000")
        time.sleep(0.15)
        rows = (r.get("result") or {}).get("list") or []
        if not rows:
            break
        for x in rows:
            ts = int(x[0])
            if START <= ts and ts + DAY <= END:
                out[ts] = {"ts": ts, "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4])}
        first = min(int(x[0]) for x in rows)
        if first <= START or len(rows) < 1000:
            break
        end = first - 1
    return [out[t] for t in sorted(out)]


def hl_funding(sym):
    coin, out, cur = hl_coin(sym), {}, START
    while cur < END:
        rows = http(HL, {"type": "fundingHistory", "coin": coin, "startTime": cur, "endTime": END})
        time.sleep(1.2)                          # weight 20+ per call; stays far below 1200/min
        if not isinstance(rows, list) or not rows:
            break
        for x in rows:
            out[int(x["time"])] = float(x["fundingRate"])
        last = max(int(x["time"]) for x in rows)
        if len(rows) < 500 or last <= cur:
            break
        cur = last + 1
    return [{"time": t, "rate": out[t]} for t in sorted(out) if t < END]


def hl_klines(sym):
    rows = http(HL, {"type": "candleSnapshot", "req": {"coin": hl_coin(sym), "interval": "1d", "startTime": START,
                                                       "endTime": END - 1}})
    return [{"ts": int(x["t"]), "o": float(x["o"]), "h": float(x["h"]), "l": float(x["l"]), "c": float(x["c"]),
             "v": float(x["v"])} for x in rows if int(x["t"]) + DAY <= END and float(x["v"]) > 0]


def perp_and_binance_funding():
    path = PORTFOLIO_RAW / "bars.json.gz"
    if not path.exists():
        sys.exit(f"{path} missing: run `python scripts/backtest_portfolio.py data` first")
    with gzip.open(path, "rt") as fh:
        blob = json.load(fh)
    return {s: blob["daily"][s] for s in UNIVERSE}, {s: blob["funding"][s] for s in UNIVERSE}


def main():
    perp, bn_fund = perp_and_binance_funding()
    blob = {"end": END, "perp": perp, "funding": {"binance": bn_fund, "bybit": {}, "hyperliquid": {}},
            "spot": {}, "xv_klines": {"bybit": {}, "hyperliquid": {}}}
    for s in UNIVERSE:
        blob["spot"][s] = cached(f"spot-{s}", lambda s=s: binance_spot(s))
        blob["funding"]["bybit"][s] = cached(f"bybit-funding-{s}", lambda s=s: bybit_funding(s))
        blob["funding"]["hyperliquid"][s] = cached(f"hl-funding-{s}", lambda s=s: hl_funding(s))
        f = blob["funding"]
        print(f"{s}: spot {len(blob['spot'][s])} d, binance {len(f['binance'][s] or [])}, bybit {len(f['bybit'][s])}, "
              f"hl {len(f['hyperliquid'][s])} prints", flush=True)
    for s in XV:
        blob["xv_klines"]["bybit"][s] = cached(f"bybit-kl-{s}", lambda s=s: bybit_klines(s))
        blob["xv_klines"]["hyperliquid"][s] = cached(f"hl-kl-{s}", lambda s=s: hl_klines(s))
    with gzip.open(RAW / "carry.json.gz", "wt") as fh:
        json.dump(blob, fh)
    print("wrote", RAW / "carry.json.gz")


if __name__ == "__main__":
    main()
