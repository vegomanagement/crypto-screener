// Regression smoke on the live server: D interval (sessions hidden), 5s seconds interval,
// FVG zones, footprint toggle, drawings toolbar, local FVG alerts toggle; collects page exceptions.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { connect } = require('./cd-cdp.cjs');
const OUT = process.argv[2];
(async () => {
  const cdp = await connect(9564);
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  const seed = { version: 5, layout: '1', active: 0, panes: [{ exchange: 'binance', symbol: 'BTCUSDT', interval: 'D' }], fvg: true, sidebar: false,
    indicatorEnabled: { 'ema-ribbon': true, 'ma-channel': true, 'turtle-zone': true, opens: true, sessions: true } };
  await cdp.send('Page.navigate', { url: 'http://127.0.0.1:5091/ui' }); await cdp.sleep(800);
  await cdp.eval(`localStorage.setItem('crypto-screener.workspace.v5', ${JSON.stringify(JSON.stringify(seed))}); location.reload(); true`);
  const wait = async (expr, n = 60) => { for (let i = 0; i < n; i += 1) { await cdp.sleep(500); if (await cdp.eval(expr).catch(() => false)) return true; } return false; };
  const out = {};
  out.dailyReady = await wait(`Boolean(window.__workspaceDebug?.panes.get(0)?.groupModels?.opens)`);
  await cdp.sleep(1000);
  out.daily = await cdp.eval(`(() => { const p = window.__workspaceDebug.panes.get(0); return { interval: '${'D'}', sessionsHidden: p.groupModels.sessions?.hidden, sessionsCount: p.groupModels.sessions?.instances.length,
    opensTfs: p.groupModels.opens.levels.filter(l => l.current).map(l => l.name), warnings: document.querySelector('.pane-plugin-warnings').textContent,
    fvgZones: p.effectiveFvgZones(window.__workspaceDebug.getIndicatorSettings('fvg')).length }; })()`);
  await cdp.shot(path.join(OUT, 'chart-BTCUSDT-D-sessions-hidden.png'));
  // Seconds interval (trade-built) with grouped indicators on.
  await cdp.eval(`document.querySelector('.pane-interval').value = '5s'; document.querySelector('.pane-interval').dispatchEvent(new Event('change')); true`);
  out.secondsReady = await wait(`(window.__workspaceDebug.panes.get(0).candles.length >= 3)`, 80);
  await cdp.sleep(1500);
  out.seconds = await cdp.eval(`(() => { const p = window.__workspaceDebug.panes.get(0); return { interval: document.querySelector('.chart-pane').dataset.interval, bars: p.candles.length,
    sessions: p.groupModels.sessions?.instances.length ?? null, opensCurrent: (p.groupModels.opens?.levels || []).filter(l => l.current).map(l => l.name + ' ' + l.price), status: p.status.textContent }; })()`);
  // Footprint + drawings toolbar + local alerts toggle on a 1m chart.
  await cdp.eval(`document.querySelector('.pane-interval').value = '1'; document.querySelector('.pane-interval').dispatchEvent(new Event('change')); true`);
  await wait(`window.__workspaceDebug.panes.get(0).candles.length > 100 && document.querySelector('.chart-pane').dataset.interval === '1'`);
  await cdp.eval(`document.querySelector('.pane-footprint-toggle').click(); document.querySelector('#local-alerts-toggle').click(); true`);
  await cdp.sleep(4000);
  out.other = await cdp.eval(`(() => { const p = window.__workspaceDebug.panes.get(0); return { footprintPressed: document.querySelector('.pane-footprint-toggle').getAttribute('aria-pressed'),
    footprintStatus: document.querySelector('.pane-footprint-status')?.textContent, drawingsToolbar: Boolean(document.querySelector('.chart-pane [class*="drawing"]')),
    localAlerts: document.querySelector('#local-alerts-toggle').checked, fvg: p.effectiveFvgZones(window.__workspaceDebug.getIndicatorSettings('fvg')).length }; })()`);
  await cdp.shot(path.join(OUT, 'chart-BTCUSDT-1m-footprint.png'));
  await cdp.eval(`document.querySelector('.pane-footprint-toggle').click(); document.querySelector('#local-alerts-toggle').click(); true`);
  out.exceptions = cdp.events.filter(e => e.method === 'Runtime.exceptionThrown').map(e => e.params.exceptionDetails.exception?.description || e.params.exceptionDetails.text);
  fs.writeFileSync(path.join(OUT, 'regression-smoke.json'), JSON.stringify(out, null, 1));
  console.log(JSON.stringify(out, null, 1));
  cdp.close();
})().catch(error => { console.error(error); process.exit(1); });
