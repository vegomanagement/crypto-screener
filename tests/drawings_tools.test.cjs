'use strict';
// Drawing tools — per-tool geometry & hit-testing, Fibonacci maths, regression trend, anchored VWAP,
// fixed-range volume profile, long/short risk-reward and quantity maths. Hand-computed fixtures.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { dr, mapper, series, ms, ORIGIN, view } = require('./fixtures/drawings-fixture.cjs');
const lines = require(path.join(__dirname, '../static/drawings/tools-lines.js'));
const fib = require(path.join(__dirname, '../static/drawings/tools-fib.js'));
const measure = require(path.join(__dirname, '../static/drawings/tools-measure.js'));
const shapes = require(path.join(__dirname, '../static/drawings/tools-shapes.js'));
const patterns = require(path.join(__dirname, '../static/drawings/tools-patterns.js'));
const y = p => series.priceToCoordinate(p);
const P = (i, price) => ({ time: ms(i), price: String(price) });
const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps, `${a} ≉ ${b}`);

// Representative points for every registered tool (count = what the creation flow stores).
const SAMPLE = { 1: [P(4, 120)], 2: [P(2, 110), P(8, 120)], 3: [P(2, 110), P(8, 120), P(5, 130)], 4: [P(2, 110), P(5, 125), P(8, 115), P(11, 128)],
  5: [P(1, 110), P(3, 125), P(5, 115), P(7, 128), P(9, 112)], 6: [P(1, 110), P(3, 125), P(5, 115), P(7, 128), P(9, 112), P(11, 130)],
  7: [P(1, 110), P(3, 125), P(5, 115), P(7, 135), P(9, 115), P(11, 125), P(13, 110)] };
function sampleFor(def) {
  if (def.mode !== 'click') return [P(2, 110), P(5, 118), P(8, 112), P(10, 125)];
  if (def.type === 'long' || def.type === 'short') return def.finalize([P(4, 115), P(12, def.type === 'long' ? 125 : 105)], view());
  return SAMPLE[def.storedPoints || def.points];
}

test('every registered tool builds, has handles on its anchors and is hit at its first handle; far clicks miss', () => {
  const v = view();
  assert.ok(dr.toolList().length >= 54, `tool count ${dr.toolList().length}`);
  for (const def of dr.toolList()) {
    const d = dr.makeDrawing(def.type, sampleFor(def), { id: `t-${def.type}`, text: def.editText ? 'Hello' : undefined });
    const prims = dr.build(d, v);
    assert.ok(prims.length > 0, `${def.type} builds primitives`);
    const handles = dr.handles(d, v);
    assert.ok(handles.length > 0, `${def.type} has handles`);
    assert.equal(dr.hitTest([d], v, handles[0].x, handles[0].y)?.id, d.id, `${def.type} hit at its handle`);
    assert.equal(dr.hitTest([d], v, 295, 795), null, `${def.type} misses a far point`);
    assert.equal(dr.validDrawing(d), true, `${def.type} storable`);
  }
});

test('lines: trend extension, ray direction, info text, vertical/cross/horizontal geometry', () => {
  const v = view(), a = P(2, 110), b = P(8, 120);
  const trend = dr.makeDrawing('trend', [a, b]);
  assert.equal(dr.hitTest([trend], v, ORIGIN + 50, y(115) + 3).part, 'body');
  assert.equal(dr.hitTest([trend], v, ORIGIN + 50, y(115) + 12), null, 'outside the 6 px tolerance');
  assert.equal(dr.hitTest([trend], v, ORIGIN + 140, y(130)), null, 'segment ends at p1');
  const extended = dr.makeDrawing('trend', [a, b], { opts: { extendRight: true, extendLeft: true } });
  assert.equal(dr.hitTest([extended], v, ORIGIN + 140, y(130)).part, 'body', 'extended right');
  assert.equal(dr.hitTest([extended], v, ORIGIN, y(106.6667)).part, 'body', 'extended left');
  const ray = dr.makeDrawing('ray', [P(2, 150), P(4, 150)]);
  assert.equal(dr.hitTest([ray], v, ORIGIN + 250, y(150)).part, 'body');
  assert.equal(dr.hitTest([ray], v, ORIGIN + 5, y(150)), null, 'ray does not extend behind p0');
  const info = dr.makeDrawing('info', [P(2, 100), P(8, 103)]);
  const label = dr.build(info, v).find(p => p.t === 'text').text;
  assert.match(label, /\+3\.00/); assert.match(label, /\+3\.00%/); assert.match(label, /6 bars · 6m/); assert.match(label, /°/);
  near(lines.angleDeg({ x: 0, y: 0 }, { x: 10, y: -10 }), 45);
  const hray = dr.makeDrawing('hray', [P(10, 140)]);
  assert.equal(dr.hitTest([hray], v, ORIGIN + 50, y(140)), null, 'horizontal ray starts at its point');
  assert.equal(dr.hitTest([hray], v, ORIGIN + 200, y(140)).part, 'body');
  const vline = dr.makeDrawing('vline', [P(6, 120)]), cross = dr.makeDrawing('cross', [P(6, 120)]);
  assert.equal(dr.hitTest([vline], v, ORIGIN + 60, 700).part, 'body');
  assert.equal(dr.hitTest([cross], v, 5, y(120)).part, 'body'); assert.equal(dr.hitTest([cross], v, ORIGIN + 60, 20).part, 'body');
  assert.deepEqual(dr.dragHandle(vline, 'p0', P(9, 999), v), [{ time: ms(9), price: '120' }], 'vertical line drags in time only');
  assert.deepEqual(dr.dragHandle(dr.makeDrawing('hline', [P(1, 120)]), 'p0', P(9, 130), v), [{ time: ms(1), price: '130' }], 'horizontal line in price only');
});

test('channels: parallel channel offset and fill hit, flat top, disjoint mirror', () => {
  const v = view();
  const ch = dr.makeDrawing('channel', [P(2, 110), P(8, 120), P(5, 130)]);
  const segs = dr.build(ch, v).filter(p => p.t === 'seg');
  // line at bar 5 is 115; third point at 130 → offset +15 in price = -60 px
  near(segs[1].a.y - segs[0].a.y, y(130) - y(115));
  assert.equal(dr.hitTest([ch], v, ORIGIN + 50, y(122)).part, 'body', 'inside the filled channel');
  assert.equal(segs[2].dash, 'dashed', 'midline');
  const flat = dr.build(dr.makeDrawing('flattop', [P(2, 110), P(8, 120), P(5, 130)]), v).filter(p => p.t === 'seg');
  assert.equal(flat[1].a.y, flat[1].b.y, 'flat side is horizontal'); near(flat[1].a.y, y(130));
  const dis = dr.build(dr.makeDrawing('disjoint', [P(2, 110), P(8, 120), P(2, 140)]), v).filter(p => p.t === 'seg');
  near(dis[1].b.y - dis[1].a.y, -(dis[0].b.y - dis[0].a.y), 1e-9);
});

test('regression trend equals a hand-computed least-squares fit (±σ)', () => {
  // closes [1,3,2,5,4] → slope 0.8, intercept 1.4, residual σ = √(3.6/5) = 0.848528…
  const reg = dr.linearRegression([1, 3, 2, 5, 4]);
  near(reg.slope, 0.8); near(reg.intercept, 1.4); near(reg.stdDev, Math.sqrt(0.72));
  const cs = [1, 3, 2, 5, 4].map((c, i) => ({ time: i, open: c, high: c + 1, low: c - 1, close: c, volume: 1 }));
  const r = lines.regressionTrend(cs, 0, 4, 'close');
  near(r.start, 1.4); near(r.end, 4.6); assert.equal(r.n, 5);
  const hl2 = lines.regressionTrend(cs, 0, 4, 'hl2'); near(hl2.start, 1.4, 1e-12);
  // on the chart: from bars 2..8 of the fixture (closes 103..109 — exact line, σ = 0)
  const d = dr.makeDrawing('regression', [P(2, 100), P(8, 100)]);
  const segs = dr.build(d, view()).filter(p => p.t === 'seg');
  near(segs[0].a.y, y(103)); near(segs[0].b.y, y(109)); near(segs[1].a.y, y(103), 1e-9);
  assert.equal(lines.regressionTrend(cs, 2, 2), null, 'needs ≥ 2 bars');
});

test('fib maths: retracement (reverse), extension, time zones, custom levels', () => {
  near(fib.retracementPrice(100, 200, 0), 200); near(fib.retracementPrice(100, 200, 0.618), 138.2); near(fib.retracementPrice(100, 200, 1), 100);
  near(fib.retracementPrice(100, 200, 1.618), 38.2, 1e-9); near(fib.retracementPrice(100, 200, 0.618, true), 161.8);
  near(fib.extensionPrice(100, 200, 150, 1.618), 311.8, 1e-9); near(fib.extensionPrice(200, 100, 150, 1), 50);
  assert.deepEqual(fib.timeZoneLogicals(2, 4).slice(0, 7).map(z => z.logical), [2, 4, 6, 8, 12, 18, 28]);
  assert.deepEqual(fib.RETRACEMENT_LEVELS.filter(l => l.on).map(l => l.v), [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618, 2.618]);
  const d = dr.makeDrawing('fib', [P(2, 100), P(8, 150)], { opts: { levels: [{ v: 0.5, on: true, color: '#ffffff' }, { v: 0.25, on: false, color: '#000000' }] } });
  const prims = dr.build(d, view());
  const levelSegs = prims.filter(p => p.t === 'seg' && p.a.y === p.b.y);
  assert.equal(levelSegs.length, 1, 'disabled levels hidden'); near(levelSegs[0].a.y, y(125));
  assert.ok(prims.some(p => p.t === 'text' && p.text === '0.5 (125.00)'), 'label with price');
  const ext = dr.build(dr.makeDrawing('fib', [P(2, 100), P(8, 150)], { opts: { extendRight: true } }), view()).filter(p => p.t === 'seg' && p.a.y === p.b.y);
  assert.ok(ext.every(s => s.b.x > 300), 'extend right');
});

test('anchored VWAP and bands from real candles match a hand-computed fixture', () => {
  const cs = [{ high: 12, low: 8, close: 10, volume: 2 }, { high: 13, low: 10, close: 13, volume: 1 }, { high: 11, low: 8, close: 8, volume: 3 }]
    .map((c, i) => ({ time: i, open: c.close, ...c }));
  const out = measure.anchoredVwap(cs, 0);
  near(out[0].vwap, 10); near(out[1].vwap, 32 / 3); near(out[2].vwap, 59 / 6);
  near(out[1].sd, Math.sqrt((200 + 144) / 3 - (32 / 3) ** 2));
  near(measure.anchoredVwap(cs, 1)[0].vwap, 12, 1e-12);
  assert.deepEqual(measure.anchoredVwap([{ ...cs[0], volume: 0 }], 0), [], 'no volume → no value');
  const d = dr.makeDrawing('avwap', [P(15, 100)]);
  const poly = dr.build(d, view()).find(p => p.t === 'poly');
  assert.equal(poly.pts.length, 5, 'one point per bar from the anchor to the last bar');
});

test('volume profile: candle approximation, exact levels, POC and value area; footprint gating', () => {
  const cs = [{ low: 100, high: 110, volume: 10 }, { low: 105, high: 110, volume: 5 }].map((c, i) => ({ time: i, open: c.low, close: c.high, ...c }));
  const p = measure.profileFromCandles(cs, 0, 1, 2);
  assert.deepEqual(p.rows.map(r => r.volume), [5, 10]); assert.equal(p.poc, 1); assert.equal(p.total, 15);
  const va = measure.finishProfile([5, 10], 100, 5, 70);
  assert.deepEqual([va.vaLow, va.vaHigh], [0, 1]);
  const va2 = measure.finishProfile([1, 2, 10, 3, 1], 0, 1, 70); // 10/17 = 59% → add 3 (76%)
  assert.deepEqual([va2.vaLow, va2.vaHigh], [2, 3]);
  assert.deepEqual(measure.profileFromLevels([{ price: 100, volume: 1 }, { price: 101, volume: 3 }, { price: 102, volume: 1 }], 3).rows.map(r => r.volume), [1, 3, 1]);
  const one = 10n ** 18n, bar = { step: one / 2n, levels: new Map([['200', { index: 200n, bid: 2n * one, ask: one }], ['201', { index: 201n, bid: 0n, ask: one / 2n }]]) };
  const store = { intervalMs: 60000, bars: new Map([[0, { coverage: 'complete', bar }], [60000, { coverage: 'complete', bar }]]) };
  const cs2 = [{ time: 0 }, { time: 60 }];
  assert.deepEqual(measure.footprintLevels(store, cs2, 0, 1, 60000), [{ price: 100, volume: 6 }, { price: 100.5, volume: 1 }]);
  store.bars.get(60000).coverage = 'partial';
  assert.equal(measure.footprintLevels(store, cs2, 0, 1, 60000), null, 'partial coverage → candle approximation');
  const label = dr.build(dr.makeDrawing('vbp', [P(2, 100), P(8, 100)]), view()).find(x => x.t === 'text').text;
  assert.match(label, /approximated from candles/); assert.match(label, /7 bars/);
});

test('long/short position: R:R, quantity from account and risk %, %, ticks, P&L, validity', () => {
  const s = measure.positionStats('long', 100, 110, 95, { account: 1000, riskPct: 1, tick: 0.01 });
  assert.equal(s.valid, true); near(s.rr, 2); near(s.riskAmount, 10); near(s.qty, 2); near(s.targetPnl, 20); near(s.stopPnl, -10);
  near(s.targetPct, 10); near(s.stopPct, -5); assert.equal(s.targetTicks, 1000); assert.equal(s.stopTicks, 500);
  near(measure.positionStats('long', 100, 110, 95, { account: 1000, riskPct: 1, qtyStep: 0.3 }).qty, 1.8, 1e-12);
  const sh = measure.positionStats('short', 100, 90, 105, { account: 5000, riskPct: 2 });
  assert.equal(sh.valid, true); near(sh.qty, 20); near(sh.targetPnl, 200); near(sh.stopPnl, -100); near(sh.rr, 2);
  assert.equal(measure.positionStats('long', 100, 90, 95).valid, false, 'target below a long entry');
  const v = view();
  assert.deepEqual(measure.finalizePosition('long', [P(4, 100), P(10, 110)], v).map(p => p.price), ['100', '110', '95']);
  assert.deepEqual(measure.finalizePosition('long', [P(4, 100), P(10, 90)], v).map(p => p.price), ['100', '120', '90']);
  assert.deepEqual(measure.finalizePosition('short', [P(4, 100), P(10, 90)], v).map(p => p.price), ['100', '90', '105']);
  const d = dr.makeDrawing('long', measure.finalizePosition('long', [P(4, 100), P(10, 110)], v));
  assert.deepEqual(dr.dragHandle(d, 'p2', P(12, 97), v).map(p => p.price), ['100', '110', '97'], 'drag the stop');
  assert.ok(dr.build(d, v).some(p => p.t === 'text' && /R:R 2\.00/.test(p.text) && /qty 2\.00/.test(p.text)));
});

test('ranges: price/date stats with volume and ticks', () => {
  const st = measure.rangeStats(dr.makeDrawing('datepricerange', [P(2, 100), P(14, 103)]), view());
  near(st.dp, 3); near(st.pct, 3); assert.equal(st.bars, 12); assert.equal(st.ms, 12 * 60000); assert.equal(st.ticks, 300);
  assert.equal(st.volume, [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14].reduce((s, i) => s + 10 + i, 0));
  assert.equal(dr.formatDuration(90061000), '1d 1h'); assert.equal(dr.formatDuration(-3725000), '-1h 2m');
});

test('ticks use the venue tick from the drawing context; an explicit tool tick still wins; chart precision is the last fallback', () => {
  const v = { ...view(), tickSize: 0.1 };
  assert.equal(measure.rangeStats(dr.makeDrawing('datepricerange', [P(2, 100), P(14, 103)]), v).ticks, 30, '3.0 / 0.1 (Binance BTCUSDT tick)');
  assert.equal(measure.rangeStats(dr.makeDrawing('datepricerange', [P(2, 100), P(14, 103)]), { ...view(), tickSize: null }).ticks, 300, 'no metadata → 10^-precision');
  const d = dr.makeDrawing('long', measure.finalizePosition('long', [P(4, 100), P(10, 110)], v));
  const label = prims => prims.filter(p => p.t === 'text').map(p => p.text).join('\n');
  assert.match(label(dr.build(d, v)), /Target 110 [^\n]*\n100 ticks/, 'target 10.0 away at tick 0.1');
  assert.match(label(dr.build(d, v)), /Stop 95 [^\n]*\n50 ticks/);
  d.opts = { ...d.opts, tick: 0.5 };
  assert.match(label(dr.build(d, v)), /Target 110 [^\n]*\n20 ticks/, 'the tool option overrides the venue tick');
});

test('shapes and annotations: rectangle fill, circle, rotated rectangle, text box, simplify', () => {
  const v = view();
  const rect = dr.makeDrawing('rect', [P(12, 170), P(16, 160)]);
  assert.equal(dr.hitTest([rect], v, ORIGIN + 140, y(165)).part, 'body', 'inside');
  const noFill = dr.makeDrawing('rect', [P(12, 170), P(16, 160)], { opts: { fillBackground: false } });
  assert.equal(dr.hitTest([noFill], v, ORIGIN + 140, y(165)), null, 'unfilled rectangle is hollow');
  const circle = dr.makeDrawing('circle', [P(5, 150), P(7, 150)]);
  assert.equal(dr.hitTest([circle], v, ORIGIN + 50, y(150) - 19).part, 'body', 'inside radius 20');
  const corners = shapes.rotatedCorners({ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 5, y: 4 });
  assert.deepEqual(corners, [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 4 }, { x: 0, y: 4 }]);
  const t = dr.makeDrawing('text', [P(5, 150)], { text: 'Hello' });
  const box = dr.textBox(dr.build(t, v)[0]);
  assert.equal(dr.hitTest([t], v, box.left + box.w / 2, box.top + box.h / 2).id, t.id);
  const pts = Array.from({ length: 50 }, (_, i) => ({ x: i, y: i < 25 ? 0 : i - 25 }));
  assert.ok(dr.simplify(pts, 0.5).length <= 4, 'straight runs collapse to their corners');
  near(patterns.ratio(100, 200, 150), 0.5);
});

test('body drag shifts by whole bars and the price delta, into the future area', () => {
  const d = dr.makeDrawing('trend', [P(2, 110), P(8, 120)]);
  assert.deepEqual(dr.movePoints(d.points, mapper, { x: 100, y: 200 }, { x: 131, y: 180 }, 2), [P(5, 115), P(11, 125)]);
  assert.equal(dr.movePoints(d.points, mapper, { x: 100, y: 200 }, { x: 250, y: 200 }, 2)[1].time, ms(19) + 4 * 60000);
  assert.deepEqual(dr.movePoints([P(1, 120)], mapper, { x: 0, y: 0 }, { x: 50, y: 40 }, 2, true), [P(1, 110)], 'keepTime for horizontal lines');
});
