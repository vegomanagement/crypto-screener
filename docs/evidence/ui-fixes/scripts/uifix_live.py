"""Live evidence: heatmap updates over 2+ min and bands match a raw depth snapshot."""
import json
import sys
import time
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

import requests

sys.path.insert(0, "/Users/maxbelov/.hermes/cache/scratch")
from uifix_cdp import EVID, Page  # noqa: E402

ex, sym, iv, tag = sys.argv[1:5]
duration = int(sys.argv[5]) if len(sys.argv) > 5 else 130
p = Page()
p.size()
p.nav("/static/liquidity-heatmap.js", 1)
p.ev("""(() => { const k='crypto-screener.workspace.v5'; const s=JSON.parse(localStorage.getItem(k));
 s.layout='1'; s.active=0; s.panes[0]={...s.panes[0], symbol:%s, interval:%s, exchange:%s};
 s.indicatorEnabled.liquidity=true; localStorage.setItem(k, JSON.stringify(s)); })()""" % (json.dumps(sym), json.dumps(iv), json.dumps(ex)))
p.nav("/ui", 8)
STATE = """(() => { const pane=[...window.__workspaceDebug.panes.values()][0]; const d=pane.liquidityData, m=pane.liquidityModel;
 if(!d||!m) return {noData:true, status:document.querySelector('.pane-liquidity-status')?.textContent};
 const xs=m.cells.map(c=>c.x), xe=m.cells.map(c=>c.x+c.width), ys=m.cells.map(c=>c.y), ye=m.cells.map(c=>c.y+c.height);
 return {t:Date.now(), frames:d.frames.length, samples:d.samples, coverage_start:d.coverage_start, latest:d.latest.observed_at,
  rows:[d.latest.bids.length,d.latest.asks.length], step:m.step, cells:m.cells.length, profile:m.profile.length, bookPx:Math.round(m.bookPx),
  cellsX:[Math.round(Math.min(...xs)),Math.round(Math.max(...xe))], cellsY:[Math.round(Math.min(...ys)),Math.round(Math.max(...ye))],
  minRowPx:Math.min(...m.cells.map(c=>c.height)), status:document.querySelector('.pane-liquidity-status').textContent}; })()"""
timeline = []
start = time.time()
shots = []
while time.time() - start < duration:
    s = p.ev(STATE)
    s["elapsed_s"] = round(time.time() - start)
    timeline.append(s)
    print({k: s.get(k) for k in ("elapsed_s", "frames", "latest", "cells", "cellsX", "cellsY", "step", "bookPx")})
    if not shots or time.time() - start >= 60 * len(shots):
        shots.append(p.shot(f"{tag}-t{s['elapsed_s']:03d}.png"))
    time.sleep(10)
shots.append(p.shot(f"{tag}-t{round(time.time()-start):03d}.png"))

# Raw depth snapshot fetched now; compare with the collected frame nearest in time.
if ex == "binance":
    t_raw0 = int(time.time() * 1000)
    raw = requests.get("https://fapi.binance.com/fapi/v1/depth", params={"symbol": sym, "limit": 1000}, timeout=10).json()
    t_raw = int(time.time() * 1000)
    rb, ra = raw["bids"], raw["asks"]
else:
    t_raw0 = int(time.time() * 1000)
    raw = requests.get("https://api.bybit.com/v5/market/orderbook", params={"category": "linear", "symbol": sym, "limit": 1000}, timeout=10).json()
    t_raw = int(time.time() * 1000)
    rb, ra = raw["result"]["b"], raw["result"]["a"]
def fetch_raw():
    if ex == "binance":
        r = requests.get("https://fapi.binance.com/fapi/v1/depth", params={"symbol": sym, "limit": 1000}, timeout=10).json()
        return int(time.time() * 1000), r["bids"], r["asks"]
    r = requests.get("https://api.bybit.com/v5/market/orderbook", params={"category": "linear", "symbol": sym, "limit": 1000}, timeout=10).json()
    return int(time.time() * 1000), r["result"]["b"], r["result"]["a"]


time.sleep(1)
t_raw2, rb2, ra2 = fetch_raw()
time.sleep(12)  # let the page read the frame collected right after the raw fetch
page = p.ev("""(() => { const pane=[...window.__workspaceDebug.panes.values()][0]; const d=pane.liquidityData, m=pane.liquidityModel;
 const f=[...d.frames, d.latest].sort((a,b)=>Math.abs(a.observed_at-%d)-Math.abs(b.observed_at-%d))[0];
 const bins=window.WorkspaceLiquidity.aggregate(f, m.step, 0);
 return {observed_at:f.observed_at, step:m.step, bids:bins.bids, asks:bins.asks}; })()""" % (t_raw, t_raw))
step = Decimal(repr(page["step"]))


def buckets(rows, side):
    out = {}
    for price, qty in rows:
        r = Decimal(price) / step
        b = (r.to_integral_value(ROUND_FLOOR) if side == "bids" else r.to_integral_value(ROUND_CEILING)) * step
        out[b] = out.get(b, Decimal(0)) + Decimal(qty)
    return out


import sqlite3, zlib  # noqa: E402,E401
db = sqlite3.connect("file:/Users/maxbelov/Projects/wt/ui-fixes/.data/liquidity.sqlite3?mode=ro", uri=True)
blob = db.execute("SELECT payload FROM observations WHERE exchange=? AND symbol=? AND observed_at=?", (ex, sym, page["observed_at"])).fetchone()[0]
stored = json.loads(zlib.decompress(blob))
stored_bins = p.ev("(() => { const b=window.WorkspaceLiquidity.aggregate(%s, %s, 0); const o={}; for (const s of ['bids','asks']) { o[s]={}; b[s].forEach(r=>{ o[s][String(r.price)]=String(r.qty); }); } return o; })()" % (json.dumps(stored), page["step"]))
stored_bins = {side: {str(Decimal(k).quantize(step)): str(Decimal(v)) for k, v in stored_bins[side].items()} for side in ("bids", "asks")}
compare = {"raw2_fetched_at": t_raw2, "stored_rows": [len(stored["bids"]), len(stored["asks"])],"exchange": ex, "symbol": sym, "raw_fetched_at": [t_raw0, t_raw], "page_frame_observed_at": page["observed_at"],
           "delta_ms": page["observed_at"] - t_raw, "step": str(step), "bands": []}
for side in ("bids", "asks"):
    rawb = buckets(rb if side == "bids" else ra, side)
    rawb2 = buckets(rb2 if side == "bids" else ra2, side)
    pg = {Decimal(repr(r["price"])).quantize(step): Decimal(repr(r["qty"])) for r in page[side]}
    rawq = {k.quantize(step): v for k, v in rawb.items()}
    common = sorted(set(pg) & set(rawq), reverse=(side == "bids"))
    near = common[:5]
    largest = sorted(rawq, key=lambda k: rawq[k], reverse=True)[:3]
    for k in list(dict.fromkeys(near + largest)):
        a, b = pg.get(k), rawq.get(k)
        c = rawb2.get(k)
        compare["bands"].append({"side": side, "bucket": str(k), "page_qty": str(a) if a is not None else None,
                                 "raw_qty": str(b), "diff_pct": float(abs(a - b) / b * 100) if a is not None and b else None,
                                 "raw2_qty": str(c) if c is not None else None,
                                 "raw_vs_raw2_pct": float(abs(c - b) / b * 100) if c is not None and b else None,
                                 "db_equal": stored_bins[side].get(str(k)) == (str(a) if a is not None else None),
                                 "kind": "near" if k in near else "largest"})
    compare[f"{side}_buckets_page"] = len(pg)
    compare[f"{side}_buckets_raw"] = len(rawq)
for b in compare["bands"]:
    print(b)
json.dump({"timeline": timeline, "shots": shots, "compare": compare}, open(EVID + f"{tag}.json", "w"), indent=1)
print("delta_ms", compare["delta_ms"], "step", compare["step"])
