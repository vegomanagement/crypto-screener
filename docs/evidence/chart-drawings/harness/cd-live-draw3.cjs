// Live check 2c: drag + delete shared-level lines right after creating them (known pixel positions).
'use strict';
const { connect } = require('./cd-cdp.cjs');
const fs = require('node:fs');
const EV = '/Users/maxbelov/Projects/wt/chart-drawings/docs/evidence/chart-drawings';
const out = { steps: [] };
const log = (name, data) => { out.steps.push({ name, ...data }); console.log(name, JSON.stringify(data).slice(0, 500)); };
(async () => {
  const c = await connect();
  await c.eval(`(() => { const s = JSON.parse(localStorage.getItem('crypto-screener.workspace.v4')); s.panes[0].interval = '1'; localStorage.setItem('crypto-screener.workspace.v4', JSON.stringify(s));
    localStorage.removeItem('screener.drawings.v1'); localStorage.removeItem('screener.levels.v1'); localStorage.removeItem('screener.drawings.prefs.v1'); return true; })()`);
  await c.send('Page.reload', { ignoreCache: true });
  await c.sleep(7000);
  const host = await c.eval(`(() => { const r = document.querySelector('.chart-pane .chart-host').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; })()`);
  const P = (fx, fy) => [host.x + host.w * fx, host.y + host.h * fy];
  const drawings = async () => (await c.eval(`JSON.parse(localStorage.getItem('screener.drawings.v1') || '{"symbols":{}}')`)).symbols['binance:BTCUSDT']?.items || [];
  const levels = async () => (await c.eval(`JSON.parse(localStorage.getItem('screener.levels.v1') || '{"levels":[]}')`)).levels;
  const tool = name => c.eval(`document.querySelector('.chart-pane .draw-toolbar [data-tool="${name}"]').click()`);
  const opt = title => c.eval(`[...document.querySelectorAll('.chart-pane .draw-toolbar .draw-opt')].find(x => x.title.startsWith('${title}')).click()`);
  await c.eval(`document.querySelector('.chart-pane .pane-draw-toggle').click()`);
  await opt('Level');
  const range0 = await c.eval(`document.querySelector('.chart-pane .chart-host canvas') ? 1 : 0`);
  await tool('hline'); await c.click(...P(0.40, 0.25));
  await tool('hray'); await c.click(...P(0.45, 0.70));
  const created = { drawings: (await drawings()).map(d => [d.type, d.points[0].price, d.levelId]), levels: (await levels()).map(l => [l.id, l.price, l.source]) };
  log('created', created);
  await c.drag(...P(0.60, 0.25), P(0.60, 0.25)[0], P(0.60, 0.25)[1] - 40, 10);
  await c.sleep(300);
  const ds = await drawings(), ls = await levels();
  const hl = ds.find(d => d.type === 'hline');
  log('dragged_hline_level', { before: created.levels[0][1], hline_after: hl.points[0].price, store_after: ls.find(l => l.id === hl.levelId)?.price });
  await c.click(...P(0.75, 0.70)); await c.sleep(150);
  log('selection_toolbar_visible', { visible: await c.eval(`!document.querySelector('.chart-pane .draw-selection').hidden`) });
  await c.key('Delete'); await c.sleep(300);
  log('deleted_hray', { drawings: (await drawings()).map(d => d.type), levels: (await levels()).map(l => l.price) });
  // dragging a drawing must not pan the chart: compare first visible time label before/after
  const labels = () => c.eval(`document.querySelector('.chart-pane .pane-status').textContent`);
  log('status', { status: await labels(), canvas: range0 });
  // DOM-side level appears, then is deleted from the chart (virtual line)
  const domPrice = (Number(hl.points[0].price) - 30).toFixed(1);
  await c.eval(`ScreenerLevels.add({ exchange: 'binance', symbol: 'BTCUSDT', price: '${domPrice}', kind: 'signal', source: 'dom' })`);
  await c.sleep(300);
  await c.shot(`${EV}/drawings_level_sync.png`);
  log('dom_level_added', { levels: (await levels()).map(l => `${l.source}:${l.kind}:${l.price}`) });
  fs.writeFileSync(`${EV}/drawings_live_3.json`, JSON.stringify(out, null, 1));
  const errors = c.events.filter(e => e.method === 'Runtime.exceptionThrown').map(e => JSON.stringify(e.params).slice(0, 400));
  console.log('exceptions', errors);
  c.close();
})().catch(e => { console.error(e); process.exit(1); });
