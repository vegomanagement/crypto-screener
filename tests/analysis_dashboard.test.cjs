'use strict';
// Analysis dashboard: pure view-model, metric cards, renderer and controller lifecycle.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const M = require('../static/analysis-dashboard-model.js');
const FL = require('../static/analysis-metrics-flow.js');
const R = require('../static/analysis-dashboard-render.js');
const D = require('../static/analysis-dashboard.js');

// /api/agent/analysis response built by tests/fixtures/analysis_dashboard/make_fixture.py (test providers, no network).
const BASE = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/analysis_dashboard/analysis_fixture.json'), 'utf8'));
const POS = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/positioning/snapshot_binance_BTCUSDT.json'), 'utf8'));
const NOW = BASE.generated_at + 30000;
const clone = o => JSON.parse(JSON.stringify(o));
function result(over) {
  const r = clone(BASE);
  r.snapshot.indicators.atr14 = 2;
  r.price.value = 100; r.snapshot.price.value = 100;
  r.analysis.levels = [
    { type: 'resistance', price: 110, reason: 'far above', source_fields: ['indicators.ema28'] },
    { type: 'support', price: 99, reason: 'near below <b>x</b>', source_fields: ['indicators.close'] },
    { type: 'target', price: 103, reason: 'mid', source_fields: [] },
    { type: 'stop', price: 95, reason: 'stop', source_fields: ['fvg.nearest.0.top'] }];
  r.analysis.factor_scores = [
    { factor: 'RSI weak', direction: 'long', strength: 1, source_fields: ['indicators.rsi14'] },
    { factor: 'EMA stack up', direction: 'long', strength: 3, source_fields: ['indicators.ema_stack_7_14_28', 'indicators.ema7'] },
    { factor: 'Crowd long', direction: 'short', strength: 2, source_fields: ['positioning.crowd.long_share'] },
    { factor: 'Funding hot', direction: 'short', strength: 3, source_fields: ['funding.predicted.rate'] },
    { factor: 'Book bid', direction: 'long', strength: 3, source_fields: ['orderbook.imbalance'] }];
  return Object.assign(r, over || {});
}

test('levels: sorted by distance from the analysed price, with % and ATR distance', () => {
  const rows = M.levelTable(result(), NOW, 2);
  assert.deepEqual(rows.map(r => r.price), [99, 103, 95, 110]);
  assert.equal(rows[0].distPctText, '−1.00%');
  assert.equal(rows[0].distAtrText, '−0.50 ATR');
  assert.equal(rows[1].arrow, '▲');
  assert.equal(rows[0].side, 'below');
  const noAtr = result(); noAtr.snapshot.indicators.atr14 = null;
  assert.equal(M.levelTable(noAtr, NOW, 2)[0].distAtrText, '— (no ATR)');
});

test('factors: long vs short columns sorted by strength 3→1, grouped, tags carry live values or reasons', () => {
  const c = M.factorColumns(result(), NOW);
  assert.deepEqual(c.long.map(f => f.text), ['EMA stack up', 'Book bid', 'RSI weak']);
  assert.deepEqual(c.short.map(f => f.text), ['Funding hot', 'Crowd long']);
  assert.deepEqual([c.longPoints, c.shortPoints], [7, 5]);
  assert.deepEqual(c.long.map(f => f.group), ['Trend', 'Liquidity', 'Momentum']);
  assert.deepEqual(c.short.map(f => f.group), ['Funding', 'Positioning']);
  const rsi = c.long[2].tags[0];
  assert.equal(rsi.value, String(+BASE.snapshot.indicators.rsi14.toPrecision(6)));
  assert.match(rsi.hint, /indicators\.rsi14 = .* · .* (ago|age unknown)/);
  assert.equal(M.groupOfPath('flow.fear_greed.value'), 'Sentiment');
  assert.equal(M.groupOfPath('footprint.bars.5.delta'), 'Volume');
  assert.equal(M.groupOfPath('positioning.liquidations.windows.1h'), 'Liquidity');
  const missing = M.metricTag({ missing: { footprint: 'footprint is only built for 1/5/15m' } }, 'footprint.bars.0.delta', NOW);
  assert.equal(missing.value, '—');
  assert.match(missing.hint, /only built for/);
});

test('verdict: word, one-sentence maths, calibration, disagreement and meta', () => {
  const r = result();
  const v = M.verdictModel(r, { total: 3, pending: 3, overall: { n: 0 } });
  assert.equal(v.word, r.analysis.bias === 'long' ? 'Long' : r.analysis.bias === 'short' ? 'Short' : 'Neutral');
  assert.equal(v.long + v.short, 100);
  assert.match(v.sentence, /^The split blends Hermes' scored factors \(\d+ long vs \d+ short points, net [+−]?\d\.\d\d, weight 65%\) with the deterministic confluence baseline .* weight 35%\) into net .*% long; within ±5 points of 50 reads Neutral\.$/);
  assert.equal(v.calibrated, false);
  assert.match(v.calibrationNote, /not a calibrated probability/);
  const base = result({ source: 'baseline', status: 'hermes_unavailable' });
  base.analysis.split = { ...base.analysis.split, agent_net: null, weights: { agent: 0, baseline: 1 } };
  assert.match(M.verdictModel(base, null).sentence, /^Hermes did not answer/);
  assert.equal(M.A.verdictWord('neutral'), 'Neutral');
  assert.equal(M.verdictModel(null, null), null);
});

test('scenarios: bull / base / bear ordering from targets vs price and the bias', () => {
  const r = result();
  r.analysis.bias = 'long';
  r.analysis.scenarios = [{ name: 'Dump', target: '90 then 85', trigger: 't', invalidation: 'i' },
    { name: 'Squeeze', target: '120', trigger: 't', invalidation: 'i' }, { name: 'Grind', target: '105 EQH', trigger: 't', invalidation: 'i' }];
  const cards = M.scenarioCards(r);
  // Base = the bias-side scenario with the nearest target; order bull → base → bear.
  assert.deepEqual(cards.map(c => [c.name, c.kind]), [['Squeeze', 'bull'], ['Grind', 'base'], ['Dump', 'bear']]);
  assert.equal(cards.find(c => c.kind === 'bear').arrow, '▼');
});

test('stale analysis: greyed label and header until the new chart is analysed', () => {
  const view = { result: result(), resultKey: 'binance:BTCUSDT:15', activeKey: 'bybit:ETHUSDT:60', active: { exchange: 'bybit', symbol: 'ETHUSDT', interval: '60' } };
  assert.deepEqual(M.staleState(view), { stale: true, label: 'for BTCUSDT 15m — re-analyze' });
  const h = M.headerModel(view, NOW);
  assert.equal(h.target, 'Bybit · ETHUSDT · 1h');
  assert.match(h.fresh, /showing BTCUSDT 15m — re-analyze/);
  const html = R.verdictHtml({ ...view, stale: true, added: {} }, NOW);
  assert.match(html, /ad-verdict is-stale/);
  assert.match(html, /Previous analysis for BTCUSDT 15m — re-analyze/);
  assert.match(R.levelsHtml({ ...view, stale: true, added: {}, now: NOW }, 2), /data-act="center"[^>]*disabled/);
  assert.equal(M.staleState({ ...view, activeKey: view.resultKey }).stale, false);
  assert.equal(M.headerModel({ active: null }, NOW).fresh, 'not analysed yet');
});

test('metric cards: grouped, every number has provenance, missing values show — with the reason', () => {
  const input = { result: result(), stale: false, exchange: 'binance', precision: 2, funding: null, positioning: null, whales: null, now: NOW };
  const cards = FL.metricCards(input);
  assert.deepEqual(cards.map(c => c.title), ['Price & volatility', 'Trend', 'Funding', 'Positioning', 'Liquidations', 'Hyperliquid whales', 'Liquidity & volume', 'Sentiment & session']);
  const price = cards[0].rows.find(r => r.label === 'ATR %');
  assert.equal(price.value, '2.00%');
  assert.match(cards[0].rows[0].hint, /last closed candle close · Binance · /);
  assert.ok(cards[2].rows.every(r => r.missing && /funding not loaded yet/.test(r.reason)));
  assert.ok(cards[3].rows.every(r => r.value === '—' && /positioning not loaded/.test(r.reason)));
  const ema200 = cards[1].rows.find(r => r.label === 'Price vs EMA 200');
  assert.equal(ema200.value, '—');
  assert.equal(ema200.reason, 'fewer than 200 closed bars');
  // Stale: snapshot numbers belong to another chart, so they are withheld with the reason.
  const stale = FL.metricCards({ ...input, stale: true, resultKey: 'binance:BTCUSDT:15' });
  assert.ok(stale[0].rows.every(r => r.missing && /analysis is for BTCUSDT 15m/.test(r.reason)));
  // Funding error and positioning payload.
  const f = FL.X.fundingCard({ ...input, funding: { error: 'upstream', detail: 'HTTP 502' } });
  assert.match(f.rows[0].reason, /HTTP 502/);
  const p = FL.positioningCard({ ...input, positioning: POS });
  assert.equal(p.bars[2].share, 0.6268);
  assert.match(p.bars[0].hint, /topLongShortPositionRatio/);
  assert.equal(p.rows.find(r => r.label === 'OI change 24h').value, '−0.79%');
  assert.equal(p.rows.find(r => r.label === 'OI change 24h').arrow, '▼');
  const liq = FL.liquidationsCard({ ...input, positioning: POS });
  assert.equal(liq.lowerBound, true);
  assert.match(liq.label, /LOWER BOUND/);
  const wh = FL.whalesCard({ ...input, whales: POS.whales });
  assert.equal(wh.available, true);
  assert.equal(wh.longCount, 1);
});

test('renderer: escapes model text and keeps the disclaimer', () => {
  const r = result();
  r.analysis.summary_ru = '<script>alert(1)</script>';
  r.analysis.risks = ['<img src=x onerror=1>'];
  const view = { result: r, stats: null, resultKey: 'k', activeKey: 'k', active: { exchange: 'binance', symbol: 'BTCUSDT', interval: '15' }, added: {}, now: NOW };
  const html = [R.summaryHtml(view), R.risksHtml(view), R.levelsHtml(view, 2)].join('');
  assert.doesNotMatch(html, /<script>|<img|<b>x<\/b>/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /Not financial advice/);
});

test('wheel guard: prevents page scroll only for wheels over a chart and is removable', () => {
  const chart = { closest: sel => (sel === '.chart-stage' ? {} : null) }, outside = { closest: () => null };
  assert.equal(D.isChartWheelTarget(chart), true);
  assert.equal(D.isChartWheelTarget(outside), false);
  assert.equal(D.isChartWheelTarget(null), false);
  const added = [], removed = [];
  const grid = { addEventListener: (t, fn, o) => added.push([t, fn, o]), removeEventListener: (t, fn) => removed.push([t, fn]) };
  const dispose = D.installWheelGuard(grid);
  assert.equal(added[0][0], 'wheel');
  assert.equal(added[0][2].passive, false, 'must be non-passive to cancel scrolling');
  let prevented = 0;
  added[0][1]({ target: chart, cancelable: true, preventDefault: () => { prevented += 1; } });
  added[0][1]({ target: outside, cancelable: true, preventDefault: () => { prevented += 10; } });
  assert.equal(prevented, 1);
  dispose();
  assert.equal(removed[0][1], added[0][1]);
});

