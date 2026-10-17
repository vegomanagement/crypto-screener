'use strict';
// Pure-function tests for the grouped overlay indicators (static/indicators/*).
const assert = require('node:assert/strict');
const test = require('node:test');
const G = require('../static/indicators/groups.js');
const O = require('../static/indicators/opens.js');
const S = require('../static/indicators/sessions.js');

const iso = s => Date.parse(s) / 1000;
const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps * Math.max(1, Math.abs(b)), `${a} ≈ ${b}`);
const bars = (n, start = iso('2026-01-05T00:00:00Z'), step = 3600, f = i => 100 + Math.sin(i / 3) * 5 + i * 0.1) =>
  Array.from({ length: n }, (_, i) => { const c = f(i); return { time: start + i * step, open: c - 0.2, high: c + 1, low: c - 1, close: c, volume: 10 }; });
const defaults = def => Object.fromEntries(def.settingsSchema.map(f => [f.key, f.default]));
const def = id => G.definitions.find(d => d.id === id);

test('EMA ribbon: defaults 7/14/21, SMA seed, no invented warm-up, per-line settings', () => {
  const s = defaults(def('ema-ribbon'));
  assert.deepEqual([s.period1, s.period2, s.period3], [7, 14, 21]);
  assert.equal(def('ema-ribbon').labelForSettings(s), 'EMA 7/14/21');
  const candles = bars(30);
  const model = G.computeRibbon(candles, { ...s, color2: '#123456', width3: 3, show2: false });
  assert.equal(model.lines.length, 3);
  assert.equal(model.lines[0].data.length, 30 - 7 + 1);
  assert.equal(model.lines[2].data.length, 30 - 21 + 1);
  assert.equal(model.lines[1].visible, false); assert.equal(model.lines[1].color, '#123456'); assert.equal(model.lines[2].width, 3);
  const seed = candles.slice(0, 7).reduce((a, c) => a + c.close, 0) / 7;
  near(model.lines[0].data[0].value, seed);
  assert.equal(model.lines[0].data[0].time, candles[6].time);
  const alpha = 2 / 8; near(model.lines[0].data[1].value, alpha * candles[7].close + (1 - alpha) * seed);
  assert.equal(G.ema(candles.slice(0, 20), 21).length, 0, 'fewer bars than the period → nothing, not zeros');
  for (const p of [0, -1, 7.5, NaN]) assert.equal(G.ema(candles, p).length, 0);
});

test('MA channel: two SMAs of high/low with optional fill flag', () => {
  const s = defaults(def('ma-channel'));
  const candles = bars(25);
  const m = G.computeChannel(candles, s);
  assert.equal(m.upper.data.length, 6); assert.equal(m.lower.data.length, 6);
  near(m.upper.data[0].value, candles.slice(0, 20).reduce((a, c) => a + c.high, 0) / 20);
  near(m.lower.data.at(-1).value, candles.slice(5, 25).reduce((a, c) => a + c.low, 0) / 20);
  assert.equal(m.fill, false);
  assert.equal(G.computeChannel(candles, { ...s, fill: true, period: 10 }).upper.data.length, 16);
  assert.equal(def('ma-channel').labelForSettings(s), 'MA 20 H/L');
});

test('Turtle Zone: one indicator with upper and lower zone, inner below outer', () => {
  const s = defaults(def('turtle-zone'));
  const candles = bars(260, iso('2026-01-01T00:00:00Z'), 3600);
  const m = G.computeTurtle(candles, s);
  assert.equal(m.zones.length, 2);
  const [up, down] = m.zones;
  assert.equal(up.inner.length, 61); assert.equal(down.outer.length, 61);
  up.inner.forEach((p, i) => { assert.ok(up.outer[i].value > p.value); assert.ok(p.value > down.inner[i].value); assert.ok(down.inner[i].value > down.outer[i].value); });
  assert.equal(m.fill, true);
  assert.equal(def('turtle-zone').warnings(bars(150), s).length, 1);
  assert.equal(def('turtle-zone').labelForSettings({ ...s, length: 250 }), 'Turtle Zone 250');
});

test('settings migration from the old split ids', () => {
  const old = { version: 4, ema: { 7: false, 14: false, 28: true, 200: true }, ma20hl: { high: true, low: false },
    indicatorEnabled: { 'turtle-upper': true, 'turtle-lower': false, 'prev-day-high': true, 'prev-week-low': false, 'session-asia': true, 'session-ny': false, 'vwap-daily': true },
    indicatorSettings: { ema7: { period: 9, color: '#111111' }, ema14: { period: 14 }, ema28: { period: 28 }, ma20high: { period: 30, source: 'close' }, 'turtle-upper': { length: 300, inner: 4 } } };
  const { saved, notes } = G.migrateLegacyState(old);
  assert.equal(saved.indicatorEnabled['ema-ribbon'], true, 'ema28 enabled → ribbon enabled');
  assert.deepEqual(saved.indicatorSettings['ema-ribbon'], { period1: 9, color1: '#111111', show1: false, show2: false, show3: true });
  assert.equal(saved.indicatorEnabled.ema200, true);
  assert.equal(saved.indicatorEnabled['ma-channel'], true);
  assert.deepEqual(saved.indicatorSettings['ma-channel'], { period: 30, highSource: 'close', showHigh: true, showLow: false });
  assert.equal(saved.indicatorEnabled['turtle-zone'], true);
  assert.deepEqual(saved.indicatorSettings['turtle-zone'], { length: 300, inner: 4, showUpper: true, showLower: false });
  assert.equal(saved.indicatorEnabled.opens, true); assert.equal(saved.indicatorSettings.opens.pdhShow, true); assert.equal(saved.indicatorSettings.opens.pwlShow, false);
  assert.equal(saved.indicatorEnabled.sessions, true); assert.equal(saved.indicatorSettings.sessions.s3Show, true); assert.equal(saved.indicatorSettings.sessions.s2Show, false);
  assert.equal(saved.indicatorEnabled['vwap-daily'], true, 'unrelated ids untouched');
  for (const id of ['ema7', 'ema28', 'ma20high', 'turtle-upper', 'prev-day-high', 'session-asia']) { assert.equal(id in saved.indicatorEnabled, false); assert.equal(id in saved.indicatorSettings, false); }
  assert.equal('ema' in saved, false); assert.equal('ma20hl' in saved, false);
  assert.ok(notes.some(n => /custom period 9/.test(n)) && notes.some(n => /EMA 28 was enabled/.test(n)));
  // A custom EMA 28 period survives; the old default 28 maps to the new default 21.
  assert.equal(G.migrateLegacyState({ ema: { 28: true }, indicatorSettings: { ema28: { period: 34 } } }).saved.indicatorSettings['ema-ribbon'].period3, 34);
  assert.equal(G.migrateLegacyState({ ema: { 28: true }, indicatorSettings: { ema28: { period: 28 } } }).saved.indicatorSettings['ema-ribbon'].period3, undefined);
  // All old EMA lines off → ribbon off, but its lines keep default visibility.
  const off = G.migrateLegacyState({ ema: { 7: false, 14: false, 28: false } }).saved;
  assert.equal(off.indicatorEnabled['ema-ribbon'], false); assert.deepEqual(off.indicatorSettings['ema-ribbon'], {});
  assert.deepEqual(G.migrateLegacyState(null).saved, null);
});

// ── Opens / key levels ──
test('UTC period anchoring: day, Monday week, calendar month/quarter/half/year', () => {
  const t = iso('2026-10-17T13:45:00Z'); // Saturday
  assert.equal(O.periodStart(t, 'D'), iso('2026-10-17T00:00:00Z'));
  assert.equal(O.periodStart(t, 'W'), iso('2026-10-12T00:00:00Z'));
  assert.equal(O.periodStart(iso('2026-10-12T00:00:00Z'), 'W'), iso('2026-10-12T00:00:00Z'), 'Monday 00:00 starts its own week');
  assert.equal(O.periodStart(iso('2026-10-11T23:59:59Z'), 'W'), iso('2026-10-05T00:00:00Z'), 'Sunday belongs to the previous week');
  assert.equal(O.periodStart(t, 'M'), iso('2026-10-01T00:00:00Z'));
  assert.equal(O.periodStart(t, '3M'), iso('2026-10-01T00:00:00Z'));
  assert.equal(O.periodStart(iso('2026-09-30T23:00:00Z'), '3M'), iso('2026-07-01T00:00:00Z'));
  assert.equal(O.periodStart(t, '6M'), iso('2026-07-01T00:00:00Z'));
  assert.equal(O.periodStart(t, 'Y'), iso('2026-01-01T00:00:00Z'));
  assert.equal(O.periodStart(t, '4h'), iso('2026-10-17T12:00:00Z'));
  assert.equal(O.nextPeriodStart(iso('2026-12-01T00:00:00Z'), 'M'), iso('2027-01-01T00:00:00Z'));
  assert.equal(O.prevPeriodStart(iso('2026-03-01T00:00:00Z'), 'M'), iso('2026-02-01T00:00:00Z'));
  assert.equal(O.prevPeriodStart(iso('2026-01-01T00:00:00Z'), '3M'), iso('2025-10-01T00:00:00Z'));
  assert.equal(O.nextPeriodStart(iso('2026-10-12T00:00:00Z'), 'W'), iso('2026-10-19T00:00:00Z'));
});

const daily = (from, n, f = i => 1000 + i) => Array.from({ length: n }, (_, i) => { const p = f(i); return { time: iso(from) + i * 86400, open: p, high: p + 50, low: p - 40, close: p + 5, closed: true }; });
test('opens come only from real candles starting exactly at the period start; lookback; partial current period', () => {
  const D = daily('2026-09-01T00:00:00Z', 47); // 1 Sep … 17 Oct (17 Oct forming)
  D.at(-1).closed = false;
  const chart = { candles: bars(200, iso('2026-10-09T00:00:00Z'), 3600), step: 3600 }; // through 17 Oct 07:00
  const s = { ...O.settingsDefaults(), o1Lookback: 3 };
  const m = O.computeModel({ D, W: [], M: [] }, s, chart);
  const opens = m.levels.filter(l => l.kind === 'open');
  const dOpens = opens.filter(l => l.tf === 'D');
  assert.deepEqual(dOpens.map(l => l.startTime), [iso('2026-10-15T00:00:00Z'), iso('2026-10-16T00:00:00Z'), iso('2026-10-17T00:00:00Z')]);
  assert.equal(dOpens.at(-1).current, true); assert.equal(dOpens.at(-1).price, 1046, 'forming daily bar supplies the current day open');
  assert.equal(dOpens[0].endTime, iso('2026-10-16T00:00:00Z'));
  assert.equal(opens.find(l => l.tf === 'W').price, D.find(c => c.time === iso('2026-10-12T00:00:00Z')).open);
  assert.equal(opens.find(l => l.tf === 'M').price, 1030, 'month open = 1 Oct daily open');
  assert.ok(m.warnings.some(w => /Y open of the current period is unavailable/.test(w)), 'no Jan-1 candle → year open not fabricated');
  assert.equal(opens.some(l => l.tf === 'Y'), false);
  // With a monthly candle for January the year open appears.
  const M = [{ time: iso('2026-01-01T00:00:00Z'), open: 777, high: 900, low: 700, close: 800, closed: true }];
  assert.equal(O.computeModel({ D, W: [], M }, s, chart).levels.find(l => l.tf === 'Y').price, 777);
  // A later bar is NOT accepted as an open for a period whose first bar is missing.
  const gap = D.filter(c => c.time !== iso('2026-10-12T00:00:00Z'));
  assert.equal(O.collectOpens({ D: gap }, 'W').some(o => o.start === iso('2026-10-12T00:00:00Z')), false);
});

test('opens visibility follows the script: only timeframes larger than the chart', () => {
  assert.equal(O.isVisible('D', 3600), true); assert.equal(O.isVisible('D', 86400), false); assert.equal(O.isVisible('M', 7 * 86400), true);
  assert.deepEqual(O.neededTimeframes(O.settingsDefaults(), 900), ['D', 'M', 'W']);
  assert.deepEqual(O.neededTimeframes({ ...O.settingsDefaults(), pmhShow: false, pmlShow: false, o3Show: false, o5Show: false }, 900), ['D', 'W']);
});

test('previous D/W/M highs and lows come from the closed higher-timeframe candle of the prior period', () => {
  const D = daily('2026-09-01T00:00:00Z', 47); D.at(-1).closed = false;
  const W = [{ time: iso('2026-10-05T00:00:00Z'), open: 1, high: 1200, low: 900, close: 2, closed: true }, { time: iso('2026-10-12T00:00:00Z'), open: 3, high: 5000, low: 1, close: 4, closed: false }];
  const M = [{ time: iso('2026-09-01T00:00:00Z'), open: 1, high: 1300, low: 800, close: 2, closed: true }];
  const chart = { candles: bars(200, iso('2026-10-09T00:00:00Z'), 3600), step: 3600 };
  const m = O.computeModel({ D, W, M }, { ...O.settingsDefaults(), prevLookback: 2 }, chart);
  const get = (key, k = 0) => m.levels.filter(l => l.key === key)[k];
  assert.equal(get('pdh').price, D.find(c => c.time === iso('2026-10-16T00:00:00Z')).high); assert.equal(get('pdh').current, true);
  assert.equal(get('pdl', 1).price, D.find(c => c.time === iso('2026-10-15T00:00:00Z')).low);
  assert.equal(get('pdl', 1).startTime, iso('2026-10-16T00:00:00Z')); assert.equal(get('pdl', 1).endTime, iso('2026-10-17T00:00:00Z'));
  assert.equal(get('pwh').price, 1200); assert.equal(get('pwl').price, 900); assert.equal(get('pwh').startTime, iso('2026-10-12T00:00:00Z'));
  assert.equal(get('pmh').price, 1300); assert.equal(get('pml').price, 800);
  assert.equal(m.levels.filter(l => l.key === 'pwh').length, 1, 'week before last unavailable → only one level, with no fabricated value');
  const none = O.computeModel({ D: [], W: [], M: [] }, O.settingsDefaults(), chart);
  assert.ok(none.warnings.some(w => /PDH: previous D candle not available/.test(w)));
  assert.equal(none.levels.length, 0);
});

test('labels of overlapping current opens are padded right like the script', () => {
  const D = daily('2026-10-01T00:00:00Z', 17, () => 1000); // flat: D, W, M opens all equal
  const chart = { candles: bars(50, iso('2026-10-15T00:00:00Z'), 3600, () => 1000), step: 3600 };
  const m = O.computeModel({ D, W: [], M: [] }, O.settingsDefaults(), chart);
  assert.deepEqual(m.levels.filter(l => l.kind === 'open' && l.current).map(l => [l.tf, l.padding]), [['D', 0], ['W', 1], ['M', 2]]);
  const calls = []; const ctx = { save() {}, restore() {}, beginPath() {}, moveTo(x, y) { calls.push(['m', x, y]); }, lineTo(x, y) { calls.push(['l', x, y]); }, stroke() {}, fillText(t, x) { calls.push(['t', t, x]); }, setLineDash() {} };
  const view = { x: t => (t - chart.candles[0].time) / 3600 * 10, lastX: 495, barSpacing: 10, y: p => 1000 - p + 100, format: p => String(p) };
  O.draw(ctx, view, m, O.settingsDefaults());
  assert.deepEqual(calls.filter(c => c[0] === 't').map(c => [c[1], c[2]]), [['D', 698], ['W', 738], ['M', 778], ['PDH', 698], ['PDL', 698]]);
  // Labels that would leave the plot are pinned inside its right edge, one label width per overlap.
  calls.length = 0;
  O.draw({ ...ctx, measureText: () => ({ width: 10 }) }, { ...view, plotWidth: 720 }, m, O.settingsDefaults());
  assert.deepEqual(calls.filter(c => c[0] === 't').map(c => [c[1], c[2]]), [['D', 698], ['W', 692], ['M', 676], ['PDH', 698], ['PDL', 698]]);
});

test('HTF loader fetches each timeframe once per TTL and appends the forming bar', async () => {
  const urls = [];
  const fetch = async url => { urls.push(url); const live = url.startsWith('/api/chart/live');
    return { ok: true, json: async () => live ? { candle: { ts: 1760659200000, o: 5, h: 6, l: 4, c: 5, v: 1, is_forming: true, interval: 'D', symbol: 'BTCUSDT' } }
      : { exchange: 'binance', closed_only: true, candles: [{ ts: 1760572800000, o: 1, h: 2, l: 0.5, c: 1.5, v: 1 }] } }; };
  let now = 0; const loader = O.createLoader({ fetch, now: () => now });
  const a = await loader.ensure('binance', 'BTCUSDT', ['D']);
  assert.equal(a.D.length, 2); assert.equal(a.D[1].closed, false); assert.equal(a.D[1].open, 5);
  await loader.ensure('binance', 'BTCUSDT', ['D']); assert.equal(urls.length, 2);
  now = 61000; await loader.ensure('binance', 'BTCUSDT', ['D']); assert.equal(urls.length, 4);
  assert.match(urls[0], /interval=D&limit=500/);
});

// ── Market sessions ──
const only = (n, extra = {}) => ({ ...S.settingsDefaults(), s1Show: n === 1, s2Show: n === 2, s3Show: n === 3, s4Show: n === 4, ...extra });
const hourly = (from, n) => bars(n, iso(from), 3600, i => 100 + i); // high = 101 + i, low = 99 + i

test('session time and fixed GMT offsets parse like the script inputs', () => {
  assert.deepEqual(S.parseSession('0800-1700'), { start: 480, end: 1020, minutes: 540 });
  assert.deepEqual(S.parseSession('2000-0500'), { start: 1200, end: 300, minutes: 540 });
  assert.equal(S.parseOffset('GMT+0'), 0); assert.equal(S.parseOffset('GMT-5'), -18000); assert.equal(S.parseOffset('GMT+530'), 19800); assert.equal(S.parseOffset('GMT+330'), 12600);
  for (const bad of ['8-17', '2500-0100', '0800-0800', '']) assert.throws(() => S.parseSession(bad));
  assert.throws(() => S.parseOffset('Europe/London'));
  const d = S.settingsDefaults();
  assert.deepEqual([1, 2, 3, 4].map(n => [d[`s${n}Name`], d[`s${n}Time`], d[`s${n}Show`]]),
    [['London', '0800-1700', true], ['New York', '1300-2200', true], ['Tokyo', '0000-0900', true], ['Sydney', '2000-0500', false]]);
  assert.equal(d.history, 10); assert.equal(d.orMinutes, 15); assert.equal(d.timezone, 'GMT+0');
});

test('London window: bars whose open time is in [08:00, 17:00) UTC; high/low = max/min of those bars', () => {
  const candles = hourly('2026-10-12T00:00:00Z', 48);
  const m = S.compute(candles, only(1), 3600);
  assert.equal(m.instances.length, 2);
  const [a, b] = m.instances;
  assert.equal(a.start, iso('2026-10-12T08:00:00Z')); assert.equal(a.end, iso('2026-10-12T17:00:00Z'));
  assert.equal(a.firstTime, iso('2026-10-12T08:00:00Z')); assert.equal(a.lastTime, iso('2026-10-12T16:00:00Z'));
  assert.equal(a.high, 101 + 16); assert.equal(a.low, 99 + 8); assert.equal(a.open, candles[8].open); assert.equal(a.close, candles[16].close);
  assert.equal(a.ended, true); assert.equal(a.day, 'Mon'); assert.equal(b.day, 'Tue');
  assert.equal(a.label, 'London');
});

test('Sydney 2000-0500 crosses midnight and timezone offsets shift the window', () => {
  const candles = hourly('2026-10-12T00:00:00Z', 48);
  const syd = S.compute(candles, only(4, { s4Show: true }), 3600).instances;
  // 12 Oct 20:00 → 13 Oct 05:00 (complete) plus the partial 11 Oct 20:00 → 12 Oct 05:00 tail and the 13 Oct evening.
  const full = syd.find(i => i.start === iso('2026-10-12T20:00:00Z'));
  assert.equal(full.end, iso('2026-10-13T05:00:00Z'));
  assert.equal(full.lastTime, iso('2026-10-13T04:00:00Z')); assert.equal(full.high, 101 + 28); assert.equal(full.low, 99 + 20);
  assert.equal(syd[0].start, iso('2026-10-11T20:00:00Z')); assert.equal(syd[0].firstTime, candles[0].time, 'partial: only real bars inside the window');
  assert.equal(syd.at(-1).ended, false, 'the window still open at the last closed bar is in progress');
  // GMT-5: London 0800-1700 local = 13:00-22:00 UTC.
  const ny = S.compute(candles, only(1, { timezone: 'GMT-5' }), 3600).instances[0];
  assert.equal(ny.start, iso('2026-10-12T13:00:00Z')); assert.equal(ny.end, iso('2026-10-12T22:00:00Z'));
  const india = S.compute(candles, only(3, { timezone: 'GMT+530' }), 3600).instances.find(i => i.start === iso('2026-10-12T18:30:00Z'));
  assert.ok(india, 'Tokyo 0000 at GMT+530 starts 18:30 UTC the day before');
  assert.equal(india.firstTime, iso('2026-10-12T19:00:00Z'), 'hourly bars: first bar whose open is inside the window');
});

test('history count keeps the latest N sessions per slot; extend keeps only the latest', () => {
  const candles = hourly('2026-10-01T00:00:00Z', 24 * 15);
  assert.equal(S.compute(candles, only(1), 3600).instances.length, 10);
  assert.equal(S.compute(candles, only(1, { history: 3 }), 3600).instances.length, 3);
  assert.equal(S.compute(candles, only(1, { s1Extend: true }), 3600).instances.length, 1);
  assert.equal(S.compute(candles, S.settingsDefaults(), 3600).instances.length, 30, 'three default sessions × 10');
});

test('sessions are hidden on daily and higher intervals', () => {
  const m = S.compute(bars(30, iso('2026-09-01T00:00:00Z'), 86400), S.settingsDefaults(), 86400);
  assert.equal(m.hidden, true); assert.equal(m.instances.length, 0); assert.match(m.warnings[0], /intraday-only/);
});

test('opening range: first N minutes, targets from the average of previous session ranges, breakout flags', () => {
  // 5m bars: first London session flat 100±3 (range 6), second one 100±1 then a breakout.
  const start = iso('2026-10-12T00:00:00Z'), step = 300, candles = [];
  for (let t = start; t < start + 2 * 86400; t += step) {
    const day2 = t >= start + 86400, inLondon2 = day2 && t >= start + 86400 + 8 * 3600;
    const k = (t - (start + 86400 + 8 * 3600)) / step; // bar index inside day-2 London
    let mid = 100;
    if (inLondon2 && k >= 3) mid = 100 + (k - 2) * 0.5; // rallies after the opening range
    const w = day2 ? 1 : 3; // day-1 range 6 so that day-2 targets sit above the opening range
    candles.push({ time: t, open: mid, high: mid + w, low: mid - w, close: mid, volume: 1 });
  }
  const m = S.compute(candles, only(1, { s1Op: true, orMinutes: 15 }), step).instances;
  const [first, second] = m;
  assert.equal(first.or.targets, null, 'no previous session → no targets (avg range is na in Pine)');
  assert.equal(first.or.top, 103); assert.equal(first.or.btm, 97); assert.equal(first.or.lastOrTime, iso('2026-10-12T08:10:00Z'), '15 min of 5m bars = 3 bars');
  assert.equal(second.avgRange, 6);
  const t = second.or.targets, mdl = 100;
  near(t.R1, (99 + mdl) / 2 + 6 * 0.786 * 0.5); near(t.R2, (99 + mdl) / 2 + 6 * 1.272 * 0.5);
  near(t.S1, (101 + mdl) / 2 - 6 * 0.786 * 0.5); near(t.S2, (101 + mdl) / 2 - 6 * 1.272 * 0.5);
  const r1 = second.or.breakouts.find(b => b.level === 'R1');
  const firstAbove = candles.find(c => c.time > second.or.lastOrTime && c.high > t.R1);
  assert.equal(r1.time, firstAbove.time); assert.equal(r1.side, 'up');
  assert.equal(second.or.breakouts.filter(b => b.level === 'R1').length, 1, 'flag once per level');
  assert.equal(second.or.breakouts.some(b => b.side === 'down'), false);
  assert.ok(second.or.breakouts.some(b => b.level === 'R2'));
  // On a 1h chart the opening range is the first bar.
  const hour = S.compute(hourly('2026-10-12T00:00:00Z', 30), only(1, { s1Op: true }), 3600).instances[0];
  assert.equal(hour.or.lastOrTime, iso('2026-10-12T08:00:00Z'));
  // Pending until the opening-range bars have closed.
  const early = candles.filter(c => c.time <= iso('2026-10-13T08:05:00Z'));
  assert.equal(S.compute(early, only(1, { s1Op: true }), step).instances.at(-1).or.pending, true);
});

test('fibonacci levels and label text (name, day, price range, range %)', () => {
  const inst = S.compute(hourly('2026-10-12T00:00:00Z', 30), only(1, { s1Fib: true, labelDay: true, labelPrice: true, labelPct: true }), 3600).instances[0];
  assert.deepEqual(inst.fib.map(f => f.level), [0.5, 0.628, 0.382]);
  near(inst.fib[0].price, (inst.high + inst.low) / 2);
  assert.equal(inst.label, `London • Mon • 10 • ${(10 / 107 * 100).toFixed(2)}%`);
});

test('session renderer: Box draws one rect per instance; Background has no border; extend reaches the plot edge', () => {
  const model = S.compute(hourly('2026-10-12T00:00:00Z', 30), only(1), 3600);
  const log = []; const ctx = { save() {}, restore() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, setLineDash() {}, fillText(t) { log.push(['text', t]); },
    fillRect(x, y, w, h) { log.push(['fill', x, w]); }, strokeRect(x, y, w) { log.push(['stroke', x, w]); } };
  const view = { x: t => (t - iso('2026-10-12T00:00:00Z')) / 3600 * 10, y: p => 500 - p, barSpacing: 10, plotWidth: 999, format: String };
  assert.equal(S.draw(ctx, view, model, only(1)), 1);
  assert.deepEqual(log.find(e => e[0] === 'stroke'), ['stroke', 80, 90], '08:00 → 17:00 edges');
  log.length = 0; S.draw(ctx, view, model, only(1, { boxStyle: 'Background' }));
  assert.equal(log.some(e => e[0] === 'stroke'), false);
  log.length = 0; S.draw(ctx, view, S.compute(hourly('2026-10-12T00:00:00Z', 30), only(1, { s1Extend: true }), 3600), only(1, { s1Extend: true }));
  assert.deepEqual(log.find(e => e[0] === 'stroke'), ['stroke', 80, 919]);
});
