# Public liquidity depth snapshots

`liquidity_data.fetch_depth_snapshot(exchange, symbol, depth=200)` performs one
fresh, unauthenticated REST request for the **exact selected native market**.
It never queries a catalog, caches, retries, changes venue, submits orders, or
processes WebSocket deltas. This release collects **sampled snapshots, not
tick-complete history**, liquidation data, hidden liquidity, or a reconstructed
order-event feed. Missing periods cannot be reconstructed from these samples.

## Return contract

```python
{
    "exchange": "bybit",             # bybit | binance | hyperliquid
    "symbol": "BTCUSDT",             # exact caller-supplied native identifier
    "observed_at": 1792233279315,     # local UTC epoch ms at response receipt
    "time": 1792233279159,            # venue UTC epoch ms, or None
    "bids": [[price_float, quantity_float]],
    "asks": [[price_float, quantity_float]],
    "depth_requested": 200,
    "depth_actual": {"bids": 200, "asks": 200},
    "sequence": "9007199254740993123", # exact decimal string, or None
    "quantity_unit": "native",
    "quote_unit": "USDT",            # USDT/USDC suffix, otherwise None
    "warnings": ["Sampled REST snapshot; not tick-complete history."],
}
```

The example schema illustrates types, not a fabricated live book. Quantities
are passed through numerically in source-native units; no USD notional,
underlying coin, shares, or contract-multiplier conversion is inferred. The
quote suffix is recognized only for Bybit/Binance; Hyperliquid quote units are
always `None` because HIP-3 collateral cannot safely be inferred from a symbol.
Bybit USDC identifiers such as `BTCPERP` are accepted, but their unknown quote
label stays `None` without metadata lookup. Case is preserved, including
Hyperliquid `kBONK` and prefixed HIP-3 coins such as `xyz:CL`.

`observed_at` is captured after the HTTP call returns, before JSON decoding; it
is neither the request-start time nor the venue timestamp. Every call takes a
new wall-clock observation. No synthetic increment, cached timestamp, or
monotonic-process value is substituted for UTC. Wall clocks may be adjusted by
the host; this API does not promise strictly increasing UTC values.

## Explicit sources and depth policy

All requests use fixed production HTTPS endpoint constants, `timeout=10`,
`allow_redirects=False`, JSON Accept headers, and no credentials. User symbols
are only query/JSON values, never URL path fragments. There is no fallback.

| Venue | Request | Native source fields / limits |
|---|---|---|
| Bybit | `GET https://api.bybit.com/v5/market/orderbook` with `category=linear`, `symbol`, `limit=depth` | `result.b/a`, `ts`, `u`; documented linear limit 1–1000. RPI orders excluded. |
| Binance USD-M | `GET https://fapi.binance.com/fapi/v1/depth` with `symbol`, supported `limit` | `bids/asks`, transaction `T` (fallback `E` only if `T` absent), `lastUpdateId`. |
| Hyperliquid | `POST https://api.hyperliquid.xyz/info` with `{"type":"l2Book","coin":native_symbol}` | `levels[0/1].px/sz`, `time`, no sequence ID; at most 20 levels/side. No aggregation arguments. |

Binance supports `[5, 10, 20, 50, 100, 500, 1000]`, not 200. Choose the largest
supported limit no greater than the requested depth, except requests 1–4 need
the minimum source limit 5. Thus default 200 requests 100, and requested 500
requests 500. Any difference is warned; results are capped to requested depth.
Hyperliquid has no max-depth parameter: default 200 still requests the native
20-level snapshot, with an explicit warning. Sparse books are legitimate and
warned whenever either side has fewer levels than requested. If an upstream
returns more than requested, a truncation warning is emitted. `depth_actual`
always reports returned counts, never configured limits.

## Validation and errors

- `depth` must be an integer 1–1000 (bool is rejected).
- Supported exchange names are exact lowercase values. Symbol validation matches
  existing exchange context patterns: Bybit uppercase alphanumeric/underscore;
  Binance Unicode word characters; Hyperliquid exact-case coin names with an
  optional lowercase dex prefix. Paths, queries, whitespace and separators
  outside those patterns are rejected before any request.
- Both sides must be nonempty, strictly price-sorted, duplicate-free, and have
  finite positive prices **and quantities**. Bids descend, asks ascend, and the
  top bid must be strictly below the top ask. Zero quantities reject the whole
  snapshot; they are not silently discarded as if this were a delta feed.
- Malformed arrays, errors, non-200 HTTP statuses (including redirects), Bybit
  nonzero `retCode`, Binance `code`/`error`, mismatched Bybit symbols and present
  Hyperliquid `coin` mismatches reject the whole snapshot. All received levels
  are validated before requested-depth truncation.
- Venue timestamps must be nonnegative JSON integers or null/missing, never
  coerced from floats, strings, or bool. Sequence IDs accept nonnegative JSON
  integers or digit strings and preserve their exact decimal text, including
  leading zeroes. No floating-point conversion loses large-ID precision.
- Invalid caller arguments raise `ValueError`; upstream/network/JSON/book
  failures raise `LiquidityDataError` (a `RuntimeError`) with safe messages, no
  response body. No partially valid book is returned.

## Official references checked

- Bybit API documentation: `https://bybit-exchange.github.io/docs/v5/market/orderbook`
- Binance official connector implementation and documented supported limits:
  `https://github.com/binance/binance-futures-connector-python/blob/main/binance/um_futures/market.py`
- Binance documentation (HTTP 202 empty challenge response from this host):
  `https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Order-Book`
- Hyperliquid official info endpoint documentation:
  `https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint`

Offline fixture tests do not call exchanges. Manual public live probes succeeded
for Bybit `BTCUSDT` (200/200), Binance `BTCUSDT` (100/100), Hyperliquid `BTC`
(20/20), and HIP-3 `xyz:CL` (20/20). Full actual snapshots and UTC timestamps
are saved outside the repo at
`/Users/maxbelov/.hermes/cache/scratch/liquidity-depth-live.json`.
