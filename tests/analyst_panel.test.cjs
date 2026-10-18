'use strict';
// Hermes analyst panel: pure model + controller flow with a tiny fake DOM (no browser).
const assert = require('node:assert/strict');
const test = require('node:test');
const A = require('../static/analyst-panel.js');

const RESULT = {
  status: 'ok', source: 'hermes', model: 'hermes-agent', latency_ms: 23353, snapshot_ms: 14000, cached: false,
  price: { value: 100 }, freshness: { last_close_ts: 1000, missing: { footprint: 'n/a' } },
  warnings: ['levels[3]: dropped, unknown source_fields'],
  analysis: { bias: 'short', agent_bias: 'long', long_pct: 38, short_pct: 62, confidence: 'low', horizon: '4-12 bars of 15m',
    levels: [{ type: 'support', price: 95, reason: '<b>x</b>', source_fields: ['fvg.nearest.0.top'] },
      { type: 'resistance', price: 105, reason: 'r', source_fields: ['a'], unanchored: true }],
    scenarios: [{ name: 's', trigger: 't', target: 'g', invalidation: 'i' }],
    factor_scores: [{ factor: 'RSI <i>high</i>', direction: 'short', strength: 3, source_fields: ['indicators.rsi14'] },
      { factor: 'funding negative', direction: 'long', strength: 1, source_fields: ['funding.predicted.rate'] }],
    split: { long_pct: 38, short_pct: 62, p_long: 37.6, bias: 'short', net: -0.1678, agent_net: -0.3333, baseline_net: 0.14,
      agent_long_points: 1, agent_short_points: 3, weights: { agent: 0.65, baseline: 0.35 }, k: 3, neutral_band: 5, method: 'm' },
    disagreement: { agent_bias: 'long', computed_bias: 'short', severity: 'opposite', text: 't' },
    risks: ['r1'], summary_ru: 'Шорт' }
};

test('requestFor rejects seconds charts and unknown venues', () => {
  assert.match(A.requestFor({ exchange: 'binance', symbol: 'BTCUSDT', interval: '5s' }).error, /Seconds/);
  assert.ok(A.requestFor({ exchange: 'kraken', symbol: 'X', interval: '15' }).error);
  assert.ok(A.requestFor(null).error);
  assert.deepEqual(A.requestFor({ exchange: 'bybit', symbol: 'ETHUSDT', interval: '60', id: 2 }).body,
    { exchange: 'bybit', symbol: 'ETHUSDT', interval: '60' });
});

test('gauge is honest about calibration and baseline', () => {
  const g = A.gauge(RESULT, null);
  assert.equal(g.long + g.short, 100);
  assert.equal(g.calibrated, false);
  assert.match(g.caption, /not a calibrated probability/);
  assert.match(g.caption, /2 scored factors \+ baseline/);
  assert.equal(A.gauge(RESULT, { overall: { n: 25 } }).calibrated, true);
  assert.match(A.gauge({ ...RESULT, source: 'baseline' }, null).caption, /baseline/);
  assert.equal(A.gauge({ ...RESULT, analysis: { ...RESULT.analysis, long_pct: 140 } }).short, 0);
  assert.equal(A.gauge(null), null);
});

test('factorChips colour each factor by direction with its strength and sources', () => {
  const chips = A.factorChips(RESULT);
  assert.deepEqual(chips.map(c => [c.cls, c.sign + c.strength, c.funding]), [['is-short', '−3', false], ['is-long', '+1', true]]);
  assert.deepEqual(chips[0].sources, ['indicators.rsi14']);
  assert.deepEqual(A.factorChips({ analysis: {} }), []);
  assert.deepEqual(A.factorChips(null), []);
});

test('splitLine shows the traceable maths and the disagreement warning', () => {
  const line = A.splitLine(RESULT);
  assert.equal(line, 'factors −0.33 (L 1 · S 3) ×0.65 · baseline +0.14 ×0.35 → net −0.17 → p_long 37.6 (k 3, neutral ±5)');
  const base = { analysis: { split: { ...RESULT.analysis.split, agent_net: null, weights: { agent: 0, baseline: 0.35 } } } };
  assert.doesNotMatch(A.splitLine(base), /factors/);
  assert.equal(A.splitLine({ analysis: {} }), '');
  assert.match(A.disagreementText(RESULT), /^Disagreement: agent says long, scored evidence computes short/);
  const partial = { analysis: { disagreement: { ...RESULT.analysis.disagreement, severity: 'neutral_mismatch', computed_bias: 'neutral' } } };
  assert.match(A.disagreementText(partial), /^Partial disagreement/);
  assert.equal(A.disagreementText({ analysis: { disagreement: null } }), '');
});

test('levelRows sorts high to low with signed distance', () => {
  const rows = A.levelRows(RESULT);
  assert.deepEqual(rows.map(r => r.price), [105, 95]);
  assert.equal(rows[0].distPct, 5);
  assert.equal(rows[1].distPct, -5);
  assert.equal(rows[0].unanchored, true);
  assert.equal(rows[1].index, 0);
});

test('freshness, statsLine and status text', () => {
  const text = A.freshness(RESULT, 61000);
  assert.match(text, /last closed bar 1m ago/);
  assert.match(text, /hermes-agent 23\.4 s/);
  assert.match(text, /missing: footprint/);
  assert.match(A.statsLine(null), /no data/);
  assert.match(A.statsLine({ total: 3, pending: 2, overall: { n: 0 } }), /3 logged, 2 pending/);
  assert.match(A.statsLine({ total: 7, pending: 7, overall: { n: 0 }, split: { n: 7, p_long_min: 31.2, p_long_max: 68.4, distinct_long_pct: 6, hermes_runs: 6, hermes_runs_citing_funding: 4 } }),
    /p_long 31\.2–68\.4 over 7 \(6 distinct\) · funding cited 4\/6$/);
  assert.match(A.statsLine({ overall: { n: 4, hit_rate: 0.5, win: 2, loss: 2, timeout: 0, ambiguous: 0 } }), /hit rate 50% over n=4.*small sample/);
  assert.match(A.statusText({ status: 'hermes_unavailable' }), /baseline/);
});

test('autoDue enforces the 2-minute minimum, visibility and no overlap', () => {
  assert.equal(A.autoDue(false, true, NaN, 0, false), false);
  assert.equal(A.autoDue(true, true, NaN, 0, false), true);
  assert.equal(A.autoDue(true, true, 0, 119999, false), false);
  assert.equal(A.autoDue(true, true, 0, 120000, false), true);
  assert.equal(A.autoDue(true, false, 0, 999999, false), false);
  assert.equal(A.autoDue(true, true, 0, 999999, true), false);
});

test('toStoreLevel produces a valid shared-levels input', () => {
  const L = require('../static/levels.js');
  const input = A.toStoreLevel({ type: 'support', price: 81226.70000001 }, { exchange: 'binance', symbol: 'BTCUSDT' }, 1);
  assert.deepEqual(input, { exchange: 'binance', symbol: 'BTCUSDT', price: '81226.7', kind: 'simple', source: 'chart', color: '#00c087', note: 'hermes' });
  const store = L.createStore({ storage: L.memoryStorage() });
  assert.equal(store.add(input).note, 'hermes');
  assert.equal(A.toStoreLevel({ type: 'poi', price: 3 }, { exchange: 'bybit', symbol: 'X' }, 0).price, '3');
  assert.equal(A.toStoreLevel({ type: 'poi', price: -1 }, { exchange: 'bybit', symbol: 'X' }), null);
});

test('esc neutralises model text', () => {
  assert.equal(A.esc('<img onerror="x">'), '&lt;img onerror=&quot;x&quot;&gt;');
});

// ── controller with a fake DOM ──────────────────────────────────────────────
function fakeEl() {
  const roles = {}, listeners = {};
  const node = () => ({ textContent: '', innerHTML: '', disabled: false, checked: false, classList: { toggle() {} },
    addEventListener(type, fn) { this[`on${type}`] = fn; } });
  return {
    roles, listeners,
    set innerHTML(html) { for (const m of html.matchAll(/data-role="(\w+)"/g)) roles[m[1]] = roles[m[1]] || node(); },
    querySelector(sel) { return roles[/data-role="(\w+)"/.exec(sel)[1]]; },
    addEventListener(type, fn) { listeners[type] = fn; }
  };
}

test('controller posts the active pane, draws lines, renders the compact summary and keeps a stale result on symbol change', async () => {
  const calls = [], drawn = [], cleared = [], seen = [];
  let active = { id: 1, exchange: 'binance', symbol: 'BTCUSDT', interval: '15', precision: 1 };
  const fetch = async (url, options) => {
    calls.push([url, options && JSON.parse(options.body)]);
    if (url.startsWith('/api/agent/stats')) return { ok: true, json: async () => ({ total: 1, pending: 1, overall: { n: 0 } }) };
    return { ok: true, status: 200, json: async () => RESULT };
  };
  const el = fakeEl();
  const ctl = A.mount(el, { fetch, getActive: () => active, drawLines: (id, rows) => drawn.push([id, rows.length]),
    clearLines: id => cleared.push(id), centerOn() {}, levels: null, visible: () => true, now: () => 61000 });
  const off = ctl.subscribe(v => seen.push(v));
  try {
    await ctl.run(true);
    const post = calls.find(c => c[0] === '/api/agent/analysis');
    assert.deepEqual(post[1], { exchange: 'binance', symbol: 'BTCUSDT', interval: '15', force: true });
    assert.deepEqual(drawn.at(-1), [1, 2]);
    // Sidebar shows only the split + verdict; the full read is the dashboard's job.
    assert.match(el.roles.summary.innerHTML, /▲ 38%/);
    assert.match(el.roles.summary.innerHTML, /Short · /);
    assert.doesNotMatch(el.roles.summary.innerHTML, /analyst-chip|RSI/);
    assert.ok(el.roles.open, 'Open full analysis link exists');
    assert.equal(seen.at(-1).stale, false);
    active = { ...active, symbol: 'ETHUSDT' };
    ctl.activeChanged();
    assert.ok(cleared.includes(1));
    assert.equal(ctl._state.result, RESULT, 'previous analysis is kept');
    assert.equal(ctl.view().stale, true);
    assert.equal(ctl.view().resultKey, 'binance:BTCUSDT:15');
    assert.match(el.roles.status.textContent, /BTCUSDT 15m — re-analyze/);
    assert.match(el.roles.summary.innerHTML, /is-stale/);
    assert.equal(ctl.addLevel(0), false, 'cannot save a stale level onto another symbol');
  } finally { off(); ctl.destroy(); }
  assert.equal(ctl.listenerCount(), 0);
});

test('controller shows rate-limit and seconds-chart errors without posting', async () => {
  const el = fakeEl();
  let active = { id: 0, exchange: 'binance', symbol: 'BTCUSDT', interval: '5s' };
  const posts = [];
  const fetch = async url => {
    if (url === '/api/agent/analysis') { posts.push(url); return { ok: false, status: 429, json: async () => ({ retry_after_s: 33 }) }; }
    return { ok: false, json: async () => ({}) };
  };
  const ctl = A.mount(el, { fetch, getActive: () => active, drawLines() {}, clearLines() {}, centerOn() {}, visible: () => true });
  try {
    await ctl.run(true);
    assert.equal(posts.length, 0);
    assert.match(el.roles.status.textContent, /Seconds/);
    active = { ...active, interval: '15' };
    ctl.activeChanged();
    await ctl.run(true);
    assert.match(el.roles.status.textContent, /retry in 33s/);
  } finally { ctl.destroy(); }
});

test('positioningTarget maps the active pane to venue/symbol/period, rejecting what positioning cannot serve', () => {
  assert.deepEqual(A.positioningTarget({ exchange: 'binance', symbol: 'BTCUSDT', interval: '15' }),
    { exchange: 'binance', symbol: 'BTCUSDT', period: '15m', coin: 'BTC', key: 'binance:BTCUSDT:15m' });
  assert.equal(A.positioningTarget({ exchange: 'bybit', symbol: '1000PEPEUSDT', interval: '240' }).period, '4h');
  assert.equal(A.positioningTarget({ exchange: 'bybit', symbol: '1000PEPEUSDT', interval: '240' }).coin, 'PEPE');
  assert.equal(A.positioningTarget({ exchange: 'hyperliquid', symbol: 'BTC', interval: 'D' }).coin, 'BTC');
  assert.match(A.positioningTarget({ exchange: 'binance', symbol: 'BTCUSDT', interval: '5s' }).error, /No positioning period/);
  assert.match(A.positioningTarget({ exchange: 'hyperliquid', symbol: 'xyz:GOLD', interval: '15' }).error, /no data/);
  assert.ok(A.positioningTarget(null).error);
});

test('sidebar card no longer duplicates positioning, funding or factor detail', () => {
  const el = fakeEl();
  const ctl = A.mount(el, { fetch: async () => ({ ok: false, json: async () => ({}) }), getActive: () => null,
    drawLines() {}, clearLines() {}, centerOn() {}, visible: () => true });
  try {
    assert.deepEqual(Object.keys(el.roles).sort(), ['open', 'run', 'status', 'summary', 'target']);
  } finally { ctl.destroy(); }
});

test('workspace loads positioning-panel.js before the analyst card', () => {
  const html = require('node:fs').readFileSync(require('node:path').join(__dirname, '../templates/workspace.html'), 'utf8');
  const p = html.indexOf('/static/positioning-panel.js'), a = html.indexOf('/static/analyst-panel.js');
  assert.ok(p > 0 && a > p);
});
