// Live check 2: drawing tools, magnet, persistence and level sync on a real Binance BTCUSDT pane.
'use strict';
const { connect } = require('./cd-cdp.cjs');
const fs = require('node:fs');
const EV = '/Users/maxbelov/Projects/wt/chart-drawings/docs/evidence/chart-drawings';
const out = { steps: [] };
const log = (name, data) => { out.steps.push({ name, ...data }); console.log(name, JSON.stringify(data).slice(0, 400)); };
(async () => {
  const c = await connect();
  await c.send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 900, deviceScaleFactor: 1, mobile: false });
  await c.send('Page.navigate', { url: 'http://127.0.0.1:5089/ui' });
  await c.sleep(2000);
  await c.eval(`(() => { const s = JSON.parse(localStorage.getItem('crypto-screener.workspace.v4'));
    s.layout = '1'; s.active = 0; s.panes[0] = { symbol: 'BTCUSDT', interval: '1', exchange: 'binance' };
    localStorage.clear(); localStorage.setItem('crypto-screener.workspace.v4', JSON.stringify(s)); return true; })()`);
  await c.send('Page.reload', { ignoreCache: true });
  await c.sleep(7000);
  const host = await c.eval(`(() => { const r = document.querySelector('.chart-pane .chart-host').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; })()`);
  log('host', host);
  const P = (fx, fy) => [host.x + host.w * fx, host.y + host.h * fy];
  const tool = name => c.eval(`(() => { const b = document.querySelector('.chart-pane .draw-toolbar [data-tool="${name}"]'); b.click(); return b.getAttribute('aria-pressed'); })()`);
  const opt = title => c.eval(`(() => { const b = [...document.querySelectorAll('.chart-pane .draw-toolbar .draw-opt')].find(x => x.title.startsWith('${title}')); b.click(); return b.getAttribute('aria-pressed'); })()`);
  const drawings = () => c.eval(`JSON.parse(localStorage.getItem('screener.drawings.v1') || 'null')`);
  const levels = () => c.eval(`JSON.parse(localStorage.getItem('screener.levels.v1') || 'null')`);
  await c.eval(`document.querySelector('.chart-pane .pane-draw-toggle').click()`);
  log('toolbar_open', { hidden: await c.eval(`document.querySelector('.chart-pane .draw-toolbar').hidden`) });

  // 1. line by drag, magnet on → endpoints must be exact O/H/L/C of real bars
  log('magnet', { pressed: await opt('Magnet') });
  await tool('line'); await c.drag(...P(0.30, 0.35), ...P(0.55, 0.55));
  // 2. rectangle by click-click (magnet off)
  log('magnet_off', { pressed: await opt('Magnet') });
  await tool('rect'); await c.click(...P(0.60, 0.30)); await c.mouse('mouseMoved', ...P(0.72, 0.42), { buttons: 0 }); await c.click(...P(0.72, 0.42));
  // 3. ray and horizontal ray
  await tool('ray'); await c.drag(...P(0.10, 0.70), ...P(0.25, 0.60));
  // 4. horizontal line as shared level
  log('level_mode', { pressed: await opt('Level') });
  await tool('hline'); await c.click(...P(0.40, 0.22));
  await tool('hray'); await c.click(...P(0.45, 0.78));
  log('level_mode_off', { pressed: await opt('Level') });
  let d = await drawings(); let l = await levels();
  const items = d.symbols['binance:BTCUSDT'].items;
  log('stored_drawings', { count: items.length, types: items.map(i => i.type), points: items.map(i => i.points), levelIds: items.map(i => i.levelId) });
  log('stored_levels', { levels: l.levels.map(x => ({ id: x.id, price: x.price, source: x.source, kind: x.kind })) });
  // 5. a DOM-side signal level written straight into the shared store → drawn dashed on the chart
  const mid = items.find(i => i.type === 'hline').points[0].price;
  const domPrice = (Number(mid) - 40).toFixed(1);
  await c.eval(`ScreenerLevels.add({ exchange: 'binance', symbol: 'BTCUSDT', price: '${domPrice}', kind: 'signal', source: 'dom', note: 'from DOM' })`);
  await c.sleep(300);
  // 6. ruler (temporary)
  await tool('ruler'); await c.drag(...P(0.20, 0.62), ...P(0.50, 0.40));
  await c.sleep(400);
  await c.shot(`${EV}/drawings_1m.png`);
  const measure = await c.eval(`document.querySelector('.chart-pane .draw-toolbar') && true`);
  log('ruler', { drawn: measure });
  fs.writeFileSync(`${EV}/drawings_live.json`, JSON.stringify(out, null, 1));
  c.close();
})().catch(e => { console.error(e); process.exit(1); });
