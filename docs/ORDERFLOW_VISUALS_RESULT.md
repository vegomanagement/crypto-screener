# Order-flow visuals: big-trade bubbles + abnormal-volume candles

Branch `claude/orderflow-visuals` (from `claude/integration` @ 2705802). Two new chart
indicators on `/ui`, each one toggle + one ⚙ settings popover in the overlay bar
(`Big trades …`, `Abnormal vol …`). Both are **off by default**; old saves keep their
choices (tested). Clean-room: behaviour was designed from public help-centre descriptions
only; notes live outside the repo.

## What was built

### 1. Big-trade bubbles (`static/indicators/bubbles.js`, `bubbles-render.js`)
- A circle at (trade time, trade price) for every **large aggressive print**.
  - Taker **buy** = solid disc (default blue) with ▲; taker **sell** = ring (default pink)
    with ▼ — a shape cue, not only colour.
  - Area ∝ notional: `r = rMin·√(notional / threshold)`, clamped to `[rMin, rMax]`
    (defaults 5–30 px). When zoomed out the max radius shrinks to ≈ bar width
    (diameter ≤ ~2 bars); x stays inside the trade's own bar slot.
  - Opacity grows with size (0.45–0.85); views with > 300 bubbles fade
    (× √(300/n), floor 0.35) so candles stay readable; at most `maxVisible` (1500)
    of the **largest** prints in view are drawn.
- **Large** = `mode`:
  - `fixed`: notional ≥ `minUsd` (default $50,000, USD = quote currency);
  - `adaptive`: notional ≥ nearest-rank percentile `p` (default 99.5) of all print sizes in
    the trailing `windowMin` (30) minutes for that symbol; needs `minSamples` (300) prints
    first (until then: no bubbles in `adaptive`, fixed only in `both`);
  - `both` (default): ≥ max(fixed, percentile).
  - The threshold is **causal**: each print is judged against the distribution *before* it,
    and keeps that threshold (shown in the tooltip), so bubbles do not change later.
- **Print aggregation (heuristic, labelled in the tooltip)**: same side, within
  `aggWindowMs` (150 ms) of the print's *first* trade and within `aggTicks` (2) ticks of
  its first price (tick from `/api/scalp/instrument`). Buy and sell prints are tracked
  separately, so interleaved opposite trades do not split a print. If the tick is unknown,
  only exact-price fills merge (status says so). Binance aggTrades are already one taker
  order at one price; our merge on top can join different takers.
- **History**: new route `GET /api/trades/recent?exchange&symbol&minutes&max_requests`
  (`trades_api.py`) returns raw rows `[id, time_ms, price, qty, side]` with coverage:
  Binance = contiguous aggTrades back to `minutes` within a request budget (default 10,
  max 20; shares the footprint aggTrades rate window, cuts the oldest first, says so);
  Bybit = latest ≤1000 trades (**partial**); Hyperliquid = recent handful (**partial**).
  The backfill is requested only after the live trade stream is up (so the snapshot's last
  aggTrade id meets the first streamed id), then the live stream continues it. Binance live
  ids are de-duplicated and **gaps are counted** in the status line; Bybit/HL by trade id.
  A busy rate window returns 502 and the client retries (15/30/45 s).
- Memory per pane: ≤ `maxStored` (5000) big prints; the percentile distribution keeps
  only the trailing window (cap 200k values).
- Hover tooltip: side, size, notional, price (VWAP + range if merged), time (UTC, ms),
  venue, "aggregated from N aggTrades (heuristic …)", threshold then + basis, ids.
- Status line per pane: source, exact/PARTIAL history start, budget truncation, live
  gaps, current threshold.

### 2. Abnormal-volume candles (`static/indicators/volume-anomaly.js`)
- Default: **z-score of ln(volume)** vs the previous `lookback` (50) bars (bar itself
  excluded, sample σ), tiers **2σ / 3σ**. Alternatives: `k × median` (2.5/4),
  `k × average` (2.5/4), `percentile rank` (95 / 100 = above every bar in the window).
- Visual: outline the body (default) or recolour it; tier 1 yellow, tier 2 orange,
  thicker for tier 2; optional volume-bar highlight; marker above the high (`●`/`●●`).
- Forming bar: scored against the same closed window; its volume only grows, so its tier
  only rises (no flicker); drawn dashed and labelled "provisional". When it closes, the
  closed bar gets the identical score (unit-tested).
- Delta (1m–1h only): from this chart's footprint if FP is on, otherwise a hidden
  footprint controller for the last 30/20/12/8/6/4 bars (1/3/5/15/30/60 m). Marker
  `Δ+I` / `Δ−A` = initiative (delta agrees with the bar) / absorption (strong delta, bar
  went the other way or barely moved); `|delta| < 15 %` = balanced. "from footprint
  (complete bar)" only when the footprint bar is complete and closed; otherwise `?` and
  "provisional".
- Hover tooltip on the bar's marker/body: tier, score, method, volume, delta text.

### 3. Analyst snapshot fields — **not done** (left to avoid touching analyst files that
another agent is editing). `BigTradeStore.barSummary(open, interval)` already returns
per-bar count / buys / sells / net notional for a future wiring.

## Files
- New: `trades_api.py`, `static/indicators/bubbles.js`, `bubbles-render.js`,
  `volume-anomaly.js`, `orderflow-pane.js` (pane glue), tests
  `tests/test_trades_api.py`, `tests/orderflow_visuals.test.cjs`.
- Wiring only: `screener.py` (+2 lines), `templates/workspace.html` (+4 script tags),
  `static/workspace.js` (+19 lines: 2 definitions, `initOrderflow`, draw/tooltip/dispose
  hooks, chip order), `static/indicators/indicators.css` (tooltip/status styles),
  `tests/workspace_unit.test.cjs` (toggle list + migration test).

## Tests
- `node --test tests/`: 238 tests, 232 pass, 0 fail, 6 skipped (pre-existing browser tests).
  New: fixed/adaptive thresholds incl. warm-up and `both`, rolling-distribution expiry/cap,
  aggregation window/tolerance/side isolation, radius scale (area ∝ notional, clamps,
  zoom-out cap), store dedupe/gap/memory bound/causal threshold, batched render with
  1,200 of 2,000 drawn largest-first + hit-test + tooltip, controller (backfill after the
  stream, retry, cancel on disable), z-score / ratio / percentile tiers, forming-bar
  monotonic and equal to the closed result, delta classification + footprint delta, settings
  migration (new ids off, corrupt values → defaults), overlay list = one toggle + one ⚙ per
  indicator.
- `pytest`: 1672 passed. New: Binance rows exact vs fake aggTrades (side from maker flag),
  budget cuts oldest, Bybit partial, validation 400s, malformed venue 502, busy rate window
  502 instead of an empty "exact" history.
- CI lint exactly as `.github/workflows/ci.yml` (py_compile + both ruff commands): pass.

## Live checks (2026-10-18, Sunday ≈ 07:40–08:25 UTC, quiet market)
Server: this worktree on 127.0.0.1:5100 (scratch DBs). Headless Chrome 155, 1600×1000,
DPR 1. Independent checker (stdlib Python) re-fetched raw venue data right after dumping
the page state. Evidence: `docs/evidence/orderflow-visuals/`.

**Bubbles vs raw aggTrades** (`crosscheck-4panes.json`, 4-pane layout, ~100 s after load):

| Pane | raw trades re-fetched | prints page / independent | big page / independent | mismatches |
|---|---|---|---|---|
| Binance BTCUSDT 1m | 5,350 aggTrades, ids contiguous | 3,807 / 3,807 | 45 / 45 | 0 threshold, 0 Decimal sum, 0 live gaps |
| Binance BTCUSDT 15m | same 5,350 | 3,807 / 3,807 | 45 / 45 | 0 / 0 / 0 |
| Binance ETHUSDT 5s | 5,396 aggTrades, contiguous | 3,262 / 3,263 (one print still open on the page at dump time) | 34 / 34 | 0 / 0 / 0 |
| Bybit SOLUSDT 1m | recent-trade (1000) | — | 3 stored, 2 still re-fetchable | 0 (side, first time, Decimal qty sum) |

"Independent" = a separate Python re-implementation of aggregation + causal percentile run
over the re-fetched aggTrade id range; every page print's qty/notional equals the Decimal sum
of its member aggTrades (rel. 1e-9), first-trade time and side (maker flag) equal.

**Abnormal volume vs REST** (same report): BTC 1m 300 bars → 10 flags (7 tier 1, 3 tier 2),
independent 10, 0 disagreements; BTC 15m 11 flags (5/6), 0 disagreements; ETH 5s 186 bars,
volumes rebuilt from 4,370 aggTrades into 5 s buckets: 0 volume mismatches, 1 flag = 1;
Bybit SOL 1m (v5 kline) 9 flags (8/1), 0 disagreements.
Delta: BTC 1m 07:42 UTC flagged bar shows "+80.6 % initiative, complete"; Binance kline
taker-buy gives 2·144.151 − 159.653 = 128.649 → +80.58 % (`delta-crosscheck-btc1m-0742.txt`).

**Frame time with 1,000+ bubbles** (`perf-1000-bubbles.json`): fixed $1K threshold, BTC 1m,
30-min backfill → 2,032 stored, 1,878–2,032 drawn per frame while panning every frame for 300
frames: frame interval mean 16.67 ms (60.0 fps), p95 18.4 ms, max 22.7 ms; bubble draw mean
1.53 ms (p95 2.1, max 5.6); whole overlay mean 1.83 ms.

**Forming bar / interval change** (`forming-*.json`): ETH 5s, 500 ms sampling, 16 forming bars
(test thresholds median ×1/×1.5): 5 reached a tier, every bar's tier was monotonic, drawn tier
== model on every sample. BTC 1m forming bars stayed tier 0 (quiet market) — no flicker but
not a strong test. Interval 1m → 5m → 1m: flags recomputed (120 → 115), bubble store kept
(19 big prints, all drawn).

Screenshots: `four-panes.png`, `btc-1m-bubbles.png`, `btc-1m-tooltip.png`,
`btc-1m-abnormal-tooltip.png`, `btc-15m-bubbles.png`, `btc-15m-abnormal-tooltip.png`,
`eth-5s-*.png`, `sol-bybit-1m-*.png`, `perf-1000-bubbles.png`, `settings-big-trades.png`,
`settings-abnormal-volume.png`.

## Known limits / unverified
- Hyperliquid not checked live. Headed Chrome, DPR 2 (Retina) and a busy market not measured.
- Backfill depth is budget-bound: 10 Binance requests ≈ 10k aggTrades (≈ 15–30 min on this
  quiet Sunday, only minutes on busy days); on 15m+ charts bubbles cover only recent bars.
  Bybit/HL history is partial by venue design (labelled). Live history is lost on reload or on a
  data-setting change (the store is rebuilt from a fresh backfill).
- The aggTrades rate window (60 req/min) is shared with footprint and seconds candles; on
  busy layouts the first backfill can be refused → status "live stream only, retrying".
- The adaptive percentile needs 300 prints; with a short backfill the first prints use the fixed
  threshold only (status shows "percentile warming up").
- Delta is available only for 1m–1h and recent bars; none on seconds or > 1h intervals.
- The chart's own last closed 1m candle once differed from REST klines (17.355 vs 17.548 BTC,
  and 1 bar in the final check) — this is the existing candle feed, not these modules;
  abnormal-volume uses the chart's candles.
- Overlapping bubbles: hover picks the topmost (smallest); the larger one underneath is
  reachable only at its edge.
