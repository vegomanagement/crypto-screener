# Workspace follow-ups — scope

## 1. Daily candle "stuck" — verified, not a bug

Live check against real Bybit D candles: the latest visible daily bar is the most recently fully CLOSED day (opens 00:00 UTC, closes next 00:00 UTC). Today's still-forming day is correctly excluded — this is the same closed-only policy that removed backtest lookahead in PR #71. A daily bar only ever moves once per day, so it will always look static intraday; this is expected, not stale.

To address the visual concern without weakening data integrity: a dotted live-price reference line is added per pane, sourced from `/api/ticker` (reuses the existing 30s-cached bulk catalog, no extra per-coin polling). It never alters candle OHLC, FVG or EMA, which remain closed-only. Clearly distinguished from the candle's own price line.

## 2. New overlay strategy: dual MA(20), High vs Low source

Two independent SMA(20) lines computed client-side from already-fetched closed candles: one sourced from the High series, one from the Low series. Different colors, one toggle. Not a backend change — purely a rendering/calc addition in the existing indicator toggle row, same pattern as EMA7/14/28/200.

## 3. Favorites

A star toggle per watchlist row, persisted per exchange+symbol in local storage. A "Favorites" filter button shows only starred markets; sort pins favorites to the top within the current sort order.

## 4. Sortable "Chg%" column

Clicking the "Chg%"/Change header cycles ascending/descending (and off, back to the default catalog order). Visible arrow indicator. Search and favorites filters still apply before sorting.

## Constraints

- No new network cost: ticker reuses the existing cached catalog; no per-coin REST polling loop.
- FVG/EMA/decision-relevant data remains strictly closed-only; only the new dotted live line is non-authoritative and visually distinct.
- All four changes are additive to the current workspace; existing six layouts, FVG, EMA, exchange dropdown and search must keep passing.
