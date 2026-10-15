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
    const symbol = helpers.normalizeSymbol(value);
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
