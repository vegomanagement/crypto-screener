'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ob = require(path.join(__dirname, '../static/orderbook.js'));
const R = require(path.join(__dirname, '../static/scalp-render.js'));
const S = require(path.join(__dirname, '../static/scalp-settings.js'));
const { ColumnData, tickString } = require(path.join(__dirname, '../static/scalp-column.js'));

function fakeCtx() {
  const ops = [];
  const rec = name => (...args) => ops.push({ name, args, fillStyle: ctx.fillStyle, font: ctx.font });
  const ctx = { ops, fillStyle: '', strokeStyle: '', font: '', textAlign: '', textBaseline: '', lineWidth: 1 };
  for (const m of ['fillRect', 'strokeRect', 'fillText', 'beginPath', 'moveTo', 'lineTo', 'stroke', 'fill', 'arc', 'save', 'restore', 'rect', 'clip', 'clearRect']) ctx[m] = rec(m);
  return ctx;
}
function sampleBook() {
  const book = new ob.Book(0.1);
  book.load([['100.0', '2'], ['99.9', '1'], ['99.0', '50']], [['100.2', '3'], ['100.3', '1'], ['101.0', '40']]);
  const b = book.best(); book.coverage = { bidFloor: b.deepestBid, askCeil: b.deepestAsk };
  return book;
}
const texts = ctx => ctx.ops.filter(o => o.name === 'fillText').map(o => String(o.args[0]));

test('layout: clusters | feed | price | volume, volume width capped, clusters dropped when narrow', () => {
  const L = R.layout(1200, { clusterCount: 3 });
  assert.equal(L.clusters.count, 3);
  assert.equal(L.feed.x, L.clusters.w);
  assert.equal(L.price.x, L.feed.x + L.feed.w);
  assert.equal(L.volume.x + L.volume.w, 1200);
  assert.equal(L.volume.w, 240);
  const narrow = R.layout(300, { clusterCount: 3 });
  assert.ok(narrow.clusters.count < 3);
  assert.ok(narrow.volume.w >= 84 && narrow.feed.w >= 0);
});

test('geometry: rows, prices and y are consistent; autocenter only past the distance', () => {
  const view = { centerRow: 1000, rowPx: 16 };
  assert.equal(R.rowCenterY(view, 1000, 400), 200);
  assert.equal(R.rowCenterY(view, 1001, 400), 184, 'higher price is drawn higher');
  assert.equal(R.rowAtY(view, 184, 400), 1001);
  assert.ok(Math.abs(R.priceY(view, 100.05, 0.1, 400) - 200) < 1e-6, 'row centre price maps to row centre');
  assert.equal(R.autocenter(1000, 1003, 400, 16, 30), 1000, 'inside 30% of half height: no jump');
  assert.equal(R.autocenter(1000, 1004, 400, 16, 30), 1004);
  assert.equal(R.autocenter(NaN, 7, 400, 16, 30), 7);
  assert.equal(R.fullScale([1, -5, 3], 0), 5);
  assert.equal(R.fullScale([1, 5], 100), 100);
  assert.equal(R.bigColor(2e6, 5e5, 2e6), R.COLORS.big2);
  assert.equal(R.bigColor(6e5, 5e5, 2e6), R.COLORS.big1);
  assert.equal(R.bigColor(1e5, 5e5, 2e6), null);
});

test('ladder: best bid/ask rows, $ vs coin values, unknown rows outside the depth window get no size', () => {
  const book = sampleBook(), cfg = S.defaultColumn();
  const L = R.layout(600, { clusterCount: 0 }), view = { centerRow: 1001, rowPx: 16 };
  const density = new ob.DensityTracker(); density.update(book, 3000, 0);
  const ctx = fakeCtx();
  const stats = R.drawLadder(ctx, L, 800, view, { book, rowN: 1, decimals: 1, density, now: 65000, ruler: {} }, cfg);
  assert.equal(stats.bestBidRow, 1000); assert.equal(stats.bestAskRow, 1002);
  const t = texts(ctx);
  assert.ok(t.includes('100.0') && t.includes('100.2') && t.includes('100.1'));
  assert.ok(t.includes(ob.abbreviate(200)), '$ value of 2 @ 100.0');
  assert.ok(t.includes('1m05'), 'density age of the 50-coin bid (≥ $3000) since first seen');
  assert.ok(stats.unknown > 0, 'rows beyond 99.0 / 101.0 are unknown, not empty');
  const coin = fakeCtx();
  R.drawLadder(coin, L, 800, view, { book, rowN: 1, decimals: 1, now: 0, ruler: {} }, { ...cfg, units: 'coin', abbreviate: false });
  assert.ok(texts(coin).includes('2') && texts(coin).includes('50'));
  const none = fakeCtx();
  R.drawLadder(none, L, 800, view, { book: null, rowN: 1, decimals: 1, now: 0, ruler: {} }, cfg);
  assert.ok(texts(none).some(x => x.includes('not synced')), 'no book → explicit message, no levels');
});

test('ladder grouping ×10 sums levels into rows; ruler reports % and liquidity to the hovered row', () => {
  const book = sampleBook(), cfg = { ...S.defaultColumn(), abbreviate: false, units: 'coin' };
  const L = R.layout(600, { clusterCount: 0 }), ctx = fakeCtx();
  R.drawLadder(ctx, L, 800, { centerRow: 100, rowPx: 16 }, { book, rowN: 10, decimals: 1, now: 0, ruler: { anchorRow: 100, hoverRow: 101 } }, cfg);
  const t = texts(ctx);
  assert.ok(t.includes('2') && t.includes('4'), 'row 100.0–100.9 holds bid 2 and asks 3+1, drawn as two half rows');
  assert.ok(t.some(x => /^\+0\.995% · 1 rows · liq 44(\.0+)?$/.test(x)), `ruler label: ${t.filter(x => x.includes('%'))}`);
});

test('feed: visible prints honour span and hide-below filter; every visible print is drawn', () => {
  const feed = new ob.PrintFeed({ tick: 0.1 });
  [[1000, 100, 1, 'buy'], [5000, 100.1, 0.05, 'sell'], [9000, 100.2, 20, 'buy'], [70000, 100.1, 2, 'sell']].forEach(([time, price, qty, side]) => feed.add({ time, price, qty, side }));
  const f = { ...S.defaultColumn().feed, spanMs: 60000, minUsd: 10, labelMinUsd: 1000 };
  const v = R.visiblePrints(feed, 70000, f);
  assert.deepEqual(v.list.map(p => p.lastTime), [70000], 'only the last 60 s');
  const v2 = R.visiblePrints(feed, 61000, f);
  assert.deepEqual(v2.list.map(p => p.lastTime), [1000, 9000]);
  assert.equal(v2.hidden, 1, 'the $5 print is hidden by the filter');
  const ctx = fakeCtx(), L = R.layout(800, { clusterCount: 0 });
  const stats = R.drawFeed(ctx, L, 600, { centerRow: 1001, rowPx: 16 }, { feed, tick: 0.1, rowN: 1, now: 61000 }, { ...S.defaultColumn(), feed: f });
  assert.equal(stats.drawn, 2); assert.equal(ctx.ops.filter(o => o.name === 'arc').length, 2);
  assert.equal(stats.labels, 1, 'only the $2004 print is labelled');
});

test('clusters: drawn from footprint bars, newest rightmost, partial bars flagged with ~', () => {
  const data = new ColumnData({ venue: 'binance', symbol: 'BTCUSDT', tick: 0.1, clusters: { count: 2, intervalMs: 60000 }, now: () => 30000 });
  const row = (a, T, p, q, m) => ({ e: 'aggTrade', s: 'BTCUSDT', a, f: a, l: a, T, p, q, m });
  data.onEvent({ type: 'trades', rows: [row(1, 30000, '100.0', '1', false), row(2, 61000, '100.1', '2', true), row(3, 62000, '100.1', '1', false)] });
  assert.equal(data.bars.length, 2);
  assert.equal(data.bars[0].partial, true, 'first bar opened before the subscription');
  assert.equal(data.bars[1].partial, false);
  const ctx = fakeCtx(), L = R.layout(900, { clusterCount: 2 });
  const stats = R.drawClusters(ctx, L, 600, { centerRow: 1001, rowPx: 16 }, { rowN: 1, clusters: data.bars }, { ...S.defaultColumn(), clusters: { ...S.defaultColumn().clusters, showMinUsd: 0 } });
  assert.equal(stats.columns, 2);
  const t = texts(ctx);
  assert.ok(t.includes('00:00~') && t.includes('00:01'));
  assert.ok(t.includes(ob.abbreviate(300.3)), 'volume $ of 3 coins at 100.1');
});

test('column data: feed counts equal received trades, id gaps and disconnects mark bars partial', () => {
  let now = 1000;
  const data = new ColumnData({ venue: 'binance', symbol: 'BTCUSDT', tick: 0.1, feed: { aggregateMs: 100 }, now: () => now });
  const row = (a, T, q, m = false) => ({ e: 'aggTrade', s: 'BTCUSDT', a, f: a, l: a, T, p: '100.0', q, m });
  data.onEvent({ type: 'status', status: 'live' });
  data.onEvent({ type: 'trades', rows: [row(1, 1000, '1'), row(2, 1050, '1'), row(3, 1300, '2', true), { bad: true }] });
  const w = data.windowCounts(0, 2000);
  assert.equal(w.received, 3); assert.equal(w.feedTrades, 3); assert.equal(w.feedPrints, 2, 'two same-side prints merged within 100 ms');
  assert.equal(data.stats.rejected, 1);
  now = 120000;
  data.onEvent({ type: 'trades', rows: [row(5, 120000, '1')] });
  assert.equal(data.stats.idGaps, 1);
  assert.equal(data.bars.at(-1).partial, true, 'aggregate id 4 missing → bar partial');
  data.onEvent({ type: 'trades', rows: [row(6, 180000, '1')] });
  assert.equal(data.bars.at(-1).partial, false);
  data.onEvent({ type: 'status', status: 'disconnected' });
  assert.equal(data.bars.at(-1).partial, true);
  data.setFeedAggregation({ aggregateMs: 0 });
  assert.equal(data.feed.prints.length, 5, 'feed rebuilt from raw prints without aggregation');
  assert.equal(tickString(0.0000001), '0.0000001');
});

test('settings: versioned, sanitised, defaults on corrupt storage; hotkeys are view-only', () => {
  const store = new Map(), storage = { getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  assert.deepEqual(S.load(storage), S.defaults());
  store.set(S.STORAGE_KEY, '{oops'); assert.deepEqual(S.load(storage), S.defaults());
  const saved = S.defaults(); saved.columns = 9; saved.cols[0] = { ...saved.cols[0], symbol: 'btc usdt', rowPx: 99, grid: 7, feed: { minUsd: -5 } };
  S.save(storage, saved);
  const loaded = S.load(storage);
  assert.equal(loaded.columns, 4); assert.equal(loaded.cols[0].symbol, 'BTCUSDT'); assert.equal(loaded.cols[0].rowPx, 32);
  assert.equal(loaded.cols[0].grid, 10); assert.equal(loaded.cols[0].feed.minUsd, 0);
  assert.deepEqual(S.sanitize({ ...saved, version: 0 }), S.defaults(), 'unknown version → defaults');
  const col = S.defaultColumn();
  assert.equal(S.hotkey(col, '3').column.scaleIndex, 2);
  assert.equal(S.hotkey(col, 'p').column.scaleMode, 'percent');
  assert.equal(S.hotkey(col, 'u').column.units, 'coin');
  assert.equal(S.hotkey(col, '+').column.rowPx, 18);
  assert.equal(S.hotkey({ ...col, autocenter: false }, 'c').action, 'center');
  assert.equal(S.hotkey(col, 'b').action, null);
  assert.equal(S.scaleLabel({ ...col, scaleIndex: 2 }), '×5 ticks');
  assert.equal(S.chartLink(col), '/ui?exchange=binance&symbol=BTCUSDT');
  assert.ok(S.validSymbol('hyperliquid', 'BTC') && !S.validSymbol('binance', 'BTC') && !S.validSymbol('bybit', 'btcusdt'));
});

test('no order path: scalp scripts only read public endpoints and never send keys or signed requests', () => {
  const files = ['orderbook.js', 'scalp-render.js', 'scalp-settings.js', 'scalp-column.js', 'scalp-dom.js', 'scalp.js'];
  for (const f of files) {
    const src = fs.readFileSync(path.join(__dirname, '../static', f), 'utf8');
    assert.doesNotMatch(src, /X-MBX-APIKEY|X-BAPI|signature=|\/fapi\/v1\/order|\/v5\/order|"exchange"\s*,\s*action|apiKey|privateKey/i, f);
    assert.doesNotMatch(src, /vataga/i, f);
  }
});
