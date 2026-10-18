// Regression: a calibration-stats response for the previous chart must not overwrite the
// stats of the chart that is active when it arrives (stale "Calibration: …" line).
const test = require('node:test');
const assert = require('node:assert/strict');
const A = require('../static/analyst-panel.js');

test('late stats for the previous symbol are dropped', async () => {
  let active = { id: 0, exchange: 'binance', symbol: 'BTCUSDT', interval: '15' };
  const pending = [];
  const fetch = url => new Promise(resolve => {
    const symbol = new URLSearchParams(url.split('?')[1]).get('symbol');
    pending.push({ symbol, resolve: () => resolve({ ok: true, json: async () => ({ total: symbol === 'BTCUSDT' ? 111 : 222, pending: 0, overall: { n: 0 } }) }) });
  });
  const ctl = A.createController({ fetch, getActive: () => active, drawLines() {}, clearLines() {}, centerOn() {}, visible: () => true });
  try {
    assert.equal(pending.at(-1).symbol, 'BTCUSDT');
    active = { ...active, symbol: 'ETHUSDT' };
    ctl.activeChanged();
    const eth = pending.find(p => p.symbol === 'ETHUSDT'), btc = pending.find(p => p.symbol === 'BTCUSDT');
    eth.resolve(); await new Promise(r => setTimeout(r, 0));
    btc.resolve(); await new Promise(r => setTimeout(r, 0));
    assert.equal(ctl._state.stats.total, 222, 'stats belong to the active ETHUSDT chart');
    // The calibration line now renders in the Analysis dashboard from view().stats.
    assert.match(A.statsLine(ctl.view().stats), /222 logged/);
  } finally { ctl.destroy(); }
});
