"""Footprint fetchers with injected fake venues (no network)."""
from decimal import Decimal

import pytest

import footprint_data as fd

MIN = 60_000
T0 = 1_700_000_040_000  # minute-aligned


class FakeBinance:
    """aggTrades semantics: latest page, startTime/endTime window, fromId paging."""

    def __init__(self, trades, tick="0.10", kline_override=None):
        self.trades = trades  # list of dict rows in id order
        self.tick = tick
        self.kline_override = kline_override or {}
        self.calls = []

    def __call__(self, exchange, path, *, params=None, payload=None):
        self.calls.append((path, dict(params or {})))
        assert exchange == "binance"
        if path == "/fapi/v1/exchangeInfo":
            return {"symbols": [{"symbol": "BTCUSDT", "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                {"filterType": "PRICE_FILTER", "tickSize": self.tick}]}]}
        if path == "/fapi/v1/klines":
            assert params["interval"] == "1m" and params["limit"] <= 1000
            rows = []
            for o, (vol, buy) in sorted(expected_totals(self.trades).items()):
                if params["startTime"] <= o <= params["endTime"]:
                    vol, buy = self.kline_override.get(o, (vol, buy))
                    rows.append([o, "1", "1", "1", "1", str(vol), o + MIN - 1, "0", 0, str(buy), "0", "0"])
            return rows
        assert path == "/fapi/v1/aggTrades"
        limit = params["limit"]
        if "fromId" in params:
            rows = [t for t in self.trades if t["a"] >= params["fromId"]][:limit]
        elif "startTime" in params:
            assert params["endTime"] - params["startTime"] < 3_600_000
            rows = [t for t in self.trades if params["startTime"] <= t["T"] <= params["endTime"]][:limit]
        else:
            rows = self.trades[-limit:]
        return [dict(r) for r in rows]


def make_trades(n_per_bar, bars, start=T0, first_id=1000):
    rows, ident = [], first_id
    for b in range(bars):
        for k in range(n_per_bar):
            t = start + b * MIN + k * (MIN // n_per_bar)
            rows.append({"a": ident, "p": f"{100 + (k % 7) / 10:.1f}", "q": f"0.{k % 9 + 1:03d}",
                         "f": ident * 2, "l": ident * 2 + (k % 3), "T": t, "m": k % 2 == 0})
            ident += 1
    return rows


def expected_totals(rows):
    out = {}
    for r in rows:
        o = r["T"] // MIN * MIN
        tot, ask = out.get(o, (Decimal(0), Decimal(0)))
        out[o] = (tot + Decimal(r["q"]), ask + (Decimal(0) if r["m"] else Decimal(r["q"])))
    return out


def service(fake, now, **kw):
    clock = {"now": now}
    svc = fd.FootprintService(request=fake, clock=lambda: clock["now"], **kw)
    return svc, clock


def test_binance_reconciles_every_complete_bar_exactly_and_marks_forming():
    rows = make_trades(600, 12)  # 12 bars; bar 11 is forming
    fake = FakeBinance(rows)
    now = T0 + 11 * MIN + 30_000
    rows_until_now = [r for r in rows if r["T"] <= now]
    fake.trades = rows_until_now
    svc, _ = service(fake, now, max_requests=100)
    out = svc.footprint("binance", "BTCUSDT", "1", limit=10)
    assert out["truncated"] is False
    assert [b["time"] * 1000 for b in out["bars"]] == [T0 + i * MIN for i in range(1, 12)]
    want = expected_totals(rows_until_now)
    for bar in out["bars"]:
        total, ask = want[bar["open_time"]]
        assert Decimal(bar["total"]) == total and Decimal(bar["buy_vol"]) == ask
        assert bar["coverage"] == "complete"
    assert out["bars"][-1]["forming"] is True and out["bars"][0]["forming"] is False
    assert out["last_trade_id"] == rows_until_now[-1]["a"]
    assert out["coverage"]["missing"] == 0 and out["coverage"]["requested"] == 11
    assert any("Forming bar" in w for w in out["warnings"])
    checks = [b["kline_check"]["status"] for b in out["bars"]]
    assert checks == ["exact"] * 10 + ["unchecked"]  # the forming bar has no closed kline


def test_kline_cross_check_flags_boundary_straddle_without_redistributing():
    rows = make_trades(100, 4, start=T0 - MIN)
    want = expected_totals(rows)
    # Venue attributes 0.005 of bar 1's taker-buy volume to bar 2 (aggregate across the boundary).
    shifted = {T0 + MIN: (want[T0 + MIN][0] - Decimal("0.005"), want[T0 + MIN][1] - Decimal("0.005")),
               T0 + 2 * MIN: (want[T0 + 2 * MIN][0] + Decimal("0.005"), want[T0 + 2 * MIN][1] + Decimal("0.005"))}
    svc, _ = service(FakeBinance(rows, kline_override=shifted), T0 + 3 * MIN + 1)
    out = svc.footprint("binance", "BTCUSDT", "1", limit=3, include_forming=False)
    by_open = {b["open_time"]: b for b in out["bars"]}
    assert by_open[T0]["kline_check"]["status"] == "exact"
    assert by_open[T0 + MIN]["kline_check"] == {
        "status": "mismatch", "kline_volume": str(shifted[T0 + MIN][0].normalize()),
        "kline_taker_buy": str(shifted[T0 + MIN][1].normalize()),
        "volume_diff": "0.005", "taker_buy_diff": "0.005"}
    assert by_open[T0 + 2 * MIN]["kline_check"]["volume_diff"] == "-0.005"
    # Trades stay where the venue timestamped them; nothing is moved or invented.
    assert Decimal(by_open[T0 + MIN]["total"]) == want[T0 + MIN][0]
    assert by_open[T0 + MIN]["coverage"] == "complete"
    assert any("straddling" in w for w in out["warnings"])


def test_budget_truncates_oldest_history_newest_first_never_interpolates():
    rows = make_trades(600, 12)
    now = T0 + 12 * MIN + 1  # bar 12 forming with zero trades yet
    # 4 requests = 1 probe after the run (empty) + 3 pages = newest 3000 trades = bars 7..11.
    svc, _ = service(FakeBinance(rows), now, max_requests=4)
    out = svc.footprint("binance", "BTCUSDT", "1", limit=12, include_forming=False)
    assert out["truncated"] is True
    coverages = [(b["open_time"], b["coverage"]) for b in out["bars"]]
    complete = [o for o, c in coverages if c == "complete"]
    partial = [o for o, c in coverages if c == "partial"]
    assert complete == [T0 + i * MIN for i in range(8, 12)]
    # The oldest fetched trade sits exactly at bar 7's open: an earlier same-ms trade
    # cannot be excluded, so bar 7 is only partial (strict proof, never assumed).
    assert partial == [T0 + 7 * MIN]
    assert out["coverage"]["missing"] == 7
    assert any("budget" in w for w in out["warnings"])
    want = expected_totals(rows)
    for bar in out["bars"]:
        if bar["coverage"] == "complete":
            assert Decimal(bar["total"]) == want[bar["open_time"]][0]
        else:  # here it happens to hold every trade, but completeness is unprovable
            assert Decimal(bar["total"]) <= want[bar["open_time"]][0]


def test_closed_complete_bars_are_cached_and_extend_history_progressively():
    rows = make_trades(600, 13, start=T0 - MIN)  # one older bar proves bar T0's start
    now = T0 + 12 * MIN + 1
    fake = FakeBinance(rows)
    svc, _ = service(fake, now, max_requests=4)
    first = svc.footprint("binance", "BTCUSDT", "1", limit=12, include_forming=False)
    calls = len(fake.calls)
    second = svc.footprint("binance", "BTCUSDT", "1", limit=12, include_forming=False)
    assert second["coverage"]["complete"] > first["coverage"]["complete"]
    assert len(fake.calls) - calls <= 5  # only the older uncached run is fetched
    third = svc.footprint("binance", "BTCUSDT", "1", limit=12, include_forming=False)
    assert third["coverage"]["complete"] == 12 and third["truncated"] is False
    calls = len(fake.calls)
    again = svc.footprint("binance", "BTCUSDT", "1", limit=12, include_forming=False)
    assert len(fake.calls) == calls and again["requests"] == 0
    assert [b["total"] for b in again["bars"]] == [b["total"] for b in third["bars"]]


def test_rate_window_caps_aggtrade_requests_across_calls():
    rows = make_trades(600, 12)
    svc, clock = service(FakeBinance(rows), T0 + 12 * MIN + 1, max_requests=100, binance_rate=(3, MIN))
    out = svc.footprint("binance", "BTCUSDT", "1", limit=12, include_forming=False)
    assert out["truncated"] and out["requests"] == 3
    assert any("rate budget" in w for w in out["warnings"])


def test_non_contiguous_ids_are_rejected_not_silently_merged():
    rows = make_trades(100, 3)
    del rows[150]
    svc, _ = service(FakeBinance(rows), T0 + 3 * MIN + 1)
    with pytest.raises(fd.FootprintError):
        svc.footprint("binance", "BTCUSDT", "1", limit=3, include_forming=False)


def test_auto_and_manual_grouping():
    rows = make_trades(300, 3)
    svc, _ = service(FakeBinance(rows), T0 + 3 * MIN + 1)
    auto = svc.footprint("binance", "BTCUSDT", "1", limit=3, include_forming=False)
    assert auto["group_mode"] == "auto" and auto["group_ticks"] == 1  # range 7 ticks
    manual = svc.footprint("binance", "BTCUSDT", "1", limit=3, include_forming=False, group_ticks=5)
    assert manual["group_ticks"] == 5
    assert {lv["price"] for lv in manual["bars"][0]["levels"]} == {"100", "100.5"}


@pytest.mark.parametrize("kwargs", [
    {"exchange": "kraken"}, {"interval": "D"}, {"limit": 0}, {"limit": 301}, {"group_ticks": 0},
    {"group_ticks": "3"},
])
def test_invalid_queries_raise_value_error(kwargs):
    svc, _ = service(FakeBinance([]), T0)
    args = {"exchange": "binance", "symbol": "BTCUSDT", "interval": "1", **kwargs}
    extra = {k: args.pop(k) for k in ("limit", "group_ticks") if k in args}
    with pytest.raises(ValueError):
        svc.footprint(args["exchange"], args["symbol"], args["interval"], **extra)


def bybit_fake(rows):
    def request(exchange, path, *, params=None, payload=None):
        if path == "/v5/market/kline":
            return {"retCode": 0, "result": {"list": [[str(T0 + MIN), "1", "1", "1", "1", "3", "0"]]}}
        if path == "/v5/market/instruments-info":
            return {"retCode": 0, "result": {"list": [{"symbol": "BTCUSDT",
                                                       "priceFilter": {"tickSize": "0.10"}}]}}
        assert path == "/v5/market/recent-trade" and params["limit"] == 1000
        return {"retCode": 0, "result": {"list": list(reversed(rows))}}  # newest first
    return request


def test_bybit_recent_trades_bar_containing_oldest_trade_is_partial():
    rows = [{"execId": str(i), "symbol": "BTCUSDT", "price": "100.0", "size": "1",
             "side": "Buy" if i % 2 else "Sell", "time": str(T0 + 30_000 + i * 20_000)}
            for i in range(9)]  # 30s into bar 0 → 190s
    svc, _ = service(bybit_fake(rows), T0 + 3 * MIN + 10_000)
    out = svc.footprint("bybit", "BTCUSDT", "1", limit=5)
    cov = {b["open_time"]: (b["coverage"], b["total"]) for b in out["bars"]}
    assert cov[T0][0] == "partial"
    assert cov[T0 + MIN][0] == "complete" and cov[T0 + 2 * MIN][0] == "complete"
    assert T0 - MIN not in cov and out["coverage"]["missing"] == 2  # two older bars: blank
    assert cov[T0 + 3 * MIN] == ("complete", "1") and out["bars"][-1]["forming"] is True
    checks = {b["open_time"]: b["kline_check"]["status"] for b in out["bars"]}
    assert checks == {T0: "unchecked", T0 + MIN: "exact", T0 + 2 * MIN: "unchecked",
                      T0 + 3 * MIN: "unchecked"}  # partial/forming never checked; missing kline row
    assert out["bars"][1]["buy_vol"] == "1" and out["bars"][1]["sell_vol"] == "2"  # 70s S, 90s B, 110s S
    assert any("≤1000" in w for w in out["warnings"])
    # ids of snapshot trades within 5s of as_of let the browser dedupe live trades exactly
    assert out["recent_ids"] == ["8"] and out["dedupe_window_ms"] == 5000


def test_hyperliquid_tick_from_sz_decimals_and_side_mapping():
    def request(exchange, path, *, params=None, payload=None):
        if payload["type"] == "meta":
            return {"universe": [{"name": "BTC", "szDecimals": 5}]}
        return [{"coin": "BTC", "side": "B", "px": "81528.0", "sz": "0.5", "time": T0 + 61_000, "tid": 1},
                {"coin": "BTC", "side": "A", "px": "81527.0", "sz": "0.25", "time": T0 + 62_000, "tid": 2},
                {"coin": "ETH", "side": "A", "px": "3000", "sz": "9", "time": T0 + 62_000, "tid": 3}]
    svc, _ = service(request, T0 + 63_000)
    out = svc.footprint("hyperliquid", "BTC", "1", limit=3)
    assert out["tick_size"] == "1"
    [bar] = out["bars"]
    assert bar["coverage"] == "partial" and bar["forming"] is True
    assert (bar["buy_vol"], bar["sell_vol"]) == ("0.5", "0.25")
