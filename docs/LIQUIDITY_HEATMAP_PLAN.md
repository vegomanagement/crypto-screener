# Own-collected order book liquidity heatmap — implementation scope

User chose self-collection rather than paid CoinGlass API. Target is the historical limit-order heatmap and current bid/ask depth profile seen in the supplied CoinGlass Legend screenshot, not a modeled liquidation map.

## Scope and integrity

- Observe public perpetual order book snapshots from the selected Bybit, Binance or Hyperliquid native instrument. No private keys, orders, paid provider, cross-venue fallback or candle-derived fake liquidity.
- First version samples complete returned snapshots approximately every 10 seconds, not every incremental exchange event. It therefore cannot claim tick-complete history or capture all short-lived orders.
- History starts when collection is explicitly enabled. Earlier times, network failures and expired collection leases remain empty. Retention truncation is disclosed, not filled with invented values.
- Record actual timestamp, observation timestamp, venue, native symbol, depth limits and native quantities. No unverified USD/USDT quantity conversion. Report shallow returned depth (especially Hyperliquid) rather than implying full market depth.
- Price grouping sums levels within one real observation only. Time downsampling selects actual observed frames; it does not sum successive resting-book snapshots, which would double-count the same orders.

## Lifecycle and storage

- Isolated SQLite database `.data/liquidity.sqlite3`, ignored by git. Never changes `signals.db`.
- Bounded collector: at most six market keys, shared between duplicate chart panes; leases expire after 90 seconds unless renewed. Disabling the layer, hiding/removing a pane or closing the UI releases or eventually expires its lease. No full-Watchlist polling.
- Collection requires the local server to keep running; this change does not install a system daemon, launch agent, scheduled job or production service. A later unattended deployment can be authorized separately.
- Retain up to seven days subject to bounded storage. Expose true coverage and errors. Stop workers explicitly during test cleanup.

## Interface

- Off-by-default Liquidity layer, independent of forming-price WebSocket and existing indicators.
- Low-to-high intensity palette, opacity, price binning and minimum quantity settings. Current bid/ask profile, observation-time/price/quantity tooltip and collection status.
- Clip to plot area; preserve axes, zoom/pan/crosshair and candle visibility. Ten-second samples must not be stretched into four-hour blocks of alleged observed liquidity.

## APIs

- `POST /api/liquidity/watch` and `/unwatch`: JSON `{exchange,symbol,owner}`. Explicit selection only; same-origin controls, bounded body and identifiers.
- `GET /api/liquidity/history`: exchange/native symbol, start/end milliseconds, maximum 7-day range, `max_frames` 10–600. Empty history is HTTP200 with empty frames; viewing it never starts a worker or creates a new database.
- Blueprint registration has no worker/database side effects at import time. Legacy trading/notification routes and data integrity rules are untouched.

## Acceptance

Offline tests cover parsing/provenance, persistence/restart/retention, lease/refcount/rate bounds, gaps/time downsampling, request validation and cross-site rejection. Real public snapshot checks and an actual collector→SQLite→API→browser integration test must be run before claiming completed functionality. Browser fixtures and live exchange evidence remain separately labeled. Independent review and full existing suites are required before local commit. No push, merge or production deployment is authorized by this local implementation scope.
