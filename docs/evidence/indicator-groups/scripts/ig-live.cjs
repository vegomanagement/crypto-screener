// Live check of grouped indicators on /ui (port 5091) via headless Chrome CDP (port 9564).
// Usage: node ig-live.cjs <outdir>
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { connect } = require('./cd-cdp.cjs');
const OUT = process.argv[2];
const BASE = 'http://127.0.0.1:5091/ui';
const CASES = [['BTCUSDT', '15'], ['BTCUSDT', '60'], ['ETHUSDT', '5']];
const ENABLED = { 'ema-ribbon': true, ema200: false, 'ma-channel': true, 'turtle-zone': true, opens: true, sessions: true, 'vwap-daily': false, liquidity: false };
const SETTINGS = { 'ma-channel': { fill: true }, sessions: { s1Op: true, orTargets: true, orBreakout: true, labelDay: true, labelPct: true }, opens: { labelPrice: true } };

(async () => {
  const cdp = await connect(9564);
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  const results = [];
  for (const [symbol, interval] of CASES) {
    const seed = { version: 5, layout: '1', active: 0, panes: [{ exchange: 'binance', symbol, interval }], fvg: true, sidebar: false, indicatorEnabled: ENABLED, indicatorSettings: SETTINGS };
    await cdp.send('Page.navigate', { url: 'about:blank' }); await cdp.sleep(300);
    await cdp.send('Page.navigate', { url: BASE }); await cdp.sleep(500);
    await cdp.eval(`localStorage.setItem('crypto-screener.workspace.v5', ${JSON.stringify(JSON.stringify(seed))}); location.reload(); true`);
    let ready = false;
    for (let i = 0; i < 80 && !ready; i += 1) {
      await cdp.sleep(500);
      ready = await cdp.eval(`(() => { const p = window.__workspaceDebug?.panes.get(0); return Boolean(p && p.candles.length && p.groupModels?.opens && p.groupModels?.sessions); })()`).catch(() => false);
    }
    await cdp.sleep(1500);
    const data = await cdp.eval(`(() => {
      const p = window.__workspaceDebug.panes.get(0);
      const m = p.groupModels;
      return { ready: ${'true'}, symbol: '${symbol}', interval: '${interval}', bars: p.candles.length, firstBar: p.candles[0].time, lastBar: p.candles.at(-1).time,
        candles: p.candles.map(c => [c.time, c.high, c.low]),
        opens: m.opens.levels.map(l => ({ kind: l.kind, key: l.key || l.tf, name: l.name, price: l.price, startTime: l.startTime, current: l.current, source: l.source || null, sourceTime: l.sourceTime || null })),
        opensWarnings: m.opens.warnings, htfErrors: p.htf?.errors,
        sessions: m.sessions.instances.map(i => ({ n: i.n, name: i.name, start: i.start, end: i.end, firstTime: i.firstTime, lastTime: i.lastTime, high: i.high, low: i.low, ended: i.ended, label: i.label,
          or: i.or && !i.or.pending ? { top: i.or.top, btm: i.or.btm, lastOrTime: i.or.lastOrTime, targets: i.or.targets, breakouts: i.or.breakouts } : (i.or ? 'pending' : null) })),
        ribbon: ['r0','r1','r2'].map(k => { const v = p.emaValues[k]; return v ? [...v.entries()].at(-1) : null; }),
        turtle: m.turtle ? m.turtle.zones.map(z => z.inner.at(-1)) : null,
        warnings: document.querySelector('.pane-plugin-warnings').textContent,
        chips: [...document.querySelectorAll('.indicator-toolbar .toggle-chip')].map(l => l.textContent.trim()),
        legend: document.querySelector('.chart-legend').textContent };
    })()`);
    data.ready = ready;
    results.push(data);
    const tag = `${symbol}-${interval}m`;
    await cdp.shot(path.join(OUT, `chart-${tag}.png`));
    console.log(tag, 'ready', ready, 'bars', data.bars, 'opens', data.opens.length, 'sessions', data.sessions.length, 'warn', data.warnings.slice(0, 300));
  }
  fs.writeFileSync(path.join(OUT, 'live-models.json'), JSON.stringify(results, null, 1));
  cdp.close();
})().catch(error => { console.error(error); process.exit(1); });
