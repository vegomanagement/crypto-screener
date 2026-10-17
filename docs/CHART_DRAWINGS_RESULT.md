# Chart drawings, level sync, seconds timeframes — result (Track B, phase 2)

Branch `claude/chart-drawings` (based on `claude/scalping` @ `fb7d555`). Local commits only. Nothing
was pushed, merged or deployed. View-only: no orders and no synthetic ticks.

## Delivered

- **Drawing tools on `/ui` panes** (`static/drawings.js`, wired into `workspace.js` with small
  patches). Each pane header has a `✎` button that opens the tool strip:
  - Line (2 points), Ray, Horizontal line, Horizontal ray, Rectangle.
  - Ruler. It is temporary and shows Δprice, Δ%, bars and duration. It stays on screen until the
    next click or Esc.
  - Magnet: snaps to the O/H/L/C of the bar under the cursor that is closest in pixels.
  - Level mode `L`, session-start lines `S` (00:00 UTC), hide all, and clear all (needs two clicks).
  - Selection: colour, width 1–4, lock, duplicate, delete (button or Del/Backspace).
  - Drag a handle to move one point (magnet applies). Drag the body to move the whole shape by
    whole bars and a price delta. Pointer handlers run in the capture phase, so the chart does not
    pan while you draw or drag.
- **Storage.** Drawings are saved in `localStorage['screener.drawings.v1']` as
  `{version:1, symbols:{"venue:SYMBOL":{hidden, items}}}`. Each point is
  `{time: ms, price: exact decimal string}`. Validation is strict: unknown fields, bad points and
  foreign versions are dropped. The cap is 200 per symbol. Drawings are shared by all intervals of
  the same symbol, and this was verified live across 1m → 5m → 5s.
- **Level sync** through the shared `levels.js` contract:
  - A horizontal line or ray created with `L` on is written with `source:'chart'` and
    `kind:'simple'`. The drawing keeps `levelId`.
  - Every store level for the pane's (venue, symbol) is drawn, with a price tag. Signal levels are
    dashed and tagged `⚑`.
  - Dragging a level or changing its colour/width updates the store. Deleting it removes the level.
  - The store is the source of truth: a level that is moved or removed elsewhere (DOM, other tab
    via the `storage` event) moves or removes the linked drawing.
- **Seconds timeframes 1s/5s/15s/30s** (`static/seconds-candles.js`, `seconds_candles.py`,
  `seconds_api.py`). The 30s button is now enabled, along with 1s/5s/15s.
  - Candles are built only from real trades. A second without trades has no candle.
  - Bars without proven complete coverage are drawn grey and counted as partial: the stream started
    inside the bucket, or there was a live aggTrade id gap.
  - **Binance** backfills the last 5 (1s), 10 (5s) or 15 (15s/30s) minutes from aggTrades through
    `GET /api/seconds`. This route reuses the footprint fetcher and its shared rate window, with a
    budget of 20 requests. A budget cut is reported. The REST request is sent after the first live
    trade arrives (or after 5 s), so the REST snapshot and the stream overlap. The seam is then
    stitched exactly by aggTrade id.
  - **Bybit/Hyperliquid** are live only. `/api/seconds` returns a 400 that says so.
  - The status line is explicit, for example: "Backfilled from Binance aggTrades since HH:MM:SS UTC
    (N trades, R requests) · live since HH:MM:SS UTC · seconds without trades have no candle".
  - Server contract: `/api/chart/intervals` lists the seconds entries with `source:"trades"`.
    `/api/chart` still rejects every seconds interval with a 400. Seconds candles are never served
    as if a provider supplied them.
- **Candle-close countdown** for every interval: seconds, minutes, hours, D, 2D/3D epoch-aligned,
  W Monday-aligned and M calendar months, all in UTC. It is pinned under the last-price label on
  the price scale.
- **`static/levels.js` is a vendored copy.** The plan says Track A owns this file. Track A had not
  landed on this branch, so `static/levels.js` was written from the "Shared contract" section only:
  same key `screener.levels.v1`, same schema and API names, exact BigInt `crossed()`, cap 500 with
  the oldest triggered levels dropped first. It has its own tests in `tests/levels.test.cjs`.
  **Reconcile with Track A's canonical file at merge.** It is intentionally minimal. One extra
  behaviour beyond the spec: after all triggered levels are gone, the cap falls back to dropping
  the oldest created level.

## Checks actually executed

- `pytest tests/`: **1285 passed** (11 pre-existing matplotlib warnings).
  - New: `tests/test_seconds_candles.py` (shared fixture, UTC/midnight alignment, gaps,
    out-of-order ids, parity with the footprint bucket formula, partial first bucket, backward
    paging, budget truncation, non-contiguous ids, route validation).
  - Updated: interval menu tests in `test_chart_data.py` and `test_workspace_routes.py`.
- `node --test tests/*.cjs`: **117 passed, 0 failed, 6 skipped** (pre-existing optional browser
  tests).
  - New: `levels.test.cjs` (7), `seconds_candles.test.cjs` (9), `drawings.test.cjs` (9), and one
    test in `workspace_unit.test.cjs`.
  - They cover hit-testing, magnet, serialization round-trip, level-sync mapping, seconds
    aggregation and parity with Python through the shared `tests/fixtures/seconds_parity.json`,
    and countdown formatting.
- Ruff, exactly as CI (`--select E4,E7,E9,F`) plus the new Python files: clean.
  `git diff --check`: clean.

### Live (real network, no mocks)

Setup: local launcher on 127.0.0.1:5089 (no Telegram/scheduler) and headless Chrome over CDP
(port 9562). Evidence is in `docs/evidence/chart-drawings/`. The harness scripts are in `harness/`.

- **Binance BTCUSDT 5s on `/ui`, 2 min 14 s** (`live_5s_samples.json`, `5s_chart_*.png`):
  - Ready after 4 s with 120+ closed bars, growing by 2 bars every 10 s.
  - Header: `TRADES LIVE`. The countdown ticks under the price label. No JS exceptions; the only
    console errors are `/favicon.ico` 404s.
  - A **6-minute run** is in `live_5s_samples_6min.json` and `5s_chart_after_6min.png`.
  - The first run found a startup "1 live id gap": REST answered before the WebSocket delivered
    anything. The fix sends REST after the first live trade, and every later run had 0 gaps.
- **Σvolume of 5s bars per 1m vs the Binance 1m kline** (exact decimal compare):
  - `/api/seconds` backfill, run 1: **9/10** full minutes exact.
  - `/api/seconds` backfill, run 2: **6/10** exact (`backfill_volume_vs_kline.json`).
  - In-browser controller on the same code path, run 1: **9/13** exact. This includes the
    backfill→live seam minute and the live-only minutes (`live_probe_volume_vs_kline.json`).
  - In-browser controller, 6-minute run: **11/14** exact. The seam minute 16:30 and live-only
    minutes 16:31–16:33 are exact (`live_probe_6min_volume_vs_kline.json`).
  - **Every mismatch is the aggTrade-boundary caveat**, and each was checked against Binance
    aggTrades. An aggTrade with several fills whose first fill lands just before a minute boundary
    is counted whole in the earlier minute, while the kline splits it by fill. Cases:
    - `a=3488511268`: q=2.153, 4 fills, −83 ms. Shift ±0.004.
    - `a=3488514720`: q=0.003, 2 fills, −39 ms. Shift ±0.001.
    - `a=3488520410`: q=0.051, 5 fills, −3 ms. Shift +0.049, the last compared minute.
  - Adjacent pairs cancel exactly when both minutes are compared. In both in-browser runs, OHLC
    matched the kline in every compared minute except 16:25. There the kline's open is the
    straddling aggTrade's price (81256.00), while the 5s bars open with the next aggTrade
    (81256.1).
- **Drawings on Binance BTCUSDT 1m** (`drawings_1m.png`, `drawings_level_sync.png`,
  `drawings_live*.json`):
  - All five shapes, two shared levels, a DOM-side signal level (dashed `⚑`) and the ruler
    ("+122.54 (+0.15%) · 42 bars · 42m") were drawn.
  - Magnet endpoints equal the real Binance 1m kline opens: 81517.1 and 81423.1.
  - Dragging a shared level by 40 px moved both the drawing and the store: 81509.69 → 81551.58.
  - Selecting a horizontal ray and pressing Del removed it from both stores.
  - Drawings persisted through 1m → 5m → 5s (`drawings_5m.png`, `drawings_on_5s.png`).
  - `drawings_live_2.json` contains a drag/delete attempt after a page reload. It did nothing
    because the stored pixel positions were stale after the reload. It is superseded by
    `drawings_live_3.json` and is kept only for transparency.
- **Bybit BTCUSDT and Hyperliquid BTC 1s, live only** (Node with the same UMD modules and the real
  venue WebSockets, 60 s each; `live_only_venues_node.json`): 46 and 42 closed bars. Exactly one
  partial (grey) first bar each, and status says no history exists.

## Not verified / known limits

- Cross-tab `storage` event sync is covered by unit tests only, not by a two-tab browser run.
- Touch/pen drawing was not exercised. Only mouse events went through CDP.
- Seconds charts show no FVG zones. Zones come from `/api/chart`, which does not serve seconds.
- Footprint (FP) reports "unsupported" on seconds intervals.
- Hyperliquid's trade subscription replays a few recent trades on connect. "Live since" is
  therefore the time of the first received trade, which can be slightly before the subscribe
  time. The oldest bucket is still flagged partial.
- The magnet only snaps to closed bars. The forming candle is not in the closed set, so a point
  placed on it uses the cursor price.
- Duplicating a shared-level line creates a plain drawing, not a second level.
- Seconds history is kept in memory only: up to 2000 bars, lost on reload. On reload Binance is
  backfilled again, and Bybit/HL restart from live.
- `levels.js` must be reconciled with Track A's file (see above).
