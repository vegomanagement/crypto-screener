# UI fixes: sidebar is the watchlist only; the liquidity heatmap is visible

Branch `claude/ui-fixes` (from `claude/integration` @ 2705802). Live checks ran on 2026-10-18, 07:48–08:17 UTC. Setup: Flask on :5099 started from this worktree, with scratch `DB_PATH` and `LIQUIDATIONS_DB_PATH`, and headless Chrome on CDP :9574 with its own profile, at 1600×1000 and 1280×720 with DPR 1. The scripts are in `docs/evidence/ui-fixes/scripts/`.

## 1. Right sidebar: only the watchlist

**Before.** The sidebar held the watchlist, the compact Hermes card (split bar, verdict, Analyze, "Open full analysis ↓"), the FVG legend and the LOCAL notifications feed. The rows container had `min-height: 140px`, so the other blocks took the rest of the height.

**Change:**
- Removed `#analyst-panel` and the FVG legend (`.zone-key`) from `templates/workspace.html`.
- `static/analyst-panel.js`: `mount(el, deps)` became `createController(deps)`. The controller still owns the analysis state (run, stats, auto refresh, chart lines, stale result), but it renders no DOM. The dashboard below the charts is now its only view. The unused `esc` export was removed.
- `static/workspace.js` creates the controller without an element. The jump links are now only `[data-jump="analysis"]`.
- Dead CSS removed: `.analyst-*`, `.zone-key`, `.bull-swatch`, `.bear-swatch`, `.watchlist-rows { min-height: 140px }` (`workspace.css`), and `.analyst-mini`/`.analyst-open` (`analysis-dashboard.css`).
- Kept: the "Analysis ↓" button in the top bar, the footer link "Analysis ↓ scroll for the full read", and the collapsed "LOCAL notifications" feed (one line at the bottom). That feed is the only output of the opt-in "LOCAL FVG alerts" toggle.
- The **Watchlist** button already toggled `state.sidebar` (`aria-expanded`, `.sidebar-collapsed`, saved to localStorage), and still does. Its tooltip now reads "Show or hide the watchlist".

**Measured** (`before-*-sidebar*.json` vs `after-*-sidebar*.json`; row height 47 px; 866 Bybit markets):

| viewport | rows area before | rows area after | full rows visible before → after |
|---|---|---|---|
| 1600×1000 | 424 px | 638 px | 9 → 13 |
| 1280×720 | 140 px (sidebar scrolled as a whole) | 346 px (sidebar does not scroll, the list does) | 2 → 7 |

The sidebar height does not depend on the chart layout: 1, 4 and 6 panes gave identical numbers.

**Toggle, live:** expanded=true and grid 1316 px → click → `display:none`, grid 1600 px, saved `sidebar:false` → click → back to 1316 px. Same at 1280 (1018 ↔ 1280 px).

**Dashboard still works:** "Analysis ↓" scrolled the page to y=230, and "Analyze" in the dashboard posted `/api/agent/analysis`. Result: `status ok`, Bybit ETHUSDT 5m, long 33 %, verdict "Short · low confidence" (`after-dashboard-analysis.png`).

## 2. Liquidity heatmap

### Reproduction (before, `before-*.png`)
The API, leases and collector all worked. `/api/liquidity/watch` returned 200, samples arrived every 10 s with 0 gaps, and the indicator id survived the v5 migration (`indicatorEnabled.liquidity`). Canvas sizing and DPR were fine. The geometry the page computed (`pane.liquidityModel`) explains why nothing was visible:

| market | rows received | book span | auto step | row height | book on screen |
|---|---|---|---|---|---|
| Binance BTCUSDT 1m | 100 / 100 | 81628.9–81653.3 | 0.4 | **0.87 px** | 66 px band, 11 px wide, under the 80 px profile |
| Bybit BTCUSDT 4h | 200 / 200 | 81608.8–81669 | 0.8 | **0.04 px** | 3.7 px |
| Bybit ETHUSDT 15m | 200 / 200 | 2405.53–2409.80 | 0.06 | **0.26 px** | 20 px |

### Root cause
1. **Shallow book.** `LiquidityCollector` called `fetch_depth_snapshot(exchange, symbol)` with its default `depth=200`, and Binance has no 200 tier, so it rounds down to 100. That covers only ±0.015 % (Binance) to ±0.04 % (Bybit) of the BTC price. A raw snapshot taken the same minute: Binance 100 rows spanned 24 USD, while 1000 rows spanned 255 USD. Bybit 200 rows spanned 63 USD, while 1000 rows spanned 383 USD.
2. **Subpixel buckets.** `automaticStep` was `span / 80` in price units and ignored the price scale. A 60 USD book on a 4h chart (18.9 USD/px) gave 0.04 px rows. At opacity 0.3 those rows were invisible. Even on 1m they were below 1 px.
3. **Profile on top of the newest columns.** The current-book profile was always drawn at `width − 82`. That covers exactly the last ~80 px, where a fresh 10-minute heatmap lives (on 1m, the cells were at x 1167–1178 and the profile at 1108–1188).

Ruled out: WS paths (the collector polls REST depth), API errors, lease expiry, the v5 migration, drawing z-order, and the size of the dashboard scroll pane.

### Fix
- `liquidity_api.py`: the collector asks for `COLLECT_DEPTH = 1000` rows, the maximum on both Bybit and Binance.
- `static/liquidity-heatmap.js`:
  - The automatic step is `max(span/80, 3 px of price at the current scale)`, rounded up to 1/2/…/9×10ⁿ.
  - A manual step thinner than 1 px is raised to 1 px. The pane status says so ("raised to 1px minimum") and shows the bucket and the book's pixel extent.
  - The profile is drawn in the empty right-offset space after the last candle. It falls back to the old right-edge slot if there is less than 24 px of room.
- Aggregation is unchanged: bids floor, asks ceil, quantities are summed within one snapshot only, and gaps stay transparent.

### After (live)
| market | rows | step | min row | book on screen | cells over time |
|---|---|---|---|---|---|
| Binance BTCUSDT 1m (130 s) | 1000/1000 | 4 | 7.55 px | 677 px | 1863 → 2490, frames 34 → 39, latest 1792310442522 → 1792310556949 |
| Bybit ETHUSDT 1m (130 s) | 1000/1000 | 0.4 | — | 686 px | 697 → 1148, frames 18 → 29 |
| Binance BTCUSDT 5m | 1000/1000 | 8 | — | 128 px | 1489 → 1592 in 30 s |
| Binance BTCUSDT 15m | 1000/1000 | 10 | — | 110 px | 1403 → 1484 in 30 s |
| Bybit ETHUSDT 5m | 1000/1000 | 0.5 | — | 231–245 px | 63 → 248 in 30 s |
| Bybit BTCUSDT 4h | 1000/1000 | 60 | 3.02 px | 27 px | 69 cells, 9 profile rows |

Screenshots: `live-*-t000/t060/t120.png` and `after-*.png`. Timelines: `live-*.json`.

### Bands vs a raw depth snapshot (`live-*.json → compare`)
Method:
- Fetch raw REST depth (limit 1000), then a second one 1.3 s later.
- Bucket both with the page's step.
- Compare against the collected frame nearest in time, which the page rendered (Δt −2.6 s to +4.9 s).
- Also re-aggregate the stored SQLite payload of that frame.

Results:
- **Page bucket = stored frame bucket in 100 % of compared bands, on all 5 runs.** Bucket counts matched too: Binance 1m 31/31 bids and 35/35 asks; Bybit ETH 1m 36/36 and 42/42.
- The largest resting bands agree with raw:
  - Binance BTC 1m: asks 81744 page 52.642 / raw 52.615 (0.05 %); asks 81764 23.361 / 23.360; bids 81632 36.402 / 36.425 (0.06 %).
  - Bybit ETH 1m: bids 2396.4 2432.09 / 2432.09; asks 2418.0 2504.78 / 2504.78; asks 2418.4 2251.00 / 2251.00 (exact).
  - Binance BTC 5m: 81568 41.967 / 41.981; asks 81712 56.117 / 56.103.
- The larger differences are real book churn between the samples, not a rendering error. The raw-vs-raw check 1.3 s apart shows the same jumps. Examples: Binance 15m asks 81720 page 29.318, raw 58.667, raw+1.3 s 29.307. Bybit ETH 5m bids 2404 page 2198, raw 2845, raw+1.3 s 2086. Near-touch buckets move by 2–20 % within seconds.

## Tests
- `pytest`: **1663 passed**.
- `node --test tests/`: **224 tests, 218 pass, 6 skipped** (the browser tests). With `WORKSPACE_PLAYWRIGHT` (scratch Playwright, real Chrome): **224 pass, 0 fail**, including the liquidity browser test.
- `ruff 0.17.0` with the exact CI commands: all checks passed. `py_compile` OK.
- New or changed tests:
  - `liquidity_heatmap.test.cjs`: regression with the real Bybit BTC 4h scale and 200-row book. Rows must be at least 3 px, quantity is preserved, the manual step is raised to 1 px. The profile position is checked in both slots. Without the fix the regression fails: step 0.5 gives subpixel rows.
  - `test_liquidity_api.py::test_collector_requests_deep_books` checks that the collector requests depth 1000.
  - `analyst_panel*.test.cjs`: the controller has no DOM. The sidebar markup and CSS contain no analyst card, no `.zone-key` and no 140 px row floor, and both analysis entry points remain.

## Known limits
- On high timeframes the REST book (±0.25 % of BTC on Bybit, ±0.16 % on Binance) is still only ~27 px tall on a 4h chart. Deeper liquidity would need a WS diff book, which is not in scope.
- 1000-row snapshots are ~8 KB compressed (200 rows: ~1.7 KB). The 128 MB store cap therefore holds about 1.8 days per continuously watched symbol instead of 7. Binance REST weight per poll is 20 instead of 5, which is still far below the limits at one poll every 10 s.
- The FVG colour legend was removed together with the card. FVG colours are still explained by the FVG chip and its settings.
