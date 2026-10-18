'use strict';
// Multi-timeframe section: view-model, HTML (escaped), SMC/HTF overlay model + draw, controller wiring.
const test = require('node:test');
const assert = require('node:assert/strict');
const MT = require('../static/analysis-mtf.js');
const SO = require('../static/indicators/smc.js');
const D = require('../static/analysis-dashboard.js');

const T = 1792000000000, H = 3600000;
const tfRead = (trend, extra = {}) => ({
  price: 81455.9, atr14: 100,
  bias: { bias: trend === 'bull' ? 'bull' : 'bear', score: trend === 'bull' ? 3 : -3 },
  smc: { trend, internal_trend: trend, last_break: { kind: 'CHoCH', dir: trend, level: 81733, ts: T, swing_ts: T - 5 * H },
    breaks: [{ kind: 'CHoCH', dir: trend, level: 81733, ts: T, swing_ts: T - 5 * H }],
    swings: [{ kind: 'H', label: 'LH', price: 82900.5, ts: T - 10 * H }, { kind: 'L', label: 'LL', price: 80751, ts: T - 4 * H }],
    sweeps: [{ side: 'low', level: 80780, price: 80751, ts: T - 4 * H }],
    dealing_range: { high: 82900.5, low: 80751, eq: 81825.75, zone: 'discount', high_ts: T - 10 * H, low_ts: T - 4 * H },
    order_blocks: [{ dir: 'bull', top: 81295, bottom: 80095.9, ts: T - 6 * H, state: 'unmitigated' }], fvgs: [] },
  wyckoff: { status: 'structure', schematic: 'distribution', phase: 'A', tr: { support: 80780, resistance: 82900.5, start_ts: T - 30 * H, end_ts: null } },
  ...extra });
const BODY = { mtf: {
  interval: '15', ladder: ['240', '60', '15'], verdict: 'mixed_counter_trend', dominant_tf: '240', sync_tf: '240', entry_tf: '60',
  tfs: { 240: tfRead('bear'), 60: tfRead('bull'), 15: tfRead('bull', { wyckoff: { status: 'structure', schematic: 'accumulation', phase: 'C', tr: { support: 80780, resistance: 82092.1, start_ts: T - 20 * H } } }) },
  pairs: [{ ltf: '60', htf: '240', status: 'divergence', divergence: true }, { ltf: '15', htf: '60', status: 'aligned', divergence: false }],
  position_vs_htf: { 15: 'at_htf_support' },
  conflicts: [{ id: 'W1', tfs: ['15', '60'], text: '15m accumulation <b>at</b> HTF support of the 1h distribution', resolve: ['1h close above 82900.5 would invalidate the 1h distribution and align with the 15m accumulation'] }],
  aligned: [], resolve: ['1h close above 82900.5 would invalidate the 1h distribution and align with the 15m accumulation'],
  levels: [{ price: 80780, tfs: ['1h', '15m'], kinds: ['1h TR support', '15m TR support'], n_tfs: 2, score: 3.5, side: 'below', distance_atr: -6.76 }],
  setups: [{ pair: '4h→15m', state: 'waiting_for_poi', htf_trend: 'bear', poi: null, direction: null, entry: null, stop: null, target: null, edge: 'no proven edge' }],
  missing: {}, edge: 'descriptive context: no proven out-of-sample edge', source: 'mtf.py' } };

test('mtfView: ladder rows top-down with breaks, zone, POI, divergence, sync/entry markers', () => {
  const v = MT.mtfView(BODY, 1);
  assert.equal(v.verdictLabel, 'Mixed · counter-trend'); assert.equal(v.dominant, '4h'); assert.equal(v.sync, '4h'); assert.equal(v.entry, '1h');
  assert.deepEqual(v.rows.map(r => r.label), ['4h', '1h', '15m']);
  const h1 = v.rows[1];
  assert.equal(h1.lastBreak, 'CHoCH ▲ 81733.0'); assert.equal(h1.zone, 'discount'); assert.equal(h1.divergence, true); assert.equal(h1.pairStatus, 'DIVERGENCE');
  assert.equal(h1.poi, '▲ OB 80095.9–81295.0'); assert.equal(v.rows[0].sync, true); assert.equal(h1.entry, true);
  assert.equal(v.rows[2].position, 'at HTF support'); assert.equal(v.rows[2].wyckoff, 'Acc C · 80780.0–82092.1');
  assert.equal(v.levels[0].dist, '-6.76 ATR'); assert.deepEqual(v.levels[0].tfs, ['1h', '15m']);
  assert.equal(MT.mtfView(null).available, false); assert.equal(MT.mtfView({ error: 'HTTP 502' }).reason, 'HTTP 502');
});

test('mtfHtml: escaped conflicts, resolve triggers, TF badges, toggles, no-edge setups', () => {
  const html = MT.mtfHtml(BODY, { precision: 1, smc: true });
  assert.ok(html.includes('&lt;b&gt;at&lt;/b&gt;') && !html.includes('<b>at</b>'));
  assert.match(html, /1h close above 82900\.5 would invalidate the 1h distribution/);
  assert.match(html, /<span class="ad-tf-badge">1h<\/span><span class="ad-tf-badge">15m<\/span>/);
  assert.match(html, /data-act="smc-overlay" checked/); assert.match(html, /data-act="htf-box"/);
  assert.match(html, /no proven out-of-sample edge/); assert.match(html, /waiting for HTF POI/);
  assert.match(html, /⚡/);
  assert.match(MT.mtfHtml({ error: 'HTTP 502' }, {}), /— HTTP 502/);
});

test('overlay model: pane layer, optional HTF layer and HTF Wyckoff box; draw is bounded', () => {
  assert.equal(SO.overlayModel(BODY, 'binance:BTCUSDT:15', {}), null);
  const m = SO.overlayModel(BODY, 'binance:BTCUSDT:15', { smc: true, htf: true, box: true });
  assert.equal(m.layers.length, 2); assert.equal(m.layers[1].htf, true); assert.equal(m.layers[1].tf, '60');
  assert.equal(m.layers[0].breaks[0].label, 'CHoCH'); assert.equal(m.layers[1].breaks[0].label, '1h CHoCH');
  assert.deepEqual([m.box.tf, m.box.top, m.box.bottom], ['60', 82900.5, 80780]);
  assert.equal(m.layers[0].swings[0].label, 'LH'); assert.equal(m.layers[0].sweeps[0].label, 'sweep');
  const ops = [];
  const ctx = new Proxy({}, { get: (_t, k) => (typeof k === 'string' && /^(fill|stroke|move|line|begin|close|save|restore|set|strokeRect|fillRect|fillText)/.test(k) ? (...a) => ops.push([k, ...a]) : undefined), set: () => true });
  const view = { x: t => (t - (T - 40 * H) / 1000) / 900, xc: t => (t - (T - 40 * H) / 1000) / 900 + 1, y: p => 1000 - (p - 80000) / 3, plotWidth: 400, first: (T - 40 * H) / 1000 };
  const drawn = SO.draw(ctx, view, m);
  assert.ok(drawn >= 8, `drew ${drawn}`);
  assert.ok(ops.some(o => o[0] === 'fillText' && /1h Wyckoff distribution/.test(o[1])));
});

test('controller: fetches /api/agent/mtf for the active pane only while visible, drives the SMC overlay', async () => {
  const pending = [], overlays = [];
  const fetch = url => new Promise(resolve => pending.push({ url, resolve: body => resolve({ ok: true, json: async () => body }) }));
  const active = { id: 0, exchange: 'binance', symbol: 'BTCUSDT', interval: '15', precision: 1, emaPeriods: [7, 14, 21] };
  const analyst = { view: () => ({ active, activeKey: 'binance:BTCUSDT:15', result: null, stats: null, added: {}, stale: false }), subscribe: () => () => {}, run() {} };
  const slots = {};
  const root = { classList: { toggle() {} }, set innerHTML(h) { for (const m of h.matchAll(/data-slot="(\w+)"/g)) slots[m[1]] = ''; },
    querySelector: sel => { const k = /data-slot="(\w+)"/.exec(sel)[1]; return { set innerHTML(v) { slots[k] = v; } }; },
    querySelectorAll: () => [], listeners: {}, addEventListener(t, fn) { this.listeners[t] = fn; }, removeEventListener() {}, contains: () => true };
  let io;
  class IO { constructor(cb) { this.cb = cb; io = this; } observe() {} disconnect() {} }
  const dash = D.mount(root, { analyst, getFunding: () => null, fetch, IntersectionObserver: IO, pageVisible: () => true, setInterval: () => 1, clearInterval() {}, setSmcOverlay: m => overlays.push(m) });
  assert.equal(pending.filter(p => /\/api\/agent\/mtf/.test(p.url)).length, 0, 'off-screen: no MTF request');
  io.cb([{ isIntersecting: true }]);
  const req = pending.filter(p => /\/api\/agent\/mtf/.test(p.url));
  assert.equal(req.length, 1); assert.match(req[0].url, /exchange=binance&symbol=BTCUSDT&interval=15&ema=7%2C14%2C21/);
  req[0].resolve(BODY);
  await new Promise(r => setTimeout(r, 0));
  assert.match(slots.mtf, /Multi-timeframe/); assert.match(slots.mtf, /Mixed · counter-trend/);
  root.listeners.change({ target: { dataset: { act: 'smc-overlay' }, checked: true } });
  assert.equal(overlays.at(-1).key, 'binance:BTCUSDT:15'); assert.equal(overlays.at(-1).layers.length, 1);
  dash.destroy();
  assert.equal(overlays.at(-1), null, 'overlay cleared on destroy');
});
