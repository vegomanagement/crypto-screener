"""Footprint (bid x ask order-flow) aggregation — pure functions, no I/O.

Conventions (see docs/FOOTPRINT_PLAN.md):
- ask volume = buyer-initiated (taker buy), bid volume = seller-initiated (taker sell);
- every price/quantity is an exact ``Decimal`` parsed from the venue's decimal string,
  floats are rejected so bucketing never drifts (0.3/0.1 must be bucket 3, not 2);
- a level bucket is ``floor(price / (tick_size * group_ticks))``;
- ties are resolved toward the lower price so Python and ``static/footprint.js`` agree.

The same rules are implemented with BigInt in ``static/footprint.js``; the shared
fixture ``tests/fixtures/footprint_parity.json`` is asserted by both test suites.
"""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Context, Decimal, InvalidOperation

SIDES = ("bid", "ask")
COVERAGE = frozenset({"complete", "partial"})
DEFAULT_SETTINGS = {
    "input": "volume",  # or "trades" (number of trades)
    "va_pct": 70,
    "imbalance_ratio": Decimal(300),  # percent, ask@p vs bid@(p-1 level)
    "imbalance_min_diff": Decimal(0),
    "include_zero": False,
    "stacked_min": 3,
    "min_delta": Decimal(0),
}
_CTX = Context(prec=80)
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class Trade:
    time_ms: int
    price: Decimal
    qty: Decimal
    side: str  # "bid" (seller aggressor) or "ask" (buyer aggressor)
    count: int = 1
    ident: object = None


def dec(value) -> Decimal:
    """Exact non-negative finite decimal from a venue string/int; floats are refused."""
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("decimal string or int required")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str) and value.strip():
        try:
            result = Decimal(value.strip())
        except InvalidOperation:
            raise ValueError("invalid decimal") from None
    else:
        raise ValueError("invalid decimal")
    if not result.is_finite() or result < 0 or abs(result.adjusted()) > 30:
        raise ValueError("decimal out of range")
    return result


def _int(value, name="integer") -> int:
    if isinstance(value, bool):
        raise ValueError(f"invalid {name}")
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid {name}")
    return value


def fmt(value: Decimal | None) -> str | None:
    """Plain decimal string without exponent or trailing zeros ("0.20" -> "0.2")."""
    if value is None:
        return None
    text = format(value.normalize(_CTX), "f")
    return "0" if text in ("-0", "0", "") else text


def bucket_index(price: Decimal, step: Decimal) -> int:
    if step <= 0:
        raise ValueError("step must be positive")
    return int(_CTX.divide(price, step).to_integral_value(rounding=ROUND_FLOOR))


def bucket_price(index: int, step: Decimal) -> Decimal:
    return _CTX.multiply(Decimal(index), step)


def _group(group_ticks) -> int:
    if isinstance(group_ticks, bool) or not isinstance(group_ticks, int) or group_ticks < 1:
        raise ValueError("group_ticks must be a positive integer")
    return group_ticks


# ── venue normalisation ──────────────────────────────────────────────────────

def normalize_binance(row: dict) -> Trade:
    """USDT-M aggTrade (REST or WS ``aggTrade``). ``m`` = buyer is maker -> seller hit bid."""
    if not isinstance(row.get("m"), bool):
        raise ValueError("binance: missing maker flag")
    first, last = _int(row.get("f"), "first id"), _int(row.get("l"), "last id")
    if last < first:
        raise ValueError("binance: invalid trade id range")
    return Trade(_int(row.get("T"), "time"), dec(row.get("p")), dec(row.get("q")),
                 "bid" if row["m"] else "ask", last - first + 1, _int(row.get("a"), "id"))


def normalize_bybit(row: dict) -> Trade:
    """Bybit linear trade; REST ``recent-trade`` or WS ``publicTrade`` shape. Side = taker."""
    side = row.get("side", row.get("S"))
    if side not in ("Buy", "Sell"):
        raise ValueError("bybit: invalid taker side")
    return Trade(_int(row.get("time", row.get("T")), "time"),
                 dec(row.get("price", row.get("p"))), dec(row.get("size", row.get("v"))),
                 "ask" if side == "Buy" else "bid", 1, row.get("execId", row.get("i")))


def normalize_hyperliquid(row: dict) -> Trade:
    """Hyperliquid trade; ``side`` B = buy aggressor (ask), A = sell aggressor (bid)."""
    side = row.get("side")
    if side not in ("B", "A"):
        raise ValueError("hyperliquid: invalid side")
    return Trade(_int(row.get("time"), "time"), dec(row.get("px")), dec(row.get("sz")),
                 "ask" if side == "B" else "bid", 1, row.get("tid"))


NORMALIZERS = {
    "binance": normalize_binance,
    "bybit": normalize_bybit,
    "hyperliquid": normalize_hyperliquid,
}


def hyperliquid_tick(price: Decimal, sz_decimals: int) -> Decimal:
    """HL perps: ≤5 significant figures and ≤(6 - szDecimals) decimals; integers always valid."""
    if price <= 0:
        raise ValueError("price must be positive")
    significant = Decimal(1).scaleb(price.adjusted() - 4)
    decimals = Decimal(1).scaleb(-(6 - _int(sz_decimals, "szDecimals")))
    return min(Decimal(1), max(significant, decimals))


# ── accumulation ─────────────────────────────────────────────────────────────

class BarAccumulator:
    """Exact per-tick bid/ask volume and trade counts for one bar window."""

    def __init__(self, open_ms: int, interval_ms: int, tick_size: Decimal):
        self.open_ms = _int(open_ms, "open_ms")
        self.interval_ms = _int(interval_ms, "interval_ms")
        if self.interval_ms <= 0:
            raise ValueError("interval_ms must be positive")
        self.tick_size = dec(tick_size)
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive")
        self.levels: dict[int, list] = {}
        self.trade_count = 0
        self.open = self.high = self.low = self.close = None
        self._first = self._last = None

    @property
    def close_ms(self) -> int:
        return self.open_ms + self.interval_ms - 1

    def add(self, trade: Trade) -> bool:
        """Add a trade inside ``[open_ms, close_ms]``; returns False (ignored) otherwise."""
        if not self.open_ms <= trade.time_ms <= self.close_ms:
            return False
        if trade.side not in SIDES or trade.count < 1:
            raise ValueError("invalid trade")
        cell = self.levels.setdefault(bucket_index(trade.price, self.tick_size),
                                      [Decimal(0), Decimal(0), 0, 0])
        k = 0 if trade.side == "bid" else 1
        cell[k] = _CTX.add(cell[k], trade.qty)
        cell[k + 2] += trade.count
        self.trade_count += trade.count
        if self._first is None or trade.time_ms < self._first:
            self._first, self.open = trade.time_ms, trade.price
        if self._last is None or trade.time_ms >= self._last:
            self._last, self.close = trade.time_ms, trade.price
        self.high = trade.price if self.high is None else max(self.high, trade.price)
        self.low = trade.price if self.low is None else min(self.low, trade.price)
        return True

    def volumes(self) -> tuple[Decimal, Decimal]:
        """(bid/sell volume, ask/buy volume) at full tick resolution."""
        bid = sum((c[0] for c in self.levels.values()), Decimal(0))
        ask = sum((c[1] for c in self.levels.values()), Decimal(0))
        return bid, ask


def grouped_levels(acc: BarAccumulator, group_ticks: int) -> list[dict]:
    group = _group(group_ticks)
    step = _CTX.multiply(acc.tick_size, Decimal(group))
    merged: dict[int, list] = {}
    for tick_index, (bid, ask, bid_n, ask_n) in acc.levels.items():
        cell = merged.setdefault(tick_index // group, [Decimal(0), Decimal(0), 0, 0])
        cell[0] = _CTX.add(cell[0], bid)
        cell[1] = _CTX.add(cell[1], ask)
        cell[2] += bid_n
        cell[3] += ask_n
    return [
        {"index": i, "price": bucket_price(i, step), "bid": c[0], "ask": c[1],
         "bid_trades": c[2], "ask_trades": c[3]}
        for i, c in sorted(merged.items())
    ]


# ── analytics ────────────────────────────────────────────────────────────────

def settings_from_json(raw: dict | None) -> dict:
    raw = raw or {}
    unknown = set(raw) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ValueError(f"unknown settings: {sorted(unknown)}")
    out = dict(DEFAULT_SETTINGS)
    if "input" in raw:
        if raw["input"] not in ("volume", "trades"):
            raise ValueError("input must be volume or trades")
        out["input"] = raw["input"]
    if "va_pct" in raw:
        va = _int(raw["va_pct"], "va_pct")
        if not 1 <= va <= 100:
            raise ValueError("va_pct must be 1..100")
        out["va_pct"] = va
    if "imbalance_ratio" in raw:
        ratio = dec(raw["imbalance_ratio"])
        if ratio < 100:
            raise ValueError("imbalance_ratio must be >= 100")
        out["imbalance_ratio"] = ratio
    for key in ("imbalance_min_diff", "min_delta"):
        if key in raw:
            out[key] = dec(raw[key])
    if "include_zero" in raw:
        if not isinstance(raw["include_zero"], bool):
            raise ValueError("include_zero must be boolean")
        out["include_zero"] = raw["include_zero"]
    if "stacked_min" in raw:
        stacked = _int(raw["stacked_min"], "stacked_min")
        if stacked < 2:
            raise ValueError("stacked_min must be >= 2")
        out["stacked_min"] = stacked
    return out


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal:
    return _CTX.divide(numerator * 100, denominator).quantize(_CENT, rounding=ROUND_HALF_UP)


def analyze(levels: list[dict], step: Decimal, settings: dict | None = None) -> dict:
    """POC, delta POC, value area, diagonal + stacked imbalances for grouped levels."""
    s = settings or DEFAULT_SETTINGS
    if s["input"] == "trades":
        rows = {lv["index"]: (Decimal(lv["bid_trades"]), Decimal(lv["ask_trades"])) for lv in levels}
    else:
        rows = {lv["index"]: (lv["bid"], lv["ask"]) for lv in levels}
    out = {"total": Decimal(0), "buy_vol": Decimal(0), "sell_vol": Decimal(0),
           "delta": Decimal(0), "delta_pct": None, "poc": None, "delta_poc": None,
           "va_low": None, "va_high": None, "imbalances": [], "stacked": []}
    if not rows:
        return out
    lo, hi = min(rows), max(rows)
    zero = (Decimal(0), Decimal(0))
    bid = {i: rows.get(i, zero)[0] for i in range(lo, hi + 1)}
    ask = {i: rows.get(i, zero)[1] for i in range(lo, hi + 1)}
    vol = {i: bid[i] + ask[i] for i in bid}
    sell, buy = sum(bid.values(), Decimal(0)), sum(ask.values(), Decimal(0))
    total = sell + buy
    out.update(total=total, buy_vol=buy, sell_vol=sell, delta=buy - sell)
    price = lambda i: bucket_price(i, step)  # noqa: E731
    if total > 0:
        out["delta_pct"] = _pct(buy - sell, total)
        poc = min(vol, key=lambda i: (-vol[i], i))
        out["poc"] = price(poc)
        # Value area: grow from POC toward the larger adjacent level (ties go up).
        target, acc, va_lo, va_hi = _CTX.divide(total * s["va_pct"], Decimal(100)), vol[poc], poc, poc
        while acc < target and (va_lo > lo or va_hi < hi):
            up = vol[va_hi + 1] if va_hi < hi else None
            down = vol[va_lo - 1] if va_lo > lo else None
            if down is None or (up is not None and up >= down):
                va_hi += 1
                acc += up
            else:
                va_lo -= 1
                acc += down
        out["va_low"], out["va_high"] = price(va_lo), price(va_hi)
    deltas = {i: ask[i] - bid[i] for i in bid}
    best = min(deltas, key=lambda i: (-abs(deltas[i]), i))
    if deltas[best] != 0 and abs(deltas[best]) >= s["min_delta"]:
        out["delta_poc"] = price(best)

    flags = {"buy": set(), "sell": set()}
    for i in range(lo, hi + 1):
        for side, num, den in (("buy", ask[i], bid.get(i - 1)), ("sell", bid[i], ask.get(i + 1))):
            if den is None or num <= 0 or num - den < s["imbalance_min_diff"]:
                continue
            if den == 0:
                if not s["include_zero"]:
                    continue
                ratio = None
            elif num * 100 >= s["imbalance_ratio"] * den:
                ratio = _pct(num, den)
            else:
                continue
            flags[side].add(i)
            out["imbalances"].append({"price": price(i), "side": side, "ratio": ratio})
    for side in ("buy", "sell"):
        run = []
        for i in range(lo, hi + 2):
            if i in flags[side]:
                run.append(i)
                continue
            if len(run) >= s["stacked_min"]:
                out["stacked"].append({"side": side, "from": price(run[0]), "to": price(run[-1]),
                                       "count": len(run)})
            run = []
    out["stacked"].sort(key=lambda z: (z["from"], z["side"]))
    return out


def bar_payload(acc: BarAccumulator, group_ticks: int, *, coverage: str, source: str,
                settings: dict | None = None) -> dict:
    """JSON-safe bar: decimals as exact strings. ``total/delta/buy/sell`` are always volume."""
    if coverage not in COVERAGE:
        raise ValueError("coverage must be complete or partial")
    settings = settings or DEFAULT_SETTINGS
    group = _group(group_ticks)
    step = _CTX.multiply(acc.tick_size, Decimal(group))
    levels = grouped_levels(acc, group)
    volume = analyze(levels, step, {**settings, "input": "volume"})
    stats = volume if settings["input"] == "volume" else analyze(levels, step, settings)
    return {
        "time": acc.open_ms // 1000,
        "open_time": acc.open_ms,
        "close_time": acc.close_ms,
        "open": fmt(acc.open), "high": fmt(acc.high), "low": fmt(acc.low), "close": fmt(acc.close),
        "tick_size": fmt(acc.tick_size),
        "group_ticks": group,
        "levels": [
            {"price": fmt(lv["price"]), "bid": fmt(lv["bid"]), "ask": fmt(lv["ask"]),
             "trades": lv["bid_trades"] + lv["ask_trades"],
             "bid_trades": lv["bid_trades"], "ask_trades": lv["ask_trades"]}
            for lv in levels
        ],
        "trade_count": acc.trade_count,
        "total": fmt(volume["total"]),
        "buy_vol": fmt(volume["buy_vol"]),
        "sell_vol": fmt(volume["sell_vol"]),
        "delta": fmt(volume["delta"]),
        "delta_pct": fmt(volume["delta_pct"]),
        "input": settings["input"],
        "poc": fmt(stats["poc"]),
        "delta_poc": fmt(stats["delta_poc"]),
        "va_low": fmt(stats["va_low"]),
        "va_high": fmt(stats["va_high"]),
        "imbalances": [{"price": fmt(m["price"]), "side": m["side"], "ratio": fmt(m["ratio"])}
                       for m in stats["imbalances"]],
        "stacked": [{"side": z["side"], "from": fmt(z["from"]), "to": fmt(z["to"]),
                     "count": z["count"]} for z in stats["stacked"]],
        "coverage": coverage,
        "source": source,
    }


_NICE = (1, 2, 5)


def auto_group_ticks(ranges_in_ticks, target_levels: int = 60) -> int:
    """Smallest 1-2-5 multiple keeping every bar at ≤ ``target_levels`` grouped levels."""
    widest = max((int(r) for r in ranges_in_ticks), default=0)
    scale = 1
    while True:
        for nice in _NICE:
            group = nice * scale
            if -(-widest // group) <= target_levels:
                return group
        scale *= 10
