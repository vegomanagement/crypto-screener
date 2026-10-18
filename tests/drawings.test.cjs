'use strict';
// Drawing tools — core: mapper, magnet, schema v2, v1→v2 migration, store, history, templates,
// per-interval visibility, level sync. The chart mock rejects fractional logical indices.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const dr = require(path.join(__dirname, '../static/drawings.js'));
const levelsApi = require(path.join(__dirname, '../static/levels.js'));
const { mapper, series, candles, ms, ORIGIN, memoryStorage } = require('./fixtures/drawings-fixture.cjs');

test('mapper: bar times map to bar centres, extrapolates past both ends, inverse slot time', () => {
  assert.equal(mapper.x(ms(0)), ORIGIN);
  assert.equal(mapper.x(ms(19)), ORIGIN + 190);
  assert.equal(mapper.x(ms(19) + 5 * 60000), ORIGIN + 240, 'future slots step by the interval');
  assert.equal(mapper.x(ms(0) - 60000), ORIGIN - 10);
  assert.equal(mapper.x(ms(3) + 30000), ORIGIN + 35, 'mid-bar time (a 1m point on a 5m chart) interpolates');
  assert.equal(mapper.slotTime(ORIGIN + 31), ms(3));
  assert.equal(mapper.slotTime(ORIGIN + 230), ms(19) + 4 * 60000);
  assert.equal(mapper.barAt(ORIGIN + 72).time, candles[7].time);
  assert.equal(mapper.barAt(ORIGIN + 400), null);
  assert.deepEqual(mapper.indexRange(ms(3), ms(6)), [3, 6]);
  assert.deepEqual(mapper.indexRange(ms(6) + 1, ms(3) - 1), [3, 6], 'order-insensitive, inclusive of bar opens');
  assert.equal(mapper.indexRange(ms(30), ms(40)), null);
});

test('magnet: weak snaps only within 14 px, strong always, off = exact cursor price', () => {
  const x = ORIGIN + 51; // bar 5: O105 H108 L103 C106
  assert.deepEqual(dr.snap(mapper, x, series.priceToCoordinate(107.8), { magnet: 'strong' }), { time: ms(5), price: '108', snapped: 'high' });
  assert.equal(dr.snap(mapper, x, series.priceToCoordinate(103.2), { magnet: 'weak' }).snapped, 'low', '0.8 px away');
  assert.equal(dr.snap(mapper, x, series.priceToCoordinate(112), { magnet: 'weak', precision: 2 }).snapped, null, '16 px from the high: weak ignores');
  assert.equal(dr.snap(mapper, x, series.priceToCoordinate(112), { magnet: 'strong' }).snapped, 'high', 'strong snaps anyway');
  assert.equal(dr.snap(mapper, x, series.priceToCoordinate(112), { magnet: true }).snapped, 'high', 'v1 boolean = strong');
  assert.deepEqual(dr.snap(mapper, x, series.priceToCoordinate(107.8), { magnet: 'off', precision: 2 }), { time: ms(5), price: '107.8', snapped: null });
  assert.equal(dr.snap(mapper, ORIGIN + 300, 100, { magnet: 'strong', precision: 1 }).snapped, null, 'no bar in the future area');
});

test('price strings are plain exact decimals at chart precision', () => {
  assert.equal(dr.priceString(81510.70000001, 1), '81510.7');
  assert.equal(dr.priceString(0.0000123456, 8), '0.00001235');
  assert.equal(dr.priceString(100, 2), '100');
  assert.equal(dr.priceString(-1, 2), null);
  assert.equal(dr.priceString(0.0000001, 2), null);
  assert.equal(dr.exactValue(1e-7), '0.0000001');
});

test('schema v2: normalisation keeps known fields, rejects bad points/ids, clamps style', () => {
  const d = dr.makeDrawing('trend', [{ time: ms(1), price: '81510.70' }, { time: ms(3), price: '81400.1' }], { id: 'a', createdAt: 1, style: { color: '#ABCDEF', width: 3 } });
  assert.equal(d.style.color, '#abcdef'); assert.equal(d.style.width, 3); assert.equal(d.style.dash, 'solid');
  assert.deepEqual(d.visibility, { seconds: true, minutes: true, hours: true, days: true, weeks: true });
  assert.equal(dr.validDrawing(d), true);
  assert.equal(dr.normalizeDrawing({ ...d, points: [{ time: 1.5, price: '1' }, d.points[1]] }), null);
  assert.equal(dr.normalizeDrawing({ ...d, points: [{ time: 1, price: '-1' }, d.points[1]] }), null);
  assert.equal(dr.normalizeDrawing({ ...d, points: [d.points[0]] }), null, 'trend needs 2 points');
  assert.equal(dr.normalizeDrawing({ ...d, id: 'bad id!' }), null);
  assert.equal(dr.normalizeDrawing({ ...d, style: { width: 99, color: 'red' } }).style.width, 1, 'invalid style falls back');
  assert.equal(dr.normalizeDrawing({ ...d, extra: 1 }).extra, undefined, 'unknown keys dropped');
  assert.throws(() => dr.makeDrawing('trend', [{ time: 1, price: '1' }, { time: 2, price: '1' }], { level: true }), /invalid/, 'only horizontal tools can be levels');
  assert.throws(() => dr.makeDrawing('nope', []), /unknown tool/);
  const brush = dr.makeDrawing('brush', Array.from({ length: 50 }, (_, i) => ({ time: ms(i % 20), price: String(100 + i) })));
  assert.equal(brush.points.length, 50, 'free-hand keeps its polyline');
  assert.equal(dr.cleanOpts({ ok: 1, nan: NaN, nested: { a: 1 }, arr: [{ v: 1, on: true }] }).nested, undefined);
});

test('v1 → v2 migration is lossless (ids, exact prices, colours, widths, locks, levels, hidden)', () => {
  const v1 = { version: 1, symbols: {
    'binance:BTCUSDT': { hidden: true, items: [
      { id: 'l1', type: 'line', points: [{ time: ms(1), price: '81510.70' }, { time: ms(3), price: '81400.1' }], color: '#123456', width: 3, locked: true, level: false, levelId: null, createdAt: 11 },
      { id: 'r1', type: 'ray', points: [{ time: ms(2), price: '0.00001234' }, { time: ms(5), price: '0.00001240' }], color: '#4f8cff', width: 1, locked: false, level: false, levelId: null, createdAt: 12 },
      { id: 'h1', type: 'hray', points: [{ time: ms(4), price: '81510.7' }], color: '#f0b429', width: 2, locked: false, level: true, levelId: 'lv-1', createdAt: 13 },
      { id: 'h2', type: 'hline', points: [{ time: ms(0), price: '80000' }], color: '#4f8cff', width: 1, locked: false, level: false, levelId: null, createdAt: 14 },
      { id: 'b1', type: 'rect', points: [{ time: ms(6), price: '1' }, { time: ms(9), price: '2.5' }], color: '#ff0000', width: 4, locked: false, level: false, levelId: null, createdAt: 15 }] },
    'bybit:ETHUSDT': { hidden: false, items: [] } } };
  const storage = memoryStorage({ 'screener.drawings.v1': JSON.stringify(v1) });
  const store = dr.createStore(storage);
  const entry = store.get('binance', 'BTCUSDT');
  assert.equal(entry.hidden, true);
  assert.deepEqual(entry.items.map(d => [d.id, d.type]), [['l1', 'trend'], ['r1', 'ray'], ['h1', 'hray'], ['h2', 'hline'], ['b1', 'rect']]);
  v1.symbols['binance:BTCUSDT'].items.forEach((old, i) => {
    const d = entry.items[i];
    assert.deepEqual(d.points, old.points, 'exact price strings and times');
    assert.equal(d.style.color, old.color); assert.equal(d.style.width, old.width);
    assert.equal(d.locked, old.locked); assert.equal(d.level, old.level); assert.equal(d.levelId, old.levelId); assert.equal(d.createdAt, old.createdAt);
  });
  assert.equal(dr.optsOf(entry.items[1]).extendRight, true, 'v1 ray keeps extending right');
  assert.equal(storage.getItem('screener.drawings.v1'), JSON.stringify(v1), 'v1 kept as a backup');
  assert.equal(JSON.parse(storage.getItem(dr.KEY)).version, 2, 'v2 written once');
  store.set('binance', 'BTCUSDT', { hidden: false, items: [] });
  assert.deepEqual(dr.createStore(storage).get('binance', 'BTCUSDT').items, [], 'v2 wins afterwards (no re-migration)');
  assert.equal(dr.migrateV1('{oops'), null); assert.equal(dr.migrateV1({ version: 2 }), null);
});

test('store: per (venue, symbol), shared by all intervals; serialize round-trip', () => {
  const storage = memoryStorage();
  const store = dr.createStore(storage);
  const d = dr.makeDrawing('fib', [{ time: ms(1), price: '1' }, { time: ms(2), price: '2' }], { id: 'x', opts: { reverse: true } });
  store.set('binance', 'BTCUSDT', { hidden: false, locked: true, items: [d] });
  assert.deepEqual(Object.keys(JSON.parse(storage.getItem(dr.KEY)).symbols), ['binance:BTCUSDT']);
  assert.deepEqual(store.get('binance', 'BTCUSDT').items, [d]);
  assert.equal(store.get('binance', 'BTCUSDT').locked, true);
  assert.deepEqual(store.get('bybit', 'BTCUSDT').items, []);
  const state = JSON.parse(storage.getItem(dr.KEY));
  assert.deepEqual(dr.parseState(dr.serialize(state)), state);
  assert.deepEqual(dr.parseState('{oops').symbols, {});
  assert.deepEqual(dr.parseState({ version: 1, symbols: {} }).symbols, {}, 'foreign versions ignored by the v2 parser');
});

test('per-interval visibility groups', () => {
  assert.deepEqual(['1s', '5s', '1', '15', '59', '60', '240', 'D', '3D', 'W', 'M'].map(dr.intervalGroup),
    ['seconds', 'seconds', 'minutes', 'minutes', 'minutes', 'hours', 'hours', 'days', 'days', 'weeks', 'weeks']);
  const d = dr.makeDrawing('hline', [{ time: 0, price: '1' }], { visibility: { seconds: false, hours: false } });
  assert.equal(dr.visibleOn(d, '5s'), false); assert.equal(dr.visibleOn(d, '15'), true); assert.equal(dr.visibleOn(d, '60'), false); assert.equal(dr.visibleOn(d, 'D'), true);
  assert.equal(dr.visibleOn({ ...d, hidden: true }, '15'), false);
});

test('undo/redo history: snapshots, redo cleared by a new action, limit', () => {
  const h = dr.createHistory(3);
  let items = [];
  const apply = next => { h.record(items); items = next; };
  apply([1]); apply([1, 2]); apply([1, 2, 3]);
  assert.deepEqual(items = h.undo(items), [1, 2]);
  assert.deepEqual(items = h.undo(items), [1]);
  assert.deepEqual(items = h.redo(items), [1, 2]);
  apply([9]);
  assert.equal(h.canRedo, false, 'new action clears redo');
  apply([9, 9]); apply([9, 9, 9]); apply([0]);
  let steps = 0, prev;
  while ((prev = h.undo(items))) { items = prev; steps += 1; }
  assert.equal(steps, 3, 'limited to 3 steps');
  assert.deepEqual(items, [9]);
});

test('templates: save, list, apply, default for new drawings, remove', () => {
  const t = dr.createTemplates(memoryStorage());
  const d = dr.makeDrawing('fib', [{ time: ms(1), price: '1' }, { time: ms(2), price: '2' }], { style: { color: '#ff0000', width: 2 }, opts: { reverse: true } });
  t.save('fib', 'Red reverse', d);
  assert.deepEqual(t.list('fib'), ['Red reverse']);
  const plain = dr.makeDrawing('fib', d.points);
  const applied = t.apply(plain, 'Red reverse');
  assert.equal(applied.style.color, '#ff0000'); assert.equal(applied.opts.reverse, true); assert.deepEqual(applied.points, plain.points);
  assert.equal(t.defaultFor('fib'), null);
  t.setDefault('fib', 'Red reverse');
  assert.equal(t.defaultFor('fib').style.width, 2);
  t.remove('fib', 'Red reverse');
  assert.equal(t.defaultFor('fib'), null); assert.deepEqual(t.list('fib'), []);
  assert.throws(() => t.save('fib', '', d), /template name/);
});

test('level sync with the shared levels store (store is the truth for linked lines)', () => {
  const storage = memoryStorage();
  const levels = levelsApi.createStore({ storage, eventTarget: null });
  const d = dr.makeDrawing('hray', [{ time: ms(4), price: '81510.7' }], { id: 'h', level: true, style: { color: '#f0b429', width: 2 } });
  const level = levels.add(dr.levelFromDrawing(d, 'binance', 'BTCUSDT'));
  assert.equal(level.source, 'chart'); assert.equal(level.price, '81510.7');
  const linked = { ...d, levelId: level.id };
  levels.add({ exchange: 'binance', symbol: 'BTCUSDT', price: '82000', kind: 'signal', source: 'dom' });
  let rec = dr.reconcile([linked], levels.list({ exchange: 'binance', symbol: 'BTCUSDT' }));
  assert.equal(rec.changed, false);
  assert.equal(rec.virtual.length, 1); assert.equal(rec.virtual[0].style.dash, 'dashed', 'signal levels dashed');
  levels.update(level.id, { price: '81600.5', width: 3 });
  rec = dr.reconcile([linked], levels.list({ exchange: 'binance', symbol: 'BTCUSDT' }));
  assert.deepEqual(rec.items[0].points, [{ time: ms(4), price: '81600.5' }]);
  assert.equal(rec.items[0].style.width, 3);
  assert.equal(dr.validDrawing(rec.items[0]), true);
  levels.remove(level.id);
  assert.deepEqual(dr.reconcile(rec.items, levels.list({ exchange: 'binance', symbol: 'BTCUSDT' })).items, []);
});

test('reorder for z-order', () => {
  const items = ['a', 'b', 'c', 'd'].map(id => ({ id }));
  const ids = list => list.map(d => d.id).join('');
  assert.equal(ids(dr.reorder(items, 'b', 'up')), 'acbd');
  assert.equal(ids(dr.reorder(items, 'b', 'down')), 'bacd');
  assert.equal(ids(dr.reorder(items, 'b', 'top')), 'acdb');
  assert.equal(ids(dr.reorder(items, 'c', 'bottom')), 'cabd');
});
