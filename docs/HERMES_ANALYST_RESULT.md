# Hermes analyst + funding — result

Branch `claude/hermes-analyst` (from `claude/scalping-phase2` @ 43307a5). Local commits only: nothing was pushed, merged or deployed. View only, and no order code paths exist.

## What was built

| Piece | File(s) |
|---|---|
| Real-data snapshot of one chart (pure, provider-injected) | `analyst.py` |
| Hermes gateway client, prompt, strict validation, repair of truncated replies | `analyst_hermes.py` |
| Orchestration: 60 s cache, one in-flight request per key, 6/min global limit, deterministic fallback | `analyst_service.py` |
| SQLite log `agent_analyses` + ±1 ATR outcome evaluator | `analyst_store.py` (file `.data/agent_analyses.sqlite3`, gitignored) |
| Routes `POST /api/agent/analysis`, `GET /api/agent/stats` | `agent_api.py` (registered in `screener.py` the same way as `liquidity_api`) |
| Funding normaliser (pure) / urllib fetchers with a 30 s cache / route `GET /api/funding` | `funding.py`, `funding_data.py`, `funding_api.py` |
| UI: Hermes analyst card (replaces the "One pane. Your focus." note) | `static/analyst-panel.js` (UMD), small patches to `static/workspace.js`, `templates/workspace.html`, `static/workspace.css` |
| UI: funding chip in every pane header, Funding section in the card, last→predicted on `/scalp` | `static/funding.js` (UMD), `static/scalp-dom.js` |
| Env vars `HERMES_API_URL`, `HERMES_API_KEY`, `HERMES_MODEL` (no values) | `README.md`, `config.example.py` |

### Snapshot (`analyst.build_snapshot`)
Every section has `source` and `ts` (UTC ms). If a section is unavailable it is `null`, and the reason goes in `snapshot.missing`. Nothing is invented.
Sections: closed candles (300, last 24 OHLCV), EMA 7/14/28/200 + ATR14 + RSI14 (computed from the same candles), structure/BOS/CHoCH (`structure.py`), unfilled FVGs (`chart_data._fvg_zones`), unmitigated order blocks (`order_blocks.py`), session/killzone (`killzones.py`), liquidity pools / HTF bias / regime / CVD / Fear&Greed (from `screener.fetch_market`, labelled *Bybit-first USDT aggregate, not venue-pinned*), venue OI/mark/index (`exchange_markets.get_exchange_context`), footprint delta/POC for the last 6 closed bars with `coverage` complete/partial (`footprint_data`), and a top-20 order-book imbalance (`liquidity_data.fetch_depth_snapshot`). The `baseline` section holds long/short scores from `screener.compute_confluence_score` plus the verdicts from `decision.make_decision` (called only; `decision.py` is unchanged). The `funding` section holds settled vs predicted funding, annualised values, the trend over recent settlements, and a cross-venue comparison.

### Validation (server side)
- `bias` must be long/short/neutral, and the percentages must be integers 0–100 that sum to 100. If not, the reply is rejected and one repair turn is sent.
- Each level must have a finite price inside `[window_low − 3·ATR, window_high + 3·ATR]`, and every `source_fields` path must exist in the snapshot. Items that fail are dropped and a warning is recorded.
- Support/resistance/liquidity/POI levels more than 0.25 ATR from every value they cite are kept but marked `unanchored` (shown with "?").
- A reply cut off between tokens gets its missing `]`/`}` appended once, with a warning. This happened live (see below). A reply cut off inside a string is not repaired.
- Timeout is 60 s. If Hermes is unreachable or its output is invalid after the repair turn, the deterministic baseline is returned with status `hermes_unavailable` / `invalid_output`.

## Tests (real runs)
- `pytest`: **1343 passed** (full suite). New files: `tests/test_analyst.py`, `tests/test_analyst_service.py`, `tests/test_agent_api.py`, `tests/test_funding.py`. They cover snapshot assembly with fakes, the validator (bad JSON, sums, ranges, unknown fields, repair retry, truncated reply), cache/force, one in-flight request per key, rate limit, the fallback paths, the evaluator maths, routes (input validation, cross-site 403, 429, 502), a check that the key never reaches a response, and the funding normalisers against recorded live venue bodies (`tests/fixtures/funding/live_raw.json`).
- `node --test tests/*.cjs`: **151 tests, 145 pass, 0 fail, 6 skipped** (the 6 skips are pre-existing optional browser tests). New: `tests/analyst_panel.test.cjs`, `tests/funding_ui.test.cjs`.
- Ruff: the CI commands from `.github/workflows/ci.yml` pass, and full `ruff check` passes on all new Python files.

## Live checks (real Hermes gateway, real venues)
Server: `~/.hermes/cache/scratch/hermes-analyst-launch.py` (Flask only, 127.0.0.1:5090). It loads the key into the process environment and never prints it. The user's server on :5087 was not touched.

| Run | status | model latency | snapshot | levels | warnings | result |
|---|---|---|---|---|---|---|
| Binance BTCUSDT 15m | ok | 17.2 s | 5.3 s | 8 | 0 | short 38/62, medium |
| Binance ETHUSDT 1h | ok | 18.5 s | 14.5 s | 8 | 0 | short 38/62, low |
| Bybit SOLUSDT 15m | ok | 15.2 s | 1.0 s | 8 | 0 | short 38/62, medium |

These are the final runs, in `docs/evidence/hermes-analyst/run_*.json` (raw model text plus the validated output) and `summary.json`. An earlier batch before the funding block gave 27.2 / 24.8 / 24.4 s, and the Bybit footprint was then missing ("no closed footprint bars").

The first UI-driven run returned **`invalid_output`**: both attempts were cut off at about 4 000 characters, just before the closing `}` (`ui_run_truncated_reply_before_fix.json`). Fix: the prompt now asks for a reply under 2 500 characters, and truncated JSON is closed once. Later replies were 2.4–2.7 k characters and validated with 0 warnings.

Fallback check without Hermes (`live_fallback.py`): HTTP 200, `hermes_unavailable`, baseline long 59 % (confluence long 84 / short 58, both `WAIT`).

UI (headless Chrome, `ui_shot.cjs`): screenshots `ui_binance_btc_15m.png`, `ui_binance_eth_1h.png`, `ui_bybit_sol_15m.png` (each also `*_centered.png` after clicking a level). They show the card filled and 8 dashed `H·…` lines on the active pane. Clicking a level centred the price scale on it. "＋" wrote `{BTCUSDT, 81583, source chart, note hermes}` into `ScreenerLevels`. Console errors: only `/favicon.ico` 404.

Funding (`funding_live_compare.py` → `funding_live_compare.json`): `/api/funding` was compared field by field with the raw endpoints, fetched right before and after the API call, for Binance BTCUSDT/SOLUSDT, Bybit BTCUSDT/SOLUSDT and Hyperliquid BTC/SOL. All 7 fields matched on all 6 markets, with **0 mismatches** (predicted rate, settles_at, last settled rate/time, interval, cap, mark). Screenshots: `ui_funding_4grid_btc.png` (chips such as `F 0.0037% → 0.0028% · 06:31:29` in all 4 pane headers), `ui_funding_sidebar.png` (last vs predicted, sparkline of 30 settlements, Binance/Bybit/HL table), and `scalp_funding.png` (`Funding last 0.0037% → pred 0.0028% · in 06:31:03`).

Secrets: `check_no_secret.py` reads the key from `~/.hermes/.env` without printing it and greps tracked files, all history and the evidence dir. Result: **0 hits**.

## Honest limitations / unverified
- **Calibration has no outcomes yet.** 11 analyses are logged and all are still pending. The 15m horizon (12 bars = 3 h) and the 1h horizon (12 h) had not elapsed. The evaluator is unit-tested only. The UI says "Agent estimate, not a calibrated probability" until n ≥ 20 scored outcomes.
- **Hermes anchors on 38/62.** 10 of the 11 validated live replies were exactly 38/62 short, and the other was 40/60 (ETH 1h in the UI). The truncated reply was 42/58. This held across BTC, ETH and SOL. Until stats exist, treat the split as uninformative. Hermes did not cite any `funding.*` field in the final runs.
- `fetch_market` sections (liquidity pools, HTF bias, regime, CVD, F&G, baseline) are a Bybit-first USDT aggregate, not the pane's venue. They are labelled that way, and they are null for HIP-3 symbols.
- Footprint coverage on Bybit/Hyperliquid is `partial` (only recent trades are served). On Bybit it can be missing entirely.
- Hyperliquid `predictedFundings` returned an `HlPerp` `nextFundingTime` that had already passed in every capture. Those rows are shown as `stale`. The HL predicted rate in the chip comes from `metaAndAssetCtxs.funding`, which differs slightly from the `HlPerp` row. Hyperliquid funding cap/floor are not exposed by the info API (null with a reason).
- Bybit floor is shown as `−fundingCap`, based on Bybit's documented symmetric limit. It is not a separate field.
- Binance symbols missing from `/fapi/v1/fundingInfo` default to 8 h, and the source says so.
- The `1000X → kX` coin mapping for the cross-venue table is a heuristic.
- The auto-refresh toggle (≥ 2 min) is unit-tested but was not observed live over time.
