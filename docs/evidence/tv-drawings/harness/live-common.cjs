// Shared helpers for the live checks: open /ui on :5093 with one Binance BTCUSDT pane.
'use strict';
const { connect } = require('./cdp.cjs');
const BASE = 'http://127.0.0.1:5093/ui';
const EV = '/Users/maxbelov/Projects/wt/tv-drawings/docs/evidence/tv-drawings';

async function open({ interval = '15', reset = false, seedV1 = null } = {}) {
  const c = await connect(9566);
  await c.send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 900, deviceScaleFactor: 2, mobile: false });
  await c.send('Page.navigate', { url: BASE }); await c.sleep(2500);
  await c.eval(`(() => {
    const s = JSON.parse(localStorage.getItem('crypto-screener.workspace.v4') || 'null') || { version: 4 };
    s.version = 4; s.layout = '1'; s.active = 0; s.panes = s.panes || [];
    s.panes[0] = { exchange: 'binance', symbol: 'BTCUSDT', interval: ${JSON.stringify(interval)} };
    localStorage.setItem('crypto-screener.workspace.v4', JSON.stringify(s));
    if (${reset}) ['screener.drawings.v1', 'screener.drawings.v2', 'screener.levels.v1', 'screener.drawings.prefs.v1', 'screener.drawings.prefs.v2', 'screener.drawings.templates.v1'].forEach(k => localStorage.removeItem(k));
    if (${JSON.stringify(seedV1)}) localStorage.setItem('screener.drawings.v1', ${JSON.stringify(JSON.stringify(seedV1))});
    return true; })()`);
  await c.send('Page.reload'); await waitChart(c);
  return c;
}
async function waitChart(c, ms = 20000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const ok = await c.eval(`(() => { const st = document.querySelector('.chart-stage'); return Boolean(st && st.querySelector('.dw-toolbar') && document.querySelector('.chart-host canvas')); })()`).catch(() => false);
    if (ok) { await c.sleep(2500); return true; }
    await c.sleep(300);
  }
  throw new Error('chart did not load');
}
// Geometry of the first pane: host rect and plot size, plus the price→y/time→x of the live chart via
// the page's own controller (read back from the page, not re-derived).
async function geometry(c) {
  return c.eval(`(() => { const host = document.querySelector('.chart-host').getBoundingClientRect();
    const stage = document.querySelector('.chart-stage').getBoundingClientRect();
    return { host: { left: host.left, top: host.top, width: host.width, height: host.height }, stage: { left: stage.left, top: stage.top, width: stage.width, height: stage.height } }; })()`);
}
const stored = async c => (await c.eval(`JSON.parse(localStorage.getItem('screener.drawings.v2') || '{"symbols":{}}')`)).symbols['binance:BTCUSDT']?.items || [];
module.exports = { connect, open, waitChart, geometry, stored, EV, BASE };
