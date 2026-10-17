"""Footprint backfill from real public exchange trades, with explicit coverage.

Never synthesizes trades: a bar is ``complete`` only when every trade of its window
is provably fetched, ``partial`` when the fetched trade window starts inside the bar,
and absent (never interpolated) when no trades of it were fetched.

- Binance USDT-M: ``/fapi/v1/aggTrades`` paged backward by contiguous aggregate id
  (``fromId``), newest bars first, so a request budget truncates *old* history.
  Completeness proof: the oldest fetched trade is strictly older than the bar's open
  and ids are contiguous up to a trade after the bar's close (or up to ``as_of`` for
  the forming bar).
- Bybit linear: ``/v5/market/recent-trade`` returns only the latest ≤1000 trades.
- Hyperliquid: ``recentTrades`` returns only a handful of latest trades.

Closed complete bars are cached per (venue, symbol, interval, open, tick size).
"""

import threading
import time
from collections import OrderedDict, deque
from decimal import Decimal

import footprint as fp
from market_candles import interval_ms

FOOTPRINT_INTERVALS = ("1", "3", "5", "15", "30", "60")
MAX_BARS = 300
BINANCE_PAGE = 1000
DEFAULT_MAX_REQUESTS = 40  # per API call; aggTrades costs 20 weight each on Binance
BINANCE_RATE = (60, 60_000)  # ≤60 aggTrades requests per rolling minute (1200 weight)
DEFAULT_MAX_TRADES = 200_000
TARGET_LEVELS = 120
CACHE_BARS = 4000


class FootprintError(RuntimeError):
    """Safe public error without upstream bodies."""


def _default_request(exchange, path, *, params=None, payload=None):
    import exchange_markets

    try:
        return exchange_markets._request(exchange, path, params=params, payload=payload)
    except exchange_markets.ExchangeDataError as exc:
        raise FootprintError(str(exc)) from None


class _RateWindow:
    def __init__(self, limit, window_ms, clock):
        self.limit, self.window_ms, self.clock = limit, window_ms, clock
        self.stamps = deque()
        self.lock = threading.Lock()

    def take(self) -> bool:
        with self.lock:
            now = self.clock()
            while self.stamps and now - self.stamps[0] >= self.window_ms:
                self.stamps.popleft()
            if len(self.stamps) >= self.limit:
                return False
            self.stamps.append(now)
            return True


class FootprintService:
    def __init__(self, request=None, clock=None, *, max_requests=DEFAULT_MAX_REQUESTS,
                 max_trades=DEFAULT_MAX_TRADES, binance_rate=BINANCE_RATE):
        self._request = request or _default_request
        self._clock = clock or (lambda: int(time.time() * 1000))
        self.max_requests = max_requests
        self.max_trades = max_trades
        self._rate = _RateWindow(*binance_rate, self._clock)
        self._cache: OrderedDict = OrderedDict()
        self._meta: dict = {}
        self._lock = threading.Lock()
        self.stats = {"requests": 0}

    # ── metadata ────────────────────────────────────────────────────────────

    def _call(self, exchange, path, *, params=None, payload=None):
        self.stats["requests"] += 1
        return self._request(exchange, path, params=params, payload=payload)

    def tick_size(self, exchange, symbol, reference_price=None) -> Decimal:
        key = (exchange, symbol)
        cached = self._meta.get(key)
        now = self._clock()
        if cached is None or now - cached[0] > 3_600_000:
            cached = (now, self._load_meta(exchange, symbol))
            self._meta[key] = cached
        meta = cached[1]
        if exchange == "hyperliquid":
            if reference_price is None:
                raise FootprintError("hyperliquid: no trade price to derive tick size")
            return fp.hyperliquid_tick(reference_price, meta)
        return meta

    def _load_meta(self, exchange, symbol):
        try:
            if exchange == "binance":
                data = self._call("binance", "/fapi/v1/exchangeInfo")
                row = next(r for r in data["symbols"] if r.get("symbol") == symbol)
                price_filter = next(f for f in row["filters"] if f.get("filterType") == "PRICE_FILTER")
                return fp.dec(price_filter["tickSize"])
            if exchange == "bybit":
                data = self._call("bybit", "/v5/market/instruments-info",
                                  params={"category": "linear", "symbol": symbol})
                if data.get("retCode") != 0:
                    raise ValueError("rejected")
                row = next(r for r in data["result"]["list"] if r.get("symbol") == symbol)
                return fp.dec(row["priceFilter"]["tickSize"])
            dex = symbol.split(":", 1)[0] if ":" in symbol else ""
            data = self._call("hyperliquid", "/info",
                              payload={"type": "meta", **({"dex": dex} if dex else {})})
            row = next(r for r in data["universe"] if r.get("name") == symbol)
            return fp._int(row["szDecimals"], "szDecimals")
        except FootprintError:
            raise
        except (StopIteration, KeyError, TypeError, ValueError, AttributeError):
            raise FootprintError(f"{exchange}: instrument metadata unavailable") from None

    # ── public entry point ──────────────────────────────────────────────────

    def footprint(self, exchange, symbol, interval, *, limit=60, group_ticks="auto",
                  settings=None, include_forming=True) -> dict:
        if exchange not in fp.NORMALIZERS:
            raise ValueError("unsupported exchange")
        if interval not in FOOTPRINT_INTERVALS:
            raise ValueError("unsupported footprint interval")
        if type(limit) is not int or not 1 <= limit <= MAX_BARS:
            raise ValueError(f"limit must be 1..{MAX_BARS}")
        if group_ticks != "auto" and (type(group_ticks) is not int or not 1 <= group_ticks <= 100_000):
            raise ValueError("group_ticks must be auto or 1..100000")
        settings = settings or fp.DEFAULT_SETTINGS
        duration = interval_ms(interval)
        as_of = self._clock()
        forming_open = as_of // duration * duration
        first_open = forming_open - limit * duration
        opens = [first_open + i * duration for i in range(limit)]
        if include_forming:
            opens.append(forming_open)
        fetch = {"binance": self._binance, "bybit": self._recent, "hyperliquid": self._recent}
        bars, meta = fetch[exchange](exchange, symbol, duration, opens, forming_open, as_of)
        tick = meta["tick_size"]
        if group_ticks == "auto":
            ranges = [int(fp.bucket_index(a.high, tick) - fp.bucket_index(a.low, tick)) + 1
                      for a, _ in bars.values() if a.high is not None]
            group = fp.auto_group_ticks(ranges, TARGET_LEVELS)
        else:
            group = group_ticks
        out_bars = []
        for open_ms in opens:
            if open_ms not in bars:
                continue
            acc, coverage = bars[open_ms]
            bar = fp.bar_payload(acc, group, coverage=coverage, source=exchange, settings=settings)
            bar["forming"] = open_ms == forming_open
            out_bars.append(bar)
        counts = {k: sum(1 for b in out_bars if b["coverage"] == k) for k in ("complete", "partial")}
        warnings = list(meta.get("warnings", []))
        if meta.get("truncated"):
            warnings.append("Request budget reached: older bars are missing, not interpolated.")
        return {
            "exchange": exchange, "symbol": symbol, "interval": interval,
            "tick_size": fp.fmt(tick), "group_ticks": group,
            "group_mode": "auto" if group_ticks == "auto" else "manual",
            "as_of": as_of, "bars": out_bars,
            "coverage": {**counts, "missing": len(opens) - len(out_bars), "requested": len(opens),
                         "trades_from": meta.get("trades_from"), "trades_to": meta.get("trades_to")},
            "truncated": bool(meta.get("truncated")),
            "requests": meta.get("requests", 0),
            "last_trade_id": meta.get("last_trade_id"),
            "convention": "ask = buyer-initiated (taker buy); bid = seller-initiated (taker sell)",
            "warnings": warnings,
        }

    # ── binance: contiguous aggTrade ids, newest-first ──────────────────────

    def _cache_get(self, key):
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
            return hit

    def _cache_put(self, key, acc):
        with self._lock:
            self._cache[key] = acc
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_BARS:
                self._cache.popitem(last=False)

    def _agg_page(self, symbol, params, state):
        if state["requests"] >= self.max_requests or state["trades"] >= self.max_trades:
            state["truncated"] = True
            return None
        if not self._rate.take():
            state["truncated"] = True
            state["warnings"].add("Binance aggTrades rate budget (60 req/min) reached.")
            return None
        state["requests"] += 1
        rows = self._call("binance", "/fapi/v1/aggTrades",
                          params={"symbol": symbol, "limit": BINANCE_PAGE, **params})
        if not isinstance(rows, list):
            raise FootprintError("binance: malformed aggTrades response")
        try:
            trades = sorted((fp.normalize_binance(r) for r in rows), key=lambda t: t.ident)
        except (ValueError, TypeError, AttributeError):
            raise FootprintError("binance: malformed aggTrade") from None
        state["trades"] += len(trades)
        return trades

    def _binance(self, exchange, symbol, duration, opens, forming_open, as_of):
        tick = self.tick_size(exchange, symbol)
        state = {"requests": 0, "trades": 0, "truncated": False, "warnings": set()}
        bars, need = {}, []
        for open_ms in opens:
            hit = None if open_ms == forming_open else self._cache_get(
                ("binance", symbol, duration, open_ms, str(tick)))
            if hit is not None:
                bars[open_ms] = (hit, "complete")
            else:
                need.append(open_ms)
        # contiguous runs of uncached bars, newest run first
        runs, run = [], []
        for open_ms in need:
            if run and open_ms != run[-1] + duration:
                runs.append(run)
                run = []
            run.append(open_ms)
        if run:
            runs.append(run)
        last_id, lo_seen, hi_seen = None, None, None
        for run in reversed(runs):
            run_open, run_close = run[0], run[-1] + duration - 1
            live_anchor = run_close >= as_of  # includes the forming bar
            if not live_anchor:
                window_end = min(run_close + 3_599_999, as_of)
                head = self._agg_page(symbol, {"startTime": run_close + 1, "endTime": window_end},
                                      state)
                if head is None:
                    break
                if not head and window_end < as_of:
                    state["warnings"].add("No trades within an hour after a bar run; skipped.")
                    continue
                live_anchor = not head  # no trade since run close → latest trades anchor it
            if live_anchor:
                page = self._agg_page(symbol, {}, state)
            else:
                anchor = head[0].ident
                page = self._agg_page(symbol, {"fromId": max(0, anchor - BINANCE_PAGE)}, state)
                page = None if page is None else [t for t in page if t.ident < anchor]
            if page is None:
                break
            if live_anchor:
                page = [t for t in page if t.time_ms <= as_of]
            collected = list(page)
            while collected and collected[0].time_ms >= run_open and collected[0].ident > 0:
                cursor = collected[0].ident
                older = self._agg_page(symbol, {"fromId": max(0, cursor - BINANCE_PAGE)}, state)
                if older is None:
                    break
                older = [t for t in older if t.ident < cursor]
                if not older:
                    break
                collected = older + collected
            if not collected:
                continue
            ids = [t.ident for t in collected]
            if any(b - a != 1 for a, b in zip(ids, ids[1:])):
                raise FootprintError("binance: non-contiguous aggTrade ids")
            if live_anchor and (last_id is None or ids[-1] > last_id):
                last_id = ids[-1]
            oldest = collected[0].time_ms
            lo_seen = oldest if lo_seen is None else min(lo_seen, oldest)
            hi_seen = collected[-1].time_ms if hi_seen is None else max(hi_seen, collected[-1].time_ms)
            accs = {o: fp.BarAccumulator(o, duration, tick) for o in run}
            for t in collected:
                o = t.time_ms // duration * duration
                if o in accs:
                    accs[o].add(t)
            for o, acc in accs.items():
                if o + duration - 1 < oldest or acc.trade_count == 0 and o <= oldest:
                    continue  # nothing of this bar fetched → missing, never interpolated
                coverage = "complete" if oldest < o else "partial"
                bars[o] = (acc, coverage)
                if coverage == "complete" and o != forming_open:
                    self._cache_put(("binance", symbol, duration, o, str(tick)), acc)
        warnings = sorted(state["warnings"])
        if forming_open in opens:
            warnings.append("Forming bar is a REST snapshot as of 'as_of'; live trades merge client-side.")
        return bars, {"tick_size": tick, "truncated": state["truncated"], "requests": state["requests"],
                      "last_trade_id": last_id, "trades_from": lo_seen, "trades_to": hi_seen,
                      "warnings": warnings}

    # ── bybit / hyperliquid: latest trades only ─────────────────────────────

    def _recent(self, exchange, symbol, duration, opens, forming_open, as_of):
        try:
            if exchange == "bybit":
                data = self._call("bybit", "/v5/market/recent-trade",
                                  params={"category": "linear", "symbol": symbol, "limit": 1000})
                if data.get("retCode") != 0:
                    raise FootprintError("bybit: API response rejected")
                rows = [r for r in data["result"]["list"] if r.get("symbol") == symbol]
            else:
                rows = self._call("hyperliquid", "/info", payload={"type": "recentTrades", "coin": symbol})
                rows = [r for r in rows if r.get("coin") == symbol]
            trades = sorted((fp.NORMALIZERS[exchange](r) for r in rows), key=lambda t: t.time_ms)
        except FootprintError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            raise FootprintError(f"{exchange}: malformed recent trades") from None
        trades = [t for t in trades if t.time_ms <= as_of]
        label = "Bybit recent-trade (≤1000 latest)" if exchange == "bybit" else "Hyperliquid recentTrades"
        warnings = [f"{label} is the only public trade history; older bars are missing or partial."]
        if not trades:
            tick = self.tick_size(exchange, symbol, None) if exchange == "bybit" else None
            if tick is None:
                raise FootprintError(f"{exchange}: no recent trades")
            return {}, {"tick_size": tick, "warnings": warnings, "requests": 1}
        tick = self.tick_size(exchange, symbol, trades[-1].price)
        oldest = trades[0].time_ms
        accs = {o: fp.BarAccumulator(o, duration, tick) for o in opens}
        for t in trades:
            o = t.time_ms // duration * duration
            if o in accs:
                accs[o].add(t)
        bars = {}
        for o, acc in accs.items():
            if acc.trade_count == 0:
                continue
            # Only provable when the contiguous latest-trade window starts before the bar.
            bars[o] = (acc, "complete" if oldest < o else "partial")
        return bars, {"tick_size": tick, "warnings": warnings, "requests": 1,
                      "trades_from": oldest, "trades_to": trades[-1].time_ms,
                      "last_trade_id": None}
