# Footprint — implemented and verified locally

Branch: `claude/footprint` (based on `claude/chart-workspace` @ `a530726`). Spec: `docs/FOOTPRINT_PLAN.md`, ★ items only. Clean-room: behaviour is based on the public DeepCharts help text in `docs/evidence/footprint/deepcharts-public-docs/`. None of their code or UI copy is used, and the product name is "Footprint" only.

## Delivered

- `footprint.py`: pure aggregation with no I/O. Prices and quantities are exact `Decimal`s parsed from venue strings, and floats are refused. A bucket is `floor(price / (tick × group))` (so `0.3/0.1` is bucket 3, not 2). Venue side mapping: ask = taker buy, bid = taker sell. Binance `m=true` maps to bid, Bybit `S/side` is the taker side, Hyperliquid `B` maps to ask and `A` to bid. Each bar gets:
  - volume or number-of-trades input;
  - bar POC (ties go to the lower price) and delta POC (with a min-|delta| threshold; zero delta is never a delta POC);
  - value area (default 70%), grown from the POC toward the larger neighbour, with ties going up and empty in-range levels counted;
  - diagonal imbalances (ask@p vs bid@p−1, sell bid@p vs ask@p+1), with ratio %, min diff and the include-zero option;
  - stacked imbalance runs (≥ N, default 3);
  - total, buy, sell, delta and delta %.
  - Hyperliquid tick = max(5 significant figures, 10^-(6−szDecimals)), capped at 1.
- `footprint_data.py` uses only real public trades:
  - **Binance:** `/fapi/v1/aggTrades` paged backward by contiguous `fromId`, newest bars first, so the budget truncates *old* history. Each request is capped (40 requests, 200k trades) and there is a rolling limit of 60 aggTrades requests/min. A probe window is always < 1h.
  - **Complete** means proven: the oldest fetched trade is strictly before the bar's open and ids are contiguous. Non-contiguous ids raise an error instead of being merged.
  - Closed complete bars are cached per (venue, symbol, interval, open, tick), so repeat calls extend history progressively.
  - **Bybit** (`recent-trade`, ≤1000) and **Hyperliquid** (`recentTrades`, ~10 trades): the bar containing the oldest trade is `partial`, older bars are missing, and a warning says so.
  - Every complete closed bar is cross-checked against the venue's own kline (`kline_check: exact | mismatch | unchecked`).
- `footprint_api.py`: isolated `GET /api/footprint?exchange&symbol&interval(1,3,5,15,30,60)&limit(≤300)&group_ticks(auto|N)&include_forming&va_pct&imbalance_ratio&imbalance_min_diff&include_zero&stacked_min&min_delta&input`.
  - Registered in `screener.py` next to liquidity. Invalid queries return 400 and upstream failures return 502 without echoing upstream bodies.
  - Every response carries `Cache-Control: no-store`, coverage counts, `truncated`, `warnings`, `last_trade_id` (Binance) or `recent_ids` (Bybit/HL, last 5 s) for exact live stitching.
- `static/footprint.js` (UMD):
  - **Exact math:** a BigInt (1e18 fixed-point) port of the same rules. It is held to parity with Python by the shared fixture `tests/fixtures/footprint_parity.json`, which both suites assert.
  - **Live forming bar:** built from WS trades.
    - Binance continuity is proven by aggTrade id. A gap marks the bar `partial` and triggers one early REST re-fetch, which heals the bar only if ids become contiguous again.
    - Bybit/HL continuity is proven by stream connection time vs the snapshot's `as_of` minus the 5 s dedupe window.
    - On rollover the closed bar is re-fetched from the server about 3 s later.
  - **Canvas renderer:** draws on the pane overlay with DPR handling from the workspace.
    - It uses an integer-logical `logicalToCoordinate` (avoiding the fractional-index trap) and `priceToCoordinate`.
    - Cell types: bid×ask, volume, delta, delta|volume. Modes: box and profile. Coloration: none, fixed, volume, delta or imbalance, plus "only dominant side".
    - It also draws the POC border, dashed delta POC, VA line with dimming outside it, stacked-imbalance bars, delta footer with %, min/max cell filters, and dashed `partial` / `kline Δ` outlines.
  - **Level of detail:** text cells only when the bar is ≥ 40 px wide and the cell is ≥ 9 px tall. Auto grouping raises in 1-2-5 steps until cells reach 9 px. When it is still too dense, only POC/VA/imbalance marks and the delta footer are drawn. Footer-only below 3 px bars. Manual grouping is never silently changed.
- `static/market-stream.js`: a new `subscribeTrades()` channel, separate from kline sockets, which are unchanged:
  - Binance `<sym>@aggTrade`;
  - Bybit `publicTrade.<sym>`;
  - Hyperliquid `trades`.
  - Status events now include `connectedAt`.
- Workspace: a per-pane **FP** toggle in the pane header, persisted in the existing v4 pane storage as `footprint: true`. A ⚙ button opens the shared schema-validated settings popover. Candles become hollow while the footprint is on, and a status line shows the source, coverage counts, missing bars, kline check, gaps, truncation and the current LOD.

## Checks actually executed

- **pytest:** full suite **1266 passed** (baseline before this branch: 1213), 11 pre-existing warnings. That includes 53 new tests in `test_footprint.py`, `test_footprint_data.py` and `test_footprint_api.py`, all using injected fake venues with no network.
- **node --test** `tests/*.cjs`: **60 passed, 6 skipped** (the skips are existing optional browser tests), 0 failed. That includes 12 new tests in `tests/footprint.test.cjs`.
- **Ruff:** the exact CI commands pass. Selections `E4,E7,E9,F` were also run on the new modules and `reconcile_live.py`: clean.
- **Live reconciliation** (real public APIs, production `FootprintService`; script `docs/evidence/footprint/reconcile_live.py`):
  - **Binance BTCUSDT 1m, 40 closed bars, all `complete`** (11 requests): Σ(bid+ask) = kline `volume` and Σask = kline `takerBuyBaseAssetVolume` **exactly on 38/40 bars**. The other 2 are one adjacent pair: a single aggTrade `q=0.173` with 17 fills, first fill 8 ms before the minute boundary, is attributed whole to the earlier bar while the kline splits it by fill (shift 0.172). The pair's sums and the 40-bar window sums are exact. OHLC equal 39/40, kline trade count equal 36/40 (informational).
  - **Binance ETHUSDT 5m, 30 closed bars, all `complete`** (50 requests): exact on 24/30. All 6 mismatches are 3 adjacent pairs that cancel exactly (shift ±0.009 each). Window sums exact. OHLC 29/30, trade count 12/30 (informational, see limits).
  - The service's own `kline_check` flags agree with the independent reconciliation on every bar. Files: `reconcile-binance-BTCUSDT-1m.json`, `reconcile-binance-ETHUSDT-5m.json`.
  - **Bybit BTCUSDT 1m:** 5 complete + 1 partial + 5 missing out of 11 requested. Every complete closed bar equals Bybit kline volume, and the partial bar (8.832 vs kline 12.256) is correctly not complete. File: `coverage-bybit.json`.
  - **Hyperliquid BTC 1m:** only 10 recent trades exist, so 1 partial + 10 missing. Footprint 0.0915 vs candle 0.09265 (partial). File: `coverage-hyperliquid.json`.
- **Real headless Chrome 155** (Playwright with the system Chrome, against the local notification-free launcher bound to 127.0.0.1:5087, live data). Screenshots `footprint-btcusdt-1m.png`, `-15m.png` and `-zoomed-out` variants; report `footprint-browser.json`.
  - 1m at 80 px bars: `cells` mode, group 5×0.1, 61/61 bars complete, kline check 58 exact / 2 differ (marked `kline Δ`).
  - Zoomed out to 8.9 px bars: `marks` mode, no text.
  - 15m: cells ×10 rows, 12 complete / 2 partial, 47 of 61 bars missing on the first load because of the request budget (shown blank and stated in the status line).
  - Bybit/HL browser run (`browser-live-venues.json`): the forming bar grows from WS trades; Bybit heals to `complete` after the re-fetch.
  - **No page errors.** The only console error is `GET /favicon.ico` 404, which already exists and is unrelated (verified in the server log).

## Scope limits / known issues

- **Binance boundary aggregates:** an aggTrade whose fills straddle a bar boundary is attributed to its first fill's time (`T`), while Binance klines split it by fill. Per-bar totals can then differ from the kline by that aggregate, and the window totals stay exact. Individual fills (`historicalTrades`) need an API key, so the bar is marked `kline Δ` with the exact diff, not redistributed (that would invent price levels).
- **"Number of trades" input** counts aggregate fills (`l−f+1`). It does not always equal the kline `n` field (ETHUSDT 12/30 equal). The counts are shown as reported, not reconciled.
- **Request budget:** on long or busy intervals (15m and up) a single request backfills only part of 60 bars. More arrives on later refreshes (cache), and missing bars stay blank.
- **Bybit/Hyperliquid** have no public historical trade endpoint. Their history is limited to the latest trades plus what the open page has streamed. Hyperliquid in particular shows about 1 partial bar until the stream builds history.
- **Coverage limits:** the forming bar's REST part is a snapshot as of `as_of`. Bars streamed while the page was hidden or disconnected are partial.
- **Not built:** the non-★ features (single prints, volume clusters, multi-column, rejector/tracker, alerts, order-book input) and footprint intervals above 1h.
- **Not verified:** mobile layout with the footprint enabled, and the 3m/30m/60m intervals live.
- Not pushed, merged or deployed.
