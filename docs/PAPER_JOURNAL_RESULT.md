# Paper journal (forward test) — result

Branch `claude/paper-journal` (from `claude/integration` @ c80e660). **Paper only. Not a signal.** No orders
are placed and no exchange keys are used: the journal reads public Binance klines and funding only.

## Why

The strongest variant in `docs/SWING_HTF_RESULT.md` was SMC 1d→4h with a resting limit at the POI edge,
the stop beyond the POI, a 2 R target and the stop moved to break-even after 1 R. Its OOS result was
+0.136 R net (n = 422, CI [+0.017, +0.263]), but in-sample was flat and the variant was chosen after
looking at the OOS data. It failed Holm and the yearly folds. The only honest check left is forward:
record each setup at the moment it becomes knowable, then let the market score it.

## What was built

| File | Role |
|---|---|
| `paper_strategies.py` | Strategy registry plus the pluggable interface. Version = sha256(frozen params JSON + code version). The code version hashes the source of `swing_model`, `smc`, `signal_features.atr` and the adapter. Built-in: `smc_1d4h_limit_poi_2r_be`. Universe = the 20 coins from SWING_HTF_RESULT §1 (`PAPER_UNIVERSE` overrides it). |
| `swing_model.py` | One additive change: `scan()` now also returns `state["resting"]`, the limit orders the fill loop would check on the next bar. It uses the same zones, eligibility and `_plan` as the fill loop, plus `knowable_ts`. Trades carry `be_i`. All existing scan tests are unchanged and pass. |
| `paper_journal.py` | Engine. `observe()` = one cycle for one coin on closed candles. It runs the backtest scanner, then reconciles the result with the store: warm-up tags, new pending setups, fills, BE moves, exits, expiries, costs and funding. `run_cycle()` / `replay()`. |
| `paper_store.py` | SQLite schema: `paper_strategies`, `paper_symbols` (per-coin cursor), `paper_setups`, `paper_events`, `paper_runs`, plus caches for `paper_klines` and `paper_funding`. |
| `paper_stats.py` | n, hit rate (Wilson CI), mean net R (bootstrap CI: month-block once trades span ≥ 3 months, per-trade before that), cumulative R, max drawdown, per-coin breakdown, comparison with the backtest. |
| `paper_data.py` | Binance `/fapi/v1/klines` (only closed bars, paged, cached) and `/fapi/v1/fundingRate` (cached per covered range). |
| `paper_api.py` | Read-only `GET /api/paper/strategies`, `/api/paper/trades?strategy=&state=&version=&symbol=`, `/api/paper/stats?strategy=&version=`, the `/paper` page, and the background worker. |
| `templates/paper.html`, `static/paper-journal.js` | Paper journal page, linked from the workspace top bar ("Paper ↗"). |

### Rules (identical to the backtest)

* **Recording.** A setup is recorded when the scanner first reports it as a resting limit. That happens
  on closed 4h bars only, with HTF (1d) bars closed by the 4h close. Each record keeps:
  * `knowable_ts`: the later of (a) the HTF close since which the zone has been eligible without a break and
    (b) the 4h close at which the previous trade released the coin. It is always a bar close.
  * `observed_as_of`: the last closed 4h bar the cycle saw.
  * `recorded_ts`: wall clock.
  * The planned entry (POI edge), stop, 2 R target, and an inputs snapshot (zone, zone confirmation, HTF
    close, 1d ATR, trend, dealing range, last 4h close).
  * `late = 1` when the setup was first observed after a later close than `knowable_ts`, i.e. the server
    was down when it appeared.
* **No backfill.**
  * Trades that closed before the start are never stored.
  * A fill whose setup was not already recorded (it appeared and filled while the journal was off) is not
    recorded as a trade. It goes to `paper_events` as `missed_fill`.
* **Warm-up.** On the first cycle per (strategy version, coin), every resting or open setup is stored
  with `seen_at_start = 1`. It is followed but excluded from all stats.
* **Lifecycle.**
  * `pending` → `filled` (limit touched) → `be` (stop at entry after 1 R).
  * Closed states: `target`, `stop`, `breakeven` or `timeout` (300 bars).
  * `pending` → `expired`: the zone was mitigated or invalidated, the HTF trend or premium/discount
    changed, or the coin entered another trade.
  * Every lifecycle decision comes from the scanner itself, including the conservative intrabar rule
    (stop first).
* **Costs.**
  * Maker 0.02 % on limit fills and target exits.
  * Taker 0.05 % + 0.01 % slippage on stop, BE and timeout exits.
  * Real funding from `/fapi/v1/fundingRate` over the holding time (a < t ≤ b), with longs paying positive
    rates.
  * If funding cannot be fetched, the trade stays at `fund_status = 'pending…'`. It is retried on the next
    cycle and is not counted until it resolves.
  * Results are reported both in R and in % of price (`net_pct`).

### Pluggable interface (for the trend/momentum research)

```python
import paper_strategies as ps
def my_strategy(candles_by_tf, params):   # {'D': [closed candles…], '240': […]} -> [setup, …]
    ...
ps.register("trend_v1", my_strategy, {"lookback": 20}, title="…", timeframes=("D",), lifecycle_tf="D",
            code=(my_module,), expectation={"oos_net_r": …, "oos_ci": […], "oos_n": …})
```

The setup dict is documented in the `paper_strategies` docstring: `key`, `status` (`resting`/`open`/`closed`),
`side`, `knowable_ts`, `entry`/`stop`/`target`, `entry_type`, `fill_ts`, `be_ts`, `exit_ts`, `exit_reason`,
`r` and `inputs`.

The function must be deterministic and causal. To load plugin modules, list them in
`PAPER_STRATEGY_MODULES=mod1,mod2`. Changing params or code produces a new version row; old versions and
their trades are never edited.

## Tests

`tests/test_paper_journal.py` (10 tests, ~25 s) and `tests/test_paper_api.py` (5) run on synthetic random
walks, with no network.

* **Replay consistency** (2 seeds; one ends with an open position). The journal is run bar by bar from a
  warm-up bar, and every cycle sees only candles closed by that bar. The results must equal the backtest
  scanner on the full window exactly:
  * the same closed trades (POI, side, fill, entry, stop, target, exit time and reason, gross R);
  * the same open position and the same resting set at the end;
  * the same fee and funding R as `scripts/backtest_swing_universe.costs` on the same rates;
  * no `missed_fill`.
* **No look-ahead.** Every non-warm-up setup is recorded at the bar close where it became knowable
  (`knowable_ts == observed_as_of`). Fills happen at or after that close, at the edge or better.
* **Poisoned future.** Corrupting every candle after a cut leaves the journal identical up to the cut.
* **Restart.** Re-running the same bar adds no rows. A new process on the same file resumes and ends
  identical to an uninterrupted run, with `started_as_of` kept.
* **Downtime.** A 400-bar gap processed in one cycle records no trade retroactively; fills inside the gap
  become `missed_fill` events.
* **Versioning.** Changing params creates version 2, and version 1 stays unchanged.
* **Warm-up.** Warm-up rows are excluded from the stats.
* **Statistics.** Wilson CI, equity curve/drawdown and bootstrap are checked.
* **API and worker.** Routes are read-only (POST → 405). The worker is off by default and starts once per
  process and per DB file (flock). The 4h-close scheduler, closed-only klines and the funding cache are
  covered.
* `tests/paper_journal.test.cjs` (6): view-model, escaping, labels, and the "too few trades" verdict.

## Live checks (run on :5104 with PAPER_JOURNAL=1, scratch DBs)

All runs used scratch databases, and the user's :5087 server was not touched. Times are UTC, 2026-10-18.

**The worker on :5104.**

* Started 10:41. The first cycle did a cold fetch of 264 k 4h and 44 k 1d native klines for 20 coins. It
  took 146 s and recorded **88 warm-up setups**, all tagged `seen_at_start`:
  * 86 resting limits;
  * 2 open positions: BTC long FVG with the edge at 80 802.6, filled 2026-10-08 16:00, and SOL long OB
    with the edge at 102.83, filled 2026-10-13 00:00.
* 16 cycles ran (15–18 s each, 0 errors), including the **`bar_close` cycle at 12:00:30**, 30 s after the
  12:00 4h close. Every coin's cursor advanced to the 12:00 close.
* The 12:00 close produced no new setup and no expiry. That is a real result: nothing was recorded, and
  nothing was invented to fill the gap.
* There are 0 `missed_fill` events and 0 duplicate rows across the 16 cycles. Counted trades: 0, so
  there is nothing to conclude yet.

**Setups against fresh venue klines.** `pj_verify.py` (in scratch) fetches `/fapi/v1/klines` independently
of the cache and checks every recorded row. Result: **88 rows, 794 checks, 0 failures**. The checks:

* OB zones equal the high/low of the 1d OB candle, and that candle has the right colour.
* FVG zones equal the high of bar t−1 and the low of bar t+1 (mirrored for shorts).
* The entry is the POI edge, the stop is beyond the zone, and the target is exactly 2 R.
* `knowable_ts` ≥ zone confirmation ≥ the POI bar close, and it is a 4h close.
* `recorded_ts` > `observed_as_of` ≥ `knowable_ts`.

The check was run after cycle 1 and again after the 12:00 cycle.

**Real-data replay against the backtest.** The journal was replayed bar by bar over the last 1 200 4h bars
(from 2026-04-01) for BTC, ETH, SOL, XRP and DOGE, on the backtest's own cached data. It produced
**21 closed trades, identical to the backtest grid rows of this variant** in fill, exit, reason, gross R,
fee R and funding R, with 0 missed fills.

**Native klines against the backtest's aggregation.** The scanner on the journal's native 4h/1d klines gives
**the same 422 OOS trades** as the backtest (422 = 422, all identical), and the same resting set on 20/20 coins.

**Screenshots** (inspected) are in `docs/evidence/paper-journal/`:

* `live-5104-after-12utc-close.jpg`: the live state, with 0 counted trades and the 88 warm-up setups collapsed.
* `replay-demo-sol-xrp-doge.jpg`: the same page on the **replay** DB from the check above. It shows a
  filled equity curve and the trade tables. These are not live trades: 17 counted, mean +0.148 R, CI
  [−0.48, +0.61].

## How to enable

* **On :5087 (the user's checkout).** Set `PAPER_JOURNAL=1` in the environment of the launcher before
  `import screener`, for example `os.environ['PAPER_JOURNAL'] = '1'` in
  `crypto-screener-ui-launch.py`. Optionally set `PAPER_DB_PATH`. Then restart. The first cycle downloads
  about 15 k 4h bars per coin (≈ 2.5 min for 20 coins); later cycles take ≈ 15 s. Open `/paper`.
* **Contabo (future deploy).** Add `PAPER_JOURNAL=1` and `PAPER_DB_PATH=/var/lib/crypto-screener/paper_journal.db`
  (persistent disk) to the systemd unit / `.env`. Run one app process; the flock already prevents a
  second worker on the same file. On Railway the file system is ephemeral, so a volume is needed or the
  journal restarts its warm-up after every deploy.
* **Optional settings.**
  * `PAPER_INTERVAL_MIN` (default 15).
  * `PAPER_CLOSE_DELAY_S` (default 30 s after each 4h close).
  * `PAPER_UNIVERSE` (comma-separated).
  * `PAPER_STRATEGY_MODULES`.

### Config changes for review

* New env vars, all off or unchanged by default: `PAPER_JOURNAL`, `PAPER_DB_PATH`, `PAPER_INTERVAL_MIN`,
  `PAPER_CLOSE_DELAY_S`, `PAPER_UNIVERSE`, `PAPER_STRATEGY_MODULES`.
* **The default DB path is a new file `paper_journal.db` next to `DB_PATH`, created lazily on the first
  `/api/paper/*` request (or by the worker) — please review.** Use `PAPER_DB_PATH=` (empty) for in-memory.
* New SQLite tables, in that new file only. `signals.db` is untouched.

## Portfolio strategies and the whale observer (kind `portfolio` / `observer`)

The journal now runs more than the SMC setup strategy. `paper_strategies.register(..., kind=...)`
accepts two new kinds, and old databases migrate in place: the `kind` column defaults to `setup`, and
the SMC version hash is unchanged.

* **`portfolio`** (`paper_portfolio.py`). This kind is for the daily, multi-coin research finalists,
  registered by `paper_portfolio_strategies.py` with the frozen params of
  `docs/evidence/strategy-research/dev_selection.json`:
  * **A** `pf_trend_ema50_long_k5` (`A:ema50|long|k5`): close > EMA50 → long at the next open,
    5×ATR20 chandelier trail resting intraday, exit on close < EMA50, 1 % risk per position, cap 0.30,
    gross ≤ 2, 20 coins.
  * **D** `pf_funding_ts_w90_h7_trend` (`D:ts|W90|H7|trend`): fade a 90-day funding z-score beyond
    ±2 in the EMA50 trend direction, hold 7 days, vol-sized, gross ≤ 1.5.
  * **Each cycle:**
    * The as-of day is the last UTC day closed for every coin, at least 10 minutes after its close.
      Everything later is cut off, whatever the loader returned.
    * The strategy is replayed with `portfolio_engine.run` from `start_day` with `decide_last=True`.
      So the orders for the next open are exactly what the engine decided at this close.
    * New days are appended and never rewritten: equity relative to 1.0 at the start, fees, real
      Binance funding, turnover, gross, BTC close and the next-open orders.
    * A recomputed past day that differs (a revised bar or funding print) is logged as a
      `divergence` event. Days first written more than a day after their close are flagged `late`.
    * Positions (`paper_pf_positions`) are one row per engine trade, with entry at the next open,
      exit price and reason, P&L, fees and funding. Open rows are re-marked every cycle.
    * Between closes the cycle returns without any venue request.
  * **Warm-up rule:** the strategy **starts flat at the first daily close** it sees.
    * A: every coin starts disarmed. A long needs a fresh signal, i.e. a close ≤ EMA50 and then a
      close > EMA50, so trends already running at the start are not bought.
    * D: its signals are one-day funding events, so the first close's events are new and tradable.
  * **/paper** shows a tab per strategy. For a portfolio strategy it shows:
    * forward equity in % against BTC buy-and-hold from the same start close;
    * the backtest band: the development drift ± 1.96 σ √days from the dev CAGR and vol;
    * max DD, vol, Sharpe, fees and funding;
    * the next-open orders, and the open and closed positions.
  * API: `/api/paper/portfolio?strategy=…`.
* **`observer`** (`whale_observer.py`, only when `WHALE_RECORDER=1`). It logs hypothetical follow trades
  after recorded Hyperliquid whale opens and flips. The rules and the pre-registration are in
  `docs/WHALE_RECORDER.md`. **No edge is claimed.** API: `/api/paper/observer?strategy=…`.

### Tests

* `tests/test_paper_portfolio.py` (9 tests):
  * **Replay consistency:** a journal run once per daily close over 80 synthetic days equals one
    `portfolio_engine` run over the same window **exactly** (`==` on equity, fees and funding), and
    has the same positions.
  * Every fill was announced in the previous close's orders.
  * **No look-ahead:** the loader returns poisoned future bars and funding, and the journal is
    unchanged. The unsettled 00:00 close is not used.
  * **Restart and idempotency:** a gap of 3 missed cycles plus a process restart gives the same days
    and positions, with exactly the late days flagged. The same cycle run twice writes nothing.
  * Warm-up flat with a fresh EMA cross for A, the `kind` migration of an old DB, the frozen params
    against `dev_selection.json`, and no fetching between closes.
* `tests/test_carry_api.py` covers the portfolio and observer routes.
* `tests/carry_portfolio_views.test.cjs` covers the view models: summary, band chart, positions,
  tabs, dispatch and the observer.

### Live run (:5105, scratch DBs, 2026-10-18 14:23–15:10 UTC)

* **Worker:** started 4 strategies (SMC, A, D, observer). All cycles reported `ok = 20/20`, no errors.
  * Steady cycles took ≈ 13 s each for A and D (they reload 20 coins' 1d klines and funding from the
    local cache) and ≈ 15 s for SMC.
  * A and D started flat at the 2026-10-17 close (`start_day` = 1792195200000) and recorded day 0
    with equity 1.0 and no orders.
  * The next daily close (2026-10-18) was not reached during the run, so **no live forward day
    exists yet**.
* **A/D against `portfolio_engine` on the real data** (`scripts/paper_portfolio_replay.py`, run on
  the live run's own cached native Binance 1d klines and funding):

  | | days | journal == engine (exact) | positions | forward 2026-01-01 → 10-17 | BTC same window | research holdout |
  |---|---|---|---|---|---|---|
  | A | 290 | **true** | 205 (197 closed) | +22.03 % | −8.27 % | +21.69 % |
  | D | 290 | **true** | 42 (40 closed) | +11.84 % | −8.27 % | +11.80 % |

  * The replay starts each strategy flat on 2026-01-01 and calls the journal once per close.
  * The small gap to the research holdout has two sources: A's warm-up rule (disarmed at the start),
    and native 1d klines instead of 1h-aggregated bars. Over the full history the engine ends at
    9.446× (native) vs 9.456× (research cache) for A, and 2.027× vs 2.029× for D.
  * This replay is a consistency check on past data, **not** a forward result.
* **Whale recorder:** 16 polls at a 2-min interval (one full poll), 50 addresses, 0 errors, across the
  restarts.
  * It recorded 959 events (358 add, 523 reduce, 78 flip, 0 gaps) and 1,236 position rows.
  * `whales.db` grew from 290 KB after 4 polls to 650 KB after 16.
  * At 5-minute polls, expect a few MB per day while wallets are this active.
  * Against a fresh `clearinghouseState` fetched 3–4 s after the recorder's poll, for 3 accounts:
    * two quiet accounts matched **52/52** and **28/28** positions exactly: size, entry and leverage;
    * the most active account (224 events in 37 min) matched 78/86. The 8 differences are size
      changes made in the seconds between the two requests; entry and leverage were equal or moved
      by the new fill.
* **Observer:**
  * Version 1 followed 50 events in its first minutes, **all from one wallet** that flipped six coins
    65 times. Version 2 adds a 24-hour (wallet, coin) de-duplication. Version 1 was retired before
    any follow trade closed (150 rows, all still open).
  * Version 2 started at 15:00 UTC. Within 8 minutes it followed 11 events (33 rows), still all from
    the same wallet, now at most once per coin per day. The pre-registered test therefore requires
    ≥ 30 distinct wallets.
* **Screenshots** (scratch, `~/.hermes/cache/scratch/cw-live/shots/`): `paper-trend-A.png`,
  `paper-funding-D.png`, `paper-observer.png`, `carry.png`.

### How to enable on :5087 (the user does this; the launcher was not changed)

The journal is already enabled there (`PAPER_JOURNAL=1`), so A and D start flat at the first daily
close after the restart, with no further setting. For the whale recorder and observer, add these lines
to `crypto-screener-ui-launch.py` before `import screener`, then restart:

```python
os.environ['WHALE_RECORDER'] = '1'
# optional: os.environ['WHALES_DB_PATH'] = '/path/to/whales.db'   (default: whales.db next to DB_PATH)
# optional: os.environ['WHALE_INTERVAL_MIN'] = '5'
```

### Config changes for review (this round)

* New env vars, all off or unchanged by default: `WHALE_RECORDER`, `WHALES_DB_PATH`, `WHALE_INTERVAL_MIN`,
  `WHALE_FULL_EVERY_MIN`, `WHALE_LEADERBOARD_TTL_MIN`.
* **The default `WHALES_DB_PATH` is a new file `whales.db` next to `DB_PATH`.** It is created only
  when the recorder runs or on the first `/api/whales/recorder` request — please review.
* New tables in `paper_journal.db`: `paper_pf_state`, `paper_pf_days`, `paper_pf_positions` and
  `paper_follow`, plus the column `paper_strategies.kind` (an ALTER with a default, so old DBs keep
  working).
* With `PAPER_JOURNAL=1`, A and D are now registered by default (built-in plugins), and the observer
  is registered only with `WHALE_RECORDER=1`.
* `paper_data.funding_rows` now fetches only the uncovered tail of the cache, and keeps the cover
  2 minutes behind the clock so a late print is fetched again.

## Known limits

* The live journal uses native Binance 4h/1d klines, while the backtest aggregated 1h klines.
  * Across all 20 coins, 41 of 263 k 4h bars and 3 of 44 k 1d bars differ in OHLC, and some 4h buckets that
    the backtest dropped exist natively (venue incidents around 2023-11-10 12:00 and 2024-10-28 20:00 UTC, plus early XRP bars in 2020-01 and a DASH bar on 2025-01-29).
  * Re-running the scanner on the native bars still gives the same 422 OOS trades as the backtest and an
    identical resting set on all 20 coins (see the live checks).
* A missed cycle (server down) can only lose setups, never invent them. Setups that appear and fill
  while the server is down are logged as `missed_fill`; setups recorded late are flagged `late`.
* Funding is fetched when a trade closes. If the venue cannot be reached, the trade's net R stays empty
  until a retry succeeds.
* The scanner allows several resting limits per coin; the first one touched fills, as in the backtest.
  Most of them later expire.
* Portfolio strategies recompute their indicators over the full history since 2020 and replay from
  the start day at every new close (deterministic, append-only). On the real data this took ≈ 3 s per
  strategy per close (the 290-close replay of A and D took 28 min). Cycles between closes do no work.
* 30–50+ counted trades are needed before any conclusion. At the backtest's rate (422 trades in two years
  on 20 coins), that takes about 2–3 months.
