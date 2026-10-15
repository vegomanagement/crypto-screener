'use strict';
// Small dependency-free regressions for the actual workspace helpers.
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/workspace.js'), 'utf8');
const declarations = ['normalizeSymbol', 'validSymbol', 'validInterval'].map(name => {
  const line = source.split('\n').find(value => value.trim().startsWith(`const ${name} =`));
  assert.ok(line, `${name} must remain testable`);
  return line;
}).join('\n');
const helpers = vm.runInNewContext(`${declarations}\n({ normalizeSymbol, validSymbol, validInterval })`);

test('multi-day interval metadata is not dropped from dropdown or stored settings', () => {
  for (const interval of ['1', '12', '45', '240', '720', 'D', '2D', '3D', 'W', 'M']) {
    assert.equal(helpers.validInterval(interval), true, interval);
  }
  assert.equal(helpers.validInterval('30s'), false, 'unavailable seconds cannot be a stored active timeframe');
  assert.equal(helpers.validInterval('<script>'), false);
});

test('advertised Bybit aliases become exact API instrument symbols', () => {
  for (const value of ['BTCUSDT', 'btcusdt', 'BTC/USDT', 'BYBIT:BTCUSDT.P']) {
    const symbol = helpers.normalizeSymbol(value, 'bybit');
    assert.equal(symbol, 'BTCUSDT');
    assert.equal(helpers.validSymbol(symbol), true);
  }
  assert.equal(helpers.normalizeSymbol('BYBIT:1000BONKUSDT.P'), '1000BONKUSDT');
});

test('do not silently remap another exchange or malformed identifiers', () => {
  for (const value of ['BINANCE:BTCUSDT.P', '<img src=x>', 'BTC', 'BTCUSDT?x=1', '/BTCUSDT', 'BTCUSDT/']) {
    assert.equal(helpers.validSymbol(helpers.normalizeSymbol(value)), false, value);
  }
});

test('venue-native identifiers retain units, quote and case', () => {
  for (const symbol of ['BTC', 'ETH', 'kBONK', 'xyz:TSLA']) {
    assert.equal(helpers.normalizeSymbol(symbol, 'hyperliquid'), symbol);
    assert.equal(helpers.validSymbol(symbol, 'hyperliquid'), true);
  }
  assert.equal(helpers.validSymbol('1000BONKPERP', 'bybit'), true, 'Bybit USDC contracts use exact PERP identifiers');
  assert.equal(helpers.validSymbol('BTCPERP', 'bybit'), true);
  assert.equal(helpers.validSymbol('BTCPERP', 'binance'), false);
  assert.equal(helpers.validSymbol('BTCUSDC', 'binance'), true);
  for (const symbol of ['AUSDT', '4USDT']) {
    assert.equal(helpers.validSymbol(symbol, 'bybit'), true);
    assert.equal(helpers.validSymbol(symbol, 'binance'), true);
  }
  assert.equal(helpers.validSymbol('dex_name-1:BTC', 'hyperliquid'), true);
  assert.equal(helpers.validSymbol('币安人生USDT', 'binance'), true);
  assert.equal(helpers.validSymbol('我踏马来了USDT', 'binance'), true);
  assert.equal(helpers.validSymbol('1000BONKUSDT', 'binance'), true);
  assert.equal(helpers.validSymbol(helpers.normalizeSymbol('BYBIT:BTCUSDT.P', 'hyperliquid'), 'hyperliquid'), false);
  assert.equal(helpers.validSymbol(helpers.normalizeSymbol('BINANCE:BTCUSDT.P', 'bybit'), 'bybit'), false);
  assert.equal(helpers.validSymbol('BTC?x=1', 'hyperliquid'), false);
});

test('browser: complete venue catalogs, native pane selection, persistence and stale races', { skip: !process.env.WORKSPACE_PLAYWRIGHT }, async () => {
  const { chromium } = require(process.env.WORKSPACE_PLAYWRIGHT);
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
    // Deliberately emulate a transport ignoring AbortSignal: generation guards
    // must still reject late responses after venue/pane changes.
    await page.addInitScript(() => {
      const realFetch = window.fetch.bind(window);
      window.fetch = (url, options) => realFetch(url, { ...options, signal: undefined });
    });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const requests = [];
    const catalogs = {
      bybit: Array.from({ length: 1111 }, (_, i) => ({ symbol: `COIN${i}USDT`, base: `COIN${i}`, quote: 'USDT', display_symbol: `COIN${i}/USDT`, exchange: 'bybit', price: i + 10, change_24h: 2 })),
      binance: [{ symbol: 'BTCUSDC', base: 'BTC', quote: 'USDC', exchange: 'binance', price: 90123, change_24h: -1 }],
      hyperliquid: ['BTC', 'ETH', 'kBONK', 'xyz:TSLA'].map(symbol => ({ symbol, base: symbol, quote: 'USDC', exchange: 'hyperliquid', price: 123, change_24h: null }))
    };
    let delayBybit = false;
    let failMarkets = false;
    let delayChart = false;
    let wrongChartSource = false;
    await page.route('http://workspace.test/**', async route => {
      const url = new URL(route.request().url()); requests.push(url.pathname + url.search);
      if (url.pathname.startsWith('/api/markets')) {
        const exchange = url.searchParams.get('exchange');
        const fail = failMarkets;
        if (delayBybit && exchange === 'bybit') await new Promise(resolve => setTimeout(resolve, 250));
        return route.fulfill({ status: fail ? 503 : 200, json: fail ? { error: 'Fixture provider unavailable' } : { exchange, markets: catalogs[exchange], total: catalogs[exchange].length, as_of: Date.now(), cached: true, warnings: [] } });
      }
      if (url.pathname === '/api/chart/intervals') return route.fulfill({ json: { intervals: [{ value: '240', label: '4h' }, { value: 'D', label: 'D' }, { value: '60', label: '1h' }] } });
      if (url.pathname === '/api/chart') {
        if (delayChart && url.searchParams.get('exchange') === 'hyperliquid') await new Promise(resolve => setTimeout(resolve, 250));
        const requestedExchange = url.searchParams.get('exchange');
        const returnedSource = wrongChartSource && requestedExchange === 'bybit' ? 'binance' : requestedExchange;
        return route.fulfill({ json: { closed_only: true, exchange: returnedSource, source: returnedSource, candles: Array.from({ length: 250 }, (_, i) => ({ ts: 1700000000000 + i * 14400000, o: 100, h: 110, l: 90, c: 105, v: 1000 })), zones: [{ type: 'fvg', direction: 'bull', start_ts: 1700000000000, end_ts: 1701000000000, top: 102, bottom: 99 }] } });
      }
      if (url.pathname.startsWith('/api/')) return route.fulfill({ status: 404, json: { error: 'Unexpected API' } });
      const relative = url.pathname === '/' ? 'templates/workspace.html' : url.pathname.slice(1);
      return route.fulfill({ path: path.join(__dirname, '..', relative) });
    });
    await page.goto('http://workspace.test/');
    await page.waitForSelector('#exchange-select', { timeout: 1500 });
    await page.waitForFunction(() => document.querySelectorAll('.watchlist-row').length === 1111);
    assert.equal(await page.locator('#watchlist-count').innerText(), '1111 / 1111');
    assert.equal(await page.locator('.watchlist-price').first().innerText(), '10');
    assert.match(await page.locator('#prices-status').innerText(), /cached/);
    if (process.env.WORKSPACE_SCREENSHOT) await page.screenshot({ path: process.env.WORKSPACE_SCREENSHOT });
    await page.locator('#watchlist-search').fill('COIN1110');
    assert.equal(await page.locator('.watchlist-row').count(), 1);
    assert.equal(await page.locator('#watchlist-count').innerText(), '1 / 1111');
    await page.locator('#watchlist-search').fill('absent');
    assert.match(await page.locator('#watchlist-rows').innerText(), /No markets match/);
    await page.locator('#watchlist-search').fill('');
    await page.locator('#exchange-select').selectOption('hyperliquid');
    await page.waitForFunction(() => document.querySelectorAll('.watchlist-row').length === 4);
    assert.equal(await page.locator('.chart-pane[data-exchange="bybit"]').count(), 4, 'dropdown does not reconfigure charts');
    await page.locator('.watchlist-row[data-symbol="kBONK"]').click();
    await page.waitForSelector('.chart-pane[data-pane-id="0"][data-symbol="kBONK"][data-state="ready"]');
    assert.match(await page.locator('.chart-pane').first().innerText(), /HYPERLIQUID/);
    assert.equal(await page.locator('.chart-pane[data-exchange="bybit"]').count(), 3);
    assert.deepEqual(await page.locator('#symbol-suggestions option').evaluateAll(options => options.map(option => option.value)), ['BTC', 'ETH', 'kBONK', 'xyz:TSLA']);
    await page.locator('#exchange-select').selectOption('binance');
    await page.waitForSelector('.watchlist-row[data-symbol="BTCUSDC"]');
    assert.equal(await page.locator('#symbol-suggestions option[value="kBONK"]').count(), 1, 'suggestions follow pane, not watchlist');
    await page.locator('.watchlist-row').click();
    await page.waitForSelector('.chart-pane[data-pane-id="0"][data-symbol="BTCUSDC"][data-state="ready"]');
    delayBybit = true;
    await page.locator('#exchange-select').selectOption('bybit');
    assert.equal(await page.locator('.watchlist-row').count(), 0, 'switch clears old prices immediately');
    await page.locator('#exchange-select').selectOption('hyperliquid');
    await page.waitForSelector('.watchlist-row[data-symbol="kBONK"]');
    await page.waitForTimeout(350);
    assert.equal(await page.locator('.watchlist-row[data-exchange="bybit"]').count(), 0);
    delayChart = true;
    await page.locator('.watchlist-row[data-symbol="kBONK"]').click();
    await page.locator('#exchange-select').selectOption('binance');
    await page.waitForSelector('.watchlist-row[data-symbol="BTCUSDC"]');
    await page.locator('.watchlist-row').click();
    await page.waitForTimeout(350);
    assert.match(await page.locator('.chart-pane').first().innerText(), /BINANCE/);
    assert.doesNotMatch(await page.locator('.chart-pane').first().locator('.pane-source').innerText(), /HYPERLIQUID/);
    failMarkets = true;
    await page.locator('#exchange-select').selectOption('hyperliquid');
    await page.waitForSelector('#watchlist-retry:not([hidden])');
    assert.equal(await page.locator('.watchlist-row').count(), 0);
    assert.match(await page.locator('#prices-status').innerText(), /unavailable/);
    await page.locator('#watchlist-search').fill('BTC');
    assert.equal(await page.locator('#watchlist-count').innerText(), '— / —', 'failed catalog has unknown total, not zero');
    await page.locator('#watchlist-search').fill('');
    failMarkets = false;
    await page.locator('#watchlist-retry').click();
    await page.waitForSelector('.watchlist-row[data-symbol="xyz:TSLA"]');
    assert.equal(requests.some(url => url.startsWith('/api/markets?') && url.includes('force=true')), true);
    await page.locator('.watchlist-row[data-symbol="xyz:TSLA"]').click();
    await page.waitForSelector('.chart-pane[data-pane-id="0"][data-state="ready"]');
    await page.reload();
    await page.waitForSelector('.chart-pane[data-pane-id="0"][data-symbol="xyz:TSLA"][data-exchange="hyperliquid"][data-state="ready"]');
    for (const layout of ['1', '2cols', '2rows', '3cols', '4grid', '6grid']) {
      await page.locator('#layout-select').selectOption(layout);
      assert.equal(await page.locator('.chart-pane').count(), { '1': 1, '2cols': 2, '2rows': 2, '3cols': 3, '4grid': 4, '6grid': 6 }[layout]);
    }
    await page.locator('#ema200-toggle').check();
    await page.locator('#fvg-toggle').uncheck();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(requests.some(url => url.startsWith('/api/prices')), false);
    assert.equal(requests.some(url => url.includes('exchange=hyperliquid') && url.includes('symbol=kBONK')), true);
    assert.deepEqual(errors, []);
    await page.evaluate(() => {
      const saved = JSON.parse(localStorage.getItem('crypto-screener.workspace.v3'));
      if (saved.version !== 3 || saved.panes[0].exchange !== 'hyperliquid') throw new Error('v3 pane venue was not persisted');
      localStorage.removeItem('crypto-screener.workspace.v3');
      localStorage.setItem('crypto-screener.workspace.v1', JSON.stringify({ version: 1, layout: '2cols', active: 1, panes: [{ symbol: 'SOLUSDT', interval: '240' }, { symbol: 'ETHUSDT', interval: 'D' }], fvg: false }));
    });
    await page.reload();
    await page.waitForSelector('.chart-pane[data-pane-id="1"][data-symbol="ETHUSDT"][data-exchange="bybit"][data-state="ready"]');
    assert.equal(await page.locator('.chart-pane').count(), 2);
    assert.equal(await page.locator('.chart-pane.is-active').getAttribute('data-pane-id'), '1');
    assert.equal(await page.locator('#fvg-toggle').isChecked(), false);
    await page.setViewportSize({ width: 1440, height: 900 });
    wrongChartSource = true;
    await page.locator('#layout-select').selectOption('1');
    await page.locator('.chart-pane').click();
    await page.locator('#exchange-select').selectOption('bybit');
    await page.waitForSelector('.watchlist-row[data-exchange="bybit"]');
    await page.locator('.watchlist-row').first().click();
    await page.waitForSelector('.chart-pane[data-pane-id="0"][data-state="error"]');
    assert.match(await page.locator('.chart-pane .pane-status').innerText(), /different exchange/i);
  } finally { await browser.close(); }
});

const intervalFunction = source.slice(source.indexOf('  function intervalSeconds('), source.indexOf('  function parseCandles('));
const coordinateMethod = source.slice(source.indexOf('    coordinateForTime(seconds)'), source.indexOf('    drawZones()'));
const coordinates = vm.runInNewContext(`${intervalFunction}\nconst state = {panes:[{interval:'2D'}]};\nconst prototype = {${coordinateMethod}};\n({state, prototype})`);

test('FVG right edge remains finite on multi-day charts', () => {
  for (const [interval, duration] of [['2D', 172800], ['3D', 259200]]) {
    coordinates.state.panes[0].interval = interval;
    const pane = {id:0, candles:[{time:1700000000}], chart:{timeScale:()=>({timeToCoordinate:()=>null, logicalToCoordinate:x=>x})}};
    assert.equal(coordinates.prototype.coordinateForTime.call(pane, 1700000000 + duration), 1, interval);
  }
});

test('monthly FVG edge uses actual calendar boundary, not fixed 30 days', () => {
  coordinates.state.panes[0].interval = 'M';
  for (const [month, endMonth] of [[1,2], [2,3], [3,4]]) {
    const start = Date.UTC(2024, month, 1) / 1000;
    const end = Date.UTC(2024, endMonth, 1) / 1000;
    const pane = {id:0, candles:[{time:start}], chart:{timeScale:()=>({timeToCoordinate:()=>null, logicalToCoordinate:x=>x})}};
    assert.equal(coordinates.prototype.coordinateForTime.call(pane, end), 1);
  }
});

const smaFunction = source.slice(source.indexOf('  function smaData('), source.indexOf('  function intervalSeconds('));
const sma = vm.runInNewContext(`${smaFunction}\nsmaData`);
const compareChangeSrc = source.slice(source.indexOf('  function compareChange('), source.indexOf('  function statusText('));
const compareChange = vm.runInNewContext(`${compareChangeSrc}\ncompareChange`);

test('MA20 high/low SMA has no fake warm-up and tracks only its own OHLC field', () => {
  const candles = Array.from({ length: 25 }, (_, i) => ({ time: i, high: 10 + i, low: 5 + i }));
  assert.equal(sma(candles.slice(0, 19), 20, 'high').length, 0, 'fewer than 20 candles must plot nothing, not zeros');
  const high = sma(candles, 20, 'high');
  const low = sma(candles, 20, 'low');
  assert.equal(high.length, 6, '25 candles - 20 period + 1 = 6 points');
  assert.equal(high[0].time, candles[19].time, 'first point anchors at the 20th candle, not candle 0');
  // Manually verify the first and a later rolling window value for the high series.
  const expectedFirst = candles.slice(0, 20).reduce((sum, c) => sum + c.high, 0) / 20;
  assert.ok(Math.abs(high[0].value - expectedFirst) < 1e-9);
  const expectedLast = candles.slice(5, 25).reduce((sum, c) => sum + c.high, 0) / 20;
  assert.ok(Math.abs(high.at(-1).value - expectedLast) < 1e-9);
  assert.notDeepEqual(high.map(p => p.value), low.map(p => p.value), 'high- and low-sourced series must differ');
});

test('Chg% sort comparator always places null/NaN change last, never throws', () => {
  assert.equal(compareChange(1, 2, 'asc') < 0, true);
  assert.equal(compareChange(1, 2, 'desc') > 0, true);
  assert.equal(compareChange(null, 5, 'asc'), 1, 'null sorts after a real number ascending');
  assert.equal(compareChange(5, null, 'desc'), -1, 'null sorts after a real number descending too');
  assert.equal(compareChange(null, null, 'asc'), 0);
  assert.equal(compareChange(NaN, 3, 'asc'), 1, 'NaN treated as null, never NaN-compared');
  assert.equal(compareChange(undefined, undefined, 'desc'), 0);
});

test('browser: live price line, MA20 H/L overlay, favorites and Chg% sort', { skip: !process.env.WORKSPACE_PLAYWRIGHT }, async () => {
  const { chromium } = require(process.env.WORKSPACE_PLAYWRIGHT);
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const tickerRequests = [];
    let tickerFails = false;
    const markets = [
      { symbol: 'BTCUSDT', base: 'BTC', quote: 'USDT', exchange: 'bybit', price: 100, change_24h: 5 },
      { symbol: 'ETHUSDT', base: 'ETH', quote: 'USDT', exchange: 'bybit', price: 50, change_24h: -3 },
      { symbol: 'SOLUSDT', base: 'SOL', quote: 'USDT', exchange: 'bybit', price: 20, change_24h: null },
      { symbol: 'XRPUSDT', base: 'XRP', quote: 'USDT', exchange: 'bybit', price: 1, change_24h: 10 }
    ];
    await page.route('http://workspace.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/ticker') {
        tickerRequests.push(url.search);
        if (tickerFails) return route.fulfill({ status: 502, json: { error: 'Exchange ticker unavailable' } });
        return route.fulfill({ json: { symbol: url.searchParams.get('symbol'), price: 12345.6, change_24h: 1.23, as_of: Date.now() } });
      }
      if (url.pathname.startsWith('/api/markets')) {
        const exchange = url.searchParams.get('exchange');
        return route.fulfill({ json: { exchange, markets: exchange === 'bybit' ? markets : [], total: exchange === 'bybit' ? markets.length : 0, as_of: Date.now(), cached: true, warnings: [] } });
      }
      if (url.pathname === '/api/chart/intervals') return route.fulfill({ json: { intervals: [{ value: '240', label: '4h' }] } });
      if (url.pathname === '/api/chart') {
        return route.fulfill({ json: { closed_only: true, exchange: url.searchParams.get('exchange'), source: url.searchParams.get('exchange'), candles: Array.from({ length: 30 }, (_, i) => ({ ts: 1700000000000 + i * 14400000, o: 100 + i, h: 110 + i, l: 90 + i, c: 105 + i, v: 1000 })), zones: [] } });
      }
      if (url.pathname.startsWith('/api/')) return route.fulfill({ status: 404, json: { error: 'Unexpected API' } });
      const relative = url.pathname === '/' ? 'templates/workspace.html' : url.pathname.slice(1);
      return route.fulfill({ path: path.join(__dirname, '..', relative) });
    });
    await page.goto('http://workspace.test/');
    await page.waitForFunction(() => document.querySelectorAll('.watchlist-row').length === 4);

    // 1) Live price line: ticker fetched per active pane, shown and labeled.
    await page.waitForFunction(() => document.querySelector('.chart-pane[data-pane-id="0"] .pane-live-price')?.dataset.state === 'ready');
    assert.match(await page.locator('.chart-pane[data-pane-id="0"] .pane-live-price').innerText(), /Live/);
    assert.equal(tickerRequests.some(q => q.includes('symbol=BTCUSDT')), true);
    tickerFails = true;
    await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 0)));
    await page.locator('.pane-reload').first().click();
    await page.waitForFunction(() => document.querySelector('.chart-pane[data-pane-id="0"] .pane-live-price')?.dataset.state === 'hidden');
    assert.equal(await page.locator('.chart-pane[data-pane-id="0"] .pane-live-price').innerText(), '', 'unavailable ticker is hidden, never invents a price');
    tickerFails = false;

    // 2) Dual MA20 H/L overlay: off by default, togglable independently.
    assert.equal(await page.locator('#ma20-high-toggle').isChecked(), false);
    assert.equal(await page.locator('#ma20-low-toggle').isChecked(), false);
    await page.locator('#ma20-high-toggle').check();
    assert.equal(await page.evaluate(() => document.querySelector('#ma20-high-toggle').checked), true);
    await page.reload();
    await page.waitForFunction(() => document.querySelectorAll('.watchlist-row').length === 4);
    assert.equal(await page.locator('#ma20-high-toggle').isChecked(), true, 'ma20hl toggle persists across reload (v3 schema)');
    assert.equal(await page.locator('#ma20-low-toggle').isChecked(), false);

    // 3) Favorites: star toggles without selecting the row; persists; pins to top.
    const ethRow = page.locator('.watchlist-row[data-symbol="ETHUSDT"]');
    await ethRow.locator('.watchlist-star').click();
    assert.equal(await ethRow.locator('.watchlist-star').getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('.chart-pane.is-active').getAttribute('data-symbol'), 'BTCUSDT', 'star click must not select the row/market');
    assert.equal(await page.locator('.watchlist-row').first().getAttribute('data-symbol'), 'ETHUSDT', 'favorite pinned to top');
    await page.locator('#watchlist-favorites-toggle').click();
    assert.equal(await page.locator('.watchlist-row').count(), 1);
    assert.equal(await page.locator('.watchlist-row').first().getAttribute('data-symbol'), 'ETHUSDT');
    await page.locator('#watchlist-favorites-toggle').click();
    assert.equal(await page.locator('.watchlist-row').count(), 4, 'toggling off favorites-only restores full list');
    await page.locator('#exchange-select').selectOption('binance');
    await page.locator('#exchange-select').selectOption('bybit');
    await page.waitForFunction(() => document.querySelectorAll('.watchlist-row').length === 4);
    assert.equal(await ethRow.locator('.watchlist-star').getAttribute('aria-pressed'), 'true', 'favorites survive an exchange switch');

    // 4) Sortable Chg% column: asc -> desc -> none cycle, nulls last, favorite still pinned.
    await page.locator('#watchlist-sort-change').click();
    assert.equal(await page.locator('#watchlist-sort-change').getAttribute('aria-sort'), 'ascending');
    let symbols = await page.locator('.watchlist-row').evaluateAll(rows => rows.map(r => r.dataset.symbol));
    assert.deepEqual(symbols, ['ETHUSDT', 'BTCUSDT', 'XRPUSDT', 'SOLUSDT'], 'favorite first, then ascending change (5 < 10), null last');
    await page.locator('#watchlist-sort-change').click();
    assert.equal(await page.locator('#watchlist-sort-change').getAttribute('aria-sort'), 'descending');
    symbols = await page.locator('.watchlist-row').evaluateAll(rows => rows.map(r => r.dataset.symbol));
    assert.deepEqual(symbols, ['ETHUSDT', 'XRPUSDT', 'BTCUSDT', 'SOLUSDT'], 'descending (10 > 5) still keeps null last');
    await page.locator('#watchlist-sort-change').click();
    assert.equal(await page.locator('#watchlist-sort-change').getAttribute('aria-sort'), 'none');
    symbols = await page.locator('.watchlist-row').evaluateAll(rows => rows.map(r => r.dataset.symbol));
    assert.deepEqual(symbols, ['ETHUSDT', 'BTCUSDT', 'SOLUSDT', 'XRPUSDT'], 'back to catalog order, favorite still pinned');

    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
