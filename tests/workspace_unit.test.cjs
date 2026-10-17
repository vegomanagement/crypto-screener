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
  for (const interval of ['1s', '5s', '15s', '30s']) assert.equal(helpers.validInterval(interval), true, `trade-built ${interval}`);
  for (const interval of ['2s', '60s', '0s', 's']) assert.equal(helpers.validInterval(interval), false, interval);
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
    await page.routeWebSocket(/wss:/, socket => socket.close()); // fixtures never depend on real venues
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
      const saved = JSON.parse(localStorage.getItem('crypto-screener.workspace.v4'));
      if (saved.version !== 4 || saved.panes[0].exchange !== 'hyperliquid') throw new Error('v4 pane venue was not persisted');
      localStorage.removeItem('crypto-screener.workspace.v4');
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
const logicalMethod = source.slice(source.indexOf('    logicalForTime(seconds)'), source.indexOf('    edgeCoordinate(seconds)'));
const edgeMethod = source.slice(source.indexOf('    edgeCoordinate(seconds)'), source.indexOf('    effectiveFvgZones(settings)'));
const coordinates = vm.runInNewContext(`${intervalFunction}\nconst state = {panes:[{interval:'2D'}]};\nconst prototype = {${logicalMethod},\n${edgeMethod}};\n({state, prototype})`);
// Lightweight Charts' real logicalToCoordinate only accepts INTEGER logical
// indices (confirmed empirically against the vendored library: fractional
// calls silently return 0, not an interpolated pixel). The mock enforces
// that too, so a regression that re-introduces a fractional call fails loudly.
const makeScale = (originPx, barSpacing) => ({
  options: () => ({ barSpacing }),
  logicalToCoordinate: logical => {
    assert.ok(Number.isInteger(logical), `logicalToCoordinate must only be called with an integer (got ${logical})`);
    return originPx + logical * barSpacing;
  }
});
const makePane = (candles, originPx, barSpacing) => Object.assign(Object.create(coordinates.prototype), { id: 0, candles, chart: { timeScale: () => makeScale(originPx, barSpacing) } });

test('FVG right edge remains finite on multi-day charts', () => {
  for (const [interval, duration] of [['2D', 172800], ['3D', 259200]]) {
    coordinates.state.panes[0].interval = interval;
    const pane = makePane([{ time: 1700000000 }], 0, 10);
    // edgeCoordinate returns a bar BOUNDARY (logical - 0.5), not the center:
    // one full bar past the single candle's center (logical 1) lands its
    // left edge at logical 0.5 -> pixel 5 with barSpacing 10, not drifting.
    assert.equal(pane.edgeCoordinate(1700000000 + duration), 5, interval);
  }
});

test('monthly FVG edge uses actual calendar boundary, not fixed 30 days', () => {
  coordinates.state.panes[0].interval = 'M';
  for (const [month, endMonth] of [[1, 2], [2, 3], [3, 4]]) {
    const start = Date.UTC(2024, month, 1) / 1000;
    const end = Date.UTC(2024, endMonth, 1) / 1000;
    const pane = makePane([{ time: start }], 0, 10);
    assert.equal(pane.edgeCoordinate(end), 5);
  }
});

// Regression for the confirmed pixel bug (docs/INDICATOR_PLUGIN_PLAN.md):
// the FVG box's left edge must land exactly on the confirming (3rd) candle's
// own left edge pixel, not float ~1 bar to its right; the right edge must
// stop exactly at the last closed candle's right edge, not drift past it.
// Lightweight Charts' logicalToCoordinate is an affine map of the logical
// index (center-of-bar-i -> originPx + i*barSpacingPx); simulate that
// exactly so the assertion is a real pixel computation, not a guess.
test('FVG box edges land flush on the real candle slots (pixel-exact, not ~1 bar off)', () => {
  coordinates.state.panes[0].interval = '240'; // 4h, step = 14400s
  const step = 14400;
  const originPx = 40;
  const barSpacingPx = 12;
  const candleCount = 10;
  const candles = Array.from({ length: candleCount }, (_, i) => ({ time: 1700000000 + i * step }));
  const pane = makePane(candles, originPx, barSpacingPx);
  const confirmingIndex = 5; // the FVG's 3rd/confirming candle
  const candleCenterPx = originPx + confirmingIndex * barSpacingPx;
  const candleLeftPx = candleCenterPx - barSpacingPx / 2;
  // Box left edge: edgeCoordinate(confirming candle's own open time).
  const boxLeftPx = pane.edgeCoordinate(candles[confirmingIndex].time);
  assert.equal(boxLeftPx, candleLeftPx, 'box left edge must equal the candle slot left edge exactly, no ~1 bar drift');
  assert.ok(boxLeftPx >= candleLeftPx - 0.001 && boxLeftPx < candleLeftPx + barSpacingPx * 0.5, 'touching, not floating a bar away');
  // Box right edge: edgeCoordinate(close time of the LAST candle), which is
  // the open-time boundary of a hypothetical next candle.
  const lastIndex = candleCount - 1;
  const lastCandleRightPx = (originPx + lastIndex * barSpacingPx) + barSpacingPx / 2;
  const boxRightPx = pane.edgeCoordinate(candles[lastIndex].time + step);
  assert.equal(boxRightPx, lastCandleRightPx, 'right edge must stop exactly at the last closed candle, not drift past it');
  assert.ok(boxRightPx <= lastCandleRightPx + 0.001, 'must never extend past the last closed candle into empty space');
});

const fvgZonesFunction = source.slice(source.indexOf('  function fvgZones('), source.indexOf('  const INDICATOR_DEFS'));
const fvgZonesCtx = vm.runInNewContext(`${fvgZonesFunction}\nfvgZones`);

test('client-side fvgZones mirrors the backend 3-candle algorithm (wick policy, default)', () => {
  // bull gap: candle[2].low(130) > candle[0].high(110); only 3 candles so no
  // later mitigation window runs.
  const candles = [
    { time: 0, open: 100, high: 110, low: 95, close: 105 },
    { time: 1, open: 108, high: 115, low: 100, close: 112 },
    { time: 2, open: 132, high: 140, low: 130, close: 138 }
  ];
  const zones = fvgZonesCtx(candles, { fillPolicy: 'wick', minGapPct: 0 });
  assert.equal(zones.length, 1);
  assert.equal(zones[0].direction, 'bull');
  assert.equal(zones[0].top, 130);
  assert.equal(zones[0].bottom, 110);
  assert.equal(zones[0].startTime, 2, 'startTime is the confirming (3rd) candle\'s own open time');
});

test('fvgZones minGapPct filter drops gaps smaller than the threshold', () => {
  const candles = [
    { time: 0, open: 100, high: 100.1, low: 99.9, close: 100 },
    { time: 1, open: 100, high: 100.2, low: 99.9, close: 100.1 },
    { time: 2, open: 100.15, high: 100.3, low: 100.12, close: 100.2 } // tiny bull gap (~0.02%)
  ];
  assert.equal(fvgZonesCtx(candles, { fillPolicy: 'wick', minGapPct: 0 }).length, 1, 'default filter (0) keeps any gap');
  assert.equal(fvgZonesCtx(candles, { fillPolicy: 'wick', minGapPct: 1 }).length, 0, '1% filter drops a ~0.02% gap');
});

test('fvgZones close fill policy mitigates on candle close, not wick, as documented', () => {
  const candles = [
    { time: 0, open: 100, high: 110, low: 95, close: 105 },
    { time: 1, open: 108, high: 115, low: 100, close: 112 },
    { time: 2, open: 132, high: 140, low: 130, close: 138 },
    // later wick dips to 108 (fully closes the 110-130 gap: wick policy drops
    // the zone entirely) but closes at 128 (gap 110-128 survives under close policy)
    { time: 3, open: 128, high: 130, low: 108, close: 128 }
  ];
  const wick = fvgZonesCtx(candles, { fillPolicy: 'wick', minGapPct: 0 });
  const close = fvgZonesCtx(candles, { fillPolicy: 'close', minGapPct: 0 });
  assert.equal(wick.length, 0, 'wick low (108) fully mitigates the zone (108 <= bottom 110): dropped entirely');
  assert.equal(close.length, 1, 'close (128) never reaches bottom (110): zone survives under close policy');
  assert.equal(close[0].top, 128);
  assert.equal(close[0].bottom, 110);
});


test('local FVG transitions only newly closed bars, records creation and full fill without historical replay', () => {
  const start = source.indexOf('  function localFvgEvents(');
  assert.ok(start >= 0, 'local FVG transition helper exists');
  const fn = vm.runInNewContext(`${fvgZonesFunction}\n${source.slice(start, source.indexOf('  const localAlertJournal', start))}\nlocalFvgEvents`);
  const c = (time, high, low, close) => ({ time, open: close, high, low, close, volume: 1 });
  const history = [c(1, 105, 95, 100), c(2, 110, 99, 105)];
  const gap = c(3, 135, 130, 132), fill = c(4, 135, 100, 110);
  assert.equal(fn([], [...history, gap], {}).length, 0, 'initial historical load is a silent baseline');
  assert.equal(fn(history, [...history, gap], {})[0].type, 'created');
  assert.equal(fn([...history, gap], [...history, gap, fill], {})[0].type, 'fully filled');
  assert.equal(fn([...history, gap], [...history, gap], {}).length, 0, 'same snapshot has no events');
});

test('generic band renderer paints segmented edge-time polygons and independent inner/outer boundaries', () => {
  const body = source.slice(source.indexOf('    drawBands(context,'), source.indexOf('    // Bar-center coordinate'));
  const fills = [], strokes = [];
  let points = [];
  const context = { save() {}, restore() {}, beginPath() { points = []; }, rect() {}, clip() {}, moveTo(x,y) { points.push([x,y]); }, lineTo(x,y) { points.push([x,y]); }, closePath() {}, fill() { fills.push(points.slice()); }, stroke() { strokes.push(points.slice()); } };
  let durationHint;
  const def = { id: 'generic', kind: 'band', compute: (candles, settings) => { durationHint = settings.intervalSeconds; return ({ timeCoordinates: 'edges', segments: [
    { upper:[{time:1,value:10},{time:2,value:10}], lower:[{time:1,value:5},{time:2,value:5}] },
    { upper:[{time:4,value:10},{time:5,value:10}], lower:[{time:4,value:5},{time:5,value:5}] }
  ] }); } };
  const prototype = vm.runInNewContext(`${intervalFunction}\nconst state = { panes: [{ interval: '240' }] }; const INDICATOR_DEFS=[def], isIndicatorEnabled=()=>true, getIndicatorSettings=()=>({color:'#ff0000',opacity:0.1,showOuter:false,innerBoundary:'lower'}); const scaleAlpha=()=> 'rgba(255,0,0,0.1)'; ({${body}})`, { def });
  const pane = Object.assign(Object.create(prototype), { id: 0, candles: [{time:1}], series: { priceToCoordinate: n => n }, edgeCoordinate: n => n*10-5, edgeCoordinateCenter: n => n*10 });
  pane.drawBands(context, 100, 100);
  assert.equal(durationHint, 14400, '4h pane duration reaches band compute for coarse sessions');
  assert.equal(fills.length, 2, 'two disjoint sessions must not join across a gap');
  assert.equal(fills[0][0][0], 5, 'session boundaries use bar edges, not centers');
  assert.equal(strokes.length, 2, 'visible inner boundary per segment, optional outer hidden');
});

const smaFunction = source.slice(source.indexOf('  const PRICE_SOURCE ='), source.indexOf('  function fvgZones('));
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

function settingsHarness(saved, inputs = []) {
  const storage = new Map([['crypto-screener.workspace.v3', JSON.stringify(saved)]]);
  const extras = require('../static/indicator-extras.js');
  let saves = 0, redraws = 0;
  const code = source.slice(source.indexOf('  const STORAGE_KEY'), source.indexOf('  const $ =')) +
    source.slice(source.indexOf('  const defaultPanes'), source.indexOf('  function resetIndicatorSettings')) +
    `\nconst settingsPopoverId = 'ema7', settingsPopover = {}; const $all = () => inputs; const panes = new Map([[0, {applyIndicators() { redraw(); }}]]);\n` +
    source.slice(source.indexOf('  function applySettingsPopover()'), source.indexOf('  function resetSettingsPopover()')) +
    '\n({state, sanitizeFieldValue, setIndicatorSettings, applySettingsPopover, emaData, smaData})';
  const api = vm.runInNewContext(code, { window: { WorkspaceIndicatorExtras: extras }, inputs,
    redraw: () => redraws++, localStorage: { getItem: k => storage.get(k), setItem: (k,v) => { saves++; storage.set(k,v); } } });
  return { api, storage, counts: () => [saves, redraws] };
}

test('integer settings sanitize and migrate fractional periods to documented defaults without truncating fractional fields', () => {
  const h = settingsHarness({ version: 3, layout: '1', indicatorSettings: { ema7: { period: 7.5 }, fvg: { minGapPct: 0.01 }, 'turtle-upper': { inner: 5.6 } } });
  assert.equal(h.api.state.indicatorSettings.ema7.period, 7);
  assert.equal(h.api.state.indicatorSettings.fvg.minGapPct, 0.01);
  assert.equal(h.api.state.indicatorSettings['turtle-upper'].inner, 5.6);
  h.api.setIndicatorSettings('ema7', { period: 7.5 });
  assert.equal(JSON.parse(h.storage.get('crypto-screener.workspace.v4')).indicatorSettings.ema7.period, 7);
});

test('Apply rejects invalid fractional integer drafts before saving or recomputing', () => {
  let reported = 0;
  const input = { dataset: { key: 'period' }, value: '7.5', validity: { stepMismatch: true }, checkValidity: () => false, reportValidity: () => reported++ };
  const h = settingsHarness({ version:4, layout:'1' }, [input]);
  h.api.applySettingsPopover();
  assert.deepEqual(h.counts(), [0, 0]);
  assert.equal(h.api.state.indicatorSettings.ema7.period, 7);
  assert.equal(reported, 1);
  input.value = '10'; input.checkValidity = () => true;
  h.api.applySettingsPopover();
  assert.deepEqual(h.counts(), [1, 1]);
  assert.equal(JSON.parse(h.storage.get('crypto-screener.workspace.v4')).indicatorSettings.ema7.period, 10);
});

test('EMA and SMA defensively reject nonpositive, nonfinite and fractional periods', () => {
  const h = settingsHarness({ version:4, layout:'1' });
  const candles = Array.from({length:12}, (_,time) => ({time, close:100, high:110}));
  for (const period of [7.5, 0, -1, NaN, Infinity]) {
    assert.equal(h.api.emaData(candles, period).length, 0, `EMA ${period}`);
    assert.equal(h.api.smaData(candles, period, 'high').length, 0, `SMA ${period}`);
  }
});

function resyncHarness() {
  const timers = new Map(); let clock = 0, next = 0;
  const methods = source.slice(source.indexOf('    requestResync(bar)'), source.indexOf('    async fetchTicker()'));
  const stop = source.slice(source.indexOf('    stopTicker(removeLine'), source.indexOf('    renderStreamStatus()'));
  const api = vm.runInNewContext(`${intervalFunction}\nconst state = {panes:[{interval:'240'}]}; (class {${stop}\n${methods}}).prototype`, {
    setTimeout: (fn, delay) => { const id = ++next; timers.set(id, {fn, at:clock+delay, delay}); return id; },
    clearTimeout: id => timers.delete(id), clearInterval() {}
  });
  const pane = Object.assign(Object.create(api), { id:0, candles:[{time:14400}], tickerGeneration:0, status:{textContent:''},
    renderForming() { this.renders = (this.renders || 0) + 1; }, removeLivePrice() {} });
  return { pane, timers, async tick() { assert.equal(timers.size, 1, 'exactly one bounded retry timer'); const [id,t] = timers.entries().next().value; timers.delete(id); clock=t.at; await t.fn(); return t.delay; } };
}

test('gap resync retries failed or unchanged closed history without new WS messages, then resets after bridge', async () => {
  for (const firstFails of [true, false]) {
    const h = resyncHarness(); const p = h.pane;
    let calls = 0, release;
    p.load = async () => { calls++; if (calls === 1 && firstFails) throw new Error('network'); if (calls === 2) await new Promise(resolve => { release = resolve; }); if (calls === 3) p.candles.push({time:28800}); };
    const candle = {time:43200, open:1, high:2, low:1, close:2, volume:1};
    p.acceptForming(candle, 'WS');
    assert.match(p.status.textContent, /STALE.*gap/i, 'unbridged history must not look complete while retrying');
    for (let i=0;i<20;i++) p.acceptForming(candle, 'WS');
    const delay1 = await h.tick();
    assert.equal(calls, 1);
    assert.equal(h.timers.size, 1, 'failure or stale snapshot schedules autonomous retry');
    const second = h.tick();
    assert.equal(calls, 2);
    for (let i=0;i<20;i++) p.acceptForming(candle, 'WS');
    assert.equal(h.timers.size, 0, 'in-flight retry cannot spawn concurrent requests');
    release(); const delay2 = await second;
    assert.ok(delay2 > delay1, 'retries back off');
    await h.tick();
    assert.equal(calls, 3);
    assert.equal(h.timers.size, 0, 'bridge stops retries');
    assert.equal(p.lastResyncBar, null);
    assert.equal(p.resyncAttempts, 0);
    assert.deepEqual(p.candles.map(c=>c.time), [14400,28800], 'no manufactured closed bars');
    p.acceptForming(candle, 'WS'); assert.equal(p.renders, 1, 'real forming bar renders only after bridge');
  }
});

test('gap resync is capped with explicit error, avoids active history requests and cancels on ticker switch/dispose', async () => {
  const h = resyncHarness(); const p = h.pane; let calls = 0;
  p.load = async () => { calls++; };
  p.request = {}; p.acceptForming({time:43200}, 'WS'); await h.tick();
  assert.equal(calls, 0, 'existing history load is not aborted/duplicated');
  p.request = null;
  const delays = [];
  for (let i=0;h.timers.size && i<10;i++) delays.push(await h.tick());
  assert.equal(h.timers.size, 0, 'retry budget terminates even without WS messages');
  assert.equal(calls, 6, 'all six bounded recovery attempts are allowed');
  assert.deepEqual(delays, [700, 1400, 2800, 5600, 11200, 22400]);
  assert.match(p.status.textContent, /gap.*Reload/i);
  for (let i=0;i<20;i++) p.acceptForming({time:43200}, 'WS');
  assert.equal(h.timers.size, 0, 'messages cannot restart exhausted budget');
  p.stopTicker(false);
  assert.equal(p.lastResyncBar, null); assert.equal(p.resyncAttempts, 0);
  p.acceptForming({time:43200}, 'WS'); assert.equal(h.timers.size, 1);
  p.stopTicker(false); assert.equal(h.timers.size, 0, 'symbol/interval stop cancels a pending retry');
  assert.equal(p.lastResyncBar, null); assert.equal(p.resyncAttempts, 0);
  p.acceptForming({time:43200}, 'WS'); assert.equal(h.timers.size, 1);
  p.disposed = true; p.stopTicker(false); assert.equal(h.timers.size, 0);
});

test('gap retry completion from an old symbol cannot re-arm after stopTicker', async () => {
  const h = resyncHarness(); const p = h.pane; let release;
  p.load = () => new Promise(resolve => { release = resolve; });
  p.acceptForming({time:43200}, 'WS'); const running = h.tick();
  p.stopTicker(false); release(); await running;
  assert.equal(h.timers.size, 0); assert.equal(p.lastResyncBar, null);
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

test('browser: no cached ticker relabelled live, MA20 H/L overlay, favorites and Chg% sort', { skip: !process.env.WORKSPACE_PLAYWRIGHT }, async () => {
  const { chromium } = require(process.env.WORKSPACE_PLAYWRIGHT);
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
    await page.routeWebSocket(/wss:/, socket => socket.close()); // fixtures never depend on real venues
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

    // Missing real forming/WS data never falls back to the cached ticker API.
    assert.equal(tickerRequests.length, 0);
    assert.equal(await page.locator('.chart-pane[data-pane-id="0"] .pane-live-price').innerText(), '', 'no manufactured price from the cached ticker fixture');

    // 2) Dual MA20 H/L overlay: off by default, togglable independently.
    assert.equal(await page.locator('#ma20-high-toggle').isChecked(), false);
    assert.equal(await page.locator('#ma20-low-toggle').isChecked(), false);
    await page.locator('#ma20-high-toggle').check();
    assert.equal(await page.evaluate(() => document.querySelector('#ma20-high-toggle').checked), true);
    await page.reload();
    await page.waitForFunction(() => document.querySelectorAll('.watchlist-row').length === 4);
    assert.equal(await page.locator('#ma20-high-toggle').isChecked(), true, 'ma20hl toggle persists across reload (v4 schema)');
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

test('browser: real forming REST bootstrap + shared WS intrabar, rollover separation, plugin controls and local alerts', { skip: !process.env.WORKSPACE_PLAYWRIGHT }, async () => {
  const { chromium } = require(process.env.WORKSPACE_PLAYWRIGHT);
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
    await page.routeWebSocket(/wss:/, socket => socket.close()); // fixtures never depend on real venues
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    const start = 1700000040000;
    let closedCount = 3, requests = 0;
    const closedExtra = [];
    await page.addInitScript(() => {
      window.__sockets = [];
      window.WebSocket = class {
        constructor(url) { this.url = url; this.readyState = 0; this.sent = []; window.__sockets.push(this); setTimeout(() => { this.readyState = 1; this.onopen?.(); }, 0); }
        send(data) { this.sent.push(JSON.parse(data)); }
        close() { this.readyState = 3; this.onclose?.(); }
        message(data) { this.onmessage?.({ data: JSON.stringify(data) }); }
      };
      localStorage.setItem('crypto-screener.workspace.v4', JSON.stringify({ version: 4, layout: '2cols', panes: [{ exchange: 'bybit', symbol: 'BTCUSDT', interval: '1' }, { exchange: 'bybit', symbol: 'BTCUSDT', interval: '1' }] }));
    });
    await page.route('http://workspace.test/**', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/chart') { requests++; const daily = url.searchParams.get('interval') === 'D'; return route.fulfill({ json: { exchange: 'bybit', source: 'bybit', closed_only: true, candles: [...Array.from({ length: daily ? 3 : closedCount }, (_, i) => ({ ts: start + (i - 3) * (daily ? 86400000 : 60000), o: 100, h: 105, l: 99, c: 102, v: 10 })), ...(daily ? [] : closedExtra)], zones: [] } }); }
      if (url.pathname === '/api/chart/live') return route.fulfill({ json: { exchange: 'bybit', symbol: 'BTCUSDT', interval: url.searchParams.get('interval'), as_of: start + 1, candle: { ts: start, close_ts: start + 60000, o: 100, h: 105, l: 99, c: 101, v: 12, is_forming: true, source: 'bybit', symbol: 'BTCUSDT', interval: url.searchParams.get('interval') } } });
      if (url.pathname === '/api/chart/context') return route.fulfill({ json: { exchange: 'bybit', symbol: 'BTCUSDT', as_of: start, funding_rate: 0.0001, funding_interval_hours: 8, open_interest: 1234, open_interest_unit: 'BTC', mark_price: null, index_price: 101, ema_bias: { '60': 'bullish', '240': 'mixed', D: 'unavailable' }, warnings: [] } });
      if (url.pathname === '/api/markets') return route.fulfill({ json: { exchange: 'bybit', markets: [], total: 0, as_of: start } });
      if (url.pathname === '/api/chart/intervals') return route.fulfill({ json: { intervals: [{ value: '1', label: '1m' }, { value: 'D', label: 'D' }] } });
      if (url.pathname.startsWith('/api/')) return route.fulfill({ status: 404, json: {} });
      return route.fulfill({ path: path.join(__dirname, '..', url.pathname === '/' ? 'templates/workspace.html' : url.pathname.slice(1)) });
    });
    await page.goto('http://workspace.test/');
    await page.waitForFunction(() => window.__workspaceDebug?.panes.get(0)?.forming?.close === 101);
    assert.equal(await page.evaluate(() => window.__sockets.length), 1, 'two equal panes share one WS');
    for (const close of [103, 104]) {
      await page.evaluate(({ start, close }) => window.__sockets[0].message({ topic: 'kline.1.BTCUSDT', ts: start + close, data: [{ start, end: start + 59999, interval: '1', open: '100', high: '105', low: '99', close: String(close), volume: '13', confirm: false }] }), { start, close });
      assert.equal(await page.evaluate(() => window.__workspaceDebug.panes.get(0).series.dataByIndex(3).close), close);
    }
    await page.evaluate(start => window.__sockets[0].message({ topic: 'publicTrade.BTCUSDT', data: [{ s: 'BTCUSDT', p: '106', T: start + 500 }] }), start);
    assert.deepEqual(await page.evaluate(() => { const p = window.__workspaceDebug.panes.get(0); return [p.forming.close, p.forming.high, p.forming.open, p.forming.volume, p.candles.length, p.candles.at(-1).close]; }), [106, 106, 100, 13, 3, 102]);
    assert.match(await page.locator('.pane-live-price').first().innerText(), /WS.*106/);
    await page.locator('.pane-context summary').first().click();
    await page.waitForFunction(() => document.querySelector('.pane-context-body').textContent.includes('0.01%'));
    assert.match(await page.locator('.pane-context-body').first().innerText(), /1,234 BTC/);
    assert.match(await page.locator('.pane-context-body').first().innerText(), /1h bullish/);
    closedCount = 4;
    await page.evaluate(start => window.__sockets[0].message({ topic: 'kline.1.BTCUSDT', ts: start + 60001, data: [{ start: start + 60000, end: start + 119999, interval: '1', open: '102', high: '105', low: '99', close: '103', volume: '1', confirm: false }] }), start);
    await page.waitForFunction(() => window.__workspaceDebug.panes.get(0).candles.length === 4);
    assert.equal(await page.evaluate(() => window.__workspaceDebug.panes.get(0).forming.time), (start + 60000) / 1000);
    assert.equal(requests, 4, 'once per pane snapshot on rollover, no ticker polling');
    const generic = await page.evaluate(() => window.__workspaceDebug.INDICATOR_DEFS.find(d => !d.hidden && !['fvg','ema7','ema14','ema28','ema200','ma20high','ma20low'].includes(d.id))?.id);
    assert.ok(generic, 'real extras module appended to registry');
    const control = page.locator(`input[data-indicator-toggle="${generic}"]`);
    assert.equal(await control.isChecked(), false); await control.check();
    assert.equal(await page.evaluate(id => JSON.parse(localStorage.getItem('crypto-screener.workspace.v4')).indicatorEnabled[id], generic), true);
    await page.locator('.indicator-settings-btn[data-indicator="ema7"]').click();
    const savedBeforeInvalid = await page.evaluate(() => localStorage.getItem('crypto-screener.workspace.v4'));
    await page.locator('input[data-key="period"]').fill('7.5');
    assert.equal(await page.locator('input[data-key="period"]').evaluate(input => input.validity.stepMismatch), true);
    await page.locator('.indicator-settings-apply').click();
    assert.equal(await page.evaluate(() => localStorage.getItem('crypto-screener.workspace.v4')), savedBeforeInvalid, 'invalid Apply never persists the draft');
    assert.equal(await page.evaluate(() => window.__workspaceDebug.getIndicatorSettings('ema7').period), 7);
    await page.locator('input[data-key="period"]').fill('10'); await page.locator('.indicator-settings-apply').click(); await page.keyboard.press('Escape');
    assert.equal(await page.locator('label:has(#ema7-toggle) span').innerText(), 'EMA 10');
    assert.equal(await page.locator('#local-alerts-toggle').isChecked(), false); await page.locator('#local-alerts-toggle').check();
    assert.equal(await page.locator('#local-alerts-count').innerText(), '0');
    closedExtra.push({ ts: start + 60000, o: 122, h: 125, l: 120, c: 122, v: 5 });
    await page.evaluate(() => Promise.all([...window.__workspaceDebug.panes.values()].map(p => p.load(false))));
    assert.equal(await page.locator('#local-alerts-count').innerText(), '1', 'one local creation, not one per pane');
    assert.match(await page.locator('#local-alerts-feed').textContent(), /FVG created/);
    await page.evaluate(() => Promise.all([...window.__workspaceDebug.panes.values()].map(p => p.load(false))));
    assert.equal(await page.locator('#local-alerts-count').innerText(), '1', 'reconnect/reload snapshot is deduplicated');
    closedExtra.push({ ts: start + 120000, o: 110, h: 130, l: 99, c: 110, v: 6 });
    await page.evaluate(() => Promise.all([...window.__workspaceDebug.panes.values()].map(p => p.load(false))));
    assert.equal(await page.locator('#local-alerts-count').innerText(), '2', 'one fully filled local event');
    assert.match(await page.locator('#local-alerts-feed').textContent(), /FVG fully filled/);
    await page.locator('.local-alerts summary').click();
    if (process.env.WORKSPACE_SCREENSHOT) await page.screenshot({ path: process.env.WORKSPACE_SCREENSHOT.replace('.png', '-stream.png') });
    await page.locator('#interval-select').selectOption('D');
    await page.waitForFunction(() => window.__sockets.length === 2);
    await page.waitForFunction(() => window.__workspaceDebug.panes.get(0).forming?.close === 101);
    const before = await page.evaluate(() => window.__workspaceDebug.panes.get(0).forming?.close);
    await page.evaluate(start => window.__sockets[0].message({ topic: 'kline.1.BTCUSDT', ts: start + 70000, data: [{ start, end: start + 59999, interval: '1', open: '100', high: '999', low: '99', close: '999', volume: '1' }] }), start);
    assert.equal(await page.evaluate(() => window.__workspaceDebug.panes.get(0).forming?.close), before, 'old interval cannot mutate switched pane');
    for (const close of [109, 110]) {
      await page.evaluate(({ start, close }) => window.__sockets[1].message({ topic: 'kline.D.BTCUSDT', ts: start + close, data: [{ start, end: start + 86399999, interval: 'D', open: '100', high: '115', low: '99', close: String(close), volume: '100', confirm: false }] }), { start, close });
      assert.equal(await page.evaluate(() => window.__workspaceDebug.panes.get(0).series.dataByIndex(3).close), close, 'daily real current candle updates twice intrabar');
      assert.equal(await page.evaluate(() => window.__workspaceDebug.panes.get(0).candles.length), 3, 'daily indicator history remains closed-only');
    }
    await page.reload(); await page.waitForFunction(() => window.__workspaceDebug?.panes.get(0)?.candles.length === 6);
    assert.equal(await page.locator('#local-alerts-count').innerText(), '2', 'page reload retains bounded feed without historical duplicates');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('browser: failed and stale gap history retries autonomously until the actual closed bridge arrives', { skip: !process.env.WORKSPACE_PLAYWRIGHT }, async () => {
  const { chromium } = require(process.env.WORKSPACE_PLAYWRIGHT);
  const browser = await chromium.launch({ channel:'chrome', headless:true });
  try {
    const page = await browser.newPage(); const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.routeWebSocket(/wss:/, socket => socket.close());
    await page.addInitScript(() => localStorage.setItem('crypto-screener.workspace.v4', JSON.stringify({version:4, layout:'1', panes:[{symbol:'BTCUSDT',exchange:'bybit',interval:'240'}], indicatorSettings:{ema7:{period:7.5},fvg:{minGapPct:0.01},'turtle-upper':{inner:5.6}}})));
    let requests = 0;
    await page.route('http://workspace.test/**', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/chart') {
        requests++;
        if (requests === 2) return route.fulfill({status:503, json:{error:'history unavailable'}});
        const times = requests >= 4 ? [14400,28800] : [14400];
        return route.fulfill({json:{exchange:'bybit',source:'bybit',closed_only:true,candles:times.map(t=>({ts:t*1000,o:100,h:110,l:90,c:105,v:1})),zones:[]}});
      }
      if (url.pathname === '/api/chart/intervals') return route.fulfill({json:{intervals:[{value:'240',label:'4h'}]}});
      if (url.pathname === '/api/markets') return route.fulfill({json:{exchange:'bybit',markets:[],total:0,as_of:Date.now()}});
      if (url.pathname.startsWith('/api/')) return route.fulfill({status:404,json:{}});
      return route.fulfill({path:path.join(__dirname,'..',url.pathname === '/' ? 'templates/workspace.html' : url.pathname.slice(1))});
    });
    await page.goto('http://workspace.test/');
    await page.waitForSelector('.chart-pane[data-state="ready"]', {timeout:5000}).catch(async error => { throw new Error(`${error.message}\n${JSON.stringify(await page.locator('.chart-pane').allTextContents())}\n${JSON.stringify(errors)}`); });
    assert.deepEqual(await page.evaluate(() => {
      const d=window.__workspaceDebug; return [d.getIndicatorSettings('ema7').period,d.getIndicatorSettings('fvg').minGapPct,d.getIndicatorSettings('turtle-upper').inner];
    }), [7,0.01,5.6], 'real page migrates fractions without damaging decimal schemas');
    await page.clock.install();
    await page.evaluate(() => window.__workspaceDebug.panes.get(0).acceptForming({time:43200,open:105,high:110,low:100,close:108,volume:2},'WS'));
    for (const [delay,count] of [[700,2],[1400,3],[2800,4]]) {
      await page.clock.runFor(delay);
      await page.waitForFunction(count => { const p=window.__workspaceDebug.panes.get(0); return !p.request && !p.resyncInFlight && (count === 4 ? p.lastResyncBar === null : p.resyncAttempts === count-1); }, count);
      assert.equal(requests,count);
      if (count < 4) assert.match(await page.locator('.pane-status').innerText(), /STALE.*gap/);
    }
    assert.deepEqual(await page.evaluate(() => { const p=window.__workspaceDebug.panes.get(0); return [p.candles.map(c=>c.time),p.forming.time,p.resyncAttempts,p.resyncTimer,p.series.dataByIndex(2).close]; }), [[14400,28800],43200,0,null,108]);
    await page.clock.runFor(30000); assert.equal(requests,4, 'successful bridge cancels autonomous retries');
    assert.deepEqual(errors,[]);
  } finally { await browser.close(); }
});

// Real-browser pixel regression for the confirmed FVG coordinate bug
// (docs/INDICATOR_PLUGIN_PLAN.md): renders a known candle set with the real
// vendored Lightweight Charts, reads the ACTUAL candlestick canvas pixels
// (not just the library's own coordinate API) to find the confirming
// candle's true rendered left edge, and asserts the FVG overlay box touches
// it instead of floating ~1 bar to the right. Also proves the generic band
// renderer (Part A, kind:'band') is pixel-correct, and exercises the
// settings popover UI + v3->v4 persistence/migration.
test('browser: FVG box touches the real candle (pixel scan), band renderer, settings popover + migration', { skip: !process.env.WORKSPACE_PLAYWRIGHT }, async () => {
  const { chromium } = require(process.env.WORKSPACE_PLAYWRIGHT);
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
    await page.routeWebSocket(/wss:/, socket => socket.close()); // fixtures never depend on real venues
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const step = 14400000; // 4h in ms, matches interval '240'
    const base = 1700000000000;
    // Flat candles everywhere except a clean, large bull gap at index 12:
    // candle[10].high=105 .. candle[12].low=130, confirmed by candle[12].
    // Post-gap candles stay elevated (140) so neither fill policy's
    // mitigation walk erases the zone by dipping back through it.
    const candles = Array.from({ length: 30 }, (_, i) => {
      if (i === 10) return { ts: base + i * step, o: 100, h: 105, l: 98, c: 102, v: 1000 };
      if (i === 11) return { ts: base + i * step, o: 103, h: 109, l: 101, c: 107, v: 1000 };
      if (i === 12) return { ts: base + i * step, o: 132, h: 142, l: 130, c: 140, v: 1000 };
      const price = i < 10 ? 100 : 140;
      return { ts: base + i * step, o: price, h: price + 1, l: price - 1, c: price, v: 1000 };
    });
    await page.route('http://workspace.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/api/markets')) return route.fulfill({ json: { exchange: 'bybit', markets: [], total: 0, as_of: Date.now(), cached: true, warnings: [] } });
      if (url.pathname === '/api/chart/intervals') return route.fulfill({ json: { intervals: [{ value: '240', label: '4h' }] } });
      if (url.pathname === '/api/chart') return route.fulfill({ json: { closed_only: true, exchange: 'bybit', source: 'bybit', candles, zones: [] } });
      if (url.pathname.startsWith('/api/')) return route.fulfill({ status: 404, json: { error: 'Unexpected API' } });
      const relative = url.pathname === '/' ? 'templates/workspace.html' : url.pathname.slice(1);
      return route.fulfill({ path: path.join(__dirname, '..', relative) });
    });
    await page.goto('http://workspace.test/');
    await page.waitForSelector('.chart-pane[data-pane-id="0"][data-state="ready"]');
    await page.locator('#layout-select').selectOption('1');

    // Force the client-side 'close' fill policy so the box is computed
    // straight from the candles above (no backend zone fixture needed) —
    // the algorithm itself is covered by the node:test fvgZones unit tests.
    await page.evaluate(() => {
      const debug = window.__workspaceDebug;
      debug.setIndicatorSettings('fvg', { fillPolicy: 'close' });
      debug.panes.get(0).chart.timeScale().applyOptions({ barSpacing: 15 });
      debug.panes.get(0).applyIndicators();
    });
    await page.waitForTimeout(150);

    const pixels = await page.evaluate(() => {
      const debug = window.__workspaceDebug;
      const pane = debug.panes.get(0);
      const ratio = window.devicePixelRatio || 1;
      const candle = pane.candles[12];
      const yCss = pane.series.priceToCoordinate((candle.open + candle.close) / 2);
      function scanColumnRange(canvas, yCssPos, colorTest) {
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        const yPx = Math.max(0, Math.min(canvas.height - 1, Math.round(yCssPos * ratio)));
        const row = ctx.getImageData(0, yPx, canvas.width, 1).data;
        let left = null; let right = null;
        for (let x = 0; x < canvas.width; x += 1) {
          const i = x * 4;
          if (colorTest(row[i], row[i + 1], row[i + 2], row[i + 3])) {
            if (left === null) left = x;
            right = x;
          }
        }
        return left === null ? null : { leftCss: left / ratio, rightCss: (right + 1) / ratio };
      }
      // Real rendered candle body: up color is #00c087 (0,192,135).
      const isUpCandle = (r, g, b, a) => a > 40 && g > 140 && r < 90 && b > 70 && b < 180;
      let candleRange = null;
      for (const canvas of pane.host.querySelectorAll('canvas')) {
        const found = scanColumnRange(canvas, yCss, isUpCandle);
        if (found) { candleRange = found; break; }
      }
      // FVG overlay canvas starts fully transparent (cleared every frame) and
      // only our own drawZones/drawBands code ever paints onto it, so any
      // non-zero alpha at this row is unambiguously part of the box. Sample
      // a y inside the CONFIRMING zone's own [bottom, top] price range (not
      // the candle's own high/low, which can sit outside the gap itself).
      const confirmingZone = debug.panes.get(0).effectiveFvgZones(debug.getIndicatorSettings('fvg')).find(zone => zone.startTime === candle.time);
      const overlayY = pane.series.priceToCoordinate((confirmingZone.top + confirmingZone.bottom) / 2);
      const isBoxFill = (r, g, b, a) => a > 0;
      const overlayRange = scanColumnRange(pane.canvas, overlayY, isBoxFill);
      const barSpacing = pane.chart.timeScale().options().barSpacing;
      const lastIndex = pane.candles.length - 1;
      const expectedRightEdge = pane.chart.timeScale().logicalToCoordinate(lastIndex) + barSpacing / 2;
      const expectedLeftEdge = pane.chart.timeScale().timeToCoordinate(candle.time) - barSpacing / 2;
      return { candleRange, overlayRange, barSpacing, expectedLeftEdge, expectedRightEdge };
    });
    if (process.env.WORKSPACE_SCREENSHOT) { console.log('FVG 15px boundary evidence:', JSON.stringify(pixels)); fs.writeFileSync(process.env.WORKSPACE_SCREENSHOT.replace('.png', '-boundaries.json'), JSON.stringify(pixels, null, 2)); }
    assert.ok(pixels.candleRange, 'could not locate the real rendered candle pixels');
    assert.ok(pixels.overlayRange, 'FVG overlay box did not render any fill pixels');
    assert.equal(pixels.barSpacing, 15, 'regression is exercised at 15px bar spacing, not a tiny zoom that hides a bar of drift');
    assert.ok(Math.abs(pixels.overlayRange.leftCss - pixels.expectedLeftEdge) <= 1,
      `exact slot LEFT boundary: ${JSON.stringify(pixels)}`);
    assert.ok(Math.abs(pixels.overlayRange.rightCss - pixels.expectedRightEdge) <= 1,
      `exact slot RIGHT boundary: ${JSON.stringify(pixels)}`);
    if (process.env.WORKSPACE_SCREENSHOT) await page.screenshot({ path: process.env.WORKSPACE_SCREENSHOT.replace('.png', '-boundaries.png') });
    // THE regression: box left edge must be at/after the candle's own left
    // edge, and within half a bar spacing of it — touching, not floating a
    // whole bar away as the confirmed bug did.
    assert.ok(pixels.overlayRange.leftCss <= pixels.candleRange.leftCss + 1.5,
      `box left (${pixels.overlayRange.leftCss}) must not start right of the candle's own left edge (${pixels.candleRange.leftCss})`);
    assert.ok(pixels.overlayRange.leftCss > pixels.candleRange.leftCss - pixels.barSpacing * 0.75,
      `box left (${pixels.overlayRange.leftCss}) must be touching the candle (${pixels.candleRange.leftCss}), not floating further left either`);
    assert.ok(Math.abs(pixels.overlayRange.leftCss - pixels.candleRange.leftCss) < pixels.barSpacing * 0.5,
      'box left edge and candle left edge must be within half a bar spacing (touching), not ~1 full bar apart like the confirmed bug');
    // Right edge must stop at the last closed candle, never drift past it.
    assert.ok(pixels.overlayRange.rightCss <= pixels.expectedRightEdge + 1.5,
      `box right (${pixels.overlayRange.rightCss}) must not drift past the last closed candle (${pixels.expectedRightEdge})`);

    // ─── Part A: band renderer (synthetic demo-band, internal/no toolbar chip) ───
    const bandPixels = await page.evaluate(() => {
      const debug = window.__workspaceDebug;
      const pane = debug.panes.get(0);
      debug.setIndicatorSettings('demo-band', { offsetPct: 3 });
      debug.setIndicatorEnabled('demo-band', true);
      pane.scheduleDraw();
      return new Promise(resolve => {
        requestAnimationFrame(() => requestAnimationFrame(() => {
          const ratio = window.devicePixelRatio || 1;
          const candle = pane.candles[20];
          const upperPrice = candle.close * 1.03;
          const lowerPrice = candle.close * 0.97;
          const upperY = pane.series.priceToCoordinate(upperPrice);
          const lowerY = pane.series.priceToCoordinate(lowerPrice);
          const midY = (upperY + lowerY) / 2;
          const ctx = pane.canvas.getContext('2d', { willReadFrequently: true });
          const xCss = pane.chart.timeScale().logicalToCoordinate(20);
          const sample = yCss => {
            const data = ctx.getImageData(Math.round(xCss * ratio), Math.round(yCss * ratio), 1, 1).data;
            return { r: data[0], g: data[1], b: data[2], a: data[3] };
          };
          resolve({ atMid: sample(midY), aboveBand: sample(upperY - 30), belowBand: sample(lowerY + 30) });
        }));
      });
    });
    assert.ok(bandPixels.atMid.a > 0 && bandPixels.atMid.b > bandPixels.atMid.r,
      'band fill must be visible (bluish) between the upper and lower curves');
    assert.ok(Math.abs(bandPixels.atMid.b - bandPixels.aboveBand.b) > 10 || bandPixels.aboveBand.a === 0,
      'outside the band (above it) must look different from inside the band');
    await page.evaluate(() => window.__workspaceDebug.setIndicatorEnabled('demo-band', false));

    // ─── Part A: settings UI — gear opens popover, Apply recomputes, Reset restores defaults, Escape closes ───
    await page.locator('.indicator-settings-btn[data-indicator="fvg"]').click();
    await page.waitForSelector('.indicator-settings-popover:not([hidden])');
    assert.equal(await page.locator('.indicator-settings-field:has(select[data-key="fillPolicy"]) select').inputValue(), 'close', 'popover reflects the setting we changed earlier via debug hook');
    await page.locator('.indicator-settings-field:has(input[data-key="minGapPct"]) input').fill('25');
    await page.locator('.indicator-settings-apply').click();
    await page.waitForTimeout(80);
    const afterApply = await page.evaluate(() => window.__workspaceDebug.getIndicatorSettings('fvg').minGapPct);
    assert.equal(afterApply, 25, 'Apply must commit the typed value immediately');
    // A 25% minimum gap filter must drop our real gap (much smaller %), so
    // the overlay canvas should go back to having no box fill pixels.
    const afterFilter = await page.evaluate(() => {
      const pane = window.__workspaceDebug.panes.get(0);
      const ctx = pane.canvas.getContext('2d', { willReadFrequently: true });
      const data = ctx.getImageData(0, 0, pane.canvas.width, pane.canvas.height).data;
      for (let i = 3; i < data.length; i += 4) if (data[i] > 0) return true;
      return false;
    });
    assert.equal(afterFilter, false, '25% minGapPct filter must remove the (much smaller) demo gap box — band was also disabled implicitly by re-render, so canvas should be empty');
    await page.locator('.indicator-settings-reset').click();
    const afterReset = await page.evaluate(() => window.__workspaceDebug.getIndicatorSettings('fvg'));
    assert.equal(afterReset.minGapPct, 0, 'Reset must restore the documented default (0 = any size)');
    assert.equal(afterReset.fillPolicy, 'wick', 'Reset must restore the documented default fill policy (matches shipped wick-based behaviour)');
    await page.keyboard.press('Escape');
    await page.waitForSelector('.indicator-settings-popover[hidden]', { state: 'attached' });
    assert.equal(await page.locator('.indicator-settings-btn[data-indicator="fvg"]').getAttribute('aria-expanded'), 'false');

    // Outside click also closes it.
    await page.locator('.indicator-settings-btn[data-indicator="ema7"]').click();
    await page.waitForSelector('.indicator-settings-popover:not([hidden])');
    await page.locator('.topbar').click();
    await page.waitForSelector('.indicator-settings-popover[hidden]', { state: 'attached' });

    // ─── v3 -> v4 schema migration: an old v3 blob has no indicatorSettings;
    // readState must fill in documented defaults, not crash or drop state. ───
    await page.evaluate(() => {
      localStorage.clear();
      localStorage.setItem('crypto-screener.workspace.v3', JSON.stringify({
        version: 3, layout: '1', active: 0,
        panes: [{ symbol: 'BTCUSDT', interval: '240', exchange: 'bybit' }],
        fvg: true, ema: { 7: true, 14: true, 28: true, 200: false }, ma20hl: { high: false, low: false }
      }));
    });
    await page.reload();
    await page.waitForSelector('.chart-pane[data-pane-id="0"][data-state="ready"]');
    const migrated = await page.evaluate(() => window.__workspaceDebug.getIndicatorSettings('fvg'));
    assert.deepEqual(migrated, { minGapPct: 0, fillPolicy: 'wick', bullColor: '#00c087', bearColor: '#f5a623', opacity: 1 }, 'v3 blob (no indicatorSettings) must migrate to documented v4 defaults');
    const versionAfterSave = await page.evaluate(() => JSON.parse(localStorage.getItem('crypto-screener.workspace.v4')).version);
    assert.equal(versionAfterSave, 4, 'subsequent saves upgrade storage to the v4 key');

    // Corrupt/out-of-range saved indicatorSettings must be sanitized per field, not crash.
    await page.evaluate(() => {
      localStorage.clear();
      localStorage.setItem('crypto-screener.workspace.v4', JSON.stringify({
        version: 4, layout: '1', active: 0,
        panes: [{ symbol: 'BTCUSDT', interval: '240', exchange: 'bybit' }],
        fvg: true, ema: { 7: true, 14: true, 28: true, 200: false }, ma20hl: { high: false, low: false },
        indicatorSettings: { fvg: { minGapPct: 999, fillPolicy: 'nonsense', bullColor: 'not-a-color' }, ema7: { period: -5 } }
      }));
    });
    await page.reload();
    await page.waitForSelector('.chart-pane[data-pane-id="0"][data-state="ready"]');
    const sanitized = await page.evaluate(() => ({ fvg: window.__workspaceDebug.getIndicatorSettings('fvg'), ema7: window.__workspaceDebug.getIndicatorSettings('ema7') }));
    assert.equal(sanitized.fvg.minGapPct, 50, 'out-of-range number clamps to the schema max, does not crash');
    assert.equal(sanitized.fvg.fillPolicy, 'wick', 'invalid select value falls back to the documented default');
    assert.equal(sanitized.fvg.bullColor, '#00c087', 'invalid color falls back to the documented default');
    assert.equal(sanitized.ema7.period, 2, 'below-minimum period clamps to the schema min (2)');

    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('seconds intervals: intervalSeconds steps and pixel edges on a 5s pane with a gap', () => {
  const fn = vm.runInNewContext(`${intervalFunction}\nintervalSeconds`);
  assert.deepEqual(['1s', '5s', '15s', '30s'].map(v => fn(v, 0)), [1, 5, 15, 30]);
  assert.equal(fn('1', 0), 60, 'minute intervals unchanged');
  coordinates.state.panes[0].interval = '5s';
  try {
    // 5s bars with a 20 s gap (no trades → no candle); the next slot extrapolates by 5 s.
    const candles = [0, 5, 10, 30, 35].map(t => ({ time: 1792195200 + t }));
    const pane = makePane(candles, 100, 15);
    assert.equal(pane.edgeCoordinate(1792195200 + 35), 100 + 4 * 15 - 7.5);
    assert.equal(pane.edgeCoordinate(1792195200 + 40), 100 + 5 * 15 - 7.5, 'right edge of the last 5s bar');
  } finally { coordinates.state.panes[0].interval = '2D'; }
});
