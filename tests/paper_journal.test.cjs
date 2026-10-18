'use strict';
// Paper journal page: pure view-model and renderer (no network).
const test = require('node:test');
const assert = require('node:assert/strict');
const P = require('../static/paper-journal.js');

const T0 = Date.UTC(2026, 9, 18, 8);
const trades = [
  { id: 1, symbol: 'BTCUSDT', side: 'long', state: 'pending', knowable_ts: T0, recorded_ts: T0 + 30000, planned_entry: 100,
    planned_stop: 95, planned_target: 110, inputs: { poi: { kind: 'ob', bottom: 98, top: 100 } }, seen_at_start: 0, late: 0 },
  { id: 2, symbol: 'ETHUSDT', side: 'short', state: 'be', knowable_ts: T0 - 1, fill_ts: T0, entry: 10, stop: 10, target: 9,
    planned_entry: 10, seen_at_start: 1, late: 0, inputs: {} },
  { id: 3, symbol: 'SOLUSDT', side: 'long', state: 'target', fill_ts: T0, exit_ts: T0 + 9e6, entry: 1.5, r_gross: 2,
    fee_r: 0.01, fund_r: -0.002, net_r: 1.988, net_pct: 3.1, seen_at_start: 0 },
  { id: 4, symbol: 'SOLUSDT', side: 'long', state: 'stop', fill_ts: T0, exit_ts: T0 + 1e6, entry: 1.5, r_gross: -1,
    fee_r: null, fund_r: null, net_r: null, fund_status: 'pending: HTTP 503', seen_at_start: 0 },
  { id: 5, symbol: 'X', side: 'long', state: 'expired', knowable_ts: T0 },
];

test('open rows: pending + filled only, warm-up rows tagged, planned prices shown', () => {
  const rows = P.openRows(trades);
  assert.deepEqual(rows.map(r => r.id), [1, 2]);
  assert.equal(rows[0].entry, '100.00');
  assert.equal(rows[0].poi, 'OB 98.0000–100.00');
  assert.equal(rows[0].knowable, '2026-10-18 08:00 UTC');
  assert.match(rows[1].tag, /seen at start/);
  assert.equal(rows[1].state, 'filled · stop at BE');
});

test('closed rows: net R with costs, missing costs explained, newest first', () => {
  const rows = P.closedRows(trades);
  assert.deepEqual(rows.map(r => r.id), [3, 4]);
  assert.equal(rows[0].net, '+1.988R');
  assert.equal(rows[0].fee, '−0.010R');
  assert.equal(rows[0].tone, 'up');
  assert.equal(rows[1].net, '—');
  assert.match(rows[1].tag, /costs pending: pending: HTTP 503/);
});

test('summary: compares with the backtest and refuses to conclude below 30 trades', () => {
  const s = P.summary({ n: 4, hit: 0.5, hit_ci: [0.15, 0.85], mean_r: 0.25, mean_r_ci: null, cum_r: 1, max_dd_r: 1,
    vs_backtest: { expected_mean_r: 0.136, expected_ci: [0.017, 0.263], expected_n: 422, diff_r: 0.114 }, note: 'n' });
  assert.match(s.verdict, /too few trades.*30–50\+/);
  assert.equal(s.expected, '+0.136R OOS (n=422, CI [+0.017 … +0.263])');
  assert.equal(s.meanCi, 'n/a');
  assert.equal(s.hitCi, '[15.0% … 85.0%]');
  assert.equal(P.summary({ n: 60 }).enough, true);
});

test('equity path spans the zero line and every point', () => {
  const e = P.equityPath([{ cum_r: 2 }, { cum_r: -1 }, { cum_r: 1 }], 100, 50, 0);
  assert.equal(e.min, -1);
  assert.equal(e.max, 2);
  assert.equal(e.d.split(' ').length, 4);
  assert.ok(e.zeroY > 0 && e.zeroY < 50);
  assert.equal(P.equityPath([], 100, 50).d, '');
});

test('render: paper banner, escaped content, empty states', () => {
  const el = { innerHTML: '' };
  P.render(el, { strategy: { id: 's', title: '<b>x</b>', version: 1 }, worker: { enabled: false },
    trades: [Object.assign({}, trades[0], { symbol: '<img>' })], stats: { n: 0, equity: [], per_coin: {}, note: 'few' } });
  assert.match(el.innerHTML, /PAPER TRADING.*not a signal/);
  assert.ok(!el.innerHTML.includes('<img>') && el.innerHTML.includes('&lt;img&gt;'));
  assert.match(el.innerHTML, /off \(set PAPER_JOURNAL=1\)/);
  assert.match(el.innerHTML, /No closed trades yet/);
  assert.match(el.innerHTML, /Seen at start — 0 setups/);
  assert.match(el.innerHTML, /no closed counted trades yet/);
});

test('load: picks the active version and queries trades + stats for it', async () => {
  const seen = [];
  const api = async url => {
    seen.push(url);
    if (url === '/api/paper/strategies') return { strategies: [{ id: 's', version: 1, active: false }, { id: 's', version: 2, active: true }], worker: {} };
    return url.startsWith('/api/paper/trades') ? { trades: [] } : { n: 0 };
  };
  const d = await P.load(api, null);
  assert.equal(d.strategy.version, 2);
  assert.deepEqual(seen.slice(1).sort(), ['/api/paper/stats?strategy=s&version=2', '/api/paper/trades?strategy=s&version=2']);
});
