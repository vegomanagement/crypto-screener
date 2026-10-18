"""Venue data for the paper journal: Binance USDT-M klines (closed bars only) and funding history,
both cached in the journal's SQLite so restarts fetch only the tail. Public read-only endpoints;
no keys, no orders."""
from __future__ import annotations

import logging
import math
import time

from funding_data import FundingError, http_json
from market_candles import interval_ms
from paper_journal import FundingUnavailable

log = logging.getLogger(__name__)

FAPI = "https://fapi.binance.com"
BINANCE_TF = {"240": "4h", "D": "1d", "60": "1h", "W": "1w"}
PAGE = 1500
PAUSE_S = 0.2                         # between pages: keeps a cold start far below the weight limit


def _q(**kw):
    return "&".join(f"{k}={v}" for k, v in kw.items())


def parse_kline(row, tf):
    ts = int(row[0])
    o, h, lo, c, v = (float(row[k]) for k in (1, 2, 3, 4, 5))
    if (not all(math.isfinite(x) for x in (o, h, lo, c, v)) or min(o, h, lo, c) <= 0 or v < 0
            or lo > min(o, c) or h < max(o, c)):
        raise ValueError(f"invalid kline at {ts}")
    return {"ts": ts, "close_ts": ts + interval_ms(tf), "o": o, "h": h, "l": lo, "c": c, "v": v}


class VenueData:
    def __init__(self, store, http=http_json, clock=time.time, sleep=time.sleep, history_start=None):
        self.store, self.http, self.clock, self.sleep = store, http, clock, sleep
        self.history_start = history_start

    def fetch_klines(self, symbol, tf, start_ms, now_ms):
        """Closed klines with open time ≥ start_ms (paged). The bar still forming is dropped."""
        out, cur = [], start_ms
        while True:
            rows = self.http(f"{FAPI}/fapi/v1/klines?{_q(symbol=symbol, interval=BINANCE_TF[tf], startTime=cur, limit=PAGE)}")
            if not isinstance(rows, list):
                raise FundingError("klines: unexpected payload")
            bars = [parse_kline(r, tf) for r in rows]
            out += [b for b in bars if b["close_ts"] <= now_ms]
            if len(rows) < PAGE or not bars or bars[-1]["close_ts"] > now_ms:
                return out
            cur = bars[-1]["ts"] + 1
            self.sleep(PAUSE_S)

    def candles(self, symbol, timeframes, start_ms=None):
        """{tf: closed candles oldest first} from the cache plus the venue tail."""
        now = int(self.clock() * 1000)
        start = start_ms if start_ms is not None else (self.history_start or 0)
        out = {}
        for tf in timeframes:
            last = self.store.last_kline_ts(symbol, tf)
            fresh = self.fetch_klines(symbol, tf, last if last is not None else start, now)
            if fresh:
                self.store.put_klines(symbol, tf, fresh)
            bars = [b for b in self.store.klines(symbol, tf) if b["ts"] >= start and b["close_ts"] <= now]
            step = interval_ms(tf)
            gaps = sum(1 for a, b in zip(bars, bars[1:]) if b["ts"] - a["ts"] != step)
            if gaps:
                log.info("paper journal %s %s: %d venue gaps in the kline history", symbol, tf, gaps)
            out[tf] = bars
        return out

    def funding(self, symbol, a, b):
        """Funding rates with a < time ≤ b from /fapi/v1/fundingRate (cached). Raises
        FundingUnavailable when the venue cannot be reached (the trade's costs are retried later)."""
        if b <= a:
            return []
        cov = self.store.funding_cover(symbol)
        if not (cov and cov["start_ms"] <= a and cov["end_ms"] >= b):
            now = int(self.clock() * 1000)
            rows, cur = [], a + 1
            try:
                while cur <= b:
                    page = self.http(f"{FAPI}/fapi/v1/fundingRate?{_q(symbol=symbol, startTime=cur, endTime=b, limit=1000)}")
                    if not isinstance(page, list):
                        raise FundingError("fundingRate: unexpected payload")
                    rows += [{"time": int(r["fundingTime"]), "rate": float(r["fundingRate"])} for r in page]
                    if len(page) < 1000:
                        break
                    cur = int(page[-1]["fundingTime"]) + 1
            except FundingError as exc:
                raise FundingUnavailable(str(exc)) from None
            self.store.put_funding(symbol, rows, a, min(b, now))
        return [r["rate"] for r in self.store.funding_rows(symbol, a, b)]
