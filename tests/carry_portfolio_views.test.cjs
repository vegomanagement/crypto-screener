'use strict';
// Portfolio / observer views of the paper journal and the funding-carry panel (pure view models).
const test = require('node:test');
const assert = require('node:assert/strict');
const PF = require('../static/paper-portfolio.js');
const OB = require('../static/paper-observer.js');
const CP = require('../static/carry-panel.js');
const PJ = require('../static/paper-journal.js');

const curve = [
  { ts: 0, ret_pct: 0, btc_pct: 0, band_lo_pct: 0, band_mid_pct: 0, band_hi_pct: 0 },
  { ts: 86400000, ret_pct: 1.5, btc_pct: -2, band_lo_pct: -3, band_mid_pct: 0.1, band_hi_pct: 3.2 },
  { ts: 172800000, ret_pct: -0.5, btc_pct: 4, band_lo_pct: -4, band_mid_pct: 0.2, band_hi_pct: 4.4 }];

test('portfolio summary: returns vs BTC, band, short-sample verdict', () => {
  const s = PF.summary({ n_days: 2, total_return_pct: -0.5, btc_return_pct: 4, curve, in_band: true, max_dd_pct: 2.0,
    expectation: { dev: { cagr: 0.4069, vol: 0.3337, sharpe: 1.192 }, holdout_2026: { total_return: 0.2169, sharpe: 0.918 } } });
  assert.equal(s.ret, '−0.50%');
  assert.equal(s.btc, '+4.00%');
  assert.equal(s.vsBtc, '−4.50%');
  assert.match(s.band, /−4\.0% … \+4\.4%/);
  assert.equal(s.inBand, 'inside the band');
  assert.match(s.verdict, /far too short/);
  assert.match(s.dev, /40\.7%/);
});

test('portfolio chart: polylines and a closed band polygon within the box', () => {
  const g = PF.chart(curve, 640, 180);
  assert.equal(g.strategy.split(' ').length, 3);
  assert.equal(g.band.split(' ').length, 6);
  for (const p of g.btc.split(' ')) {
    const [x, y] = p.split(',').map(Number);
    assert.ok(x >= 0 && x <= 640 && y >= 0 && y <= 180);
  }
  assert.equal(PF.chart([], 640, 180), null);
});

test('portfolio positions split open / closed and orders text', () => {
  const pos = [{ symbol: 'BTCUSDT', side: 'long', entry_ts: 0, entry_px: 100, weight: 0.12, exit_ts: null, why: 'open', pnl: 0.002, funding: -0.0001 },
    { symbol: 'ETHUSDT', side: 'short', entry_ts: 0, entry_px: 10, weight: 0.05, exit_ts: 86400000, exit_px: 9, why: 'time', pnl: 0.004, funding: 0.0002 }];
  assert.equal(PF.positionRows(pos, true).length, 1);
  const closed = PF.positionRows(pos, false)[0];
  assert.equal(closed.pnl, '+0.400%');
  assert.equal(closed.tone, 'up');
  assert.match(PF.ordersText({ orders: { SOLUSDT: { w: -0.1, tag: 'funding' } }, stops: { BTCUSDT: 95 } }), /SOLUSDT → w −?-?0\.100 \(funding\) \| resting stops: BTCUSDT stop 95\.0000/);
  assert.equal(PF.ordersText(null), 'none recorded yet');
});

test('journal tabs: one per strategy id, latest version, selected marked', () => {
  const t = PJ.tabs([{ id: 'a', version: 1, title: 'A · x', kind: 'portfolio' }, { id: 'a', version: 2, title: 'A · y', kind: 'portfolio', active: true },
    { id: 'smc', version: 1, title: 'SMC', active: true }], 'a');
  assert.deepEqual(t.map(x => [x.id, x.selected, x.kind]), [['a', true, 'portfolio'], ['smc', false, 'setup']]);
  assert.match(PJ.tabsHtml(t), /\?strategy=a" class="sel"/);
  assert.equal(PJ.tabsHtml([t[0]]), '');
});

test('journal load dispatches portfolio strategies to /api/paper/portfolio', async () => {
  const calls = [];
  const api = async url => { calls.push(url); return url.startsWith('/api/paper/strategies')
    ? { strategies: [{ id: 'pf', version: 1, kind: 'portfolio', active: true }], worker: {} } : { curve: [] }; };
  const d = await PJ.load(api, 'pf');
  assert.equal(d.kind, 'portfolio');
  assert.deepEqual(calls, ['/api/paper/strategies', '/api/paper/portfolio?strategy=pf&version=1']);
});

test('observer view models', () => {
  const h = OB.horizonRows({ 1: { rows: 3, closed: 2, open: 1, waiting: 0, no_price: 0, mean_net: 0.012, median_net: -0.001, hit: 0.5 } });
  assert.deepEqual([h[0].h, h[0].mean, h[0].median, h[0].hit], ['1 d', '+1.20%', '−0.10%', '50%']);
  const r = OB.tradeRows([{ knowable_ts: 0, address: '0x1234567890abcdef', wallet_rank: 2, coin: 'BTC', side: 'long', event_kind: 'open', horizon_d: 3, entry_ts: 60000, state: 'open', ret_net: null }]);
  assert.equal(r[0].wallet, '#2 0x1234…cdef');
  assert.equal(r[0].net, '—');
});

test('carry rows sort by estimated net APR and show the venue gap', () => {
  const rows = CP.rows({ rows: [
    { symbol: 'A', venues: { binance: { ann: 0.1 }, bybit: { ann: 0.05 }, hyperliquid: null }, ann_30d: 0.1, net_apr_est: 0.01, neg_share_90d: 0.1 },
    { symbol: 'B', venues: { binance: { ann: 0.2 } }, ann_30d: 0.3, net_apr_est: 0.12 },
    { symbol: 'C', venues: {}, net_apr_est: null }] });
  assert.deepEqual(rows.map(r => r.symbol), ['B', 'A', 'C']);
  assert.equal(rows[1].spread, '+5.0% (binance − bybit)');
  assert.equal(rows[1].neg, '10%');
  assert.equal(rows[2].net, '—');
  assert.equal(rows[0].spread, '—');
});
