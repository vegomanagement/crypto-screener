/* Seconds candles (1s/5s/15s/30s) built only from real trades, plus candle-close countdown.
 * A second without trades has no candle (gaps stay gaps). Bars whose trade coverage is not
 * proven complete (stream started mid-bucket, live id gap) are flagged `partial`.
 * View-only module: no orders, no synthetic ticks.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.WorkspaceSeconds = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const SECONDS = { '1s': 1000, '5s': 5000, '15s': 15000, '30s': 30000 };
  const BACKFILL_MINUTES = { '1s': 5, '5s': 10, '15s': 15, '30s': 15 };
  const MAX_BARS = 2000;
  const SCALE = 18n;
  const UNIT = 10n ** SCALE;
  const DAY = 86400000, WEEK_ANCHOR = 4 * DAY; // 1970-01-05 was a Monday

  const isSeconds = value => Object.hasOwn(SECONDS, value);
  const bucketOpen = (timeMs, step) => Math.floor(timeMs / step) * step;

  // Exact non-negative decimal string -> BigInt units of 1e-18 (volume sums stay exact).
  function units(text) {
    if (typeof text === 'number' && Number.isFinite(text)) text = String(text);
    if (typeof text !== 'string' || !/^\d+(?:\.\d+)?$/.test(text)) throw new Error('invalid decimal');
    const [whole, frac = ''] = text.split('.');
    if (frac.length > Number(SCALE)) throw new Error('too many decimals');
    return BigInt(whole) * UNIT + BigInt(frac.padEnd(Number(SCALE), '0') || '0');
  }
  function fmtUnits(value) {
    const whole = value / UNIT, frac = (value % UNIT).toString().padStart(Number(SCALE), '0').replace(/0+$/, '');
    return frac ? `${whole}.${frac}` : `${whole}`;
  }

  // Venue trade row -> { time, price (Number), qty (units), side, id }; ids are numeric only on Binance.
  function normalize(venue, row) {
    if (!row || typeof row !== 'object') throw new Error('invalid trade');
    let time, price, qty, side, id;
    if (venue === 'binance') { time = row.T; price = row.p; qty = row.q; side = row.m ? 'bid' : 'ask'; id = row.a; }
    else if (venue === 'bybit') { time = row.T ?? row.time; price = row.p ?? row.price; qty = row.v ?? row.size; side = (row.S ?? row.side) === 'Buy' ? 'ask' : 'bid'; id = String(row.i ?? row.execId); }
    else if (venue === 'hyperliquid') { time = row.time; price = row.px; qty = row.sz; side = row.side === 'B' ? 'ask' : 'bid'; id = String(row.tid); }
    else throw new Error('unsupported venue');
    time = Number(time);
    const numericPrice = Number(price);
    if (!Number.isSafeInteger(time) || time <= 0 || !(numericPrice > 0) || !Number.isFinite(numericPrice)) throw new Error('invalid trade');
    if (venue === 'binance' && !Number.isSafeInteger(id)) throw new Error('binance: invalid id');
    return { time, price: numericPrice, qty: units(String(qty)), side, id };
  }

  // Aggregator: open/close follow trade order (Binance aggTrade id; otherwise time, then arrival).
  function createAggregator(step) {
    if (!Number.isInteger(step) || step <= 0 || step % 1000) throw new Error('step must be whole seconds');
    const bars = new Map();
    let seq = 0;
    const before = (a, b) => (a[0] !== b[0] ? a[0] < b[0] : a[1] < b[1]);
    function add(trade) {
      const key = [typeof trade.id === 'number' ? trade.id : trade.time, ++seq];
      const t = bucketOpen(trade.time, step);
      let bar = bars.get(t);
      if (!bar) {
        bar = { t, open: trade.price, high: trade.price, low: trade.price, close: trade.price, first: key, last: key,
          volume: 0n, buyVolume: 0n, trades: 0, partial: false, origin: 'live' };
        bars.set(t, bar);
      }
      if (before(key, bar.first)) { bar.first = key; bar.open = trade.price; }
      if (before(bar.last, key)) { bar.last = key; bar.close = trade.price; }
      bar.high = Math.max(bar.high, trade.price); bar.low = Math.min(bar.low, trade.price);
      bar.volume += trade.qty; if (trade.side === 'ask') bar.buyVolume += trade.qty;
      bar.trades += 1;
      prune();
      return bar;
    }
    // Server bar from /api/seconds; its first/last ids keep live continuation ordered exactly.
    function addServer(row, origin = 'backfill') {
      const open = Number(row.o), high = Number(row.h), low = Number(row.l), close = Number(row.c);
      if (![row.t, open, high, low, close].every(Number.isFinite) || row.t % step) throw new Error('invalid server bar');
      bars.set(row.t, { t: row.t, open, high, low, close, first: [row.first_id ?? row.t, 0], last: [row.last_id ?? row.t, 0],
        volume: units(row.v), buyVolume: units(row.buy_v ?? '0'), trades: row.n ?? 0, partial: false, origin });
      prune();
    }
    function prune() {
      if (bars.size <= MAX_BARS) return;
      const keys = [...bars.keys()].sort((a, b) => a - b);
      keys.slice(0, bars.size - MAX_BARS).forEach(key => bars.delete(key));
    }
    const markPartial = (fromMs, toMs) => { for (const bar of bars.values()) if (bar.t + step > fromMs && bar.t <= toMs) bar.partial = true; };
    const list = () => [...bars.values()].sort((a, b) => a.t - b.t);
    return { step, add, addServer, markPartial, list, get: t => bars.get(t), get size() { return bars.size; } };
  }

  // Chart rows: time in seconds; closed = bucket ended by clock or by a later trade.
  function view(aggregator, nowMs, latestTradeMs = 0) {
    const closed = [], step = aggregator.step;
    let forming = null;
    for (const bar of aggregator.list()) {
      const row = { time: bar.t / 1000, open: bar.open, high: bar.high, low: bar.low, close: bar.close,
        volume: Number(fmtUnits(bar.volume)), volumeExact: fmtUnits(bar.volume), trades: bar.trades, partial: bar.partial, origin: bar.origin };
      if (bar.t + step <= nowMs || bar.t + step <= latestTradeMs) closed.push(row);
      else forming = row;
    }
    return { closed, forming };
  }

  // ── candle-close countdown (all intervals, UTC) ─────────────────────────────
  function durationMs(interval) {
    if (isSeconds(interval)) return SECONDS[interval];
    if (interval === 'D') return DAY;
    if (interval === '2D' || interval === '3D') return Number(interval[0]) * DAY;
    if (interval === 'W') return 7 * DAY;
    if (/^[1-9]\d{0,5}$/.test(interval)) return Number(interval) * 60000;
    return null;
  }
  function nextCloseMs(interval, nowMs) {
    if (interval === 'M') {
      const d = new Date(nowMs);
      return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
    }
    const duration = durationMs(interval);
    if (!duration) return null;
    const anchor = interval === 'W' ? WEEK_ANCHOR : 0;
    return Math.floor((nowMs - anchor) / duration) * duration + anchor + duration;
  }
  function formatCountdown(ms) {
    if (!Number.isFinite(ms) || ms < 0) return '';
    const total = Math.ceil(ms / 1000);
    const days = Math.floor(total / 86400), h = Math.floor(total % 86400 / 3600), m = Math.floor(total % 3600 / 60), s = total % 60;
    const two = n => String(n).padStart(2, '0');
    if (days) return `${days}d ${two(h)}:${two(m)}`;
    if (h) return `${h}:${two(m)}:${two(s)}`;
    return `${two(m)}:${two(s)}`;
  }
  // UTC session (day) starts within [fromMs, toMs].
  function sessionStarts(fromMs, toMs) {
    const out = [];
    for (let t = Math.ceil(fromMs / DAY) * DAY; t <= toMs && out.length < 400; t += DAY) out.push(t);
    return out;
  }

  // ── live controller: Binance backfill + live trades; Bybit/HL live only ─────
  const clock = ms => new Date(ms).toISOString().slice(11, 19);
  function createController(options) {
    const { exchange, symbol, interval, hub } = options;
    const fetchFn = options.fetch, now = options.now || Date.now, onChange = options.onChange || (() => {});
    const every = options.setInterval || setInterval, stopEvery = options.clearInterval || clearInterval;
    if (!isSeconds(interval)) throw new Error('not a seconds interval');
    const step = SECONDS[interval];
    const agg = createAggregator(step);
    const st = { state: 'loading', stream: 'connecting', message: '', liveSince: null, backfill: null, gaps: 0, errors: 0,
      lastId: null, latestTradeMs: 0, firstLiveBucket: null };
    const seen = new Set(), seenOrder = [];
    let buffer = exchange === 'binance' ? [] : null, disposed = false, off = null, request = null, timer = null, formingOpen = null;
    const emit = () => { if (!disposed) onChange(api); };

    function ingest(trade) {
      const previous = st.latestTradeMs;
      let gap = false;
      if (exchange === 'binance') {
        if (st.lastId !== null && trade.id <= st.lastId) return;
        gap = st.lastId !== null && trade.id > st.lastId + 1;
        st.lastId = Math.max(st.lastId ?? trade.id, trade.id);
      } else {
        if (seen.has(trade.id)) return;
        seen.add(trade.id); seenOrder.push(trade.id);
        if (seenOrder.length > 20000) seen.delete(seenOrder.shift());
      }
      const proven = exchange === 'binance' && st.lastId !== null && st.backfill?.ok === true;
      const bar = agg.add(trade);
      if (gap) { st.gaps += 1; agg.markPartial(previous || trade.time, trade.time); }
      if (st.firstLiveBucket === null) {
        st.firstLiveBucket = bar.t; st.liveSince = trade.time;
        // Only an id-contiguous backfill proves nothing earlier in this bucket is missing.
        if (!proven) bar.partial = true;
      }
      st.latestTradeMs = Math.max(st.latestTradeMs, trade.time);
      formingOpen = Math.max(formingOpen ?? 0, bar.t);
    }
    function onEvent(event) {
      if (disposed) return;
      if (event.type === 'status') { st.stream = event.status; emit(); return; }
      if (event.type !== 'trades' || event.venue !== exchange || event.symbol !== symbol) return;
      const trades = [];
      for (const row of event.rows) { try { trades.push(normalize(exchange, row)); } catch (_) { st.errors += 1; } }
      if (buffer) { buffer.push(...trades); return; }
      trades.forEach(ingest);
      if (st.state === 'loading') st.state = 'live';
      emit();
    }
    async function backfill() {
      const query = new URLSearchParams({ exchange, symbol, minutes: String(BACKFILL_MINUTES[interval]), seconds: String(step / 1000) });
      request = new AbortController();
      try {
        const response = await fetchFn(`/api/seconds?${query}`, { signal: request.signal, cache: 'no-store', headers: { Accept: 'application/json' } });
        const payload = await response.json().catch(() => null);
        if (disposed) return;
        if (!response.ok || payload?.exchange !== exchange || payload.symbol !== symbol || payload.seconds !== step / 1000 || !Array.isArray(payload.bars)) {
          throw new Error(payload?.error || `HTTP ${response.status}`);
        }
        payload.bars.forEach(bar => agg.addServer(bar));
        st.lastId = Number.isSafeInteger(payload.last_trade_id) ? payload.last_trade_id : null;
        st.backfill = { ok: true, from: payload.complete_from_ms, asOf: payload.as_of, trades: payload.trades, requests: payload.requests,
          truncated: Boolean(payload.truncated), bars: payload.bars.length };
      } catch (error) {
        if (disposed || error?.name === 'AbortError') return;
        st.backfill = { ok: false, error: String(error?.message || error) };
      } finally { request = null; }
      const pending = buffer || []; buffer = null;
      pending.forEach(ingest);
      st.state = 'live';
      emit();
    }
    function start() {
      off = hub?.subscribeTrades ? hub.subscribeTrades({ venue: exchange, symbol }, onEvent) : null;
      if (!off) { st.state = 'unsupported'; st.message = 'live trade stream unavailable'; buffer = null; emit(); return api; }
      if (exchange === 'binance' && fetchFn) backfill(); else { buffer = null; emit(); }
      // Close the forming bucket on the clock even when no further trade arrives.
      timer = every(() => { if (formingOpen !== null && now() >= formingOpen + step) { formingOpen = null; emit(); } }, 250);
      return api;
    }
    function dispose() {
      disposed = true; off?.(); off = null; request?.abort(); request = null;
      if (timer !== null) stopEvery(timer); timer = null;
    }
    function statusText() {
      const parts = [];
      if (st.state === 'unsupported') return `Seconds candles unavailable: ${st.message}.`;
      if (st.backfill?.ok) {
        parts.push(`Backfilled from Binance aggTrades since ${st.backfill.from ? clock(st.backfill.from) : '—'} UTC (${st.backfill.trades} trades, ${st.backfill.requests} requests)`);
        if (st.backfill.truncated) parts.push('request budget cut older history');
      } else if (st.backfill && !st.backfill.ok) parts.push(`Backfill failed (${st.backfill.error}); live only`);
      else if (exchange !== 'binance') parts.push('No public trade history for this venue; candles start with the live stream');
      parts.push(st.liveSince ? `live since ${clock(st.liveSince)} UTC` : st.state === 'loading' ? 'loading real trades…' : 'waiting for first live trade…');
      const partial = agg.list().filter(bar => bar.partial).length;
      if (partial) parts.push(`${partial} partial bar(s) grey`);
      if (st.gaps) parts.push(`${st.gaps} live id gap(s)`);
      if (st.errors) parts.push(`${st.errors} malformed trade row(s) ignored`);
      parts.push('seconds without trades have no candle');
      return parts.join(' · ');
    }
    const api = { start, dispose, statusText, view: () => view(agg, now(), st.latestTradeMs), get state() { return { ...st }; }, aggregator: agg, interval, step };
    return api;
  }

  return { SECONDS, BACKFILL_MINUTES, MAX_BARS, isSeconds, bucketOpen, units, fmtUnits, normalize, createAggregator, view,
    durationMs, nextCloseMs, formatCountdown, sessionStarts, createController };
});
