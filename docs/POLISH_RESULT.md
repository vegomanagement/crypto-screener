# Polish: sessions parity and DST, venue ticks, persistent liquidations, cross-venue opens

Branch `claude/polish`, created from `claude/integration` at c5b3dd8. Commits are local only and nothing is pushed.
For the live checks, Flask ran on its own on :5096 from this worktree (`docs/evidence/polish/scripts/polish-launch.py`).
Headless Chrome ran on CDP :9569. Every number below comes from those runs, and the evidence files are in
`docs/evidence/polish/`. Live data is from Saturday 17 Oct 2026, roughly 19:00–22:15 UTC.

## 1. Market Sessions (`static/indicators/sessions.js`)

### a) Opening range parity with Pine `f_render_oprange`
- `i_o_minutes := max(i_o_minutes, timeframe.multiplier + 1)`. On a 15m chart N becomes 16, and on 1h it becomes 61.
- The range is fixed on the bar whose open time is in `(start + N − 1 bar, start + N]`.
  The range covers every session bar up to and including that fixing bar.
  This means the bar that opens exactly at start + N is included.
  - 5m chart with N = 15: 4 bars, 08:00–08:15.
  - 15m chart: 2 bars, 08:00 and 08:15.
  - 1h chart: 2 bars, 08:00 and 09:00.
  - Seconds charts: every bar with time ≤ start + 15m. Pine's `f_get_time_by_bar` is `multiplier × 60 s`.
- If no bar exists at start + N (a data gap), Pine never fixes the range. The model marks it `missing` and draws nothing.
- Targets R1/R2/S1/S2 come from the average range of previous sessions, as before. The breakout flags
  (first `crossover(high, R)` or `crossunder(low, S)` per level) are now scanned only on bars after the fixing bar.
- New, and a deliberate departure from Pine: if the loaded history starts after a session's start,
  that session gets no opening range (`truncated`).
  Pine would treat the first chart bar as the session start, and the result would not be the session's first N minutes.
- Live check against Binance 15m klines (`sessions-verify.txt`): 9 opening ranges in the GMT+0 and DST cases.
  All have 2 bars and N = 16, and top and bottom equal the venue klines exactly.
  Examples:
  - London 17 Oct 08:00 UTC: 81724.8 / 81672.4.
  - New York (America/New_York) 16 Oct 12:00 UTC: 82616.9 / 82080.4.
- Bybit and Hyperliquid opening ranges were checked the same way (see §2):
  - Bybit 15m: 3 ranges, Bybit 1h: 10 ranges, Hyperliquid 15m: 3 ranges. All equal the venue klines.
- Screenshot `sessions-or-15m-gmt0.png` shows the 2-bar box zoomed onto London on 17 Oct.

### b) DST-aware time zones per session
- There is a new per-session `Time zone` setting. The default is `General timezone` (`inherit`), and the General
  default stays `GMT+0`, so the script's windows are unchanged unless the user opts in.
- Options:
  - `Europe/London`, `America/New_York`, `Asia/Tokyo`, `Australia/Sydney`.
  - All of the script's fixed `GMT±N` offsets.
  - The General timezone accepts the same city zones.
- The offsets are computed in the browser with `Intl.DateTimeFormat#formatToParts`, with no library.
  The start and end of each window are converted separately, so a window that spans a DST change is 1 hour shorter or longer in UTC.
  Ambiguous local times resolve to the earlier instant, and skipped local times land after the gap, matching Python `zoneinfo` with `fold=0`.
- Unit tests (`tests/indicator_groups.test.cjs`, expected values from Python `zoneinfo`):
  - London 29 Mar: `0000-0900` runs 00:00 → 08:00 UTC (8 h).
  - London 25 Oct: 23:00 on 24 Oct → 09:00 UTC (10 h). `0800` local is 08:00 UTC on 27 Mar and 07:00 UTC on 30 Mar.
  - New York 8 Mar: 05:00 → 13:00 UTC.
  - New York 1 Nov: 04:00 → 14:00 UTC. 01:30 on 1 Nov resolves to 05:30 UTC, and 02:30 on 8 Mar to 07:30 UTC.
  - Sydney `2000-0500`, which crosses midnight: 4→5 Apr runs 09:00 → 19:00 UTC (10 h), and 3→4 Oct runs 10:00 → 18:00 UTC (8 h).
  - Tokyo `0900` is 00:00 UTC.
  - A Sydney `0700-1600` window starting on the previous UTC day is labelled with the local weekday.
- Live checks:
  - Binance 15m, all four city zones: `sessions-dst-15m.png`.
  - Binance 4h with Sydney, 51 instances: `sessions-sydney-dst-4h.png`.
    The window is 10:00–19:00 UTC through 2 Oct and 09:00–18:00 UTC from 4 Oct.
    On the transition night (3 Oct) it is 10:00 → 18:00 UTC, i.e. 20:00 AEST → 05:00 AEDT.
  - `sessions_verify.py` converted every live instance back with `zoneinfo`. The local window is constant for every session
    (London 08:00–17:00, New York 08:00–17:00, Tokyo 09:00–15:00, Sydney 20:00–05:00). 0 failures.
- London and New York did not change offset inside the loaded live history (300 bars), so their DST days are covered by unit tests only.

## 2. Opens / Key levels and Sessions on Bybit and Hyperliquid

- `venue_crosscheck.py` compares the live `/ui` models (`venues-live-models.json`) with each venue's own REST klines:
  - Bybit: `/v5/market/kline` D/W/M/15/60.
  - Hyperliquid: `candleSnapshot`.
  - Binance: `/fapi/v1/klines`.
- Result after the fix (`venues-crosscheck.json`), 0 failures:

| case | opens and previous H/L | sessions (incl. opening ranges) |
|---|---|---|
| Bybit BTCUSDT 15m | 11/11 | 11/11 (3 ORs) |
| Bybit BTCUSDT 1h | 11/11 | 30/30 (10 ORs) |
| Hyperliquid BTC 15m | 11/11 | 11/11 (3 ORs) |
| Binance BTCUSDT 15m | 11/11 | 11/11 (3 ORs) |

- Bybit: weekly klines start Monday 00:00 UTC and `M` starts on the calendar 1st. Both matched with no change:
  W open 83733.1 (Mon 12 Oct), PWH/PWL 86986.5 / 80350.1, M 83573.8.
- **Bug found and fixed (Hyperliquid week start):** Hyperliquid's native `1w` candles open on **Thursday** 00:00 UTC.
  The server rejected them as off-anchor, so the `/ui` weekly fetch failed and Key levels showed
  "W: Selected exchange data unavailable…" and did not draw PWH/PWL (`venues-crosscheck-before-hl-week-fix.json`).
  `/api/chart` W on Hyperliquid now builds Monday weeks from 7 complete Hyperliquid daily candles.
  Incomplete weeks are dropped and nothing is padded. The response carries the warning
  "Hyperliquid 1w candles start Thursday 00:00 UTC; Monday-based weeks are aggregated from 7 complete daily candles".
  - Live result: PWH/PWL 86990 / 80351 for the week of Mon 5 Oct, equal to the max/min of Hyperliquid's own daily klines.
    The W open, 83730 on Mon 12 Oct, is Hyperliquid's daily open on that Monday.
  - Hyperliquid's own weekly candles would not match this check, because they open on Thursdays. This is recorded in `native_1w_open_weekdays: ["Thu"]`.
- Hyperliquid monthly: the native `1M` is 30-day buckets (live opens on 07-06, 08-05, 09-04, 10-04), not calendar months.
  The server already builds calendar `M` from daily candles, so M, 3M and Y opens are never taken from `1M`.
  PMH/PML 87471 / 74903 for September match the daily-kline reference.
- The forming W bar from `/api/chart/live` is still unavailable on Hyperliquid for the same Thursday reason.
  The current W open comes from the Monday daily candle, which matched.
- Screenshots: `venue-bybit-BTCUSDT-15m.png`, `venue-bybit-BTCUSDT-60m.png`, `venue-hyperliquid-BTC-15m.png`.

## 3. Real tick sizes in drawings
- The server already exposed venue metadata through `/api/scalp/instrument`, which `/scalp` also uses:
  - Binance `PRICE_FILTER.tickSize`.
  - Bybit `priceFilter.tickSize`.
  - Hyperliquid `szDecimals`.
  It is cached for 1 h on the server.
- Each `/ui` pane now fetches it once per market, deduplicated per page. `drawingContext()` passes `tickSize` and
  `tickSource` to the drawing controller, and `view.tickSize` feeds every tick count (position tool, price and date ranges).
- Tick priority: the tool's own `Tick size` option first, then the venue tick, then `10^-precision`.
- Hyperliquid's tick depends on price (≤ 5 significant figures, ≤ 6 − szDecimals decimals). The rule is the same as
  `footprint.hyperliquid_tick`, and the unit tests use values cross-checked against it.
- Live (`live-tick-toolbar.json`, `position-tick-*.png`): for a long position at +200 / −100 venue ticks, the labels read
  "200 ticks" and "100 ticks" on all three venues.
  - Binance BTCUSDT: tick 0.1 (`binance tickSize`). Target 81529.5 is +20.0 from entry 81509.5.
    Before this change the chart precision (0.01) would have counted that as 2000 ticks.
  - Bybit BTCUSDT: tick 0.1. Venue REST reports `tickSize "0.10"`.
  - Hyperliquid BTC: tick 1 (`szDecimals` 5 at about 81.5k).

## 4. Persistent liquidation store
- `LiquidationStore(path=…)` mirrors events and coverage spans to SQLite.
  - It uses `liq_events` and `liq_coverage` tables, WAL mode and `journal_size_limit` 4 MB.
  - `PositioningService` uses `LIQUIDATIONS_DB_PATH`, or `liquidations.db` next to `DB_PATH`.
    An empty `LIQUIDATIONS_DB_PATH` means in memory only. The store refuses `signals.db`.
- Retention is 24 h on every write and on load. The per-market cap (default 20 000) also applies to the file.
- Honest coverage: the collector loop sends a heartbeat for the open spans, written at most every 10 s.
  On restart, a span that was still open is closed at its last heartbeat, not at the restart time.
  A span with no heartbeat claims nothing.
- Tests: `tests/test_liquidations_persist.py`, 7 tests:
  - restart survival
  - crash closes the span at the heartbeat
  - no heartbeat means no coverage
  - retention on write and on load
  - size cap on the file
  - collector writes heartbeats
  - default path and the `signals.db` guard
- Live run, about 18 min with 10 Binance and 10 Bybit symbols leased:
  - 3 real Binance liquidations were stored (LINKUSDT 144.09, SUIUSDT 714.7 and DOGEUSDT 630.49 USD, all shorts).
    Bybit had no events for the leased symbols in that window.
  - The server was killed without a clean shutdown at about 1792264775 s and restarted 64 s later.
  - With `watch=0`, so the collector was not restarted, the same three events came back with identical
    price, qty, ts and notional (`liq-before-restart.json` and `liq-after-restart.json`).
  - Spans were closed at their last heartbeat. For example, Binance BTCUSDT was [1792263659522, 1792264763279] and
    Bybit BTCUSDT was [1792263659582, 1792264771937]. `connected: false`, and the dead time was not claimed.

## 5. EMA legend stuck on "warming up"
- Cause: `renderForming()` calls `updateLegend(this.forming)`, but `emaValues` only has closed bars.
  A 4h pane almost always has a forming bar, so every EMA read "· warming up" even with 300 bars
  (`legend-4h-before.json`: EMA 7/14/21/200 all "warming up").
- Fix: for the forming bar the legend now shows a provisional EMA computed from the last closed EMA and the forming close,
  marked "(live)" with a tooltip. The plotted line still uses closed bars only.
  "warming up" now appears only before the first `period` closed bars.
- Live check, Binance 4h (`legend-4h-after.json`): "EMA 7 81,568.872 (live)".
  This equals α·81506 + (1−α)·81589.829 with α = 0.25, recomputed independently. EMA 14, 21 and 200 also match.

## 6. Cleanup
- Removed the `.draw-toolbar` and `.draw-selection` styles from `workspace.css`. `.pane-draw-toggle` is still used by `ui.js`.
- Live DOM check (`live-tick-toolbar.json`, `toolbar-layout4.png`):
  - Each pane has exactly one `.dw-toolbar` (vertical, 42×679 px at the left), one `.dw-favs` strip and zero `.draw-toolbar`.
  - This holds in the 1-pane layout on three venues and in a 4-pane layout.
  - No leftover horizontal toolbar exists.
- `indicator-extras.js`:
  - Removed the old split definitions (`turtle-upper/lower`, `prev-day/week-high/low`, `session-asia/london/ny`)
    and their helpers `computePreviousLevel` and `computeSession`.
  - grep showed /ui no longer registered them, `/scalp` does not load the module, and only their own unit tests used them.
  - Kept: the VWAP definitions, `computeTurtleZone` (used by `groups.js`), and the save migration of the old ids in `groups.js`.
  - Removed `LEGACY_SPLIT_IDS` from `workspace.js` and updated `docs/INDICATOR_EXTRAS.md`.

## Tests (as in `.github/workflows/ci.yml`)
- `py_compile` of the six modules: OK.
- `ruff check --select E4,E7,E9,F decision.py llm_agents.py chart.py tracking.py tests/` and the `screener.py`/`webhook_server.py` line: all checks passed (ruff 0.17.0).
- `pytest`: 1419 passed, 0 failed (new: 7 in `test_liquidations_persist.py` and 1 Hyperliquid-week test in `test_chart_data.py`).
- `node --test tests/*.cjs`: 205 tests, 199 pass, 0 fail, 6 skipped. The skipped tests are the existing Playwright browser tests, which skip when Playwright is not installed.
  New or changed tests: Pine opening-range rule, DST zones, truncated session, legend EMA, venue tick (×2), and removal of the split definitions
  (7 obsolete tests deleted from `indicator_extras.test.cjs`).

## Not verified / limits
- London and New York DST days were not seen live (no transition in the loaded window), so unit tests are the only evidence.
  Sydney's 4 Oct change was seen live on 4h.
- Opening-range targets and breakout flags were checked only against the Pine rule in unit tests, not against TradingView output.
- Hyperliquid's weekly forming bar (`/api/chart/live?interval=W`) is still unavailable because of the Thursday anchor. The UI does not need it.
- Bybit liquidations: no event arrived for the leased symbols during the live run, so Bybit event persistence is covered by unit tests only.
  Coverage persistence was seen live.
- The provisional "(live)" EMA in the legend changes with every tick until the bar closes. The line itself does not repaint.
