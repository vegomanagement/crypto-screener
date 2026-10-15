# Indicator plugin architecture — plan

## Confirmed bug (real repro, not guesswork)

Pixel inspection of the live workspace (`fvg-current-1.png` → `fvg-zoom.png`) shows the amber FVG box's **left edge sits roughly one bar to the right of the candle that actually forms the gap** — a visible blank margin, not flush. This is the "не вплотную" complaint. Root cause to find and fix in `coordinateForTime`/`drawZones`: the box left edge must land on the exact same pixel x as the real candle body that created it (verify programmatically: `boxLeftPx === candleLeftPx` for the confirming candle, not just close visually). Right edge currently extends across the full visible chart width with no visual stopping point at the actual mitigation candle or latest bar — must terminate exactly at whichever is correct (mitigation candle if filled-but-still-partially-open is impossible since mitigated zones are dropped already; otherwise the latest closed candle). Fix with a precise coordinate test, not just a screenshot check.

## 1. Generic indicator plugin contract

One registry, each entry:

```js
{
  id, label, kind: 'line' | 'band' | 'boxes',
  defaultEnabled: boolean,
  settingsSchema: [{ key, label, type: 'number'|'select'|'color'|'boolean', min, max, step, options, default }, ...],
  compute(candles, settings) => for 'line': Array<{time, value}>; for 'band': {upper: [...], lower: [...]}; for 'boxes': zones array (FVG shape)
}
```

- `kind: 'line'` renders through the native Lightweight Charts line series (like today's EMA).
- `kind: 'boxes'` renders through the existing canvas overlay technique used for FVG (reused, bug-fixed).
- `kind: 'band'` is new: a continuous filled polygon between two curves across the full visible range, drawn with the same canvas overlay machinery as boxes (coordinate conversion reused), needed for Turtle Zone's upper/lower shaded bands.

EMA7/14/28/200, MA20 High/Low and FVG are migrated into this registry (not just the new Turtle Zone), so all three get consistent gear-icon settings panels:

- **FVG**: minimum gap size filter (percent of price, default 0 = any size), fill policy (wick touch vs candle close — both already documented as the project's two real choices, no invented third option), bullish/bearish colors, box opacity. Direction/creation/mitigation timestamps and math are unchanged — only presentation and an optional size filter.
- **EMA**: each of the four periods independently editable (reasonable bounds, e.g. 2–500) and independently colorable; disabling via the existing per-period toggle stays as is.
- **MA20 High/Low**: period (2–500) and price source (open/high/low/close) independently editable per line, plus colors.

## 2. Settings UI

One ⚙ button per indicator entry in the existing overlay toggle row, opening an anchored popover with inputs generated from `settingsSchema` (number steppers, select, native color input). Apply recomputes immediately; Reset restores the indicator's documented defaults; Escape/outside-click closes without discarding unapplied-but-valid edits mid-type. Settings persist in `localStorage`, versioned (bump schema with full fallback chain, same pattern as v1→v2→v3).

## 3. New indicator: Turtle Zone (ported from the user's Pine v6 script)

Independently reimplemented (not a Pine interpreter — JavaScript port of the published algorithm):

- `length` (default 200), `innerAmp` (default 5.6), `outerAmp` (default 9.6), price `source` (hlc3 default; open/high/low/close/hl2/ohlc4 selectable).
- True range on log prices: `max(logH-logL, |logH-logC1|, |logL-logC1|)` using the previous candle's close (first bar uses its own close, matching Pine's `nz(close[1], close)`).
- Ehlers two-pole Super Smoother applied independently to `log(source)` and to the log true range, using the exact coefficients from the script (`a1,c1,c2,c3` from `length`), seeded honestly from the first two real closed bars (no fabricated warm-up values; zone not drawn until the recursive filter has enough real history — same "no fake warm-up" rule as MA20/EMA).
- Bands: `exp(mean ± innerAmp*trMean)` and `exp(mean ± outerAmp*trMean)`; upper zone (red, low opacity) fills between inner/outer above mean, lower zone (green) fills between inner/outer below mean. Default disabled, like MA20.
- Documented clearly as an independent port that approximates — not guarantees bit-exact match to — the referenced Pine library's internal smoother.

## Constraints

- No backend changes; everything computes client-side from already-fetched closed candles, consistent with EMA/MA20.
- Existing FVG zone math (3-candle detection, mitigation, directions) is unchanged — only the rendering/coordinate bug and the new optional size filter.
- All 6 layouts, exchange dropdown, favorites, sort, live price line keep passing; add regression coverage for the fixed FVG coordinate alignment specifically (pixel-level assertion, not a screenshot-only check).
