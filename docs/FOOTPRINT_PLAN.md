# Footprint (Deep Print–style) — plan

Branch: `claude/footprint` (from `claude/chart-workspace` @ `a530726`).

## Research basis (clean-room)

- Reference product: DeepCharts "Deep Print (Footprint®)". No DeepCharts subscription;
  only public material was examined.
- REA `analyze-web-bundle` on the public help page (evidence:
  `docs/evidence/footprint/rea-web-bundle-deep-print-page.json`) found only 10 small
  marketing-site (Webflow) scripts — **no footprint engine is shipped publicly**; the app
  is behind login. Therefore nothing of their code is used or available.
- Behaviour spec comes from their public help-centre/blog text, saved verbatim under
  `docs/evidence/footprint/deepcharts-public-docs/`. Everything below is our own design
  on top of standard order-flow definitions. Do not copy their wording into UI copy;
  do not use the "Footprint®" / "Deep Print" names in the product.

Feature list extracted from their docs (★ = this MVP):
- ★ Cell types: Volume, Bid×Ask split, Delta, Delta+Volume. ★ Modes: Box, Profile (histogram).
- ★ Input: traded volume; number of trades. (Their "aggregate volume" and order-book input — later.)
- ★ Tick grouping: auto (by visible cell height) / manual N ticks. Min/Max value filter.
- ★ Coloration: none / fixed / fading by Volume, Delta, Imbalance; "only dominant side".
- ★ Bar POC (max volume price) border; ★ Delta POC (max |delta|, min-delta threshold).
- ★ Value Area per bar (default 70%), line + outside dimming.
- ★ Diagonal imbalance: ask@p vs bid@(p−1 tick), ratio ≥ X% (default 300), min volume diff,
  "include zero" option; ★ stacked imbalance (≥ N consecutive, default 3) zones.
- ★ Bar delta footer + delta % per bar.
- Later: single prints, volume cluster, pullback, alerts, multi-column (≤4), imbalance
  rejector / tracker with extension until retest, bar-POC extension lines.

## Data (only real public exchange trades; never synthesize)

Aggressor side convention: **ask volume = buyer-initiated (taker buy)**, **bid volume =
seller-initiated (taker sell)**.

| Venue | History backfill | Live | Side field |
|---|---|---|---|
| Binance USDT-M | REST `GET /fapi/v1/aggTrades` (`startTime`/`endTime` ≤ 1h window, `limit` 1000, paginate by `fromId`) | WS `<sym>@aggTrade` | `m=true` → buyer is maker → **seller aggressor → bid** |
| Bybit linear | REST `/v5/market/recent-trade` (≤1000 latest only) | WS `publicTrade.<sym>` (already subscribed in `market-stream.js`) | `S`/`side` = taker side: Buy→ask, Sell→bid |
| Hyperliquid | REST info `recentTrades` (latest only) | WS `trades` | `side` "B" = buy aggressor → ask, "A" → bid |

Coverage must be explicit per bar: `complete` (all trades for the bar's whole window
fetched) vs `partial` (bar window not fully covered — e.g. Bybit/HL older bars, or the
live bar started before the stream connected). Partial bars are drawn but visibly marked;
missing bars are blank, never interpolated.

Backfill budget: cap trades fetched per request (e.g. ≤ 200k aggTrades, ≤ N bars) and
return `truncated` + coverage instead of hanging. Cache per (venue, symbol, bar_open)
for closed bars.

## Backend

New module `footprint.py` (pure functions, no I/O) + thin fetcher in `footprint_data.py`
+ route `GET /api/footprint?exchange=&symbol=&interval=&limit=&group_ticks=` registered
like `liquidity_api.py` (isolated `register_footprint_routes(app)`).

Per bar output: `time, open, high, low, close, tick_size, group_ticks, levels:[{price, bid,
ask, trades}], total, delta, delta_pct, buy_vol, sell_vol, poc, delta_poc, va_high, va_low,
imbalances:[{price, side, ratio}], stacked:[{side, from, to, count}], coverage, source`.
Price bucketing uses exact decimal/integer tick math (no float drift); bucket = floor to
`tick_size × group_ticks`.

Value area: standard algorithm — start at POC, repeatedly add the higher-volume of the
two adjacent (grouped) levels until ≥ VA% of bar volume.

## Frontend

`static/footprint.js` (UMD like `market-stream.js`, testable with `node --test`):
- Canvas overlay plugin in the workspace indicator-plugin system; per-pane toggle +
  settings persisted in the existing versioned storage.
- Pixel mapping via `series.priceToCoordinate` / `timeScale().timeToCoordinate`, DPR-aware,
  redraw on visible-range change and resize (see Hermes skill
  `lightweight-charts-pixel-coords`).
- Level-of-detail: draw text cells only when bar width ≥ ~40px and cell height ≥ ~9px;
  otherwise auto-raise tick grouping; if still too dense, draw only POC + imbalance marks
  + delta footer.
- Live: merge WS trades into the forming bar client-side using the same aggregation rules
  as Python (shared fixtures prove parity).

## Acceptance

1. Unit tests (pytest + node --test) for bucketing, side mapping per venue, POC/delta POC,
   VA, diagonal + stacked imbalance, include-zero, coverage flags, truncation.
2. **Live reconciliation, Binance BTCUSDT 1m, ≥ 30 closed bars**: Σ(bid+ask) per bar equals
   kline `volume` and Σask equals kline `takerBuyBaseAssetVolume` (exact to the venue's
   quantity precision). Record the output in `docs/evidence/footprint/`.
3. Same reconciliation on ETHUSDT 5m; Bybit/HL: recent bars marked `partial`/`complete`
   honestly.
4. Real Chromium screenshot of the workspace with footprint on BTCUSDT 1m and 15m; no JS
   errors; zoomed-out view degrades gracefully.
5. Full existing test suite still green; ruff selections as in CI.
