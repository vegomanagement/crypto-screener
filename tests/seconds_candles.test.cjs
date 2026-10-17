'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const sc = require(path.join(__dirname, '../static/seconds-candles.js'));
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/seconds_parity.json'), 'utf8'));

function aggregateFixture(seconds) {
  const agg = sc.createAggregator(seconds * 1000);
  fixture.trades.forEach(row => agg.add(sc.normalize('binance', row)));
  return agg.list().map(b => ({ t: b.t, o: b.open, h: b.high, l: b.low, c: b.close, v: sc.fmtUnits(b.volume), buy_v: sc.fmtUnits(b.buyVolume), n: b.trades }));
}

test('shared fixture: JS seconds aggregation equals the hand-computed (and Python) bars', () => {
  for (const seconds of ['1', '5', '15', '30']) {
    const expected = fixture.expected[seconds].map(b => ({ t: b.t, o: Number(b.o), h: Number(b.h), l: Number(b.l), c: Number(b.c), v: b.v, buy_v: b.buy_v, n: b.n }));
    assert.deepEqual(aggregateFixture(Number(seconds)), expected, `${seconds}s`);
  }
});

test('UTC alignment, midnight split, gaps stay gaps, same formula as footprint bars', () => {
  const M = fixture.midnight_ms;
  const times = aggregateFixture(1).map(b => b.t);
  assert.ok(times.includes(M - 1000) && times.includes(M));
  assert.deepEqual(times.filter(t => t >= M + 3000 && t < M + 29000), [], 'no candle for seconds without trades');
  for (const row of fixture.trades) for (const step of [1000, 5000, 15000, 30000]) {
    assert.equal(sc.bucketOpen(row.T, step), Math.floor(row.T / step) * step);
    assert.equal(sc.bucketOpen(row.T, step) % step, 0);
  }
});

test('out-of-order delivery keeps id-ordered open/close; exact volume units', () => {
  const agg = sc.createAggregator(1000);
  const T = 1792195200000;
  for (const [a, p] of [[3, '11'], [1, '10'], [2, '12']]) agg.add(sc.normalize('binance', { a, T: T + a, p, q: '0.1', m: false, f: a, l: a }));
  const [bar] = agg.list();
  assert.equal(bar.open, 10); assert.equal(bar.close, 11); assert.equal(bar.high, 12);
  assert.equal(sc.fmtUnits(bar.volume), '0.3', '0.1+0.1+0.1 is exact, not 0.30000000000000004');
  assert.throws(() => sc.units('1e-3')); assert.throws(() => sc.units('-1'));
  assert.throws(() => sc.createAggregator(1500));
});

test('venue normalizers keep side/id semantics', () => {
  assert.deepEqual(sc.normalize('bybit', { T: 5, p: '1.5', v: '2', S: 'Buy', i: 'x' }), { time: 5, price: 1.5, qty: sc.units('2'), side: 'ask', id: 'x' });
  assert.equal(sc.normalize('hyperliquid', { time: 5, px: '3', sz: '1', side: 'A', tid: 9 }).side, 'bid');
  assert.throws(() => sc.normalize('binance', { a: 'x', T: 5, p: '1', q: '1', m: true }));
  assert.throws(() => sc.normalize('bybit', { T: 5, p: '0', v: '1', S: 'Buy', i: 'y' }));
});

test('countdown: next close per interval (UTC, Monday weeks, calendar months) and formatting', () => {
  const now = Date.UTC(2026, 9, 17, 13, 47, 12, 300); // Saturday
  assert.equal(sc.nextCloseMs('5s', now), Date.UTC(2026, 9, 17, 13, 47, 15));
  assert.equal(sc.nextCloseMs('1s', now), Date.UTC(2026, 9, 17, 13, 47, 13));
  assert.equal(sc.nextCloseMs('1', now), Date.UTC(2026, 9, 17, 13, 48));
  assert.equal(sc.nextCloseMs('240', now), Date.UTC(2026, 9, 17, 16));
  assert.equal(sc.nextCloseMs('D', now), Date.UTC(2026, 9, 18));
  assert.equal(sc.nextCloseMs('W', now), Date.UTC(2026, 9, 19), 'weeks close Monday 00:00 UTC');
  assert.equal(sc.nextCloseMs('M', now), Date.UTC(2026, 10, 1));
  assert.equal(sc.nextCloseMs('M', Date.UTC(2026, 11, 31, 23)), Date.UTC(2027, 0, 1));
  assert.equal((sc.nextCloseMs('3D', now)) % (3 * 86400000), 0, 'multi-day epoch aligned');
  assert.equal(sc.nextCloseMs('bogus', now), null);
  assert.equal(sc.formatCountdown(2700), '00:03');
  assert.equal(sc.formatCountdown(59000), '00:59');
  assert.equal(sc.formatCountdown(3600000 + 61000), '1:01:01');
  assert.equal(sc.formatCountdown(2 * 86400000 + 3 * 3600000 + 5 * 60000), '2d 03:05');
  assert.equal(sc.formatCountdown(-1), '');
  assert.deepEqual(sc.sessionStarts(Date.UTC(2026, 9, 17, 5), Date.UTC(2026, 9, 19, 1)), [Date.UTC(2026, 9, 18), Date.UTC(2026, 9, 19)]);
});

function fakeHub() {
  const subs = [];
  return { subs, subscribeTrades(config, fn) { const entry = { config, fn, off: false }; subs.push(entry); fn({ type: 'status', status: 'connecting' }); return () => { entry.off = true; }; },
    push(rows) { subs.filter(s => !s.off).forEach(s => s.fn({ type: 'trades', venue: s.config.venue, symbol: s.config.symbol, rows })); } };
}
const bt = (a, T, p = '100', q = '0.010', m = false) => ({ a, T, p, q, m, f: a, l: a });

test('controller: Binance backfill then id-contiguous live continuation; buffered overlap deduped', async () => {
  const T = 1792195200000;
  let now = T + 12500;
  const hub = fakeHub();
  let resolve, waited = null;
  const fetch = url => { assert.match(url, /^\/api\/seconds\?exchange=binance&symbol=BTCUSDT&minutes=10&seconds=5$/); return new Promise(r => { resolve = r; }); };
  const ctl = sc.createController({ exchange: 'binance', symbol: 'BTCUSDT', interval: '5s', hub, fetch, now: () => now, setInterval: () => 1, clearInterval: () => {}, setTimeout: (fn, ms) => { waited = ms; return 7; }, clearTimeout: () => {} }).start();
  assert.equal(resolve, undefined, 'REST waits for the first live trade so snapshot and stream overlap');
  assert.equal(waited, 5000, 'bounded wait: a silent stream still gets the backfill after 5 s');
  hub.push([bt(11, T + 12100, '101'), bt(12, T + 12200, '102')]); // first live trades trigger REST
  resolve({ ok: true, status: 200, json: async () => ({ exchange: 'binance', symbol: 'BTCUSDT', seconds: 5, as_of: T + 12150, last_trade_id: 11, complete_from_ms: T + 5000, trades: 3, requests: 1, truncated: false,
    bars: [{ t: T + 5000, o: '99', h: '100', l: '99', c: '100', v: '0.02', buy_v: '0', n: 2, first_id: 9, last_id: 10, forming: false },
      { t: T + 10000, o: '101', h: '101', l: '101', c: '101', v: '0.01', buy_v: '0.01', n: 1, first_id: 11, last_id: 11, forming: true }] }) });
  await new Promise(r => setImmediate(r));
  let v = ctl.view();
  assert.deepEqual(v.closed.map(b => b.time), [(T + 5000) / 1000]);
  assert.equal(v.forming.close, 102); assert.equal(v.forming.volumeExact, '0.02', 'id 11 not double counted');
  assert.equal(v.forming.partial, false, 'id-contiguous with backfill → complete');
  hub.push([bt(14, T + 15100, '103')]); // id 13 missing → gap
  now = T + 15200;
  v = ctl.view();
  assert.equal(ctl.state.gaps, 1);
  assert.equal(v.closed.at(-1).partial, true, 'bar spanning the gap is flagged');
  assert.equal(v.forming.partial, true);
  assert.match(ctl.statusText(), /Backfilled from Binance aggTrades since 00:00:05 UTC/);
  assert.match(ctl.statusText(), /1 live id gap/);
  ctl.dispose();
  assert.equal(hub.subs[0].off, true);
});

test('controller: Bybit is live-only, first bucket partial, ids deduped, empty seconds stay empty', () => {
  const T = 1792195200000;
  let now = T + 1500;
  const hub = fakeHub();
  let fetched = false;
  const ctl = sc.createController({ exchange: 'bybit', symbol: 'BTCUSDT', interval: '1s', hub, fetch: () => { fetched = true; }, now: () => now, setInterval: () => 1, clearInterval: () => {} }).start();
  const row = (i, T, p) => ({ T, p, v: '1', S: 'Sell', i });
  hub.push([row('a', T + 1400, '10'), row('a', T + 1400, '10')]);
  hub.push([row('b', T + 4100, '11')]);
  now = T + 5200;
  const v = ctl.view();
  assert.equal(fetched, false, 'no history request for venues without public trade history');
  assert.deepEqual(v.closed.map(b => [b.time, b.partial, b.volumeExact]), [[(T + 1000) / 1000, true, '1'], [(T + 4000) / 1000, false, '1']]);
  assert.equal(v.forming, null);
  assert.match(ctl.statusText(), /No public trade history.*live since 00:00:01 UTC/);
});

test('controller: failed backfill falls back to live-only with explicit status', async () => {
  const hub = fakeHub();
  const ctl = sc.createController({ exchange: 'binance', symbol: 'BTCUSDT', interval: '15s', hub, fetch: async () => ({ ok: false, status: 502, json: async () => ({ error: 'binance down' }) }), now: () => 1792195200000, setInterval: () => 1, clearInterval: () => {}, setTimeout: fn => { fn(); return 1; }, clearTimeout: () => {} }).start();
  await new Promise(r => setImmediate(r));
  hub.push([bt(5, 1792195200100)]);
  assert.match(ctl.statusText(), /Backfill failed \(binance down\); live only/);
  assert.equal(ctl.view().forming.partial, true);
});

test('aggregator prunes to MAX_BARS oldest-first', () => {
  const agg = sc.createAggregator(1000);
  for (let i = 0; i < sc.MAX_BARS + 5; i += 1) agg.add({ time: 1000 * (i + 1), price: 1, qty: 1n, side: 'ask', id: i });
  assert.equal(agg.size, sc.MAX_BARS);
  assert.equal(agg.list()[0].t, 6000);
});
