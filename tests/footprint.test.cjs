'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const fp = require(path.join(__dirname, '../static/footprint.js'));
const stream = require(path.join(__dirname, '../static/market-stream.js'));
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/footprint_parity.json'), 'utf8'));

const MIN = 60000, T0 = 1700000040000;

test('shared fixture: JS aggregation equals the hand-computed (and Python) results', () => {
  assert.ok(fixture.cases.length >= 3);
  for (const c of fixture.cases) {
    const bar = fp.createBar(c.open_ms, c.interval_ms, c.tick_size);
    for (const t of c.trades) fp.addTrade(bar, { time: t.time, price: fp.dec(t.price), qty: fp.dec(t.qty), side: t.side, count: t.count ?? 1 });
    const got = fp.barPayload(bar, c.group_ticks, 'complete', 'fixture', c.settings || {});
    for (const [key, expected] of Object.entries(c.expected)) assert.deepEqual(got[key], expected, `${c.name}: ${key}`);
  }
});

test('exact decimal bucketing has no float drift and rejects non-decimal input', () => {
  const step = fp.dec('0.1');
  assert.equal(fp.dec('0.3') / step, 3n); // 0.3 / 0.1 in float floors to 2
  assert.equal(fp.fmt(fp.dec('81510.70')), '81510.7');
  assert.equal(fp.fmt(fp.dec('0.20') - fp.dec('0.30')), '-0.1');
  for (const bad of ['-1', '1e5', 'nan', '', 0.1, null, true]) assert.throws(() => fp.dec(bad));
  assert.equal(fp.fmt(fp.pct(-1n * fp.dec('1'), fp.dec('3'))), '-33.33');
});

test('venue side mapping: Binance m=true → bid, Bybit taker side, Hyperliquid B/A', () => {
  const b = fp.normalizeTrade('binance', { e: 'aggTrade', a: 7, p: '100.0', q: '0.5', f: 10, l: 12, T: 5, m: true });
  assert.deepEqual([b.side, b.count, b.id], ['bid', 3, 7]);
  assert.equal(fp.normalizeTrade('binance', { a: 8, p: '1', q: '1', f: 1, l: 1, T: 5, m: false }).side, 'ask');
  assert.equal(fp.normalizeTrade('bybit', { i: 'x', p: '1', v: '2', S: 'Buy', T: 9 }).side, 'ask');
  assert.equal(fp.normalizeTrade('bybit', { execId: 'y', price: '1', size: '2', side: 'Sell', time: '9' }).side, 'bid');
  assert.equal(fp.normalizeTrade('hyperliquid', { px: '1', sz: '1', side: 'B', time: 1, tid: 4 }).side, 'ask');
  assert.equal(fp.normalizeTrade('hyperliquid', { px: '1', sz: '1', side: 'A', time: 1, tid: 4 }).side, 'bid');
  assert.throws(() => fp.normalizeTrade('bybit', { i: 'x', p: '1', v: '2', S: 'None', T: 9 }));
});

function serverBar(open, levels, { coverage = 'complete', forming = false } = {}) {
  const bar = fp.createBar(open, MIN, '0.1');
  levels.forEach(([price, qty, side], k) => fp.addTrade(bar, { time: open + k, price: fp.dec(price), qty: fp.dec(qty), side, count: 1 }));
  return { ...fp.barPayload(bar, 1, coverage, 'fixture'), forming };
}
function snapshot(bars, extra = {}) {
  return { exchange: 'binance', symbol: 'BTCUSDT', interval: '1', tick_size: '0.1', group_ticks: 1, bars,
    as_of: T0 + MIN + 30000, last_trade_id: 100, recent_ids: [], dedupe_window_ms: 5000, coverage: { missing: 0, requested: bars.length }, ...extra };
}
const agg = (id, time, price = '100.0', qty = '1', m = false) => ({ e: 'aggTrade', s: 'BTCUSDT', a: id, p: price, q: qty, f: id, l: id, T: time, m });

test('Binance live merge: contiguous ids keep the forming bar complete; an id gap marks it partial', () => {
  const store = new fp.LiveFootprint({ venue: 'binance', interval: '1' });
  store.onTrades([agg(101, T0 + MIN + 31000)]); // arrives before the snapshot → buffered, replayed
  store.loadSnapshot(snapshot([serverBar(T0, [['100.0', '2', 'ask']]), serverBar(T0 + MIN, [['100.0', '1', 'bid']], { forming: true })]));
  store.onTrades([agg(100, T0 + MIN + 29000), agg(102, T0 + MIN + 32000, '100.1', '0.5', true)]); // 100 is a duplicate
  let forming = store.list().at(-1);
  assert.equal(forming.coverage, 'complete');
  assert.equal(fp.fmt(fp.analyze(fp.groupedLevels(forming.bar, 1), store.step).total), '2.5');
  store.onTrades([agg(105, T0 + MIN + 40000)]); // ids 103..104 never arrived
  forming = store.list().at(-1);
  assert.equal(forming.coverage, 'partial');
  assert.equal(store.summary().gaps, 1);
  store.onTrades([agg(106, T0 + 2 * MIN + 1000)]); // rollover after the gap point: provably complete
  const next = store.list().at(-1);
  assert.deepEqual([next.bar.openMs, next.coverage, next.forming, next.origin], [T0 + 2 * MIN, 'complete', true, 'live']);
  assert.equal(store.list().find(e => e.bar.openMs === T0 + MIN).forming, false);
});

test('refresh: server bars replace weaker coverage, newer live bars are rebuilt without double counting', () => {
  const store = new fp.LiveFootprint({ venue: 'binance', interval: '1' });
  store.loadSnapshot(snapshot([serverBar(T0 + MIN, [['100.0', '1', 'bid']], { forming: true })]));
  store.onTrades([agg(101, T0 + MIN + 50000), agg(102, T0 + 2 * MIN + 100, '100.0', '3')]);
  // Refresh as of a moment inside bar 2 that saw id 102 at REST time.
  store.loadSnapshot(snapshot([serverBar(T0 + MIN, [['100.0', '1', 'bid'], ['100.0', '1', 'ask']]),
    serverBar(T0 + 2 * MIN, [['100.0', '3', 'ask']], { forming: true })], { as_of: T0 + 2 * MIN + 200, last_trade_id: 102 }));
  store.onTrades([agg(103, T0 + 2 * MIN + 300, '100.0', '1')]);
  const bars = store.list();
  assert.deepEqual(bars.map(e => [e.bar.openMs, e.coverage, e.origin]), [[T0 + MIN, 'complete', 'server'], [T0 + 2 * MIN, 'complete', 'server']]);
  assert.equal(fp.fmt(fp.analyze(fp.groupedLevels(bars[1].bar, 1), store.step).total), '4');
});

test('Bybit/Hyperliquid live merge: dedupe by ids near as_of, continuity by connection time', () => {
  const bybit = (id, time, side = 'Buy', size = '1') => ({ i: id, s: 'BTCUSDT', p: '100.0', v: size, S: side, T: time });
  const store = new fp.LiveFootprint({ venue: 'bybit', interval: '1' });
  store.onStatus('connected', T0 + MIN + 1000); // stream up before the snapshot window
  const asOf = T0 + MIN + 30000;
  store.loadSnapshot(snapshot([serverBar(T0 + MIN, [['100.0', '1', 'bid']], { forming: true })], { exchange: 'bybit', as_of: asOf, last_trade_id: null, recent_ids: ['a'] }));
  store.onTrades([bybit('old', asOf - 6000), bybit('a', asOf - 100), bybit('b', asOf - 50), bybit('c', asOf + 10), bybit('c', asOf + 10)]);
  let forming = store.list().at(-1);
  assert.equal(forming.coverage, 'complete');
  assert.equal(fp.fmt(fp.analyze(fp.groupedLevels(forming.bar, 1), store.step).total), '3'); // 1 + b + c
  store.onStatus('disconnected', asOf + 1000);
  assert.equal(store.list().at(-1).coverage, 'partial', 'trades may be missed while disconnected');
  store.onStatus('connected', T0 + 2 * MIN + 5000);
  store.onTrades([bybit('d', T0 + 2 * MIN + 6000)]); // bar 2 opened before reconnect → partial
  store.onTrades([bybit('e', T0 + 3 * MIN + 1)]); // bar 3 opened after reconnect → complete
  assert.deepEqual(store.list().slice(-2).map(e => e.coverage), ['partial', 'complete']);
  const late = new fp.LiveFootprint({ venue: 'hyperliquid', interval: '1' });
  late.loadSnapshot(snapshot([serverBar(T0 + MIN, [['100.0', '1', 'ask']], { forming: true })], { exchange: 'hyperliquid', as_of: asOf, last_trade_id: null }));
  assert.equal(late.list().at(-1).coverage, 'partial', 'no stream before snapshot → gap after as_of');
});

test('level of detail: text cells only when wide and tall, grouping auto-raises, marks when dense', () => {
  assert.deepEqual(fp.chooseDetail({ barSpacing: 60, pxPerStep: 12 }), { group: 1, cell: 12, mode: 'cells' });
  const raised = fp.chooseDetail({ barSpacing: 60, pxPerStep: 0.5 });
  assert.equal(raised.group, 20); assert.equal(raised.mode, 'cells');
  assert.equal(fp.chooseDetail({ barSpacing: 12, pxPerStep: 12 }).mode, 'marks');
  assert.equal(fp.chooseDetail({ barSpacing: 2, pxPerStep: 12 }).mode, 'footer');
  assert.equal(fp.chooseDetail({ barSpacing: 60, pxPerStep: 2, manual: true }).mode, 'marks', 'manual grouping is never silently changed');
});

function mockContext() {
  const calls = [];
  const ctx = new Proxy({}, { get(target, key) {
    if (key in target) return target[key];
    return (...args) => { calls.push([key, ...args]); };
  }, set(target, key, value) { target[key] = value; return true; } });
  return { ctx, calls };
}
test('renderer draws cells, POC border, partial outline and footer inside the given view', () => {
  const store = new fp.LiveFootprint({ venue: 'binance', interval: '1' });
  const trades = [['100.0', '2', 'ask'], ['100.0', '1', 'bid'], ['100.1', '5', 'ask'], ['99.9', '1', 'bid']];
  store.loadSnapshot(snapshot([serverBar(T0, trades), serverBar(T0 + MIN, trades, { coverage: 'partial', forming: true })]));
  const { ctx, calls } = mockContext();
  const view = { barSpacing: 80, plotWidth: 400, plotHeight: 300, barCenter: s => 100 + (s * 1000 - T0) / MIN * 80, priceToY: p => 150 - (p - 100) * 200 };
  const ui = { cellType: 'bidask', coloration: 'delta', showPoc: true, showVA: true, showFooter: true };
  const result = fp.draw(ctx, store, view, ui);
  assert.equal(result.mode, 'cells'); assert.equal(result.drawn, 2); assert.equal(result.cells, 6);
  const texts = calls.filter(c => c[0] === 'fillText').map(c => c[1]);
  assert.ok(texts.includes('5.00') && texts.includes('partial'));
  assert.ok(calls.some(c => c[0] === 'strokeRect'), 'POC border');
  const mismatch = new fp.LiveFootprint({ venue: 'binance', interval: '1' });
  mismatch.loadSnapshot(snapshot([{ ...serverBar(T0, trades), kline_check: { status: 'mismatch', volume_diff: '0.172' } }, serverBar(T0 + MIN, trades, { forming: true })]));
  assert.equal(mismatch.summary().klineMismatch, 1);
  const marked = mockContext(); fp.draw(marked.ctx, mismatch, view, ui);
  assert.ok(marked.calls.some(c => c[0] === 'fillText' && c[1] === 'kline Δ'), 'kline mismatch is visibly marked');
  const zoomedOut = fp.draw(mockContext().ctx, store, { ...view, barSpacing: 4 }, ui);
  assert.equal(zoomedOut.mode, 'marks'); assert.equal(zoomedOut.cells, 0);
});

class Socket {
  static instances = [];
  constructor(url) { this.url = url; this.sent = []; this.readyState = 0; Socket.instances.push(this); }
  send(data) { this.sent.push(JSON.parse(data)); }
  open() { this.readyState = 1; this.onopen?.(); }
  message(data) { this.onmessage?.({ data: JSON.stringify(data) }); }
  close() { this.readyState = 3; this.onclose?.(); }
}
function hub() {
  Socket.instances = [];
  return stream.createHub({ WebSocket: Socket, now: () => 1000, random: () => 0.5, setTimeout: () => 1, clearTimeout: () => {} });
}
test('market stream: trade channel per venue keeps exact strings and does not touch kline sockets', () => {
  const h = hub(), events = [];
  const offKline = h.subscribe({ venue: 'binance', symbol: 'BTCUSDT', interval: '1' }, () => {});
  const off = h.subscribeTrades({ venue: 'binance', symbol: 'BTCUSDT' }, e => events.push(e));
  assert.equal(Socket.instances.length, 2);
  assert.equal(Socket.instances[0].url, 'wss://fstream.binance.com/market/ws/btcusdt@kline_1m');
  const ws = Socket.instances[1];
  assert.equal(ws.url, 'wss://fstream.binance.com/market/ws/btcusdt@aggTrade');
  ws.open();
  ws.message(agg(5, 1700000000000, '81510.70', '0.001', true));
  ws.message({ ...agg(6, 1700000000001), s: 'ETHUSDT' });
  const tradeEvents = events.filter(e => e.type === 'trades');
  assert.equal(tradeEvents.length, 1); assert.equal(tradeEvents[0].rows[0].p, '81510.70');
  assert.equal(events.find(e => e.type === 'status' && e.status === 'connected').connectedAt, 1000);
  off(); offKline();
  const b = hub(); b.subscribeTrades({ venue: 'bybit', symbol: 'BTCUSDT' }, () => {}); Socket.instances[0].open();
  assert.deepEqual(Socket.instances[0].sent[0], { op: 'subscribe', args: ['publicTrade.BTCUSDT'] });
  const hl = hub(), got = []; hl.subscribeTrades({ venue: 'hyperliquid', symbol: 'BTC' }, e => got.push(e)); Socket.instances[0].open();
  assert.deepEqual(Socket.instances[0].sent[0], { method: 'subscribe', subscription: { type: 'trades', coin: 'BTC' } });
  Socket.instances[0].message({ channel: 'trades', data: [{ coin: 'BTC', side: 'B', px: '1', sz: '2', time: 1, tid: 1 }, { coin: 'ETH', side: 'B', px: '1', sz: '2', time: 1, tid: 2 }] });
  assert.equal(got.filter(e => e.type === 'trades')[0].rows.length, 1);
});

test('pane controller: fetches /api/footprint, rejects foreign payloads, refreshes after rollover', async () => {
  const timers = [], urls = [];
  let listener = null;
  const payload = snapshot([serverBar(T0 + MIN, [['100.0', '1', 'bid']], { forming: true })]);
  const controller = fp.createController({
    fetch: async url => { urls.push(url); return { ok: true, status: 200, json: async () => payload }; },
    hub: { subscribeTrades: (config, fn) => { listener = fn; return () => { listener = null; }; } },
    setTimeout: (fn, delay) => { timers.push({ fn, delay }); return timers.length; }, clearTimeout: () => {}
  });
  controller.enable({ exchange: 'binance', symbol: 'BTCUSDT', interval: '1', bars: 60 });
  await new Promise(resolve => setImmediate(resolve));
  assert.match(urls[0], /^\/api\/footprint\?exchange=binance&symbol=BTCUSDT&interval=1&limit=60&group_ticks=auto$/);
  assert.equal(controller.status.state, 'ready');
  assert.match(fp.statusText(controller), /Binance aggTrades · 1 complete \/ 0 partial/);
  listener({ type: 'trades', rows: [agg(101, T0 + 2 * MIN + 5)] });
  assert.equal(timers.at(-1).delay, 3000, 'closed bar is re-fetched from the server shortly after rollover');
  const store = controller.store;
  controller.enable({ exchange: 'binance', symbol: 'BTCUSDT', interval: '1', bars: 60 }); // candle reload re-syncs
  assert.equal(controller.store, store, 'same market: live state is kept, no refetch');
  assert.equal(urls.length, 1);
  controller.enable({ exchange: 'binance', symbol: 'BTCUSDT', interval: '240' });
  assert.equal(controller.status.state, 'unsupported'); assert.equal(listener, null);
  const foreign = fp.createController({ fetch: async () => ({ ok: true, status: 200, json: async () => ({ ...payload, symbol: 'ETHUSDT' }) }),
    hub: null, setTimeout: () => 1, clearTimeout: () => {} });
  foreign.enable({ exchange: 'binance', symbol: 'BTCUSDT', interval: '1' });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(foreign.status.state, 'error'); assert.match(foreign.status.message, /another market/);
});

test('an id gap between snapshot and stream is healed by one early re-fetch, never papered over', async () => {
  const timers = [];
  let listener = null, call = 0;
  const first = snapshot([serverBar(T0 + MIN, [['100.0', '1', 'bid']], { forming: true })]);
  const second = snapshot([serverBar(T0 + MIN, [['100.0', '1', 'bid'], ['100.0', '1', 'ask'], ['100.0', '1', 'ask']], { forming: true })], { last_trade_id: 103 });
  const controller = fp.createController({
    fetch: async () => { call += 1; const body = call === 1 ? first : second; return { ok: true, status: 200, json: async () => body }; },
    hub: { subscribeTrades: (config, fn) => { listener = fn; return () => {}; } },
    setTimeout: (fn, delay) => { timers.push({ fn, delay }); return timers.length; }, clearTimeout: () => {}
  });
  controller.enable({ exchange: 'binance', symbol: 'BTCUSDT', interval: '1' });
  await new Promise(resolve => setImmediate(resolve));
  listener({ type: 'trades', rows: [agg(103, T0 + MIN + 31000)] }); // 101, 102 missed before the socket opened
  assert.equal(controller.store.list().at(-1).coverage, 'partial');
  assert.equal(timers.at(-1).delay, 2000);
  listener({ type: 'trades', rows: [agg(104, T0 + MIN + 31500)] });
  timers.at(-1).fn(); // REST snapshot now runs through id 103
  await new Promise(resolve => setImmediate(resolve));
  const forming = controller.store.list().at(-1);
  assert.equal(forming.coverage, 'complete', 'snapshot through id 103 + live 104 is contiguous again');
  assert.equal(fp.fmt(fp.analyze(fp.groupedLevels(forming.bar, 1), controller.store.step).total), '4');
  const before = timers.length;
  listener({ type: 'trades', rows: [agg(107, T0 + MIN + 33000)] }); // another gap in the same bar
  assert.equal(controller.store.list().at(-1).coverage, 'partial', 'still reported, never papered over');
  assert.equal(timers.length, before, 'at most one early re-fetch per bar');
});

test('Bybit/HL: late stream start schedules a re-fetch after the dedupe window, which proves continuity', async () => {
  const timers = [];
  let listener = null, clock = T0 + MIN + 30000;
  const body = () => snapshot([serverBar(T0, [['100.0', '1', 'bid']]), serverBar(T0 + MIN, [['100.0', '1', 'bid']], { forming: true })],
    { exchange: 'bybit', as_of: clock, last_trade_id: null });
  const controller = fp.createController({
    fetch: async () => { const b = body(); return { ok: true, status: 200, json: async () => b }; },
    hub: { subscribeTrades: (config, fn) => { listener = fn; return () => {}; } }, now: () => clock,
    setTimeout: (fn, delay) => { timers.push({ fn, delay }); return timers.length; }, clearTimeout: () => {}
  });
  controller.enable({ exchange: 'bybit', symbol: 'BTCUSDT', interval: '1' });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(controller.store.list().at(-1).coverage, 'partial', 'stream not yet up at snapshot time');
  clock += 500; listener({ type: 'status', status: 'connected', connectedAt: clock });
  assert.equal(timers.at(-1).delay, 6500);
  clock += 6500; timers.at(-1).fn();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(controller.store.list().at(-1).coverage, 'complete');
});
