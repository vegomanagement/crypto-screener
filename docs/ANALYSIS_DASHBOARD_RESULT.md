# Analysis dashboard under the charts (/ui): result

Branch `claude/analysis-dashboard` (from `claude/integration` @ 242ea32). Not pushed.

## What changed

The coin analysis now has its own full-width page section below the charts. You scroll down to it.
The narrow right-hand card holds only a short summary.

- **Layout.** The chart area still takes one full screen: header, toolbars, charts, the watchlist and the footer. The page now scrolls, and `#analysis-dashboard` sits right below that screen at full width.
  - The watchlist stays beside the charts only. It is for navigating the charts, so it has no reason to run down the side of a long report. Giving the dashboard the full width makes room for three columns on wide screens.
  - **"Analysis ↓"** is a button in the top bar (hidden at ≤ 1000 px with the other top-bar buttons). The same link is in the footer, and the sidebar has an **"Open full analysis ↓"** link.
  - A **sticky header** stays at the top of the dashboard. It shows venue · symbol · interval of the active chart, the age of the analysis and of the last closed bar, and an "↑ Charts" link.
- **Wheel.** `installWheelGuard(grid)` adds a non-passive `wheel` listener in the bubble phase. It calls `preventDefault()` only when the wheel is over `.chart-stage`. The chart library handles the zoom first, and the page does not scroll. Anywhere else, the wheel scrolls the page.
- **Order of the content, most important first:**
  1. **Verdict strip:** a large long/short split bar with ▲/▼ and a striped short side, so it does not rely on colour alone. Then the verdict word and its confidence, and one plain sentence explaining the maths (factor points and net, baseline net, weights, logistic curve, neutral band). Below that: calibration (⚠ "not a calibrated probability" until there are ≥ 20 scored outcomes), source, a UTC timestamp, model and snapshot latency, the disagreement warning, the exact formula in a collapsed section, and the Analyze / Auto-refresh / Levels-on-chart controls.
  2. **Summary and scenarios:** Hermes' summary as prose, then bull / base / bear cards. Each card shows its trigger, target and invalidation. The base case is the scenario on the bias side whose target is nearest the price.
  3. **Key levels:** sorted by absolute distance from the analysed close. Columns: price, type, signed % distance, distance in ATR (14-bar ATR of the chart), the cited source fields with their values, the reason, and **Centre** / **Save** buttons. The buttons are disabled when the analysis is stale.
  4. **Evidence:** long and short in two columns, each sorted by strength 3→1. Each factor shows strength dots, a group badge and the cited fields as tags with their value at analysis time. Hovering a tag shows its source and age, or "—" with the reason.
     - Groups: Trend / Momentum / Volume / Funding / Positioning / Liquidity / Structure, plus Sentiment.
  5. **Market metrics** in titled cards: Price & volatility, Trend, Funding, Positioning, Liquidations, Liquidity & volume, Sentiment & session, plus the full Hyperliquid whale table across the whole width.
     - Every row's tooltip shows source · venue · age. A missing value shows "—" with the API's own reason.
     - Snapshot-based cards hide their numbers while the analysis is stale, rather than showing another chart's numbers.
  6. **Risks and caveats:** the risks, the validator warnings (collapsed) and "Not financial advice".
- **Sidebar.** The analyst card is now a compact summary: split bar, verdict, status, Analyze, and "Open full analysis ↓". The Funding and Positioning sections and the factor, level and scenario detail were removed from it, so each piece of information appears in one place only.
- **Server.** `POST /api/agent/analysis` now also returns `snapshot`: the exact snapshot Hermes analysed, about 14 KB. The dashboard reads cited values and metrics from it, so it needs no extra requests. Test: `tests/test_analyst_service.py::test_response_carries_the_analysed_snapshot`.

### Modules (vanilla JS, UMD, no new dependencies)

| file | role |
|---|---|
| `static/analysis-dashboard-model.js` | pure view-model: verdict, scenarios, levels, factors, groups, cited tags, stale state, header |
| `static/analysis-metrics.js` | pure metric rows and the Price / Trend / Funding cards |
| `static/analysis-metrics-flow.js` | Positioning / Liquidations / Whales / Liquidity / Sentiment cards; reuses `PositioningPanel.viewModel` |
| `static/analysis-dashboard-render.js` | escaped HTML string builders (no DOM) |
| `static/analysis-dashboard.js` | controller (IntersectionObserver, one poll timer, event delegation, `destroy`), wheel guard |
| `static/analysis-dashboard.css` | styles: workspace.css variables, body text ≥ 13 px, tabular numbers, 3 → 2 → 1 columns at 1280 / 860 px |

Reused rather than duplicated: `WorkspaceAnalyst` (gauge, splitLine, statsLine, levelRows, disagreementText, positioningTarget, fmtPrice), `WorkspaceFunding` (pct, annualPct, countdown, crossRows, sparkline) and `PositioningPanel.viewModel`. The analyst controller keeps the single analysis state; the dashboard subscribes to it (`subscribe` / `view()`).

### Performance and cleanup

- **Rendering only on screen.** The dashboard renders and polls only while an `IntersectionObserver` reports it on screen, with `rootMargin: 0px 0px -24px 0px`. The dashboard starts exactly at the fold, and an element that only touches the viewport edge counts as intersecting, so it must be 24 px into the viewport.
- **Polling.** One 30 s positioning poll runs only while the dashboard is visible, with `owner=analysis-dashboard`. Funding comes from the active pane's existing cache through the workspace's existing 1 s tick, so the dashboard has no timer of its own for it.
- **Late responses.** A response that arrives after a pane or symbol switch is dropped, using a sequence number.
- **One instance.** The dashboard is mounted once and is not tied to panes, so layout switches create nothing new. `destroy()` disconnects the observer, clears the timer, unsubscribes and removes the root's `click` and `change` listeners. Calling it twice is safe.
- **Stale analysis.** After switching to a pane, symbol or interval that has no analysis, the previous result stays visible but greyed, labelled "Previous analysis for BTCUSDT 15m — re-analyze". The header reads "showing BTCUSDT 15m — re-analyze", and the sidebar status says the same.

## Tests

| suite | result |
|---|---|
| `python -m py_compile …` (CI list) | ok |
| `ruff check --select E4,E7,E9,F …` (both CI lines) | All checks passed (twice) |
| `pytest` | **1446 passed** |
| `node --test tests/*.cjs` | **220 tests, 214 pass, 0 fail, 6 skipped** (the existing Playwright tests, which skip when Playwright is not installed) |

What the new tests cover:

- `tests/analysis_dashboard.test.cjs` (8 tests):
  - levels sorted by distance, with % and ATR distance, and "— (no ATR)" when ATR is missing;
  - factors sorted 3→1 per side, with group mapping and tag values or reasons;
  - the verdict sentence for Hermes and for the baseline fallback;
  - scenario order and the base case for long and short bias, and "No 1 ATR" not being read as a target;
  - stale state: the label, the header, Centre/Save disabled, and no calibration from another chart;
  - metric cards: grouping, provenance, "—" with reasons, and every number withheld while stale;
  - HTML escaping of model text;
  - the wheel guard: blocks the page scroll only over a chart, listener is non-passive, can be removed.
- `tests/analysis_dashboard_controller.test.cjs` (4 tests):
  - off-screen: no render, fetch or timer; on screen: one render and one 30 s timer; off-screen again: the timer is cleared;
  - the `rootMargin` setting;
  - following the pane switch and dropping a late BTC response;
  - 20 mount/destroy cycles: every observer disconnected, 0 timers, 0 subscribers, 0 root listeners;
  - Analyze goes through the shared controller, and `tick()` re-renders only when the funding cache changes.
- Updated tests:
  - `analyst_panel.test.cjs`: the compact summary, the stale result being kept, and that the card no longer duplicates the moved content;
  - `analyst_panel_stats_race.test.cjs`: the calibration check now reads `view().stats`;
  - `funding_ui.test.cjs`: the dashboard's funding card replaces the old sidebar section.

## Live checks (headless Chrome, CDP 9572, server :5097, real Hermes)

The scripts are `docs/evidence/analysis-dashboard/live.cjs` and `offscreen.cjs`. The raw results are in `live_*.json` and `w*_analysis_*.json`.

**Analyses.** All runs returned `status ok`, `source hermes` and a `snapshot`. Each had 12 factors, 8 levels and 3 scenarios.

| viewport | market | split | bias | confidence | model latency | snapshot time |
|---|---|---|---|---|---|---|
| 1600×1000 | Binance BTCUSDT 15m | 58/42 | long | low | 20.5 s | 2.7 s |
| 1600×1000 | Bybit ETHUSDT 1h | 13/87 | short | medium | 21.5 s | 1.3 s |
| 1280×800 | Binance BTCUSDT 15m | 63/37 | long | low | 23.5 s | 1.7 s |
| 1280×800 | Bybit ETHUSDT 1h | 11/89 | short | medium | 22.7 s | 1.1 s |
| 820×900 | Binance BTCUSDT 15m | 60/40 | long | low | 21.4 s | 1.7 s |

**What the 1600 BTC run looked like on the page:**

- The Hermes agent said "neutral" while the scored evidence computed long, and the dashboard showed the disagreement warning.
- Factors: long 4 factors / 6 points, short 8 factors / 13 points. 25 cited tags, all of them resolved to a value.
- Levels started 0.00 % / −0.08 % (−0.47 ATR) / +0.10 % (+0.58 ATR) from the 81,412.7 close.

**Wheel** (CDP `mouseWheel`, 5 × deltaY 120 over the chart centre). Results were the same at both sizes:

| | 1600×1000 | 1280×800 |
|---|---|---|
| visible bars over the chart centre | 130 → 220.85 | 130 → 216 |
| page `scrollY` after wheeling over the chart | 0 | 0 |
| wheel over the price axis | `scrollY` stays 0 | `scrollY` stays 0 |
| 3 × deltaY 200 over the footer | `scrollY` 0 → 600 | `scrollY` 0 → 600 |

**Switching panes.** Clicking the Bybit ETHUSDT 1h pane gave:
- dashboard header "Bybit · ETHUSDT · 1h · showing BTCUSDT 15m — re-analyze";
- a greyed verdict with "Previous analysis for BTCUSDT 15m — re-analyze";
- sidebar "Shown: BTCUSDT 15m — re-analyze for this chart".

After Analyze, the header read "analysed 28s ago · last closed bar 28m ago".

**Off-screen polling** (`live_offscreen_poll.json`, server log):
- at scroll 0 for 65 s: **0** dashboard positioning requests, 0 renders, `visible:false`, `polling:false`;
- scrolled to the dashboard for 65 s: **3** requests (the first one plus two 30 s polls).

An earlier build used `rootMargin: 120px` and polled at scroll 0 (2 requests in 65 s). That is fixed.

**20 layout switches** (`live_w1600.json`, `live_w1280.json`), cycling 1 → 2cols → 2rows → 3cols → 4grid → 6grid, sampled after each switch with a forced GC:
- **Listeners stay flat.** The same layout always gives the same counts. 2 panes: `jsEventListeners` 2845, document 21 / window 7. 6 panes: 3193, 57 / 15. 1 pane: 2758, 12 / 5. Analyst subscribers stay at 1, there is one observer, and the dashboard stays alive.
- **Heap.**
  - In the 1600 run, heap at 2cols went 5.55 → 5.71 → 6.64 → 6.87 MB across the 20 switches.
  - Two more 20-switch runs on the same page (`live_layouts_extra_a/b.json`, 60 switches in total) gave 2cols values of 5.99, 6.03, 6.91, 6.78, 6.98, 6.80, 6.39 and 7.08 MB. The heap levels off around 6.4–7.1 MB and does not keep growing.
- **DOM nodes** at 2cols dropped from 16.7 K to a stable ~12.3 K after the first cycle.

**Screenshots** (each one inspected; layout changed between rounds):
- `w1600_*`, `w1280_*`: full page (`full_btc`, `full_eth`), each section (`section_verdict|summary|levels|factors|metrics|risks`, `eth_section_*`), `stale_verdict_eth`, `viewport_top` (charts plus the compact sidebar) and `viewport_sticky`;
- `w820_*`: the one-column layout.

**Changes after looking at the screenshots:**

- **Round 1:**
  - The metrics grid left large empty gaps; it now uses columns, with the whale table across the full width.
  - A "Range chop" scenario was labelled a bear case because of the "1" in "No 1 ATR".
  - Divergence read "true".
  - Tags said "1 bars ago".
- **Round 2:**
  - Scenario order was wrong for short bias (the base case sorted last).
  - The stale verdict showed calibration from the newly active chart.
  - The calibration line was entirely orange.
- **Round 3:** at 820 px the verdict and summary overflowed sideways, because the grid track grew to the width of the levels table. The column is now `minmax(0, 1fr)`.

The API key was never printed or logged. A check of the evidence folder, `static/`, `templates/`, `tests/` and the server log found it **0** times.

## Not verified / known limits

- Only headless Chrome was tested. Safari and Firefox were not, and neither were real trackpad gestures or horizontal wheel; only synthetic CDP `mouseWheel` was used.
- Phone-width touch scrolling (≤ 700 px) was not tested. At 820 px the wheel guard still applies over charts, as intended.
- Calibration shows "no outcomes scored yet" because the scratch database is new. A calibrated (✓) state was never seen live; it is covered only by the existing `gauge` logic.
- Liquidations come from a collector that had only just started. Coverage was "covered 2 of 60 min" with 0–1 events, which the dashboard labels honestly. Full 24 h windows were not observed.
- The heap measurements vary by about ±1 MB between samples. The numbers above show a plateau, not a proven zero leak. Listener and node counts are exact.
- The Hyperliquid whale table needs the tracker's first poll, about 1 min. Before that, the card shows "— whale tracker warming up".
- Factor groups are inferred from the cited snapshot paths, by a mapping in `groupOfPath`. Hermes does not output a group.
