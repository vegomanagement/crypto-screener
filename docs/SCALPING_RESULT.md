# Scalping DOM — Phase 1 result

Status: **Phase 1 is implemented and checked against live venue data.** The page is view-only.
It has no order path, no API keys, no accounts and no paper trading. Everything below comes from
real runs on 2026-10-17 (local Mac, headless Google Chrome, public Binance USDT-M, Bybit linear and
Hyperliquid endpoints). Anything that was not measured is listed under "Not verified".

## What exists

`/scalp` shows 1–4 independent columns. Each column picks a venue and symbol and contains:

| Part | Behaviour |
|---|---|
| **DOM ladder** (canvas, DPR-aware, one rAF paint per frame for all dirty columns) | Bid/ask size bars with full-scale fill (auto = largest visible row, or a fixed $ value). Two big-volume colours (≥ big1 $, ≥ big2 $). $ or coin units, 1.2K/3.4M abbreviation, grid line every 10/50 rows, autocenter with a distance threshold. Wheel scrolls; Ctrl/⌘+wheel changes row height. Scale is ×1/×2/×5/×10/×20 ticks or 0.005–0.1 % rows. Best bid/ask/spread and mark price are shown, plus funding and a countdown where the venue publishes them. A click-and-hover ruler shows the % distance, the row count and the resting liquidity between the touch and the hovered row. |
| **Unknown depth** | Rows outside the depth window the venue actually sent are hatched as "unknown" and never drawn as empty. Binance re-anchors from REST when the touch leaves the snapshot window. Bybit is capped at 200 levels per side and Hyperliquid at ≤ 20; both caps show as a chip. |
| **Prints feed** | Circles are sized by $ volume (sqrt scale), with a time axis where the newest print sits next to the ladder. Options: a "hide < $X" filter, labels from $Y up (colliding labels are skipped and counted, never overprinted), same-side aggregation within N ms (and optionally N ticks), and an optional connecting line. |
| **Cluster columns** | 0–8 columns at 1m/5m/15m, in volume, delta or bid×ask mode. They are built with `footprint.js` (`createBar`/`addTrade`/`groupedLevels`, the same exact-decimal aggregation as the chart footprint) and regrouped to the ladder rows. A bar that opened before the page subscribed, or that saw a Binance aggregate-id gap or a disconnect, is marked partial (`~`). |
| **Density lifetime** | For levels at or above a $ threshold, shows the age since **this page** first saw the level at that size. The age resets when the level disappears or the threshold changes. |
| **Settings / hotkeys** | Per-column popover, versioned (`crypto-screener.scalp.v1`), sanitised on load. View hotkeys: `1–5` scale, `P` ticks↔%, `U` $↔coin, `+/-` row height, `C` recenter, `Esc` clear ruler. |
| **Links** | The `/ui` header has "DOM ↗" → `/scalp`. Each column's "chart ↗" opens `/ui?exchange=…&symbol=…`, and `/ui` now applies that deep link to the active pane. |

Server side: `GET /scalp` (template) and `GET /api/scalp/instrument` (venue tick size, or
Hyperliquid `szDecimals`, from the footprint metadata cache). Book, trades, mark and funding
are streamed or fetched by the browser directly from the venues' public endpoints.

### Files
`static/orderbook.js` (book engines: Binance diff sync per the official "manage a local order
book" rules, Bybit snapshot/delta, Hyperliquid snapshots; grouping, density, prints, clusters,
WS lifecycle), `static/scalp-render.js` (pure geometry + canvas drawing),
`static/scalp-settings.js`, `static/scalp-column.js` (trade bookkeeping per column),
`static/scalp-dom.js` (column UI), `static/scalp.js` (page shell), `static/scalp.css`,
`templates/scalp.html`, `scalp_api.py`, `footprint_data.instrument()`, `/ui` link and deep link.

## Bug found by the live check (fixed)

Binance SOLUSDT has `tickSize 0.0100`, yet its REST depth contained resting levels at
`105.0020` and `105.5290`. The first engine treated off-grid prices as corrupt and resynced, so
the SOL column looped in `resyncing` forever. The book now keeps such a level at its exact price
and counts it in the tick that contains it. A `qty 0` for that exact price removes exactly that
size, and the crossed-book guard compares exact prices. This is covered by a unit test. After
the fix, SOLUSDT synced with 0 resyncs.

## Tests (all green)

* `pytest`: **1271 passed** (including `tests/test_scalp_routes.py`: routes, script order, asset
  availability, `/ui` ↔ `/scalp` links, clean-room name check).
* `node --test tests/`: **91 pass, 0 fail, 6 skipped**. The skips are the existing
  Playwright-gated workspace browser tests, which are not part of this work.
  `tests/orderbook.test.cjs` covers the engines, including the off-grid case.
  `tests/scalp.test.cjs` (10 tests) covers layout, row↔y geometry, autocenter, the ladder in $/coin
  with unknown rows, ×10 grouping, ruler %/liquidity, feed span/filter/labels, clusters with the
  partial flag, feed counts vs received trades, id-gap/disconnect partial marking, settings
  sanitisation, hotkeys, and a static "no signed/order endpoint" scan.
* `ruff check --select E4,E7,E9,F` exactly as in CI, plus `scalp_api.py` and
  `footprint_data.py`: all checks passed. No new pip dependencies.

## Live acceptance (real data)

Scripts: `docs/evidence/scalping/live_acceptance.cjs` + `live_checks.cjs` + `cdp.cjs` (Chrome
DevTools Protocol, no npm deps). Raw output: `live-acceptance.json` (captured
2026-10-17T15:35Z, final code).

### 1. Book vs REST snapshot

**Binance BTCUSDT** (page synced for 65.5 s; 12 samples, 1205 price levels compared against
`/fapi/v1/depth?limit=50` across the REST top-50 price range).
* Each REST `lastUpdateId` L was aligned to the diff event with `U ≤ L ≤ u`. One sample hit
  `L == u` exactly, and in it **0 of 100 levels differed**.
* In the other samples, the local book after that event differs from REST at L only on levels
  the event itself changed (expected), with 4 exceptions across all 12 samples. All 4 are levels
  present in REST but absent locally, with sizes ≤ 0.033 BTC. None repeats in another sample.
  This is consistent with orders that were placed and cancelled inside one 100 ms diff window:
  the diff stream carries net changes, so such an order never appears in it. It is not proven.
* 8 of 12 samples were fully explained. There were 0 sequence gaps and 0 resyncs during the
  session.

**Bybit BTCUSDT** (page synced for 61 s; 12 samples, 1233 levels against
`/v5/market/orderbook?limit=50`).
* REST `u` and WebSocket `u` turned out to be **different id spaces**, so alignment uses `seq`.
* No REST `seq` ever equalled a WS push `seq`. Comparison was therefore against the last push
  before the REST seq. 5 of 12 samples were fully explained, with 13 residual levels (none
  repeated across samples).
* Stronger check, because REST and WS are evidently not the same feed: the incrementally
  maintained WS book was compared with **fresh WS snapshots** from a second connection at
  equal `seq`. Script: `bybit_ws_vs_ws.cjs`; output: `bybit-ws-vs-ws-BTCUSDT.json`. Result:
  **12/12 equal-seq samples, 4800 levels, 0 mismatches.**

### 2. Feed count vs trades (60 s window)

* **Binance BTCUSDT**, window of 60.0 s: 392 aggTrades received over WS = **392** from REST
  `/fapi/v1/aggTrades` (same ids, 0 missing, 0 extra). The feed holds 392 trades with the same
  $ volume (2,040,495.67). At render level, every print in the visible span was drawn (`hidden 0`).
* **Bybit BTCUSDT**, window of 57.9 s (bounded by the first received trade): 377 received =
  **377** from REST `recent-trade` (same execIds). The feed holds 377 trades with the same $.
* 0 aggregate-id gaps and 0 rejected rows.

### 3. Screenshots and errors

`scalp-1col.png` (Binance BTCUSDT), `scalp-3col.png` (Binance BTCUSDT / ETHUSDT / SOLUSDT),
`scalp-1col-ruler-x5.png` (hotkey `3` → ×5 ticks, persisted; ruler via real CDP mouse input),
`scalp-bybit-btcusdt.png`. All at 2× DPR.
Console errors / uncaught exceptions / HTTP ≥ 400 on `/scalp`: **0 / 0 / 0** in every run.
The earlier favicon 404 is fixed with an inline empty icon.

### 4. Frame time (headless Chrome, 2× DPR, this Mac)

| Layout | JS paint per frame (all dirty columns) | rAF interval over 5 s |
|---|---|---|
| 1 column, 1280×860 | avg 1.63 ms, p95 2.7 ms, max 3.0 ms | p50 16.7 ms, p95 16.8 ms, max 16.8 ms, 0 frames > 50 ms |
| 3 columns, 1680×900 | avg 2.26 ms, p95 4.8 ms, max 6.5 ms | p50 16.7 ms, p95 16.7 ms, max 16.8 ms, 0 frames > 50 ms |

### 5. Other live observations
* Hyperliquid ETH (smoke run, not part of the scripted acceptance) synced from `l2Book`
  snapshots, derived tick 0.1, showed 9 "unknown" rows beyond the ≤ 20-level window, and drew
  mark and hourly funding.
* `/ui?exchange=binance&symbol=ETHUSDT` switched the active pane to Binance ETHUSDT. The
  "DOM ↗" link is present in the `/ui` header.

## Not verified / known limitations

* **Binance residual levels** (4 small levels, none repeated): the "placed and cancelled inside
  one diff window" explanation is plausible but not proven.
* **Bybit REST vs WS**: the REST book cannot be aligned exactly with the WS book (different `u`
  spaces, no equal `seq` observed). Exactness is shown only WS-vs-WS.
* **Hyperliquid**: there is no automated REST/WS comparison and no feed-vs-REST count. The depth
  is capped at ≤ 20 levels per side by the venue's public `l2Book`.
* **Clusters** use only trades seen since the page opened. There is no history backfill, so the
  first bar is always marked partial.
* **Density lifetime** is "since this page saw it", not the level's true age at the venue.
* **Frame times** were measured in headless Chrome on one machine. They were not measured on a
  real display, under GPU contention, or with 4 columns.
* **Hotkeys**: `+/-`, `P`, `U`, `C` and `Esc` are unit-tested only. In the browser, only `3` and
  the ruler were driven.
* **Settings popover** fields are covered by the sanitiser tests, but they were not clicked
  through in a browser.
* USD values for off-grid levels use the containing tick's price, which is low by less than one
  tick.
