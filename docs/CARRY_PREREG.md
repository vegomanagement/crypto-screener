# Pre-registration — delta-neutral funding carry (analytics, research only)

Written and committed BEFORE `scripts/carry_analysis.py run` touched any carry P&L. After that commit
this document is frozen; deviations go to §8 with their reason, never edited in place. No orders are
placed by anything in this study; it reads public venue history only.

This is analytics of a known premium (perp funding), not signal discovery, so there is no sealed
holdout gate. The same split as `docs/STRATEGY_PREREG.md` is still reported: **development**
2020-01-01 … 2025-12-31 per year, and **2026-01-01 … the last closed day** as a separate period.
Every number is reported as it comes out.

## 1. Data

- Universe: the 20-coin main universe (BTC, ETH, SOL, ZEC, XRP, DOGE, BNB, 1000PEPE, SUI, NEAR, ADA,
  LINK, WLD, AVAX, UNI, FIL, BCH, LTC, AAVE, DASH), USDT-M perps.
- Funding, Binance: `/fapi/v1/fundingRate`, every print (the cache of the strategy research,
  `~/.hermes/cache/scratch/strategy-research/bars.json.gz`, re-fetched if pruned).
- Funding, Bybit: `/v5/market/funding/history` (linear), full history. Hyperliquid: `fundingHistory`
  (hourly) from its launch (2023-05) for the coins it lists (`kPEPE` for 1000PEPE).
- Perp daily bars: Binance USDT-M (the same cache, UTC days aggregated from 1h). Spot daily bars:
  Binance spot `/api/v3/klines` 1d (`PEPEUSDT` × 1000 for 1000PEPE). Bybit linear and Hyperliquid
  perp 1d klines for the cross-venue variants.
- Annualisation: sum of prints in a window × 365 days / window length (calendar days actually
  covered). Prints are summed as published (8 h, 4 h or 1 h intervals).

## 2. Descriptive analytics (no trading rule)

Per coin and venue, per calendar year and for dev / 2026: gross funding yield (annualised), share of
negative prints and of negative days, and the worst drawdown of cumulative funding (peak-to-trough
of Σ rate, % of notional) with its dates.

## 3. Execution model (all variants)

- Delta-neutral pair per coin: long spot q units + short perp q units (same units → 1:1 hedge in
  units; 1000PEPE converts by 1000). Decision at the UTC close of day t on data ≤ t; both legs fill at
  the OPEN of t+1 (spot open, perp open).
- Capital at short-leg margin L ∈ {1, 2, 3}: a coin slice of equity E·w is split into spot notional
  E·w·L/(L+1) and perp margin E·w/(L+1). Base case **L = 1**. Return on TOTAL capital (both legs).
- Costs per side: spot taker **0.10 %** (sensitivity 0.075 % with BNB), perp taker **0.05 %**
  (Bybit 0.055 %, Hyperliquid 0.045 %), plus 0.01 % slippage per leg.
- Funding: the short leg receives rate × q × perp close for every print with open < time ≤ close.
- Basis: both legs are marked at their own closes, so the spread change between entry and exit
  is in the P&L (mark-to-market of perp − spot).
- Rebalancing: at each close, when a coin's perp-leg equity (margin + uPnL + funding) leaves
  [0.5, 1.5] × its target E·w/(L+1), the slice is re-split to target at the next open (both legs
  traded for |Δq|, fees on both). Target-weight changes trade the same way.
- Liquidation: if the day's perp high takes the perp-leg equity below maintenance (0.5 % of
  notional for BTC/ETH, 1 % for the rest), the perp leg is lost (equity → 0 on that leg) and the
  spot leg is sold at the next open. Counted and reported.
- A coin without a spot or perp bar that day cannot be entered; an open pair whose data ends is
  closed at the last common close.

## 4. Variants (11; the trial count for any later reading)

| id | rule |
|---|---|
| V1 `AO-BTC` | always on BTC, w = 1 |
| V2 `AO-ETH` | always on ETH, w = 1 |
| V3 `AO-EW` | always on every coin with spot+perp data, equal weight, re-targeted on the 1st of each month |
| V4 `TOP3-30` | every Sunday close: the 3 coins with the highest trailing 30-day Binance funding sum (only if > 0), w = 1/3 each |
| V5 `TOP5-30` | same with 5 coins, w = 1/5 |
| V6 `T7-10/5` | per coin, fixed slice w = 1/20: enter when trailing 7-day annualised funding > 10 %, exit when < 5 % (hysteresis), daily check |
| V7 `T7-20/10` | same, enter > 20 %, exit < 10 % |
| V8 `T30-10/5` | trailing 30-day, enter > 10 %, exit < 5 % |
| V9 `T30-20/10` | trailing 30-day, enter > 20 %, exit < 10 % |
| V10 `XV-BN-BY` | BTC and ETH (w = 1/2 each): short the perp on the venue with the higher trailing 7-day funding (Binance vs Bybit), long it on the other; enter when the annualised difference > 10 %, exit < 5 %; each perp leg margined at 1x (capital 2× notional) |
| V11 `XV-BN-HL` | same with Binance vs Hyperliquid (from 2023-05) |

Cross-venue extra risks (reported, not modelled): two counterparties, the two perps' prices can
diverge (only daily closes are marked), funding intervals differ (8 h vs 1 h), withdrawals can halt.

## 5. Metrics

Per variant: CAGR on total capital, vol, Sharpe (daily, ×√365), max drawdown, funding/yr, fees/yr,
basis P&L/yr, rebalances and liquidations, average deployment, per-year returns 2020…2025 and 2026.
Risk table: per coin the largest 1-day, 7-day and 30-day perp pumps (high over the window ÷ the
previous close − 1) in dev and in 2026, and which L ∈ {1, 2, 3} a short leg survives without a top-up
(pump < 1/L − maintenance). Capital efficiency: return on total capital at L = 1, 2, 3 for V1/V2.
Venue/counterparty risk: qualitative.

## 6. Live panel

`/carry`: current annualised funding per coin per venue, trailing 7/30/90-day Binance carry, % of
negative prints over 90 days and an **estimated** net APR = trailing-30-day annualised gross × L/(L+1)
− round-trip cost amortised over a 30-day hold, labelled as an estimate (past funding does not
predict future funding).

## 7. What would count as a finding

Nothing here is a trading spec. A variant is "worth a forward paper test" only if its net return
on total capital is positive in at least 5 of the 6 dev years AND in 2026, and its worst drawdown is
smaller than its annual net return.

## 8. Deviations

(none yet)
