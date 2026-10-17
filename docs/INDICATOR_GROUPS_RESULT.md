# Grouped indicators in /ui: result

Branch `claude/indicator-groups` (from `claude/scalping-phase2` @ 43307a5). Commits are local only: nothing was pushed, merged or deployed.

## What changed

The overlay toolbar used to have a separate checkbox for each part of an indicator. It now has **one chip (toggle) plus one ⚙ settings popover per indicator**, like TradingView. The popover is schema-driven and now has section headings plus text and session-time fields.

| Indicator (id) | Replaces | Settings |
|---|---|---|
| **EMA 7/14/21** (`ema-ribbon`, on by default) | `ema7`, `ema14`, `ema28` | 3 lines. Each line has its own visibility, period (defaults **7/14/21**), colour and width. The chip label follows the periods, e.g. "EMA 10/14/21". |
| **EMA 200** (`ema200`) | the same id | Kept as a separate indicator, as asked. |
| **MA 20 H/L** (`ma-channel`) | `ma20high`, `ma20low` | Period, width, and a source, colour and visibility for each line. Optional fill between the lines (colour and opacity). |
| **Turtle Zone 200** (`turtle-zone`) | `turtle-upper`, `turtle-lower` | Length, inner/outer amplitude, source and width. Upper and lower zone each have visibility and colour. Optional outer boundary. Optional fill (inner→outer) with opacity. The maths is still `computeTurtleZone` (MPL port, see NOTICE). |
| **Key levels / Opens** (`opens`) | `prev-day-high/low`, `prev-week-high/low` | Port of sbtnc "Daily Weekly Monthly Yearly Opens" v7.0, plus PDH/PDL/PWH/PWL and the new **PMH/PML**. Each sub-level has its own toggle and colour (details below). |
| **Market Sessions** (`sessions`) | `session-asia`, `session-london`, `session-ny` | Port of boitoki "FX Market Sessions" v54 (details below). |

Unchanged and still separate: FVG, VWAP daily/weekly, Liquidity, the footprint (pane "FP"), drawings, agent levels, seconds intervals and LOCAL FVG alerts.

Code layout. New logic lives in new UMD modules to keep `workspace.js` merge conflicts small:
- `static/indicators/groups.js`: ribbon, channel and turtle models, migration, canvas fill/polyline helpers. Project code.
- `static/indicators/opens.js`: **MPL-2.0**, JS port of sbtnc v7.0.
- `static/indicators/sessions.js`: **MPL-2.0**, JS port of boitoki v54.
- `static/indicators/indicators.css`: popover sections and scrolling.
- `static/workspace.js`: about +137/−84 lines, confined to the indicator definitions, storage/migration, `applyIndicators`, overlay drawing, legend and settings popover. The pane header and right sidebar are not touched.
- `templates/workspace.html`: the six hard-coded EMA/MA chips were removed, and the module scripts and CSS added.

## Settings migration (storage v4 → v5)

`STORAGE_KEY` is now `crypto-screener.workspace.v5`. The v4/v3/v2/v1 keys are still read and are run through `WorkspaceIndicatorGroups.migrateLegacyState`:

- **EMA:** if any of `ema.7/14/28` was saved, the ribbon is enabled when at least one of them was on.
  - If a user had **EMA 28 enabled, the ribbon is enabled**.
  - Custom periods are kept: e.g. a saved EMA 28 period of 34 becomes line 3 = 34. Only the old default 28 becomes the new default 21.
  - Colours are kept.
  - Line visibility follows the old checkboxes. A user who had only EMA 28 gets the ribbon with lines 1 and 2 hidden. If every old line was off, the ribbon is off but its lines keep their default visibility.
- `ema.200` → `ema200`.
- **MA 20 H/L channel:** `ma20hl.high/low` → `ma-channel` enabled if either was on. Period, sources and colours are kept, and the line visibility follows the old flags.
- **Turtle Zone:** `turtle-upper/lower` → `turtle-zone` enabled if either was on. Length, amplitudes, source and opacity come from the old upper (or lower) entry, with the colours of each zone and their visibility.
- **Key levels / Opens:**
  - Any `prev-*` enabled → `opens` enabled, with PDH/PDL/PWH/PWL set to their old flags.
  - Missing old ids count as **off**, because the split ids defaulted to off.
  - The new PMH/PML and the period opens keep the script defaults (D, W, M, Y on). So a user who only had PDH now also sees the opens and the PMH/PML levels; each one can be turned off in ⚙.
- **Market Sessions:**
  - Any `session-*` enabled → `sessions` enabled. London → #1, New York → #2, Asia → #3 (Tokyo); missing ids count as off.
  - **The windows reset to the script's GMT+0 defaults**: London 0800-1700, New York 1300-2200, Tokyo 0000-0900. The old shading used DST-aware Europe/London 08–17, America/New_York 08–17 and Asia/Tokyo 09–18 local time, so a migrated user's boxes move. This is stated in the migration note shown in the footer.
- The old ids are deleted from `indicatorEnabled`/`indicatorSettings`, and the old `ema`/`ma20hl` objects are dropped.
- Live check (`migration-live.json`): a real page was given a v4 save with only EMA 28 at period 34, MA20 High, PDH and London on. After reload it showed version 5; the ribbon, channel, opens and sessions on; ribbon `period3 = 34` with lines 1 and 2 hidden; sessions `[London on, NY off, Tokyo off, Sydney off]`.

## Port details

### Opens (sbtnc v7.0) → `static/indicators/opens.js`

What was ported:
- 5 slots, with the script defaults: #1 D green, #2 W orange, #3 M red, #4 3M purple (**off**), #5 Y (12M) blue. Each slot has enable, timeframe, colour and lookback (1–500 opens).
- `isVisible`: a slot shows only if its timeframe is larger than the chart's.
- The current open runs to `last bar + offset` (default 20 bars). Labels are pushed `+4 bars` for each earlier level within ATR(10)/2, the script's `padding()`. The ATR is Wilder RMA, as in `ta.atr`.
- **Tails**: the current open extends left, dotted. **Projections** (heads): previous opens extend right to `offset − 8` bars at 60% transparency.
- Labels (D/W/M/3M/6M/Y/H/4H), with the price optionally added.

Time anchoring (UTC; crypto is 24/7):
- D = 00:00 UTC.
- W = Monday 00:00 UTC.
- M / 3M / 6M = calendar boundaries.
- Y = 1 January.
- 1h and 4h use epoch multiples.

Data sources:
- `/api/chart?interval=D&limit=500`, `W&limit=500` and `M&limit=60`. The backend builds M from at most 1000 daily bars, so about 32 months are available.
- The venue's forming D/W bar from `/api/chart/live`.
- Results are cached for 60 s per market and timeframe, and only the needed timeframes are fetched.
- An open is taken **only from a candle that starts exactly at the period start**: daily first, then native W/M, then the chart's own candles. A missing period is **not drawn**, and the pane shows a warning (e.g. "Y open of the current period is unavailable").

Previous highs/lows:
- PDH/PDL come from the closed 1d candle of the previous UTC day, PWH/PWL from the closed W candle, PMH/PML from the closed M candle.
- Each one is toggleable, with its own colour. "Prev H/L history (periods)" also draws earlier periods (default 1).
- Drawn dashed from the current period start to the right offset.

Differences from the script:
- "Discover Prices" and "Extended Hours" are not ported: no RTH/ETH sessions on crypto.
- Timeframe choices are 1h, 4h, D, W, M, 3M, 6M and Y. The script's 5s…12h choices are reduced to 1h and 4h.
- Lookback is limited by the data fetched: about 500 days, 500 weeks and ~32 months. So Y lookback covers about 3 years.
- Labels that would leave the plot (the `/ui` chart has much less right margin than TradingView) are pinned inside the right edge above their line.

### Market Sessions (boitoki v54) → `static/indicators/sessions.js`

The AwesomeColor and Utilities imports were re-implemented locally: TradingView/monokai colours, `get_day`, and history trimming.

Ported:
- 4 sessions with the script defaults: London 0800-1700 #66d9ef, New York 1300-2200 #fd971f, Tokyo 0000-0900 #ae81ff (all on), Sydney 2000-0500 #fb71a3 (off). Each has name, colour, `HHMM-HHMM` window (an end before the start crosses midnight), extend, opening range and Fibonacci toggles.
- Timezone: the script's fixed **GMT offsets** (GMT-11 … GMT+12, including +330, +430 and +530). **No DST**, exactly like the script's input; DST-aware zones were deliberately not offered.
- A bar belongs to a session when its open time is inside `[start, end)`, which is what Pine's `time(tf, session, tz)` does. The session high/low is the max/min over those bars.
- Box styles **Box / Background / Hamburger / Sandwich**, with border style and width, BG colour, and **Dots** (open/close dotted band with ● and ◉ markers).
- "Include the latest bar in the session end". An in-progress session is drawn over its projected full window, like the script. **Extend** reaches the right edge and keeps only the latest instance, as the script does.
- Labels: Top/Bottom and Inside/Outside position; Name, Day and Price (high − low). **Pips are replaced by Range %**, i.e. (high − low) / low.
- **Opening range**:
  - Covers the first N minutes (default 15). It is fixed only after those bars have closed, and drawn as a yellow 50% box plus dotted top/bottom lines with price labels.
  - Targets: R1/R2/S1/S2 = midpoint ± avg·0.786/1.272·0.5, where avg is the average of up to 50 previous completed ranges of that session. If there is no previous session, there are no targets, as in Pine.
  - **Breakout flag** "×" on the first `ta.crossover(high, R)` / `ta.crossunder(low, S)` for each level, plus target lines (with fills), target boxes, "current session only", the current-close marker, and a colour mode (blue/red or session colour).
- **Fibonacci** 0.5 (width 2), 0.628 and 0.382. 0.628 is exactly as in the source.
- **History** count (default 10) per session.
- Sessions show only on intraday intervals. **On daily and higher they are hidden**, and the pane warns "Sessions are intraday-only and are hidden on daily and higher intervals" (live: `regression-smoke.json`, `chart-BTCUSDT-D-sessions-hidden.png`).

Not ported:
- Bar colouring / plotcandle, the session-candle style, the range info table, the opening-range statistics table, alertconditions and visualised alerts, and the >500-bars limit message.
- **Opening-range bar rule**: the port uses "bars whose open time is before start + N minutes, at least the first bar". Pine also includes the bar that opens exactly at start + N, and forces N ≥ chart timeframe + 1 minute. So on a 15m chart the port's opening range is one bar, while Pine's is two.

## Tests

- `tests/indicator_groups.test.cjs`: 18 pure-function tests.
  - EMA ribbon: SMA seeding, no warm-up values, invalid periods.
  - MA channel, and Turtle zone ordering and warm-up warning.
  - Migration from the old ids, including a custom EMA 28 period and missing ids.
  - UTC anchoring: Monday weeks, Sunday → previous week, calendar months, quarters, halves, years; prev/next across year boundaries.
  - Opens: exact-start rule, lookback, partial current period with a forming daily bar, year open not fabricated without a January candle; visibility rule and needed timeframes.
  - Previous D/W/M high/low from the closed HTF candle only; label padding and edge pinning; HTF loader TTL and forming bar.
  - Sessions: parsing of session strings and GMT offsets, London window high/low, Sydney 2000-0500 across midnight, GMT-5 and GMT+530 shifts, history count, extend, hidden on D.
  - Opening range: 3 bars of 5m, targets against the formula, breakout times against the data, single bar on 1h, pending state.
  - Fibonacci and label text; Box / Background / extend renderer.
- `tests/workspace_unit.test.cjs`:
  - The settings harness moved to v5/ema200.
  - New: `readState` v4 → v5 migration through the real code, and **the overlay list renders exactly one toggle and one ⚙ per indicator**, in the order `ema-ribbon, ema200, ma-channel, turtle-zone, vwap-daily, vwap-weekly, opens, sessions, liquidity`. The template no longer contains the legacy checkboxes.
  - The Playwright browser regressions were updated to the new ids and the v5 key, as was `liquidity_heatmap.test.cjs`.
- Results:
  - `node --test tests/*.cjs`: **150 pass, 0 fail, 6 skipped** (the browser tests).
  - With `WORKSPACE_PLAYWRIGHT` set (scratch Playwright, real Chrome): **156 pass, 0 fail**. On base 43307a5 the same browser tests also all passed.
  - `pytest`: **1285 passed**.
  - `ruff check --select E4,E7,E9,F decision.py llm_agents.py chart.py tracking.py tests/` and `ruff check --select E4,E7,E9,F screener.py --ignore E701,E702 webhook_server.py`: **all checks passed**.

## Live checks (2026-10-17, ~17:30–17:55 UTC)

Setup: own Flask launcher on :5091 (`scripts/indicator-groups-launch.py`), headless Chrome on CDP :9564 with its own profile, Binance USDⓈ-M perpetuals. All five grouped indicators were on: channel fill on, London opening range with targets and breakouts, opens price labels. Reference values come from `fapi.binance.com/fapi/v1/klines` (`scripts/ig_crosscheck.py` → `crosscheck.json`, `scripts/ig_ema_check.py` → `ema-crosscheck.json`).

**Opens and previous highs/lows**: 10 of 10 current levels matched the Binance klines exactly in every case (BTCUSDT 15m, BTCUSDT 1h, ETHUSDT 5m).

| Level | BTCUSDT /ui | Binance kline | ETHUSDT /ui | Binance |
|---|---|---|---|---|
| D open (17 Oct) | 81578.3 | 1d 1792195200 open 81578.3 | 2422.49 | 2422.49 |
| W open (12 Oct) | 83723.1 | 1w 1791763200 open 83723.1 | 2535.66 | 2535.66 |
| M open (1 Oct) | 83576.8 | 1M 1790812800 open 83576.8 | 2684.68 | 2684.68 |
| Y open (1 Jan 2026) | 87608.3 | 1M 1767225600 open 87608.3 | 2970.4 | 2970.4 |
| PDH / PDL (16 Oct) | 82900.5 / 80780 | 1d 1792108800 high/low | 2493.48 / 2392.14 | same |
| PWH / PWL (5–11 Oct) | 86976.1 / 80344.8 | 1w 1791158400 high/low | 2737.61 / 2405.07 | same |
| PMH / PML (Sep) | 87385.1 / 74909.4 | 1M 1788220800 high/low | 2806.76 / 2355.12 | same |

**Session highs/lows**: the max/min of the Binance intraday klines inside each window matched every instance on the chart, including the first/last bar times.

| Case | Sessions matched | Examples |
|---|---|---|
| BTCUSDT 15m | 11/11 | London 17 Oct 08–17: 36 bars, H 81724.8, L 81157.1. Tokyo 17 Oct: H 81733, L 81373.3. New York 17 Oct (in progress, 19 bars): H 81575.5, L 81149.8. |
| BTCUSDT 1h | 30/30 | Same windows: London 9 bars, H 81724.8, L 81157.1. |
| ETHUSDT 5m | 5/5 | London 17 Oct: 108 bars, H 2440.88, L 2397.11. New York 16 Oct: H 2432.81, L 2392.14. |

- ETHUSDT 5m London opening range: 3 bars to 08:10, top 2434.77, btm 2432.75. Targets R1 2435.4558, R2 2436.8166, S1 2432.0642, S2 2430.7034. Breakouts: R1 at 1792227300 (2435.62), R2 at 1792230600, S1 at 1792231800, S2 at 1792239000.
- The targets and breakouts were checked only by the unit test formula, not against TradingView.
- **EMA 7/14/21**: on each case, recomputed independently from REST closes over the same 300 bars; the difference was 0.0 (e.g. BTCUSDT 15m: 81280.0108 / 81340.7277 / 81384.1859).

**Regression smoke** (`regression-smoke.json`, no page exceptions):
- **BTCUSDT D**: sessions hidden with the warning; opens show only W, M and Y; PWH/PWL/PMH/PML drawn; 10 FVG zones.
- **5s seconds interval**: trade-built, 150 bars (Binance aggTrades backfill plus live). Grouped indicators keep working, with the same D/W/M/Y opens and previous levels as the minute charts.
- **1m with footprint on**: the footprint loaded real trades (after about 25 s: "61 complete / 0 partial bars, venue kline check 54 exact / 6 differ"). The drawings toolbar is present (`draw-toolbar`, `draw-tool`). The LOCAL FVG alerts toggle worked; event generation is covered by the existing browser test, which passes.

## Evidence (`docs/evidence/indicator-groups/`)

- `overlay-list.png`: the toolbar reads `FVG | EMA 7/14/21 | EMA 200 | MA 20 H/L | Turtle Zone 200 | VWAP daily (UTC) | VWAP weekly (UTC) | Key levels / Opens | Market Sessions | Liquidity | LOCAL FVG alerts`.
- Settings popovers:
  - `settings-ema-ribbon.png`, `settings-ma-channel.png`, `settings-turtle-zone.png`.
  - `settings-opens-1..3.png` (scrolled pages: Open #1–#5, Style, Previous highs / lows).
  - `settings-sessions-1..3.png` (General, Session #1–#4, Box, Labels, Opening range, Fibonacci levels).
- Chart screenshots: `chart-BTCUSDT-15m.png`, `chart-BTCUSDT-60m.png`, `chart-ETHUSDT-5m.png`, `chart-BTCUSDT-D-sessions-hidden.png`, `chart-BTCUSDT-1m-footprint.png`.
- Data and reports: `live-models.json` (models read from the live page), `crosscheck.json`, `ema-crosscheck.json`, `regression-smoke.json`, `migration-live.json`.
- `scripts/`: launcher, CDP client and run/check scripts, to reproduce the above.

## Licensing

- `static/indicators/opens.js` and `static/indicators/sessions.js` are **MPL-2.0**. Each carries a header naming the original author, script, version and URL, and says it is a JS port.
- The `.pine` sources are kept unmodified, with their headers, in `third_party/tradingview/`.
- The MPL-2.0 text is at `third_party/tradingview/LICENSE-MPL-2.0`, fetched from https://www.mozilla.org/media/MPL/2.0/index.txt (373 lines).
- `NOTICE` has entries for both ports. The rest of the repo keeps its existing terms.

## Not verified / known limitations

- These are ports, not a claim of bit-exact or pixel-exact equality with TradingView. Nothing was compared against a TradingView chart.
- Only Binance was checked live. Bybit and Hyperliquid opens and previous levels use the same code path (`/api/chart` D/W/M plus forming D/W) but were not cross-checked.
- The opening-range breakout/target values were checked against the formula in unit tests only. The live ETH breakouts above are reported as observed, not cross-checked externally.
- Sessions use fixed GMT offsets without DST, as the script does. Users who want "London local time" must switch offsets for summer/winter themselves.
- Pre-existing, unchanged: while a forming bar is shown, the legend says "EMA … · warming up", because EMAs exist only for closed bars (`renderForming` passes the forming bar to `updateLegend`). This applies to EMA 200 and the ribbon alike.
- On narrow panes, session labels and opens labels can overlap each other or the opening-range price labels. Labels are not collision-managed beyond the script's ATR padding and the right-edge pinning.
- `static/indicator-extras.js` still exports the old split definitions (Turtle upper/lower, prev-*, session-*) for its own tests and API. `/ui` simply no longer registers them.
- Merge with `claude/hermes-analyst` (75ec933 at check time) was only simulated with `git merge-tree --write-tree`. It exited 0, i.e. no textual conflicts. A real merge and the combined test suite were not run.
