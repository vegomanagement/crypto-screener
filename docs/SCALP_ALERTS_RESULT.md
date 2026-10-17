# Scalping phase 2 · Track A — alerts and signal levels on `/scalp` (result)

Branch `claude/scalp-alerts` (from `claude/scalping` @ fb7d555). View only: a level is a marker and an
alert is a message. Nothing can place, edit or cancel an order. No new pip or npm dependencies.

## What was built

| Part | File | Notes |
|---|---|---|
| Shared levels contract | `static/levels.js` | Exactly as in `SCALPING_PHASE2_PLAN.md`: key `screener.levels.v1`, `{version:1, levels}`, `load/list/add/update/remove/clear/subscribe/crossed`. Exact decimals via BigInt. Unknown fields and non-positive or non-decimal prices are rejected. 500-level cap (oldest triggered first, then oldest created). `subscribe` fires on local writes and on `storage` events. If storage is missing or a write fails, it falls back to memory and sets `lastError`, which the footer shows. Extra helpers for consumers: `createStore`, `fromTicks`, `compareDecimal`, `distanceAtLeast`, `DEFAULT_COLORS`, `TRIGGERED_COLOR`. |
| Alert detectors (pure) | `static/scalp-alerts.js` | `evaluateSignals` (trigger / re-arm ≥ 1 tick away), `triggerPatch`, `RateLimiter` (3 s per (type, price)), `VolumeAlerts` (appear / decrease / gone / large print), `AlertLog` (≤ 200, newest first, oldest **volume** alerts evicted first), `activeRows`, `tradePriceString` (venue's exact price string). |
| Level drawing | `static/scalp-render.js` | `drawLevels`, `levelY` and `levelAtY`. Lines span clusters, feed and ladder. Signal levels are dashed, fired one-shots dotted grey, and each level gets a tag (`◆ signal`, `◇ fired`, `— level`, or the note). |
| Level input | `static/scalp-levels-ui.js` | Alt+click sets or removes a simple level, Shift+Alt+click sets or removes a signal level, drag moves a level (a moved signal re-arms), double-click opens the editor (type, colour, width 1–4, repeat, note ≤ 80, re-arm, delete). Shift+L clears the column's levels (plain `l` does nothing). Level prices are built from the row's tick index (`fromTicks`), never from float pixels. |
| Triggering + feedback | `static/scalp-alerts-ui.js` | Every live trade is checked against armed signal levels, once per (venue, symbol) even when several columns show it. Before firing, the level is re-read from storage so a level already fired by another tab is skipped. On a trigger: a WebAudio beep (a different pitch per alert type), a 5 s header blink, then the header stays highlighted until clicked, an optional `Notification` (permission is requested only from the drawer button), and a log row. |
| Volume alerts per column | settings → "Volume alerts (off by default)" | Appear ≥ X $ within Y % of mid; decrease ≥ Z % from peak, or gone; large print ≥ X $ (single, or same side within N ms). Each has its own sound pitch. The first synced book after a start or resync only seeds the tracker. "Pulled" is claimed only when no trade at that tick on the consuming side explains the drop. The verdict waits 400 ms for trades, because depth and trades arrive on separate sockets. |
| Notifications drawer | `static/scalp-alerts-panel.js`, `templates/scalp.html`, `static/scalp.css` | Opened with 🔔 Alerts (badge shows the count). Tabs: Active (armed signals, plus repeat levels waiting to re-arm) and Triggered. Columns: time UTC, venue, symbol, source, price, type. Delete one row or all. Clicking a row centres the first matching column on that price (autocenter paused until `C`). Dock left or right. Sound toggle. Persisted in `screener.scalp.alerts.v1` and `screener.scalp.alertprefs.v1`. |

## Tests (all run locally)

- `node --test tests/*.cjs` → **111 pass, 0 fail**. New: `tests/levels.test.cjs` (8), `tests/scalp_alerts.test.cjs` (10), and 2 added to `tests/scalp.test.cjs`. They cover:
  - cross logic: touches, jumps in both directions, the no-previous-price case, the float trap `0.09999999999999999`;
  - exact decimals and validation;
  - corrupt or foreign-version storage, storage events and unsubscribe, the 500 cap and its eviction order, write failure;
  - appear seeding, a moving window over an old level, threshold flapping;
  - traded vs pulled vs partly traded, a trade arriving late after depth, untracking below the threshold;
  - print aggregation and the 3 s rate limit;
  - log cap and eviction, Active rows, level drawing and hit-testing, settings sanitising, the Shift+L hotkey.
- `pytest` → **1271 passed**. `tests/test_scalp_routes.py` now checks the new script order.
- `ruff check --select E4,E7,E9,F …` exactly as in `ci.yml` → clean.

## Live checks (real Chrome 155 headless, real Binance USDⓈ-M BTCUSDT, local server on :5088)

Scripts: `docs/evidence/scalping-phase2/live_alerts.cjs` and `live_storage.cjs`. Raw output: `live-alerts.json`, `live-storage.json`. The numbers below come from the final run (2026-10-17 16:21:52–16:23:45 UTC).

1. **Signal 1 tick above the best ask, set by a real Shift+Alt+click.**
   - Book when clicked: bid 81278.2, ask 81278.3, tick 0.1. The level was stored as `"81278.4"`, which is the expected price.
   - It fired **11.3 s later** on aggTrade `a=3488513210` at `81278.40`; the previous trade was `81278.30`.
   - **Binance REST `/fapi/v1/aggTrades` agrees**: same id, same price, the previous id has the same price, and `crossed()` on the REST prices is `true`.
   - After firing, the level was `armed:false` and recoloured `#5a6273`.
   - Alert time minus venue trade time was 389 ms. That includes network delay and any clock difference, so it is not a latency measurement.
   - The earlier run gave the same result: level 81312.2 fired after 66 s, REST matched, and it was 123 ms after the trade time.
2. **Repeat signal 1 tick below the bid, set through the double-click editor.** The editor opened; repeat and the note "live repeat" were saved. The level fired twice in the window: `81278.20 after 81277.90`, then `81278.00 after 81278.20`, and re-armed in between (`rearmed: 2`).
3. **Feedback.**
   - Header class: `alert-blink` right after the trigger, `alert-hold` after 5.2 s, cleared by clicking the header.
   - `beeps: 3, beepsBlocked: 0`, AudioContext `running`.
   - Notification permission `granted` (granted through CDP, then the drawer button was clicked); `notifications: 3`.
   - Clicking the Triggered row set `centerRow` to 812784, which equals the expected row of 81278.4.
4. **Volume alerts, 90 s window, thresholds lowered so events happen** (appear ≥ $500k within 0.2 %, decrease ≥ 60 %, print ≥ $100k):
   - Logged: 96 appear, 79 decrease·pulled, 14 gone·pulled, 4 decrease·partly traded, 4 prints.
   - The rate limiter suppressed 305.
   - All 4 print alerts were re-checked against REST aggTrades at the same millisecond. Each has a Binance trade at the same price with p·q equal to the alert's $ (307 138, 109 910, 165 678, 199 984).
5. **Shared store across documents.** A level added through `levels.js` in another same-origin document appeared in the `/scalp` column through the real `storage` event, and removing it there removed it here. `/ui` still loads.
6. **Page health.** No console errors and no exceptions on `/scalp`. The only 404 was `/favicon.ico`, in the cross-document check; that is pre-existing and unrelated. Paint time p50 0.9 ms, p95 1.4 ms, max 10.6 ms, over 1 200 frames.

Screenshots in `docs/evidence/scalping-phase2/`: `scalp-levels-armed.png`, `scalp-signal-blink.png`, `scalp-signal-hold-panel.png`, `scalp-alerts-triggered-tab.png`, `scalp-alerts-active-tab.png`.

A bug found live and fixed (commit 649cce2): the first run produced 528 volume alerts in 90 s.
- A large level that had been resting for a while was reported as "appeared" when the price moved and the Y % window reached it.
- The remainder of a level that fell below the threshold kept firing "decrease".
- That flood pushed the signal hits out of the 200-row log.

Now large levels are known across the whole covered book (with hysteresis), a level below the threshold is no longer tracked, and the log evicts volume alerts before signals.

## Not verified / known limits (stated plainly)

- **Audio was not heard.** Headless Chrome reports the AudioContext `running` and the oscillators started, but nobody listened. On a real page the first beep needs a click or key press beforehand; until then the log row says "sound blocked (no click yet)".
- **Notifications in a normal (non-headless) Chrome:** the `Notification` constructor ran 3 times without error in headless mode. Whether it shows on the desktop was not observed.
- **"Traded" vs "pulled" has no ground truth.** Public data does not say whether a resting order was cancelled or filled. The attribution uses trades at the same tick on the consuming side within 3 s, plus a 400 ms grace. Live, the result was mostly "pulled" and a few "partly traded"; a pure "traded" verdict was seen only in unit tests. Binance depth diffs are net per 100 ms, so new orders at the same price hide fills. Treat the label as a heuristic.
- **"Appear" is noisy at low thresholds** on BTCUSDT (about 1 per second at $500k / 0.2 %), because large orders are reposted at nearby prices. The defaults ($1M / 0.5 %) were not measured live. All volume alerts are off by default.
- **Bybit and Hyperliquid were not checked live** for alerts. They use the same code path; their trade price strings come from `p` / `px`.
- **Two tabs both evaluating the same level:** a re-read from storage before firing prevents most double fires. Two tabs reading at the same instant can still both fire, because localStorage has no compare-and-swap.
- **Firing across a stream gap:** a signal can fire on the first trade after a disconnect if the price jumped over the level while the stream was down. Such alerts are labelled "across a stream gap". The level's cross was then not observed trade by trade.
- **Rows coarser than 1 tick** (scale ×2…×20 or %): a click sets the level at the row's lowest tick price, and drawing groups levels by row. Live checks used ×1 tick only.
- Not run: a drag on the live page (unit-tested geometry only), the Track B `/ui` drawing of these levels (Track B's job), and mobile or touch input (Alt-click needs a keyboard).
