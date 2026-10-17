'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const L = require(path.join(__dirname, '../static/levels.js'));

function fakeTarget() {
  const handlers = new Set();
  return { addEventListener: (t, fn) => { if (t === 'storage') handlers.add(fn); }, removeEventListener: (t, fn) => handlers.delete(fn),
    fire: event => handlers.forEach(fn => fn(event)), get count() { return handlers.size; } };
}
function makeStore(extra = {}) {
  let t = 1000, n = 0;
  const storage = L.memoryStorage(), target = fakeTarget();
  const store = L.createStore({ storage, target, now: () => t, makeId: () => `id${++n}`, ...extra });
  return { store, storage, target, tick: (ms = 1) => { t += ms; } };
}
const base = { exchange: 'binance', symbol: 'BTCUSDT', price: '67890.1', kind: 'signal', source: 'dom' };

test('exact decimals: BigInt scaling, no float artefacts', () => {
  assert.equal(L.compareDecimal('0.3', '0.30'), 0);
  assert.equal(L.compareDecimal('0.1', '0.10000000000000001'), -1, 'a float compare would call these equal');
  assert.equal(L.compareDecimal('67890.10', '67890.1'), 0);
  assert.equal(L.compareDecimal('100', '99.99999999'), 1);
  assert.equal(L.compareDecimal('abc', '1'), null);
  assert.equal(L.numberToDecimal(1e-7), '0.0000001');
  assert.equal(L.numberToDecimal(1.5e21), '1500000000000000000000');
  assert.equal(L.numberToDecimal(0.30000000000000004), '0.30000000000000004');
  assert.equal(L.fromTicks(678901, '0.10'), '67890.10');
  assert.equal(L.fromTicks(3, '0.1'), '0.3');
  assert.equal(L.fromTicks(12345, '0.000001'), '0.012345');
  assert.equal(L.distanceAtLeast('100.1', '100.0', '0.1'), true);
  assert.equal(L.distanceAtLeast('100.05', '100.0', '0.1'), false);
  assert.equal(L.distanceAtLeast('99.9', '100', '0.1'), true);
});

test('crossed(): inclusive, works on gaps/jumps in both directions, exact touch without prev', () => {
  const lv = { price: '100.5' };
  assert.equal(L.crossed(lv, '100.4', '100.5'), true, 'touch from below');
  assert.equal(L.crossed(lv, '100.6', '100.5'), true, 'touch from above');
  assert.equal(L.crossed(lv, '100.5', '100.6'), true, 'leaving from the level is inclusive');
  assert.equal(L.crossed(lv, '99.0', '102.0'), true, 'jump over the level up');
  assert.equal(L.crossed(lv, '102.0', '99.0'), true, 'jump over the level down');
  assert.equal(L.crossed(lv, '100.4', '100.49999999'), false, 'just short of the level');
  assert.equal(L.crossed(lv, '100.6', '100.7'), false);
  assert.equal(L.crossed(lv, null, '100.50'), true, 'no prev: exact touch counts');
  assert.equal(L.crossed(lv, null, '100.4'), false);
  assert.equal(L.crossed(lv, 100.4, 100.6), true, 'numbers are converted to exact decimals');
  assert.equal(L.crossed({ price: '0.1' }, '0.09999999999999999', '0.1'), true);
  assert.equal(L.crossed({ price: '0.1' }, '0.09', '0.09999999999999999'), false, 'float rounding would wrongly report a touch');
  assert.equal(L.crossed({ price: 'x' }, '1', '2'), false);
});

test('validation: defaults, unknown fields and bad prices are rejected', () => {
  const lv = L.normalize({ ...base, id: 'a' }, () => 5);
  assert.deepEqual(lv, { id: 'a', exchange: 'binance', symbol: 'BTCUSDT', price: '67890.1', kind: 'signal', source: 'dom', color: '#f5a623',
    width: 1, repeat: false, armed: true, createdAt: 5, triggeredAt: null, note: '' });
  assert.equal(L.normalize({ ...base, id: 'b', kind: 'simple' }).armed, false);
  for (const bad of [{ extra: 1 }, { price: 67890.1 }, { price: '0' }, { price: '0.000' }, { price: '-1' }, { price: 'NaN' }, { price: 'Infinity' },
    { price: '1e5' }, { price: '' }, { exchange: 'kraken' }, { kind: 'stop' }, { source: 'api' }, { color: 'red' }, { width: 5 }, { width: 1.5 },
    { repeat: 'yes' }, { note: 'x'.repeat(81) }, { triggeredAt: NaN }]) {
    assert.throws(() => L.normalize({ ...base, id: 'c', ...bad }), /levels:/, JSON.stringify(bad));
  }
  assert.equal(L.normalize({ ...base, id: 'd', note: 'x'.repeat(80), color: '#AABBCC' }).color, '#aabbcc');
});

test('store: add/list/update/remove/clear round-trip through storage JSON', () => {
  const { store, storage } = makeStore();
  const a = store.add(base), b = store.add({ ...base, price: '67900', kind: 'simple' }), c = store.add({ ...base, symbol: 'ETHUSDT', price: '3000' });
  assert.equal(a.id, 'id1');
  const raw = JSON.parse(storage.getItem(L.KEY));
  assert.equal(raw.version, 1); assert.equal(raw.levels.length, 3);
  assert.deepEqual(store.list({ exchange: 'binance', symbol: 'BTCUSDT' }).map(l => l.id), [a.id, b.id]);
  assert.deepEqual(store.list({ exchange: 'binance', symbol: 'BTCUSDT', kind: 'signal' }).map(l => l.id), [a.id]);
  const up = store.update(a.id, { armed: false, triggeredAt: 2000, color: L.TRIGGERED_COLOR });
  assert.equal(up.armed, false); assert.equal(store.list({ symbol: 'BTCUSDT' })[0].triggeredAt, 2000);
  assert.throws(() => store.update(a.id, { price: '-5' }), /positive decimal/);
  assert.throws(() => store.update(a.id, { bogus: 1 }), /unknown field/);
  assert.throws(() => store.update(a.id, { id: 'other' }), /id cannot change/);
  assert.equal(store.update('missing', { note: 'x' }), null);
  assert.throws(() => store.add({ ...base, id: a.id }), /duplicate/);
  assert.equal(store.remove(b.id), true); assert.equal(store.remove(b.id), false);
  assert.equal(store.clear({ exchange: 'binance', symbol: 'BTCUSDT', kind: 'simple' }), 0);
  assert.equal(store.clear({ exchange: 'binance', symbol: 'BTCUSDT' }), 1);
  assert.deepEqual(store.list().map(l => l.id), [c.id]);
  assert.throws(() => store.clear({}), /exchange and symbol/);
});

test('store: corrupt, foreign-version and invalid entries are dropped on load, never thrown', () => {
  const { store, storage } = makeStore();
  storage.setItem(L.KEY, '{not json');
  assert.deepEqual(store.load(), { version: 1, levels: [] });
  storage.setItem(L.KEY, JSON.stringify({ version: 2, levels: [{ ...base, id: 'x' }] }));
  assert.equal(store.load().levels.length, 0, 'unknown schema version is not guessed at');
  const good = L.normalize({ ...base, id: 'g' });
  storage.setItem(L.KEY, JSON.stringify({ version: 1, levels: [good, { ...good }, { ...good, id: 'h', price: '-1' }, { ...good, id: 'i', hack: true }, null] }));
  assert.deepEqual(store.load().levels.map(l => l.id), ['g']);
});

test('store: subscribe fires on local changes and on storage events from other tabs; unsubscribe detaches', () => {
  const { store, storage, target } = makeStore();
  const seen = [];
  const off = store.subscribe((levels, info) => seen.push([levels.length, info.origin, info.op ?? null]));
  assert.equal(target.count, 1);
  store.add(base);
  // another tab writes the same key: the browser fires 'storage' in this tab only
  const other = L.createStore({ storage, target: null, makeId: () => 'tab2' });
  other.add({ ...base, price: '1' });
  assert.equal(seen.length, 1, 'a write through another store instance is not a local change here');
  target.fire({ key: L.KEY }); target.fire({ key: 'unrelated' }); target.fire({ key: null });
  assert.deepEqual(seen, [[1, 'local', 'add'], [2, 'storage', null], [2, 'storage', null]]);
  off(); assert.equal(target.count, 0);
  store.add({ ...base, price: '2' }); assert.equal(seen.length, 3);
});

test('store: cap at 500 drops the oldest triggered first, then the oldest created', () => {
  const { store, tick } = makeStore();
  for (let k = 0; k < 500; k += 1) { store.add({ ...base, price: String(1000 + k) }); tick(); }
  store.update('id10', { triggeredAt: 50, armed: false });
  store.update('id5', { triggeredAt: 60, armed: false });
  store.add({ ...base, price: '9999' });
  let ids = store.list().map(l => l.id);
  assert.equal(ids.length, 500); assert.ok(!ids.includes('id10') && ids.includes('id5') && ids.includes('id501'));
  store.add({ ...base, price: '9998' });
  ids = store.list().map(l => l.id);
  assert.ok(!ids.includes('id5'), 'next oldest triggered goes');
  store.add({ ...base, price: '9997' });
  ids = store.list().map(l => l.id);
  assert.ok(!ids.includes('id1') && ids.includes('id2'), 'then the oldest created');
  assert.equal(ids.length, 500);
  assert.equal(L.capLevels([{ id: 'a', triggeredAt: null, createdAt: 1 }], 1).length, 1);
});

test('store: storage failures fall back to memory and say so', () => {
  const broken = { getItem: () => null, setItem: () => { throw new Error('QuotaExceeded'); } };
  const store = L.createStore({ storage: broken, target: null, makeId: () => 'm1' });
  assert.equal(store.lastError, null);
  store.add(base);
  assert.match(store.lastError, /write failed/);
  assert.equal(store.list().length, 1, 'still usable for this page');
  const none = L.createStore({ storage: null, target: null });
  assert.match(none.lastError, /unavailable/);
});
