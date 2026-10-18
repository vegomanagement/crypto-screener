#!/usr/bin/env python3
"""Universe selection + data for scripts/backtest_swing_universe.py (stdlib only, cached in scratch).

Universe rule (applied at a decision time T, chosen BY DATA, not by hand):
  candidates  Binance USDT-M PERPETUAL contracts from /fapi/v1/exchangeInfo (TRADING and, for the
              historical check, SETTLING = delisted/settled ones too), underlyingType COIN (no
              index / TradFi contracts), base asset not a stablecoin (STABLES);
  history     onboardDate ≤ T − 3 × 365 days (at least three years of history at T);
  rank        mean daily quote volume (USDT, kline field 7 of /fapi/v1/klines 1d) over the 365 days
              before T; the top N (default 20) form the universe.
  'main'      T = the backtest end (today's liquid coins: survivorship-biased by construction);
  'asof_oos'  T = the out-of-sample start, candidates include SETTLING contracts, so the universe is
              what a trader could have picked on that day (the bias check).
Daily klines for the ranking come from /fapi/v1/klines (works for settled symbols too), 1h / 15m
history from data.binance.vision monthly zips via scripts/signals_data.py, funding from
/fapi/v1/fundingRate. Everything is cached under SIGNALS_CACHE (scratch), never in the repo.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from datetime import UTC, datetime

import signals_data as data

DAY = 86_400_000
STABLES = {"USDC", "BUSD", "TUSD", "FDUSD", "USDP", "DAI", "USDE", "PAX", "UST", "USTC", "EUR", "AEUR",
           "PYUSD", "USD1", "RLUSD", "XUSD", "BFUSD"}
YEARS = 3


def exchange_info():
    path = data.CACHE / "universe" / "exchangeInfo.json"
    if path.exists():
        return json.loads(path.read_text())
    blob = json.loads(data._get(f"{data.FAPI}/fapi/v1/exchangeInfo"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(blob))
    return blob


def candidates(info, t_ms, include_settled):
    out = []
    for s in info["symbols"]:
        if s.get("contractType") != "PERPETUAL" or s.get("quoteAsset") != "USDT":
            continue
        if s.get("underlyingType") != "COIN" or s.get("baseAsset", "").upper() in STABLES:
            continue
        if s["status"] != "TRADING" and not (include_settled and s["status"] == "SETTLING"):
            continue
        if s["onboardDate"] > t_ms - YEARS * 365 * DAY:
            continue
        out.append({"symbol": s["symbol"], "onboard": s["onboardDate"], "status": s["status"]})
    return out


def daily(sym, end_ms):
    """Every closed 1d kline (ts, quote volume, close) up to ``end_ms`` (cached)."""
    path = data.CACHE / "universe" / "daily" / f"{sym}-{end_ms}.json"
    if path.exists():
        return json.loads(path.read_text())
    out, cursor = [], int(datetime(2019, 9, 1, tzinfo=UTC).timestamp() * 1000)
    while cursor < end_ms:
        q = urllib.parse.urlencode({"symbol": sym, "interval": "1d", "startTime": cursor,
                                    "endTime": end_ms - 1, "limit": 1500})
        rows = json.loads(data._get(f"{data.FAPI}/fapi/v1/klines?{q}") or b"[]")
        time.sleep(0.35)                     # weight 10 per call, 2400/min budget
        if not rows:
            break
        out += [[int(r[0]), float(r[7]), float(r[4])] for r in rows if int(r[6]) < end_ms]
        if len(rows) < 1500:
            break
        cursor = int(rows[-1][0]) + DAY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def rank(info, t_ms, end_ms, include_settled, top=20):
    """Universe at decision time ``t_ms``: [{symbol, mean_qv_usd, days, onboard, status}]."""
    rows = []
    for c in candidates(info, t_ms, include_settled):
        d = [r for r in daily(c["symbol"], end_ms) if t_ms - 365 * DAY <= r[0] < t_ms]
        if len(d) < 200:                     # traded on fewer than 200 of the 365 days
            continue
        rows.append(dict(c, mean_qv_usd=round(sum(r[1] for r in d) / len(d)), days=len(d)))
    rows.sort(key=lambda r: -r["mean_qv_usd"])
    return rows[:top], len(rows)


def first_month(onboard_ms, floor=(2020, 1)):
    t = datetime.fromtimestamp(onboard_ms / 1000, tz=UTC)
    return max(floor, (t.year, t.month))


def klines(sym, iv, end_ms, onboard_ms):
    data.FIRST_MONTH.setdefault(sym, first_month(onboard_ms))
    try:
        return data.klines(sym, iv, end_ms)
    except Exception:  # noqa: BLE001 — settled contracts: the current-month API tail can fail
        tail = data.CACHE / "klines" / sym / iv / f"tail-{end_ms}.json"
        tail.parent.mkdir(parents=True, exist_ok=True)
        tail.write_text("[]")
        return data.klines(sym, iv, end_ms)


def funding(sym, end_ms):
    """Historical funding [{time, rate}] or None when the venue does not serve it."""
    try:
        return data.funding(sym, end_ms)
    except Exception:  # noqa: BLE001
        return None
