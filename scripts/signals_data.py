#!/usr/bin/env python3
"""Historical Binance USDT-M data for scripts/backtest_signals.py (stdlib only).

Klines come from data.binance.vision monthly zips (columns: open_time, open, high, low,
close, volume, close_time, quote_volume, count, taker_buy_volume, taker_buy_quote_volume,
ignore) plus /fapi/v1/klines for the current month; funding from /fapi/v1/fundingRate;
daily aggTrades zips only for the short footprint sample. Everything is cached under
SIGNALS_CACHE (default ~/.hermes/cache/scratch/wyckoff-signals-data), never in the repo.

Per-bar delta = taker_buy_volume − (volume − taker_buy_volume): exact taker buy minus
taker sell volume of the bar (Binance kline field 9), but without per-price levels.
"""
from __future__ import annotations

import csv
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

CACHE = Path(os.environ.get("SIGNALS_CACHE",
                            Path.home() / ".hermes" / "cache" / "scratch" / "wyckoff-signals-data"))
VISION = "https://data.binance.vision/data/futures/um"
FAPI = "https://fapi.binance.com"
IV_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000}
FIRST_MONTH = {"BTCUSDT": (2020, 1), "ETHUSDT": (2020, 1), "SOLUSDT": (2020, 9)}


def _get(url, tries=4, timeout=60):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "crypto-screener-backtest"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt == tries - 1:
                raise
        except Exception:  # noqa: BLE001
            if attempt == tries - 1:
                raise
        time.sleep(1.5 * (attempt + 1))
    return None


def _months(first, last):
    y, m = first
    while (y, m) <= last:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _parse_kline_rows(rows, iv):
    out = []
    for r in rows:
        if not r or not r[0].strip().isdigit():
            continue  # header row in newer files
        ts = int(r[0])
        out.append({"ts": ts, "close_ts": ts + IV_MS[iv], "o": float(r[1]), "h": float(r[2]),
                    "l": float(r[3]), "c": float(r[4]), "v": float(r[5]), "tb": float(r[9])})
    return out


def _month_file(sym, iv, y, m):
    path = CACHE / "klines" / sym / iv / f"{y}-{m:02d}.json"
    if path.exists():
        return json.loads(path.read_text())
    blob = _get(f"{VISION}/monthly/klines/{sym}/{iv}/{sym}-{iv}-{y}-{m:02d}.zip")
    if blob is None:
        return None
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        text = zf.read(zf.namelist()[0]).decode()
    rows = _parse_kline_rows(csv.reader(io.StringIO(text)), iv)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))
    return rows


def _api_klines(sym, iv, start_ms, end_ms):
    out, cursor = [], start_ms
    while cursor < end_ms:
        q = urllib.parse.urlencode({"symbol": sym, "interval": iv, "startTime": cursor,
                                    "endTime": end_ms - 1, "limit": 1500})
        rows = json.loads(_get(f"{FAPI}/fapi/v1/klines?{q}"))
        if not rows:
            break
        out += [r for r in _parse_kline_rows([[str(x) for x in r] for r in rows], iv)
                if r["close_ts"] <= end_ms]
        cursor = int(rows[-1][0]) + IV_MS[iv]
        time.sleep(0.2)
    return out


def klines(sym, iv, end_ms):
    """Closed klines from the symbol's first month up to ``end_ms`` (sorted, de-duplicated)."""
    now = datetime.fromtimestamp(end_ms / 1000, tz=UTC)
    last = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    months = list(_months(FIRST_MONTH[sym], last))
    with ThreadPoolExecutor(max_workers=6) as pool:
        parts = list(pool.map(lambda ym: _month_file(sym, iv, *ym), months))
    rows = [r for p in parts if p for r in p]
    month_start = int(datetime(now.year, now.month, 1, tzinfo=UTC).timestamp() * 1000)
    tail = CACHE / "klines" / sym / iv / f"tail-{end_ms}.json"
    if tail.exists():
        rows += json.loads(tail.read_text())
    else:
        extra = _api_klines(sym, iv, month_start, end_ms)
        tail.write_text(json.dumps(extra))
        rows += extra
    dedup = {r["ts"]: r for r in rows if r["close_ts"] <= end_ms}
    return [dedup[k] for k in sorted(dedup)]


def funding(sym, end_ms):
    path = CACHE / "funding" / f"{sym}-{end_ms}.json"
    if path.exists():
        return json.loads(path.read_text())
    out, cursor = [], int(datetime(2019, 9, 1, tzinfo=UTC).timestamp() * 1000)
    while cursor < end_ms:
        q = urllib.parse.urlencode({"symbol": sym, "startTime": cursor, "endTime": end_ms,
                                    "limit": 1000})
        rows = json.loads(_get(f"{FAPI}/fapi/v1/fundingRate?{q}"))
        if not rows:
            break
        out += [{"time": int(r["fundingTime"]), "rate": float(r["fundingRate"])} for r in rows]
        if len(rows) < 1000:
            break
        cursor = int(rows[-1]["fundingTime"]) + 1
        time.sleep(0.3)
    out = sorted({r["time"]: r for r in out}.values(), key=lambda r: r["time"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def agg_trades_day(sym, day):
    """[(time_ms, price, qty, buyer_is_aggressor)] of one UTC day (``day`` = 'YYYY-MM-DD')."""
    path = CACHE / "aggtrades" / sym / f"{day}.zip"
    if not path.exists():
        blob = _get(f"{VISION}/daily/aggTrades/{sym}/{sym}-aggTrades-{day}.zip", timeout=180)
        if blob is None:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    with zipfile.ZipFile(path) as zf:
        text = zf.read(zf.namelist()[0]).decode()
    out = []
    for r in csv.reader(io.StringIO(text)):
        if not r or not r[0].isdigit():
            continue
        # agg_trade_id, price, quantity, first_id, last_id, transact_time, is_buyer_maker
        out.append((int(r[5]), float(r[1]), float(r[2]), r[6].strip().lower() != "true"))
    return out
