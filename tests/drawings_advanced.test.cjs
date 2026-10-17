'use strict';
// Drawing tools — Tier 2 maths: pitchfork variants, Gann fan, golden spiral, trend-based Fib time,
// cycles, sine, bars pattern, ghost feed and table layout.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { dr, series, candles, ms, ORIGIN, view } = require('./fixtures/drawings-fixture.cjs');
const adv = require(path.join(__dirname, '../static/drawings/tools-advanced.js'));
const y = p => series.priceToCoordinate(p);
const P = (i, price) => ({ time: ms(i), price: String(price) });
const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) <= eps, `${a} ≉ ${b}`);

test('pitchfork variants: origin and median through the B–C midpoint; parallels through B and C', () => {
  const a = { x: 0, y: 100 }, b = { x: 50, y: 0 };
  assert.deepEqual(adv.pitchOrigin('standard', a, b), a);
  assert.deepEqual(adv.pitchOrigin('schiff', a, b), { x: 0, y: 50 });
  assert.deepEqual(adv.pitchOrigin('modschiff', a, b), { x: 25, y: 50 });
  const v = view(), d = dr.makeDrawing('pitchfork', [P(2, 110), P(6, 130), P(8, 120)], { opts: { fillBackground: false } });
  const segs = dr.build(d, v).filter(p => p.t === 'seg');
  const median = segs[1], m = { x: (ORIGIN + 60 + ORIGIN + 80) / 2, y: (y(130) + y(120)) / 2 };
  near(dr.distanceToSegment(m, median.a, median.b), 0);
  assert.ok(segs.some(s => Math.abs(s.a.x - (ORIGIN + 60)) < 1e-9 && Math.abs(s.a.y - y(130)) < 1e-9), 'upper tine starts at B');
  assert.ok(segs.some(s => Math.abs(s.a.x - (ORIGIN + 80)) < 1e-9 && Math.abs(s.a.y - y(120)) < 1e-9), 'lower tine starts at C');
  const inside = dr.build(dr.makeDrawing('insidepitchfork', d.points, { opts: { fillBackground: false } }), v).filter(p => p.t === 'seg');
  assert.equal(inside.length, 2 + 8, 'B–C base, median and ±0.25/0.5/0.75/1 tines');
});

test('Gann fan: the 1×1 ray is the A→B diagonal; 1×2 halves the price move', () => {
  const rays = adv.gannFanRays({ x: 0, y: 0 }, { x: 100, y: -80 });
  assert.equal(rays.length, 9);
  assert.deepEqual(rays.find(r => r.label === '1×1').to, { x: 100, y: -80 });
  assert.deepEqual(rays.find(r => r.label === '1×2').to, { x: 50, y: -80 });
  assert.deepEqual(rays.find(r => r.label === '2×1').to, { x: 100, y: -40 });
});

test('golden spiral grows by φ per quarter turn and passes through B', () => {
  const pts = adv.spiralPoints({ x: 0, y: 0 }, { x: 10, y: 0 }, 1, 400);
  const atB = pts.find(p => Math.abs(p.x - 10) < 1e-9 && Math.abs(p.y) < 1e-9);
  assert.ok(atB, 'passes through B');
  const r = p => Math.hypot(p.x, p.y), outward = pts.slice(-60);
  near(r(outward[29]), 10 * adv.PHI, 1e-6); // +90°
});

test('trend-based Fib time, cyclic lines, sine', () => {
  assert.deepEqual(adv.trendFibTimeLogicals(2, 6, 10, [0, 0.5, 1, 1.618]).map(z => z.logical), [10, 12, 14, 16.472]);
  const v = view({ w: 300 });
  const xs = adv.cycleXs(v, 2, 5);
  assert.equal(xs[0] <= 0 + 30, true); assert.ok(xs.includes(ORIGIN + 20) && xs.includes(ORIGIN + 50) && xs.includes(ORIGIN + 80), 'repeats every 3 bars');
  assert.deepEqual(adv.cycleXs(v, 2, 2), []);
  const s = adv.sinePoints(v, { x: 50, y: 100 }, { x: 80, y: 160 });
  const exact = x => 130 - 30 * Math.cos(Math.PI * (x - 50) / 30);
  s.forEach(p => near(p.y, exact(p.x), 1e-9));
  near(exact(50), 100); near(exact(80), 160); near(exact(110), 100);
});

test('bars pattern copies the real candles shifted to the anchor; ghost feed walks point to point', () => {
  const copy = adv.barsPattern(candles, [3, 5], 30, 200);
  assert.deepEqual(copy.map(k => k.logical), [30, 31, 32]);
  near(copy[0].open, 200); near(copy[1].close - copy[1].open, candles[4].close - candles[4].open); near(copy[2].high, candles[5].high + (200 - candles[3].open));
  const ghosts = adv.ghostCandles([P(2, 100), P(6, 120), P(8, 110)], view());
  assert.equal(ghosts.length, 4 + 2);
  near(ghosts[0].open, 100); near(ghosts[3].close, 120); near(ghosts[5].close, 110);
  ghosts.slice(1).forEach((k, i) => near(k.open, ghosts[i].close, 1e-9));
  ghosts.forEach(k => assert.ok(k.high >= Math.max(k.open, k.close) && k.low <= Math.min(k.open, k.close)));
});

test('table layout and Tier-2 tools are registered in their groups', () => {
  const L = adv.tableLayout('A | Long cell\nx', 10, t => t.length * 6);
  assert.equal(L.cols, 2); assert.deepEqual(L.widths, [16, 64]); assert.deepEqual(L.rows[1], ['x']);
  const groups = Object.fromEntries(dr.toolList().map(t => [t.type, t.group]));
  ['pitchfork', 'schiff', 'modschiff', 'insidepitchfork', 'pitchfan'].forEach(t => assert.equal(groups[t], 'channels'));
  ['gannfan', 'gannbox', 'gannsquare', 'fibspeedfan', 'fibarcs', 'fibcircles', 'fibspiral', 'fibwedge', 'fibtrendtime'].forEach(t => assert.equal(groups[t], 'fib'));
  ['cyclic', 'timecycles', 'sine', 'projection', 'barspattern', 'ghostfeed'].forEach(t => assert.equal(groups[t], 'measure'));
  ['signpost', 'table'].forEach(t => assert.equal(groups[t], 'annotation'));
});
