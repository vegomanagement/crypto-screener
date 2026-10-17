'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const levels = require(path.join(__dirname, '../static/levels.js'));

function memoryStorage() {
  const map = new Map();
  return { getItem: key => (map.has(key) ? map.get(key) : null), setItem: (key, value) => map.set(key, String(value)), removeItem: key => map.delete(key), map };
}
function fakeTarget() {
  const handlers = new Set();
  return { addEventListener: (type, fn) => type === 'storage' && handlers.add(fn), removeEventListener: (type, fn) => handlers.delete(fn), fire: event => handlers.forEach(fn => fn(event)), get size() { return handlers.size; } };
}
const base = { exchange: 'binance', symbol: 'BTCUSDT' };

test('storage key, schema and defaults follow the shared contract', () => {
  const storage = memoryStorage();
  let t = 1000;
  const store = levels.createStore({ storage, now: () => t++, random: () => 0.5, eventTarget: null });
  const level = store.add({ ...base, price: '81510.70' });
  assert.equal(levels.KEY, 'screener.levels.v1');
  const saved = JSON.parse(storage.getItem('screener.levels.v1'));
  assert.equal(saved.version, 1);
  assert.deepEqual(Object.keys(saved.levels[0]).sort(), [...levels.FIELDS].sort());
  assert.equal(level.price, '81510.70', 'exact decimal string kept verbatim');
  assert.equal(level.kind, 'simple'); assert.equal(level.source, 'chart'); assert.equal(level.triggeredAt, null);
  assert.deepEqual(store.list(base).map(l => l.id), [level.id]);
  assert.deepEqual(store.list({ exchange: 'bybit', symbol: 'BTCUSDT' }), []);
});

test('validation rejects unknown fields and non-finite or non-positive prices', () => {
  const store = levels.createStore({ storage: memoryStorage(), eventTarget: null });
  assert.throws(() => store.add({ ...base, price: '1', extra: 1 }), /unknown field/);
  for (const price of ['0', '0.000', '-1', 'NaN', 'Infinity', '1e5', '', 12.5, null]) assert.throws(() => store.add({ ...base, price }), String(price));
  assert.throws(() => store.add({ ...base, price: '1', exchange: 'okx' }));
  assert.throws(() => store.add({ ...base, price: '1', width: 5 }));
  assert.throws(() => store.add({ ...base, price: '1', note: 'x'.repeat(81) }));
  const ok = store.add({ ...base, price: '0.0001', kind: 'signal' });
  assert.equal(ok.armed, true);
  assert.throws(() => store.update(ok.id, { bogus: true }), /unknown field/);
  assert.throws(() => store.update(ok.id, { price: '0' }));
  assert.equal(store.update(ok.id, { price: '0.0002' }).price, '0.0002');
  assert.equal(store.update('missing', { price: '1' }), null);
});

test('corrupt or foreign-version storage is ignored; invalid rows dropped', () => {
  const storage = memoryStorage();
  storage.setItem(levels.KEY, '{not json');
  const store = levels.createStore({ storage, eventTarget: null });
  assert.deepEqual(store.load(), { version: 1, levels: [] });
  storage.setItem(levels.KEY, JSON.stringify({ version: 2, levels: [] }));
  assert.deepEqual(store.load().levels, []);
  const good = { id: 'a', ...base, price: '1', kind: 'simple', source: 'dom', color: '#112233', width: 1, repeat: false, armed: false, createdAt: 1, triggeredAt: null, note: '' };
  storage.setItem(levels.KEY, JSON.stringify({ version: 1, levels: [good, { ...good, id: 'b', price: '-1' }, { ...good, id: 'c', hack: 1 }] }));
  assert.deepEqual(store.load().levels.map(l => l.id), ['a']);
});

test('cap at 500: oldest triggered dropped first, then oldest created', () => {
  const store = levels.createStore({ storage: memoryStorage(), eventTarget: null });
  for (let i = 0; i < 500; i += 1) store.add({ ...base, id: `x${i}`, price: String(i + 1), createdAt: i, triggeredAt: i === 400 ? 50 : i === 450 ? 10 : null });
  store.add({ ...base, id: 'new1', price: '9999', createdAt: 1000 });
  let ids = store.list(base).map(l => l.id);
  assert.equal(ids.length, 500);
  assert.ok(!ids.includes('x450') && ids.includes('x400'), 'oldest triggeredAt goes first');
  store.add({ ...base, id: 'new2', price: '9998', createdAt: 1001 });
  ids = store.list(base).map(l => l.id);
  assert.ok(!ids.includes('x400') && ids.includes('x0'));
  store.add({ ...base, id: 'new3', price: '9997', createdAt: 1002 });
  ids = store.list(base).map(l => l.id);
  assert.ok(!ids.includes('x0') && ids.includes('x1'), 'then the oldest untriggered by createdAt');
});

test('remove and clear by (exchange, symbol, kind?)', () => {
  const store = levels.createStore({ storage: memoryStorage(), eventTarget: null });
  const a = store.add({ ...base, price: '1' });
  store.add({ ...base, price: '2', kind: 'signal' });
  store.add({ exchange: 'bybit', symbol: 'BTCUSDT', price: '3' });
  assert.equal(store.remove(a.id), true); assert.equal(store.remove(a.id), false);
  store.add({ ...base, price: '4' });
  assert.equal(store.clear({ ...base, kind: 'signal' }), 1);
  assert.equal(store.clear(base), 1);
  assert.equal(store.list({}).length, 1);
  assert.throws(() => store.clear({}));
});

test('subscribe fires on local changes and on storage events from other tabs', () => {
  const target = fakeTarget();
  const store = levels.createStore({ storage: memoryStorage(), eventTarget: target });
  const seen = [];
  const off = store.subscribe(change => seen.push(change.type));
  const level = store.add({ ...base, price: '1' });
  store.update(level.id, { note: 'x' });
  store.remove(level.id);
  target.fire({ key: 'screener.levels.v1' });
  target.fire({ key: 'something.else' });
  assert.deepEqual(seen, ['add', 'update', 'remove', 'storage']);
  off();
  assert.equal(target.size, 0, 'storage listener released with the last subscriber');
  store.add({ ...base, price: '2' });
  assert.equal(seen.length, 4);
});

test('crossed(): inclusive, exact decimals, gaps/jumps, no floats', () => {
  const L = { price: '100.10' };
  assert.equal(levels.crossed(L, '100.05', '100.15'), true);
  assert.equal(levels.crossed(L, '100.15', '100.05'), true, 'downward');
  assert.equal(levels.crossed(L, '100.1', '100.2'), true, 'touch at prev counts (inclusive)');
  assert.equal(levels.crossed(L, '100.0', '100.100'), true, 'touch at price, different scale');
  assert.equal(levels.crossed(L, '99', '120'), true, 'jump over the level');
  assert.equal(levels.crossed(L, '100.11', '100.2'), false);
  assert.equal(levels.crossed(L, '100.09', '100.0'), false);
  // 0.1 + 0.2 style float traps: exact compare only.
  assert.equal(levels.crossed({ price: '0.3' }, '0.30000000000000004', '0.4'), false);
  assert.equal(levels.crossed({ price: '0.30000000000000004' }, '0.3', '0.3'), false);
  assert.equal(levels.crossed({ price: '81510.7' }, '81510.70', '81510.70'), true);
  assert.equal(levels.crossed(L, null, '100.1'), false, 'no previous trade → no cross');
  assert.equal(levels.crossed(L, 'abc', '100.1'), false);
  assert.equal(levels.compareDecimal('1.50', '1.5'), 0);
  assert.equal(levels.numberToDecimal(1e-7), '0.0000001');
});
