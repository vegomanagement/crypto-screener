"""Field-by-field comparison of /api/positioning (:5092) against raw venue responses fetched concurrently."""
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

OUT = "/Users/maxbelov/Projects/wt/positioning-data/docs/evidence/positioning/"
BN, BY, HL = "https://fapi.binance.com", "https://api.bybit.com", "https://api.hyperliquid.xyz/info"


def get(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json", "User-Agent": "positioning-compare"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def D(x):
    return None if x in (None, "") else Decimal(str(x))


def pct(a, b):
    return ((D(b) - D(a)) / D(a) * 100).quantize(Decimal("0.01"))


rows = []


def check(market, field, api, raw, api_ts=None, raw_ts=None, tolerance_ms=0):
    same_ts = api_ts == raw_ts or (tolerance_ms and abs(api_ts - raw_ts) <= tolerance_ms)
    a, r = D(api), D(raw)
    ok = a == r if same_ts else None
    if not same_ts and a == r:
        ok = True   # equal value even though sampled at different instants
    rows.append({"market": market, "field": field, "api": api, "raw": str(raw) if raw is not None else None,
                 "api_ts": api_ts, "raw_ts": raw_ts, "match": ok,
                 "note": "" if same_ts else "different sample time (cache/new datapoint)"})


def binance(sym):
    calls = {"api": lambda: get(f"http://127.0.0.1:5092/api/positioning?exchange=binance&symbol={sym}&period=1h&watch=0"),
             "ta": lambda: get(f"{BN}/futures/data/topLongShortAccountRatio?symbol={sym}&period=1h&limit=1"),
             "tp": lambda: get(f"{BN}/futures/data/topLongShortPositionRatio?symbol={sym}&period=1h&limit=1"),
             "g": lambda: get(f"{BN}/futures/data/globalLongShortAccountRatio?symbol={sym}&period=1h&limit=1"),
             "tk": lambda: get(f"{BN}/futures/data/takerlongshortRatio?symbol={sym}&period=1h&limit=1"),
             "oi": lambda: get(f"{BN}/fapi/v1/openInterest?symbol={sym}"),
             "oih": lambda: get(f"{BN}/futures/data/openInterestHist?symbol={sym}&period=1h&limit=25"),
             "basis": lambda: get(f"{BN}/futures/data/basis?pair={sym}&contractType=PERPETUAL&period=1h&limit=1"),
             "prem": lambda: get(f"{BN}/fapi/v1/premiumIndex?symbol={sym}")}
    with ThreadPoolExecutor(9) as ex:
        res = dict(zip(calls, ex.map(lambda f: f(), calls.values())))
    a, m = res["api"], f"binance {sym}"
    for name, key, node in (("top account L/S", "ta", a["top_traders"]["account"]),
                            ("top position L/S", "tp", a["top_traders"]["position"]),
                            ("global account L/S", "g", a["crowd"])):
        raw = res[key][-1]
        check(m, name + " ratio", node["value"], raw["longShortRatio"], node["timestamp"], raw["timestamp"])
        check(m, name + " long share", node["long"], raw["longAccount"], node["timestamp"], raw["timestamp"])
    tk = res["tk"][-1]
    check(m, "taker buySellRatio", a["taker"]["value"], tk["buySellRatio"], a["taker"]["timestamp"], tk["timestamp"])
    check(m, "taker buyVol", a["taker"]["buy_volume"], tk["buyVol"], a["taker"]["timestamp"], tk["timestamp"])
    cur = a["open_interest"]["current"]
    check(m, "OI now", cur["value"], res["oi"]["openInterest"], cur["timestamp"], res["oi"]["time"])
    hist = sorted(res["oih"], key=lambda r: r["timestamp"])
    by_ts = {r["timestamp"]: r for r in hist}
    for w, h in (("1h", 1), ("4h", 4), ("24h", 24)):
        c = a["open_interest"]["changes"][w]
        then = by_ts.get(hist[-1]["timestamp"] - h * 3600000)
        raw = pct(then["sumOpenInterest"], hist[-1]["sumOpenInterest"]) if then else None
        check(m, f"OI change {w} %", c["value"], raw, c["timestamp"], hist[-1]["timestamp"])
    b = res["basis"][-1]
    check(m, "basis", a["basis"]["value"], b["basis"], a["basis"]["timestamp"], b["timestamp"])
    check(m, "funding rate", a["basis"]["funding_rate"], res["prem"]["lastFundingRate"],
          a["basis"]["next_funding"], res["prem"]["nextFundingTime"])


def bybit(sym):
    calls = {"api": lambda: get(f"http://127.0.0.1:5092/api/positioning?exchange=bybit&symbol={sym}&period=1h&watch=0"),
             "ar": lambda: get(f"{BY}/v5/market/account-ratio?category=linear&symbol={sym}&period=1h&limit=1"),
             "oih": lambda: get(f"{BY}/v5/market/open-interest?category=linear&symbol={sym}&intervalTime=1h&limit=25"),
             "tk": lambda: get(f"{BY}/v5/market/tickers?category=linear&symbol={sym}")}
    with ThreadPoolExecutor(4) as ex:
        res = dict(zip(calls, ex.map(lambda f: f(), calls.values())))
    a, m = res["api"], f"bybit {sym}"
    r = res["ar"]["result"]["list"][0]
    check(m, "account buyRatio (long share)", a["crowd"]["long"], r["buyRatio"], a["crowd"]["timestamp"], int(r["timestamp"]))
    t = res["tk"]["result"]["list"][0]
    cur = a["open_interest"]["current"]
    check(m, "OI now (single side)", cur["value"], t.get("singleOpenInterest") or t["openInterest"], cur["timestamp"], res["tk"]["time"])
    hist = sorted(res["oih"]["result"]["list"], key=lambda r: int(r["timestamp"]))
    by_ts = {int(r["timestamp"]): r for r in hist}
    last = int(hist[-1]["timestamp"])
    for w, h in (("1h", 1), ("4h", 4), ("24h", 24)):
        c = a["open_interest"]["changes"][w]
        then = by_ts.get(last - h * 3600000)
        key = "singleOpenInterest" if "singleOpenInterest" in hist[-1] else "openInterest"
        raw = pct(then[key], hist[-1][key]) if then else None
        check(m, f"OI change {w} %", c["value"], raw, c["timestamp"], last)
    check(m, "basis mark-index", a["basis"]["value"], D(t["markPrice"]) - D(t["indexPrice"]), a["basis"]["timestamp"], res["tk"]["time"])
    check(m, "funding rate", a["basis"]["funding_rate"], t["fundingRate"], a["basis"]["next_funding"], t["nextFundingTime"])


def hyperliquid(coin):
    with ThreadPoolExecutor(2) as ex:
        fa = ex.submit(get, f"http://127.0.0.1:5092/api/positioning?exchange=hyperliquid&symbol={coin}&period=1h&watch=0")
        fr = ex.submit(lambda: (get(HL, {"type": "metaAndAssetCtxs"}), int(time.time() * 1000)))
        a, (raw, raw_ts) = fa.result(), fr.result()
    idx = [u["name"] for u in raw[0]["universe"]].index(coin)
    ctx, m = raw[1][idx], f"hyperliquid {coin}"
    # HL ctxs carry no exchange timestamp: API reports its fetch time; raw = our receive time.
    # Values sampled within 1.5 s are treated as the same sample (HL updates ctxs continuously).
    ts = a["open_interest"]["current"]["timestamp"]
    check(m, "OI now", a["open_interest"]["current"]["value"], ctx["openInterest"], ts, raw_ts, 1500)
    check(m, "basis mark-oracle", a["basis"]["value"], D(ctx["markPx"]) - D(ctx["oraclePx"]), ts, raw_ts, 1500)
    check(m, "funding rate (1h)", a["basis"]["funding_rate"], ctx["funding"], ts, raw_ts, 1500)


binance("BTCUSDT"); binance("ETHUSDT"); bybit("BTCUSDT"); bybit("ETHUSDT"); hyperliquid("BTC"); hyperliquid("ETH")
stamp = time.strftime("%Y%m%dT%H%M%S")
json.dump({"generated_at": stamp, "rows": rows}, open(OUT + f"compare_{stamp}.json", "w"), indent=1)
ok = sum(r["match"] is True for r in rows)
bad = [r for r in rows if r["match"] is False]
diff_time = [r for r in rows if r["match"] is None]
print(f"{stamp}: {len(rows)} fields, {ok} exact match, {len(bad)} mismatch, {len(diff_time)} different sample time")
for r in bad + diff_time:
    print(" ", r)
sys.exit(1 if bad else 0)
