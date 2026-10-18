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
* 30–50+ counted trades are needed before any conclusion. At the backtest's rate (422 trades in two years
  on 20 coins), that takes about 2–3 months.
