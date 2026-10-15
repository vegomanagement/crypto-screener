'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const file = require('node:path').join(__dirname, '../static/market-stream.js');
function api() { assert.ok(fs.existsSync(file), 'isolated market-stream module exists'); return require(file); }
class Socket {
  static instances = [];
  constructor(url) { this.url = url; this.readyState = 0; this.sent = []; Socket.instances.push(this); }
  send(data) { this.sent.push(JSON.parse(data)); }
  open() { this.readyState = 1; this.onopen?.(); }
  message(data) { this.onmessage?.({ data: JSON.stringify(data) }); }
  close() { this.readyState = 3; this.onclose?.(); }
}
function setup() {
  Socket.instances = [];
  let time = 1700000000000, id = 0;
  const tasks = new Map();
  const hub = api().createHub({ WebSocket: Socket, now: () => time, random: () => 0.5,
    setTimeout: (fn, delay) => { tasks.set(++id, { fn, delay }); return id; }, clearTimeout: n => tasks.delete(n) });
  return { hub, tasks, advance: ms => { time += ms; }, run: n => { const task = tasks.get(n); tasks.delete(n); task.fn(); } };
}
test('rejects wrong instrument/interval, malformed OHLCV and regressive timestamps; rollover is real', () => {
  const { hub } = setup(), events = []; const off = hub.subscribe({ venue: 'bybit', symbol: 'BTCUSDT', interval: '1' }, e => events.push(e));
  const ws = Socket.instances[0]; ws.open(); ws.message(bybit());
  ws.message({ ...bybit(), topic: 'kline.1.ETHUSDT' });
  const wrong = bybit(); wrong.data[0].interval = 'D'; ws.message(wrong);
  ws.message(bybit(1700000040000, 1700000040099, '103'));
  const malformed = bybit(); malformed.data[0].high = '10'; ws.message(malformed);
  ws.message(bybit(1699999980000, 1700000040300));
  ws.message(bybit(1700000040000, 1700000040400, '104', true));
  ws.message(bybit(1700000100000, 1700000100100, '100'));
  assert.deepEqual(events.filter(e => e.type === 'candle').map(e => [e.candle.time, e.closed]), [[1700000040, false], [1700000040, true], [1700000100, false]]);
  off();
});
test('disconnect retries with bounded jitter and ignores old socket after reconnect/dispose', () => {
  const { hub, tasks, run } = setup(), events = [];
  const off = hub.subscribe({ venue: 'bybit', symbol: 'BTCUSDT', interval: '1' }, e => events.push(e));
  const first = Socket.instances[0]; first.open(); first.close();
  assert.equal(tasks.size, 1); const retry = [...tasks][0]; assert.ok(retry[1].delay >= 750 && retry[1].delay <= 1000);
  run(retry[0]); const second = Socket.instances[1]; second.open();
  first.message(bybit()); assert.equal(events.filter(e => e.type === 'candle').length, 0);
  second.message(bybit()); assert.equal(events.filter(e => e.type === 'candle').length, 1);
  off(); second.message(bybit()); assert.equal(events.filter(e => e.type === 'candle').length, 1); assert.equal(tasks.size, 0);
});
test('Binance Unicode URL encoding and USDC stay exact; Hyperliquid uses native coin and daily interval', () => {
  const { hub } = setup(), events = [];
  const symbol = '币安人生USDC'; const off = hub.subscribe({ venue: 'binance', symbol, interval: 'D' }, e => events.push(e));
  const ws = Socket.instances[0]; assert.equal(ws.url, `wss://fstream.binance.com/market/ws/${encodeURIComponent(symbol.toLowerCase())}@kline_1d`); ws.open();
  ws.message({ e: 'kline', E: 1700000040100, s: symbol, k: { s: symbol, i: '1d', t: 1700000040000, T: 1700086439999, o: '100', h: '105', l: '99', c: '103', v: '12', x: false } });
  assert.equal(events.at(-1).candle.close, 103); off();
  const hl = hub.subscribe({ venue: 'hyperliquid', symbol: 'kBONK', interval: 'D' }, e => events.push(e));
  const h = Socket.instances[1]; h.open(); assert.deepEqual(h.sent[0].subscription, { type: 'candle', coin: 'kBONK', interval: '1d' });
  h.message({ channel: 'candle', data: { s: 'kBONK', i: '1d', t: 1700000040000, T: 1700086439999, o: '100', h: '105', l: '99', c: '104', v: '13' } });
  assert.equal(events.at(-1).candle.close, 104); hl();
});
test('heartbeat detects silent disconnects and reconnect delay stays bounded', () => {
  const { hub, tasks, run, advance } = setup(); const events=[];
  const off=hub.subscribe({venue:'hyperliquid',symbol:'BTC',interval:'1'},e=>events.push(e));
  const ws=Socket.instances[0]; ws.open();
  run([...tasks.keys()][0]); assert.deepEqual(ws.sent.at(-1), {method:'ping'});
  advance(76000); run([...tasks.keys()][0]);
  assert.equal(events.at(-1).status,'disconnected');
  for(let i=0;i<10;i++) { const [id,task]=[...tasks][0]; assert.ok(task.delay>0 && task.delay<=30000); run(id); Socket.instances.at(-1).close(); }
  off(); assert.equal(tasks.size,0);
});
test('pending handshake has a deadline, preventing a permanently connecting pane', () => {
  const { hub, tasks, run }=setup(); const off=hub.subscribe({venue:'bybit',symbol:'BTCUSDT',interval:'1'},()=>{});
  assert.equal(tasks.size,1); const [id,task]=[...tasks][0]; assert.ok(task.delay<=20000); run(id);
  assert.equal(Socket.instances[0].readyState,3); assert.equal(tasks.size,1); off(); assert.equal(tasks.size,0);
});
const config = { venue: 'bybit', symbol: 'BTCUSDT', interval: '1' };
function bybit(start = 1700000040000, ts = start + 100, close = '101', confirm = false) {
  return { topic: 'kline.1.BTCUSDT', ts, data: [{ start, end: start + 59999, interval: '1', open: '100', high: '105', low: '99', close, volume: '10', confirm }] };
}
test('shared venue/symbol/interval subscription emits real intrabar OHLCV and releases by refcount', () => {
  const { hub, tasks } = setup(), a = [], b = [];
  const offA = hub.subscribe(config, event => a.push(event));
  const offB = hub.subscribe(config, event => b.push(event));
  assert.equal(Socket.instances.length, 1);
  const ws = Socket.instances[0]; ws.open();
  assert.deepEqual(ws.sent[0], { op: 'subscribe', args: ['kline.1.BTCUSDT', 'publicTrade.BTCUSDT'] });
  ws.message(bybit()); ws.message(bybit(1700000040000, 1700000040200, '102'));
  assert.deepEqual(a.filter(e => e.type === 'candle').map(e => e.candle.close), [101, 102]);
  assert.equal(b.filter(e => e.type === 'candle').length, 2);
  assert.equal(a.at(-1).candle.volume, 10);
  offA(); assert.equal(ws.readyState, 1); offB(); assert.equal(ws.readyState, 3);
  assert.equal(tasks.size, 0, 'last release removes heartbeat/retry timers');
});
