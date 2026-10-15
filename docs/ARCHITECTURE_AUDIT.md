# Crypto Screener: architecture audit and module roadmap

## Scope and verified baseline

Audited GitHub `vegomanagement/crypto-screener`, commit `8434f8d8169eb61dbce4e8f085c55a922238bee4`. This report is an engineering assessment, not evidence of a profitable strategy.

- Existing `/Users/maxbelov/Projects/crypto-screener` is an older checkout at `46bd77d`, with untracked `config.py` and `start.sh`. It was not modified, updated or used to load credentials.
- Fresh isolated checkout: `/Users/maxbelov/Projects/crypto-screener-audit`; Python 3.12 venv, requirements-dev installed. Playwright was additionally installed only here for UI verification.
- Full suite: **763 passed**, 11 matplotlib date-locator warnings, 39.33 seconds.
- CI-equivalent baseline Ruff selections pass: explicit `E4,E7,E9,F`, with E701/E702 ignored for the legacy entrypoints. A broader lint invocation reports 84 findings; that is not the same acceptance gate.
- Local Flask app runs at `http://127.0.0.1:5087/ui` via a separate safe launcher. Main startup was intentionally not executed: no Telegram polling, scheduler, command registration, startup messages, or real Anthropic credentials. Outgoing messaging/model helper calls are disabled. This does not validate the full production lifecycle.
- HTTP health, UI, symbols, and 4H candle endpoints return 200. Defaults contain only BTCUSDT/ETHUSDT. Chromium rendered the real page and chart; no JavaScript page errors were captured in that smoke run. UI visual inspection confirms broken Turtle object rendering and absent market fields.
- Seven isolated observations reproduced in `evidence/reproduce.py`, using synthetic fixtures/mocks, not invented trading results. No webhook dispatch or Telegram message was sent in those tests.
- No production deployment, trading API keys, broker connection, push, or strategy changes.

## User-specific target

Bybit futures; BTC, ETH, SOL, BONK and optional alts; 4H/D1. Methods: price action, SMC, candle analysis, Wyckoff, EMA 7/14/28, optional EMA200, VWAP. Exact setup/exit rules, VWAP anchoring, risk sizing and account fee tier remain unspecified. Assume USDT linear perpetuals only for the proposed first pilot; confirm settlement type before implementing user-specific contract selection.

## Current architecture

```text
TradingView webhook / Telegram commands / scheduled detectors
  -> SignalAggregator + cooldown gates
  -> fetch_market(): Bybit / Binance / Hyperliquid + other public metrics
  -> indicators / SMC / confluence / HTF gates
  -> decision.make_decision(): deterministic verdict, entry, SL, TP
  -> optional LLM commentary + chart + Telegram
  -> SQLite signals / tracking / statistics

Flask UI -> /api/klines, /api/market, /api/zones, analysis, signals
Backtest -> bt_data -> bt_market -> detectors -> make_decision
         -> _simulate_outcome -> reports / walk-forward
```

This is a working Python application with a large Flask/worker integration file (`screener.py`, 6726 lines), embedded frontend and several separated strategy/research modules. It is not necessary to rewrite it wholesale. Shared contracts and lifecycle seams are missing; test coverage is substantial but some tests preserve invalid assumptions.

## Priority findings

### P1: Correctness of data and evaluation

1. **HTF future leakage.** `bt_data.py:164-175` stores bar open time. `bt_market.py:294-316,339-346` admits full OHLCV whenever open_ts <= primary open_ts, even if the higher bar has not closed. Our offline reproduction confirms a 4H candle is admitted at minute five. Fix around a single decision clock and `close_ts <= as_of`, not by merely dropping the final dataset row. Test mutation invariance of all future/unclosed candles, including exact boundaries.

2. **Unverified entry fills.** `backtest.py:629-639` calculates a midpoint but `_simulate_outcome` at 182-240 receives no entry price. The fixture with intended entry 80 and future range 99-104 still returns TP1/+23R. This is a synthetic repro of missing execution logic, not an actual trade. Introduce pending/filled/unfilled state and explicit market/limit policy; require fill before exits.

3. **Optimistic and bucket-dependent exits.** `backtest.py:235-238` and `tracking.py:297-301` choose the furthest TP reached within a candle for the entire trade. A fixture touching TP1/2/3 yields TP3/+4R, but touching TP1 in an earlier candle closes at TP1. Pick a documented single/partial exit policy. Ambiguous OHLC paths require conservative treatment or lower-TF evidence; do not claim exact intrabar execution.

4. **Tracking can use pre-entry extremes.** Adapters discard timestamps (`screener.py:419-420,455-456,473-474`); tracking requests buffered bars (`tracking.py:206-225`) and `_detect_hit` ignores entry_ts (`246-303`). Expiry is applied before historical checking (`186-193`). Preserve timestamps, reject pre-entry portions when unobservable, backfill before expiry and distinguish censored/open from 0R expired.

5. **Chart times are invented from request time.** `screener.py:4551-4564` reconstructs bars backwards from now. The returned 4H timestamp is not aligned to a real exchange boundary; reload changes timestamps. Fix at the provider boundary and preserve exact venue timestamps all the way to the UI, tracker and model input.

6. **Different live/backtest strategies.** Backtest sends `mtf={}` and fixed confluence (`backtest.py:579-585`) and stubs market fields (`bt_market.py:380-395`). CVD proxy and EMA definitions differ between paths (`bt_market.py:141-200` vs `screener.py:495-585`). Missing fields silently disable some decision vetoes. Share one feature implementation and test golden snapshots/verdicts across replay and live.

7. **Wrong timeframe for live risk.** Live indicators always use 1H (`screener.py:1713`), while scan labels can be 4H/D1. `decision.py:145-169` takes a single ATR without execution timeframe. Provide features per TF and an explicit decision timeframe; no implicit H1 fallback.

8. **Incomplete economic accounting.** Backtest fee approximation uses twice entry notional (`backtest.py:482-493,646-647`); funding is a feature but not a cash flow. Tracking stats are gross. Slippage, spread, portfolio margin and simultaneous risk are not modeled. Realized equity ordered by entries is not portfolio drawdown. Do not reuse historical STRATEGY.md figures as validated performance after these findings.

Additional defects: unfinished live/latest historical bars are admitted; `compute_change_24h` uses 288 bars even for H4/D1; OI uses observation counts instead of elapsed time; cache validates historical start but not fresh tail/continuous coverage; flat RSI behavior needs a defined policy; HTF voting includes lower/equal TFs on H4/D1. Relevant areas: `bt_data.py:402-438,498-501,569-572`; `bt_market.py:100-101,206-216,254-266`; `htf_bias.py:47`.

### P1: Access control and dispatch

9. **Webhook has no authentication.** `screener.py:2889-2902` enqueues user input without a secret/signature, schema or symbol allowlist. Offline Flask test confirms unauthenticated enqueue (aggregator mocked). Invalid JSON arrays/null can cause 500. If exposed externally, this path can trigger paid LLM and Telegram work. Actual public exposure/reverse proxy policy was not checked. Add authentication compatible with the actual TradingView sender, replay protection, validation, bounded queues/rates and symbol canonicalization. Do not assume TradingView can emit arbitrary HMAC headers.

10. **Telegram has no allowed-user/chat gate.** `screener.py:5151-5179` dispatches any supplied chat_id; callback path has the same issue. Offline mock confirms `/status` from an arbitrary chat reaches the handler. Restrict user AND chat identity as required, including callbacks/photos/free text; bound workers and expensive commands.

11. **Dispatch/tracking state is not atomic.** Cooldown check and record surround external work without reservation (`screener.py:2821-2879,5424-5485`). Tracking opens before successful notification (`194-198,2835-2836`). Add atomic idempotency reservation and outbox states `pending/sent/failed/suppressed`. Distinguish a generated signal, a delivered alert, a paper-filled order and a user-executed trade.

### P2: API, UI and maintainability

12. **API schema drops existing fields.** `screener.py:4323-4339` reads lowercase EMA keys instead of `1H/4H/1D`, `open_interest` instead of `oi`, and casts the nested VWAP dictionary to float. Offline response confirms nulls despite populated fixtures. Turtle dictionaries render as `[object Object]`; reproduced in Chromium screenshot. Fix serializers + contract tests, not cosmetic fallback values.

13. **Direction/visibility markers are wrong.** Stored bull/bear does not match frontend long/short (`tracking.py:104-106`; `screener.py:3789-3796`). Suppressed signals and inconsistent datetime-string formats can contaminate marker selection (`4218-4235`). Use typed enums and canonical timestamps.

14. **UI request races and mislabeled change.** Async responses have no active-symbol generation check (`screener.py:3331-3348,3391-3395`). A late previous-symbol response can overwrite the current selection. `3360-3366` labels the entire visible-range change as 24h. Add abort/generation guards and real interval calculations.

15. **Runtime lifecycle is coupled to side effects.** Main (`screener.py:6697-6723`) requires credentials and launches messaging/polling/scheduling just to serve UI; health only reports running. Introduce app factory, settings, `ui-only`/worker modes, readiness and worker health. LLM should run after deterministic filtering; avoid paying for suppressed signals.

16. **User's indicator profile is not implemented.** Current UI shows EMA20/50; other layers use EMA9/21 or close/EMA20. Daily history is only 50 bars, not enough for EMA200. Preserve old profile for comparison; add an explicit versioned 7/14/28/200 profile with sufficient warmup. Current CVD is an OHLCV proxy, not observed aggressor flow: label it honestly.

## Proposed module boundaries (incremental)

- `bootstrap/settings`: app factory, local-only defaults, workers and notifier switches, readiness.
- `market_data`: provider adapters, instrument registry, immutable timestamped CandleSeries, freshness/gaps/closed-bar/provenance checks.
- `features`: shared deterministic implementations, per-TF snapshots and golden fixtures.
- `strategies`: versioned user profile and auditable setup evidence; missing != neutral.
- `decisions`: deterministic policy on validated snapshots; forecast cannot directly size/execute trades.
- `dispatch`: access-controlled ingress, bounded queue, atomic dedup/outbox, optional commentary and notifications.
- `execution_replay`: order fills, exit policy, fees/funding, timestamps and portfolio ledger used by tracker and backtest.
- `forecast`: isolated optional Kronos worker, persisted experiments, evaluation independent from production decisions.
- `api/ui`: validated serializers, request-generation guards, separate frontend assets.

## Research: Bybit and Kronos integration

Primary sources retrieved directly over HTTPS: Bybit V5 kline and instruments documentation; upstream Kronos README and `model/kronos.py`. Automated extract tool failed, so direct requests were used; no secondary performance claims relied upon.

Public Bybit instrument requests in this audit returned USDT linear perpetuals BTCUSDT, ETHUSDT, SOLUSDT and **1000BONKUSDT**. Literal BONKUSDT returned retCode 10001. These observations are not a universal symbol alias: UI may display BONK but must preserve exact instrument metadata and price/volume units. Funding interval in these responses differs for 1000BONKUSDT versus the three large contracts; do not hardcode a universal schedule. Evidence saved in `evidence/instruments.json`; fetch metadata afresh at runtime.

Kronos-small uses a matching base tokenizer, a 512 context limit and OHLC with optional volume/amount. Input preparation must use exact UTC bar opens, separate as-of close time, strictly increasing regular closed bars, explicit missing-bar policy and correct base/quote volume. Preserve genuine turnover/amount when available rather than inventing precision from an approximation. No EMA/VWAP/SMC interpretation is supplied automatically.

**Important correction to the earlier conceptual discussion:** upstream `auto_regressive_inference` averages samples (`kronos.py:465-467`); `sample_count > 1` does NOT expose separate paths through the standard predictor result. For path distributions, retain separate seeded single-sample runs or carefully adapt the inference boundary and test it. Quantiles are empirical model-sample spread, not calibrated probabilities or guaranteed prediction intervals.

Proposed experiment (not yet installed): pinned Kronos code/model/tokenizer revisions, isolated worker process/environment, CPU/MPS/CUDA availability measured rather than assumed. No torch load in web workers. Forecast only on a new closed bar; cache by instrument, venue, TF, last bar, input hash and model/settings revision. Persist horizon, seed, sampling settings, input range, creation/as-of times and output. Web timeout/failure should show unavailable, never block current deterministic analysis or silently fabricate forecasts.

Candidate horizons: short multi-bar 4H and D1 forecasts, selected and frozen before holdout scoring. First outputs: close path and subsequent realized error versus last-close baseline. Evaluate direction and error by asset/TF/regime; then shadow-filter user setups, tracking rejected winners as well as avoided losses. Prevent lookahead, use temporal holdout/walk-forward, retain uncertainty and avoid claims based on selected screenshots. No claim of positive alpha is made.

## Ordered delivery plan and acceptance gates

### Stage A — foundation, before new trading signals
- Safe startup modes + ingress auth, rate limits, allowed Telegram identities and no paid calls in UI-only mode.
- Timestamp-preserving Bybit-first provider and instrument registry; no silent cross-venue substitute for Bybit forecasts.
- Closed-candle/as-of semantics and correct API contracts. Source/freshness visible in UI.
- Regression gates: no HTF future mutation changes a past verdict; candle time survives provider->UI unchanged; no entry fill means no trade; no pre-entry TP/SL; no unauthorized side effect.

### Stage B — user's research workspace
- Versioned D1/4H profile and configurable EMA7/14/28/200 with warmup.
- VWAP choice (daily/weekly/anchored) made explicit; UTC reset/anchor visible.
- Watchlist BTC/ETH/SOL/1000BONK via metadata, configurable alts.
- Fix UI fields, markers, request races; classify PA/SMC/Wyckoff outputs as rule-based candidates, not guaranteed phase recognition.

### Stage C — credible replay and shadow journal
- Shared live/replay features; realistic pending/fill/exit state machine.
- Fees/slippage/funding cash flows, chronological positions/equity and open/censored outcomes.
- Rebuild baseline from scratch; old STRATEGY.md results remain unverified.

### Stage D — Kronos shadow module
- Optional isolated service, forecasting endpoint, persisted journal and UI forecast toggle clearly labeled experimental.
- Unit/contract tests without model downloads plus a real pinned-weight inference smoke and measured runtime on target hardware.
- Holdout evaluation against naive baseline and the user's strategy, including costs and rejected opportunities. No automatic execution or decision veto until evaluation supports its use.

### Later, only after demonstrated need
- Actual aggressor-trade delta instead of OHLCV CVD proxy, with streaming/history provenance.
- Funding/OI basis and event-risk context, BTC correlation and combined portfolio exposure.
- Broker execution remains a separate project scope, not an implicit consequence of adding Kronos.

## Evidence

`evidence/pytest.log`: full suite; `evidence/reproductions.json` and `reproduce.py`: seven offline observations; `evidence/http.json`: HTTP smoke; `evidence/instruments.json`: public instrument lookup; `evidence/ui.png`: browser screenshot. Source checkout unchanged except this audit documentation/evidence. No bug fixes or forecast modules shipped in this stage.

Sources: https://github.com/shiyu-coder/Kronos ; https://raw.githubusercontent.com/shiyu-coder/Kronos/master/model/kronos.py ; https://bybit-exchange.github.io/docs/v5/market/kline ; https://bybit-exchange.github.io/docs/v5/market/instrument .
