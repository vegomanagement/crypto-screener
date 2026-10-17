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

### Long/short split: evidence factors, computed server-side
Hermes no longer outputs percentages (the prompt forbids them; any `long_pct`/`short_pct` it sends is ignored with a warning). It returns `factor_scores`: up to 12 `{factor, direction long|short, strength 1–3, source_fields}`. The prompt asks for evidence on **both** sides and for a funding factor from `funding.last_settled`, `funding.predicted`, `funding.annualized_predicted` and `funding.cross_venue` when present.

Validation (`analyst_hermes.validate_factors`) drops a factor when its direction or strength is invalid (strength must be an integer, so `2.0`/`true` fail), when any `source_fields` path is unknown, when it cites `baseline.*` (the server already blends that in, so it would count twice), or when it cites the same field set as a factor already kept (field order does not matter). If no factor survives, the reply is invalid and one repair turn is sent.

The split (`analyst.compute_split`, all constants in `analyst.SPLIT_CONFIG`):

```
agent_net    = Σ sign·strength / max(Σ strength, 6)                      ∈ [−1, 1]
baseline_net = clip(2·(L−S)/(L+S) + 0.5·[long_verdict=LONG] − 0.5·[short_verdict=SHORT])
               (L, S = confluence long/short scores in snapshot.baseline)
net          = (0.65·agent_net + 0.35·baseline_net) / (sum of the weights that exist)
p_long       = 100 / (1 + e^(−3·net))      → long_pct/short_pct, integers summing to 100
bias         = neutral if |p_long − 50| < 5, else the side of p_long
```

Rounding is half-away-from-50, so a mirrored input gives exactly the mirrored split. The floor of 6 in `agent_net` means a single strength-3 factor moves `agent_net` by at most 0.5. With |net| ≤ 1, `p_long` stays within 4.7–95.3. Without Hermes, the fallback uses `baseline_net` only (`weights.agent = 0`).

The agent's own `bias` is kept as `agent_bias` for cross-checking only. If it differs from the computed bias, the response gets `disagreement` (`opposite` or `neutral_mismatch`) and a warning, and the card shows an orange ⚠ line. The card shows each factor as a chip: green `+n` for long, red `−n` for short, a dashed border for funding, and the cited fields in the tooltip. It also shows a line with the maths, for example `factors −0.67 (L 4 · S 20) ×0.65 · baseline +0.08 ×0.35 → net −0.40 → p_long 22.9`. `agent_analyses` now also stores `p_long`, `net`, `factors_json`, `agent_bias` and `disagreement`, and old files are migrated in place. `GET /api/agent/stats` adds `split` with the p_long min/max/mean/std, a histogram, the number of distinct splits, factors per Hermes run, runs citing funding, and disagreement counts. The calibration line in the card shows the same figures.

### Validation (server side)
- `bias` must be long/short/neutral, and at least one factor must validate. If not, the reply is rejected and one repair turn is sent.
- Each level must have a finite price inside `[window_low − 3·ATR, window_high + 3·ATR]`, and every `source_fields` path must exist in the snapshot. Items that fail are dropped and a warning is recorded.
- Support/resistance/liquidity/POI levels more than 0.25 ATR from every value they cite are kept but marked `unanchored` (shown with "?").
- A reply cut off between tokens gets its missing `]`/`}` appended once, with a warning. This happened live (see below). A reply cut off inside a string is not repaired.
- Timeout is 60 s. If Hermes is unreachable or its output is invalid after the repair turn, the deterministic baseline is returned with status `hermes_unavailable` / `invalid_output`.

## Tests (real runs)
- After the factor-scored split: `pytest` **1360 passed**, `node --test tests/*.cjs` **153 tests, 147 pass, 0 fail, 6 skipped**, and the CI ruff commands plus full `ruff check` on the analyst files are clean. `tests/test_analyst_split.py` covers factor validation (unknown or partly unknown fields, duplicate field sets in either order, non-integer or bool strength, `baseline.*` citations, the cap of 12, model percentages ignored), the prompt (no percentages, both sides, funding fields), the split maths (symmetry under mirroring, bounds and sum = 100 over a 2 304-case grid, half-away-from-50 rounding, the neutral band, the `agent_net` floor, baseline blending and renormalised weights), the disagreement warning end to end through the service, `split_stats`, and the migration of an old SQLite file. `analyst_panel.test.cjs` covers the factor chips, the maths line, the disagreement text, the rendered HTML (escaping, colours, the funding chip) and the p_long figures in the stats line.
- Before the factor change: `pytest` **1343 passed** (full suite). New files: `tests/test_analyst.py`, `tests/test_analyst_service.py`, `tests/test_agent_api.py`, `tests/test_funding.py`. They cover snapshot assembly with fakes, the validator (bad JSON, sums, ranges, unknown fields, repair retry, truncated reply), cache/force, one in-flight request per key, rate limit, the fallback paths, the evaluator maths, routes (input validation, cross-site 403, 429, 502), a check that the key never reaches a response, and the funding normalisers against recorded live venue bodies (`tests/fixtures/funding/live_raw.json`).
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

## Live re-check after the factor change (2026-10-17)
Same launcher on :5090, `docs/evidence/hermes-analyst/live_factor_runs.py` (POST with `force`). Raw responses are in `factor_run_*_{a,gainer,b}.json`, the rows in `factor_summary.json`, the aggregates in `factor_distribution.json`, and `GET /api/agent/stats` in `stats_factor_live.json`. Batch **b** is the final code (with the `baseline.*` drop):

| Run (batch b) | p_long | split | computed / agent bias | agent_net | baseline_net | factors L/S | funding factors | model s |
|---|---|---|---|---|---|---|---|---|
| Binance BTCUSDT 15m | 24.6 | 25/75 | short / short | −0.62 | +0.08 | 2/10 | 1 | 19.9 |
| Binance BTCUSDT 1h | 21.8 | 22/78 | short / short | −0.70 | +0.08 | 2/10 | 2 | 22.4 |
| Binance ETHUSDT 15m | 38.0 | 38/62 | short / short | −0.36 | +0.21 | 4/8 | 1 | 25.2 |
| Binance SOLUSDT 5m | 45.3 | 45/55 | neutral / neutral | −0.14 | +0.08 | 5/7 | 1 | 22.9 |
| Bybit ETHUSDT 15m | 31.0 | 31/69 | short / short | −0.52 | +0.21 | 3/9 | 1 | 21.8 |
| Bybit SOLUSDT 15m | 30.8 | 31/69 | short / short | −0.46 | +0.08 | 3/9 | 1 | 22.9 |
| Hyperliquid BTC 15m | 32.8 | 33/67 | short / short | −0.57 | +0.37 | 3/9 | 2 | 24.2 |
| Hyperliquid SOL 1h | 26.1 | 26/74 | short / short | −0.58 | +0.08 | 3/9 | 1 | 24.9 |
| Binance AGTUSDT 15m (+100 % 24h) | 15.4 | 15/85 | short / short | −0.33 | −1.00 | 5/7 | 1 | 25.4 |
| Binance ICNTUSDT 15m (+35 % 24h) | 10.8 | 11/89 | short / short | −0.55 | −1.00 | 3/9 | 1 | 22.4 |
| Binance BTWUSDT 1h (+33 % 24h) | 49.0 | 49/51 | neutral / **long** | +0.30 | −0.60 | 8/4 | 1 | 25.6 |

- **The distribution did not collapse.** Batch b (11 runs) gave p_long 10.8–49.0 (mean 29.6, std 11.1) with 10 distinct splits. Batch a (8 majors, before the `baseline.*` drop) gave 28.9–44.5 with 6 distinct values. Across all 25 logged runs (`stats.split`): 18 distinct splits, histogram 10–20: 2, 20–30: 7, 30–40: 12, 40–50: 3, 50–60: 1. Only 1 of the 11 batch-b runs landed on 38/62. Equal results come only from equal inputs: Bybit ETH 15m in batch b and Binance ETH 15m in batch a both had agent_net −0.5238 and baseline_net +0.2105, giving 30.995 each.
- **Factors:** every run returned 12 factors (batch a: 11–12). Batch b had 41 long and 91 short in total, so every run had evidence on both sides. **Funding was cited in 11/11 runs** (13 factors; before the change it was 0 of 11). Latency was 19.9–25.6 s (median 22.9 s), with 0 repair turns and no `invalid_output`. Warnings: one level flagged `unanchored` (SOL 5m) and one disagreement. In batch a, 2 levels and 1 factor were dropped for citing `candles.recent.N.l/h/v`. Those rows are arrays, so the prompt now says to use a numeric index.
- **Disagreement warning, live:** BTWUSDT 1h — the agent said long, but its factors (+0.30) plus a strongly short baseline (−0.60) computed neutral 49/51. The card shows "⚠ Partial disagreement" (`ui_factors_binance_btw_1h.png`). In batch a (before the fix), the agent said long or neutral on all 3 gainers while the computed split was short. On AGTUSDT it had cited `baseline.short_score` as a factor, counting the baseline twice. That is why `baseline.*` citations are now dropped. In batch b the agent agreed on AGT and ICNT.
- **Not verified live: a computed long.** No run computed long. The market was falling during the window: the summaries for every major describe a bearish structure and a downward EMA stack. Even the top 24h gainers came out short or neutral: they sat in HTF premium, and the confluence short score was 84–100. The maths is symmetric by test, but no live long was observed.
- The confluence baseline leaned *long* (+0.08 to +0.37) on all majors while the structure was bearish. With weight 0.35 it pulls the split toward 50. This is the existing `compute_confluence_score` behaviour and was not changed.
- UI (`factor_ui_shot.cjs`, headless Chrome): `ui_factors_binance_btc_15m.png` (12 chips: 10 red and 2 green, the funding chip dashed, maths line `… → p_long 22.9`), `ui_factors_binance_btw_1h.png` (green-heavy chips and the orange disagreement line), and `ui_factors_hyperliquid_sol_1h.png`. The DOM read-outs are in `factor_ui_*.txt`. Console errors: only `/favicon.ico` 404.

## Honest limitations / unverified
- **Calibration has no outcomes yet.** After the re-check, 36 analyses are logged (11 legacy and 25 with p_long). 33 are pending and 3 are neutral (not scored), so none has an outcome yet. The 15m horizon (12 bars = 3 h) and the 1h horizon (12 h) had not elapsed. The evaluator is unit-tested only. The UI says "Agent estimate, not a calibrated probability" until n ≥ 20 scored outcomes.
- **(Fixed) Hermes anchored on 38/62.** Before the factor change, 10 of 11 validated replies were exactly 38/62. The split is now computed from the factors (see the live re-check above). It is still **not a calibrated probability**: k = 3 and the weights 0.65/0.35 are hand-set, and they should be refit once outcomes are scored (`stats.split` plus the hit rate per conviction bucket). The factor strengths are still the model's own judgement. What changed is that they are now visible, cited and auditable.
- Hermes leaned short in every batch-b run except BTWUSDT. Whether that is market reality or a model bias cannot be told without scored outcomes.
- `fetch_market` sections (liquidity pools, HTF bias, regime, CVD, F&G, baseline) are a Bybit-first USDT aggregate, not the pane's venue. They are labelled that way, and they are null for HIP-3 symbols.
- Footprint coverage on Bybit/Hyperliquid is `partial` (only recent trades are served). On Bybit it can be missing entirely.
- Hyperliquid `predictedFundings` returned an `HlPerp` `nextFundingTime` that had already passed in every capture. Those rows are shown as `stale`. The HL predicted rate in the chip comes from `metaAndAssetCtxs.funding`, which differs slightly from the `HlPerp` row. Hyperliquid funding cap/floor are not exposed by the info API (null with a reason).
- Bybit floor is shown as `−fundingCap`, based on Bybit's documented symmetric limit. It is not a separate field.
- Binance symbols missing from `/fapi/v1/fundingInfo` default to 8 h, and the source says so.
- The `1000X → kX` coin mapping for the cross-venue table is a heuristic.
- The auto-refresh toggle (≥ 2 min) is unit-tested but was not observed live over time.
