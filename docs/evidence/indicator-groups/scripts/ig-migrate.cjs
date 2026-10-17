// Live: a v4 save with only EMA 28 (custom period 34) + MA20 High + prev-day-high migrates on the real page.
const fs = require('node:fs'); const { connect } = require('./cd-cdp.cjs');
(async () => {
  const c = await connect(9564);
  await c.send('Page.navigate', { url: 'http://127.0.0.1:5091/ui' }); await c.sleep(1500);
  await c.eval(`localStorage.clear(); localStorage.setItem('crypto-screener.workspace.v4', JSON.stringify({ version: 4, layout: '1', panes: [{ exchange: 'binance', symbol: 'BTCUSDT', interval: '60' }],
    ema: { 7: false, 14: false, 28: true, 200: false }, ma20hl: { high: true, low: false }, indicatorEnabled: { 'prev-day-high': true, 'session-london': true },
    indicatorSettings: { ema28: { period: 34, color: '#6b98cf' } } })); location.reload(); true`);
  await c.sleep(6000);
  const r = await c.eval(`(() => { const d = window.__workspaceDebug; return { version: d.state.version, enabled: d.state.indicatorEnabled, ribbon: d.getIndicatorSettings('ema-ribbon'),
    channel: [d.getIndicatorSettings('ma-channel').showHigh, d.getIndicatorSettings('ma-channel').showLow], opens: [d.getIndicatorSettings('opens').pdhShow],
    sessions: [1,2,3,4].map(n => d.getIndicatorSettings('sessions')['s' + n + 'Show']), legend: document.querySelector('.chart-legend').textContent,
    chips: [...document.querySelectorAll('[data-indicator-toggle]')].filter(i => i.checked).map(i => i.dataset.indicatorToggle),
    v5saved: JSON.parse(localStorage.getItem('crypto-screener.workspace.v5') || 'null')?.version ?? null }; })()`);
  fs.writeFileSync(process.argv[2], JSON.stringify(r, null, 1)); console.log(JSON.stringify(r));
  c.close();
})();
