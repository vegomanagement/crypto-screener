# Hyperliquid whale-position recorder (forward-test data)

Research only. Nothing here places orders or claims an edge. The recorder collects data for a
**future** test that is pre-registered in §5. That test may only be read after the data exist.

## 1. What it records

- **Who:** the 50 accounts with the largest account value on the public Hyperliquid leaderboard
  (`stats-data.hyperliquid.xyz/Mainnet/leaderboard`), as already tracked by
  `hl_whale_tracker.WhaleTracker`. These are "Hyperliquid top-50 accounts by account value", not
  all whales. Each leaderboard snapshot also stores rank, account value and the day / week / month /
  all-time PnL windows (`whale_leaderboard`).
- **Positions:** every `WHALE_INTERVAL_MIN` minutes (default 5), `clearinghouseState` for each
  address: coin, signed size `szi`, entry price, position value, leverage, unrealised PnL and
  liquidation price. Values are stored as exact decimal text (`whale_positions`). To keep the file
  small over months, a poll writes a row only when the size changed (or the position closed:
  `szi = '0'`), plus every position on a **full** poll (default hourly, `WHALE_FULL_EVERY_MIN`, with
  uPnL). `whale_state` holds the latest book per address, `whale_seen` the last poll each address
  answered, and `whale_polls` the poll metadata, including which addresses answered (`ok_json`).
- **Events** (`whale_events`) are derived only from two consecutive answers of the **same**
  address: `open`, `add`, `reduce`, `close` or `flip`, with `from_szi → to_szi`, the side after the
  change, the new entry price and the notional of the change.
  - `knowable_ts` is the poll's finish time, the earliest moment we could have known about the
    change. `prev_ts` is the previous answer, so the change happened in `(prev_ts, knowable_ts]`.
  - `gap = 1` when that window is longer than 3 intervals (recorder down, or the address missed polls).
  - An address seen for the first time is a baseline: its existing positions are not events.
    An address that did not answer a poll produces no events for that poll; it is never read as
    "closed everything".

## 2. Configuration (env, all optional)

| variable | default | meaning |
|---|---|---|
| `WHALE_RECORDER` | off | `1` starts the recorder thread (one per DB: a non-blocking flock on `<db>.lock`) |
| `WHALES_DB_PATH` | `whales.db` next to `DB_PATH` | SQLite file (empty string = in memory). **New file next to the user's DB, flagged for review** |
| `WHALE_INTERVAL_MIN` | 5 | minutes between recorded polls |
| `WHALE_FULL_EVERY_MIN` | 60 | full snapshot (all positions + uPnL) cadence |
| `WHALE_LEADERBOARD_TTL_MIN` | 360 | leaderboard refresh when the recorder is on (the shared tracker's TTL is raised to this) |

Rate limits:
- The recorder **shares** the positioning page's tracker. A poll younger than half an interval is
  reused, not repeated.
- A poll is 50 sequential requests paced at ≤ 1 request/s by `positioning_data.HttpClient`
  (`clearinghouseState`, weight 2 each).
- The ~38 MB leaderboard is cached by the tracker. A failed refresh keeps the old list and is
  retried at most every 10 minutes. It is never re-downloaded after every error (the earlier
  fix is kept and tested). With the recorder on, the list is refreshed every 6 h instead of
  hourly, so the tracked set stays stable and downloads are rare.

Size: measured on the live run (§4). Expect a few MB per day at 5-minute polls.

## 3. Observer strategy (paper journal, kind `observer`)

`whale_observer.py` registers `obs_hl_whale_follow_top10_30d` in the paper journal **only when
`WHALE_RECORDER=1`** (and the journal runs only with `PAPER_JOURNAL=1`). Rules (frozen in its
params hash):
- **Events followed:** `open` and `flip` only, i.e. new directional exposure, knowable after the
  observer's first cycle. Older events are warm-up and are never followed.
- **Wallets:** the top-10 of the tracked accounts by trailing 30-day PnL, taken from the last
  leaderboard snapshot fetched at or before the event's `knowable_ts`. A later snapshot is
  never used.
- **De-duplication:** at most one followed event per (wallet, coin) per 24 h. A later event within
  24 h of the last followed one is skipped; this is knowable at the time. Reason: on the first live
  run, one top-10 wallet flipped six coins back and forth 65 times in 37 minutes, and version 1
  (without this rule) followed only that wallet: 50 of 50 events. Version 1 was retired after
  37 minutes, before any follow trade had closed, so no result was read before the rule was added.
- **Entry:** the open of the first 1-minute Hyperliquid candle starting after `knowable_ts`,
  same side as the whale's new position, one unit of notional. A row is priced only after that
  minute has closed.
- **Exits:** the 1-minute open exactly 1, 3 and 7 days after entry, giving three rows per event.
- **Costs:** Hyperliquid taker 0.045 % per side, plus hourly funding over the hold (longs pay
  positive rates).
- **Missing prices:** a row whose candle is no longer served (1m history ≈ 3.5 days) becomes
  `no_price` with the reason. It is never filled with a guess.

The `/paper` page shows it as a tab, with counts and mean/median/hit per holding period.
**No edge is claimed.**

## 4. Live verification (scratch server :5105)

See `docs/PAPER_JOURNAL_RESULT.md` § "Portfolio strategies and the whale observer — live run".

## 5. Pre-registration for the future test (commit before reading any follow result)

- **When:** after ≥ 90 calendar days of recording **and** ≥ 300 closed 7-day rows from ≥ 30
  distinct wallets. Expect ~60–90 days to reach the row count if the top-10 open/flip a few
  times a day; if not reached by day 120, report as "insufficient events" and stop.
- **Primary hypothesis (one):** the mean net return of 3-day follow rows > 0.
- **Statistics:** wallet-day cluster bootstrap (2,000 resamples, seed 7) for a 95 % CI. The
  test passes only if the CI lower bound > 0.
- **Secondary (Holm over 2):** 1-day and 7-day horizons.
- **Control:** for every followed event, the same coin and entry minute with the **opposite**
  side, and a random-side entry at the same minute (crc32 seed on the event id). The strategy
  must beat the random-side control by more than the cost of a round trip (0.09 %).
- **Exclusions decided now:** rows with `gap = 1` events, `no_price` rows, and coins with fewer
  than 10 rows are reported separately, not dropped silently.
- **Out of scope until then:** any change to K, the ranking window or the event kinds. Such a
  change creates a new strategy version that starts from zero.
