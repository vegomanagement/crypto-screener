# Positioning data (CoinGlass-style) from free official endpoints

Branch `claude/positioning-data` (from `claude/scalping-phase2` @ 43307a5). Live runs: 2026-10-17, 20:54–21:11 EEST,
from a Flask-only launcher on :5092. **We do not scrape coinglass.com**: its Terms of Service forbid scraping, crawling
and bulk extraction. Everything below comes from public exchange APIs. An optional official CoinGlass v4 adapter
turns on only when `COINGLASS_API_KEY` is set. It was **not** run live, because no key was used.

## What was built

| File | Role |
|---|---|
| `positioning.py` | Pure normalisers (Binance, Bybit, Hyperliquid), exact `Decimal` maths, `metric()` envelope (`value, source, venue, timestamp, period, reason`), OI window changes |
| `positioning_snapshot.py` | Builds the per-symbol snapshot from raw payloads. A failed or odd response becomes `null` + reason, never an exception |
| `positioning_signals.py` | Descriptive labels with fixed, documented formulas (not trade signals) |
| `positioning_data.py` | urllib fetchers, TTL cache (ratios 60 s, OI/ticker 30 s), per-host pacing, 8 s timeout, 4 MB response cap, bounded concurrency (4) |
| `hl_whales.py`, `hl_whale_tracker.py` | Streams the leaderboard into a top-N heap. Lease-driven poller, aggregation per coin, change detection |
| `liquidations_store.py`, `liquidations_collector.py`, `ws_client.py` | Rolling 24 h store with coverage spans, a lease-driven WS collector (Binance + Bybit) and a stdlib RFC 6455 client (no new pip deps) |
| `coinglass_client.py` | Optional adapter for `GET /api/exchange/balance/list` (header `CG-API-KEY`) |
| `positioning_api.py` | `GET /api/positioning?exchange=&symbol=&period=[&owner=&watch=0/1]`, `GET /api/positioning/whales?coin=`, demo page `/positioning`. Registered in `screener.py` |
| `static/positioning-panel.js` | UMD module: pure `viewModel()`, `renderHtml()`, `mount(el, opts)` → `{refresh, update, destroy}`. Scoped CSS is injected once |
| `templates/positioning.html` | Temporary demo page with 3 cards (`?cards=binance:BTCUSDT,bybit:ETHUSDT,hyperliquid:BTC`) |

`static/workspace.js` and the analyst files are untouched. Mounting the panel into the Hermes analyst card is left for the merge.

### Snapshot sections and sources

| Section | Binance USDT-M | Bybit linear | Hyperliquid |
|---|---|---|---|
| Top traders (accounts / positions) | `/futures/data/topLongShortAccountRatio`, `topLongShortPositionRatio` | null: "venue publishes no top-trader ratio" | null (same) |
| Crowd (all accounts) | `/futures/data/globalLongShortAccountRatio` | `/v5/market/account-ratio` (buyRatio/sellRatio; L/S = buy/sell, 4 dp) | null: "publishes no long/short ratio" |
| Taker buy/sell | `/futures/data/takerlongshortRatio` | null: no public endpoint | null |
| OI now | `/fapi/v1/openInterest` (+ USD at premiumIndex mark) | `/v5/market/tickers` `singleOpenInterest` (one side; both-sides value kept) | `metaAndAssetCtxs.openInterest`, timestamp = actual fetch time |
| OI change 1h/4h/24h + price change at the same stamps | `/futures/data/openInterestHist` 1h + `/fapi/v1/klines` 1h opens | `/v5/market/open-interest` 1h + `/v5/market/kline` 60 | null: "no public OI history endpoint" |
| Basis / funding | `/futures/data/basis` PERPETUAL + `/fapi/v1/premiumIndex` | mark − index from tickers; `fundingRate` | markPx − oraclePx; hourly `funding` |
| Liquidations | WS `fstream.binance.com/market/ws/!forceOrder@arr` (**lower bound**) | WS `allLiquidation.<symbol>` | null: "Hyperliquid has no public liquidation stream" |
| Whales | — | — | Top-N accounts by account value from the leaderboard + `clearinghouseState` |
| Exchange flows (on-chain) | CoinGlass adapter only. Without a key: `null`, reason `requires CoinGlass API key (paid)` | | |

The legacy `wss://fstream.binance.com/ws/!forceOrder@arr` path delivered **0 messages in 40 s** today, while
`/market/ws/` delivered events. The repo's `market-stream.js` already uses `/market/ws/`.

### Derived labels (descriptive only)

Every label returns its formula, inputs and `note: "Descriptive label of current positioning, not a trade signal."`

- **top_vs_crowd**: d = (top-trader **position** long share − global **account** long share) × 100 pp. d ≥ +5 → top more long; d ≤ −5 → top more short; otherwise aligned. The ratio difference is also reported.
- **oi_price_4h**: the OI % change and the price % change are sampled at the same timestamps. OI ±1 % and price ±0.3 % give four labels: longs building, shorts building, short covering, long unwinding. Anything else is "no clear change".
- **taker_flow**: ratio ≥ 1.10 buyers aggressive; ≤ 0.90 sellers aggressive.
- **crowd_extreme**: long share ≥ 0.70 or ≤ 0.30.
- **funding**: sign of the funding rate.
- **whale_bias**: HL long notional share ≥ 0.60 / ≤ 0.40.
- **liquidation_imbalance**: 1 h long-liquidation USD share ≥ 0.70 / ≤ 0.30. Zero recorded gives "none recorded".

## Live verification (real numbers)

### Field-by-field: `/api/positioning` vs raw endpoints fetched concurrently

`docs/evidence/positioning/compare_*.json`, `compare_console.txt`. Two rounds ran after a server restart (cold cache):

- 21:09:09: **48 fields, 47 exact match, 0 mismatch, 1 different sample time.** Binance ETH OI now: the API sampled 5.1 s after the raw call, 2277946.85 vs 2277946.715.
- 21:10:19: **48 fields, 46 exact match, 0 mismatch, 2 different sample time.** Bybit mark − index, BTC −35.26 vs −35.23 and ETH −0.82 vs −1.09; the ticker `time` differs by about 220 ms.

Sample from 21:10:19 (API = raw):

| Market | Field | Value |
|---|---|---|
| Binance BTCUSDT | top account L/S, long share | 1.8321, 0.6469 |
| Binance BTCUSDT | top position L/S, long share | 1.7295, 0.6336 |
| Binance BTCUSDT | global account L/S, long share | 1.7086, 0.6308 |
| Binance BTCUSDT | taker buySellRatio | 1.1518 |
| Binance BTCUSDT | OI now; Δ1h / Δ4h / Δ24h | 93702.034 BTC; −0.13 % / −0.32 % / −0.14 % |
| Binance BTCUSDT | basis; funding | −28.47717391; 0.00002632 |
| Binance ETHUSDT | top position long share; global long share | 0.6283; 0.7422 |
| Bybit BTCUSDT | account buyRatio; OI (one side) | 0.63; 29583.387 |
| Bybit ETHUSDT | OI Δ4h | −1.71 % |
| Hyperliquid BTC | OI; mark − oracle; funding (1 h) | 41134.96342; −33.2; 0.0000124703 |
| Hyperliquid ETH | OI; mark − oracle | 1060735.1150000002; −1.08 |

An earlier round at 20:55 ran on the previous build, while the live-run traffic kept the 30 s cache warm. It showed 2 HL "mismatches" (OI 41084.61232 vs 41084.61556) and 5 different-sample rows. Cause: HL values had no timestamp, and the snapshot stamped them with render time. **Fixed:** HL metrics now carry the real fetch time (also on cache hits).

### Whale tracker: Hyperliquid top-50 accounts by account value

`live_run.jsonl`, `live_run_summary.txt`, `leaderboard_stream_memory.txt`

- Leaderboard: 47,151 rows streamed in 1.43–1.48 s. Peak RSS rose about 8 MB (23.8 → 32.0 MB) for the ~38 MB JSON. Only the top-50 heap is kept, cached for 1 h. The 50th account holds $49.28M; the 1st holds $15.50B.
- **12 polls in 13.1 min.** Each poll covered **50/50 accounts with 0 failures** and took 49.3–50.7 s (paced ≤ 1 req/s = 100 weight/min against HL's 1200/min).
- Changes detected per poll (all coins): 0 on the first poll (baseline), then 16, 11, 8, 14, 7, 10, 12, 14, 19, 16, 17.
- BTC at the end: long $4,983,094.30 (1 account) vs short $496,480,357.99 (7 accounts). Avg entry: long 81328.1, short 77142.32.
- ETH at the end: long $16,568.44 (2) vs short $508,022,629.39 (6).
- Example changes: `0xf5d8…ad53` BTC flipped long→short 6.15157 → −5.87831 (Δ≈$980K); `0xecb6…` BTC long decreased 65.16 → 61.17 (Δ≈$325K).

### Liquidation collector (13.1 min, BTC/ETH/SOL/XRP/DOGE on both venues)

| Venue | Messages received | Events stored (watched symbols) | Long liq. USD | Short liq. USD | Reconnects | Errors |
|---|---|---|---|---|---|---|
| Binance (all-market stream, **lower bound**) | 208 | 29 (BTC 14, ETH 11, DOGE 3, XRP 1) | 347.23 | 911,885.11 | 1 connect | none |
| Bybit (subscribed 5 symbols) | 52 (incl. ack/pong) | 13 (BTC 3, ETH 7, XRP 3) | 0.00 | 47,981.19 | 1 connect | none |

Coverage per market was 12.5–12.6 of 60 min, so every window shows "covered N of M min" and `complete_coverage=false`.
Hyperliquid returns null with "Hyperliquid has no public liquidation stream".

### Screenshots

`docs/evidence/positioning/positioning_default.png` (Binance BTCUSDT, Bybit ETHUSDT, HL BTC) and `positioning_eth.png`
(Binance ETHUSDT, Bybit BTCUSDT, HL ETH). Taken with headless Chrome (`--headless=new`, own profile, CDP port 9565) after the live run. All sections render with live data, and the null sections show their reasons.

## Rate limits and politeness

- Binance `/futures/data/*`: "IP rate limit 1000 requests/5min"; only the latest 30 days are available (official docs). One snapshot makes 9 requests; with the 60 s / 30 s caches, repeated panel refreshes cost at most ~9 requests/min per symbol.
- Bybit: 600 requests / 5 s per IP (official docs); a snapshot makes 4 requests.
- Hyperliquid: 1200 weight/min per IP; `clearinghouseState` weight 2, `metaAndAssetCtxs` weight 20. The whale poller uses ~100 weight/min.
- CoinGlass: plan-dependent per-minute limit (`API-KEY-MAX-LIMIT` header); the adapter caches for 10 min and paces at ≥ 2 s.
- Background work (WS collectors, whale polling) runs only under leases: 120 s for liquidations, 180 s for whales. Panel refreshes renew them. Caps: 10 symbols per venue, 40 liquidation owners, 20 whale owners.

## Tests

- New pytest files: `test_positioning.py`, `test_positioning_signals.py`, `test_hl_whales.py`, `test_liquidations.py`, `test_coinglass_client.py`, `test_positioning_api.py`. They use recorded live responses in `tests/fixtures/positioning/` (see its README).
- New node test: `tests/positioning_panel.test.cjs`.
- CoinGlass fixtures: the documented response example of `exchange-balance-list` verbatim, plus a `{code:"401"}` error built from the documented error table. They were not recorded, because no key was used.

## Limitations (plain)

- **Liquidation totals are lower bounds.** Binance pushes at most one liquidation per symbol per 1000 ms. Both venues only count while the collector is connected, and it runs only while a page holds a lease. Bybit notional uses the bankruptcy price. The store is in memory (lost on restart), with 24 h retention and 20k events per market.
- **"Whales" = Hyperliquid top-N accounts by account value**, not all whales. Some top accounts are likely vaults or market makers (#1 shows $15.5B). Changes are only detected between successive polls (~60 s), so a position opened and closed inside one poll is invisible. Size changes under 5 % are ignored.
- **Exchange inflows/outflows** have no free reliable source. Without `COINGLASS_API_KEY` they are `null`, with reason "requires CoinGlass API key (paid)". The adapter covers only the coins in the endpoint's documented symbol list and has never been exercised against the live CoinGlass API.
- Bybit has no top-trader ratio and no taker ratio. Hyperliquid has no OI history, no long/short ratio and no liquidation stream. All of these are shown as null + reason.
- Binance ratio series are completed-period data, so the newest point can lag up to one period. Taker and basis may lag one more period than the account ratios (seen live: 17:00 vs 16:00 UTC).
- Periods are limited to the common set `5m,15m,30m,1h,4h,1d`. Bybit lacks 2h/6h/12h.
- Bybit "basis" is mark − index from tickers, not a futures basis endpoint.
- `/positioning` is a temporary demo page. The panel is meant to be mounted into the Hermes analyst card at merge.
