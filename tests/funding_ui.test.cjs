'use strict';
// Funding display helpers (pane chip, analyst section, sparkline) against a recorded /api/funding shape.
const assert = require('node:assert/strict');
const test = require('node:test');
const F = require('../static/funding.js');

const NOW = 1792257683000;
const DATA = {
  exchange: 'binance', symbol: 'BTCUSDT', as_of: NOW,
  last_settled: { rate: '0.00003726', time: 1792252800003, source: 'binance GET /fapi/v1/fundingRate' },
  predicted: { rate: '0.00002886', settles_at: 1792281600000, note: 'live estimate', source: 'premiumIndex' },
  interval_hours: { value: 8, source: 'fundingInfo' },
  annualized: { last_settled: '0.0407997', predicted: '0.0316017', formula: 'rate × 24 / interval_hours × 365' },
  history: { items: [{ rate: '-0.00004135', time: 1 }, { rate: '0.0001', time: 2 }, { rate: '0.00003726', time: 3 }] },
  trend: { n: 3, positive: 2, direction: 'rising' },
  cross_venue: { annualized_spread: '0.04', rows: [
    { venue: 'hyperliquid', rate: '0.0000088168', interval_hours: 1, settles_at: NOW - 1000, annualized: '0.0772', stale: true },
    { venue: 'binance', rate: '0.00002876', interval_hours: 8, settles_at: 1792281600000, annualized: '0.0315', stale: false },
    { venue: 'bybit', rate: '-0.00006508', interval_hours: 8, settles_at: 1792281600000, annualized: '-0.0713', stale: false }] },
  missing: {}
};

test('pct, annualPct and countdown formatting', () => {
  assert.equal(F.pct('0.00003726'), '0.0037%');
  assert.equal(F.pct(null), '—');
  assert.equal(F.pct('abc'), '—');
  assert.equal(F.annualPct('0.0407997'), '4.08%/yr');
  assert.equal(F.countdown(2 * 3600e3 + 13 * 60e3 + 40e3 + 999), '02:13:40');
  assert.equal(F.countdown(-5), '00:00:00');
});

test('chip shows last → predicted with countdown, coloured by predicted sign', () => {
  const chip = F.chipModel(DATA, 1792281600000 - (2 * 3600e3 + 13 * 60e3 + 40e3));
  assert.equal(chip.text, 'F 0.0037% → 0.0029% · 02:13:40');
  assert.equal(chip.sign, 'pos');
  assert.match(chip.title, /Last settled: 0\.0037% \(4\.08%\/yr\)/);
  assert.match(chip.title, /live estimate, changes until settlement/);
  assert.match(chip.title, /Interval: 8h/);
  assert.equal(F.chipModel({ ...DATA, predicted: { rate: '-0.0001', settles_at: NOW } }, NOW).sign, 'neg');
  assert.equal(F.chipModel({ ...DATA, last_settled: null }, NOW).text.startsWith('F — → 0.0029%'), true);
  assert.equal(F.chipModel({ error: 'x' }, NOW), null);
});

test('cross-venue rows are ordered and mark self + stale', () => {
  const rows = F.crossRows(DATA);
  assert.deepEqual(rows.map(r => r.venue), ['Binance', 'Bybit', 'Hyperliquid']);
  assert.equal(rows[0].self, true);
  assert.equal(rows[2].stale, true);
  assert.equal(rows[1].sign, 'neg');
  assert.deepEqual(F.crossRows({}), []);
});

test('sparkline spans min..max with a zero baseline', () => {
  const s = F.sparkline(DATA.history.items, 100, 20);
  assert.equal(s.n, 3);
  const ys = s.points.split(' ').map(p => Number(p.split(',')[1]));
  assert.equal(Math.max(...ys), 18);   // lowest rate at the bottom
  assert.equal(Math.min(...ys), 2);    // highest rate at the top
  assert.ok(s.zeroY > 2 && s.zeroY < 18);
  assert.equal(F.sparkline([{ rate: '1' }]), null);
});

test('section HTML escapes and labels predicted as an estimate', () => {
  const html = F.sectionHtml({ ...DATA, symbol: '<x>' }, NOW);
  assert.match(html, /Last settled/);
  assert.match(html, /Live estimate for the current period/);
  assert.match(html, /<polyline points=/);
  assert.match(html, /stale/);
  assert.match(F.sectionHtml({ error: 'funding unavailable', detail: '<b>x</b>' }, NOW), /&lt;b&gt;x&lt;\/b&gt;/);
  assert.match(F.sectionHtml(null, NOW), /loading/);
});

test('analyst panel renders the funding section from workspace data', () => {
  const A = require('../static/analyst-panel.js');
  const roles = {};
  const node = () => ({ textContent: '', innerHTML: '', disabled: false, classList: { toggle() {} }, addEventListener() {} });
  const el = { set innerHTML(html) { for (const m of html.matchAll(/data-role="(\w+)"/g)) roles[m[1]] = roles[m[1]] || node(); },
    querySelector(sel) { return roles[/data-role="(\w+)"/.exec(sel)[1]]; }, addEventListener() {} };
  const ctl = A.mount(el, { fetch: async () => ({ ok: false, json: async () => ({}) }), getActive: () => null, drawLines() {}, clearLines() {}, centerOn() {}, visible: () => false, funding: F });
  try {
    ctl.setFunding(DATA, NOW);
    assert.match(roles.funding.innerHTML, /Predicted/);
    assert.match(roles.funding.innerHTML, /0\.0037%/);
  } finally { ctl.destroy(); }
});
