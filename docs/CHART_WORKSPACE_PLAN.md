# Multi-chart workspace — scope and acceptance

Reference: the user's TradingView screenshot and FVG (Nephew_Sam_) page. This is an independently implemented research workspace using the existing Lightweight Charts dependency, not the TradingView product or a Pine runtime.

## Requested workflow

- FVG shown as time-bounded semi-transparent rectangles, bullish green and bearish amber, with concise labels. No horizontal full-chart price-line representation of FVGs in the new workspace.
- A TradingView-style top interval strip plus a complete dropdown, applied to the selected pane. Active pane is visually clear.
- Multiple independent panes: one, horizontal/vertical pair, three columns, four-grid and six-grid. Different instruments and timeframes can coexist.
- Right watchlist changes only the selected pane; layouts and pane choices persist locally.

## Data rules

Read-only `/api/chart` must supply candles and FVGs from the same timestamped series. Actual source is visible. Native intervals reuse the exchange adapter. Custom intervals are aggregated from complete, UTC-aligned real bars; calendar months use calendar boundaries. Insufficient history is reported, never filled with generated prices. All chart candles are closed-only; no execution/decision/tracking policy changes are included.

30-second candles are visible as unavailable because the existing REST candle pipeline does not provide them; there is no synthetic 30s subdivision. This can be enabled later only with genuine trade/tick aggregation.

FVG detection is a transparent three-candle rule. Later wicks fully filling a gap remove it; partial fills reduce the residual price range. This is not a claim of matching every option or signal from the linked Pine script.

## Implementation boundaries

- New template and static modules; old single-chart implementation retained at `/ui/legacy`.
- Existing market/strategy endpoints remain intact. No production credentials or Telegram effects.
- Version-pinned Lightweight Charts 4.1.3 served locally with Apache-2.0 license, NOTICE, source URL and checksum manifest. Visible TradingView attribution retained.
- EMA rendering is visual-only, not a modification of the decision engine.
- No automatic deploy/merge. Review before release because this is a multi-file interface change.

## Verification gates

- Offline aggregation tests cover native/custom/calendar periods, closure boundaries, missing/partial groups, invalid interval and no invented price/time values.
- FVG tests cover both directions, creation times, partial mitigation, full fill and no lookahead from unfinished bars.
- HTTP tests cover new/legacy routes, local assets, input validation and actual snapshot data contract.
- Browser check uses the actual vendored chart library, verifies visible charts, all layouts, per-pane symbol/timeframe isolation, persistence, toggles, resize/mobile behavior and stale response protection. Synthetic fixtures, if used for deterministic browser interactions, are explicitly labeled test-only; separate public-exchange smoke checks real data.
- Full existing pytest suite, JS syntax and CI-equivalent correctness lint must pass. Screenshots evidence the built result, not a mockup.

Not part of this change: broker buttons, Pine scripts, all TradingView drawing/alert/replay features, real-time tick charts, Kronos inference or new exchange integrations.
