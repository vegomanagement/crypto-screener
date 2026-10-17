'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const dr = require(path.join(__dirname, '../static/drawings.js'));
const levelsApi = require(path.join(__dirname, '../static/levels.js'));

// Mock chart: bar i centre at ORIGIN + i*SPACING; price y = (200 - price) * 4. The mock rejects
// fractional logical indices (the real library silently returns 0 for them).
const ORIGIN = 50, SPACING = 10, T0 = 1792195200; // seconds
const candles = Array.from({ length: 20 }, (_, i) => ({ time: T0 + i * 60, open: 100 + i, high: 103 + i, low: 98 + i, close: 101 + i }));
function chartMock() {
  return { timeScale: () => ({ options: () => ({ barSpacing: SPACING }), logicalToCoordinate: i => { assert.ok(Number.isInteger(i), `fractional logical ${i}`); return ORIGIN + i * SPACING; } }) };
}
const series = { priceToCoordinate: p => (200 - p) * 4, coordinateToPrice: y => 200 - y / 4 };
const mapper = dr.createMapper({ chart: chartMock(), series, candles, stepMs: 60000 });
const ms = i => (T0 + i * 60) * 1000;

test('mapper: bar times map to bar centres, extrapolates past both ends, inverse slot time', () => {
  assert.equal(mapper.x(ms(0)), ORIGIN);
  assert.equal(mapper.x(ms(19)), ORIGIN + 190);
  assert.equal(mapper.x(ms(19) + 5 * 60000), ORIGIN + 240, 'future slots step by the interval');
  assert.equal(mapper.x(ms(0) - 60000), ORIGIN - 10);
  assert.equal(mapper.x(ms(3) + 30000), ORIGIN + 35, 'mid-bar time (e.g. a 1m point on a 5m chart) interpolates');
  assert.equal(mapper.slotTime(ORIGIN + 31), ms(3));
  assert.equal(mapper.slotTime(ORIGIN + 230), ms(19) + 4 * 60000);
  assert.equal(mapper.barAt(ORIGIN + 72).time, T0 + 7 * 60);
  assert.equal(mapper.barAt(ORIGIN + 400), null);
});

test('magnet snaps to the nearest of O/H/L/C of the bar under the cursor; off = exact pixel price', () => {
  const bar = candles[5]; // O105 H108 L103 C106
  const x = ORIGIN + 51;
  const nearHigh = dr.snap(mapper, x, series.priceToCoordinate(107.8), { magnet: true });
  assert.deepEqual(nearHigh, { time: ms(5), price: '108', snapped: 'high' });
  assert.equal(dr.snap(mapper, x, series.priceToCoordinate(103.2), { magnet: true }).snapped, 'low');
  assert.equal(dr.snap(mapper, x, series.priceToCoordinate(105.4), { magnet: true }).price, String(bar.open));
  assert.deepEqual(dr.snap(mapper, x, series.priceToCoordinate(107.8), { magnet: false, precision: 2 }), { time: ms(5), price: '107.8', snapped: null });
  // Off the data, magnet has no bar: falls back to the cursor price at the slot time.
  assert.equal(dr.snap(mapper, ORIGIN + 300, 100, { magnet: true, precision: 1 }).snapped, null);
});

test('hit-testing: handles beat bodies, topmost first, tolerance, every tool geometry', () => {
  const y = p => series.priceToCoordinate(p);
  const line = dr.makeDrawing('line', [{ time: ms(2), price: '110' }, { time: ms(8), price: '120' }], { id: 'line' });
  const ray = dr.makeDrawing('ray', [{ time: ms(2), price: '150' }, { time: ms(4), price: '150' }], { id: 'ray' });
  const hline = dr.makeDrawing('hline', [{ time: ms(0), price: '130' }], { id: 'hl' });
  const hray = dr.makeDrawing('hray', [{ time: ms(10), price: '140' }], { id: 'hr' });
  const rect = dr.makeDrawing('rect', [{ time: ms(12), price: '170' }, { time: ms(16), price: '160' }], { id: 'rect' });
  const items = [line, ray, hline, hray, rect];
  const W = 300;
  assert.deepEqual(dr.hitTest(items, mapper, W, ORIGIN + 20, y(110)), { id: 'line', part: 'p0' });
  assert.deepEqual(dr.hitTest(items, mapper, W, ORIGIN + 80, y(120) + 2), { id: 'line', part: 'p1' });
  assert.deepEqual(dr.hitTest(items, mapper, W, ORIGIN + 50, y(115) + 3), { id: 'line', part: 'body' });
  assert.equal(dr.hitTest(items, mapper, W, ORIGIN + 50, y(115) + 12), null, 'outside 6px tolerance');
  assert.deepEqual(dr.hitTest(items, mapper, W, ORIGIN + 250, y(150)), { id: 'ray', part: 'body' }, 'ray extends beyond p1');
  assert.equal(dr.hitTest(items, mapper, W, ORIGIN + 5, y(150)), null, 'ray does not extend behind p0');
  assert.deepEqual(dr.hitTest(items, mapper, W, 3, y(130) - 4), { id: 'hl', part: 'body' });
  assert.equal(dr.hitTest(items, mapper, W, ORIGIN + 50, y(140)), null, 'horizontal ray starts at its point');
  assert.deepEqual(dr.hitTest(items, mapper, W, ORIGIN + 200, y(140)), { id: 'hr', part: 'body' });
  assert.deepEqual(dr.hitTest(items, mapper, W, ORIGIN + 140, y(165)), { id: 'rect', part: 'body' }, 'inside rectangle');
  const over = dr.makeDrawing('hline', [{ time: ms(0), price: '110' }], { id: 'over' });
  assert.equal(dr.hitTest([...items, over], mapper, W, ORIGIN + 120, y(110)).id, 'over', 'later drawing is on top');
});

test('serialization round-trip; unknown fields, bad points and foreign versions are dropped', () => {
  const a = dr.makeDrawing('rect', [{ time: ms(1), price: '81510.70' }, { time: ms(3), price: '81400.1' }], { id: 'a', createdAt: 1, color: '#123456', width: 3, locked: true });
  const b = dr.makeDrawing('hray', [{ time: ms(1), price: '0.00001234' }], { id: 'b', createdAt: 2, level: true, levelId: 'lv-1' });
  const state = { version: 1, symbols: { 'binance:BTCUSDT': { hidden: true, items: [a, b] } } };
  const text = dr.serialize(state);
  assert.deepEqual(dr.parseState(text), state, 'exact price strings and every field survive');
  const bad = JSON.parse(text);
  bad.symbols['binance:BTCUSDT'].items.push({ ...a, id: 'c', extra: 1 }, { ...a, id: 'd', points: [{ time: 1.5, price: '1' }, a.points[1]] },
    { ...a, id: 'e', points: [{ time: 1, price: '-1' }, a.points[1]] }, { ...a, id: 'a' }, { ...a, id: 'f', type: 'hline' });
  bad.symbols['kraken:BTC'] = { items: [a] };
  assert.deepEqual(dr.parseState(bad).symbols['binance:BTCUSDT'].items.map(d => d.id), ['a', 'b']);
  assert.equal(dr.parseState(bad).symbols['kraken:BTC'], undefined);
  assert.deepEqual(dr.parseState('{oops').symbols, {});
  assert.deepEqual(dr.parseState({ version: 2, symbols: {} }).symbols, {});
  assert.throws(() => dr.makeDrawing('line', [{ time: 1, price: '1' }], {}), /invalid/);
  assert.throws(() => dr.makeDrawing('line', [{ time: 1, price: '1' }, { time: 2, price: '1' }], { level: true, levelId: 'x' }), /invalid/, 'only horizontal tools can be levels');
});

test('store persists per (venue, symbol) under screener.drawings.v1 — shared by all intervals', () => {
  const map = new Map();
  const storage = { getItem: k => map.get(k) ?? null, setItem: (k, v) => map.set(k, v) };
  const store = dr.createStore(storage);
  const d = dr.makeDrawing('line', [{ time: ms(1), price: '1' }, { time: ms(2), price: '2' }], { id: 'x' });
  store.set('binance', 'BTCUSDT', { hidden: false, items: [d] });
  assert.deepEqual(Object.keys(JSON.parse(map.get('screener.drawings.v1')).symbols), ['binance:BTCUSDT']);
  assert.deepEqual(store.get('binance', 'BTCUSDT').items, [d]);
  assert.deepEqual(store.get('bybit', 'BTCUSDT').items, []);
  store.set('binance', 'BTCUSDT', { hidden: false, items: [] });
  assert.deepEqual(JSON.parse(map.get('screener.drawings.v1')).symbols, {});
});

test('price strings are plain exact decimals at chart precision', () => {
  assert.equal(dr.priceString(81510.70000001, 1), '81510.7');
  assert.equal(dr.priceString(0.0000123456, 8), '0.00001235');
  assert.equal(dr.priceString(100, 2), '100');
  assert.equal(dr.priceString(-1, 2), null);
  assert.equal(dr.priceString(0.0000001, 2), null, 'rounds to zero → rejected');
  assert.equal(dr.exactValue(1e-7), '0.0000001');
});

test('move: body drag shifts by whole bars and price delta; hline keeps its time', () => {
  const d = dr.makeDrawing('line', [{ time: ms(2), price: '110' }, { time: ms(8), price: '120' }], { id: 'm' });
  const pts = dr.movePoints(d, mapper, { x: 100, y: 200 }, { x: 131, y: 180 }, 2);
  assert.deepEqual(pts, [{ time: ms(5), price: '115' }, { time: ms(11), price: '125' }]);
  const beyond = dr.movePoints(d, mapper, { x: 100, y: 200 }, { x: 250, y: 200 }, 2);
  assert.equal(beyond[1].time, ms(19) + 4 * 60000, 'moves into future slots by interval steps');
});

test('ruler measure: Δprice, Δ%, bars and duration', () => {
  const m = dr.measure({ time: ms(2), price: '100' }, { time: ms(14), price: '103' }, mapper);
  assert.equal(m.deltaPrice, 3); assert.equal(m.deltaPct, 3); assert.equal(m.bars, 12); assert.equal(m.deltaMs, 12 * 60000);
  assert.equal(dr.formatDuration(12 * 60000), '12m');
  assert.equal(dr.formatDuration(5000), '5s');
  assert.equal(dr.formatDuration(90061000), '1d 1h');
  assert.equal(dr.formatDuration(-3725000), '-1h 2m');
});

test('level sync mapping with the shared levels store (source chart, store is truth)', () => {
  const map = new Map();
  const storage = { getItem: k => map.get(k) ?? null, setItem: (k, v) => map.set(k, v) };
  const levels = levelsApi.createStore({ storage, eventTarget: null });
  const d = dr.makeDrawing('hray', [{ time: ms(4), price: '81510.7' }], { id: 'h', level: true, color: '#f0b429', width: 2 });
  const level = levels.add(dr.levelFromDrawing(d, 'binance', 'BTCUSDT'));
  assert.equal(level.source, 'chart'); assert.equal(level.kind, 'simple'); assert.equal(level.price, '81510.7');
  assert.equal(JSON.parse(map.get('screener.levels.v1')).levels.length, 1);
  const linked = { ...d, levelId: level.id };
  levels.add({ exchange: 'binance', symbol: 'BTCUSDT', price: '82000', kind: 'signal', source: 'dom' });
  let rec = dr.reconcile([linked], levels.list({ exchange: 'binance', symbol: 'BTCUSDT' }));
  assert.equal(rec.changed, false);
  assert.equal(rec.virtual.length, 1, 'DOM level drawn as a virtual line');
  assert.equal(rec.virtual[0].dashed, true, 'signal levels dashed');
  assert.equal(rec.virtual[0].points[0].price, '82000');
  levels.update(level.id, { price: '81600.5', width: 3 }); // moved on the DOM
  rec = dr.reconcile([linked], levels.list({ exchange: 'binance', symbol: 'BTCUSDT' }));
  assert.equal(rec.changed, true);
  assert.deepEqual(rec.items[0].points, [{ time: ms(4), price: '81600.5' }]);
  assert.equal(rec.items[0].width, 3);
  assert.equal(dr.validDrawing(rec.items[0]), true, 'reconciled drawing is still storable');
  levels.remove(level.id); // deleted on the DOM
  rec = dr.reconcile(rec.items, levels.list({ exchange: 'binance', symbol: 'BTCUSDT' }));
  assert.deepEqual(rec.items, [], 'linked drawing disappears with its level');
});
