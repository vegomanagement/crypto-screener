/* Versioned, sanitised settings and view-only hotkeys for /scalp. Pure (no DOM): unit-tested in node. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ScalpSettings = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const VERSION = 1;
  const STORAGE_KEY = 'crypto-screener.scalp.v1';
  const VENUES = { binance: 'Binance', bybit: 'Bybit', hyperliquid: 'Hyperliquid' };
  const TICK_SCALES = [1, 2, 5, 10, 20];
  const PERCENT_SCALES = [0.005, 0.01, 0.02, 0.05, 0.1];
  const CLUSTER_INTERVALS = { 60000: '1m', 300000: '5m', 900000: '15m' };
  const DEFAULT_SYMBOLS = [['binance', 'BTCUSDT'], ['binance', 'ETHUSDT'], ['binance', 'SOLUSDT'], ['bybit', 'BTCUSDT']];

  function defaultColumn(venue = 'binance', symbol = 'BTCUSDT') {
    return { venue, symbol, scaleMode: 'ticks', scaleIndex: 0, units: 'usd', abbreviate: true, rowPx: 16, grid: 10,
      autocenter: true, autocenterPct: 30, fullScaleUsd: 0, big1Usd: 500000, big2Usd: 2000000, densityUsd: 500000,
      feed: { spanMs: 60000, minUsd: 0, labelMinUsd: 50000, aggregateMs: 0, aggregateTicks: 0, line: true,
        minRadius: 2, maxRadius: 16, maxSizeUsd: 500000 },
      clusters: { count: 3, intervalMs: 60000, mode: 'volume', showMinUsd: 5000, markMinUsd: 500000 },
      // Volume alerts: every type off by default (docs/SCALPING_PHASE2_PLAN.md, Track A §3).
      alerts: { appear: false, appearUsd: 1000000, withinPct: 0.5, decrease: false, decreasePct: 50,
        print: false, printUsd: 500000, printAggMs: 0, sound: true } };
  }
  function defaults() {
    return { version: VERSION, columns: 1, active: 0, cols: DEFAULT_SYMBOLS.map(([v, s]) => defaultColumn(v, s)) };
  }
  /** Same shapes the server accepts (liquidity_data.symbol_pattern), tightened to linear perps. */
  function validSymbol(venue, symbol) {
    if (typeof symbol !== 'string') return false;
    if (venue === 'hyperliquid') return /^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/.test(symbol);
    if (venue === 'binance') return /^[A-Z0-9]{1,40}(?:USDT|USDC)$/.test(symbol);
    if (venue === 'bybit') return /^[A-Z0-9]{1,26}(?:USDT|USDC|PERP)$/.test(symbol);
    return false;
  }
  const clamp = (v, lo, hi, fallback) => (typeof v === 'number' && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : fallback);
  const pick = (v, allowed, fallback) => (allowed.includes(v) ? v : fallback);
  const bool = (v, fallback) => (typeof v === 'boolean' ? v : fallback);

  function sanitizeColumn(raw, fallback) {
    const d = fallback, r = raw && typeof raw === 'object' ? raw : {};
    const venue = Object.hasOwn(VENUES, r.venue) ? r.venue : d.venue;
    const symbol = validSymbol(venue, r.symbol) ? r.symbol : venue === d.venue ? d.symbol : venue === 'hyperliquid' ? 'BTC' : 'BTCUSDT';
    const f = r.feed && typeof r.feed === 'object' ? r.feed : {}, c = r.clusters && typeof r.clusters === 'object' ? r.clusters : {};
    const a = r.alerts && typeof r.alerts === 'object' ? r.alerts : {}, da = d.alerts || defaultColumn().alerts;
    return {
      venue, symbol,
      scaleMode: pick(r.scaleMode, ['ticks', 'percent'], d.scaleMode),
      scaleIndex: Math.round(clamp(r.scaleIndex, 0, 4, d.scaleIndex)),
      units: pick(r.units, ['usd', 'coin'], d.units), abbreviate: bool(r.abbreviate, d.abbreviate),
      rowPx: Math.round(clamp(r.rowPx, 8, 32, d.rowPx)), grid: pick(r.grid, [0, 10, 50], d.grid),
      autocenter: bool(r.autocenter, d.autocenter), autocenterPct: clamp(r.autocenterPct, 0, 95, d.autocenterPct),
      fullScaleUsd: clamp(r.fullScaleUsd, 0, 1e12, d.fullScaleUsd),
      big1Usd: clamp(r.big1Usd, 0, 1e12, d.big1Usd), big2Usd: clamp(r.big2Usd, 0, 1e12, d.big2Usd),
      densityUsd: clamp(r.densityUsd, 0, 1e12, d.densityUsd),
      feed: { spanMs: Math.round(clamp(f.spanMs, 5000, 600000, d.feed.spanMs)), minUsd: clamp(f.minUsd, 0, 1e12, d.feed.minUsd),
        labelMinUsd: clamp(f.labelMinUsd, 0, 1e12, d.feed.labelMinUsd), aggregateMs: Math.round(clamp(f.aggregateMs, 0, 60000, d.feed.aggregateMs)),
        aggregateTicks: Math.round(clamp(f.aggregateTicks, 0, 1000, d.feed.aggregateTicks)), line: bool(f.line, d.feed.line),
        minRadius: clamp(f.minRadius, 1, 20, d.feed.minRadius), maxRadius: clamp(f.maxRadius, 2, 60, d.feed.maxRadius),
        maxSizeUsd: clamp(f.maxSizeUsd, 1, 1e12, d.feed.maxSizeUsd) },
      clusters: { count: Math.round(clamp(c.count, 0, 8, d.clusters.count)),
        intervalMs: pick(c.intervalMs, Object.keys(CLUSTER_INTERVALS).map(Number), d.clusters.intervalMs),
        mode: pick(c.mode, ['volume', 'delta', 'bidask'], d.clusters.mode),
        showMinUsd: clamp(c.showMinUsd, 0, 1e12, d.clusters.showMinUsd), markMinUsd: clamp(c.markMinUsd, 0, 1e12, d.clusters.markMinUsd) },
      alerts: { appear: bool(a.appear, da.appear), appearUsd: clamp(a.appearUsd, 1000, 1e12, da.appearUsd), withinPct: clamp(a.withinPct, 0.01, 20, da.withinPct),
        decrease: bool(a.decrease, da.decrease), decreasePct: clamp(a.decreasePct, 1, 100, da.decreasePct),
        print: bool(a.print, da.print), printUsd: clamp(a.printUsd, 1000, 1e12, da.printUsd), printAggMs: Math.round(clamp(a.printAggMs, 0, 10000, da.printAggMs)),
        sound: bool(a.sound, da.sound) }
    };
  }
  function sanitize(raw) {
    const out = defaults();
    if (!raw || typeof raw !== 'object' || raw.version !== VERSION) return out;
    out.columns = Math.round(clamp(raw.columns, 1, 4, 1));
    out.active = Math.round(clamp(raw.active, 0, out.columns - 1, 0));
    if (Array.isArray(raw.cols)) out.cols = out.cols.map((d, k) => sanitizeColumn(raw.cols[k], d));
    return out;
  }
  function load(storage) {
    try { return sanitize(JSON.parse(storage.getItem(STORAGE_KEY) || 'null')); } catch (_) { return defaults(); }
  }
  function save(storage, settings) {
    try { storage.setItem(STORAGE_KEY, JSON.stringify(settings)); return true; } catch (_) { return false; }
  }
  /** Ladder row size label, e.g. "×5 ticks" or "0.02 %". */
  function scaleLabel(col) {
    return col.scaleMode === 'percent' ? `${PERCENT_SCALES[col.scaleIndex]} %` : `×${TICK_SCALES[col.scaleIndex]} tick${TICK_SCALES[col.scaleIndex] > 1 ? 's' : ''}`;
  }
  const scaleSpec = col => ({ mode: col.scaleMode, ticks: TICK_SCALES[col.scaleIndex], percent: PERCENT_SCALES[col.scaleIndex] });

  /**
   * View-only hotkeys → { column, action }. Never places, edits or cancels anything; there is no order path.
   * action: 'center' | 'clear-ruler' | 'clear-levels' | 'regroup' | 'redraw' | null.
   */
  function hotkey(col, key) {
    const next = { ...col };
    if (key === 'c' || key === 'C') return { column: { ...next, autocenter: true }, action: 'center' };
    if (key === '+' || key === '=') return { column: { ...next, rowPx: Math.min(32, col.rowPx + 2) }, action: 'redraw' };
    if (key === '-' || key === '_') return { column: { ...next, rowPx: Math.max(8, col.rowPx - 2) }, action: 'redraw' };
    if (/^[1-5]$/.test(key)) return { column: { ...next, scaleIndex: Number(key) - 1 }, action: 'regroup' };
    if (key === 'p' || key === 'P') return { column: { ...next, scaleMode: col.scaleMode === 'ticks' ? 'percent' : 'ticks' }, action: 'regroup' };
    if (key === 'u' || key === 'U') return { column: { ...next, units: col.units === 'usd' ? 'coin' : 'usd' }, action: 'redraw' };
    if (key === 'Escape') return { column: next, action: 'clear-ruler' };
    if (key === 'L') return { column: next, action: 'clear-levels' }; // Shift+L only, so a stray 'l' never wipes levels
    return { column: col, action: null };
  }
  /** Chart deep link understood by /ui (workspace.js reads ?exchange=&symbol= for the active pane). */
  const chartLink = col => `/ui?${new URLSearchParams({ exchange: col.venue, symbol: col.symbol })}`;

  return { VERSION, STORAGE_KEY, VENUES, TICK_SCALES, PERCENT_SCALES, CLUSTER_INTERVALS, defaultColumn, defaults, validSymbol,
    sanitize, sanitizeColumn, load, save, scaleLabel, scaleSpec, hotkey, chartLink };
});
