'use strict';
// Shared fixture for the drawing-tool tests: a mock chart (bar i centre at ORIGIN + i*SPACING,
// fractional logical calls rejected like the real library), price y = (200 - price) * 4.
const assert = require('node:assert/strict');
const path = require('node:path');
const dr = require(path.join(__dirname, '../../static/drawings.js'));

const ORIGIN = 50, SPACING = 10, T0 = 1792195200; // seconds
const candles = Array.from({ length: 20 }, (_, i) => ({ time: T0 + i * 60, open: 100 + i, high: 103 + i, low: 98 + i, close: 101 + i, volume: 10 + i }));
const chart = { timeScale: () => ({ options: () => ({ barSpacing: SPACING }), logicalToCoordinate: i => { assert.ok(Number.isInteger(i), `fractional logical ${i}`); return ORIGIN + i * SPACING; } }) };
const series = { priceToCoordinate: p => (200 - p) * 4, coordinateToPrice: y => 200 - y / 4 };
const mapper = dr.createMapper({ chart, series, candles, stepMs: 60000 });
const ms = i => (T0 + i * 60) * 1000;
const view = (extra = {}) => ({ mapper, candles, w: 300, h: 800, precision: 2, stepMs: 60000, ...extra });
function memoryStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return { getItem: k => (map.has(k) ? map.get(k) : null), setItem: (k, v) => map.set(k, String(v)), removeItem: k => map.delete(k), map };
}
module.exports = { dr, ORIGIN, SPACING, T0, candles, chart, series, mapper, ms, view, memoryStorage };
