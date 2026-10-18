# SMC swing model on higher-TF fractal pairs (1d→4h, 1w→1d), 20-coin universe — result

Evidence: `docs/evidence/swing-htf/` (`results.md` = every table, `results.json` = the same data,
`live-crosscheck.txt`, screenshots). Re-run: `python scripts/backtest_swing_universe.py universe`
(select + cache klines/funding in scratch, ~10 min the first time), `... grid` (~2 min), `... refs`
(~1 min), `... report` (~15 s). Raw trades and the per-variant table stay in scratch
(`~/.hermes/cache/scratch/swing-htf/`), never in the repo.

## Verdict

- **No variant passes.** 0 of 962 variants tried (480 on 1d→4h + 480 on 1w→1d + the original
  model on 1d→1h and 4h→15m) has a positive out-of-sample net expectancy that survives Holm over
  the family (Bonferroni one-sided z ≥ 3.88; the best OOS z is 2.11, Holm p = 1.0).
- **Breaker and inducement do not help.** Trade-weighted OOS net R per factor level on 1d→4h:
  POI any −0.014 / OB −0.010 / FVG −0.094 / **breaker −0.147**; IDM none +0.055 / **IDM required
  −0.084 / EQH-EQL as inducement −0.575**. On 1w→1d every level is −0.38…−0.94 R.
- **1w→1d is clearly negative OOS** (97 variants with n ≥ 10: 4 positive, 44 with z < −1.96),
  although 15 of 27 in-sample variants were positive: the weekly read is long-biased and the OOS
  window (Oct 2024 → Oct 2026) took most alts down. Trades are rare (≈ 0.3–0.9 per coin-year).
- The **closest to passing** is a post-hoc pick, not a pre-registered one: 1d→4h, resting limit at
  the edge of any unmitigated HTF OB/FVG in discount/premium, stop beyond the zone, 2 R target with
  break-even after 1 R. OOS: n 422, net **+0.136 R** [month-block 95 % CI +0.017, +0.263], 14/20
  coins positive, +0.105 R without the best coin (WLD), random-entry gross +0.013 R, no-HTF
  control −0.012 R, as-of-OOS-start universe +0.076 R [−0.068, +0.222]. It fails because the
  **same rule was flat in-sample (−0.016 R, n 739, CI −0.10…+0.08)** and its yearly folds are
  −0.04 / +0.03 / +0.04 / −0.05 / +0.08 / +0.19 R (2021…2026): the OOS gain is a 2025–26 regime,
  and z 2.11 is what the best of ~350 tested variants reaches by chance.
- The **pre-registered candidates** (the 5 best in-sample per pair, IS n ≥ 30) all lose OOS:
  1d→4h = breaker + IDM limit variants (IS +0.20…+0.35 R on n 30–45 → OOS −0.42…−0.74 R on n 11–15);
  1w→1d = sweep + limit variants (IS +0.16…+0.52 R → OOS −0.46…−0.00 R).
- References on the same universe: original model **4h→15m −0.224 R** OOS (n 758, z −3.6; fees
  0.13 R/trade), **1d→1h −0.113 R** (n 147). Without the HTF filter: −0.159 / −0.032 R.
- So nothing is wired as a signal. Breaker / IDM / EQH-EQL ship as **descriptive context** in the
  SMC overlay and the structure ladder; the live "Swing setups" keep their "no proven
  out-of-sample edge" label (now citing 0 of 962).

## 1. Universe (chosen by data)

Rule (`scripts/swing_universe_data.py`): Binance USDT-M `PERPETUAL`, `underlyingType = COIN`
(no index/TradFi), base not a stablecoin, `onboardDate` ≥ 3 years before the decision date, traded
on ≥ 200 of the 365 days before it, top 20 by mean daily quote volume (1d kline field 7) over
those 365 days.

| decision date | candidates | top 20 |
|---|---|---|
| END 2026-10-18 (main; survivorship-biased) | 143 | BTC, ETH, SOL, ZEC, XRP, DOGE, BNB, 1000PEPE, SUI, NEAR, ADA, LINK, WLD, AVAX, UNI, FIL, BCH, LTC, AAVE, DASH |
| OOS start 2024-10-03 (as-of check; settled contracts included) | 107 | BTC, ETH, SOL, DOGE, XRP, BNB, AVAX, 1000SHIB, LINK, TRB, ADA, FIL, BCH, NEAR, LTC, FTM, DOT, ETC, GALA, UNI |

Survivorship: the main list is today's liquid coins (ZEC, WLD, SUI, PEPE, AAVE, DASH became
liquid recently). The as-of list is what was knowable on the OOS start date; the candidates were
re-run on it (column "as-of-universe net" in `results.md`). No conclusion changes.

## 2. Data, pairs, split, costs

- 1h klines from data.binance.vision monthly zips (+ API tail), 4h / 1d / 1w aggregated from
  them (complete buckets only; weeks from Monday 00:00 UTC), 15m for the 4h→15m reference.
  History from 2020-01 (or the listing month) to 2026-10-18.
- Pairs: **1d→4h** and **1w→1d** with the full grid; 1d→1h and 4h→15m with the original model
  only. 1w→1d has ≈ 350 weekly bars per coin, so all statistics pool coins.
- Split: one calendar date for every coin, **OOS from 2024-10-03** (= 2020-01-01 + 70 % of the
  span). A trade belongs to the period of its signal bar. Yearly folds 2021…2026 for consistency.
- Costs per trade in R (notional per 1 R = entry / risk): maker 0.02 % per side for limit fills
  (retest / POI-edge entries, target and partial exits), taker 0.05 % + 0.01 % slippage for market
  fills (CHoCH-close entries, stop / break-even / timeout exits); **real historical funding** from
  `/fapi/v1/fundingRate` summed over the holding time (half size after the partial), 0.01 % per 8 h
  against the position only where the venue has no history. A stress column charges taker +
  slippage on both sides. Higher TFs make the costs small: 0.01–0.03 R per trade on 1d→4h and
  1w→1d (0.13 R on 4h→15m); funding averages −0.01…+0.03 R.

## 3. SMC extensions (`smc.py`, causal, each with a confirmation time)

| item | rule | known at |
|---|---|---|
| breaker | an OB that failed: the first later close beyond its far edge plus an opposite BOS/CHoCH within 20 bars of that close (and after the OB's confirmation); polarity flips, zone = the OB's high/low; mitigated on the retest from the other side, invalidated by a close back through | the later of the close-through and the break |
| inducement (IDM) | per BOS/CHoCH: the first INTERNAL (2/2) swing against the break that forms after the break bar; swept by the first later bar whose wick trades through it | the internal swing's confirmation; sweep at that bar's close |
| equal highs / lows | two internal swings of one kind ≤ 50 bars apart within 0.1 × ATR14 (simple mean TR of the later swing's bar), untouched between them; level = the pair's extreme | the later swing's confirmation |

Model use (`swing_model.py`, the default parameters reproduce the original model exactly —
tested): POI ∈ {OB or FVG, OB, FVG, breaker}; IDM ∈ {none, the zone's IDM confirmed beyond the
zone (the tap then sweeps it), an HTF EQL/EQH pool formed after the break beyond the zone};
entry-TF sweep before the tap ∈ {no, yes}; entry ∈ {LTF CHoCH → limit at the CHoCH's OB/FVG,
LTF CHoCH close (market), resting limit at the POI edge without LTF confirmation}; stop ∈
{beyond the CHoCH leg, beyond the HTF POI ± 0.1 HTF ATR}; exits ∈ {HTF external liquidity with
half off at 2 R and stop to break-even, fixed 2 R, fixed 3 R, fixed 2 R + break-even after 1 R}.
4 × 3 × 2 × 5 (entry/stop combos; limit implies the POI stop) × 4 = **480 per pair**.
Limit entries use only HTF bars closed by the entry bar's OPEN and fill at min(edge, open).

## 4. Statistics

Month-clustered z (trades of one month across 20 coins are not independent) and a month-block
bootstrap CI; Holm over all 962 variants (one-sided, OOS n < 10 → p = 1). Pass required ALL of:
net > 0, Holm p < 0.05, CI above 0, > 0 without the best coin, ≥ 60 % of coins positive, random
entries (same coin, side, count, stop in ATR, R:R, exits; 20 seeds) beaten in ≥ 95 % of seeds,
better than the same rules without the HTF filter, positive on the as-of universe, ≥ 75 % of
yearly folds positive. Per candidate: n, hit rate, gross/fees/funding/net R, all-taker net, z,
Holm p, PF, max drawdown in R, mean holding days, trades per coin-year, long share, per-coin table.

Key OOS numbers (full tables in `docs/evidence/swing-htf/results.md`):

| pair · variant | n | hit | net R [CI] | PF | maxDD R | hold d | /coin/yr | IS net (n) | verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1d→4h any · limit · POI stop · 2R+BE | 422 | 0.35 | +0.136 [+0.017, +0.263] | 1.29 | 14.3 | 4.2 | 10.3 | −0.016 (739) | fails Holm, folds |
| 1d→4h any · limit · POI stop · 3R | 385 | 0.30 | +0.172 [+0.002, +0.349] | 1.24 | 27.1 | 6.9 | 9.4 | −0.020 (688) | fails Holm, folds |
| 1d→4h OB · limit · POI stop · 3R | 251 | 0.32 | +0.224 [−0.008, +0.449] | 1.33 | 25.4 | 10.2 | 6.2 | −0.185 (448) | fails Holm, CI, coins, folds |
| 1d→4h breaker · limit · POI stop · liq | 53 | 0.43 | +0.380 [−0.127, +0.834] | 1.66 | 10.2 | 10.8 | 1.3 | +0.062 (80) | fails Holm, CI, coins, folds |
| 1d→4h breaker + IDM · limit · 3R (best IS) | 14 | 0.14 | −0.446 [−1.01, +0.40] | 0.49 | 9.2 | 3.7 | 0.3 | +0.351 (45) | fails everything |
| 1w→1d breaker · limit · 2R+BE (best OOS) | 14 | 0.43 | +0.226 [−0.50, +0.98] | 1.62 | 3.9 | 19.3 | 0.3 | +0.988 (2) | fails Holm, CI, coins, random, as-of, folds |
| 1w→1d any · sweep · limit · liq (best IS) | 28 | 0.32 | −0.003 [−0.56, +0.82] | 1.00 | 7.3 | 36.1 | 0.7 | +0.516 (50) | fails |
| 4h→15m original model | 758 | 0.31 | −0.224 | 0.72 | 189 | 0.3 | 18.6 | −0.206 (1573) | negative |
| 1d→1h original model | 147 | 0.32 | −0.113 | 0.84 | 32.7 | 1.3 | 3.6 | −0.154 (268) | negative |

Expanding walk-forward selection (each year pick the best variant on all earlier trades, n ≥ 30):
1d→4h folds −0.15, +0.16, −0.41, −0.52, +0.01 R; 1w→1d −0.21, +0.69, +0.31, −0.53 R — the
in-sample winner does not carry forward.

## 5. Live (descriptive context only)

- `/api/agent/mtf` per TF: `breakers` (≤ 2 nearest, non-invalidated, with state), `inducement`
  (latest break's IDM: price, side, resting/swept, swept_ts), `equal_levels` (≤ 3 resting EQH/EQL);
  the compact snapshot adds `nearest_breaker`, `inducement`, `equal_levels` (≤ 2).
- SMC overlay: breakers as dashed-outline zones, IDM as a violet dashed line (`IDM ✓` once swept,
  the line ends at the sweep bar), EQH/EQL as blue dotted lines; right-edge zone labels are now
  de-collided. The ladder gets a "Breaker · IDM · EQH/EQL" column.
- Venue cross-check (`live-crosscheck.txt`, BTC 4h/1d/1w/1h and ETH 1d/1w/4h vs
  `fapi/v1/klines`, Decimal): every breaker edge = the venue high/low of its OB candle and a venue
  close went through the far edge before its confirmation; every IDM price = the venue swing
  high/low and its resting/swept state matches the first venue bar through it; every EQH/EQL =
  a venue high/low of its two swings and is still untouched. **64 checks, 0 mismatches.**
- Screenshots (inspected): `btc-4h-overlay.png` (4h + 1D layer: Breaker (m), IDM ✓, 1D IDM, EQH,
  EQL), `btc-1d-overlay.png` (1D + 1W layer: 1W IDM, Breaker (m), EQH), `eth-1d-overlay.png`,
  `btc-4h-mtf.png` / `btc-1d-mtf.png` / `eth-1d-mtf.png` (ladder with the new column; setups caption).

## 6. Limitations

- Fixed definitions: fractal 5/5 external and 2/2 internal, OB = last opposite candle ≤ 10 bars
  before the leg extreme, breaker window 20 bars, EQ tolerance 0.1 ATR / 50 bars, IDM = first
  internal swing after the break. Other readings of SMC (e.g. IDM measured from the swing that
  created the POI, liquidity-weighted EQ pools, session-based sweeps) were not tested.
- Bars-based parameters (max hold 300 bars, arm 3 HTF bars, fill within 12 bars) mean different
  calendar horizons per pair; 1w→1d holds up to ~10 months.
- Intrabar order is unknown: stop first when a bar touches both, limit fills assume the touch
  happened after the open; fills at the zone edge with no queue position (optimistic for limits).
- The OOS window is two years and one regime-heavy period (alts' 2025–26 decline); 1w→1d has
  only 10–40 OOS trades per variant, so its CIs span ±0.5–1 R.
- The universe is today's survivors; the as-of-OOS-start universe check reduces but does not
  remove that bias (pre-2024 listing selection still uses today's exchangeInfo for the candidate
  pool, though settled contracts are included).
- Funding uses the venue's history; settled contracts without it fall back to 0.01 %/8 h.
