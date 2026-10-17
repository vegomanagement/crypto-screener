'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const ob = require(path.join(__dirname, '../static/orderbook.js'));
const fp = require(path.join(__dirname, '../static/footprint.js'));
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/footprint_parity.json'), 'utf8'));

const SYM = 'BTCUSDT';
const depth = (U, u, pu, b = [], a = []) => ({ e: 'depthUpdate', E: 1, s: SYM, U, u, pu, b, a });
const snap = (lastUpdateId, bids, asks) => ({ lastUpdateId, bids, asks });
const SNAP_BIDS = [['100.0', '1'], ['99.9', '2'], ['99.0', '5']];
const SNAP_ASKS = [['100.1', '1'], ['100.2', '3'], ['101.0', '4']];

test('price index is exact on the tick grid and refuses off-grid prices', () => {
  assert.equal(ob.priceIndex('0.3', 0.1), 3);
  assert.equal(ob.priceIndex('81545.10', 0.1), 815451);
  assert.equal(ob.priceIndex('0.0100', 0.01), 1);
  assert.throws(() => ob.priceIndex('100.05', 0.1));
  assert.throws(() => ob.priceIndex('-1', 0.1));
  assert.throws(() => ob.priceIndex('abc', 0.1));
});

test('binance: buffered events before the snapshot are dropped/applied per the official rule', () => {
  const s = new ob.BinanceSync(SYM, 0.1);
  s.onEvent(depth(90, 95, 89, [['100.0', '9']]));         // u < lastUpdateId → dropped
  s.onEvent(depth(96, 105, 95, [['99.9', '0']], [['100.1', '7']])); // U <= 100 <= u → first processed
  s.onEvent(depth(106, 110, 105, [], [['100.2', '0']]));
  const out = s.onSnapshot(snap(100, SNAP_BIDS, SNAP_ASKS));
  assert.deepEqual(out, { loaded: true });
  assert.equal(s.state, 'synced');
  assert.equal(s.lastU, 110);
  assert.equal(s.stats.dropped, 1);
  assert.equal(s.book.qty('bid', 1000), 1, 'stale event did not overwrite the snapshot');
  assert.equal(s.book.qty('bid', 999), 0, 'qty 0 removes the level');
  assert.equal(s.book.qty('ask', 1001), 7);
  assert.equal(s.book.qty('ask', 1002), 0);
  assert.deepEqual(s.book.top(2), { bids: [[100, 1], [99, 5]], asks: [[100.1, 7], [101, 4]] });
});

test('binance: first live event after a snapshot must straddle lastUpdateId', () => {
  const s = new ob.BinanceSync(SYM, 0.1);
  s.onSnapshot(snap(100, SNAP_BIDS, SNAP_ASKS));
  assert.equal(s.state, 'syncing');
  assert.equal(s.onEvent(depth(80, 99, 79)), null); // old: dropped while waiting
  assert.deepEqual(s.onEvent(depth(100, 104, 99, [['100.0', '2']])), { applied: true });
  assert.equal(s.state, 'synced');
  assert.equal(s.book.qty('bid', 1000), 2);
});

test('binance: snapshot older than the stream forces a fresh snapshot and keeps the buffered tail', () => {
  const s = new ob.BinanceSync(SYM, 0.1);
  s.onEvent(depth(120, 125, 119));
  s.onEvent(depth(126, 130, 125));
  const out = s.onSnapshot(snap(100, SNAP_BIDS, SNAP_ASKS)); // 101..119 missing
  assert.deepEqual(out, { resync: 'snapshot-behind' });
  assert.equal(s.state, 'resyncing');
  assert.equal(s.buffer.length, 2);
  assert.deepEqual(s.onSnapshot(snap(127, SNAP_BIDS, SNAP_ASKS)), { loaded: true });
  assert.equal(s.state, 'synced');
  assert.equal(s.lastU, 130);
});

test('binance: a pu gap triggers resync; the next snapshot restores sync', () => {
  const s = new ob.BinanceSync(SYM, 0.1);
  s.onSnapshot(snap(100, SNAP_BIDS, SNAP_ASKS));
  s.onEvent(depth(99, 101, 98));
  assert.deepEqual(s.onEvent(depth(105, 108, 104, [['100.0', '50']])), { resync: 'gap' });
  assert.equal(s.stats.gaps, 1);
  assert.deepEqual(s.lastGap, { expected: 101, pu: 104, u: 108 });
  assert.equal(s.state, 'resyncing');
  s.onEvent(depth(109, 112, 108)); // buffered while resyncing
  assert.deepEqual(s.onSnapshot(snap(110, SNAP_BIDS, SNAP_ASKS)), { loaded: true });
  assert.equal(s.state, 'synced');
  assert.equal(s.book.qty('bid', 1000), 1, 'the gapped event was not applied on top of the new snapshot');
  assert.deepEqual(s.onEvent(depth(113, 114, 112)), { applied: true });
});

test('binance: crossed book and touch leaving the snapshot window both force a resync', () => {
  const s = new ob.BinanceSync(SYM, 0.1);
  s.onSnapshot(snap(100, SNAP_BIDS, SNAP_ASKS));
  s.onEvent(depth(100, 101, 99));
  assert.deepEqual(s.onEvent(depth(102, 103, 101, [['100.5', '1']])), { resync: 'crossed' });
  assert.equal(s.stats.crossed, 1);
  const t = new ob.BinanceSync(SYM, 0.1);
  t.onSnapshot(snap(100, SNAP_BIDS, SNAP_ASKS));
  t.onEvent(depth(100, 101, 99));
  // every snapshot ask is removed and a new ask appears above the window's ceiling
  const out = t.onEvent(depth(102, 103, 101, [], [['100.1', '0'], ['100.2', '0'], ['101.0', '0'], ['101.5', '1']]));
  assert.deepEqual(out, { resync: 'left-snapshot-window' });
});

test('binance: coverage is the snapshot window; far diff levels are kept but flagged uncovered', () => {
  const s = new ob.BinanceSync(SYM, 0.1);
  s.onSnapshot(snap(100, SNAP_BIDS, SNAP_ASKS));
  s.onEvent(depth(100, 101, 99, [['50.0', '3']]));
  assert.equal(s.book.qty('bid', 500), 3);
  assert.equal(s.book.covered(500), false);
  assert.equal(s.book.covered(990), true);
  assert.equal(s.book.covered(1010), true);
  assert.equal(ob.rowLevel(s.book, 50, 10).covered, false);
});

test('binance: off-grid venue level is kept at its exact price, counted in its tick, removable by qty 0', () => {
  // Observed live 2026-10-17: Binance SOLUSDT (tickSize 0.0100) published a resting level at 105.0020.
  const s = new ob.BinanceSync(SYM, 0.1);
  s.onSnapshot(snap(100, [...SNAP_BIDS, ['99.85', '3']], SNAP_ASKS));
  assert.equal(s.book.qty('bid', 998), 3, '99.85 lives in the 99.8 tick');
  s.onEvent(depth(100, 101, 99));
  assert.deepEqual(s.onEvent(depth(102, 103, 101, [['99.95', '1'], ['99.9', '2']])), { applied: true });
  assert.equal(s.book.qty('bid', 999), 3, 'on-grid 2 + off-grid 1 in the 99.9 tick');
  assert.equal(s.book.offGridLevels, 2);
  assert.deepEqual(s.book.top(4).bids, [[100, 1], [99.95, 1], [99.9, 2], [99.85, 3]], 'top() keeps exact venue prices');
  s.onEvent(depth(104, 105, 103, [['99.95', '0']]));
  assert.equal(s.book.qty('bid', 999), 2, 'qty 0 removes exactly the off-grid size');
  assert.equal(s.book.offGridLevels, 1);
  const book = new ob.Book(0.1);
  book.load([['100.0', '1']], [['100.05', '1']]);
  assert.equal(book.crossed(), false, 'bid 100.0 < ask 100.05 although both sit in tick 1000');
  book.set('bid', '100.06', '1');
  assert.equal(book.crossed(), true);
});

const bybit = (type, u, b = [], a = [], extra = {}) => ({ topic: 'orderbook.200.BTCUSDT', type, ts: 1, cts: 1, data: { s: SYM, b, a, u, seq: u * 10 }, ...extra });

test('bybit: snapshot then deltas; size 0 deletes; deltas before a snapshot are ignored', () => {
  const s = new ob.BybitSync(SYM, 0.1);
  assert.equal(s.onMessage(bybit('delta', 5, [['100.0', '1']])), null);
  assert.deepEqual(s.onMessage(bybit('snapshot', 10, SNAP_BIDS, SNAP_ASKS)), { applied: true });
  s.onMessage(bybit('delta', 11, [['99.9', '0'], ['99.8', '4']], [['100.1', '2']]));
  assert.equal(s.book.qty('bid', 999), 0);
  assert.equal(s.book.qty('bid', 998), 4);
  assert.equal(s.book.qty('ask', 1001), 2);
  assert.equal(s.onMessage({ ...bybit('delta', 12), topic: 'orderbook.200.ETHUSDT' }), null, 'other topic ignored');
  assert.equal(s.lastU, 11);
});

test('bybit: u gap → resync; u=1 restart and new snapshot reset the book', () => {
  const s = new ob.BybitSync(SYM, 0.1);
  s.onMessage(bybit('snapshot', 10, SNAP_BIDS, SNAP_ASKS));
  assert.deepEqual(s.onMessage(bybit('delta', 13, [['100.0', '9']])), { resync: 'gap' });
  assert.equal(s.book.bids.size, 0, 'book is cleared, nothing stale shown');
  assert.equal(s.onMessage(bybit('delta', 14)), null);
  s.onMessage(bybit('snapshot', 20, SNAP_BIDS, SNAP_ASKS));
  assert.equal(s.state, 'synced');
  s.onMessage(bybit('delta', 1, [['98.0', '1']], [['102.0', '1']])); // service restart: overwrite
  assert.equal(s.stats.restarts, 1);
  assert.deepEqual([...s.book.bids.keys()], [980]);
  assert.deepEqual(s.onMessage(bybit('delta', 2, [['97.0', '1']])), { applied: true });
  assert.equal(s.book.bids.size, 2);
});

test('bybit: coverage follows the venue window and crossed deltas resync', () => {
  const s = new ob.BybitSync(SYM, 0.1);
  s.onMessage(bybit('snapshot', 10, SNAP_BIDS, SNAP_ASKS));
  assert.deepEqual(s.book.coverage, { bidFloor: 990, askCeil: 1010 });
  assert.deepEqual(s.onMessage(bybit('delta', 11, [['100.3', '1']])), { resync: 'crossed' });
});

test('hyperliquid: each l2Book message replaces the book; older snapshots are ignored; depth-capped', () => {
  const s = new ob.HyperliquidSync('BTC', 1);
  const lv = (px, sz) => ({ px, sz, n: 1 });
  const msg = (time, bids, asks) => ({ channel: 'l2Book', data: { coin: 'BTC', time, levels: [bids, asks] } });
  s.onMessage(msg(1000, [lv('100', '1'), lv('99', '2')], [lv('101', '3')]));
  s.onMessage(msg(2000, [lv('100', '5')], [lv('102', '1')]));
  assert.deepEqual([...s.book.bids], [[100, 5]]);
  assert.deepEqual([...s.book.asks], [[102, 1]]);
  assert.equal(s.onMessage(msg(1500, [lv('90', '1')], [lv('110', '1')])), null);
  assert.equal(s.stats.stale, 1);
  assert.equal(s.depthCapped, true);
  assert.deepEqual(s.onMessage(msg(3000, [lv('103', '1')], [lv('102', '1')])), { crossed: true });
  assert.equal(s.onMessage({ channel: 'l2Book', data: { coin: 'ETH', time: 4000, levels: [[], []] } }), null);
});

test('hyperliquid tick follows 5 significant figures and szDecimals', () => {
  assert.equal(ob.hyperliquidTick(81547, 5), 1);
  assert.equal(ob.hyperliquidTick(3456.7, 4), 0.1);
  assert.equal(ob.hyperliquidTick(150.12, 2), 0.01);
  assert.equal(ob.hyperliquidTick(0.012345, 0), 0.000001);
});

test('grouping: ×N ticks rows, % rows snapped to ticks, $ notional per row', () => {
  const book = new ob.Book(0.1);
  book.load([['100.0', '1'], ['99.9', '2'], ['99.5', '4']], [['100.1', '1'], ['100.4', '2']]);
  book.coverage = { bidFloor: 995, askCeil: 1004 };
  assert.equal(ob.rowTicks({ mode: 'ticks', ticks: 5 }, 100, 0.1), 5);
  assert.equal(ob.rowTicks({ mode: 'percent', percent: 0.1 }, 100, 0.1), 1);
  assert.equal(ob.rowTicks({ mode: 'percent', percent: 0.05 }, 81545, 0.1), 408);
  assert.equal(ob.rowTicks({ mode: 'percent', percent: 0.0001 }, 100, 0.1), 1, 'never below one tick');
  const row = ob.rowLevel(book, ob.rowOf(1000, 5), 5); // rows cover [1000,1005)
  assert.equal(row.bid, 1);
  assert.equal(row.ask, 3);
  assert.ok(Math.abs(row.askUsd - (100.1 + 2 * 100.4)) < 1e-9);
  const low = ob.rowLevel(book, ob.rowOf(999, 5), 5);
  assert.equal(low.bid, 6);
  assert.ok(Math.abs(low.bidUsd - (2 * 99.9 + 4 * 99.5)) < 1e-9);
  const side = ob.groupSide(book, 'bid', 5);
  assert.deepEqual([...side.keys()].sort(), [199, 200]);
  assert.equal(ob.notional(100, 2.5), 250);
});

test('density lifetime: first seen while ≥ threshold, reset on removal, threshold change, uncovered levels skipped', () => {
  const book = new ob.Book(1);
  book.load([['100', '100'], ['99', '1']], [['101', '50']]);
  book.coverage = { bidFloor: 90, askCeil: 110 };
  const d = new ob.DensityTracker();
  d.update(book, 5000, 1000);
  assert.equal(d.age('bid', 100, 4000), 3000);
  assert.equal(d.age('ask', 101, 4000), 3000);
  assert.equal(d.age('bid', 99, 4000), null);
  book.set('ask', '101', '10'); // shrinks below threshold
  d.update(book, 5000, 2000);
  assert.equal(d.age('ask', 101, 4000), null);
  book.set('ask', '101', '60'); // returns: age restarts
  d.update(book, 5000, 3000);
  assert.equal(d.age('ask', 101, 4000), 1000);
  assert.equal(d.age('bid', 100, 4000), 3000, 'unchanged level keeps its age');
  assert.equal(d.rowAge('bid', 10, 10, 4000), 3000);
  book.set('bid', '80', '1000'); // outside venue window: not tracked
  d.update(book, 5000, 3500);
  assert.equal(d.age('bid', 80, 4000), null);
  d.update(book, 6000, 3600);
  assert.equal(d.age('bid', 100, 4000), 400, 'new threshold restarts observation');
  assert.equal(ob.formatAge(65000), '1m05');
  assert.equal(ob.formatAge(3725000), '1h02');
});

test('prints: venue normalisation keeps taker side; aggregation merges same side within time and tick range', () => {
  assert.deepEqual(ob.normalizePrint('binance', { a: 7, p: '100.0', q: '2', T: 5, m: true }), { time: 5, price: 100, qty: 2, side: 'sell', id: 7, count: 1 });
  assert.equal(ob.normalizePrint('bybit', { T: 5, p: '100', v: '1', S: 'Buy', i: 'x' }).side, 'buy');
  assert.equal(ob.normalizePrint('hyperliquid', { time: 5, px: '100', sz: '1', side: 'A', tid: 1 }).side, 'sell');
  assert.equal(ob.normalizePrint('binance', { a: 1, p: '1', q: '0', T: 5, m: true }), null);
  const feed = new ob.PrintFeed({ tick: 0.1, aggregateMs: 100, aggregateTicks: 2 });
  const t = (time, price, qty, side) => feed.add({ time, price, qty, side, count: 1 });
  t(1000, 100, 1, 'buy'); t(1050, 100.2, 1, 'buy'); // merged
  t(1060, 100.5, 1, 'buy');                          // 5 ticks away → new
  t(1070, 100.5, 1, 'sell');                         // side flip → new
  t(1300, 100.5, 1, 'sell');                         // > 100 ms → new
  assert.equal(feed.prints.length, 4);
  assert.equal(feed.prints[0].count, 2);
  assert.ok(Math.abs(feed.prints[0].vwap - 100.1) < 1e-9);
  assert.deepEqual(feed.totals.count, 5);
  const w = feed.summary(0, 2000);
  assert.equal(w.trades, 5, 'no trade lost by aggregation');
  assert.ok(Math.abs(w.qty - 5) < 1e-12);
  const plain = new ob.PrintFeed({ tick: 0.1 });
  for (let i = 0; i < 10; i += 1) plain.add({ time: i, price: 100, qty: 1, side: 'buy' });
  assert.equal(plain.prints.length, 10, 'aggregation off keeps every print');
  const bounded = new ob.PrintFeed({ tick: 0.1, maxPrints: 3 });
  for (let i = 0; i < 5; i += 1) bounded.add({ time: i, price: 100, qty: 1, side: i % 2 ? 'buy' : 'sell' });
  assert.equal(bounded.prints.length, 3);
  assert.equal(bounded.totals.count, 5, 'totals count every received trade even when old prints are evicted');
  assert.equal(ob.printRadius(0, { minRadius: 2 }), 2);
  assert.equal(ob.printRadius(1e9, { minRadius: 2, maxRadius: 18, maxSizeUsd: 1e5 }), 18);
  assert.ok(ob.printRadius(25000, { minRadius: 2, maxRadius: 18, maxSizeUsd: 100000 }) === 10);
});

test('clusters: rows equal footprint.js aggregation on the shared parity fixture', () => {
  for (const c of fixture.cases) {
    if (!c.expected.levels) continue;
    const bar = fp.createBar(c.open_ms, c.interval_ms, c.tick_size);
    for (const t of c.trades) fp.addTrade(bar, { time: t.time, price: fp.dec(t.price), qty: fp.dec(t.qty), side: t.side, count: t.count ?? 1 });
    const rows = ob.clusterRows(bar, c.group_ticks);
    assert.deepEqual(rows.map(r => r.exact), c.expected.levels.map(l => ({ price: l.price, bid: l.bid, ask: l.ask })), c.name);
    assert.deepEqual(rows.map(r => r.trades), c.expected.levels.map(l => l.trades), c.name);
    const total = rows.reduce((a, r) => a + r.volume, 0);
    if (c.expected.total) assert.ok(Math.abs(total - Number(c.expected.total)) < 1e-9, c.name);
    const r0 = rows[0];
    assert.equal(ob.clusterValue(r0, 'delta', 'base'), r0.ask - r0.bid);
    assert.equal(ob.clusterValue(r0, 'volume', 'usd'), (r0.bid + r0.ask) * r0.price);
  }
});

test('abbreviation and decimals helpers', () => {
  assert.equal(ob.abbreviate(1234), '1.2K');
  assert.equal(ob.abbreviate(2500000), '2.5M');
  assert.equal(ob.abbreviate(123456789), '123M');
  assert.equal(ob.abbreviate(0.0123), '0.012');
  assert.equal(ob.abbreviate(-1500), '-1.5K');
  assert.equal(ob.decimalsOf(0.1), 1);
  assert.equal(ob.decimalsOf('0.0100'), 2);
  assert.equal(ob.decimalsOf(1e-7), 7);
});

// ── stream wiring with fake sockets/timers/fetch ─────────────────────────────
class Socket {
  static all = [];
  constructor(url) { this.url = url; this.sent = []; Socket.all.push(this); }
  send(data) { this.sent.push(JSON.parse(data)); }
  open() { this.onopen?.(); }
  msg(data) { this.onmessage?.({ data: JSON.stringify(data) }); }
  close() { this.closed = true; this.onclose?.(); }
}
function harness(extra = {}) {
  Socket.all = [];
  let time = 1_000_000, id = 0;
  const tasks = new Map(), statuses = [], fetches = [];
  const run = predicate => { for (const [k, t] of [...tasks]) if (predicate(t)) { tasks.delete(k); t.fn(); } };
  const opts = { WebSocket: Socket, now: () => time, random: () => 0.5,
    setTimeout: (fn, delay) => { tasks.set(++id, { fn, delay }); return id; }, clearTimeout: k => tasks.delete(k),
    onStatus: s => statuses.push(s.status), ...extra };
  return { opts, tasks, statuses, fetches, run, advance: ms => { time += ms; } };
}
const flush = () => new Promise(r => setImmediate(r));

test('stream: binance opens the /public depth stream, buffers, snapshots, syncs and resyncs on a gap', async () => {
  const snapshots = [snap(100, SNAP_BIDS, SNAP_ASKS), snap(200, SNAP_BIDS, SNAP_ASKS)];
  const h = harness({ fetch: async url => { h.fetches.push(url); return { ok: true, json: async () => snapshots.shift() }; } });
  const s = ob.createBookStream({ venue: 'binance', symbol: SYM, tick: 0.1, ...h.opts });
  const ws = Socket.all[0];
  assert.equal(ws.url, 'wss://fstream.binance.com/public/ws/btcusdt@depth@100ms');
  ws.open();
  ws.msg(depth(99, 101, 98));
  h.run(t => t.delay === 250); await flush(); await flush();
  assert.equal(h.fetches[0], 'https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=1000');
  assert.equal(s.status, 'synced');
  ws.msg(depth(150, 160, 140)); // gap
  assert.equal(s.status, 'resyncing');
  ws.msg(depth(161, 205, 160));
  h.run(t => t.delay === 1000 && t.fn.name === 'snapshot'); await flush(); await flush();
  assert.equal(h.fetches.length, 2);
  assert.equal(s.status, 'synced');
  assert.equal(s.sync.lastU, 205);
  s.close();
});

test('stream: bybit subscribes orderbook.200, reconnects on a gap and marks quiet books stale', () => {
  const h = harness();
  const s = ob.createBookStream({ venue: 'bybit', symbol: SYM, tick: 0.1, ...h.opts });
  Socket.all[0].open();
  assert.deepEqual(Socket.all[0].sent[0], { op: 'subscribe', args: ['orderbook.200.BTCUSDT'] });
  Socket.all[0].msg(bybit('snapshot', 10, SNAP_BIDS, SNAP_ASKS));
  assert.equal(s.status, 'synced');
  h.advance(6000); h.run(t => t.delay === 1000 && t.fn.name === 'watch');
  assert.equal(s.status, 'stale');
  Socket.all[0].msg(bybit('delta', 12));
  assert.equal(s.status, 'disconnected');
  h.run(t => t.delay >= 750 && t.delay <= 1000 && t.fn.name !== 'watch');
  assert.equal(Socket.all.length, 2, 'fresh subscription after the gap');
  s.close();
});

test('stream: hyperliquid subscribes l2Book for the coin', () => {
  const h = harness();
  const s = ob.createBookStream({ venue: 'hyperliquid', symbol: 'BTC', tick: 1, ...h.opts });
  Socket.all[0].open();
  assert.deepEqual(Socket.all[0].sent[0], { method: 'subscribe', subscription: { type: 'l2Book', coin: 'BTC' } });
  Socket.all[0].msg({ channel: 'l2Book', data: { coin: 'BTC', time: 1, levels: [[{ px: '100', sz: '1', n: 1 }], [{ px: '101', sz: '1', n: 1 }]] } });
  assert.equal(s.status, 'synced');
  assert.equal(s.book.best().bid, 100);
  s.close();
});
