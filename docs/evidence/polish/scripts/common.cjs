// Shared helpers for the polish live checks (/ui on :5096, headless Chrome CDP :9569).
'use strict';
const { connect } = require('./cdp.cjs');
const BASE = 'http://127.0.0.1:5096/ui';
async function open(seed, { width = 1600, height = 1000, wait = p => true } = {}) {
  const cdp = await connect(9569);
  await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
  await cdp.send('Network.enable'); await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
  await cdp.send('Page.navigate', { url: 'about:blank' }); await cdp.sleep(300);
  await cdp.send('Page.navigate', { url: BASE }); await cdp.sleep(800);
  await cdp.eval(`localStorage.setItem('crypto-screener.workspace.v5', ${JSON.stringify(JSON.stringify(seed))}); location.reload(); true`);
  await cdp.sleep(800);
  return cdp;
}
async function waitFor(cdp, expr, tries = 80, ms = 500) {
  for (let i = 0; i < tries; i += 1) {
    await cdp.sleep(ms);
    const ok = await cdp.eval(expr).catch(() => false);
    if (ok) return true;
  }
  return false;
}
const seed = (panes, enabled = {}, settings = {}, layout = '1') => ({ version: 5, layout, active: 0, panes, fvg: false, sidebar: false,
  indicatorEnabled: { 'ema-ribbon': true, ema200: false, 'ma-channel': false, 'turtle-zone': false, opens: false, sessions: false, 'vwap-daily': false, 'vwap-weekly': false, liquidity: false, ...enabled },
  indicatorSettings: settings });
module.exports = { open, waitFor, seed, BASE };
