'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const PP = require(path.join(__dirname, '../static/positioning-panel.js'));

// Built by positioning_snapshot.build_snapshot from the recorded live Binance BTCUSDT responses.
const snap = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/positioning/snapshot_binance_BTCUSDT.json'), 'utf8'));
const NOW = 1792259000000;

test('view-model: top traders vs crowd bars carry raw values, venue and period', () => {
  const vm = PP.viewModel(snap, null, NOW);
  assert.equal(vm.title, 'BTCUSDT · binance · 1h');
  const [pos, acc, crowd] = vm.topVsCrowd;
  assert.equal(pos.share, 0.6341);
  assert.equal(pos.text, '63.4% long · L/S 1.7331');
  assert.match(pos.meta, /^binance · 1h · \d\d:\d\d UTC$/);
  assert.equal(acc.share, 0.6437);
  assert.equal(crowd.share, 0.6268);
});

test('view-model: taker share = buy / (buy + sell) and OI change directions', () => {
  const vm = PP.viewModel(snap, null, NOW);
  assert.equal(vm.taker.ratio, '0.7939');
  assert.ok(Math.abs(vm.taker.share - 1175.501 / (1175.501 + 1480.574)) < 1e-12);
  const four = vm.oi.changes.find(c => c.window === '4h');
  assert.deepEqual([four.pct, four.price, four.dir], ['-0.15%', '-0.32%', -1]);
  assert.equal(vm.oi.value, '94231.042');
});

test('view-model: liquidations are labelled lower bound with coverage', () => {
  const L = PP.viewModel(snap, null, NOW).liquidations;
  assert.equal(L.available, true);
  assert.equal(L.lowerBound, true);
  assert.match(L.label, /LOWER BOUND/);
  assert.deepEqual(L.rows[0], { window: '1h', long: '$40.5K', short: '$8.2K', longCount: 1, shortCount: 1,
    share: 40500 / 48650, complete: true, coverage: 'full window' });
  assert.equal(L.rows[1].coverage, 'covered 120 of 240 min');
  assert.equal(L.largest[0].usd, '$40.5K');
});

test('view-model: whales aggregate, honest label and recent changes', () => {
  const W = PP.viewModel(snap, null, NOW).whales;
  assert.match(W.label, /^Hyperliquid top-50 accounts by account value/);
  assert.equal(W.long, '$1.01M');
  assert.equal(W.share, 1);
  assert.equal(W.longCount, 1);
  assert.equal(W.changes[0].kind, 'increased');
  assert.equal(W.changes[0].who, '0xf5d8…ad53');
  assert.equal(W.poll, 'poll #3 · 50/50 accounts · 52 s · 30s ago');
});

test('view-model: missing data shows reasons, never invented numbers', () => {
  const bybitLike = { ...snap, exchange: 'bybit', top_traders: { position: { value: null, reason: 'venue publishes no top-trader ratio (only Binance does)' } },
    taker: { value: null, reason: 'Bybit has no public taker buy/sell ratio endpoint' },
    liquidations: { venue: 'hyperliquid', value: null, reason: 'Hyperliquid has no public liquidation stream' }, whales: { aggregate: null, reason: 'whale tracker warming up' } };
  const vm = PP.viewModel(bybitLike, null, NOW);
  assert.equal(vm.topVsCrowd[0].share, null);
  assert.match(vm.topVsCrowd[0].reason, /only Binance/);
  assert.equal(vm.topVsCrowd[1].reason, 'no data');
  assert.equal(vm.taker.available, false);
  assert.equal(vm.liquidations.reason, 'Hyperliquid has no public liquidation stream');
  assert.equal(vm.whales.available, false);
  assert.equal(vm.flows.reason, 'requires CoinGlass API key (paid)');
  assert.deepEqual(PP.viewModel({ error: 'unsupported exchange' }), { error: 'unsupported exchange' });
});

test('view-model: CoinGlass flows when available; renderer escapes and includes every section', () => {
  const withFlows = { ...snap, exchange_flows: { available: true, value: '1144.48008', unit: 'BTC', label: 'net', totals: {},
    exchanges: [{ exchange: '<b>Coinbase</b>', change_1d: '638.797302', change_7d: '799.967408' }] } };
  const vm = PP.viewModel(withFlows, null, NOW);
  assert.equal(vm.flows.value, '+1144.48 BTC');
  const html = PP.renderHtml({ ...vm, title: '<script>x</script>' });
  for (const s of ['Top traders vs crowd', 'Taker flow', 'Open interest', 'Liquidations (lower bound)', 'HL whales', 'Exchange flows (on-chain)'])
    assert.ok(html.includes(s), s);
  assert.ok(!html.includes('<script>x'));
  assert.ok(html.includes('not trade signals'));
});

test('formatters', () => {
  assert.equal(PP.fmtUsd('7659141348.73'), '$7.66B');
  assert.equal(PP.fmtUsd(-1500), '−$1.5K');
  assert.equal(PP.fmtUsd(null), '—');
  assert.equal(PP.fmtSigned('0.5', 2, '%'), '+0.50%');
  assert.equal(PP.fmtPct('0.6341'), '63.4%');
  assert.equal(PP.fmtPx('148956.7885880246'), '148957');
  assert.equal(PP.fmtPx('2404.44'), '2404.44');
  assert.equal(PP.fmtPx('0.06303'), '0.0630300');
  assert.equal(PP.fmtPx(null), '—');
});
