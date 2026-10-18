# Indicator extras: pure library and integration contract

`static/indicator-extras.js` exports a CommonJS API in Node and
`globalThis.WorkspaceIndicatorExtras` in browsers. It has no DOM, WebSocket,
network, persistence, or backend dependencies. It never changes candles or
settings. Callers must supply **only closed candles**, sorted by increasing
integer Unix seconds, with finite positive and coherent `open/high/low/close`
and finite nonnegative `volume`. The library cannot determine whether an
exchange candle is closed without timeframe/current-time context, and does not
pretend to do so.

> **/ui since the indicator-groups change:** the split definitions `turtle-upper`/`turtle-lower`,
> `prev-day-*`/`prev-week-*` and `session-asia`/`-london`/`-ny` are still exported by this library
> (and still tested), but `/ui` no longer registers them. They are replaced by the grouped
> indicators *Turtle Zone* (uses `computeTurtleZone`), *Key levels / Opens* and *Market Sessions*
> — see docs/INDICATOR_GROUPS_RESULT.md. VWAP daily/weekly are unchanged.

## Definitions

All 11 entries are disabled by default. Each has `settingsSchema`, `compute`,
and `warnings(candles, settings)`; there is no separate custom UI.

| ID | Kind | Meaning |
| --- | --- | --- |
| `turtle-upper` | band | Red area between upper inner and outer curves |
| `turtle-lower` | band | Green area between lower outer and inner curves |
| `vwap-daily` | line | UTC midnight-anchored, HLC3 volume-weighted average |
| `vwap-weekly` | line | Monday 00:00 UTC-anchored, HLC3 volume-weighted average |
| `prev-day-high` / `prev-day-low` | line | Prior complete UTC day levels |
| `prev-week-high` / `prev-week-low` | line | Prior complete Monday–Sunday UTC week levels |
| `session-asia` | band | Configurable 09:00–18:00 Asia/Tokyo window |
| `session-london` | band | Configurable 08:00–17:00 Europe/London window |
| `session-ny` | band | Configurable 08:00–17:00 America/New_York window |

Session windows are **product defaults**, not claims of universal exchange
opening hours. They include weekends when candles exist; they do not model
holidays, auction schedules, or a particular futures exchange's trading hours.

## Integration requirements (owned by the workspace integrator)

1. Load `indicator-extras.js` before `workspace.js`; append
   `WorkspaceIndicatorExtras.definitions` to the registry.
2. Generate settings controls/defaults from the schema as for built-in plugins.
   Turtle defaults are length 200, inner 5.6, outer 9.6, source HLC3, red/green
   colors, opacity 0.10. Sources: HLC3, open, high, low, close, HL2, OHLC4.
   `innerBoundary` metadata selects lower edge for the upper zone and upper
   edge for the lower zone; stroke that inner edge. `showOuter` defaults false
   and can independently reveal the outer edge without changing the fill.
3. Catch compute errors per indicator. Display `def.warnings(candles, settings)`
   (or `getWarnings(id, candles, settings)`) instead of fabricating lines when
   the history/timeframe is unsupported. Warnings are returned, never logged.
4. Pass `intervalSeconds` in compute/warnings settings from the actual pane
   timeframe. This is a non-UI hint. Without it the minimum observed timestamp
   difference is inferred; one candle has no inferable duration. **Inference
   cannot distinguish regularly missing bars from genuinely coarser data.**
   A correct explicit duration is necessary to reliably detect those gaps.
5. Preserve whitespace `{time}` points in line output: they mean unavailable,
   not zero. The four previous-level definitions additionally expose
   `stepLine: true`; select the chart library's *WithSteps* line type so levels
   remain horizontal until the UTC rollover rather than drawing diagonals.
6. **Band renderer extension required:** Turtle output remains the existing
   `{upper:[{time,value}], lower:[{time,value}]}` shape, with candle-center
   coordinates. Session output is instead:

   ```js
   {
     upper: [], lower: [],
     timeCoordinates: 'edges',
     segments: [
       { upper: [{time: start, value: high}, {time: end, value: high}],
         lower: [{time: start, value: low}, {time: end, value: low}] }
     ]
   }
   ```

   Draw **each segment independently** using boundary/edge coordinates (not
   bar-center snapping). The deliberately empty top-level arrays prevent the
   old renderer from joining overnight gaps into a single continuous polygon.
   Segments are clipped to actual candle coverage and the last supplied closed
   candle's close edge; missing candles split rectangles. Their price extent
   is the minimum low/maximum high of the supplied chart candles. This is
   background shading, not a future-looking trade signal or an autoscale line.
7. Apply `settings.color` **and** `settings.opacity` when filling all bands,
   e.g. save the context, multiply `globalAlpha` by opacity, set fillStyle to
   color, fill, restore. Existing `fillStyle = settings.color` alone makes
   hex-color defaults opaque. Session default opacity is 0.08. No custom
   session/FVG box adapter is needed: sessions are segmented bands.

The pure library intentionally does not make these shared renderer/UI edits.

## Turtle Zone provenance and limits

The official TradingView library listing identifies `twoPole`; the exact
published **KevanoTrades/Ehlers_Super_Smoother/2**, not a guessed current
GitHub implementation, was retrieved through TradingView's own source
endpoint, which reported version **2.0**.[1][2] The original copyright line is
`© KevanoTrades`; the library listing now uses the author name FatTailAlgo.[1][2]
The original credits John Ehlers and thanks Alex Orekhov (everget).[2]

The official Turtle Zone source endpoint reported version **5.0** and imports
`KevanoTrades/Ehlers_Super_Smoother/2`. It contains the provided source selection,
logarithmic true range/mean, amplitudes 5.6/9.6, hidden mean, and exponentiated
bounds; its copyright is `© declarative`.[3][4]

Exact library initialization verified from source:[2]

- `sourceReplace1 = nz(source[1], source)`;
- `sourceReplace2 = nz(source[2], sourceReplace1)`;
- `SSF = c1*(source+sourceReplace1)/2 + c2*nz(SSF[1],sourceReplace1)
  + c3*nz(SSF[2],sourceReplace2)`;
- `a1 = exp(-sqrt(2)*pi/length)`, `c2 = 2*a1*cos(sqrt(2)*pi/length)`,
  `c3 = -a1²`, `c1 = 1-c2-c3`.

The JavaScript implementation uses those replacement seeds. Log true range
includes jumps from the **previous candle close**, using the current close on
the first input candle. The mean is computed internally, never registered as
an extra line. Output starts only after `max(200, length)` **real** input bars;
there is no backfill or replicated candle warmup. This warmup is a product
safety policy, not a claim that Pine suppresses its first 199 outputs.

Validation rejects lookbacks outside integer 1–1000, negative/nonfinite
amplitudes, outer < inner, unknown sources, invalid OHLCV, and exponential
overflow/underflow. A recursive super smoother can overshoot a nonnegative TR
below zero: material negative filtered TR triggers a descriptive error rather
than silently swapping curves. Tiny negative roundoff within `1e-12` is
normalized to zero. That safety policy differs from blindly drawing inverted
Pine bounds.

**No bit-exact TradingView equivalence is claimed.** The source algorithm and
initialization were verified, but no paired exported TradingView numeric
fixture was available. JavaScript/Pine floating-point operations and seed
history can differ; a finite loaded history is not an infinite-history match.
HLC averages divide terms before adding to avoid overflow on large prices.
Both original MPL-2.0 notices are preserved in the module and root `NOTICE`;
keep this file's source and notices available with distributions.

For reproducibility, SHA-256 of each retrieved JSON `source` string encoded as
UTF-8 (including its original line endings), not of the enclosing JSON:

- ESS v2.0: `ac5dbcaee2148aac0f29f39a065f0a0ff66c34df37598c57c9f3501f61ff8be5`
- Turtle v5.0: `d6c582a661688f80271e97e4e50a49485e6c5b1c6bc1d35bbfb7e6015df1cd90`

## Calendar integrity and coarse timeframes

VWAP uses HLC3 and actual supplied volume, not a bar count or fixed 6/42-bar
anchor. Zero-volume bars are whitespace until positive volume exists, then
retain the existing weighted average. The accumulator scales volume and uses
an online weighted mean to avoid multiplying large prices and volumes.
This is **candle-derived VWAP**, not a reconstruction of unavailable individual
trades; changing source timeframe may change it.

A first group beginning mid-day/week has no known prefix and is omitted.
A missing bar invalidates VWAP from that gap through the end of that period;
valid earlier prefix values are retained. Processing resumes at the next
observed exact UTC anchor. Prior high/low needs the immediately preceding
**fully covered, contiguous** calendar period; missing/partial prior periods
produce whitespace rather than stale levels. Current-period highs never leak
into current-period prior levels. Wholly absent periods are also warned about.

Known source duration must divide the anchor period and fit inside its edges.
Weekly candles cannot yield daily VWAP/previous-day levels and return empty
arrays with warnings. Native UTC daily candles support previous-day levels;
native Monday weekly candles support previous-week levels. Daily bars can
build weekly VWAP/levels when complete. Intraday sessions require source
bars shorter than one day; daily/weekly candles yield empty shading and a
coarse-timeframe warning. These are deliberate data limitations, not reasons
to invent lower-timeframe OHLC or volume.

Session boundaries use `Intl.DateTimeFormat` and IANA zones; London and New
York follow their **own** DST transition dates. Start is inclusive, end
exclusive. End <= start means overnight; equal hours are rejected, not treated
as an ambiguous full day. Repeated local DST hours choose the earliest matching
instant; nonexistent local boundary hours raise a warning/error rather than
inventing a time. Runtime IANA timezone data must be available.

## Additional pure exports and tests

- `computeTurtleZone(candles, settings)` → `{upper: band, lower: band}`.
- `twoPole(values, length)`, `logTrueRange(candles)`, `validateCandles(candles)`.
- `computeVWAP(candles, {anchor:'day'|'week', intervalSeconds?})`.
- Removed (polish branch, see docs/POLISH_RESULT.md): the old split definitions
  `turtle-upper/lower`, `prev-day/week-high/low`, `session-asia/london/ny` and
  their helpers `computePreviousLevel` / `computeSession`. /ui had stopped
  registering them; the grouped Turtle Zone, Key levels / Opens and Market
  Sessions replace them, and the save migration in `static/indicators/groups.js`
  still maps the old ids. The sections above describe the removed code.
- `computeemaBias(candles, periods=[7,14,28])` (alias `computeEMABias`) →
  `'up' | 'down' | 'mixed' | 'insufficient'`. SMA-seeded close EMAs, at least
  the slowest period of real history, strictly increasing integer periods
  1–1000, strict fast/medium/slow order with a relative `1e-12` equality
  tolerance. No panel, feed, or current candle fetching is done here.
- `getWarnings(id, candles, settings)` (alias `warningsFor`) → string array.

Run `node --test tests/indicator_extras.test.cjs`. Feature slices were exercised
RED→GREEN: Turtle, anchored VWAP, previous levels, session geometry/DST, EMA
bias, registry/warnings, stepped-level metadata, and absent-period warning.
Tests also cover source-replacement seeds, previous-close log TR jumps,
constant-price convergence, warmup, invalid data/settings, numerical overflow,
UTC week boundaries, partial history/gaps, native D/W candles, huge volume,
nonmutation, browser-global loading, overnight sessions, and session gaps.

## Sources

[1] https://www.tradingview.com/script/u47JU3ZR-Ehlers-Super-Smoother
[2] https://pine-facade.tradingview.com/pine-facade/get/PUB%3B91ec3faffc0a4ba1ac3e54ff867c9a80/2
[3] https://www.tradingview.com/script/7oTHYcD9-Turtle-Zone
[4] https://pine-facade.tradingview.com/pine-facade/get/PUB%3B61c766ebaf574b909435dfa4544bb0c4/5
