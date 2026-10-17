"""Positioning normalisers, window maths and snapshot assembly against recorded live responses."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

import positioning as P
from positioning_snapshot import build_snapshot

FIX = Path(__file__).parent / "fixtures" / "positioning"
NOW = 1792259000000


def load(name):
    return json.loads((FIX / name).read_text())


def binance_raw(sym="BTCUSDT"):
    return {"top_account": load(f"binance_topLongShortAccountRatio_{sym}.json"),
            "top_position": load(f"binance_topLongShortPositionRatio_{sym}.json"),
            "global_account": load(f"binance_globalLongShortAccountRatio_{sym}.json"),
            "taker": load(f"binance_takerlongshortRatio_{sym}.json"),
            "oi_hist": load(f"binance_openInterestHist_{sym}.json"),
            "oi_now": load(f"binance_openInterest_{sym}.json"),
            "basis": load(f"binance_basis_{sym}.json"),
            "premium": load(f"binance_premiumIndex_{sym}.json"),
            "klines": load(f"binance_klines_{sym}.json")}


def bybit_raw(sym="BTCUSDT"):
    return {"by_account_ratio": load(f"bybit_account_ratio_{sym}.json"),
            "by_oi_hist": load(f"bybit_open_interest_{sym}.json"),
            "by_ticker": load(f"bybit_tickers_{sym}.json"),
            "by_klines": load(f"bybit_kline_{sym}.json")}


def test_decimal_helpers_keep_exact_digits():
    assert P.fmt(P.dec("0.64500000")) == "0.645"
    assert P.fmt(P.dec("94231.042")) == "94231.042"
    assert P.dec("") is None and P.dec(None) is None
    with pytest.raises(P.NormaliseError):
        P.dec("NaN")
    assert P.pct_change(Decimal("200"), Decimal("201")) == Decimal("0.50")
    assert P.pct_change(Decimal("0"), Decimal("1")) is None


@pytest.mark.parametrize("sym", ["BTCUSDT", "ETHUSDT"])
def test_binance_ratio_rows_match_raw_latest(sym):
    raw = load(f"binance_topLongShortPositionRatio_{sym}.json")
    rows = P.binance_ratio_rows(raw)
    latest = max(raw, key=lambda r: r["timestamp"])
    assert rows[-1]["ts"] == latest["timestamp"]
    assert rows[-1]["ratio"] == Decimal(latest["longShortRatio"])
    assert rows[-1]["long"] + rows[-1]["short"] == Decimal("1")
    assert [r["ts"] for r in rows] == sorted(r["ts"] for r in rows)


def test_binance_error_payload_is_reported():
    with pytest.raises(P.NormaliseError, match="-1121"):
        P.binance_ratio_rows({"code": -1121, "msg": "Invalid symbol."})


def test_bybit_account_ratio_and_single_side_oi():
    rows = P.bybit_account_ratio(load("bybit_account_ratio_BTCUSDT.json"))
    assert rows[-1]["long"] == Decimal("0.6283") and rows[-1]["short"] == Decimal("0.3717")
    assert rows[-1]["ratio"] == Decimal("1.6903")   # 0.6283 / 0.3717 = 1.69034.. -> 4 dp
    oi = P.bybit_open_interest(load("bybit_open_interest_BTCUSDT.json"))
    assert oi[-1]["oi"] == Decimal("29658.573")      # singleOpenInterest (one side)
    assert oi[-1]["oi_both_sides"] == Decimal("59317.146")
    with pytest.raises(P.NormaliseError):
        P.bybit_list({"retCode": 10001, "retMsg": "params error"})


def test_hl_asset_ctx_uses_universe_index():
    ctx = P.hl_asset_ctx(load("hl_metaAndAssetCtxs.json"), "ETH")
    assert ctx["oi"] == Decimal("1059663.7822") and ctx["mark"] == Decimal("2406.5")
    with pytest.raises(P.NormaliseError):
        P.hl_asset_ctx(load("hl_metaAndAssetCtxs.json"), "NOPE")


def test_window_changes_align_oi_and_price_at_same_stamps():
    hist = P.binance_oi_hist(load("binance_openInterestHist_BTCUSDT.json"))
    klines = P.binance_klines(load("binance_klines_BTCUSDT.json"))
    ch = P.window_changes(hist, "oi", price_rows=klines)
    four = ch["4h"]
    assert four["from"] == Decimal("94432.61") and four["to"] == Decimal("94287.285")
    assert four["pct"] == Decimal("-0.15")          # (94287.285-94432.61)/94432.61 = -0.1539%
    assert four["price_pct"] == Decimal("-0.32")    # opens 81485.50 -> 81226.70
    assert ch["24h"]["from_ts"] == four["to_ts"] - 24 * P.HOUR_MS
    assert P.window_changes([], "oi")["1h"] is None


def test_binance_snapshot_fields_trace_to_raw():
    snap = build_snapshot("binance", "BTCUSDT", "1h", binance_raw(), now_ms=NOW, coin="BTC")
    top = snap["top_traders"]["position"]
    assert (top["value"], top["long"], top["timestamp"]) == ("1.7331", "0.6341", 1792256400000)
    assert top["source"].endswith("topLongShortPositionRatio") and top["period"] == "1h"
    assert snap["crowd"]["long"] == "0.6268"
    assert snap["taker"]["value"] == "0.7939" and snap["taker"]["buy_volume"] == "1175.501"
    assert snap["open_interest"]["current"]["value"] == "94231.042"
    assert snap["basis"]["value"] == "-36.07847826" and snap["basis"]["funding_rate"] == "0.00002598"
    ids = {s["id"]: s for s in snap["signals"]}
    assert ids["top_vs_crowd"]["value"] == "0.73" and ids["top_vs_crowd"]["state"] == "aligned"
    assert ids["oi_price_4h"]["state"] == "no_clear_change"
    assert ids["taker_flow"]["state"] == "sellers_aggressive"


def test_bybit_snapshot_marks_missing_sections_with_reasons():
    snap = build_snapshot("bybit", "BTCUSDT", "1h", bybit_raw(), now_ms=NOW, coin="BTC")
    assert snap["top_traders"]["position"]["value"] is None
    assert "only Binance" in snap["top_traders"]["position"]["reason"]
    assert snap["taker"]["value"] is None and snap["taker"]["reason"]
    assert snap["crowd"]["long"] == "0.6283"
    cur = snap["open_interest"]["current"]
    assert cur["value"] == "29621.039" and cur["oi_both_sides"] == "59242.077"
    assert snap["basis"]["value"] == "-29.69"   # markPrice 81259.20 - indexPrice 81288.89


def test_hyperliquid_snapshot_has_no_oi_history():
    snap = build_snapshot("hyperliquid", "BTC", "1h", {"hl_ctxs": load("hl_metaAndAssetCtxs.json")},
                          now_ms=NOW, coin="BTC")
    assert snap["open_interest"]["current"]["value"] == "41043.77804"
    assert snap["open_interest"]["changes"]["4h"]["reason"] == "Hyperliquid has no public OI history endpoint"
    assert snap["basis"]["value"] == "-34.9"     # markPx 81255.0 - oraclePx 81289.9


def test_failed_fetch_becomes_null_with_reason_not_exception():
    raw = binance_raw()
    raw["top_position"] = RuntimeError("fapi.binance.com: HTTP 429")
    raw["oi_hist"] = {"code": -1003, "msg": "Too many requests"}
    snap = build_snapshot("binance", "BTCUSDT", "1h", raw, now_ms=NOW)
    assert snap["top_traders"]["position"]["value"] is None
    assert "HTTP 429" in snap["top_traders"]["position"]["reason"]
    assert snap["open_interest"]["changes"]["4h"]["value"] is None
    assert {s["id"]: s for s in snap["signals"]}["top_vs_crowd"]["state"] is None
