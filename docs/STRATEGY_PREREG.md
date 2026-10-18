# Pre-registration — portfolio-level crypto strategies (A, A+SMC, B, C, D)

Written and committed BEFORE any of these strategies was run on the data. After that commit the
document is frozen; every deviation is logged in §9 with its reason, never edited in place.

Context: ~1,600 entry-pattern variants (SMC, Wyckoff, VWAP/value area, CVD, …) failed out of
sample in earlier work (`docs/SWING_HTF_RESULT.md`, `docs/MTF_SYNC_RESULT.md`). This study tests
documented market effects (time-series trend, cross-sectional momentum, regime filters, funding
crowding) as daily-rebalanced portfolios.

## 1. Data and split

- Universe (reused, chosen by data in `scripts/swing_universe_data.py`): **main** = BTC, ETH, SOL,
  ZEC, XRP, DOGE, BNB, 1000PEPE, SUI, NEAR, ADA, LINK, WLD, AVAX, UNI, FIL, BCH, LTC, AAVE, DASH
  (USDT-M perps; today's liquid coins → survivorship-biased). **as-of** = the list ranked as of
  2024-10-03 with settled contracts included (BTC, ETH, SOL, DOGE, XRP, BNB, AVAX, 1000SHIB, LINK,
  TRB, ADA, FIL, BCH, NEAR, LTC, FTM, DOT, ETC, GALA, UNI) — survivorship check only.
- Bars: Binance USDT-M 1h klines (cached data.binance.vision zips) aggregated into complete UTC
  1d and 4h buckets. A coin enters the portfolio when it has a bar; history starts 2020-01 or at
  listing. A delisted coin's position is closed at its last close (taker + slippage).
- Funding: real `/fapi/v1/fundingRate` history per coin; 0.01 %/8 h against the position only
  where the venue has no history.
- **Development** = 2020-01-01 … 2025-12-31. Walk-forward folds inside it (expanding, §6).
- **Sealed holdout** = 2026-01-01 … the last closed day (2026-10-17). Development runs load data
  truncated at 2025-12-31 23:59 UTC. The holdout is opened ONCE by `backtest_portfolio.py
  holdout`, for the frozen finalists only (§7), which writes a sentinel; numbers are reported
  whatever they are. Note: earlier SMC studies looked at 2024-10 … 2026-10 data, so the holdout
  is unseen for these strategies, not for the venue history as such.

## 2. Execution and costs (all strategies)

- Decisions at the daily close of day t use only bars closed by t; orders execute at the OPEN
  of t+1 (market) or rest during t+1 (limit/stop). No same-bar look-ahead.
- Market and stop fills: taker 0.05 % + slippage 0.01 % per side on traded notional. Limit
  fills: maker 0.02 %, no slippage. Stop: long exits at min(open, stop) when low ≤ stop; short
  mirror. A limit filled on a day whose range also reaches the stop counts as stopped (worst case).
- Funding: every perp position pays/receives each funding event with day-open < time ≤ day-close
  (the 08:00, 16:00 and next 00:00 prints), cash flow = −sign(position) × rate × |qty| × close.
  Longs pay positive rates, shorts receive them (and vice versa). A position stopped intraday pays
  the events of the first half of that day only.
- Linear USDT accounting: equity = cash + Σ qty × close; cash earns nothing; no liquidation model,
  leverage bounded by the gross caps below. Weights are fractions of the previous close's equity;
  quantity = weight × equity / fill price.

## 3. Strategies and parameter grids

### A. Time-series trend, per coin, 1d (16 variants)

- Entry ∈ {EMA50, EMA100, Donchian 20, Donchian 55} × side ∈ {long-only, long/short} × trailing
  stop k ∈ {3, 5} ATR = **16**.
- EMA entry: close > EMA(n) while flat and *armed*; a long is disarmed by a stop-out and re-armed
  by a close ≤ EMA (short mirror). Donchian entry: close > the highest high of the previous n days
  (short: close < lowest low).
- Exit (whichever first): chandelier trail = highest high since entry − k × ATR20 (simple mean
  true range of 20 days), ratcheting only, resting as a stop for the next day; or the opposite
  signal at the next open: EMA close < EMA(n); Donchian close < lowest low of the previous n/2 days
  (10 / 27 days; short mirror). In long/short an opposite entry signal flips the position.
- Size at entry, held unchanged until exit (no pyramiding, no resizing): weight = 1 % equity risk
  / (k × ATR20 / close), capped at 0.30 per coin; new entries are scaled down so gross ≤ 2.0.

### A+SMC. Same 16 signals, SMC limit execution (32 variants)

- At the A signal close: the nearest unmitigated 4h bullish OB or FVG (smc.py 5/5 external
  structure, `swing_model.htf_arrays` zones, `smc.zone_state` semantics as of the 4h bar closed at
  that daily close) whose top ≤ the 4h dealing-range equilibrium (discount), top < close and top
  > A's initial stop. Limit buy at the zone top, valid for the next 2 daily bars (= 12 × 4h bars).
  Shorts mirror (bearish zones in premium, limit sell at the zone bottom).
- Cancelled when A's rules would have exited the virtual position (opposite signal or a close
  below the virtual trail) before the fill. No eligible zone or no fill after 2 days → fallback ∈
  {market at the next open if A still holds, skip} → 16 × 2 = **32**.
- Same sizing (weight fixed at the signal close), same chandelier trail and exits as A after the
  fill. Question answered by the paired difference A+SMC − A per signal variant.

### B. Cross-sectional momentum, weekly (18 variants)

- Rank at the Sunday close (executed Monday open) by L-week return close_t / close_{t−7L} − 1,
  L ∈ {2, 4, 8}; eligible = coins with ≥ max(7L + 1, 60) daily bars. Hold top K ∈ {3, 5}.
- Mode ∈ {long-only; long-only + absolute filter (a top-K coin is held only if its own L-week
  return > 0, its slot stays in cash); long/short (long top K, short bottom K)} → 3 × 2 × 3 = **18**.
- Inverse-vol weights 1/σ20 (stdev of daily log returns, 20 days) normalised to a long book of
  1.0 (long/short: 0.5 long, 0.5 short). Weekly rebalance to target; trades with |Δw| < 0.01 skipped.
- (Risk-adjusted / BTC-relative ranking: not tested — relative-to-BTC ranks are identical to raw.)

### C. Regime filter (12 variants)

Regimes, computed at the close from BTC and the universe (undefined before warm-up → allowed):
- **R1 trend**: BTC close > SMA200 → longs allowed, shorts blocked; else longs blocked, shorts allowed.
- **R2 vol**: BTC 30-day realised vol (stdev of daily log returns) percentile within its trailing
  365 daily values ≤ 0.80 → all allowed; else all blocked.
- **R3 funding**: F = cross-coin mean of each coin's 7-day funding sum; percentile in its trailing
  365 values > 0.80 → longs blocked; < 0.20 → shorts blocked.
- **R4** = R1 ∧ R2.
Blocked side: open positions of that side close at the next open, no new entries (A: blocks do
not disarm; Donchian needs a new breakout; B: the book is rebuilt from the latest weekly ranking at
the next open after a regime change). Applied to 3 bases: best A (dev Sharpe), best B (dev
Sharpe), and the SMC variant `1d>4h any|idm=none|sweep=0|limit|stop=poi|r2+be` from
`docs/SWING_HTF_RESULT.md` (trades from the cached grid; a trade is kept when its side is allowed
at the last daily close before its signal; portfolio = 0.5 % equity risk per trade, P&L booked on
the exit day). 4 × 3 = **12**, plus the unfiltered SMC base itself = 1.

### D. Funding-extreme contrarian (18 variants)

- Daily funding f_d = sum of the coin's funding prints in (open_d, close_d]; z = (f_d − mean) / sd
  over the last W days, W ∈ {30, 90}.
- **Time-series** (12): z > +2 → short, z < −2 → long, entered at the next open, held H ∈ {1, 3, 7}
  days (exit at the open H days later), new signals ignored while held; trend filter ∈ {off, on:
  long only when close > EMA50, short only when close < EMA50}. Weight = min(0.25, 0.005 / σ20),
  gross ≤ 1.5. 2 × 3 × 2 = 12.
- **Cross-sectional** (6): every H days rank by z, short the 3 highest, long the 3 lowest, equal
  weight, 0.5 per side; W ∈ {30, 90} × H ∈ {1, 3, 7} = 6.
- Limitation: no long-history OI or top-trader ratios exist, only funding plus price/volume.

**Total trials N = 16 + 32 + 18 + 13 + 18 = 97** (used for the deflated Sharpe and Holm).

## 4. Benchmarks and controls

- BTC buy-and-hold (spot-like: one taker entry, no funding) — the main benchmark.
- Equal-weight buy-and-hold of the available main-universe coins, rebalanced on the 1st of each
  month (taker + slippage on rebalances, no funding).
- Random entries with the same exposure (20 seeds, crc32 seeds): A / A+SMC / D — every real trade
  (coin, side, holding days, entry weight) moved to a uniformly random start day of the same coin
  in the same period and run through the same engine (fees, funding); B — the same weighting with
  K random eligible coins each week. C on the SMC base — the base vs its filtered version.

## 5. Metrics (every variant)

CAGR, annualised vol (√365), Sharpe, Sortino, max drawdown and its duration (days from peak to
recovery or to the end), Calmar, annual turnover (Σ|Δnotional| / equity), cost drag (fees and
funding as % of equity per year), trade hit rate, average win / loss in R (R = trade P&L /
(entry notional × initial risk fraction); risk fraction = stop distance / price for A, A+SMC; one
ATR20 / price for B, D), the share of total P&L from the top third of trades and from the top
coin, per-year returns.
Tests: month-block bootstrap (2,000 resamples of calendar months) 95 % CIs for the Sharpe and the
mean daily return; one-sided p of mean daily return > 0 from the month-cluster z; Holm over all 97
trials; probabilistic Sharpe and the deflated Sharpe ratio (Bailey & López de Prado) with
N = 97 and the variance of the trials' daily Sharpe ratios.

## 6. Selection and walk-forward (development only)

- Per idea (A, A+SMC, B, C, D) the finalist = the variant with the highest development Sharpe.
- Expanding walk-forward per idea: for each fold year 2022, 2023, 2024, 2025, pick the variant
  with the best Sharpe on all development days before the fold, record its fold returns; the
  stitched fold series is the idea's walk-forward result. Each fold is also reported for the finalist.

## 7. Pass criteria (a finalist passes only if ALL hold)

1. Development Sharpe > 0 and deflated Sharpe (N = 97) ≥ 0.95.
2. Holm-adjusted p < 0.05 for mean daily return > 0 on development.
3. Not concentrated: development P&L without the top coin > 0 and the top coin ≤ 50 % of net P&L.
4. Beats BTC buy-and-hold risk-adjusted on development: Sharpe AND Calmar above BTC's.
5. Real development Sharpe above the 95th percentile of its 20 random-entry seeds.
6. The idea's stitched walk-forward return > 0.
7. Holdout: net return > 0 and Sharpe above BTC buy-and-hold's Sharpe in the holdout.
Reported, not required: the as-of universe run (survivorship), equal-weight benchmark, per-year
returns. Up to 5 finalists go to the holdout; nothing else is ever run on holdout data.
If a finalist passes, a live dashboard / paper-trading spec is written (no UI in this branch).

## 8. Implementation

`portfolio_engine.py` (engine, stdlib) with unit tests (no look-ahead, funding sign, vol sizing,
stops, rebalancing costs, poisoned future), `portfolio_strategies.py`, `portfolio_stats.py`,
`scripts/backtest_portfolio.py {data|dev|select|holdout|report}`. Results:
`docs/evidence/strategy-research/` (JSON < 300 KB, SVG curves), `docs/STRATEGY_RESEARCH_RESULT.md`.

## 9. Deviations log

(none yet)
