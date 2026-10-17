'use strict';
const { connect } = require(process.env.CDP_CLIENT || './cd-cdp.cjs');
const OUT = '/Users/maxbelov/Projects/wt/hermes-analyst/docs/evidence/hermes-analyst/';
const [exchange, symbol, interval, name, layout = '1'] = process.argv.slice(2);
(async () => {
  const cdp = await connect(9563);
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await cdp.send('Page.navigate', { url: 'http://127.0.0.1:5090/ui' });
  await cdp.sleep(5000);
  await cdp.eval(`(() => { const s = document.querySelector('#layout-select'); s.value = '${layout}'; s.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await cdp.sleep(1000);
  await cdp.eval(`(() => { const d = window.__workspaceDebug; d.state.active = 0; d.state.panes[0] = { ...d.state.panes[0], exchange: '${exchange}', symbol: '${symbol}', interval: '${interval}' };
    d.panes.get(0).load(true); d.panes.get(0).element.dispatchEvent(new Event('pointerdown')); document.querySelector('#layout-select').dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await cdp.sleep(6000);
  const t0 = Date.now();
  await cdp.eval(`document.querySelector('[data-role=run]').click()`);
  let status = '';
  for (let i = 0; i < 150; i += 1) {
    await cdp.sleep(1000);
    status = await cdp.eval(`document.querySelector('[data-role=run]').disabled ? 'busy' : document.querySelector('[data-role=status]').textContent`);
    if (status !== 'busy') break;
  }
  await cdp.sleep(1500);
  const info = await cdp.eval(`(() => { const p = window.__workspaceDebug.panes.get(0); return { lines: (p.analystLines || []).length,
     target: document.querySelector('[data-role=target]').textContent, gauge: document.querySelector('.analyst-pcts')?.textContent,
     levels: [...document.querySelectorAll('.analyst-level-go')].map(b => b.textContent.replace(/\\s+/g, ' ').trim()),
     fresh: document.querySelector('.analyst-fresh')?.textContent, stats: document.querySelector('[data-role=stats]').textContent }; })()`);
  console.log(JSON.stringify({ ui_wait_s: (Date.now() - t0) / 1000, status, ...info }, null, 1));
  await cdp.eval(`document.querySelectorAll('.analyst-section').forEach(d => d.open = true)`);
  await cdp.shot(OUT + name + '.png');
  // click the first level → chart centres on it, then save it to the shared levels store
  await cdp.eval(`document.querySelector('.analyst-level-go')?.click()`);
  await cdp.sleep(800);
  await cdp.eval(`document.querySelector('.analyst-level-add')?.click()`);
  await cdp.sleep(800);
  const after = await cdp.eval(`(() => { const p = window.__workspaceDebug.panes.get(0); return { center: p.analystCenter,
    hermesLevels: (window.ScreenerLevels.list({}) || []).filter(l => l.note === 'hermes').map(l => [l.symbol, l.price, l.source]) }; })()`);
  console.log(JSON.stringify(after));
  await cdp.shot(OUT + name + '_centered.png');
  const errors = cdp.events.filter(e => e.method === 'Runtime.exceptionThrown' || (e.method === 'Log.entryAdded' && e.params.entry.level === 'error')).map(e => JSON.stringify(e.params).slice(0, 300));
  console.log('errors', errors.length, errors.slice(0, 5));
  cdp.close();
})().catch(e => { console.error(e); process.exit(1); });
