'use strict';
// Drawing tools — toolbar model, keyboard shortcuts and the pane controller driven through
// synthetic pointer/keyboard events (no browser): creation flows, undo/redo, delete, clone,
// level sync, per-interval visibility and a 200-object draw pass on a recording context.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { dr, chart, series, candles, ms, ORIGIN, memoryStorage } = require('./fixtures/drawings-fixture.cjs');
const TB = require(path.join(__dirname, '../static/drawings/toolbar-model.js'));
const { createController } = require(path.join(__dirname, '../static/drawings/controller.js'));
const levelsApi = require(path.join(__dirname, '../static/levels.js'));
const y = p => series.priceToCoordinate(p);

test('toolbar model: seven groups in order, last tool per group, favourites, magnet cycle, prefs migration', () => {
  let prefs = TB.readPrefs(memoryStorage());
  const m = TB.model(prefs);
  assert.deepEqual(m.map(g => g.id), ['lines', 'channels', 'fib', 'measure', 'shapes', 'annotation', 'patterns']);
  assert.equal(m[0].current, 'trend');
  assert.equal(m.find(g => g.id === 'lines').tools.find(t => t.type === 'hline').shortcut, 'Alt+H');
  prefs = TB.chooseTool(prefs, 'cross');
  assert.equal(TB.model(prefs)[0].current, 'cross', 'remembers the last tool of the group');
  assert.equal(TB.chooseTool(prefs, 'bogus'), prefs);
  prefs = TB.toggleFavourite(prefs, 'vbp');
  assert.ok(prefs.favourites.includes('vbp'));
  assert.ok(!TB.toggleFavourite(prefs, 'vbp').favourites.includes('vbp'));
  assert.deepEqual(['off', 'weak', 'strong'].map(TB.nextMagnet), ['weak', 'strong', 'off']);
  const storage = memoryStorage({ [TB.PREFS_V1]: JSON.stringify({ magnet: true, level: true, session: false }) });
  const migrated = TB.readPrefs(storage);
  assert.equal(migrated.magnet, 'strong'); assert.equal(migrated.level, true);
  TB.savePrefs(storage, { ...migrated, lastByGroup: { fib: 'fibchannel', lines: 'fib' } });
  assert.deepEqual(TB.readPrefs(storage).lastByGroup, { fib: 'fibchannel' }, 'tools from another group are rejected');
});

test('keyboard shortcuts map to actions (Alt letters via event.code for macOS)', () => {
  const k = (key, extra = {}) => TB.shortcut({ key, code: /^[a-z]$/i.test(key) ? `Key${key.toUpperCase()}` : key, ...extra });
  assert.deepEqual(k('t', { altKey: true }), { action: 'tool', tool: 'trend' });
  assert.deepEqual(TB.shortcut({ key: '˙', code: 'KeyH', altKey: true }), { action: 'tool', tool: 'hline' }, 'macOS Option+H');
  assert.deepEqual(['v', 'c', 'f', 'r'].map(c => k(c, { altKey: true }).tool), ['vline', 'cross', 'fib', 'rect']);
  assert.deepEqual(k('Escape'), { action: 'cancel' }); assert.deepEqual(k('Delete'), { action: 'delete' }); assert.deepEqual(k('Backspace'), { action: 'delete' });
  assert.deepEqual(k('z', { ctrlKey: true }), { action: 'undo' }); assert.deepEqual(k('z', { metaKey: true }), { action: 'undo' });
  assert.deepEqual(k('Z', { metaKey: true, shiftKey: true }), { action: 'redo' }); assert.deepEqual(k('Z', { shiftKey: true }), { action: 'redo' });
  assert.deepEqual(k('y', { ctrlKey: true }), { action: 'redo' }); assert.deepEqual(k('d', { metaKey: true }), { action: 'clone' });
  assert.equal(k('t'), null, 'plain letters are not shortcuts'); assert.equal(k('x', { altKey: true }), null);
});

// ── controller harness ──
function harness({ interval = '1', levels = null, storage = memoryStorage() } = {}) {
  const doc = new EventTarget();
  globalThis.document = doc;
  const stage = new EventTarget();
  stage.dataset = {}; stage.setPointerCapture = () => {};
  const host = { getBoundingClientRect: () => ({ left: 0, top: 0 }) };
  const pane = { classList: { contains: c => c === 'is-active' }, focus() {} };
  const ctx = { exchange: 'binance', symbol: 'BTCUSDT', interval, candles, chart, series, host, stepMs: 60000, precision: 2, plotWidth: 300, plotHeight: 800 };
  let draws = 0;
  const ctl = createController({ stage, paneElement: pane, storage, levels, getContext: () => ctx, requestDraw: () => { draws += 1; } });
  const fire = (target, type, props) => { const e = new Event(type, { cancelable: true }); Object.assign(e, props); target.dispatchEvent(e); return e; };
  const at = (bar, price) => ({ clientX: ORIGIN + bar * 10, clientY: y(price) });
  const click = (bar, price) => { fire(stage, 'pointerdown', { button: 0, buttons: 1, ...at(bar, price) }); fire(stage, 'pointerup', { button: 0, buttons: 0, ...at(bar, price) }); };
  const drag = (b0, p0, b1, p1) => { fire(stage, 'pointerdown', { button: 0, buttons: 1, ...at(b0, p0) }); fire(stage, 'pointermove', { buttons: 1, ...at((b0 + b1) / 2, (p0 + p1) / 2) }); fire(stage, 'pointermove', { buttons: 1, ...at(b1, p1) }); fire(stage, 'pointerup', { button: 0, buttons: 0, ...at(b1, p1) }); };
  const key = (k, mods = {}) => fire(doc, 'keydown', { key: k, code: /^[a-z]$/i.test(k) ? `Key${k.toUpperCase()}` : k, ...mods });
  return { ctl, ctx, stage, click, drag, key, fire, storage, draws: () => draws };
}

test('controller: click-click and press-drag creation, 3-point tools, multi-point path with Enter', () => {
  const h = harness();
  h.key('t', { altKey: true });
  assert.equal(h.ctl.state.tool, 'trend');
  h.click(2, 110); h.click(8, 120);
  assert.equal(h.ctl.state.items.length, 1);
  assert.deepEqual(h.ctl.state.items[0].points, [{ time: ms(2), price: '110' }, { time: ms(8), price: '120' }]);
  assert.equal(h.ctl.state.tool, 'cursor', 'back to cursor unless stay-in-drawing');
  h.ctl.setTool('rect'); h.drag(10, 150, 14, 140);
  assert.equal(h.ctl.state.items.at(-1).type, 'rect');
  h.ctl.setTool('channel'); h.click(2, 110); h.click(8, 120); assert.equal(h.ctl.state.items.length, 2, 'waits for the 3rd point');
  h.click(5, 130); assert.equal(h.ctl.state.items.at(-1).type, 'channel');
  h.ctl.setPrefs({ stayInDrawing: true });
  h.ctl.setTool('path'); h.click(1, 110); h.click(3, 115); h.click(5, 112); h.key('Enter');
  assert.equal(h.ctl.state.items.at(-1).type, 'path'); assert.equal(h.ctl.state.items.at(-1).points.length, 3);
  assert.equal(h.ctl.state.tool, 'path', 'stay in drawing mode');
  h.ctl.setTool('brush'); h.drag(1, 100, 9, 130);
  assert.equal(h.ctl.state.items.at(-1).type, 'brush');
  h.ctl.setTool('long'); h.click(4, 100); h.click(10, 110);
  assert.deepEqual(h.ctl.state.items.at(-1).points.map(p => p.price), ['100', '110', '95']);
  h.ctl.setTool('ruler'); h.drag(2, 100, 14, 103);
  assert.equal(h.ctl.state.items.at(-1).type, 'long', 'ruler is temporary — not stored');
  assert.ok(h.ctl.state.ruler);
});

test('controller: select, drag with history, Delete, Ctrl+Z / Shift+Z, clone, lock all, Esc', () => {
  const h = harness();
  h.ctl.setTool('trend'); h.click(2, 110); h.click(8, 120);
  const id = h.ctl.state.items[0].id;
  h.key('Escape'); assert.equal(h.ctl.state.selected, null);
  h.drag(5, 115, 8, 105); // body drag: +3 bars, -10
  assert.equal(h.ctl.state.selected, id);
  assert.deepEqual(h.ctl.state.items[0].points.map(p => p.price), ['100', '110']);
  h.key('z', { ctrlKey: true });
  assert.deepEqual(h.ctl.state.items[0].points.map(p => p.price), ['110', '120'], 'one undo step per drag');
  h.key('Z', { shiftKey: true });
  assert.deepEqual(h.ctl.state.items[0].points.map(p => p.price), ['100', '110'], 'redo');
  h.ctl.select(id); h.key('d', { metaKey: true });
  assert.equal(h.ctl.state.items.length, 2, 'clone');
  h.key('Delete'); assert.equal(h.ctl.state.items.length, 1);
  h.key('z', { metaKey: true }); assert.equal(h.ctl.state.items.length, 2, 'undo delete');
  h.ctl.actions.setLockedAll(true);
  const before = JSON.stringify(h.ctl.state.items);
  h.drag(8, 105, 12, 140);
  assert.equal(JSON.stringify(h.ctl.state.items), before, 'locked: no drag');
  const persisted = dr.createStore(h.storage).get('binance', 'BTCUSDT');
  assert.equal(persisted.items.length, 2); assert.equal(persisted.locked, true);
  h.key('h', { altKey: true }); assert.equal(h.ctl.state.tool, 'hline'); h.key('Escape'); assert.equal(h.ctl.state.tool, 'cursor');
});

test('controller: level mode links horizontal lines to the shared store; undo restores the level', () => {
  const storage = memoryStorage();
  const levels = levelsApi.createStore({ storage, eventTarget: null });
  const h = harness({ levels, storage });
  h.ctl.setPrefs({ level: true });
  h.ctl.setTool('hline'); h.click(5, 150);
  const d = h.ctl.state.items[0];
  assert.ok(d.levelId); assert.equal(levels.list({ exchange: 'binance', symbol: 'BTCUSDT' })[0].price, '150');
  h.key('Delete');
  assert.equal(levels.list({ exchange: 'binance', symbol: 'BTCUSDT' }).length, 0, 'deleting the line deletes the level');
  h.key('z', { ctrlKey: true });
  assert.equal(h.ctl.state.items.length, 1);
  const restored = levels.list({ exchange: 'binance', symbol: 'BTCUSDT' });
  assert.equal(restored.length, 1, 'undo re-creates the level'); assert.equal(h.ctl.state.items[0].levelId, restored[0].id);
});

function recordingContext() {
  const calls = { stroke: 0, fill: 0, text: 0 };
  const ctx = new Proxy({ calls, measureText: t => ({ width: t.length * 6 }) }, {
    get(target, prop) { if (prop in target) return target[prop]; if (prop === 'stroke') return () => { calls.stroke += 1; }; if (prop === 'fill') return () => { calls.fill += 1; }; if (prop === 'fillText') return () => { calls.text += 1; }; return () => {}; },
    set(target, prop, value) { target[prop] = value; return true; } });
  return ctx;
}

test('controller draw: per-interval visibility, hidden-all, and 200 objects in one pass', () => {
  const h = harness({ interval: '1' });
  const types = dr.toolList().filter(t => t.mode === 'click' && !t.temporary).map(t => t.type);
  for (let i = 0; i < 200; i += 1) {
    const def = dr.getTool(types[i % types.length]), n = def.storedPoints || def.points;
    const pts = Array.from({ length: n }, (_, k) => ({ time: ms((i + k * 3) % 19), price: String(105 + ((i * 7 + k * 11) % 30)) }));
    h.ctl.actions.addDrawing(def.type, def.finalize ? def.finalize(pts.slice(0, 2), h.ctl.view()) : pts);
  }
  assert.equal(h.ctl.state.items.length, 200);
  const c = recordingContext();
  h.ctl.draw(c, 300, 800);
  assert.equal(h.ctl.state.frame.prims.size, 200, 'every object built');
  assert.ok(c.calls.stroke > 200);
  const id = h.ctl.state.items[0].id;
  h.ctl.actions.updateItem(id, { visibility: { ...h.ctl.state.items[0].visibility, minutes: false } });
  h.ctl.draw(recordingContext(), 300, 800);
  assert.equal(h.ctl.state.frame.prims.size, 199, 'hidden on minute charts');
  h.ctx.interval = '60'; h.ctl.draw(recordingContext(), 300, 800);
  assert.equal(h.ctl.state.frame.prims.size, 200, 'visible on hour charts');
  h.ctl.actions.setHiddenAll(true); h.ctl.draw(recordingContext(), 300, 800);
  assert.equal(h.ctl.state.frame.prims.size, 0);
  assert.ok(h.ctl.state.perf.frames >= 4);
});
