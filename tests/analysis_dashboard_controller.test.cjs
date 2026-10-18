'use strict';
// Analysis dashboard controller: renders/polls only while on screen, follows the active pane,
// drops late responses, and removes every observer/timer/listener on destroy.
const test = require('node:test');
const assert = require('node:assert/strict');
const D = require('../static/analysis-dashboard.js');

function fakeRoot() {
  const slots = {}, listeners = {};
  return {
    slots, listeners, classList: { toggle() {} },
    set innerHTML(html) { for (const m of html.matchAll(/data-slot="(\w+)"/g)) slots[m[1]] = { innerHTML: '', writes: 0 }; },
    querySelector(sel) {
      const s = slots[/data-slot="(\w+)"/.exec(sel)[1]];
      return { set innerHTML(v) { s.innerHTML = v; s.writes += 1; }, get innerHTML() { return s.innerHTML; } };
    },
    querySelectorAll() { return []; },
    addEventListener(t, fn) { (listeners[t] = listeners[t] || new Set()).add(fn); },
    removeEventListener(t, fn) { listeners[t] && listeners[t].delete(fn); },
    contains: () => true
  };
}
function fakeIO() {
  const made = [];
  class IO {
    constructor(cb, opts) { this.cb = cb; this.opts = opts; this.observed = []; this.disconnected = false; made.push(this); }
    observe(el) { this.observed.push(el); }
    disconnect() { this.disconnected = true; }
    fire(on) { this.cb([{ isIntersecting: on }]); }
  }
  return { IO, made };
}
function fakeTimers() {
  const live = new Map(); let id = 0;
  return { live, setInterval: (fn, ms) => { id += 1; live.set(id, { fn, ms }); return id; }, clearInterval: i => live.delete(i) };
}
function fakeAnalyst(active) {
  const subs = new Set();
  const st = { active, result: null, runs: 0 };
  return { st, subs,
    view: () => ({ active: st.active, activeKey: st.active ? `${st.active.exchange}:${st.active.symbol}:${st.active.interval}` : '', resultKey: '', result: null, stats: null, error: '', busy: false, auto: false, showLines: true, added: {}, stale: false }),
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
    notify() { subs.forEach(fn => fn()); },
    run() { st.runs += 1; }, centerOn() {}, addLevel() {}, setAuto() {}, setLines() {} };
}
const flush = () => new Promise(r => setTimeout(r, 0));

test('off-screen: no render, no fetch, no poll timer; on-screen: render + one poll; off again stops it', async () => {
  const { IO, made } = fakeIO(), timers = fakeTimers(), calls = [];
  const analyst = fakeAnalyst({ id: 0, exchange: 'binance', symbol: 'BTCUSDT', interval: '15', precision: 1 });
  const fetch = async url => { calls.push(url); return { ok: true, json: async () => ({ error: 'x' }) }; };
  const root = fakeRoot();
  const dash = D.mount(root, { analyst, getFunding: () => null, fetch, IntersectionObserver: IO, pageVisible: () => true, now: () => 1e12, ...timers });
  assert.equal(made.length, 1);
  assert.equal(made[0].observed[0], root);
  // The dashboard begins exactly at the fold; edge-adjacent must not count as on screen.
  assert.equal(made[0].opts.rootMargin, '0px 0px -24px 0px');
  analyst.notify(); dash.tick();
  assert.equal(dash.debug().renders, 0);
  assert.equal(calls.length, 0);
  assert.equal(timers.live.size, 0, 'no polling loop while off-screen');
  made[0].fire(true); await flush();
  assert.equal(dash.debug().renders >= 1, true);
  assert.equal(calls.filter(u => u.startsWith('/api/positioning?')).length, 1);
  assert.match(calls[0], /owner=analysis-dashboard/);
  assert.equal(timers.live.size, 1);
  assert.equal([...timers.live.values()][0].ms, D.POLL_MS);
  made[0].fire(false);
  assert.equal(timers.live.size, 0);
  dash.destroy();
});

test('follows the active pane and drops a late positioning response for the previous symbol', async () => {
  const { IO, made } = fakeIO(), timers = fakeTimers(), pending = [];
  const analyst = fakeAnalyst({ id: 0, exchange: 'binance', symbol: 'BTCUSDT', interval: '15' });
  const fetch = url => new Promise(resolve => pending.push({ url, resolve: body => resolve({ ok: true, json: async () => body }) }));
  const root = fakeRoot();
  const dash = D.mount(root, { analyst, getFunding: () => null, fetch, IntersectionObserver: IO, pageVisible: () => true, ...timers });
  made[0].fire(true);
  const btc = pending.filter(p => /BTC/.test(p.url) && !/structure/.test(p.url));
  analyst.st.active = { id: 1, exchange: 'bybit', symbol: 'ETHUSDT', interval: '60' };
  analyst.notify();
  assert.equal(dash._state.positioning, null, 'old data cleared on pane switch');
  const eth = pending.filter(p => /ETH/.test(p.url) && !/structure/.test(p.url));
  assert.equal(eth.length, 2);
  assert.equal(pending.filter(p => /structure\?exchange=bybit&symbol=ETHUSDT&interval=60/.test(p.url)).length, 1, 'structure follows the pane');
  assert.match(eth[0].url, /exchange=bybit&symbol=ETHUSDT&period=1h/);
  eth.forEach(p => p.resolve({ exchange: 'bybit', symbol: 'ETHUSDT', period: '1h', mark: 'eth' }));
  await flush();
  btc.forEach(p => p.resolve({ exchange: 'binance', symbol: 'BTCUSDT', period: '15m', mark: 'btc' }));
  await flush();
  assert.equal(dash._state.positioning.mark, 'eth');
  dash.destroy();
});

test('destroy removes the observer, the poll timer, the analyst subscription and DOM listeners; repeated mounts do not accumulate', async () => {
  const { IO, made } = fakeIO(), timers = fakeTimers();
  const analyst = fakeAnalyst({ id: 0, exchange: 'binance', symbol: 'BTCUSDT', interval: '15' });
  const fetch = async () => ({ ok: true, json: async () => ({}) });
  const root = fakeRoot();
  for (let i = 0; i < 20; i += 1) {
    const dash = D.mount(root, { analyst, getFunding: () => null, fetch, IntersectionObserver: IO, pageVisible: () => true, ...timers });
    made.at(-1).fire(true); await flush();
    dash.destroy();
    dash.destroy(); // idempotent
  }
  assert.ok(made.every(io => io.disconnected));
  assert.equal(timers.live.size, 0);
  assert.equal(analyst.subs.size, 0);
  assert.equal(root.listeners.click.size, 0);
  assert.equal(root.listeners.change.size, 0);
});

test('Analyze button routes to the shared analyst controller; tick re-renders when the pane funding cache changes', async () => {
  const timers = fakeTimers();
  const analyst = fakeAnalyst({ id: 0, exchange: 'hyperliquid', symbol: 'BTC', interval: '15' });
  let funding = null;
  const root = fakeRoot();
  const dash = D.mount(root, { analyst, getFunding: () => funding, fetch: async () => ({ ok: true, json: async () => ({}) }), IntersectionObserver: null, pageVisible: () => true, ...timers });
  const click = [...root.listeners.click][0];
  click({ target: { closest: () => ({ dataset: { act: 'run' }, tagName: 'BUTTON' }) } });
  assert.equal(analyst.st.runs, 1);
  const before = root.slots.metrics.writes;
  dash.tick();
  assert.equal(root.slots.metrics.writes, before, 'unchanged cache → metrics not rewritten');
  funding = { exchange: 'hyperliquid', symbol: 'BTC', as_of: 1, last_settled: { rate: '0.0001' }, predicted: { rate: '0.0002', settles_at: 5e12 }, annualized: {}, missing: {} };
  dash.tick();
  assert.ok(root.slots.metrics.writes > before);
  assert.match(root.slots.metrics.innerHTML, /0\.0100% → 0\.0200%/);
  dash.destroy();
});

test('Wyckoff + Signals sections: structure fetched for the active pane, late answers dropped, overlay toggle drives the pane', async () => {
  const timers = fakeTimers(), pending = [], overlays = [];
  const analyst = fakeAnalyst({ id: 0, exchange: 'binance', symbol: 'BTCUSDT', interval: '60', precision: 1 });
  const baseView = analyst.view;
  analyst.view = () => ({ ...baseView(), activeKey: `${baseView().activeKey}:ema7-14-21` }); // like analyst-panel keyOf
  const fetch = url => new Promise(resolve => pending.push({ url, resolve: body => resolve({ ok: true, json: async () => body }) }));
  const root = fakeRoot();
  const dash = D.mount(root, { analyst, getFunding: () => null, fetch, IntersectionObserver: null, pageVisible: () => true, setWyckoffOverlay: m => overlays.push(m), ...timers });
  assert.match(root.slots.wyckoff.innerHTML, /Loading the trading-range read/);
  const btc = pending.find(p => /structure/.test(p.url));
  analyst.st.active = { id: 0, exchange: 'binance', symbol: 'ETHUSDT', interval: '60', precision: 2 };
  analyst.notify();
  const eth = pending.filter(p => /structure/.test(p.url)).at(-1);
  assert.match(eth.url, /symbol=ETHUSDT/);
  const block = sym => ({ wyckoff: { status: 'structure', schematic: 'accumulation', phase: 'B', phase_label: 'Phase B — building cause inside the range', confidence: 0.6, confidence_label: 'medium',
    tr: { support: 100, resistance: 110, height: 10, start_ts: 1e12, end_ts: null, bars: 40 }, creek: 110, ice: null, bars: 300, source: `wyckoff.py ${sym}`,
    events: [{ event: 'SC', label: 'Selling climax', ts: 1e12, known_at: 1e12 + 3.6e6, price: 100, rule: 'rv 3 ≥ 2', confidence: 0.8, volume_rel: 3, spread_atr: 2, close_pos: 0.5 }],
    confirm: 'a Spring…', invalidate: 'closes below 100…' },
  signals: { edge: 'none proven', verdict: 'no candidate passed', events: [], volume_profile: null, session_vwap: null, unavailable: {}, rule: 'stop 1 ATR', stats_timeframe: '1h' }, missing: {} });
  eth.resolve(block('ETH')); await flush();
  btc.resolve(block('BTC')); await flush();
  assert.match(root.slots.wyckoff.innerHTML, /wyckoff\.py ETH/);
  assert.match(root.slots.wyckoff.innerHTML, /Accumulation · Phase B/);
  assert.match(root.slots.signals.innerHTML, /no proven edge/);
  assert.equal(overlays.length, 0, 'overlay off by default');
  const change = [...root.listeners.change][0];
  change({ target: { dataset: { act: 'wyckoff-overlay' }, checked: true } });
  assert.equal(overlays.length, 1);
  assert.equal(overlays[0].key, 'binance:ETHUSDT:60');
  assert.equal(overlays[0].top, 110);
  change({ target: { dataset: { act: 'wyckoff-overlay' }, checked: false } });
  assert.equal(overlays.at(-1), null);
  dash.destroy();
});
