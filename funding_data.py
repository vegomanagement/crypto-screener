"""Funding fetchers over urllib with a 30 s per-market cache. Read-only public endpoints."""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import funding

CACHE_TTL_S = 30
INFO_TTL_S = 3600
TIMEOUT_S = 10
BINANCE = "https://fapi.binance.com"
BYBIT = "https://api.bybit.com"
HL = "https://api.hyperliquid.xyz/info"


class FundingError(RuntimeError):
    pass


def http_json(url, payload=None, timeout=TIMEOUT_S):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise FundingError(f"HTTP {exc.code} from {urllib.parse.urlsplit(url).netloc}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise FundingError(f"{type(exc).__name__} from {urllib.parse.urlsplit(url).netloc}") from None


def _q(**params):
    return urllib.parse.urlencode(params)


class FundingService:
    def __init__(self, http=http_json, clock=time.time, ttl=CACHE_TTL_S):
        self.http, self.clock, self.ttl = http, clock, ttl
        self._cache, self._lock, self._locks = {}, threading.Lock(), {}

    def _cached(self, key, ttl, loader):
        with self._lock:
            hit = self._cache.get(key)
            if hit and self.clock() - hit[0] < ttl:
                return hit[1]
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            with self._lock:
                hit = self._cache.get(key)
                if hit and self.clock() - hit[0] < ttl:
                    return hit[1]
            value = loader()
            with self._lock:
                self._cache[key] = (self.clock(), value)
            return value

    def _try(self, fn, missing, name):
        try:
            return fn()
        except FundingError as exc:
            missing[name] = str(exc)
            return None

    def predicted_fundings(self):
        return self._cached(("hl", "predictedFundings"), self.ttl,
                            lambda: self.http(HL, {"type": "predictedFundings"}))

    def _binance_info(self):
        return self._cached(("binance", "fundingInfo"), INFO_TTL_S,
                            lambda: self.http(f"{BINANCE}/fapi/v1/fundingInfo"))

    def _venue(self, exchange, symbol, now_ms):
        missing = {}
        if exchange == "binance":
            jobs = {"premium": lambda: self.http(f"{BINANCE}/fapi/v1/premiumIndex?{_q(symbol=symbol)}"),
                    "history": lambda: self.http(f"{BINANCE}/fapi/v1/fundingRate?{_q(symbol=symbol, limit=30)}"),
                    "info": self._binance_info}
        elif exchange == "bybit":
            jobs = {"tickers": lambda: self.http(f"{BYBIT}/v5/market/tickers?{_q(category='linear', symbol=symbol)}"),
                    "history": lambda: self.http(
                        f"{BYBIT}/v5/market/funding/history?{_q(category='linear', symbol=symbol, limit=30)}")}
        else:
            dex = symbol.split(":", 1)[0] if ":" in symbol else ""
            ctx_payload = {"type": "metaAndAssetCtxs", **({"dex": dex} if dex else {})}
            jobs = {"ctxs": lambda: self.http(HL, ctx_payload),
                    "history": lambda: self.http(HL, {"type": "fundingHistory", "coin": symbol,
                                                      "startTime": now_ms - 31 * funding.HOUR_MS})}
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {k: pool.submit(self._try, fn, missing, k) for k, fn in jobs.items()}
            raw = {k: f.result() for k, f in futures.items()}
        try:
            if exchange == "binance":
                out = funding.binance(symbol, raw["premium"], raw["history"], raw["info"], now_ms)
            elif exchange == "bybit":
                out = funding.bybit(symbol, raw["tickers"], raw["history"], now_ms)
            else:
                out = funding.hyperliquid(symbol, raw["ctxs"], raw["history"], now_ms)
        except (AttributeError, KeyError, TypeError, IndexError) as exc:
            # A venue answered 200 with an unexpected JSON shape: report it like a fetch failure.
            raise FundingError(f"unexpected {exchange} response shape ({type(exc).__name__})") from None
        out["missing"].update({f"fetch_{k}": v for k, v in missing.items()})
        return out

    def get(self, exchange, symbol):
        if exchange not in ("binance", "bybit", "hyperliquid"):
            raise ValueError("unsupported exchange")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z0-9:._-]{1,80}", symbol):
            raise ValueError("invalid symbol")

        def load():
            now_ms = int(self.clock() * 1000)
            out = self._venue(exchange, symbol, now_ms)
            missing = {}
            pf = self._try(self.predicted_fundings, missing, "cross_venue")
            coin = funding.hl_coin(exchange, symbol)
            out["cross_venue"] = funding.cross_venue(pf, coin, now_ms)
            if out["cross_venue"] is None:
                out["missing"]["cross_venue"] = missing.get(
                    "cross_venue", f"coin {coin!r} not in predictedFundings")
            if not any(out.get(k) for k in ("predicted", "last_settled")):
                raise FundingError(f"no funding data for {exchange} {symbol}: "
                                   + "; ".join(f"{k}: {v}" for k, v in list(out["missing"].items())[:4]))
            out["cache_ttl_s"] = self.ttl
            return out
        return self._cached((exchange, symbol), self.ttl, load)
