# Scalping section (Vataga-style) — analysis and plan

Branch: `claude/scalping` (from `claude/footprint`; reuses footprint aggregation and the
trade streams in `market-stream.js`).

## Research basis (clean-room)

Reference: Vataga.terminal 7.10.3 (macOS, installed locally at
`/Applications/Vataga.terminal.app`), plus its public wiki
(vataga-trading.github.io/docs) and release notes.

- The app is .NET with an Avalonia UI. Its own GPU renderer draws the DOM, prints and clusters.
  A "VChart" native chart sits alongside an embedded TradingView chart running in CEF.
- REA `inspect-managed-artifact` identified the PE/CLI arm64 assembly, but whole-assembly
  and per-type member inspection stopped at REA's 64 MiB representation budget:
  `Vataga.terminal.dll` is 68 MB, 57 MB of it resources. The evidence for this is in
  REA evidence (kept locally by the maintainer, not committed: third-party material).
- Fallback: we read **metadata only** (type/property names and enum constants) with
  System.Reflection.Metadata (`metadata-dump-Program.cs`; outputs `types.tsv`,
  `config_props.txt`). No IL was decompiled. No code, assets or text are copied. Private
  members are obfuscated, but public property names and enums are readable. Public wiki
  pages are in local research notes (kept locally by the maintainer, not committed: third-party material).
- Use only as a feature inventory. Do not use the Vataga name or wording in the product.

### What Vataga has (from metadata + docs)

| Area | Vataga | Ours today | Take? |
|---|---|---|---|
| DOM ladder | Live bid/ask volumes; best bid/ask/spread rows (`QuoteType`); volumes in $ or lots; `FullScaleVolume` bar fill; big-volume 1/2 multipliers coloured; abbreviate (1K); price scales ×1..×5 ticks or % (`DOMScalesViewMode`, `ScalesPrice/ScalesPercent`); autocentering with distance %; grid 10/50 rows; ruler in % / liquidity / PnL (`DOMRulerType`); mark-price delta; funding % and time to funding; ADL indicator | only 10 s REST depth snapshots (heatmap) | **Phase 1** |
| Prints feed ("time & sales" in the DOM) | Circles or ovals sized by volume (`MinRadius/MaxRadius/MaxSize`, `SizeByVolumeDealsFeed`, `ShowOvalPrints`); connecting line; hide prints < X $; hide the number < Y $; aggregate same-side prints in a time range / tick range (`AggregateDealsRange[InTicks]`); compressed feed | trade WS exists (footprint) | **Phase 1** |
| Clusters next to DOM | Columns by volume / delta / bid×ask (`ClustersByVolume/Delta/BidAsk`); per-timeframe columns; full-scale fill; colour by delta + gradient; mark values ≥ X; show values ≥ Y; double-click to hide | footprint aggregation | **Phase 1** (reuse `footprint.js`) |
| Density lifetime | How long a large resting order has stood at a level (`DensityLifetimeArea`, `ShowDensityLifetimeCommand`) | — | **Phase 1** (needs live L2 only) |
| Volume alerts | Large volume appears; volume removed / decreased by X %; large print ≥ X $ within interval; sound, voice text, popup (`DOMNotificationConfig`) | — | Phase 2 |
| Levels | Simple / signal levels (`PriceLevelType`) set from DOM or chart (`PriceLevelSourceType`) and synced; level manager; repeat; header blink; Notifications window (active/triggered) | — | Phase 2 |
| Chart drawings | Line, Ray, HorLine, HorRay, Rectangle (`VDrawingObjectType`); magnet; last drawing type; day extremes; time-to-next-candle; session start line | none in workspace | Phase 2 |
| Timeframes | 1s, 5s, 15s, 30s … 1M (`KlineIntervalType`) | 1m+ (30s disabled) | Phase 2 (build seconds candles from trades) |
| Linking | Ticker opens chart + DOM, spot+futures; clone DOM; open chart for DOM | separate panes | Phase 1: DOM ↔ chart link |
| Hotkeys | Centering, zoom, ±tick size, scale 1–5, volume 1–5, squeeze/unclench, ruler mode | — | Phase 1: view hotkeys only |
| Trading | Limit/market/stop by mouse, drag orders, SL/TP, breakeven, reverse, close all, leverage, PnL | research-only | **Not in scope** (decision pending; paper mode possible later) |

## Phase 1 scope — `/scalp` page (view-only, no orders)

1. **Live L2 order book in the browser** (direct public WS, same pattern as `market-stream.js`):
   - Binance USDT-M: REST `/fapi/v1/depth?limit=1000` snapshot + WS `<sym>@depth@100ms`
     diffs, with the official sync (`U <= lastUpdateId+1 <= u`, then `pu == prev u`;
     on a gap, resync). Use the `/market/ws` host already used for klines, if applicable.
   - Bybit linear: `orderbook.200.<sym>` (snapshot + delta, `u`/`seq`; snapshot resets).
   - Hyperliquid: `l2Book` (full snapshots; note the venue's depth cap).
   - The book engine is a pure module (`static/orderbook.js`, UMD) with deterministic tests
     for sync, gaps, resets, crossed-book detection, price grouping (×N ticks and %),
     and $-notional.
   - Honest status: synced / resyncing / stale / depth-capped. Never fabricate levels.
2. **DOM ladder renderer** (canvas, DPR-aware, rAF-throttled, ≤ 1 paint/frame):
   price column, bid/ask volume bars with full-scale fill, best bid/ask and spread rows,
   big-volume 1/2 colours, $ / native toggle, abbreviation, grid, autocenter (+ distance %),
   wheel scroll, Ctrl/⌘-wheel zoom, scale ×1..×5 ticks and % mode, ruler (% from a clicked
   price), mark-price delta and funding % / countdown, where public.
3. **Prints feed** to the left of the ladder: scrolling time axis, circles sized by $ volume
   (min/max radius), buy/sell colour, optional line, filters (hide < X $, hide label < Y $),
   aggregation of same-side prints in a time window / tick range. Source: existing trade
   subscriptions (`subscribeTrades`).
4. **Cluster columns** to the left of the feed: N columns of the chosen timeframe built with
   `footprint.js` rules (volume / delta / bid×ask modes, full-scale fill, colour by delta,
   value thresholds). Closed columns can backfill from `/api/footprint` (Binance complete;
   Bybit/HL partial as already labelled).
5. **Density lifetime**: for levels ≥ big-volume threshold, track first-seen time while the
   level stays ≥ threshold (keyed by price); show the age. Reset on removal; it is
   explicitly "since this page observed it".
6. **Page**: `/scalp` (Flask route + template), 1–4 DOM columns each with its own symbol and
   venue, an "open chart" link to `/ui` for that symbol, settings persisted (versioned
   localStorage), view-only hotkeys (center, zoom, scale 1–5). Link from the `/ui` header.

## Acceptance (Phase 1)

1. Unit tests (node --test) for the book engine: Binance diff sync incl. gap → resync, Bybit
   snapshot/delta/reset, HL snapshot, grouping, notional, crossed-book guard, density-lifetime
   bookkeeping, feed aggregation, cluster parity with `footprint.js` fixtures.
2. Live check, Binance BTCUSDT for ≥ 60 s: the browser book after sync equals a fresh REST
   `/fapi/v1/depth` snapshot on the top 20 levels within one update interval (compare by
   `lastUpdateId` alignment, or report the measured diff honestly). Same sanity check on
   Bybit (`/v5/market/orderbook`).
3. Feed: the count/volume of prints over a 60 s window equals the aggTrade/publicTrade count
   received in the same window (no dropped prints at render level).
4. Real headless-Chrome screenshots of `/scalp` with 1 and 3 DOM columns (BTC/ETH/SOL),
   no JS errors, FPS/paint budget noted (frame time measured).
5. Full pytest, all `node --test`, and ruff as in CI stay green. No new pip dependencies.
