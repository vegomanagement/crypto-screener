# Sampled liquidity archive and collector contract

`liquidity_store.py` uses only the Python standard library. Importing it does
not create files or start workers. Parent Flask integration owns the explicit,
separate path, directory creation, route validation/authentication and lifecycle.
Never use `signals.db` (that basename is rejected); no signal schema is touched.

## Store

```python
store = LiquidityStore(path, retention_seconds=604800, max_bytes=134217728)
inserted = store.append(snapshot)  # optional now_ms=... for deterministic tests
result = store.history(exchange, symbol, start_ms, end_ms, max_frames=360)
metrics = store.stats()
# Stop collector before closing its caller-owned store.
store.close()
```

One zlib-compressed canonical JSON BLOB is stored per observation, with a primary
key/index `(exchange, symbol, observed_at)` and an oldest-first timestamp index.
An identical duplicate returns `False`; a different payload for the same key
raises `ValueError` and is never overwritten. No invented UUID is required.
All SQL values are bound parameters. Writes use `BEGIN IMMEDIATE`; range reads
use a coherent read transaction. A singleton supports concurrent callers via
an RLock, and WAL supports independent concurrent SQLite readers/connections.

Required snapshot fields are `exchange`, `symbol`, `observed_at`, `bids`, `asks`.
`observed_at` is a nonnegative UTC millisecond integer; optional upstream `time`
is an integer or null (a source may not publish it). Each side is a nonempty
list of up to 5,000 numeric `[price, quantity]` pairs, price > 0, quantity >= 0,
with no booleans or non-finite values. Root must be a JSON object; optional
metadata is preserved and serialized with NaN/Infinity forbidden. Warnings,
when supplied, are a list of up to 100 strings of at most 2,000 characters.
Exchange/symbol identifiers are nonempty strings of at most 80 characters, with
no control characters. Each snapshot has a 2 MiB uncompressed ceiling and must
also fit the configured compressed archive cap. The source adapter remains
responsible for exchange-specific symbol validation, book ordering, crossed
books, and native units. Snapshots over 10 seconds in the future are rejected.

### History semantics

Ranges are inclusive, ordered UTC milliseconds, at most seven days; `max_frames`
is an integer from 10 through 600. Empty ranges return empty frames, null
coverage/latest and an explanatory warning, never a fake book.

- `samples`: number of actual stored observations in the range, before
  downsampling (known unreadable selected observations are excluded).
- `coverage_start`, `coverage_end`: actual first/last observation timestamps,
  not requested boundaries and not a claim of continuous coverage.
- `frames`: ordered `{observed_at, bids, asks}`; one **real latest observation**
  per equal observed-time bucket, at most `max_frames`. Quantities are never
  summed over time, and missing books are never interpolated or persisted.
- `latest`: latest real snapshot **inside the requested range**, preserving
  source metadata/warnings. It is NOT latest overall or a current-time book.
  Ask for a range ending now for current history and use collector status to
  assess freshness; historical queries cannot leak a future snapshot.
- `gaps`: `{start, end}` for adjacent raw samples separated by more than 20s.
  The renderer must break strips at these intervals, not extend books through
  them; silence before first/after last sample is also not coverage.
- `sample_period_ms`: 10,000 (nominal default, not a guarantee of actual cadence).
- `frame_bucket_ms`: actual query downsampling bucket width.
- `warnings`: empty/unreadable/gap diagnostics. At most `max_frames` compressed
  payloads are fetched/inflated per history call; timestamp metadata is scanned
  to describe all samples and gaps. Corrupted selected blobs are omitted with
  warnings; the store does not claim a full integrity scan of unselected blobs.

### Retention and disk accounting

Every successful append/duplicate cleanup deletes observations older than
`now - retention_seconds`, then evicts globally oldest observations until
`SUM(length(payload)) <= max_bytes`. An inactive archive does not launch a
cleanup timer: retention is enforced on the next append. Retention is a maximum
age, not a promise of seven complete days (the byte cap can evict earlier).

The default 128 MiB cap is explicitly a **compressed payload cap**, NOT a hard
physical file quota. SQLite rows/indexes/page slack, WAL and SHM are additional.
`stats()` exposes `samples`, `compressed_bytes`, `max_bytes`, `retention_seconds`,
actual `database_bytes`, `wal_bytes`, `shm_bytes`, logical page bytes,
`free_bytes`, coverage and an explicit overhead warning. There is no unsupported
claim that the actual database file fits 128 MiB.

New archives enable `auto_vacuum=INCREMENTAL` before creating their schema;
append runs only `incremental_vacuum(64)`, never a full per-append VACUUM.
WAL auto-checkpoints at 128 pages and has a 4 MiB journal-size target. These are
reclamation/soft checkpoint settings, not a hard WAL bound: an external long
reader can pin WAL pages and delay truncation. Reused free pages and incremental
reclamation control normal growth; inspect exposed physical metrics and use an
external filesystem quota if an absolute disk bound is required.

## Collector

```python
collector = LiquidityCollector(store, fetch_snapshot,
                               period_seconds=10, max_markets=6, lease_seconds=90)
status = collector.watch(exchange, symbol, owner)
collector.start()  # explicit parent action, only after a UI watch is enabled
collector.unwatch(exchange, symbol, owner)
collector.stop()   # idempotent; joins in-flight work, store remains open
```

`CapacityError` is exported and subclasses `ValueError`: map it to HTTP 429
before the ordinary validation `ValueError` -> HTTP 400 handler. Limits are six
markets and **24 total owner/market subscriptions**, not 24 per market. The same
owner renewing the same market is idempotent. Owners are nonempty strings up to
80 characters. Limits never silently evict another user's watch.

A pane should create a stable random client owner, renew `watch` every 45s and
`unwatch` on disable/close. Browser close is best effort: absent renewals, leases
expire after 90s and collection stops. History survives lease expiry and process
restart. This is opt-in bounded observation, not background collection of every
watchlist market. The parent must display the lease-expiry limitation.

There is one serial daemon worker, no executor and no non-daemon worker pool.
Multiple owners of one market share one fetch. A nonblocking collection lock
prevents concurrent `collect_once`/worker overlap globally. Each due market gets
at most one attempt; next due time is completion + period, so slow calls do not
create catch-up bursts. The service wakes via Event at most once per second.
`start()` is idempotent and can restart after `stop()`; no constructor/watch
starts threads. `collect_once(now_ms=...)` is synchronous for deterministic
finite tests, and optional constructor `clock_ms=` also controls watch leases.

**Timeout/lifecycle boundary:** the injected adapter must enforce an 8-second
network timeout. The collector cannot safely cancel arbitrary blocking Python
callables and does not pretend to. `stop()` sets an Event, skips further markets,
and joins the one in-flight call before returning (normally <= one adapter
request plus SQLite cleanup), rather than abandoning executor threads. No live
network tests are required for the store; caller owns adapter timeout testing.

`status(exchange,symbol)` returns `active`, `owners`, `expires_at`, `running`,
`collecting` (leased and running), `inflight`, `last_attempt_at`,
`last_observed_at` / `last_success` (source observation time), `unavailable`,
`error`, `stale`, `period_ms`, `lease_seconds`, and warnings. Errors keep the
last successful observation timestamp, set unavailable, and write no replacement
book; they are not a fresh observation. Stale is measured from observed time,
not when an old/duplicate payload was fetched. Expired state is discarded to
bound memory, so status becomes inactive with null timestamps; persisted history
is the source of old books. Collector is process-local: the Flask application
must create one singleton service, not one collector per request/WSGI worker.

## Verification

Run scoped tests/lint with the existing environment (no installs):

```sh
.venv/bin/python -m pytest tests/test_liquidity_store.py -q
.venv/bin/python -m ruff check liquidity_store.py tests/test_liquidity_store.py
```

All tests use `tmp_path` archives and injected finite snapshots/clocks. They do
not open the actual signal database or call public exchanges.
