# Wyckoff phases + order-flow signal study — result

Branch `claude/wyckoff-signals` (from `claude/integration` @ 2705802). Data: Binance USDT-M,
all checks on 2026-10-18. Every number below comes from
`docs/evidence/wyckoff-signals/` (`results.json`, `tables.md`, `footprint_sample.json`,
`live-crosscheck.txt`) and is reproduced with `python scripts/backtest_signals.py run` (~90 s
once the data is cached) and `... footprint --days 14`.

## Bottom line

**No candidate signal has a robust out-of-sample edge.** 0 of 35 out-of-sample hypotheses
(132 variants tried in total) passed. On the ±1 ATR rule every chosen variant scores between
45 % and 52.5 % out of sample, i.e. the same coin flip as the confluence baseline (49.8 %).
Before fees most expectancies are within ±0.04 R of zero; after Binance taker fees
(0.05 % per side) every one is negative, from −0.05 R (1h value-area re-entry) to −0.64 R (5m).
So nothing was wired in as a trade signal and `SPLIT_CONFIG` is unchanged. The features ship
as descriptive context, labelled "no proven edge", with their measured stats.

## Part A — Wyckoff (deterministic)

`wyckoff.py` + `wyckoff_rules.py` read the chart's closed candles (plus the footprint delta when
it covers bars) and return the schematic, the phase A–E, the TR, creek/ice, the events and
what would confirm or invalidate the read. Distribution runs the same rules on the
price-mirrored series, so the two schematics are exactly symmetric.

| event | rule (accumulation; distribution is the mirror) |
|---|---|
| SC / BC | rv ≥ 2 (volume / mean of the previous 20 bars), range ≥ 1.2 ATR, lowest low of 30 bars after a ≥ 4 ATR decline |
| PS / PSY | last bar with rv ≥ 1.5 at a local low above the climax low, ≤ 40 bars before it |
| AR | highest high after the climax (never the climax bar), confirmed by a close giving back 35 % of the rally within 25 bars; TR height ≥ 1.5 ATR |
| ST | retest within 30 % of the TR height of the climax low, close above it |
| Spring / UTAD | low below support by > 0.05 ATR, close back above within 2 bars (after an ST) |
| Test | higher low near support after price first rose 30 % of the height |
| SOS / SOW | first close above the creek (AR high) |
| LPS / LPSY (BU) | pullback to the creek holding above it |
| Markup / Markdown (phase E) | close one TR height beyond the creek, or 12 bars held beyond it after the LPS |
| invalidation | 3 closes below support, or one close more than max(0.3·height, 1 ATR) below; after the SOS a close back under the TR midpoint |

Phases: A = climax (+AR), B = after the ST, C = after the Spring, D = after the SOS, E = markup.
Nothing is forced: no climax followed by a range → "No clear structure / trending"; a broken
attempt → `status: unclear` with the bar and rule that broke it. Every event carries bar time,
`known_at` (the close at which its rule could fire), price, the rule text, confidence 0–1,
relative volume, spread in ATR, close position in the bar and delta ratio when available.

Surfaces: dashboard section "Wyckoff" (below the metric cards), toggle "Wyckoff on chart"
(TR box, creek/ice line, event markers on the active pane, `static/indicators/wyckoff.js`;
workspace.js wiring is two pane methods + one dashboard dep), `GET /api/agent/structure`
(no LLM, 30 s cache, never starts the footprint collector), and `snapshot.wyckoff` for Hermes
(prompt: may cite `wyckoff.*`, strength ≤ 2, never invent a structure when unclear).

## Part B — candidate signals and the backtest

Definitions (long side; short = mirror) are in the `signal_features.py` docstring: sweep &
reclaim of the prior N-bar low; the same with positive bar delta; failed auction (≥ 60 % lower
tail at a new N-bar low, close in the top 35 %); CVD divergence (new N-bar closing low while
Σdelta ≥ τ·Σvolume); absorption (after a 1.5 ATR decline: rv ≥ R, body ≤ 0.3 ATR,
delta/volume ≤ −0.1, close position ≥ 0.4); delta exhaustion; session-VWAP 2σ re-entry;
value-area re-entry (profile of 120 bars, 40 bins, 70 % VA; with/without delta); Wyckoff
Spring/UTAD and SOS/SOW; funding extremes (last settled rate in the 5 %/10 % tail of the
previous 270 settlements, contrarian); optional 4h trend filter (EMA 50 of completed 4h bars and
its slope); a control that enters both sides every 12 bars.

Method: events only (12-bar cooldown per signal), entry at the signal bar's close, ATR14 of that
bar. Rules: ±1 ATR within 12 bars (as the analyst), and stop 1 ATR / target 1.5 or 2 ATR within
48 bars (stop first if both hit in one bar; timeout marked to the close), expectancy in R net of
2 × 0.05 % fee. Delta = kline taker-buy volume (field 9) → exact per-bar taker delta, but no
price levels. Split per series by time: first 70 % in-sample (thresholds and the HTF filter
chosen there, IS n ≥ 300), last 30 % out-of-sample. Pass = OOS n ≥ 100, Bonferroni
(α = 0.05/35, z = 3.19) lower CI of net expectancy > 0, and positive in ≥ 2 of 3 symbols.
No look-ahead: `tests/test_signal_features.py` replaces every bar after t with garbage and
asserts identical events, HTF bias and Wyckoff events up to t.

Data: BTC/ETH 2020-01-01 → 2026-10-18, SOL 2020-09-14 → 2026-10-18; 5m 714,816 / 639,276 bars,
15m 238,272 / 213,092, 1h 59,568 / 53,273. OOS starts 2024-10-03 (BTC, ETH) and 2024-12-21 (SOL).

### Out-of-sample, IS-chosen variant (1.5R, net) — full table in `tables.md`

| tf | signal | OOS n | ±1 ATR hit (decided) [95 %] | E[R] net [95 %] | E[R] gross | fee R |
|---|---|---:|---|---|---:|---:|
| 1h | va_reentry | 1033 | 52.5 % [49.3–55.6] | −0.047 [−0.123, +0.029] | +0.084 | 0.13 |
| 1h | vwap_reentry k=2 | 1782 | 52.0 % [49.7–54.4] | −0.070 [−0.127, −0.012] | +0.067 | 0.14 |
| 1h | va_reentry_delta | 927 | 52.1 % [48.8–55.4] | −0.077 [−0.157, +0.003] | +0.054 | 0.13 |
| 1h | funding_extreme q=5 % · htf | 176 | 51.0 % [43.2–58.7] | −0.119 [−0.301, +0.063] | +0.023 | 0.14 |
| 1h | sweep_reclaim N=50 | 1217 | 49.6 % [46.7–52.4] | −0.132 [−0.201, −0.063] | −0.006 | 0.13 |
| 1h | failed_auction N=20 | 1030 | 51.1 % [47.9–54.2] | −0.140 [−0.215, −0.065] | −0.007 | 0.13 |
| 1h | sweep_delta N=20 | 1139 | 49.2 % [46.2–52.2] | −0.156 [−0.227, −0.085] | −0.023 | 0.13 |
| 1h | cvd_divergence N=20 | 1062 | 47.5 % [44.4–50.6] | −0.158 [−0.231, −0.084] | −0.020 | 0.14 |
| 1h | exhaustion | 422 | 47.7 % [42.9–52.5] | −0.225 [−0.339, −0.110] | −0.094 | 0.13 |
| 1h | wyckoff_sos | 355 | 49.9 % [44.5–55.2] | −0.229 [−0.355, −0.104] | −0.091 | 0.14 |
| 1h | wyckoff_spring | 261 | 49.8 % [43.7–55.9] | −0.236 [−0.382, −0.090] | −0.100 | 0.14 |
| 1h | absorption | 45 | 37.8 % [25.1–52.4] | −0.364 [−0.708, −0.021] | −0.222 | 0.14 |
| 1h | control (both sides / 12 bars) | 3868 | 49.5 % [47.9–51.1] | −0.133 [−0.171, −0.094] | +0.003 | 0.14 |
| 15m | best: vwap_reentry k=2.5 | 1682 | 49.9 % [47.4–52.3] | −0.280 [−0.339, −0.221] | +0.012 | 0.29 |
| 15m | wyckoff_spring | 1045 | 50.0 % [47.0–53.1] | −0.335 [−0.410, −0.260] | −0.042 | 0.29 |
| 15m | control | 34458 | 50.0 % [49.5–50.5] | −0.305 [−0.318, −0.292] | −0.002 | 0.30 |
| 5m | best: vwap_reentry k=2 | 9192 | 51.0 % [50.0–52.0] | −0.503 [−0.530, −0.477] | +0.019 | 0.52 |
| 5m | wyckoff_spring | 2998 | 52.2 % [50.4–54.0] | −0.514 [−0.560, −0.468] | +0.037 | 0.55 |
| 5m | funding_extreme · htf | 177 | 60.0 % [52.6–67.0] | −0.470 [−0.667, −0.272] | +0.163 | 0.63 |
| 5m | control | 103420 | 50.0 % [49.7–50.3] | −0.587 [−0.595, −0.579] | +0.002 | 0.59 |

Frequency of the most frequent candidates OOS (≈ 2 years × 3 symbols): 1h VWAP re-entry
≈ 1782 events (~0.8/day/symbol), 1h VA re-entry ≈ 1033, 5m sweep & reclaim ≈ 14,000.

Stacked imbalances (footprint, app's own `footprint.analyze`, 300 % diagonal, ≥ 3 levels) from
daily aggTrades 2026-10-04 → 10-17 only (full-history aggTrades are too heavy for a stdlib run):
5m n = 696, ±1 ATR hit 49.6 % (continuation) / 50.4 % (fade), net −0.76 R / −0.72 R; 15m
n = 169, 51.2 % / 48.8 %, net −0.54 R / −0.42 R. Indicative only (two weeks).

Reading: the 1h mean-reversion pair (VA re-entry, VWAP 2σ re-entry) is the only place with a
gross edge whose nominal 95 % CI excludes 0 (+0.084 R [+0.009, +0.160]; +0.067 R [+0.009,
+0.124]); with 35 hypotheses that is about what chance produces (≈ 0.9 expected one-sided
false positives), it does not survive Bonferroni and the fee (0.13 R) eats it. The 5m funding
60 % hit rate is n = 177 with the same 177 events on every timeframe; at 1h it is 51 %. Fees
dominate short timeframes: 0.3 R per trade at 15m, 0.5–0.65 R at 5m with a 1-ATR stop.

What would make clearer signals, by this data: trade only where the stop can be wider than
the fee (1h+, or targets ≥ 3 ATR), test maker entries (limit at VAL/VWAP band) where the fee
is 0.02 %, and re-test the 1h VA/VWAP re-entry on fresh data before trusting it.

## Live checks (headless Chrome, :5101, CDP 9576)

| pane | read | events (all prices = venue high/low, rv identical to venue klines) |
|---|---|---|
| Binance BTCUSDT 1h | distribution, phase A, conf 0.78, TR 80,780–82,900.5 | BC 82,900.5 (rv 2.45), AR 80,780 (rv 4.25) |
| Binance BTCUSDT 15m | accumulation, phase C, conf 0.77, TR 80,780–82,092.1 | PS, SC 80,780, AR, ST, Spring 80,751 (rv 9.23), Test |
| Binance ETHUSDT 4h | accumulation, phase B, conf 0.79, TR 2,313.39–2,493.48 | PS, SC 2,313.39, AR 2,493.48, ST 2,355 |

Screenshots `*-chart.png` (overlay on), `*-wyckoff.png`, `*-signals.png`. One live Hermes
analysis (BTCUSDT 1h) cited `wyckoff.schematic/phase/tr.resistance` (short, strength 2) and
`signals.events.0.*` (strength 1, "no edge") with no validator warnings; the snapshot grew to
19 kB. BTC 1h (distribution) and 15m (accumulation) read the same 80,780 low differently — the
two timeframes disagree, which the user should see as a caveat, not a contradiction to hide.

## Limitations

- Delta in the backtest is per-bar taker delta from klines, not a per-price footprint; live
  charts have no per-bar delta, so delta-based signals are listed as unavailable and Wyckoff
  uses footprint delta only on the ≤ 6 footprint bars of 1–60m panes.
- OI history (Binance keeps 30 days) was not testable; funding is the only positioning input.
- One venue (Binance), three large caps, 2020–2026; regimes beyond that are untested.
- Wyckoff thresholds are fixed by hand (not fitted); the detector reports the latest
  structure in the 300-bar window only, and different timeframes can disagree.
- 14-day footprint sample is too small for conclusions.
