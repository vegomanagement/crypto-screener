# Scalping — Phase 2 plan

Base: `claude/scalping` (PR #75). Two parallel tracks in separate git worktrees:

- **Track A** `claude/scalp-alerts`: alerts and signal levels on `/scalp`, plus a
  notifications panel.
- **Track B** `claude/chart-drawings`: drawing tools and seconds timeframes on `/ui`.

The tracks share **one contract**: the price-levels store below. Each track implements
its side against this contract, so levels set in the DOM appear on the chart and vice
versa (same browser, same origin).

## Shared contract: `static/levels.js` (UMD, pure + tiny storage adapter)

Track A creates this file. Track B must not edit it, only consume it. If Track B needs
the file before Track A lands, Track B vendors an identical copy from this spec and the
files are reconciled at merge.

- Storage key: `localStorage['screener.levels.v1']`, JSON
  `{ version: 1, levels: Level[] }`.
- `Level = { id: string, exchange: 'binance'|'bybit'|'hyperliquid', symbol: string,
  price: string (exact decimal string), kind: 'simple'|'signal', source: 'dom'|'chart',
  color: string (#rrggbb), width: 1..4, repeat: boolean, armed: boolean,
  createdAt: ms, triggeredAt: ms|null, note: string (≤ 80 chars) }`.
- API: `load()`, `list({exchange, symbol})`, `add(level)`, `update(id, patch)`,
  `remove(id)`, `clear({exchange, symbol, kind?})`, `subscribe(fn) → unsubscribe`
  (fires on local changes and on the `storage` event from other tabs),
  `crossed(level, prevPrice, price) → boolean` (inclusive cross of the level between
  two consecutive trade prices; exact decimal compare via BigInt scaling, no floats).
- Validation: reject unknown fields and non-finite or non-positive prices. Cap at 500
  levels in total (oldest triggered dropped first). Bump `version` when the schema
  changes.

## Track A — `/scalp` alerts and levels (view-only, no orders)

1. **Levels in the DOM**: Alt+click sets/removes a simple level; Shift+Alt+click sets a
   signal level; drag to move; double-click opens an inline level editor (colour, width,
   repeat, note); hotkey to clear all levels of the column. Levels draw as lines across
   ladder, feed and clusters.
2. **Signal triggering**: on each live trade, `crossed()` against armed signal levels.
   When triggered: play a sound (WebAudio-generated beep, no audio files needed), blink the
   column header for 5 s and then keep it highlighted until clicked, show an optional
   browser `Notification` (requested permission only on user action), and append to the
   notifications panel. Non-repeat levels are disarmed and recoloured; repeat levels
   re-arm after the price leaves by ≥ 1 tick.
3. **Volume alerts per column** (settings, off by default):
   - large resting volume appears ≥ X $ within Y % of mid;
   - a tracked large level decreases by ≥ Z % or disappears (distinguish "traded through"
     from "pulled" by checking trades at that price in the same window; label "pulled"
     only when no trade explains the drop);
   - large print ≥ X $ (single or aggregated within N ms).
   Each alert has its own sound pitch and a 3 s rate limit per (type, price).
4. **Notifications panel**: dockable drawer on `/scalp` with Active and Triggered tabs
   (time, venue, symbol, source, price, type). Delete single/all. Clicking a row centres
   that column on the price. Persisted (triggered list capped at 200).
5. Tests: `levels.js` (cross logic incl. gaps/jumps, exact decimals, storage events,
   caps), alert detectors (appear, decrease, pulled vs traded, rate limit), panel model.
   Live: trigger a real signal level on Binance BTCUSDT (set 1 tick above best ask, wait
   for a cross) and record the evidence. Screenshots.

## Track B — `/ui` drawing tools and seconds timeframes

1. **Drawing tools** on Lightweight Charts panes (canvas overlay, same pixel-mapping
   approach as the footprint/FVG overlays):
   Line (2 points), Ray, Horizontal line, Horizontal ray, Rectangle, Ruler/measure
   (temporary: shows Δprice, Δ%, bars and time). Magnet mode snaps to the bar's
   O/H/L/C. Select, drag points, delete (Del), duplicate, colour/width, lock, hide all,
   clear all. A toolbar in each pane header. Persisted per (venue, symbol) in
   `localStorage['screener.drawings.v1']`, so they are shared by all intervals of that
   symbol. Points are stored as (time ms, exact price string).
2. **Level sync**: horizontal lines/rays created with "level" toggled on are written to
   `levels.js` with `source:'chart'`. All levels from `levels.js` for the pane's
   symbol are drawn on the chart (signal levels dashed). Moving or deleting them on the
   chart updates the store.
3. **Seconds timeframes** 1s / 5s / 15s / 30s: build candles client-side from live
   trades (`subscribeTrades`). Backfill only where real data exists: Binance aggTrades
   via the existing footprint fetcher for the last N minutes (a bounded budget), and
   Bybit/HL from the live stream only. Status is explicit ("live since HH:MM:SS",
   "backfilled from …"). Never fabricate empty candles: gaps stay gaps; a second without
   trades has no candle. Enable the currently disabled 30s button.
4. **Countdown to candle close** on the price scale label for the active interval
   (all intervals), and a session-start line option.
5. Tests: drawing hit-testing, magnet snapping, serialization round-trip, level-sync
   mapping, seconds aggregation (UTC boundary alignment, gaps, out-of-order trades,
   parity with the server-side footprint bucket times), countdown formatting. Live:
   Binance BTCUSDT 5s for ≥ 2 min. For backfilled bars, check that Σvolume of each 1m
   group of 5s candles equals the Binance 1m kline volume (same aggTrade-boundary
   caveat as the footprint). Screenshots of drawings and a 5s chart.

## Both tracks

View-only. No new pip dependencies. Ruff/pytest/node --test green. Evidence in
`docs/evidence/scalping-phase2/` (A) and `docs/evidence/chart-drawings/` (B); result docs
`docs/SCALP_ALERTS_RESULT.md` and `docs/CHART_DRAWINGS_RESULT.md`. Do not name the reference
products in UI or code. Commit locally; do not push.
