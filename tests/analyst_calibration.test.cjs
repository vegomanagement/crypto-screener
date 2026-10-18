'use strict';
// Calibrated split explanation (cap / conflict rule / flip guard) and EMA ribbon periods that
// follow the chart: request body, analysis key, Trend card (current and legacy snapshot shapes).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const A = require('../static/analyst-panel.js');
const M = require('../static/analysis-dashboard-model.js');
const MT = require('../static/analysis-metrics.js');

// The committed fixture predates the calibration: it doubles as the legacy (stored/cached) shape.
const LEGACY = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/analysis_dashboard/analysis_fixture.json'), 'utf8'));
const clone = o => JSON.parse(JSON.stringify(o));
const CAL = { long_pct: 27, short_pct: 73, p_long: 26.93, bias: 'short', net: -0.3316, agent_net: -0.3684, baseline_net: 0.5,
  baseline_raw: 1, agent_long_points: 6, agent_short_points: 13, weights: { agent: 0.9, baseline: 0 },
  configured_weights: { agent: 0.9, baseline: 0.1 }, baseline_cap: 0.5, conflict: true, conflict_min_agent: 0.15,
  flip_guard: false, k: 3, neutral_band: 5, method: 'm' };
function withSplit(split, over) {
  const r = clone(LEGACY);
  r.analysis.split = { ...split }; r.analysis.bias = split.bias; r.analysis.long_pct = split.long_pct; r.analysis.short_pct = split.short_pct;
  return Object.assign(r, over || {});
}

test('verdict sentence and exact formula explain the cap and the conflict rule (6 vs 13 case)', () => {
  const v = M.verdictModel(withSplit(CAL), null);
  assert.match(v.sentence, /6 long vs 13 short points, net −0\.37, weight 90%/);
  assert.match(v.sentence, /net \+0\.50 after the ±0\.5 cap \(raw \+1\.00\), weight 0%/);
  assert.match(v.sentence, /baseline points the other way, so the baseline is ignored/);
  assert.match(v.sentence, /no edge/);
  assert.equal(v.word, 'Short');
  assert.equal(A.splitLine(withSplit(CAL)),
    'factors −0.37 (L 6 · S 13) ×0.9 · baseline +0.50 (raw +1.00, cap ±0.5) ×0.1→0 (clear tally against it) → net −0.33 → p_long 26.9 (k 3, neutral ±5)');
  const guard = { ...CAL, conflict: false, flip_guard: true, agent_net: 0.0526, net: 0, p_long: 50, long_pct: 50, short_pct: 50, bias: 'neutral',
    weights: { agent: 0.9, baseline: 0.1 }, baseline_net: -0.5, baseline_raw: -0.5 };
  assert.match(M.verdictModel(withSplit(guard), null).sentence, /would have flipped the factors\u2019 direction, so the result is held at 50\/50/);
  assert.match(A.splitLine(withSplit(guard)), /×0\.1 → would flip the factors → net 0 → net \+0\.00 → p_long 50\.0/);
  const fallback = { ...CAL, agent_net: null, conflict: false, weights: { agent: 0, baseline: 0.1 }, net: 0.05, p_long: 53.72, bias: 'neutral' };
  const fv = M.verdictModel(withSplit(fallback, { source: 'baseline', status: 'hermes_unavailable' }), null);
  assert.match(fv.sentence, /^Hermes did not answer, so only the deterministic confluence baseline contributes .*weight 10%.*cannot leave the ±5-point Neutral band\.$/);
});

test('legacy split (no cap fields) keeps the old renormalised sentence', () => {
  assert.match(M.verdictModel(clone(LEGACY), null).sentence, /^The split blends Hermes' scored factors .* weight 65%\)/);
});

test('request body and analysis key carry the pane ribbon periods', () => {
  const active = { exchange: 'binance', symbol: 'BTCUSDT', interval: '15', emaPeriods: [55, 9, 21] };
  assert.deepEqual(A.requestFor(active).body, { exchange: 'binance', symbol: 'BTCUSDT', interval: '15', ema_periods: [9, 21, 55] });
  assert.deepEqual(A.emaPeriods({ emaPeriods: [7, 7.5, 1, 401, 14, '21', 21, 300] }), [7, 14, 21]);
  assert.equal(A.requestFor({ ...active, emaPeriods: [1.5] }).body.ema_periods, undefined);
  assert.equal(A.keyOf(active), 'binance:BTCUSDT:15:ema9-21-55');
  assert.notEqual(A.keyOf(active), A.keyOf({ ...active, emaPeriods: [7, 14, 21] }), 'changing the ribbon makes the shown analysis stale');
  assert.equal(A.keyLabel('binance:BTCUSDT:15:ema9-21-55'), 'BTCUSDT 15m · EMA 9/21/55');
  assert.equal(A.keyLabel('binance:BTCUSDT:15'), 'BTCUSDT 15m');
});

test('Trend card names the snapshot ribbon periods; legacy 7/14/28 snapshots still render', () => {
  const r = clone(LEGACY);
  r.snapshot.indicators = { ...r.snapshot.indicators, ema_periods: [9, 21, 55], ema_fast: 101.5, ema_mid: 100.25, ema_slow: 99, ema_stack: 'bullish' };
  const input = { result: r, stale: false, exchange: 'binance', precision: 2, now: r.generated_at };
  const row = MT.trendCard(input).rows[0];
  assert.equal(row.label, 'EMA ribbon 9/21/55');
  assert.equal(row.value, 'bullish');
  assert.equal(row.sub, [101.5, 100.25, 99].map(v => A.fmtPrice(v, 2)).join(' / '));
  const legacy = MT.trendCard({ ...input, result: clone(LEGACY) }).rows[0];
  assert.equal(legacy.label, 'EMA ribbon 7/14/28');
  const short = clone(r); short.snapshot.indicators = { ...short.snapshot.indicators, ema_slow: null, ema_stack: null };
  const miss = MT.trendCard({ ...input, result: short }).rows[0];
  assert.equal(miss.value, '—');
  assert.match(miss.reason, /fewer than 55 closed bars/);
});
