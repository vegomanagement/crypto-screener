'use strict';
// Pure-function tests: big-trade bubbles (static/indicators/bubbles*.js) and abnormal volume.
const assert = require('node:assert/strict');
const test = require('node:test');
const B = require('../static/indicators/bubbles.js');
const R = require('../static/indicators/bubbles-render.js');
const V = require('../static/indicators/volume-anomaly.js');

const T0 = 1_760_000_000_000;
const tr = (id, dt, price, qty, side) => ({ id, time: T0 + dt, price, qty, side, notional: price * qty });
const S = (patch = {}) => ({ ...B.defaults(), ...patch });

test('trade normalisation: WS maker flag → side, compact REST rows', () => {
  assert.deepEqual(B.tradeFromWs('binance', { a: 5, T: T0, p: '100.5', q: '2', m: true }), { id: 5, time: T0, price: 100.5, qty: 2, side: -1, notional: 201 });
  assert.equal(B.tradeFromWs('binance', { a: 6, T: T0, p: '100', q: '1', m: false }).side, 1, 'buyer is taker → BUY');
  assert.equal(B.tradeFromWs('bybit', { i: 'x', T: T0, p: '10', v: '3', S: 'Sell' }).side, -1);
  assert.equal(B.tradeFromWs('hyperliquid', { tid: 9, time: T0, px: '10', sz: '3', side: 'B' }).side, 1);
  assert.equal(B.tradeFromWs('bybit', { i: 'x', T: T0, p: '10', v: '3', S: '?' }), null);
  assert.equal(B.tradeFromRow([1, T0, '0', '1', 1]), null, 'no zero prices');
});

test('fixed threshold: notional ≥ minUsd, inclusive; adaptive warms up then uses nearest-rank percentile', () => {
  const dist = new B.RollingDistribution({ windowMs: 60000 });
  assert.deepEqual(B.threshold(S({ mode: 'fixed', minUsd: 1000 }), dist), { value: 1000, basis: 'fixed', n: 0 });
  assert.equal(B.threshold(S({ mode: 'adaptive', minSamples: 10 }), dist).value, null, 'warming → no bubbles in adaptive mode');
  for (let i = 1; i <= 100; i += 1) dist.add(T0 + i, i);
  assert.equal(dist.quantile(99), 99); assert.equal(dist.quantile(99.5), 100); assert.equal(dist.quantile(50), 50); assert.equal(dist.quantile(100), 100);
  assert.equal(B.threshold(S({ mode: 'adaptive', percentile: 95, minSamples: 10 }), dist).value, 95);
  assert.equal(B.threshold(S({ mode: 'both', percentile: 95, minUsd: 120, minSamples: 10 }), dist).value, 120, 'both = max(fixed, percentile)');
  assert.equal(B.threshold(S({ mode: 'both', percentile: 95, minUsd: 10, minSamples: 10 }), dist).value, 95);
  assert.equal(B.threshold(S({ mode: 'both', minUsd: 10, minSamples: 1000 }), dist).value, 10, 'both falls back to fixed while warming');
});

test('rolling distribution expires by time and caps samples, staying sorted', () => {
  const d = new B.RollingDistribution({ windowMs: 1000, maxSamples: 5 });
  [5, 1, 4, 2, 3].forEach((v, i) => d.add(T0 + i, v));
  assert.deepEqual(d.sorted, [1, 2, 3, 4, 5]);
  d.add(T0 + 5, 10); // cap → drops the oldest (5)
  assert.deepEqual(d.sorted, [1, 2, 3, 4, 10]);
  d.add(T0 + 1003, 7); // expires t ≤ T0+2
  assert.deepEqual(d.sorted, [2, 3, 7, 10]);
});

test('aggregation window: same side within window and tolerance merges; other side never splits it', () => {
  const agg = new B.PrintAggregator({ windowMs: 100, tolerance: 0.2 });
  const out = [];
  out.push(...agg.push(tr(1, 0, 100, 1, 1)), ...agg.push(tr(2, 30, 99, 5, -1)), ...agg.push(tr(3, 60, 100.2, 2, 1)));
  assert.equal(out.length, 0, 'still open');
  out.push(...agg.push(tr(4, 90, 100.5, 1, 1))); // price outside tolerance → closes the buy print, opens a new one
  assert.equal(out.length, 1);
  assert.deepEqual([out[0].trades, out[0].qty, out[0].firstId, out[0].lastId], [2, 3, 1, 3]);
  assert.ok(Math.abs(out[0].price - (100 * 1 + 100.2 * 2) / 3) < 1e-9, 'VWAP');
  assert.ok(Math.abs(out[0].notional - (100 + 200.4)) < 1e-9, 'notional = Σ price·qty');
  out.push(...agg.push(tr(5, 250, 100, 1, 1))); // time passes window → sell(2) and buy(4) flush
  assert.deepEqual(out.map(p => [p.side, p.firstId]), [[1, 1], [-1, 2], [1, 4]]);
  assert.deepEqual(agg.flush(T0 + 1000).map(p => p.firstId), [5]);
  const single = new B.PrintAggregator({ windowMs: 0 });
  assert.equal(single.push(tr(1, 0, 100, 1, 1)).length, 1, 'window 0 → every trade is a print');
  const strict = new B.PrintAggregator({ windowMs: 100, tolerance: 0 });
  strict.push(tr(1, 0, 100, 1, 1)); assert.equal(strict.push(tr(2, 1, 100.1, 1, 1)).length, 1, 'tolerance 0 → exact price only');
  const bounded = new B.PrintAggregator({ windowMs: 100, tolerance: 1 });
  bounded.push(tr(1, 0, 100, 1, 1)); bounded.push(tr(2, 90, 100, 1, 1));
  assert.equal(bounded.push(tr(3, 150, 100, 1, 1)).length, 1, 'window counts from the FIRST trade, so prints stay bounded');
});

test('bubble radius: area ∝ notional (sqrt), clamped to [rMin, rMax]', () => {
  assert.equal(B.radius(1000, 1000, 3, 30), 3);
  assert.equal(B.radius(4000, 1000, 3, 30), 6);
  assert.equal(B.radius(9000, 1000, 3, 30), 9);
  const a = B.radius(2000, 1000, 3, 30) ** 2, b = B.radius(8000, 1000, 3, 30) ** 2;
  assert.ok(Math.abs(b / a - 4) < 1e-9, 'area ratio = notional ratio');
  assert.equal(B.radius(1e9, 1000, 3, 30), 30); assert.equal(B.radius(10, 1000, 3, 30), 3);
  assert.ok(Math.abs(R.effectiveMaxRadius(S({ rMin: 3, rMax: 26 }), 6) - 5.4) < 1e-9, 'zoomed out: diameter ≤ ~2 bars');
  assert.equal(R.effectiveMaxRadius(S({ rMin: 3, rMax: 26 }), 2), 4, 'never below rMin + 1');
  assert.equal(R.effectiveMaxRadius(S({ rMin: 3, rMax: 26, fitBar: false }), 6), 26);
});

test('store: causal threshold, Binance live dedupe by aggTrade id, gap count, memory bound', () => {
  const store = new B.BigTradeStore({ venue: 'binance', settings: S({ mode: 'fixed', minUsd: 1000, aggregate: true, aggWindowMs: 100, aggTicks: 1, maxStored: 3 }), tick: 0.1 });
  store.onRows([{ a: 11, T: T0 + 500, p: '100', q: '20', m: false }]); // arrives before the backfill → buffered
  store.loadBackfill({ coverage: 'exact', oldest_ms: T0, as_of: T0 + 400, last_trade_id: 10, trades: [
    [8, T0, '100', '6', 1], [9, T0 + 50, '100.1', '6', 1], [10, T0 + 60, '100', '1', -1]] });
  store.onRows([{ a: 10, T: T0 + 60, p: '100', q: '1', m: true }]); // duplicate of the backfill → ignored
  store.onRows([{ a: 13, T: T0 + 900, p: '100', q: '30', m: true }]); // id 12 missing → gap
  store.flushIdle(T0 + 5000);
  assert.equal(store.gaps, 1);
  assert.deepEqual(store.big.map(p => [p.side, p.trades, Math.round(p.notional)]), [[1, 2, 1201], [1, 1, 2000], [-1, 1, 3000]]);
  assert.ok(store.big.every(p => p.threshold === 1000));
  assert.equal(store.big[0].source, 'backfill'); assert.equal(store.big[1].source, 'live');
  for (let i = 0; i < 5; i += 1) store.onRows([{ a: 14 + i, T: T0 + 2000 + i * 500, p: '100', q: '50', m: false }]);
  store.flushIdle(T0 + 9000);
  assert.equal(store.big.length, 3, 'bounded per pane'); assert.ok(store.big.every((p, i, a) => !i || a[i - 1].time <= p.time));
  assert.deepEqual(store.barSummary(T0, 60000), { count: 3, buys: 3, sells: 0, net: 15000 });
});

test('store: Bybit dedupe by trade id against the backfill', () => {
  const store = new B.BigTradeStore({ venue: 'bybit', settings: S({ mode: 'fixed', minUsd: 10, aggregate: false }), tick: 0.01 });
  store.loadBackfill({ coverage: 'partial', oldest_ms: T0, as_of: T0 + 100, last_trade_id: null, trades: [['a', T0, '10', '2', 1]] });
  store.onRows([{ i: 'a', T: T0, p: '10', v: '2', S: 'Buy' }, { i: 'b', T: T0 + 200, p: '10', v: '2', S: 'Sell' }]);
  assert.deepEqual(store.big.map(p => p.firstId), ['a', 'b']);
});

test('render: draws ≤ maxVisible largest, buys filled / sells ring, hit-test finds the topmost bubble', () => {
  const store = new B.BigTradeStore({ venue: 'binance', settings: S({ mode: 'fixed', minUsd: 1, aggregate: false }) });
  const rows = Array.from({ length: 2000 }, (_, i) => [i, T0 + i * 10, '100', String(1 + (i % 50)), i % 2 ? 1 : -1]);
  store.loadBackfill({ coverage: 'exact', oldest_ms: T0, as_of: T0 + 1e6, last_trade_id: 1999, trades: rows });
  let fills = 0, strokes = 0, arcs = 0;
  const ctx = { save() {}, restore() {}, beginPath() {}, moveTo() {}, arc() { arcs += 1; }, fill() { fills += 1; }, stroke() { strokes += 1; }, fillText() {}, set fillStyle(v) {}, set strokeStyle(v) {}, set lineWidth(v) {}, set font(v) {}, set textAlign(v) {}, set textBaseline(v) {} };
  const view = { fromMs: T0, toMs: T0 + 1e6, xForMs: ms => (ms - T0) / 20, y: () => 100, barSpacing: 10, plotWidth: 1000, plotHeight: 500 };
  const out = R.draw(ctx, store, view, S({ maxVisible: 1200 }));
  assert.equal(out.inRange, 2000); assert.equal(out.drawn, 1200); assert.equal(arcs, 1200); assert.ok(out.capped);
  assert.ok(fills <= 8 && strokes <= 8, 'batched: one path per side × opacity bucket');
  const minDrawn = Math.min(...out.hits.map(h => h.p.notional));
  assert.ok(store.big.filter(p => p.notional > minDrawn).length <= 1200, 'largest first');
  const top = out.hits.at(-1);
  assert.equal(R.hitTest(out.hits, top.x, top.y), top.p);
  assert.equal(R.hitTest(out.hits, -50, -50), null);
  const text = R.tooltipText({ ...top.p, trades: 3, lastId: top.p.firstId + 2 }, { settings: S() });
  assert.match(text, /Aggregated from 3 aggTrades \(heuristic/); assert.match(text, /Taker (BUY|SELL)/); assert.match(text, /Binance/);
});

// ── abnormal volume ───────────────────────────────────────────────────────
const bars = vols => vols.map((volume, i) => ({ time: 1000 + i * 60, open: 10, close: 11, high: 12, low: 9, volume }));
const base = n => Array.from({ length: n }, (_, i) => 100 * Math.exp(0.2 * Math.sin(i * 1.7)));
function zOf(v, w) { const l = w.map(Math.log), m = l.reduce((a, x) => a + x, 0) / l.length, sd = Math.sqrt(l.reduce((a, x) => a + (x - m) ** 2, 0) / (l.length - 1)); return (Math.log(v) - m) / sd; }

test('z-score of log volume: tiers 2σ/3σ, bar excluded from its own window, needs a full lookback', () => {
  const vols = base(60); const w = vols.slice(50, 60);
  const v2 = Math.exp(Math.log(w.reduce((a, x) => a * x, 1)) / 10) * 1; // geometric mean → z≈0
  const s = { lookback: 10, method: 'zlog' };
  assert.ok(Math.abs(V.score(v2, w, 'zlog')) < 1e-9);
  const z = zOf(1000, w); assert.ok(Math.abs(V.score(1000, w, 'zlog') - z) < 1e-12);
  assert.equal(V.tierOf(1.99, V.defaults()), 0); assert.equal(V.tierOf(2, V.defaults()), 1); assert.equal(V.tierOf(3, V.defaults()), 2);
  const flags = V.compute(bars([...vols, 1000]), s);
  assert.equal(flags.length, 1); assert.equal(flags[0].index, 60); assert.equal(flags[0].tier, z >= 3 ? 2 : 1);
  assert.equal(V.compute(bars([1, 1, 1, 1000]), { lookback: 10 }).length, 0, 'no flag before a full lookback');
  assert.equal(V.score(0, w, 'zlog'), null, 'zero volume never flagged');
});

test('ratio and percentile methods', () => {
  const w = [1, 2, 3, 4, 100];
  assert.equal(V.score(9, w, 'median'), 3); assert.equal(V.score(22, w, 'sma'), 1);
  assert.equal(V.score(101, w, 'percentile'), 100); assert.equal(V.score(4, w, 'percentile'), 60);
  const s = { ...V.defaults(), method: 'percentile' };
  assert.equal(V.tierOf(100, s), 2); assert.equal(V.tierOf(96, s), 1); assert.equal(V.tierOf(94, s), 0);
  const m = { ...V.defaults(), method: 'median' }; assert.equal(V.tierOf(2.5, m), 1); assert.equal(V.tierOf(4, m), 2);
});

test('forming bar: scored against the same closed window, tier only rises as volume grows, equals the closed result', () => {
  const vols = base(55), closed = bars(vols), s = { lookback: 50 };
  const at = v => V.compute(closed, s, { time: closed.at(-1).time + 60, volume: v }).filter(f => f.forming);
  const tiers = [50, 200, 300, 400, 600, 1000].map(v => at(v)[0]?.tier || 0);
  assert.deepEqual(tiers, tiers.slice().sort((a, b) => a - b), 'monotonic → no flicker');
  assert.ok(tiers.at(-1) === 2 && tiers[0] === 0);
  const closedAfter = V.compute(bars([...vols, 1000]), s).at(-1);
  assert.equal(closedAfter.score, at(1000)[0].score); assert.equal(closedAfter.forming, false);
  assert.equal(V.compute(closed, s, { time: closed.at(-1).time, volume: 1e9 }).filter(f => f.forming).length, 0, 'not newer → ignored');
});

test('delta sign classification: initiative vs absorption vs balanced', () => {
  const up = { open: 10, close: 12, high: 12.5, low: 9.5 }, down = { open: 12, close: 10, high: 12.5, low: 9.5 }, doji = { open: 10, close: 10.1, high: 12, low: 8 };
  assert.equal(V.classifyDelta({ delta: 30, total: 100, ...up }).kind, 'initiative');
  assert.equal(V.classifyDelta({ delta: -30, total: 100, ...down }).kind, 'initiative');
  assert.equal(V.classifyDelta({ delta: 30, total: 100, ...down }).kind, 'absorption');
  assert.equal(V.classifyDelta({ delta: -30, total: 100, ...doji }).kind, 'absorption', 'strong delta, bar barely moved');
  assert.equal(V.classifyDelta({ delta: 10, total: 100, ...up }).kind, 'balanced');
  assert.equal(V.classifyDelta({ delta: null, total: 100, ...up }).kind, 'unknown');
  assert.equal(V.classifyDelta({ delta: 30, total: 100, ...up }, 40).kind, 'balanced', 'threshold configurable');
  const ONE = 10n ** 18n;
  const fd = V.footprintDelta({ coverage: 'complete', forming: false, bar: { levels: new Map([['1', { bid: 3n * ONE, ask: 5n * ONE / 2n }], ['2', { bid: 0n, ask: ONE }]]) } });
  assert.deepEqual(fd, { delta: 0.5, total: 6.5, coverage: 'complete', complete: true });
  assert.equal(V.footprintDelta({ coverage: 'complete', forming: true, bar: { levels: new Map() } }).complete, false, 'forming is never "complete"');
});

test('controller: backfill waits for the live stream, retries a busy budget, disable cancels everything', async () => {
  const timers = new Map(); let nextTimer = 1, listener = null, unsubscribed = 0;
  const later = (fn, ms) => { const id = nextTimer++; timers.set(id, { fn: () => { timers.delete(id); fn(); }, ms }); return id; };
  const cancel = id => timers.delete(id);
  const hub = { subscribeTrades: (cfg, fn) => { listener = fn; return () => { unsubscribed += 1; }; } };
  const calls = []; let fail = true;
  const fetch = async url => { calls.push(url); return { ok: !fail, status: fail ? 502 : 200, json: async () => fail ? { error: 'binance: aggTrades rate budget busy' }
    : { exchange: 'binance', symbol: 'BTCUSDT', coverage: 'exact', oldest_ms: T0, as_of: T0 + 10, last_trade_id: 1, trades: [[1, T0, '100', '1000', 1]], warnings: [] } }; };
  const c = R.createController({ fetch, hub, setTimeout: later, clearTimeout: cancel, setInterval: () => 0, clearInterval: () => {}, now: () => T0 + 100 });
  c.enable({ exchange: 'binance', symbol: 'BTCUSDT', tick: 0.1, settings: S({ mode: 'fixed', minUsd: 1000 }) });
  assert.equal(calls.length, 0, 'no backfill before the stream is up');
  listener({ type: 'status', status: 'connecting' }); assert.equal(calls.length, 0);
  listener({ type: 'status', status: 'live' }); await new Promise(r => setImmediate(r));
  assert.equal(calls.length, 1); assert.equal(c.status.state, 'live-only'); assert.match(c.statusText(), /retrying/);
  const retryTimer = [...timers.values()].find(t => t.ms === 15000); assert.ok(retryTimer, 'retry scheduled');
  fail = false; retryTimer.fn(); listener({ type: 'status', status: 'live' }); await new Promise(r => setImmediate(r));
  assert.equal(calls.length, 2); assert.equal(c.status.state, 'ready'); assert.equal(c.store.big.length, 0, 'open print not yet final');
  c.store.flushIdle(T0 + 10000); assert.equal(c.store.big.length, 1);
  c.disable(); assert.equal(c.store, null); assert.ok(unsubscribed >= 2); assert.equal(timers.size, 0, 'timers cancelled');
});
