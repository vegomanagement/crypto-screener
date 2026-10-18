# Multi-timeframe sync + SMC swing structure — result

Branch `claude/mtf-sync` (from `claude/integration` @ d2adbda). All live checks on 2026-10-18
≈ 09:15–09:25 UTC against Binance USDT-M / Bybit linear. Every number below comes from
`docs/evidence/mtf-sync/` (`align.json/.md`, `swing.json/.md`, `live-crosscheck.txt`,
`hermes-btc-15m.json`, screenshots) and is reproduced with `python scripts/backtest_mtf.py`
(~2 min) and `python scripts/backtest_swing.py` (~1 min) once the kline cache exists.

## Bottom line

- **Neither HTF alignment nor the SMC swing entry model has an out-of-sample edge.** 0 of 900
  alignment hypotheses (540 variants tried; Bonferroni z = 4.03) and 0 of 6 swing-model
  hypotheses (42 variants; z = 2.64) passed. Filtering LTF signals by the 1–2 higher TFs' bias,
  SMC trend, Wyckoff phase or HTF levels does not lift the ±1 ATR hit rate off ≈ 50 % and does
  not turn any expectancy reliably positive after fees, taker or maker.
- So MTF/SMC ships as **descriptive context** ("no proven edge"): `SPLIT_CONFIG` is unchanged;
  the split only gets a **"counter-trend vs HTF" warning** when it leans against the dominant TF.
  The swing setups are shown as live states labelled "no proven out-of-sample edge — context
  only", not as signals.
- What is new and verified: a deterministic SMC structure layer (`smc.py`), the TF ladder with
  reconciliation rules, divergence/sync detection, merged levels, the analyst `mtf.*`/`smc.*`
  blocks and the dashboard section + chart overlay. Live: 25 TF reads (5 panes × 5 TFs), every
  labelled swing equals the venue kline high/low and every BOS/CHoCH is the first close beyond
  its level — 0 mismatches.

## 1. SMC structure (`smc.py`, pure, stdlib, causal)

| item | rule (long side; short = mirror) | known at (`confirm_ts`) |
|---|---|---|
| swing high / low | fractal: h[i] > the `left` highs before and ≥ the `right` highs after; EXTERNAL 5/5, INTERNAL 2/2 (configurable) | close of bar i + right |
| HH/HL/LH/LL | vs the previous swing of the same kind and strength | same as the swing |
| BOS / CHoCH | first CLOSE beyond the latest confirmed, unbroken swing: BOS in the trend's direction, CHoCH against it (first break of a series = initial BOS); the swing is consumed; wicks never break | close of the break bar |
| liquidity sweep | wick beyond an unbroken swing, close back inside; once per swing; swing stays | close of that bar |
| order block | last opposite candle at/≤ 10 bars before the leg extreme between the broken swing and the break bar; zone = its high–low; mitigated when traded into, invalidated by a close through it | close of the break bar |
| FVG | 3-bar gaps inside the displacement leg; mitigated when traded into, filled at the far edge | close of the break bar |
| dealing range | latest confirmed swing high/low, each extended by later extremes; 50 % = equilibrium; premium/discount | last bar |

Zones expire after 500 bars (keeps a full-history pass linear). `smc.as_of(items, t)` and
`smc.zone_state(z, i)` give the state as of any time without look-ahead.

## 2. Ladder, reconciliation, sync (`mtf.py`, `mtf_rules.py`, `mtf_block.py`)

**Ladder**: the pane TF, up to 3 higher ladder TFs (5m→15m→1h→4h→1D→1W) and 1 lower
(15m pane: 5m/15m/1h/4h/1D; 1h pane: 15m/1h/4h/1D/1W). Each TF: 300 closed candles through
the `/api/chart` path (`build_chart_snapshot` + `fetch_exchange_candles`), analysed in a
4-thread pool with one 20 s deadline. **Cache per (venue, symbol, tf, EMA periods, last closed
bar)**: a TF whose expected last close (start of the forming bar) is already cached is neither
fetched nor recomputed (test: second call = 0 fetches; new bar → 1 fetch). Live: first ladder
0.33–0.72 s, repeat 0.3 ms. Computed only on request: `GET /api/agent/mtf` (dashboard, only
while it is on screen — the request rides the existing visible-only 30 s poll; nothing polls
off-screen) or an analysis.

**Per TF**: bias = 5 votes (EMA ribbon stack with the chart's periods, close vs EMA 200, EMA 200
slope over 10 bars, SMC external trend, HH/HL of the last two swings), |score| ≥ 3 = clear;
SMC view (external + internal, last 4 breaks, 6 swings, sweeps, dealing range, nearest 3 OB/FVG
with state); Wyckoff (`wyckoff.analyze`, same as the chart's section); volume profile of 120
bars (POC/VAH/VAL) and the 120-bar VWAP; key levels.

**Dominant TF** = highest TF (pane and above) with a clear read (clear bias, or a Wyckoff
structure with confidence ≥ 0.45) — HTF priority. **Verdict**: `unclear` (no clear read),
`mixed_counter_trend` (a context TF leans against the dominant one), `aligned_long/short`
(dominant and pane lean the same way), else `range_bound`.

**Rules** (ids shown in the UI; all in `mtf_rules.py` docstring):

| id | case | outcome |
|---|---|---|
| W1 / W1m | LTF accumulation inside / at the edge of an HTF distribution (mirror) | conflict: counter-trend bounce inside a top; triggers: HTF close above the HTF resistance invalidates the distribution; HTF close below support confirms it |
| W2 / W2m | LTF accumulation at HTF support with the HTF bullish (bias or accumulation) | aligned |
| W3 / W3m | LTF Spring below the HTF range low | HTF Spring candidate only after an HTF bar closes back inside; still below = HTF breakdown; HTF bar not closed yet = pending; inside an HTF distribution = a test of its edge (conflict) |
| W4 / W4c | LTF range while the HTF trends without a range | with the trend = re-accumulation/re-distribution; against it = conflict |
| W5 | LTF range fully outside the HTF range | breakout vs UTAD/Spring — HTF close decides |
| B1 | clear LTF bias against clear HTF bias | conflict |
| S1 | LTF CHoCH against an intact HTF structure | **divergence**: a pullback or the start of an HTF reversal; triggers = HTF CHoCH level, LTF swing that ends the pullback |
| S2 / S3 / S4 | HTF CHoCH after the LTF CHoCH / LTF breaks back with the HTF / LTF trends against via BOS | reversal propagated / pullback ended / counter-trend leg |

**Sync TF** = lowest TF from which every TF above shares the external direction (needs ≥ 2 TFs;
none when the top two disagree); **entry TF** = the TF just below it.

**Merged levels**: all TF levels, weight 1 (5m) … 3 (4h), 4 (1D), 5 (1W); clustered within
0.5 × pane ATR; ranked by number of TFs, then Σ weight. The dashboard shows them with TF badges.

The live case from the brief is a test fixture and reproduces live: BTC 15m accumulation
phase C (80,780–82,092.1, Spring 80,751) **W1** at the support of the 1h distribution phase A
(80,780–82,900.5) → "counter-trend bounce inside a top; the 1h read has priority", trigger
"1h close above 82900.5 would invalidate the 1h distribution and align with the 15m
accumulation"; **W3**: the Spring pierced the 1h low and the 1h bar closed back inside
(81,099.5) → inside a 1h distribution "a test of its edge, not a reversal yet".

## 3. Swing-trading entry model (`swing_model.py`, one scanner for backtest and live)

HTF external trend (closed HTF bars only) → an HTF OB/FVG in that direction, unmitigated on
closed HTF bars, in discount (≤ HTF equilibrium) for longs / premium for shorts, touched by an
entry-TF bar (variant: after an entry-TF wick sweep within 24 bars) → within 3 HTF bars an
entry-TF CHoCH in the HTF direction → limit at the top of that CHoCH's OB (else its FVG, else
the close), filled within 12 bars → stop = low of the CHoCH leg − 0.1 ATR, target = HTF
dealing-range high (opposing HTF liquidity), skipped when < 2 R; half off at 2 R and stop to
break-even, rest at the target or the close after 300 bars; stop first in a bar touching both.
Pairs: 4h→15m, 1h→5m, 1d→1h. Live state per pair: `waiting_for_poi`,
`at_poi_waiting_ltf_choch`, `triggered_waiting_retest`, `triggered_filled`.

## 4. Backtests (Binance USDT-M, data to 2026-10-18 00:00 UTC)

Data: BTC/ETH 2020-01-01 →, SOL 2020-09-14 → (5m 714,816 / 639,276 bars; 15m 238,272 /
213,092; 1h 59,568 / 53,273; 4h/1d/1w aggregated from complete 1h bars). Split per series by
time: first 70 % in-sample, OOS from 2024-10-03 (BTC, ETH) / 2024-12-21 (SOL). Fees 2 × 0.05 %
taker and 2 × 0.02 % maker, in R = 2·fee·entry/risk. **No look-ahead**: an LTF bar closing at t
sees only HTF bars with `close_ts ≤ t` (the forming HTF candle never), HTF features per HTF bar
use bars ≤ that bar, swings exist only after their right-side bars closed. Tests poison every
LTF and HTF bar after t and assert identical structure, HTF trend/dealing-range arrays and
trades up to t (`tests/test_smc.py`, `tests/test_mtf.py`).

### 4a. HTF-alignment filter on the existing event signals (`scripts/backtest_mtf.py`)

Signals, entry and outcome rules exactly as `scripts/backtest_signals.py` (entry at the signal
bar's close, ATR14 unit, stop 1 ATR, targets 1.5 / 2 ATR in 48 bars, **3 ATR in 96 bars**,
12-bar cooldown). HTFs: 15m → 1h + 4h, 1h → 4h + 1d, 4h → 1d + 1w. Filters: `bias1`, `bias2`
(the 1 / 2 higher TFs' clear bias = side), `smc1`, `smc2` (SMC external trend = side), `wyck`
(HTF1 Wyckoff schematic = side), `wyck_na` (not against), `level` (entry within 0.3 HTF1 ATR of
an HTF1 level: TR edges, last swings, POC/VAH/VAL, EMA 200), `combo` (bias2 + wyck_na + level).
Params chosen on IS per (tf, signal, filter), IS n ≥ 100. **540 variants, 225 chosen × 4
settings (1.5 R / 3 ATR × taker / maker) = 900 OOS hypotheses, Bonferroni z = 4.03; pass = OOS
n ≥ 100, Bonferroni lower CI of net E[R] > 0, positive in ≥ 2 of 3 symbols. Passed: none.**

Does the filter help? OOS net E[R] at 1.5 R taker, filtered minus unfiltered, per signal family
(families with OOS n ≥ 50 after filtering), and the same filter applied to the "both sides every
12 bars" control:

| tf | filter | families improved | mean ΔE[R] | control ΔE[R] (hit rate) |
|---|---|---:|---:|---|
| 15m | bias1 / bias2 | 2/11 · 2/11 | −0.082 / −0.095 | +0.023 (50.4 %) / +0.036 (50.4 %) |
| 15m | smc1 / smc2 | 2/11 · 1/11 | −0.048 / −0.087 | −0.005 / −0.006 |
| 15m | wyck / wyck_na | 8/11 · 9/11 | +0.020 / +0.022 | −0.017 / −0.000 |
| 15m | level / combo | 5/11 · 2/8 | −0.009 / −0.064 | +0.003 / −0.009 |
| 1h | bias1 / bias2 | 4/9 · 5/7 | −0.021 / +0.037 | +0.007 / +0.024 |
| 1h | smc1 / smc2 | 6/10 · 5/9 | +0.013 / +0.005 | −0.015 / +0.003 |
| 1h | level / combo | 8/10 · 2/2 | +0.036 / +0.120 (n ≈ 70) | +0.004 / +0.074 (n 194) |
| 4h | bias1 / smc1 / level | 3/4 · 4/7 · 4/7 | +0.091 / −0.004 / −0.011 | +0.027 / +0.052 / +0.026 |

Unfiltered controls OOS: 15m n 34,434, hit 50.0 %, E −0.305 R [−0.318, −0.292]; 1h n 8,572,
50.0 %, −0.136 R; 4h n 2,106, 50.0 %, −0.063 R. Every filtered control stays at 46–51 % hit.
The few positive deltas are within noise and none makes a variant pass.

Best OOS cells (n ≥ 100, any filter) — none survives Bonferroni:

| setting | best cell | OOS n | E[R] [95 %] | Bonferroni CI |
|---|---|---:|---|---|
| 1.5 R taker | 4h cvd_divergence · wyck_na | 172 | +0.058 [−0.129, +0.245] | [−0.326, +0.442] |
| 3 ATR taker | 4h va_reentry · level | 108 | +0.163 [−0.187, +0.513] | [−0.557, +0.882] |
| 1.5 R maker | 4h cvd_divergence · wyck_na | 172 | +0.095 [−0.092, +0.281] | [−0.289, +0.478] |
| 3 ATR maker | 4h va_reentry · level | 108 | +0.198 [−0.151, +0.548] | [−0.520, +0.917] |

**Re-test of the flagged 1h/4h value-area / VWAP re-entries** (OOS; "2026" = signals since
2026-01-01, the newest data):

| candidate | filter | OOS n | hit [95 %] | 1.5 R taker | 3 ATR taker | 1.5 R maker | 3 ATR maker | 2026: n · 1.5 R taker |
|---|---|---:|---|---|---|---|---|---|
| 1h va_reentry | none | 1029 | 52.5 % [49.3–55.6] | −0.045 [−0.121, +0.031] | −0.023 | +0.034 [−0.042, +0.109] | +0.056 [−0.054, +0.165] | 417 · −0.133 [−0.251, −0.015] |
| 1h va_reentry | level | 528 | 54.5 % [50.1–58.8] | −0.019 | +0.019 | +0.060 | +0.097 [−0.057, +0.252] | 236 · −0.150 |
| 1h vwap_reentry k=2 | none | 1776 | 52.1 % [49.7–54.4] | −0.068 [−0.125, −0.010] | −0.028 | +0.014 | +0.054 [−0.030, +0.137] | 690 · −0.139 [−0.231, −0.046] |
| 1h vwap_reentry k=2 | bias2 | 333 | 52.6 % [47.0–58.0] | −0.008 | +0.094 | +0.063 | +0.166 [−0.032, +0.363] | 114 · +0.063 |
| 4h va_reentry | none | 261 | 48.4 % [42.2–54.6] | −0.076 | +0.118 [−0.104, +0.339] | −0.038 | +0.155 [−0.067, +0.377] | 106 · −0.151 |
| 4h vwap_reentry k=2.5 | none | 94 | 52.2 % [42.0–62.2] | +0.060 | −0.078 | +0.094 | −0.044 | 37 · +0.219 |

On the newest data the 1h re-entries got worse (2026 E at 1.5 R taker −0.13/−0.14 R, CIs below
zero). Maker fees and 3 ATR targets lift point estimates to +0.05…+0.2 R but every CI includes
zero, and the selection over 900 cells makes such maxima expected by chance.

### 4b. SMC swing model per fractal pair (`scripts/backtest_swing.py`)

Grid per pair: POI ∈ {any, OB, FVG} × sweep ∈ {no, yes} × entry-TF fractal ∈ {2/2, 3/3} = 12
filtered variants + 2 unfiltered (every entry-TF CHoCH → retest, either side, same stop/target
rules). 42 variants; IS-best per (pair, model) scored OOS → 6 hypotheses, Bonferroni z = 2.64.
Random control: for the chosen filtered variant, the same number of OOS entries per symbol at
random bars, random side, same risk in ATR and same R targets/management, 50 seeds.

| pair | model | chosen | IS n · E | OOS n | win rate [95 %] | E[R] taker [95 %] | E[R] maker | gross | max DD R | trades/mo/sym | random E (p05…p95) |
|---|---|---|---|---:|---|---|---:|---:|---:|---:|---|
| 4h→15m | HTF POI + CHoCH | FVG, sweep, 3/3 | 109 · −0.086 | 44 | 31.8 % [20.0–46.6] | −0.503 [−0.86, −0.14] | −0.366 | −0.275 | 22.8 | 0.62 | −0.271 (−0.69…+0.18) |
| 4h→15m | CHoCH only | 3/3 | 5293 · −0.208 | 2238 | 31.9 % [30.0–33.9] | −0.294 [−0.37, −0.22] | −0.127 | −0.017 | 666 | 31.6 | — |
| 1h→5m | HTF POI + CHoCH | FVG, no sweep, 2/2 | 665 · −0.415 | 270 | 31.1 % [25.9–36.9] | −0.501 [−0.72, −0.28] | −0.203 | −0.005 | 137 | 3.81 | −0.687 (−0.96…−0.44) |
| 1h→5m | CHoCH only | 3/3 | 15863 · −0.440 | 6653 | 29.4 % [28.4–30.6] | −0.612 [−0.66, −0.57] | −0.291 | −0.076 | 4081 | 94.0 | — |
| 1d→1h | HTF POI + CHoCH | any, sweep, 2/2 | 33 · +0.044 | 28 | 39.3 % [23.6–57.6] | −0.046 [−0.65, +0.55] | +0.036 | +0.091 | 10.1 | 0.40 | −0.024 (−0.60…+0.45) |
| 1d→1h | CHoCH only | 2/2 | 1608 · −0.114 | 666 | 34.1 % [30.6–37.8] | −0.103 [−0.27, +0.06] | −0.015 | +0.043 | 126 | 9.41 | — |

Reading: gross expectancy is ≈ 0 everywhere (−0.28…+0.09 R); the fee is large because the
stop sits just beyond the CHoCH leg: mean taker fee 0.23–0.28 R at 15m, 0.50–0.54 R at 5m,
0.14–0.15 R at 1h (maker 0.09–0.11 / 0.20–0.21 / 0.05–0.06 R). The HTF filter cuts the
trade count 50–100× but does not improve the gross result; 1d→1h is the only cell near zero and
has 28 OOS trades (indicative only). The model does not beat random entries with the same R:R
in a way the CIs can resolve. **No pass → no "Swing setups" signal section**; the live states are
context only.

## 5. Analyst and dashboard

- Snapshot: `mtf` (compact ladder: per TF bias/score, SMC trend + internal, last break, zone/EQ,
  Wyckoff, POC/value/VWAP position, nearest OB, position vs HTF; verdict, dominant/sync/entry
  TF, pair statuses, ≤ 6 conflicts, ≤ 5 triggers, 8 merged levels, setup states; tested
  < 8 kB) and `smc` (pane TF structure). Fetched in parallel with the other providers from the
  same per-TF cache. Live BTCUSDT 15m: snapshot 28,646 B (mtf 5,649 B, smc 3,507 B), built in
  11.3 s (all providers), Hermes 35 s.
- Prompt: read `mtf` top-down from the highest TF, explain conflicts with the resolve trigger,
  SMC vocabulary; an LTF factor against a clear dominant-TF read gets strength ≤ 1; `mtf.*` and
  `smc.*` factors strength ≤ 2 (no proven edge).
- Split: unchanged (`SPLIT_CONFIG` untouched — the backtest does not support re-weighting).
  `split.htf_warning` + a warning line when the split leans against the dominant TF.
- Live Hermes run (BTCUSDT 15m, `hermes-btc-15m.json`): 12 factors, 0 validator warnings;
  cited `smc.last_internal_break.*`, `smc.dealing_range.zone/position`,
  `mtf.tfs.1D.smc_trend`, `mtf.tfs.1D.wyckoff.phase`, `mtf.tfs.4h.*`, `mtf.verdict`; split
  63/37 long with the warning "counter-trend vs HTF: the split leans long while the dominant 1D
  reads bearish (MTF verdict mixed_counter_trend)"; summary_ru: "…это откат против медвежьей
  1D-дистрибуции и 4h-структуры… Резолв: закрытие 1h выше 82900.5 отменит 1h-дистрибуцию".
- Dashboard: "Multi-timeframe" is the second section (after the verdict): verdict, dominant /
  sync / entry TF, a TF chip ladder (▲/▼, ⚡ divergence, ⇄ sync), the structure table (TF →
  external trend, last BOS/CHoCH with price and time, internal trend, premium/discount + EQ,
  nearest unmitigated OB/FVG, bias, Wyckoff, position vs HTF range, relation to the TF above),
  conflicts with rule id and triggers, merged levels with TF badges, swing setups (labelled no
  edge). Toggles: "SMC on chart" (swings, BOS/CHoCH lines, OBs, FVGs, sweeps, EQ),
  "HTF structure on chart" (the next higher TF's layer in gold), "HTF range box" (the first
  higher TF with a defined Wyckoff range, dashed, titled).

## 6. Live checks (server :5102, headless Chrome CDP :9577)

`live-crosscheck.txt`: for each pane and each ladder TF the API read was compared with the
venue klines fetched independently (Binance `/fapi/v1/klines`, Bybit `/v5/market/kline`),
Decimal compare: every labelled swing high/low = that bar's venue high/low (6 per TF); every
BOS/CHoCH (4 per TF) closes beyond its level, no earlier close between the swing and the break
did, and its level = the venue high/low of the swing bar; Wyckoff TR edges are venue
highs/lows; POC/VAH/VAL recomputed from the venue's last 120 bars match. **25 TF reads,
150 swings, 100 breaks, 0 mismatches.**

| pane | verdict | dominant · sync · entry | conflicts | notes |
|---|---|---|---|---|
| Binance BTCUSDT 15m | mixed / counter-trend | 1D · 4h · 1h | W1, W1m, W1, W3, S1 | 1D bear CHoCH 82,500.1 + distribution D (82,832–87,385.1); 4h bear (CHoCH 80,985); 1h bull CHoCH 81,733 = ⚡ divergence vs 4h; 15m Spring case (above) |
| Binance BTCUSDT 1h | mixed / counter-trend | 1W · — · — | W4c, W1, W1m, W1, W3, B1, W4c, S1, S1 | 1W bull (CHoCH 82,828.7, bias +3) vs 1D bear: top two disagree → no sync |
| Binance ETHUSDT 15m | range-bound | 4h · 15m · 5m | B1, S1 | 1D/4h/1h/15m bear, 5m bull CHoCH 2,415.36 = pullback; sync at 15m |
| Binance ETHUSDT 1h | aligned short | 4h · — · — | S1 | 4h bias −4, 1h −4; 1W bull vs 1D bear → no sync |
| Bybit SOLUSDT 15m | mixed / counter-trend | 4h · — · — | S1 ×4 | alternating structure 1D bull / 4h bear / 1h bull / 15m bear / 5m bull |

Top merged level, BTCUSDT 15m: 82,056.57 (4h EMA 200, 1h VWAP, 1h swing high HH, 15m swing high,
15m TR resistance; 3 TFs) and 80,773.18 (4h HL, 1h LL, 1h TR support, 15m TR support).
Screenshots (inspected): `btc-15m-chart.png` (15m SMC + 1h layer + 1h range box
80,780–82,900.5), `btc-15m-mtf.png`, `btc-1h-*`, `eth-15m-*` (4h range box 2,313.39–2,493.48),
`eth-1h-*`, `sol-15m-*` (1h range box 101.56–109).

## 7. Tests

`tests/test_smc.py` (fractals and confirmation, close-only BOS/CHoCH, wick sweeps, OB/FVG and
mitigation, dealing range, poisoned future), `tests/test_mtf.py` (W1/W2/W3 incl. the live BTC
1h-distribution vs 15m-Spring case, W3 breakdown/pending/HTF-accumulation, W4/W4c, W5, B1;
S1 divergence → S2 propagated / S3 pullback ended / S4; sync incl. "top two disagree"; dominant
TF and every verdict; level clustering weights; per-last-bar cache; snapshot bound; HTF bars
complete and closed as of the LTF bar; swing model and HTF arrays unchanged under poisoned
LTF + HTF futures), `tests/analysis_mtf.test.cjs` (view-model, escaping, toggles, overlay model
+ draw, controller fetches only while visible and clears the overlay on destroy). Full suites:
pytest 1,720 passed; node --test 248 tests: 242 passed, 6 skipped, 0 failed; ruff (CI selection) clean.

## Limitations

- Backtest HTF bias uses full-history EMAs; live uses the 300-candle window (EMA 200 seeding
  differs slightly). HTF Wyckoff in the backtest is recomputed per HTF bar on the trailing 300
  bars, exactly like live.
- One venue for the backtest (Binance), three large caps, 2020–2026. Bybit is checked live only.
- The swing model is one explicit reading of SMC (fractal 5/5 external, OB = last opposite
  candle, POI in discount, CHoCH trigger, OB retest). Other definitions (body-only OBs, breaker
  blocks, inducement, session timing) were not tested; the filtered samples are small
  (28–270 OOS trades per pair).
- Zones expire after 500 bars; live windows are 300 bars per TF, so older HTF zones are not seen.
- Merged levels use one ATR (the pane's); weights are by TF, not by measured reaction.
- Dominant TF can be 1W on a 1h pane (3 levels up); the verdict then reflects weekly context.
