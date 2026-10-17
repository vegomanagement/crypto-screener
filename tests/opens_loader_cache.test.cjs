// Regression: the Key levels / Opens higher-timeframe loader kept every (exchange, symbol, tf)
// entry forever, so each pane's cache grew with every market visited (live: 48 entries after
// 30 symbol switches, ~55 KB each). Expired entries must be dropped.
const test = require('node:test');
const assert = require('node:assert/strict');
const O = require('../static/indicators/opens.js');

test('createLoader drops expired entries instead of growing per visited symbol', async () => {
  let t = 0;
  const fetch = async url => {
    const q = new URLSearchParams(url.split('?')[1]);
    const candles = [{ ts: 1_700_000_000_000, o: '1', h: '2', l: '0.5', c: '1.5', v: '1' }];
    return { ok: true, status: 200, json: async () => (url.startsWith('/api/chart/live')
      ? { candle: null } : { exchange: q.get('exchange'), closed_only: true, candles }) };
  };
  const loader = O.createLoader({ fetch, ttlMs: 60000, now: () => t });
  for (let i = 0; i < 20; i += 1) {
    await loader.ensure('binance', `SYM${i}USDT`, ['D', 'W']);
    t += 61000;
  }
  assert.ok(loader.cache.size <= 2, `cache holds ${loader.cache.size} entries after 20 symbols`);
  const again = await loader.ensure('binance', 'SYM19USDT', ['D']);
  assert.ok(Array.isArray(again.D));
});
