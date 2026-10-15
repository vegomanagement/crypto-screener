# Exchange-scoped watchlist: acceptance criteria

This extends the locally approved chart workspace; no live trading, account keys or production deployment.

## Scope

- A Bybit / Binance / Hyperliquid dropdown next to Watchlist, with client-side market search and an honest shown/total count.
- Load the complete supported active perpetual catalog using bulk exchange endpoints, not a fixed symbol array or per-coin price requests. Bybit linear and Binance USD-M can include USDT/USDC-settled perpetuals. Spot, options, dated futures and Binance COIN-M are outside this futures-workspace catalog.
- Hyperliquid exact native names and builder-deployed dex:coin names, if available, remain intact. No invented USDT suffix and no silent contract multiplier conversion.
- Changing dropdown replaces only the list; existing chart panes keep their selected exchange. Clicking a market changes only the active pane's instrument AND exchange. Both persist per pane.

## Integrity and lifecycle

- `/api/markets?exchange=...` returns an exchange-tagged full market array and exact total; quotes use actual public data. Missing price/24h-change is unavailable, not a fabricated zero.
- `/api/chart?exchange=...&symbol=...` requests only the chosen venue. Provider failure stays an error; it does not fall back to another exchange.
- `chart_data.build_chart_snapshot(..., exchange=...)` rejects mismatched candle provenance and supports case-sensitive native symbols. Legacy direct callers without exchange retain their strict USDT contract.
- Abort previous catalog/chart requests and check generation/exchange before applying a response. Clear old rows on dropdown change so old prices cannot appear under the new exchange label.
- Bulk requests cached server-side; refresh does not multiply into requests per visible market. Bybit instrument pagination must follow all cursors and detect cycles.
- Search handles symbol/display/base/quote and shows no-results state. Do not truncate the universe silently; if rendering is paged or virtualized, disclose visible count and provide access to the full list.
- Old browser settings migrate to exchange=bybit. Native casing, quote currency and multiplier remain metadata, not guessed alias conversions.

## Validation gates

- Offline fixture tests for all three parsers, pagination, active/delisted filters, nonfinite quote values, failures/cache expiry, exact-venue candles and Hyperliquid 3m aggregation if required.
- API tests for bad exchange, no fallback, native symbol case preservation and source mismatch rejection.
- Browser tests for catalogs >10 symbols, exchange switching, search, per-pane venue isolation, persistence and stale catalog-response races. Synthetic fixtures identified separately from public-API checks.
- Live verification of actual catalog counts, representative charts and displayed venue badges. Counts computed from API payloads, never inferred from a partial viewport.
- Full existing Python suite, JS regressions, syntax, correctness lint and independent review before local commit.
