'use strict';
// Wyckoff + Signals dashboard view-models, HTML and the chart overlay model/draw.
const test = require('node:test');
const assert = require('node:assert/strict');
const W = require('../static/analysis-wyckoff.js');
const O = require('../static/indicators/wyckoff.js');

const ACC = { status: 'structure', schematic: 'accumulation', phase: 'C', phase_label: 'Phase C — Spring tested the lows; supply looks exhausted',
  confidence: 0.74, confidence_label: 'high', delta_used: false, bars: 300, source: 'wyckoff.py over the chart\u2019s closed candles', note: 'deterministic rules',
  tr: { support: 74, resistance: 84.6, height: 10.6, start_ts: 1700216000000, end_ts: null, bars: 70 }, creek: 84.6, ice: null,
  events: [
    { event: 'SC', label: 'Selling climax', ts: 1700216000000, known_at: 1700219600000, price: 74, rule: 'rv 4.2 ≥ 2.0', confidence: 1, volume_rel: 4.2, spread_atr: 5.71, close_pos: 0.48 },
    { event: 'AR', label: 'Automatic rally', ts: 1700244800000, known_at: 1700262800000, price: 84.6, rule: 'highest high after the climax', confidence: 1, volume_rel: 1.6, spread_atr: 1.1, close_pos: 0.8 },
    { event: 'Spring', label: 'Spring (shakeout below support)', ts: 1700435600000, known_at: 1700439200000, price: 72.6, rule: 'low below support <script>', confidence: 0.7, volume_rel: 1.4, spread_atr: 2, close_pos: 0.6, delta_ratio: -0.12 }],
  confirm: 'a SOS: close above the creek 84.6 on rising volume', invalidate: 'closes below 74 that do not reclaim it' };

test('wyckoffView: title, TR, creek, events with rule/conf/delta; unclear says so', () => {
  const v = W.wyckoffView(ACC, 1);
  assert.equal(v.title, 'Accumulation · Phase C — Spring tested the lows; supply looks exhausted');
  assert.equal(v.support, '74.0'); assert.equal(v.resistance, '84.6'); assert.equal(v.creek, '84.6'); assert.equal(v.trTo, 'ongoing');
  assert.deepEqual(v.events.map(e => e.code), ['SC', 'AR', 'Spring']);
  assert.equal(v.events[2].delta, '-12%'); assert.equal(v.events[0].vol, '4.2×'); assert.equal(v.events[0].delta, '—');
  assert.equal(v.confidence, 'high (0.74)');
  const u = W.wyckoffView({ status: 'unclear', schematic: null, events: [], reason: 'no selling or buying climax … trending or no clear structure' });
  assert.equal(u.structure, false); assert.equal(u.title, 'No clear structure / trending'); assert.equal(u.support, '—');
});

test('wyckoffHtml escapes rule text and shows confirm/invalidate and the overlay toggle', () => {
  const html = W.wyckoffHtml({ wyckoff: ACC, origin: 'live' }, { precision: 1, overlay: true });
  assert.match(html, /data-act="wyckoff-overlay" checked/);
  assert.match(html, /Confirms:<\/b> a SOS: close above the creek 84\.6/);
  assert.match(html, /Invalidates:<\/b> closes below 74/);
  assert.ok(html.includes('&lt;script&gt;') && !html.includes('<script>'));
  assert.match(W.wyckoffHtml({ wyckoff: null, error: 'HTTP 502' }, {}), /— HTTP 502/);
});

test('signalsView: entry/stop/target and OOS stats per event, no-edge banner', () => {
  const block = { edge: 'none proven', verdict: 'no candidate passed the out-of-sample test', rule: 'stop 1 ATR, target 1.5 ATR', stats_timeframe: '1h',
    events: [{ signal: 'sweep_reclaim', params: { N: 20 }, direction: 'long', ts: 1700216000000, bars_ago: 3, entry: 100, stop: 99, target: 101.5,
      stats: { n: 1217, hit_rate_decided: 0.4957, hit_ci95_decided: [0.467, 0.524], expectancy_r: -0.1318, expectancy_ci: [-0.2007, -0.0628] } }],
    volume_profile: { bars: 120, poc: 100.2, vah: 101, val: 99.5, hvn: [100.2], lvn: [], price_vs_value: 'inside' },
    session_vwap: { vwap: 100.1, sigma: 0.4, upper_2s: 100.9, lower_2s: 99.3, z: -0.25, bars_into_session: 9 },
    unavailable: { cvd_divergence: 'needs per-bar taker delta' } };
  const v = W.signalsView(block, 1);
  assert.equal(v.events[0].name, 'Sweep & reclaim');
  assert.equal(v.events[0].stats, 'n 1217 · ±1 ATR hit 49.6% (46.7%–52.4%) · E −0.13R [-0.20, -0.06] net'.replace('−', '-'));
  assert.equal(v.events[0].negative, true);
  assert.equal(v.vwap.z, '-0.25σ'); assert.equal(v.profile.poc, '100.2');
  const html = W.signalsHtml({ signals: block }, { precision: 1 });
  assert.match(html, /Signals · no proven edge/); assert.match(html, /⚠ no candidate passed/);
  assert.match(html, /CVD divergence/); assert.match(html, /class="num down">99\.0/);
});

test('pickBlocks prefers live structure for the active key, then the matching snapshot', () => {
  const live = { key: 'binance:BTCUSDT:60', body: { wyckoff: ACC, signals: null, missing: {} } };
  assert.equal(W.pickBlocks(live, null, 'binance:BTCUSDT:60', '').origin, 'live');
  const result = { snapshot: { wyckoff: ACC, signals: null, missing: {} } };
  assert.equal(W.pickBlocks(live, result, 'binance:ETHUSDT:60', 'binance:ETHUSDT:60').origin, 'snapshot');
  assert.equal(W.pickBlocks(null, result, 'binance:ETHUSDT:60', 'binance:BTCUSDT:60').origin, 'none');
});

test('overlay model: TR box, creek line, markers below lows / above highs; mirrored for distribution', () => {
  const m = O.overlayModel(ACC, 'binance:BTCUSDT:60');
  assert.equal(m.start, 1700216000); assert.equal(m.end, null); assert.equal(m.top, 84.6); assert.equal(m.bottom, 74);
  assert.equal(m.edge, 84.6); assert.equal(m.edgeLabel, 'creek');
  assert.deepEqual(m.marks.map(x => [x.label, x.below]), [['SC', true], ['AR', false], ['Spring', true]]);
  const d = O.overlayModel({ ...ACC, schematic: 'distribution', creek: null, ice: 74, events: [{ event: 'BC', ts: 1, price: 2 }, { event: 'AR', ts: 2, price: 1 }] }, 'k');
  assert.equal(d.edgeLabel, 'ice'); assert.deepEqual(d.marks.map(x => x.below), [false, true]);
  assert.equal(O.overlayModel({ status: 'unclear', events: [], tr: null }, 'k'), null);
  const calls = [];
  const ctx = new Proxy({}, { get: (t, k) => (k in t ? t[k] : (...a) => calls.push([k, ...a])), set: (t, k, v) => { t[k] = v; return true; } });
  const n = O.draw(ctx, { x: s => (s - 1700216000) / 3600 * 5, xc: s => (s - 1700216000) / 3600 * 5 + 2.5, y: p => 500 - p * 5, plotWidth: 800 }, m);
  assert.equal(n, 2 + 3);
  assert.ok(calls.some(c => c[0] === 'fillText' && /phase C/.test(c[1])));
});
