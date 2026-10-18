# Swing HTF universe backtest — raw tables

Split: OOS from 2024-10-03 to 2026-10-18 (same date for every coin). Variant grid per pair: 480; Holm family (all variants tried incl. references): 962 (Bonferroni one-sided z ≥ 3.88); with OOS n ≥ 10: 353. Any variant passing every check: **False**.

## Universe

Rule: Binance USDT-M PERPETUAL, underlyingType COIN, non-stablecoin base, onboardDate ≥ 3 years before the decision date, ≥ 200 trading days in the 365 days before it, top 20 by mean daily quote volume over those 365 days.

| decision date | candidates | coins (mean daily quote volume, $M) |
|---|---|---|
| END | 143 | BTCUSDT (12121), ETHUSDT (10319), SOLUSDT (2301), ZECUSDT (1141), XRPUSDT (964), DOGEUSDT (583), BNBUSDT (468), 1000PEPEUSDT (308), SUIUSDT (273), NEARUSDT (220), ADAUSDT (217), LINKUSDT (168), WLDUSDT (167), AVAXUSDT (163), UNIUSDT (142), FILUSDT (139), BCHUSDT (138), LTCUSDT (116), AAVEUSDT (106), DASHUSDT (101) |
| OOS_START (settled/delisted contracts included) | 107 | BTCUSDT (15517), ETHUSDT (7428), SOLUSDT (2756), DOGEUSDT (756), XRPUSDT (643), BNBUSDT (488), AVAXUSDT (432), 1000SHIBUSDT (360), LINKUSDT (346), TRBUSDT (335), ADAUSDT (276), FILUSDT (270), BCHUSDT (263), NEARUSDT (256), LTCUSDT (214), FTMUSDT (198), DOTUSDT (193), ETCUSDT (192), GALAUSDT (165), UNIUSDT (136) |

## 1d>4h

Variants 480, IS n ≥ 30: 189, OOS n ≥ 10: 254.

### Pre-registered candidates (best 5 by in-sample net), in-sample → out-of-sample

| variant | n | coins | hit | gross R | fees R | funding R | net R | net (all-taker) | z | Holm p | PF | maxDD R | hold d | /coin/yr |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| IS breaker|idm=idm|sweep=0|limit|stop=poi|r3 | 45 | 15 | 0.356 | 0.360 | 0.009 | -0.000 | **0.351** | 0.344 | 1.28 | – | 1.53 | 10.2 | 11.2 | 0.57 |
| OOS breaker|idm=idm|sweep=0|limit|stop=poi|r3 | 14 | 12 | 0.143 | -0.429 | 0.013 | -0.004 | **-0.446** | -0.453 | -1.16 | 1.000 | 0.49 | 9.2 | 3.7 | 0.34 |
| IS breaker|idm=idm|sweep=0|limit|stop=poi|r2 | 45 | 15 | 0.422 | 0.267 | 0.009 | 0.005 | **0.263** | 0.256 | 1.10 | – | 1.45 | 8.1 | 6.4 | 0.57 |
| OOS breaker|idm=idm|sweep=0|limit|stop=poi|r2 | 15 | 12 | 0.200 | -0.400 | 0.012 | -0.003 | **-0.415** | -0.423 | -1.28 | 1.000 | 0.49 | 10.2 | 3.5 | 0.37 |
| IS breaker|idm=idm|sweep=0|limit|stop=poi|liq | 41 | 15 | 0.439 | 0.400 | 0.179 | 0.023 | **0.244** | 0.152 | 0.74 | – | 1.34 | 14.2 | 11.7 | 0.52 |
| OOS breaker|idm=idm|sweep=0|limit|stop=poi|liq | 13 | 11 | 0.154 | -0.412 | 0.013 | -0.009 | **-0.434** | -0.442 | -1.02 | 1.000 | 0.49 | 9.2 | 6.8 | 0.32 |
| IS breaker|idm=idm|sweep=1|limit|stop=poi|r3 | 30 | 14 | 0.333 | 0.239 | 0.009 | -0.001 | **0.228** | 0.222 | 0.67 | – | 1.34 | 10.9 | 10.7 | 0.38 |
| OOS breaker|idm=idm|sweep=1|limit|stop=poi|r3 | 11 | 10 | 0.091 | -0.636 | 0.013 | 0.002 | **-0.648** | -0.656 | -1.68 | 1.000 | 0.29 | 8.1 | 2.1 | 0.27 |
| IS breaker|idm=idm|sweep=1|limit|stop=poi|r2 | 30 | 14 | 0.400 | 0.200 | 0.009 | 0.005 | **0.197** | 0.189 | 0.66 | – | 1.33 | 9.0 | 6.3 | 0.38 |
| OOS breaker|idm=idm|sweep=1|limit|stop=poi|r2 | 11 | 10 | 0.091 | -0.727 | 0.013 | 0.002 | **-0.739** | -0.746 | -2.55 | 1.000 | 0.20 | 8.1 | 2.0 | 0.27 |

### Closest to passing (best 5 by OOS z — post hoc)

| variant | n | coins | hit | gross R | fees R | funding R | net R | net (all-taker) | z | Holm p | PF | maxDD R | hold d | /coin/yr |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| OOS any|idm=none|sweep=0|limit|stop=poi|r2+be | 422 | 20 | 0.351 | 0.156 | 0.022 | 0.001 | **0.136** | 0.118 | 2.11 | 1.000 | 1.29 | 14.3 | 4.2 | 10.34 |
| OOS any|idm=none|sweep=0|limit|stop=poi|r3 | 385 | 20 | 0.304 | 0.195 | 0.023 | 0.000 | **0.172** | 0.155 | 1.93 | 1.000 | 1.24 | 27.1 | 6.9 | 9.44 |
| OOS ob|idm=none|sweep=0|limit|stop=poi|r3 | 251 | 20 | 0.323 | 0.236 | 0.011 | -0.001 | **0.224** | 0.215 | 1.90 | 1.000 | 1.33 | 25.4 | 10.2 | 6.15 |
| OOS breaker|idm=none|sweep=0|limit|stop=poi|liq | 53 | 19 | 0.434 | 0.395 | 0.011 | -0.004 | **0.380** | 0.371 | 1.71 | 1.000 | 1.66 | 10.2 | 10.8 | 1.30 |
| OOS any|idm=none|sweep=1|limit|stop=poi|liq | 229 | 20 | 0.376 | 0.258 | 0.027 | -0.002 | **0.229** | 0.209 | 1.59 | 1.000 | 1.36 | 14.5 | 5.5 | 5.61 |

### Factor levels (OOS net R, trade-weighted over every variant with that level)

| factor | levels (net R / trades) |
|---|---|
| poi | any: -0.014 / 6996; ob: -0.010 / 4367; fvg: -0.094 / 4343; breaker: -0.147 / 627 |
| idm | none: 0.055 / 9623; idm: -0.084 / 5484; eq: -0.575 / 1226 |
| sweep | False: -0.046 / 10104; True: -0.029 / 6229 |
| entry | retest: -0.348 / 2010; close: -0.015 / 3666; limit: 0.010 / 10657 |
| stop | choch: -0.126 / 2972; poi: -0.020 / 13361 |
| target | liq: -0.037 / 3311; r2: -0.044 / 8780; r3: -0.031 / 4242 |
| be | False: -0.043 / 11906; True: -0.029 / 4427 |

### Expanding walk-forward selection over the whole grid (yearly folds)

| fold | chosen (best net on all earlier trades, n ≥ 30) | train net | fold n | fold net |
|---|---|---|---|---|
| 2022-01-01..2023-01-01 | any|idm=idm|sweep=1|limit|stop=poi|r3 | 0.266 | 60 | -0.147 |
| 2023-01-01..2024-01-01 | breaker|idm=none|sweep=0|limit|stop=poi|r3 | 0.442 | 29 | 0.157 |
| 2024-01-01..2025-01-01 | breaker|idm=idm|sweep=0|limit|stop=poi|r3 | 0.524 | 10 | -0.409 |
| 2025-01-01..2026-01-01 | breaker|idm=idm|sweep=0|limit|stop=poi|r3 | 0.322 | 8 | -0.517 |
| 2026-01-01..2026-10-18 | breaker|idm=none|sweep=0|limit|stop=poi|liq | 0.211 | 15 | 0.012 |

## 1w>1d

Variants 480, IS n ≥ 30: 27, OOS n ≥ 10: 97.

### Pre-registered candidates (best 5 by in-sample net), in-sample → out-of-sample

| variant | n | coins | hit | gross R | fees R | funding R | net R | net (all-taker) | z | Holm p | PF | maxDD R | hold d | /coin/yr |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| IS any|idm=none|sweep=1|limit|stop=poi|liq | 50 | 17 | 0.400 | 0.569 | 0.010 | -0.043 | **0.516** | 0.509 | 1.20 | – | 1.88 | 14.1 | 52.0 | 0.63 |
| OOS any|idm=none|sweep=1|limit|stop=poi|liq | 28 | 17 | 0.321 | 0.008 | 0.007 | -0.004 | **-0.003** | -0.008 | -0.01 | 1.000 | 1.00 | 7.3 | 36.1 | 0.69 |
| IS any|idm=none|sweep=1|limit|stop=poi|r3 | 51 | 17 | 0.373 | 0.400 | 0.007 | -0.006 | **0.388** | 0.382 | 1.18 | – | 1.63 | 10.1 | 56.4 | 0.64 |
| OOS any|idm=none|sweep=1|limit|stop=poi|r3 | 34 | 18 | 0.176 | -0.317 | 0.005 | -0.008 | **-0.330** | -0.333 | -1.66 | 1.000 | 0.61 | 12.5 | 42.5 | 0.83 |
| IS any|idm=idm|sweep=1|limit|stop=poi|liq | 31 | 12 | 0.323 | 0.381 | 0.011 | -0.043 | **0.327** | 0.320 | 0.53 | – | 1.48 | 15.1 | 48.3 | 0.39 |
| OOS any|idm=idm|sweep=1|limit|stop=poi|liq | 22 | 13 | 0.318 | -0.228 | 0.007 | -0.004 | **-0.240** | -0.244 | -0.89 | 1.000 | 0.65 | 7.2 | 40.0 | 0.54 |
| IS any|idm=none|sweep=1|limit|stop=poi|r2 | 51 | 17 | 0.412 | 0.206 | 0.007 | -0.001 | **0.198** | 0.192 | 0.86 | – | 1.35 | 5.7 | 48.5 | 0.64 |
| OOS any|idm=none|sweep=1|limit|stop=poi|r2 | 34 | 18 | 0.265 | -0.206 | 0.005 | -0.005 | **-0.216** | -0.219 | -1.07 | 1.000 | 0.71 | 9.1 | 29.0 | 0.83 |
| IS ob|idm=none|sweep=0|limit|stop=poi|r3 | 54 | 19 | 0.315 | 0.164 | 0.004 | 0.003 | **0.163** | 0.161 | 0.50 | – | 1.25 | 14.6 | 69.5 | 0.68 |
| OOS ob|idm=none|sweep=0|limit|stop=poi|r3 | 37 | 20 | 0.135 | -0.459 | 0.004 | 0.001 | **-0.463** | -0.465 | -1.67 | 1.000 | 0.47 | 17.2 | 31.1 | 0.91 |

### Closest to passing (best 5 by OOS z — post hoc)

| variant | n | coins | hit | gross R | fees R | funding R | net R | net (all-taker) | z | Holm p | PF | maxDD R | hold d | /coin/yr |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| OOS breaker|idm=none|sweep=0|limit|stop=poi|r2+be | 14 | 9 | 0.429 | 0.214 | 0.005 | 0.016 | **0.226** | 0.222 | 0.56 | 1.000 | 1.62 | 3.9 | 19.3 | 0.34 |
| OOS breaker|idm=none|sweep=0|limit|stop=poi|liq | 11 | 8 | 0.455 | 0.102 | 0.005 | 0.028 | **0.124** | 0.120 | 0.38 | 1.000 | 1.24 | 2.0 | 35.5 | 0.27 |
| OOS breaker|idm=none|sweep=0|limit|stop=poi|r2 | 14 | 9 | 0.357 | 0.071 | 0.005 | 0.022 | **0.088** | 0.085 | 0.22 | 1.000 | 1.14 | 4.7 | 27.5 | 0.34 |
| OOS ob|idm=none|sweep=1|limit|stop=poi|liq | 18 | 16 | 0.278 | 0.030 | 0.005 | 0.007 | **0.033** | 0.029 | 0.05 | 1.000 | 1.05 | 7.0 | 43.4 | 0.44 |
| OOS any|idm=none|sweep=1|limit|stop=poi|liq | 28 | 17 | 0.321 | 0.008 | 0.007 | -0.004 | **-0.003** | -0.008 | -0.01 | 1.000 | 1.00 | 7.3 | 36.1 | 0.69 |

### Factor levels (OOS net R, trade-weighted over every variant with that level)

| factor | levels (net R / trades) |
|---|---|
| poi | any: -0.500 / 1340; ob: -0.555 / 913; fvg: -0.578 / 821; breaker: -0.528 / 442 |
| idm | none: -0.550 / 1866; idm: -0.505 / 1326; eq: -0.577 / 324 |
| sweep | False: -0.519 / 2243; True: -0.565 / 1273 |
| entry | retest: -0.674 / 831; close: -0.869 / 956; limit: -0.285 / 1729 |
| stop | choch: -0.939 / 962; poi: -0.384 / 2554 |
| target | liq: -0.542 / 692; r2: -0.520 / 1902; r3: -0.564 / 922 |
| be | False: -0.550 / 2558; True: -0.498 / 958 |

### Expanding walk-forward selection over the whole grid (yearly folds)

| fold | chosen (best net on all earlier trades, n ≥ 30) | train net | fold n | fold net |
|---|---|---|---|---|
| 2022-01-01..2023-01-01 | None | – | 0 | – |
| 2023-01-01..2024-01-01 | any|idm=none|sweep=0|limit|stop=poi|liq | 0.176 | 30 | -0.211 |
| 2024-01-01..2025-01-01 | any|idm=none|sweep=1|limit|stop=poi|liq | 0.392 | 13 | 0.694 |
| 2025-01-01..2026-01-01 | any|idm=none|sweep=1|limit|stop=poi|liq | 0.466 | 17 | 0.309 |
| 2026-01-01..2026-10-18 | any|idm=none|sweep=1|limit|stop=poi|liq | 0.428 | 8 | -0.532 |

## Candidate controls (OOS)

| pair variant | IS net (n) | net R [95 % month-block CI] | coins + | w/o best coin | top-2 share | random gross (share ≥) | variant gross | no-HTF net | as-of-universe net (n) | yearly folds net (n) | passes |
|---|---|---|---|---|---|---|---|---|---|---|
| 1d>4h breaker|idm=idm|sweep=0|limit|stop=poi|r3 | 0.351 (45) | -0.446 [-1.011, 0.395] (n 14) | 2/12 | -0.708 (DOGEUSDT) | – | -0.300 (0.70) | -0.429 | 0.118 | -0.613 (10) | 2021: 1.17 (11), 2022: 0.49 (8), 2023: -0.19 (14), 2024: -0.41 (10), 2025: -0.52 (8), 2026: -0.22 (5) | fail: net_gt_0, holm_p_lt_0.05, ci_above_0, not_one_coin, coins_majority_positive, beats_random, beats_no_htf, asof_universe_positive, folds_positive |
| 1d>4h breaker|idm=idm|sweep=0|limit|stop=poi|r2 | 0.263 (45) | -0.415 [-0.857, 0.289] (n 15) | 3/12 | -0.586 (DOGEUSDT) | – | -0.166 (0.80) | -0.400 | 0.062 | -0.192 (11) | 2021: 0.91 (11), 2022: 0.12 (8), 2023: -0.14 (14), 2024: -0.10 (10), 2025: -0.64 (8), 2026: -0.01 (6) | fail: net_gt_0, holm_p_lt_0.05, ci_above_0, not_one_coin, coins_majority_positive, beats_random, beats_no_htf, asof_universe_positive, folds_positive |
| 1d>4h breaker|idm=idm|sweep=0|limit|stop=poi|liq | 0.244 (41) | -0.434 [-1.012, 0.451] (n 13) | 2/11 | -0.850 (DOGEUSDT) | – | -0.215 (0.70) | -0.412 | -0.045 | -0.170 (9) | 2021: 1.25 (10), 2022: 0.15 (8), 2023: -0.52 (13), 2024: 0.07 (9), 2025: -0.22 (7), 2026: -0.62 (5) | fail: net_gt_0, holm_p_lt_0.05, ci_above_0, not_one_coin, coins_majority_positive, beats_random, beats_no_htf, asof_universe_positive, folds_positive |
| 1d>4h breaker|idm=idm|sweep=1|limit|stop=poi|r3 | 0.228 (30) | -0.648 [-1.015, 0.190] (n 11) | 1/10 | -1.010 (DOGEUSDT) | – | -0.291 (0.65) | -0.636 | 0.118 | -0.514 (8) | 2021: 0.98 (8), 2022: 0.58 (5), 2023: -0.69 (10), 2024: -0.12 (7), 2025: -0.34 (6), 2026: -1.01 (4) | fail: net_gt_0, holm_p_lt_0.05, ci_above_0, not_one_coin, coins_majority_positive, beats_random, beats_no_htf, asof_universe_positive, folds_positive |
| 1d>4h breaker|idm=idm|sweep=1|limit|stop=poi|r2 | 0.197 (30) | -0.739 [-1.015, -0.109] (n 11) | 1/10 | -1.010 (DOGEUSDT) | – | -0.155 (0.90) | -0.727 | 0.062 | -0.260 (8) | 2021: 0.88 (8), 2022: 0.18 (5), 2023: -0.40 (10), 2024: -0.14 (7), 2025: -0.51 (6), 2026: -1.01 (4) | fail: net_gt_0, holm_p_lt_0.05, ci_above_0, not_one_coin, coins_majority_positive, beats_random, beats_no_htf, asof_universe_positive, folds_positive |
| 1d>4h any|idm=none|sweep=0|limit|stop=poi|r2+be | -0.016 (739) | 0.136 [0.017, 0.263] (n 422) | 14/20 | 0.105 (WLDUSDT) | 0.47 | 0.023 (0.05) | 0.156 | -0.012 | 0.076 (409) | 2021: -0.04 (152), 2022: 0.03 (162), 2023: 0.04 (189), 2024: -0.05 (212), 2025: 0.08 (213), 2026: 0.19 (153) | fail: holm_p_lt_0.05, folds_positive |
| 1d>4h any|idm=none|sweep=0|limit|stop=poi|r3 | -0.020 (688) | 0.172 [0.002, 0.349] (n 385) | 13/20 | 0.108 (WLDUSDT) | 0.64 | -0.003 (0.00) | 0.195 | -0.040 | 0.209 (376) | 2021: -0.05 (139), 2022: -0.01 (154), 2023: 0.06 (176), 2024: 0.05 (196), 2025: 0.09 (195), 2026: 0.18 (141) | fail: holm_p_lt_0.05, folds_positive |
| 1d>4h ob|idm=none|sweep=0|limit|stop=poi|r3 | -0.185 (448) | 0.224 [-0.008, 0.449] (n 251) | 10/20 | 0.169 (WLDUSDT) | 0.50 | 0.034 (0.00) | 0.236 | -0.060 | 0.198 (238) | 2021: -0.24 (91), 2022: -0.25 (88), 2023: -0.12 (119), 2024: -0.03 (140), 2025: 0.06 (126), 2026: 0.35 (87) | fail: holm_p_lt_0.05, ci_above_0, coins_majority_positive, folds_positive |
| 1d>4h breaker|idm=none|sweep=0|limit|stop=poi|liq | 0.062 (80) | 0.380 [-0.127, 0.834] (n 53) | 11/19 | 0.272 (DOGEUSDT) | 0.57 | -0.015 (0.05) | 0.395 | -0.045 | 0.209 (50) | 2021: 0.74 (14), 2022: -0.02 (18), 2023: -0.25 (26), 2024: -0.03 (22), 2025: 0.47 (34), 2026: 0.01 (15) | fail: holm_p_lt_0.05, ci_above_0, coins_majority_positive, folds_positive |
| 1d>4h any|idm=none|sweep=1|limit|stop=poi|liq | -0.181 (379) | 0.229 [-0.058, 0.515] (n 229) | 12/20 | 0.154 (WLDUSDT) | 0.59 | -0.027 (0.00) | 0.258 | -0.023 | 0.201 (217) | 2021: -0.07 (72), 2022: -0.31 (94), 2023: -0.06 (84), 2024: -0.13 (120), 2025: 0.12 (130), 2026: 0.32 (68) | fail: holm_p_lt_0.05, ci_above_0, folds_positive |
| 1w>1d any|idm=none|sweep=1|limit|stop=poi|liq | 0.516 (50) | -0.003 [-0.563, 0.824] (n 28) | 5/17 | -0.375 (FILUSDT) | – | 0.043 (0.40) | 0.008 | -0.179 | -0.135 (30) | 2021: -0.70 (3), 2022: 0.13 (11), 2023: 0.01 (21), 2024: 0.69 (13), 2025: 0.31 (17), 2026: -0.53 (8) | fail: net_gt_0, holm_p_lt_0.05, ci_above_0, not_one_coin, coins_majority_positive, beats_random, asof_universe_positive |
| 1w>1d any|idm=none|sweep=1|limit|stop=poi|r3 | 0.388 (51) | -0.330 [-0.696, 0.082] (n 34) | 5/18 | -0.540 (LTCUSDT) | – | -0.007 (0.90) | -0.317 | -0.317 | -0.327 (34) | 2021: -0.79 (4), 2022: -0.05 (10), 2023: -0.15 (20), 2024: 1.22 (16), 2025: -0.19 (23), 2026: -0.99 (7) | fail: net_gt_0, holm_p_lt_0.05, ci_above_0, not_one_coin, coins_majority_positive, beats_random, beats_no_htf, asof_universe_positive, folds_positive |
| 1w>1d any|idm=idm|sweep=1|limit|stop=poi|liq | 0.327 (31) | -0.240 [-0.698, 0.417] (n 22) | 5/13 | -0.431 (LTCUSDT) | – | 0.031 (0.70) | -0.228 | -0.179 | -0.465 (24) | 2021: -1.01 (1), 2022: -0.43 (9), 2023: -0.62 (13), 2024: 0.48 (8), 2025: -0.13 (15), 2026: -0.25 (5) | fail: net_gt_0, holm_p_lt_0.05, ci_above_0, not_one_coin, coins_majority_positive, beats_random, beats_no_htf, asof_universe_positive, folds_positive |
| 1w>1d any|idm=none|sweep=1|limit|stop=poi|r2 | 0.198 (51) | -0.216 [-0.616, 0.190] (n 34) | 6/18 | -0.357 (LTCUSDT) | – | 0.015 (0.90) | -0.206 | -0.302 | -0.302 (34) | 2021: -0.79 (4), 2022: 0.46 (10), 2023: -0.35 (20), 2024: 0.67 (16), 2025: 0.03 (23), 2026: -0.99 (7) | fail: net_gt_0, holm_p_lt_0.05, ci_above_0, not_one_coin, coins_majority_positive, beats_random, asof_universe_positive, folds_positive |
| 1w>1d ob|idm=none|sweep=0|limit|stop=poi|r3 | 0.163 (54) | -0.463 [-0.997, 0.025] (n 37) | 5/20 | -0.561 (LTCUSDT) | – | -0.018 (0.90) | -0.459 | -0.404 | -0.351 (37) | 2021: -0.77 (4), 2022: -0.38 (9), 2023: -0.19 (21), 2024: 0.55 (18), 2025: -0.24 (26), 2026: -0.99 (8) | fail: net_gt_0, holm_p_lt_0.05, ci_above_0, not_one_coin, coins_majority_positive, beats_random, beats_no_htf, asof_universe_positive, folds_positive |
| 1w>1d breaker|idm=none|sweep=0|limit|stop=poi|r2+be | 0.988 (2) | 0.226 [-0.501, 0.977] (n 14) | 4/9 | 0.076 (DASHUSDT) | 1.36 | -0.025 (0.15) | 0.214 | -0.162 | -0.006 (13) | 2021: – (0), 2022: – (0), 2023: – (0), 2024: 0.98 (4), 2025: 0.86 (6), 2026: -0.65 (6) | fail: holm_p_lt_0.05, ci_above_0, coins_majority_positive, beats_random, asof_universe_positive, folds_positive |
| 1w>1d breaker|idm=none|sweep=0|limit|stop=poi|liq | 1.492 (2) | 0.124 [-0.567, 0.660] (n 11) | 4/8 | -0.299 (NEARUSDT) | 3.70 | 0.056 (0.50) | 0.102 | -0.206 | -0.002 (10) | 2021: – (0), 2022: – (0), 2023: – (0), 2024: 0.73 (4), 2025: 0.27 (5), 2026: 0.02 (4) | fail: holm_p_lt_0.05, ci_above_0, not_one_coin, coins_majority_positive, beats_random, asof_universe_positive |
| 1w>1d breaker|idm=none|sweep=0|limit|stop=poi|r2 | 1.969 (2) | 0.088 [-0.706, 0.835] (n 14) | 4/9 | -0.225 (NEARUSDT) | 4.80 | -0.046 (0.40) | 0.071 | -0.327 | -0.089 (13) | 2021: – (0), 2022: – (0), 2023: – (0), 2024: 1.22 (4), 2025: 0.54 (6), 2026: -0.49 (6) | fail: holm_p_lt_0.05, ci_above_0, not_one_coin, coins_majority_positive, beats_random, asof_universe_positive, folds_positive |
| 1w>1d ob|idm=none|sweep=1|limit|stop=poi|liq | 0.195 (29) | 0.033 [-0.812, 1.643] (n 18) | 5/16 | -0.416 (FILUSDT) | 16.49 | 0.181 (0.55) | 0.030 | -0.207 | -0.119 (19) | 2021: -0.52 (2), 2022: 0.79 (7), 2023: -0.55 (10), 2024: 0.63 (8), 2025: 0.54 (12), 2026: -0.98 (5) | fail: holm_p_lt_0.05, ci_above_0, not_one_coin, coins_majority_positive, beats_random, asof_universe_positive, folds_positive |

## References (original model, same universe)

| variant | n | coins | hit | gross R | fees R | funding R | net R | net (all-taker) | z | Holm p | PF | maxDD R | hold d | /coin/yr |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| IS 1d>1h any|idm=none|sweep=0|retest|stop=choch|liq | 268 | 20 | 0.321 | -0.093 | 0.060 | -0.002 | **-0.154** | -0.194 | -1.44 | – | 0.79 | 49.1 | 1.2 | 3.38 |
| OOS 1d>1h any|idm=none|sweep=0|retest|stop=choch|liq | 147 | 20 | 0.320 | -0.054 | 0.060 | 0.001 | **-0.113** | -0.152 | -0.74 | 1.000 | 0.84 | 32.7 | 1.3 | 3.60 |
| IS 1d>1h NOHTF | 7122 | 20 | 0.341 | 0.020 | 0.066 | 0.001 | **-0.044** | -0.086 | -1.27 | – | 0.94 | 455.4 | 1.4 | 89.74 |
| OOS 1d>1h NOHTF | 3620 | 20 | 0.342 | 0.036 | 0.067 | -0.001 | **-0.032** | -0.076 | -0.73 | – | 0.95 | 226.1 | 1.5 | 88.74 |
| IS 4h>15m any|idm=none|sweep=0|retest|stop=choch|liq | 1573 | 20 | 0.318 | -0.072 | 0.134 | 0.000 | **-0.206** | -0.291 | -4.73 | – | 0.74 | 334.0 | 0.3 | 19.82 |
| OOS 4h>15m any|idm=none|sweep=0|retest|stop=choch|liq | 758 | 20 | 0.309 | -0.098 | 0.128 | 0.002 | **-0.224** | -0.306 | -3.56 | 1.000 | 0.72 | 189.0 | 0.3 | 18.58 |
| IS 4h>15m NOHTF | 30986 | 20 | 0.328 | -0.011 | 0.139 | 0.001 | **-0.148** | -0.238 | -9.48 | – | 0.81 | 4692.5 | 0.3 | 390.42 |
| OOS 4h>15m NOHTF | 15298 | 20 | 0.327 | -0.013 | 0.147 | 0.001 | **-0.159** | -0.254 | -8.47 | – | 0.80 | 2547.8 | 0.3 | 375.01 |

## Lowest Holm p over the whole family (OOS n ≥ 10)

| variant | n | coins | hit | gross R | fees R | funding R | net R | net (all-taker) | z | Holm p | PF | maxDD R | hold d | /coin/yr |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1d>4h any|idm=none|sweep=0|limit|stop=poi|r2+be | 422 | 20 | 0.351 | 0.156 | 0.022 | 0.001 | **0.136** | 0.118 | 2.11 | 1.000 | 1.29 | 14.3 | 4.2 | 10.34 |
| 1d>4h any|idm=none|sweep=0|limit|stop=poi|r3 | 385 | 20 | 0.304 | 0.195 | 0.023 | 0.000 | **0.172** | 0.155 | 1.93 | 1.000 | 1.24 | 27.1 | 6.9 | 9.44 |
| 1d>4h ob|idm=none|sweep=0|limit|stop=poi|r3 | 251 | 20 | 0.323 | 0.236 | 0.011 | -0.001 | **0.224** | 0.215 | 1.90 | 1.000 | 1.33 | 25.4 | 10.2 | 6.15 |
| 1d>4h breaker|idm=none|sweep=0|limit|stop=poi|liq | 53 | 19 | 0.434 | 0.395 | 0.011 | -0.004 | **0.380** | 0.371 | 1.71 | 1.000 | 1.66 | 10.2 | 10.8 | 1.30 |
| 1d>4h any|idm=none|sweep=1|limit|stop=poi|r2+be | 251 | 20 | 0.363 | 0.159 | 0.020 | 0.000 | **0.140** | 0.122 | 1.59 | 1.000 | 1.28 | 11.5 | 4.3 | 6.15 |
| 1d>4h any|idm=none|sweep=1|limit|stop=poi|liq | 229 | 20 | 0.376 | 0.258 | 0.027 | -0.002 | **0.229** | 0.209 | 1.59 | 1.000 | 1.36 | 14.5 | 5.5 | 5.61 |
| 1d>4h any|idm=none|sweep=1|limit|stop=poi|r3 | 233 | 20 | 0.313 | 0.230 | 0.020 | -0.001 | **0.209** | 0.192 | 1.56 | 1.000 | 1.30 | 20.4 | 7.0 | 5.71 |
| 1d>4h any|idm=none|sweep=0|limit|stop=poi|r2 | 405 | 20 | 0.380 | 0.135 | 0.021 | 0.001 | **0.115** | 0.096 | 1.53 | 1.000 | 1.18 | 16.2 | 5.2 | 9.93 |
| 1d>4h ob|idm=none|sweep=0|limit|stop=poi|r2+be | 276 | 20 | 0.359 | 0.134 | 0.011 | 0.001 | **0.123** | 0.115 | 1.47 | 1.000 | 1.26 | 19.8 | 6.1 | 6.77 |
| 1d>4h ob|idm=none|sweep=1|close|stop=poi|r2+be | 36 | 17 | 0.528 | 0.385 | 0.011 | -0.007 | **0.367** | 0.366 | 1.37 | 1.000 | 1.92 | 5.1 | 19.3 | 0.88 |
| 1d>4h breaker|idm=none|sweep=0|limit|stop=poi|r2 | 58 | 19 | 0.431 | 0.262 | 0.010 | -0.001 | **0.251** | 0.241 | 1.37 | 1.000 | 1.43 | 8.2 | 6.9 | 1.42 |
| 1d>4h any|idm=none|sweep=1|limit|stop=poi|r2 | 244 | 20 | 0.381 | 0.140 | 0.019 | -0.000 | **0.121** | 0.103 | 1.31 | 1.000 | 1.19 | 14.6 | 5.3 | 5.98 |
| 1d>4h ob|idm=none|sweep=1|limit|stop=poi|r3 | 154 | 20 | 0.312 | 0.212 | 0.011 | -0.004 | **0.197** | 0.188 | 1.27 | 1.000 | 1.28 | 14.6 | 9.5 | 3.78 |
| 1d>4h any|idm=idm|sweep=1|limit|stop=poi|liq | 145 | 20 | 0.352 | 0.292 | 0.025 | -0.004 | **0.263** | 0.245 | 1.26 | 1.000 | 1.40 | 19.9 | 6.0 | 3.55 |
| 1d>4h any|idm=none|sweep=0|limit|stop=poi|liq | 382 | 20 | 0.361 | 0.165 | 0.028 | 0.000 | **0.138** | 0.118 | 1.25 | 1.000 | 1.21 | 16.5 | 5.4 | 9.36 |
