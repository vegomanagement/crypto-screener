// Smoke: page loads, toolbar renders, no console errors; screenshot.
'use strict';
const { open, geometry, EV } = require('./live-common.cjs');
(async () => {
  const c = await open({ interval: '15', reset: true });
  const g = await geometry(c);
  const info = await c.eval(`(() => { const ctl = [...WorkspaceDrawings.instances][0]; const v = ctl.view();
    return { tools: WorkspaceDrawings.toolList().length, candles: v && v.candles.length, w: v && v.w, h: v && v.h, buttons: document.querySelectorAll('.dw-toolbar button').length,
      errors: (window.__errs || []).length }; })()`);
  console.log(JSON.stringify({ g, info }));
  const logs = c.events.filter(e => e.method === 'Runtime.exceptionThrown' || (e.method === 'Log.entryAdded' && e.params.entry.level === 'error')).map(e => JSON.stringify(e.params).slice(0, 300));
  console.log('errors', logs);
  await c.shot(`${EV}/smoke.png`);
  c.close(); process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
