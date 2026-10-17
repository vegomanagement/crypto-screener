# Chart workspace — implemented and verified locally

Branch: `claude/chart-workspace`, based on main `03b536f`.

## Delivered

- `/ui` is a new multi-chart research workspace. Original single-chart viewer remains at `/ui/legacy`; its legacy UI tests explicitly target that route, rather than being deleted.
- 1, 2 columns, 2 rows, 3 columns, 2×2, and 3×2 layouts; each chart owns its instrument, interval, requests, chart instance and viewport. Click a pane to select it; symbol search and watchlist act on the selected pane. Layout/pane/overlay settings persist in versioned browser storage.
- Green bullish and amber bearish FVG rectangles, bounded by real creation and latest closed-bar times. Fully filled gaps are removed, partially wick-filled gaps shrink to their residual range. Visibility toggles without fetching data. This independently implemented rule is not an exact Pine-script port.
- Compact TradingView-style interval strip and dropdown: 1/3/5/10/12/15/30/45 minutes; 1/2/3/4/8/12 hours; D/2D/3D/W/M. 30s is visibly disabled with an explanation, not fabricated.
- Native candles reused; custom intervals aggregate complete UTC-aligned real base groups. Weeks start Monday UTC, months follow actual calendar boundaries. 2D/3D are Unix-epoch aligned. At most 1000 base candles per call; short available history is disclosed instead of synthesized.
- EMA 7/14/28 and optional EMA200 are visual overlays, SMA-seeded with honest warmup. They do not modify decision logic. Volume, crosshair OHLC, source/freshness, loading/error states, optional visible-only 60s refresh and mobile single-column flow.
- Local pinned Lightweight Charts 4.1.3, licensed Apache-2.0, with LICENSE/NOTICE, checksum manifest and visible attribution. No chart CDN request at runtime.

## Checks actually executed

- Full Python suite: **908 passed**, 11 pre-existing matplotlib warnings.
- Dependency-free JS tests: **5 passed**, `node --test tests/workspace_unit.test.cjs`; syntax check passes.
- CI-equivalent correctness Ruff selections pass; `git diff --check` passes.
- Public Bybit snapshots: BTC4H, ETHD, SOL8H, 1000BONKD, BTC12m and BTCM returned HTTP200 with genuine source=bybit. Monthly history returned 32 bars with an explicit short-history warning.
- Actual Chromium + real local API, no mocked market data: all six layouts; active-pane-only symbol and interval changes; 2D persistence; FVG canvas clears/repaints; 390px mobile has no horizontal page overflow; no JS runtime errors. Additional live checks confirm FVG pixels render on BTC2D,3D,M after coordinate fixes.
- Separate explicitly synthetic browser fixtures exercised stale/out-of-order responses, disposal, refresh gating and error/empty/warmup handling. The report is labeled `fixture_only: true`; it is not live market evidence.

## Independent review and fixes

A fresh read-only review found the initial UI validator dropped 2D/3D and disabled30s metadata, accepted symbol aliases that the exact-instrument backend rejected, and produced NaN FVG boundary coordinates on multi-day periods. It also found fixed-30-day monthly extrapolation. No other concrete aggregation/FVG/XSS/race errors were reported.

All listed findings have been corrected and covered by regression tests. First two were independently found and reproduced in parent live browser integration before receiving the review. 2D/3D coordinate NaN and February/March month-length repros were RED before the fix and GREEN afterward. Menus preserve 2D/3D settings; disabled seconds cannot be stored as an active chart timeframe. Advertised aliases BTC/USDT and BYBIT:BTCUSDT.P canonicalize only their explicit formatting to BTCUSDT; another venue prefix is rejected, not silently remapped. BONK is not silently mapped to a different instrument.

## Scope limits

This is not full TradingView: no Pine runtime, full drawing suite, broker trading buttons, tick/seconds candles, chart replay, or multi-chart crosshair/time synchronization. No invented quotes, simulated live ticks, model forecasts or trade executions. Only closed candles are displayed. Kronos remains a separate future module.

The old viewer retains original drawing/signal/analysis controls at `/ui/legacy`; those tools have not all been migrated to the new workspace. VWAP visual overlay and alternate close-based FVG fill settings are not included in this change. Current FVG policy is visibly labeled wick fill.

Local UI-only launcher is notification-disabled and bound to localhost. Existing production webhook/startup hardening remains separate work. No push/deploy/merge was performed during implementation. This multi-file UI change requires review before release.

Evidence: `docs/evidence/workspace/` contains real screenshots, live API/browser checks, fixture-labeled browser report and test output. Optional browser test harness lives in the local audit scratch area, not application runtime dependencies.
