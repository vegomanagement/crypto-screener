# Streamed chart display and indicator plugins — local verification

## Implemented

- Browser-native public exchange WebSocket subscriptions with shared/refcounted lifecycle, reconnect/backoff, identity checks and cleanup. Bybit native kline + public trades, Binance current market WS route, Hyperliquid candle subscriptions. Real REST forming-bar bootstrap supplies genuine OHLCV, not ticker-derived fabricated bars.
- The forming bar is display-only. Closed history remains separate for indicator calculations, confirmed FVGs, alerts and backtest semantics. Native intervals stream; custom aggregated intervals currently show an explicit unsupported/offline status, not invented ticks. No order submission or account credentials.
- Schema-driven indicator controls and settings with migration from older browser storage. EMA and MA source/period/color editing, FVG size/fill-policy/color/opacity controls. Fractional values in integer-period fields are rejected before Apply; persisted invalid integer periods revert to documented defaults. Decimal amplitude/opacity/threshold settings remain decimal.
- Turtle upper/lower bands, daily/weekly candle-volume VWAP approximations, prior complete day/week high/low levels, configurable DST-aware Asia/London/New York sessions. New plugins are disabled by default. Fine-resolution history and complete anchor periods are required; warnings disclose insufficient/coarse history. Existing incomplete-history candles are never padded.
- Turtle algorithm and ESS initialization verified against versioned published Pine source; MPL notices/credits retained in NOTICE and the port. Numeric bit-exact equivalence to TradingView has not been asserted; see INDICATOR_EXTRAS.md for source and limitations.
- FVG rectangles now use explicit candle-slot boundaries. Actual chart-canvas regression measured left 803/803 and right 1073/1073 CSS px at 15px bar spacing. Candle bodies are narrower than slots. No claim of an exact clone of every setting of the linked TradingView script.
- Opt-in LOCAL FVG creation/full-fill feed, bounded and persisted, silent for historical initial load and deduplicated across panes. No Telegram or browser push permission request.
- Collapsible venue-native funding/OI and EMA7/14/28 bias for 1h/4h/D. Funding is rendered from fractional values exactly once as percent. Unknown funding interval or OI units are displayed as unknown; no assumed USD conversion. Context is a cached snapshot, not a tick feed.

## Verified by parent execution

- **1,075 Python tests passed**; 11 existing matplotlib warnings.
- **48 Node/browser tests passed, zero skips** with actual Chrome/Playwright and the locally pinned chart library. Offline invocation intentionally skips browser-only tests.
- Scoped correctness Ruff, JS syntax and `git diff --check` passed.
- Real integrated local browser loaded genuine Bybit 1m data, received 26 WS frames and displayed four distinct price labels in eight seconds, then loaded D with Turtle bands and actual context. No JS runtime errors.
- Separately captured real WS evidence includes repeated 1m/D updates from all three venues and Binance BTCUSDC. Public context/forming checks succeeded for Bybit, Binance, Hyperliquid native and HIP-3.

## Independent review findings and corrections

Initial review found fractional EMA persistence and permanently suppressed gap-history recovery. Separate fix work added exact failing regressions, rejected invalid settings and implemented bounded autonomous history recovery (700ms initial backoff, six attempts, in-flight deduplication, cancellation and stale-completion rejection). Failure to bridge explicitly displays STALE / Reload rather than pretending a current chart. A band test harness was repaired to supply the newly required timeframe context; production computes both lines and bands with the actual interval hint.

The full green runs above were executed after these fixes. Focused independent re-review passed: no security concerns or logic errors in the corrected settings validation, lifecycle cancellation and gap recovery. Its nonblocking suggestion was applied: retry tests now assert exactly six requests and delays of 700, 1400, 2800, 5600, 11200 and 22400 milliseconds. All 48 Node/browser tests passed again after strengthening those assertions. This is local verification, not a production release record.

## Evidence and scope

Evidence is under `docs/evidence/stream-indicators/`. Fixture-based tests and live public-feed reports are labeled separately. This is a local development workspace, not a deployed release. No push, PR or deployment has been performed for this batch. Custom-timeframe streaming reconstruction and a general Pine interpreter are not included. Browser settings/favorites are local to the browser profile, not synchronized account preferences.
