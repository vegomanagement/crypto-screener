// Live check 2b: magnet exactness vs Binance klines, drag/delete level sync, persistence across intervals.
'use strict';
const { connect } = require('./cd-cdp.cjs');
const fs = require('node:fs');
const EV = '/Users/maxbelov/Projects/wt/chart-drawings/docs/evidence/chart-drawings';
const prev = JSON.parse(fs.readFileSync(`${EV}/drawings_live.json`, 'utf8'));
const out = { steps: [] };
const log = (name, data) => { out.steps.push({ name, ...data }); console.log(name, JSON.stringify(data).slice(0, 500)); };
(async () => {
  const c = await connect();
  await c.send('Page.reload', { ignoreCache: true });
  await c.sleep(7000);
  const host = prev.steps.find(s => s.name === 'host');
  const P = (fx, fy) => [host.x + host.w * fx, host.y + host.h * fy];
  const drawings = async () => (await c.eval(`JSON.parse(localStorage.getItem('screener.drawings.v1') || 'null')`)).symbols['binance:BTCUSDT'].items;
  const levels = async () => (await c.eval(`JSON.parse(localStorage.getItem('screener.levels.v1') || 'null')`)).levels;
  // a. magnet: the line endpoints must equal an O/H/L/C of the real Binance 1m kline at that time
  const line = (await drawings()).find(d => d.type === 'line');
  const checks = [];
  for (const p of line.points) {
    const k = (await (await fetch(`https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1m&startTime=${p.time}&limit=1`)).json())[0];
    const ohlc = { open: k[1], high: k[2], low: k[3], close: k[4] };
    const match = Object.entries(ohlc).find(([, v]) => Number(v) === Number(p.price));
    checks.push({ time: new Date(p.time).toISOString(), stored: p.price, kline_open_time: new Date(k[0]).toISOString(), ohlc, snapped_to: match ? match[0] : null });
  }
  log('magnet_vs_binance_kline', { checks });
  // b. drag the shared-level horizontal line up by 40 px → store updated, drawing follows
  const before = await levels();
  await c.drag(...P(0.40, 0.22), P(0.40, 0.22)[0], P(0.40, 0.22)[1] - 40);
  await c.sleep(300);
  const after = await levels(); const ds = await drawings();
  const hl = ds.find(d => d.type === 'hline');
  log('drag_level', { before: before.map(l => l.price), after: after.map(l => l.price), hline_price: hl.points[0].price, linked_level_price: after.find(l => l.id === hl.levelId)?.price });
  // c. select the horizontal ray (shared level) and delete with Del → gone from both stores
  const [hx, hy] = P(0.60, 0.78);
  await c.click(hx, hy); await c.sleep(150); await c.key('Delete');
  await c.sleep(300);
  log('delete_hray_level', { drawings: (await drawings()).map(d => d.type), levels: (await levels()).map(l => `${l.source}:${l.kind}:${l.price}`) });
  // d. DOM-side level moved in the store → re-drawn; then removed on the chart (virtual line): click it and Del
  const dom = (await levels()).find(l => l.source === 'dom');
  await c.eval(`ScreenerLevels.update('${dom.id}', { price: '${(Number(dom.price) + 10).toFixed(1)}' })`);
  await c.sleep(300);
  await c.shot(`${EV}/drawings_after_edit.png`);
  log('dom_level_moved', { levels: (await levels()).map(l => `${l.source}:${l.kind}:${l.price}`) });
  // e. persistence across intervals: switch to 5m and to 5s; drawings come from the same (venue, symbol) entry
  await c.eval(`[...document.querySelectorAll('#favorite-intervals button')].find(b => b.dataset.interval === '5').click()`);
  await c.sleep(6000);
  await c.shot(`${EV}/drawings_5m.png`);
  log('interval_5m', { interval: await c.eval(`document.querySelector('.chart-pane').dataset.interval`), drawings: (await drawings()).length });
  await c.eval(`[...document.querySelectorAll('#favorite-intervals button')].find(b => b.dataset.interval === '5s').click()`);
  await c.sleep(12000);
  await c.shot(`${EV}/drawings_on_5s.png`);
  log('interval_5s', { interval: await c.eval(`document.querySelector('.chart-pane').dataset.interval`), drawings: (await drawings()).length, status: await c.eval(`document.querySelector('.chart-pane .pane-status').textContent`) });
  const errors = c.events.filter(e => e.method === 'Runtime.exceptionThrown').map(e => JSON.stringify(e.params).slice(0, 400));
  log('js_exceptions', { errors });
  fs.writeFileSync(`${EV}/drawings_live_2.json`, JSON.stringify(out, null, 1));
  c.close();
})().catch(e => { console.error(e); process.exit(1); });
