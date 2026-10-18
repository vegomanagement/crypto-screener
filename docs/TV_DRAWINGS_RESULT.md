# Chart drawing tools v2 — result

Branch `claude/tv-drawings` (from `claude/scalping-phase2` @ 43307a5). This is our own implementation on the vendored
Lightweight Charts 4.1.3 (Apache-2.0). I did not copy third-party charting code, icons, CSS or text. The only outside
input was a list of drawing-tool *names*, used to decide which tools to build. That inventory file is not in the repository.

## What was built

**Layout.** The old `static/drawings.js` (545 lines) is now a thin entry point. The logic lives in UMD modules under
`static/drawings/`:

| module | role |
|---|---|
| `geometry.js` | exact price strings, time↔pixel mapper (integer `logicalToCoordinate` only, bar-index interpolation, future area), primitives (seg/poly/text) with rendering and hit-testing, magnet, RDP simplification, linear regression |
| `core.js` | tool registry, schema v2, lossless v1→v2 migration, store, per-interval visibility, undo/redo history, style templates, generic build/handles/hit-test/drag, z-order, level sync |
| `tools-lines.js` | trend line (extend L/R, Δprice / % / bars+duration / angle), ray, info line, extended line, trend angle, horizontal line/ray, vertical line, cross line; parallel channel (+midline), regression trend, flat top/bottom, disjoint channel |
| `tools-fib.js` | Fib retracement (configurable levels 0…4.236, labels with price, reverse, extend, band fill), trend-based Fib extension, Fib time zone, Fib channel |
| `tools-measure.js` | price range, date range, date & price range, temporary ruler, long and short position, anchored VWAP (±σ bands), fixed-range volume profile |
| `tools-shapes.js` | rectangle, rotated rectangle, circle, ellipse, triangle, path, polyline, arc, brush, highlighter, arrow, arrow marks up/down; text, note, price label, callout, flag mark |
| `tools-patterns.js` | XABCD, Cypher, ABCD (with ratios), head & shoulders, triangle pattern, three drives, Elliott impulse/correction/triangle/double/triple |
| `tools-advanced.js` (Tier 2) | pitchfork, Schiff, modified Schiff, inside pitchfork, pitchfan, Gann fan/box/square, Fib speed fan/arcs/circles/spiral/wedge, trend-based Fib time, cyclic lines, time cycles, sine line, projection, bars pattern, ghost feed, signpost, table |
| `toolbar-model.js` | 7 groups, last tool per group, favourites, magnet cycle, prefs v1→v2, keyboard shortcut map |
| `controller.js` | per-pane state, actions (each mutation is one undo step), pointer creation flows (click-click, press-drag-release, multi-point + Enter/double-click/Esc, free-hand), drag of handles/bodies, document keyboard for the active pane, rendering into the pane's existing DPR-aware overlay canvas |
| `ui.js`, `ui-panels.js`, `drawings.css` | left vertical toolbar with group flyouts and ★ favourites, favourites strip, floating mini-toolbar, properties dialog, object tree |

There are **76 tool types** (75 persistent plus the temporary ruler).

**Wiring into the shared files (kept minimal):**
- `static/workspace.js`: +3 lines in `drawingContext()`. They pass `interval` (for per-interval visibility) and the
  pane's footprint store (for exact volume profiles). Nothing else changed.
- `templates/workspace.html`: script and stylesheet tags for the new modules.

**Editing UX:**
- Mini-toolbar on the selected object: colour, width, line style, lock, clone, settings, delete.
- Properties dialog with tabs: Style, Text, Coordinates (UTC time and exact price per point), Visibility (seconds,
  minutes, hours, days, weeks), Options (per tool, including a Fib level editor) and Template (save, apply, set as
  default for new drawings, reset).
- Toolbar toggles: magnet off/weak (14 px)/strong, Ctrl/⌘ inverts it while drawing; stay in drawing mode; level mode;
  session lines; lock all; hide all.
- Object tree per pane: rename, hide, lock, z-order ▲▼, delete.
- Undo/redo, 150 steps per pane, cleared on symbol change.
- Keyboard shortcuts: Alt+T trend, Alt+H horizontal, Alt+V vertical, Alt+C cross, Alt+F Fib, Alt+R rectangle.
  Shortcuts read `event.code`, so macOS Option works. Esc cancels, Del/Backspace deletes, Ctrl/⌘+Z undoes,
  Ctrl/⌘+Shift+Z, Shift+Z or Ctrl+Y redo, Ctrl/⌘+D clones.

**Persistence:**
- Drawings are stored in `localStorage['screener.drawings.v2']` per `venue:SYMBOL`, shared by all intervals.
  Points are (time ms, exact decimal price string).
- On first read, v1 is migrated: `line` becomes `trend`, color/width move into `style`, and ids, points, locks, level
  links and hidden flags are kept. The v1 key stays untouched as a backup.
- Horizontal lines and rays still sync with `static/levels.js`. Our own level writes no longer trigger a mid-action
  reload, and undo/redo re-creates or removes the linked levels.

**Computed tools:**
- Regression trend: least squares over the real candles between the anchors, ± N·σ of the residuals.
- Anchored VWAP: cumulative typical price × volume from the anchor candle.
- Volume profile: when every bar in the range has `complete` footprint coverage, it bins the exact footprint
  volume-at-price and is labelled **"exact from footprint trades"**. Otherwise it spreads each candle's volume
  uniformly over its high–low range and is labelled **"approximated from candles"**. It shows POC and a value area.
- Long/short position: R:R, quantity = account × risk% ÷ |entry − stop| (optional quantity step), % and ticks, P&L.

## Tests (all real runs)

- `node --test tests/`: **161 tests, 155 pass, 0 fail, 6 skipped**. The 6 skips are in suites that existed before this
  branch.
- Drawing suites: `drawings.test.cjs` (11), `drawings_tools.test.cjs` (11), `drawings_controller.test.cjs` (6) and
  `drawings_advanced.test.cjs` (6).
- What they cover:
  - Every registered tool: builds, has a handle, is hit at that handle, misses far points, and is storable.
  - Line, channel and shape geometry and hit tolerances.
  - Fib maths: 0.618 of 100→200 = 138.2; extension 1.618 = 311.8; time zones 2,4,6,8,12,18,28.
  - Regression against a hand-computed fit: closes 1,3,2,5,4 give slope 0.8, intercept 1.4, σ = √0.72.
  - AVWAP fixture: 10, 32/3, 59/6 and the σ.
  - Volume profile: candle split, exact levels, POC / value area, and footprint gating (partial coverage → approximation).
  - R:R and quantity: 1000 account at 1% with a 5-point stop gives qty 2 and R:R 2; quantity step 0.3 gives 1.8.
  - v1→v2 migration (lossless, no re-migration), store, templates, per-interval visibility groups, history limit.
  - Toolbar model and prefs migration, shortcut map.
  - Controller driven by synthetic pointer and keyboard events: creation flows, drag + undo, Del + undo, clone,
    lock all, level link restored by undo, and a 200-object draw pass.
  - Tier-2 maths: pitchfork origins and median, Gann 1×1, φ spiral, trend-based Fib time, cycles, sine, bars pattern,
    ghost feed, table.
- `pytest`: **1285 passed**.
- `ruff check --select E4,E7,E9,F …` exactly as in `ci.yml`: all checks passed. `py_compile` OK.

## Live verification

Setup: own Flask-only launcher on :5093; headless Chrome on CDP port 9566 at 1500×900, DPR 2; Binance BTCUSDT.
Scripts are in `docs/evidence/tv-drawings/harness/`, and results with screenshots are in `docs/evidence/tv-drawings/`.

- **Every tool drawn through real CDP mouse/keyboard input** (`live_draw_all_15m.json`, `live_draw_all_1h.json`):
  - Tool selection: Alt shortcut for the 6 shortcut tools; for all others, a real click on the group's flyout button
    and then on the menu item. The active tool was confirmed after each selection.
  - Drawing: clicks, press-drag-release, multi-point + Enter, or a free-hand drag. Text tools got their text through
    the dialog.
  - Result: 75/75 persistent tools stored on 15m and again on 1h; 5/5 text tools stored the typed text; the ruler
    stayed temporary as intended.
  - After a real click on the other interval (15m→1h and 1h→15m), storage was identical and all 75 still rendered.
    After a page reload, storage was identical and 75 rendered.
- **Editing, 20/20 checks** (`live_edit.json`):
  - long/short from the favourites strip; select by click; mini-toolbar.
  - Properties dialog: risk % typed as 2; "Minutes" unchecked, so the object is hidden on 15m (1 of 2 rendered).
  - Alt+T trend; body drag; Ctrl+Z restores the exact points; Shift+Z redoes; Del then ⌘+Z.
  - Level mode + Alt+H creates the shared level at the same exact price.
  - A template saved and set as default applies to the next trend (#ff00aa, 3 px).
  - Object tree: rename, z-order down, hide/show.
  - Real click on "1h": drawings identical; the minutes-hidden object appears (4 → 5 rendered).
  - Reload: identical, 5 rendered.
- **v1→v2 migration in the page** (`live_migration_perf.json`): 3 seeded v1 drawings (line/rect/hray) came back
  lossless. Ids, exact prices, colours, widths and locks match; the v1 key is still present; 3 rendered.
- **Performance with 200 objects**, chart panned on every animation frame for 240 frames:
  - Frame interval: mean 16.67 ms, p95 16.7 ms, max 16.8 ms (60 fps held).
  - Drawing pass `draw()`: mean 2.9 ms, p95 3.4 ms, max 7.7 ms.
  - The pane's own scheduler redrew the overlay on 120 of 239 frames during the programmatic pan.
- **Magnet** (`live_magnet_vbp.json`): the toolbar button cycles off → weak → strong. Alt+H clicked 9 px above a candle
  high stored exactly that high (`81659.5`).
- **Exact volume profile from the footprint:** footprint switched on, 4 complete 15m bars, real clicks.
  - Label: "VbP 4 bars · Σ 2.72K · POC 81482.04 · exact from footprint trades".
  - Footprint volume total 2715.99 vs kline volume 2715.63 for the same bars (0.013 % apart).

**Screenshots:**
- Toolbar flyout open for each group: `flyout_<group>.png`.
- Clean per-group galleries: `group_<group>.png`.
- All tools on each interval and after switching: `all_tools_15m.png`, `all_tools_1h.png`, `*_then_*.png`.
- `long_short_position.png`, `mini_toolbar.png`.
- Properties dialog tabs: `properties_style|options|coords|visibility|template.png`.
- `object_tree.png`, `edit_15m.png`, `edit_1h.png`, `migration_v1_to_v2.png`, `perf_200_objects.png`,
  `vbp_footprint.png`.

## Known issues / not verified

- **Tier-2 semantics are my own definitions**, not checked against any reference:
  - "Inside pitchfork" draws the tines at ±0.25/0.5/0.75/1 of the B–C half-width.
  - Gann square uses a 0.25-step grid, diagonals and two quarter-arcs.
  - Fib arcs are centred on B; Fib circles are centred on A.
  - Ghost feed synthesises candles along the path with a fixed wick rule.
- **Only mouse input was tested.** Touch and pen were not.
- **Headless Chrome only.** No headed or GPU run, so frame timing on real hardware is unmeasured.
- **Default tick size is the chart's price precision** (0.01 on BTCUSDT; the Binance tick is 0.1). The real tick can be
  set in the position tool's options.
- **Undo history is per pane, in memory only.** It is not persisted and is cleared on symbol change.
- **The left toolbar overlays the leftmost ~45 px of the plot.** It starts below the legend.
- **Drawings are clipped to the plot.** Labels near the left edge can be cut off.
- **`localStorage` is per browser.** There is no server sync.
- **The pre-existing v1 toolbar CSS (`.draw-toolbar` in `workspace.css`) is now unused but still there.** I left it so
  the shared file stays untouched.
