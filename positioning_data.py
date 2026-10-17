"""Public-endpoint fetchers for positioning data: urllib only, TTL cache, per-host pacing.

Importing performs no network I/O. Every request has an 8 s timeout and a response-size cap.
Venue limits (documented, Oct 2026): Binance /futures/data/* 1000 req / 5 min per IP;
Bybit market endpoints ~600 req / 5 s per IP; Hyperliquid info 1200 weight / min per IP
(clearinghouseState weight 2, metaAndAssetCtxs 20). We stay far below via caching + pacing.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from positioning import BYBIT_PERIOD

TIMEOUT = 8
MAX_BYTES = 4 * 1024 * 1024
MIN_INTERVAL = {"fapi.binance.com": 0.1, "api.bybit.com": 0.1, "api.hyperliquid.xyz": 1.0,
                "open-api-v4.coinglass.com": 2.0}
RATIO_TTL = 60          # ratios are at most 5-minute series; 60 s cache is ample
OI_TTL = 30
UA = "crypto-screener-positioning/1.0 (+public market data)"


class FetchError(RuntimeError):
    pass


class HttpClient:
    """Thread-safe JSON client with TTL cache and minimum spacing per host."""

    def __init__(self, opener=None, clock=time.monotonic, sleep=time.sleep,
                 min_interval=None, max_entries=512, wall=time.time):
        self._open = opener or (lambda req, timeout: urlopen(req, timeout=timeout))  # noqa: S310
        self._clock, self._sleep, self._wall = clock, sleep, wall
        self._min = dict(MIN_INTERVAL if min_interval is None else min_interval)
        self._next = {}
        self._cache = {}
        self._pace_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self.max_entries = max_entries
        self.requests = 0

    def _pace(self, host):
        gap = self._min.get(host, 0.2)
        with self._pace_lock:
            now = self._clock()
            due = max(now, self._next.get(host, 0))
            self._next[host] = due + gap
        if due > now:
            self._sleep(due - now)

    def request_json(self, url, *, body=None, headers=None, ttl=0, cache_key=None, meta=None):
        """meta (optional dict) receives fetched_at (wall ms of the real upstream fetch, also on cache hits)."""
        key = cache_key or (url, json.dumps(body, sort_keys=True) if body is not None else None)
        if ttl:
            with self._cache_lock:
                hit = self._cache.get(key)
                if hit and hit[0] > self._clock():
                    if meta is not None:
                        meta.update(fetched_at=hit[2], cached=True)
                    return hit[1]
        host = urlparse(url).hostname or ""
        self._pace(host)
        data = json.dumps(body).encode() if body is not None else None
        hdrs = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
        if data is not None:
            hdrs["Content-Type"] = "application/json"
        req = Request(url, data=data, headers=hdrs, method="POST" if data is not None else "GET")
        self.requests += 1
        try:
            with self._open(req, TIMEOUT) as resp:
                raw = resp.read(MAX_BYTES + 1)
        except Exception as exc:  # noqa: BLE001 - surface as one error type, never with headers
            code = getattr(exc, "code", None)
            raise FetchError(f"{host}: HTTP {code}" if code else f"{host}: {type(exc).__name__}") from None
        if len(raw) > MAX_BYTES:
            raise FetchError(f"{host}: response exceeds {MAX_BYTES} bytes")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise FetchError(f"{host}: invalid JSON") from None
        fetched_at = int(self._wall() * 1000)
        if meta is not None:
            meta.update(fetched_at=fetched_at, cached=False)
        if ttl:
            with self._cache_lock:
                if len(self._cache) >= self.max_entries:
                    now = self._clock()
                    self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
                    while len(self._cache) >= self.max_entries:
                        self._cache.pop(next(iter(self._cache)))
                self._cache[key] = (self._clock() + ttl, payload, fetched_at)
        return payload

    def get(self, base, path, params, ttl, meta=None):
        return self.request_json(f"{base}{path}?{urlencode(params)}", ttl=ttl, meta=meta)


BINANCE = "https://fapi.binance.com"
BYBIT = "https://api.bybit.com"
HL_INFO = "https://api.hyperliquid.xyz/info"


def coin_of(exchange, symbol):
    if exchange == "hyperliquid":
        return symbol
    m = re.fullmatch(r"(?:1000)?([A-Z0-9]+?)(USDT|USDC)", symbol)
    return m.group(1) if m else symbol


def fetch_raw(client, exchange, symbol, period):
    """Fetch every raw payload a snapshot needs; failures are stored as exceptions."""
    raw, tasks = {}, []

    def take(key, fn):
        tasks.append((key, fn))

    def run(key, fn):
        try:
            raw[key] = fn()
        except Exception as exc:  # noqa: BLE001
            raw[key] = exc if isinstance(exc, FetchError) else FetchError(str(exc)[:160])

    if exchange == "binance":
        ratio = {"symbol": symbol, "period": period, "limit": 30}
        for key, path in (("top_account", "/futures/data/topLongShortAccountRatio"),
                          ("top_position", "/futures/data/topLongShortPositionRatio"),
                          ("global_account", "/futures/data/globalLongShortAccountRatio"),
                          ("taker", "/futures/data/takerlongshortRatio")):
            take(key, lambda p=path: client.get(BINANCE, p, ratio, RATIO_TTL))
        take("oi_hist", lambda: client.get(BINANCE, "/futures/data/openInterestHist",
                                           {"symbol": symbol, "period": "1h", "limit": 25}, RATIO_TTL))
        take("oi_now", lambda: client.get(BINANCE, "/fapi/v1/openInterest", {"symbol": symbol}, OI_TTL))
        take("basis", lambda: client.get(BINANCE, "/futures/data/basis", {
            "pair": symbol, "contractType": "PERPETUAL", "period": period, "limit": 30}, RATIO_TTL))
        take("premium", lambda: client.get(BINANCE, "/fapi/v1/premiumIndex", {"symbol": symbol}, OI_TTL))
        take("klines", lambda: client.get(BINANCE, "/fapi/v1/klines",
                                          {"symbol": symbol, "interval": "1h", "limit": 25}, RATIO_TTL))
    elif exchange == "bybit":
        take("by_account_ratio", lambda: client.get(BYBIT, "/v5/market/account-ratio", {
            "category": "linear", "symbol": symbol, "period": BYBIT_PERIOD[period], "limit": 30}, RATIO_TTL))
        take("by_oi_hist", lambda: client.get(BYBIT, "/v5/market/open-interest", {
            "category": "linear", "symbol": symbol, "intervalTime": "1h", "limit": 25}, RATIO_TTL))
        take("by_ticker", lambda: client.get(BYBIT, "/v5/market/tickers",
                                             {"category": "linear", "symbol": symbol}, OI_TTL))
        take("by_klines", lambda: client.get(BYBIT, "/v5/market/kline", {
            "category": "linear", "symbol": symbol, "interval": "60", "limit": 25}, RATIO_TTL))
    elif exchange == "hyperliquid":
        meta = {}
        take("hl_ctxs", lambda: client.request_json(HL_INFO, body={"type": "metaAndAssetCtxs"}, ttl=OI_TTL,
                                                    meta=meta))
    else:
        raise ValueError("unsupported exchange")
    # Bounded concurrency; the client's per-host pacing still spaces the requests.
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda t: run(*t), tasks))
    if exchange == "hyperliquid":
        raw["hl_fetched_at"] = meta.get("fetched_at")   # HL ctxs carry no exchange timestamp
    return raw
