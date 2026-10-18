// Live check 3: (a) v1 → v2 migration in the real page; (b) frame time with 200 objects while the
// chart is panned every animation frame (Binance BTCUSDT 15m, DPR 2).
'use strict';
const fs = require('node:fs');
const { open, stored, EV } = require('./live-common.cjs');
(async () => {
  const out = { when: new Date().toISOString() };
  // (a) migration: seed a v1 store as the old static/drawings.js wrote it
  const T = Date.UTC(2026, 9, 17, 6, 0, 0);
  const v1 = { version: 1, symbols: { 'binance:BTCUSDT': { hidden: false, items: [
    { id: 'v1-line', type: 'line', points: [{ time: T, price: '81000.5' }, { time: T + 6 * 900000, price: '81700.25' }], color: '#e5534b', width: 2, locked: true, level: false, levelId: null, createdAt: 1 },
    { id: 'v1-rect', type: 'rect', points: [{ time: T - 20 * 900000, price: '81200' }, { time: T - 10 * 900000, price: '81900.1' }], color: '#4fae6a', width: 1, locked: false, level: false, levelId: null, createdAt: 2 },
    { id: 'v1-hray', type: 'hray', points: [{ time: T - 30 * 900000, price: '80950.7' }], color: '#4f8cff', width: 3, locked: false, level: false, levelId: null, createdAt: 3 }] } } };
  let c = await open({ interval: '15', reset: true, seedV1: v1 });
  const migrated = await stored(c);
  out.migration = { v1Kept: (await c.eval(`localStorage.getItem('screener.drawings.v1')`)) === JSON.stringify(v1),
    items: migrated.map(d => ({ id: d.id, type: d.type, points: d.points, color: d.style.color, width: d.style.width, locked: d.locked })),
    rendered: await c.eval(`[...WorkspaceDrawings.instances][0].state.frame.prims.size`) };
  out.migration.lossless = migrated.length === 3 && v1.symbols['binance:BTCUSDT'].items.every((o, i) => JSON.stringify(o.points) === JSON.stringify(migrated[i].points)
    && o.color === migrated[i].style.color && o.width === migrated[i].style.width && o.locked === migrated[i].locked && o.id === migrated[i].id);
  console.log('migration', JSON.stringify(out.migration).slice(0, 400));
  await c.shot(`${EV}/migration_v1_to_v2.png`);
  c.close();

  // (b) 200 objects, pan every frame for 240 frames, measure rAF intervals and the overlay draw() cost
  c = await open({ interval: '15', reset: true });
  out.perf = await c.eval(`(async () => {
    const ctl = [...WorkspaceDrawings.instances][0], ctx = ctl.context(), v = ctl.view(), m = v.mapper, n = v.candles.length;
    const types = WorkspaceDrawings.toolList().filter(t => t.mode === 'click' && !t.temporary).map(t => t.type);
    const price = i => WorkspaceDrawings.priceString(v.candles[i].low + (v.candles[i].high - v.candles[i].low) * 0.5, 2);
    for (let i = 0; i < 200; i += 1) {
      const def = WorkspaceDrawings.getTool(types[i % types.length]), k = def.storedPoints || def.points, base = n - 120 + (i * 7) % 110;
      let pts = Array.from({ length: k }, (_, j) => ({ time: v.candles[Math.min(n - 1, base + j * 3)].time * 1000, price: price(Math.min(n - 1, base + j * 3)) }));
      if (def.finalize) pts = def.finalize(pts.slice(0, 2), v);
      ctl.actions.addDrawing(def.type, pts);
    }
    const count = ctl.state.items.length;
    await new Promise(r => setTimeout(r, 500));
    const ts = ctx.chart.timeScale(), start = ts.scrollPosition();
    ctl.state.perf.frames = 0; ctl.state.perf.total = 0; ctl.state.perf.max = 0;
    const deltas = [], draws = [];
    let last = performance.now();
    await new Promise(resolve => {
      let f = 0;
      const step = now => {
        deltas.push(now - last); last = now;
        ts.scrollToPosition(start - 40 * Math.sin(f / 30), false);
        draws.push(ctl.state.perf.last);
        if (++f < 240) requestAnimationFrame(step); else resolve();
      };
      requestAnimationFrame(step);
    });
    const sorted = a => a.slice().sort((x, y) => x - y), pct = (a, p) => sorted(a)[Math.floor((a.length - 1) * p)];
    const d = deltas.slice(1);
    return { objects: count, rendered: ctl.state.frame.prims.size, frames: d.length, drawCalls: ctl.state.perf.frames,
      rafMeanMs: d.reduce((s, x) => s + x, 0) / d.length, rafP50Ms: pct(d, 0.5), rafP95Ms: pct(d, 0.95), rafMaxMs: Math.max(...d),
      drawMeanMs: ctl.state.perf.total / Math.max(1, ctl.state.perf.frames), drawP95Ms: pct(draws, 0.95), drawMaxMs: ctl.state.perf.max,
      dpr: devicePixelRatio, plot: [v.w, v.h] };
  })()`);
  console.log('perf', JSON.stringify(out.perf));
  await c.shot(`${EV}/perf_200_objects.png`);
  fs.writeFileSync(`${EV}/live_migration_perf.json`, JSON.stringify(out, null, 1));
  c.close(); process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
