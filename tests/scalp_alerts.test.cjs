'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const ob = require(path.join(__dirname, '../static/orderbook.js'));
const L = require(path.join(__dirname, '../static/levels.js'));
const A = require(path.join(__dirname, '../static/scalp-alerts.js'));

function book(bids, asks) {
  const b = new ob.Book(0.1);
  b.load(bids, asks);
  const best = b.best(); b.coverage = { bidFloor: best.deepestBid - 1000, askCeil: best.deepestAsk + 1000 };
  return b;
}
const lvl = (price, extra = {}) => L.normalize({ id: `x${price.replace('.', '_')}`, exchange: 'binance', symbol: 'BTCUSDT', price, kind: 'signal', source: 'dom', ...extra });
const print = (price, qty, side, time) => ({ price, qty, side, time });

test('signals: armed level triggers on a cross, simple levels ignored, repeat re-arms only ≥ 1 tick away', () => {
  const a = lvl('100.5'), s = lvl('100.4', { kind: 'simple' }), r = lvl('100.2', { repeat: true });
  let out = A.evaluateSignals([a, s, r], '100.3', '100.6', '0.1');
  assert.deepEqual(out.trigger.map(l => l.price), ['100.5'], 'simple levels never alert; 100.2 not crossed');
  assert.deepEqual(A.triggerPatch(a, 7), { armed: false, triggeredAt: 7, color: L.TRIGGERED_COLOR });
  assert.deepEqual(A.triggerPatch(r, 7), { armed: false, triggeredAt: 7 }, 'repeat levels keep their colour');
  const fired = { ...r, armed: false, triggeredAt: 7 };
  assert.deepEqual(A.evaluateSignals([fired], '100.2', '100.2', '0.1'), { trigger: [], rearm: [] }, 'still on the level');
  assert.deepEqual(A.evaluateSignals([fired], '100.2', '100.25', '0.1').rearm, [], 'half a tick is not leaving');
  assert.deepEqual(A.evaluateSignals([fired], '100.2', '100.3', '0.1').rearm.map(l => l.id), [r.id]);
  const done = { ...a, armed: false, triggeredAt: 7 };
  assert.deepEqual(A.evaluateSignals([done], '100.4', '100.6', '0.1'), { trigger: [], rearm: [] }, 'a fired non-repeat level stays quiet');
  assert.equal(A.evaluateSignals([lvl('67890.1')], '67889.9', '67900.0', '0.1').trigger.length, 1, 'gap over the level');
});

test('rate limiter: 3 s per (type, price)', () => {
  const r = new A.RateLimiter(3000);
  assert.equal(r.allow('print', '100.1', 0), true);
  assert.equal(r.allow('print', '100.1', 2999), false);
  assert.equal(r.allow('print', '100.2', 2999), true, 'other price');
  assert.equal(r.allow('appear', '100.1', 2999), true, 'other type');
  assert.equal(r.allow('print', '100.1', 3000), true);
});

test('appear: first synced book only seeds; a new large level inside Y % alerts once; outside Y % is ignored', () => {
  const va = new A.VolumeAlerts({ tick: 0.1, tickStr: '0.1' });
  va.configure({ appear: true, appearUsd: 50000, withinPct: 1 });
  assert.deepEqual(va.onBook(book([['100.0', '1000']], [['100.1', '1']]), 0), [], 'pre-existing $100k bid is a seed, not an alert');
  let out = va.onBook(book([['100.0', '1000'], ['99.5', '600']], [['100.1', '1'], ['105.0', '9000']]), 100);
  assert.equal(out.length, 1);
  assert.deepEqual({ type: out[0].type, side: out[0].side, price: out[0].price }, { type: 'appear', side: 'bid', price: '99.5' });
  assert.equal(Math.round(out[0].usd), 59700);
  out = va.onBook(book([['100.0', '1000'], ['99.5', '600']], [['100.1', '1']]), 200);
  assert.deepEqual(out, [], 'still there: no repeat alert');
  va.reset();
  assert.deepEqual(va.onBook(book([['99.0', '900']], [['100.1', '1']]), 300), [], 'after a resync the next book seeds again');
});

test('decrease: drop explained by trades at that price is "traded", unexplained drop is "pulled", partial is "partly traded"', () => {
  const mk = () => { const va = new A.VolumeAlerts({ tick: 0.1, tickStr: '0.1', graceMs: 0 }); va.configure({ decrease: true, decreasePct: 50, appearUsd: 50000, withinPct: 1 }); return va; };
  // traded through: sell takers hit the 100.0 bid for exactly the dropped size
  let va = mk();
  va.onBook(book([['100.0', '1000']], [['100.1', '1']]), 0);
  va.onTrade(print(100.0, 700, 'sell', 50), 50);
  let out = va.onBook(book([['100.0', '300']], [['100.1', '1']]), 100);
  assert.equal(out.length, 1);
  assert.deepEqual({ type: out[0].type, cause: out[0].cause, price: out[0].price, tradedQty: out[0].tradedQty, pulledQty: out[0].pulledQty },
    { type: 'decrease', cause: 'traded', price: '100.0', tradedQty: 700, pulledQty: 0 });
  // pulled: no trade at that price; a buy at the same price lifts asks, it cannot consume a bid
  va = mk();
  va.onBook(book([['100.0', '1000']], [['100.1', '1']]), 0);
  va.onTrade(print(100.0, 700, 'buy', 50), 50); va.onTrade(print(99.9, 700, 'sell', 60), 60);
  out = va.onBook(book([['99.0', '1']], [['100.1', '1']]), 100);
  assert.deepEqual(out.map(a => [a.type, a.cause]), [['gone', 'pulled']]);
  // partly traded
  va = mk();
  va.onBook(book([['100.0', '1000']], [['100.1', '1']]), 0);
  va.onTrade(print(100.0, 200, 'sell', 10), 10);
  out = va.onBook(book([['100.0', '100']], [['100.1', '1']]), 100);
  assert.deepEqual(out.map(a => [a.type, a.cause, a.tradedQty, a.pulledQty]), [['decrease', 'partly traded', 200, 700]]);
  // small drops below the threshold accumulate from the peak; growth resets the peak
  va = mk();
  va.onBook(book([['100.0', '1000']], [['100.1', '1']]), 0);
  assert.deepEqual(va.onBook(book([['100.0', '700']], [['100.1', '1']]), 100), []);
  assert.equal(va.onBook(book([['100.0', '450']], [['100.1', '1']]), 200)[0].type, 'decrease');
});

test('decrease: a trade that arrives after the depth update (separate sockets) is credited within the grace period', () => {
  const va = new A.VolumeAlerts({ tick: 0.1, tickStr: '0.1', graceMs: 400 });
  va.configure({ decrease: true, decreasePct: 50, appearUsd: 50000, withinPct: 1 });
  va.onBook(book([['100.0', '1000']], [['100.1', '1']]), 0);
  assert.deepEqual(va.onBook(book([['99.0', '1']], [['100.1', '1']]), 100), [], 'waits for the grace period');
  assert.deepEqual(va.onTrade(print(100.0, 1000, 'sell', 150), 150), []);
  const out = va.onBook(book([['99.0', '1']], [['100.1', '1']]), 600);
  assert.deepEqual(out.map(a => [a.type, a.cause]), [['gone', 'traded']]);
});

test('decrease: a level leaving the Y % window (price moved) is untracked silently', () => {
  const va = new A.VolumeAlerts({ tick: 0.1, tickStr: '0.1', graceMs: 0 });
  va.configure({ decrease: true, decreasePct: 50, appearUsd: 50000, withinPct: 1 });
  va.onBook(book([['100.0', '1000']], [['100.1', '1']]), 0);
  assert.deepEqual(va.onBook(book([['102.0', '1']], [['102.1', '1']]), 100), []);
  assert.equal(va.tracked.size, 0);
});

test('prints: single and aggregated within N ms; once per group; rate limited per price', () => {
  const va = new A.VolumeAlerts({ tick: 0.1, tickStr: '0.1' });
  va.configure({ print: true, printUsd: 100000 });
  assert.deepEqual(va.onTrade(print(100.0, 500, 'buy', 1), 1), []);
  assert.deepEqual(va.onTrade(print(100.0, 1000, 'buy', 2), 2).map(a => [a.type, a.price, a.count]), [['print', '100.0', 1]]);
  assert.deepEqual(va.onTrade(print(100.0, 1000, 'buy', 3), 3), [], 'same (type, price) within 3 s');
  va.configure({ print: true, printUsd: 100000, printAggMs: 100 });
  const at = 10000;
  assert.deepEqual(va.onTrade(print(100.1, 400, 'sell', at), at), []);
  assert.deepEqual(va.onTrade(print(100.1, 400, 'buy', at + 10), at + 10), [], 'other side starts a new group');
  assert.deepEqual(va.onTrade(print(100.1, 400, 'buy', at + 20), at + 20), []);
  const out = va.onTrade(print(100.2, 300, 'buy', at + 90), at + 90);
  assert.deepEqual(out.map(a => [a.type, a.side, a.count, a.price]), [['print', 'buy', 3, '100.2']]);
  assert.deepEqual(va.onTrade(print(100.3, 900, 'buy', at + 95), at + 95), [], 'group already fired');
  assert.equal(va.onTrade(print(100.4, 1000, 'buy', at + 300), at + 300).length, 1, 'window passed: new group');
  const off = new A.VolumeAlerts({ tick: 0.1 });
  assert.deepEqual(off.onTrade(print(100, 1e6, 'buy', 1), 1), [], 'all volume alerts are off by default');
  assert.deepEqual(off.onBook(book([['100.0', '1e6']], [['100.1', '1']]), 1), []);
});

test('alert log: newest first, capped at 200, delete single/all, junk ignored', () => {
  const storage = L.memoryStorage();
  let t = 0;
  const log = new A.AlertLog(storage, { now: () => ++t });
  for (let k = 0; k < 205; k += 1) log.add({ exchange: 'binance', symbol: 'BTCUSDT', source: 'dom', price: String(100 + k), type: 'signal' });
  const list = log.list();
  assert.equal(list.length, 200); assert.equal(list[0].price, '304'); assert.equal(list.at(-1).price, '105');
  assert.throws(() => log.add({ exchange: 'kraken', symbol: 'X', source: 'dom', price: '1', type: 'signal' }), /invalid entry/);
  assert.equal(log.remove(list[0].id), true); assert.equal(log.list().length, 199);
  log.clear(); assert.deepEqual(log.list(), []);
  storage.setItem(A.LOG_KEY, JSON.stringify({ version: 1, entries: [{ id: 'z', at: 1, exchange: 'bybit', symbol: 'ETHUSDT', source: 'book', price: '1.5', type: 'gone', detail: 'x', evil: '<img>' }, { id: 1 }] }));
  assert.deepEqual(log.list(), [{ id: 'z', at: 1, exchange: 'bybit', symbol: 'ETHUSDT', source: 'book', price: '1.5', type: 'gone', detail: 'x' }]);
  const broken = new A.AlertLog({ getItem: () => null, setItem: () => { throw new Error('quota'); } });
  broken.add({ exchange: 'binance', symbol: 'BTCUSDT', source: 'trades', price: '1', type: 'print' });
  assert.equal(broken.list().length, 1, 'kept in memory when storage fails');
});

test('active rows: armed signals and repeat levels waiting to re-arm; fired one-shots and simple levels excluded', () => {
  const rows = A.activeRows([lvl('1', { createdAt: 1 }), lvl('2', { createdAt: 2, repeat: true, armed: false, triggeredAt: 5 }),
    lvl('3', { createdAt: 3, armed: false, triggeredAt: 4 }), lvl('4', { kind: 'simple', createdAt: 4 })]);
  assert.deepEqual(rows.map(r => [r.price, r.type, r.status.split(' ')[0]]), [['2', 'signal · repeat', 'waiting'], ['1', 'signal', 'armed']]);
});
