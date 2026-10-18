/* Research-only workspace. Pinned Lightweight Charts 4.1.3; no trading APIs. */
(() => {
  'use strict';
  // v3 -> v4: added per-indicator settings (indicatorSettings), schema-driven
  // via INDICATOR_DEFS. Migration chain below falls through v4 -> v3 -> v2 -> v1,
  // each older shape simply lacks indicatorSettings/new FVG fields and gets
  // the documented defaults (see INDICATOR_DEFS settingsSchema `default`s).
  // v4 -> v5: split overlays merged into grouped indicators (EMA ribbon, MA
  // channel, Turtle Zone, Opens/key levels, Market Sessions); older saves are
  // migrated by WorkspaceIndicatorGroups.migrateLegacyState (static/indicators/groups.js).
  const STORAGE_KEY = 'crypto-screener.workspace.v5';
  const VERSION = 5;
  const PREVIOUS_KEYS = ['crypto-screener.workspace.v4', 'crypto-screener.workspace.v3', 'crypto-screener.workspace.v2', 'crypto-screener.workspace.v1'];
  const FAVORITES_KEY = 'crypto-screener.workspace.favorites.v1';
  const EXCHANGES = { bybit: 'Bybit', binance: 'Binance', hyperliquid: 'Hyperliquid' };
  const LAYOUTS = { '1': 1, '2cols': 2, '2rows': 2, '3cols': 3, '4grid': 4, '6grid': 6 };
  const FAVORITES = [['1s', '1s'], ['5s', '5s'], ['15s', '15s'], ['30s', '30s'], ['1', '1m'], ['3', '3m'], ['5', '5m'], ['10', '10m'], ['12', '12m'], ['15', '15m'], ['30', '30m'], ['45', '45m'], ['60', '1h'], ['120', '2h'], ['180', '3h'], ['240', '4h'], ['480', '8h'], ['720', '12h'], ['D', 'D'], ['2D', '2D'], ['3D', '3D'], ['W', 'W'], ['M', 'M']];

  const EMA_COLORS = { 200: '#c9d2df' };
  const GROUPS = window.WorkspaceIndicatorGroups || null, OPENS = window.WorkspaceOpens || null, SESSIONS = window.WorkspaceSessions || null;
  const RIBBON_KEYS = ['r0', 'r1', 'r2'];

  // ─── Part A: generic indicator plugin registry (see docs/INDICATOR_PLUGIN_PLAN.md) ───
  // Each entry: { id, label, kind: 'line'|'band'|'boxes', defaultEnabled,
  // settingsSchema: [{key,label,type,min,max,step,options,default}], compute(candles, settings) }.
  // 'line' renders via native Lightweight Charts line series (EMA/MA20 today).
  // 'boxes' renders via the canvas overlay (FVG, bug-fixed below).
  // 'band' renders via the same canvas overlay machinery as boxes; no shipped
  // indicator uses it yet (Turtle Zone is a follow-up task) but the renderer
  // is implemented and regression-tested now so that task only has to add a
  // registry entry.
  const PRICE_SOURCE = { open: c => c.open, high: c => c.high, low: c => c.low, close: c => c.close };
  function emaData(candles, period) {
    if (!Number.isInteger(period) || period < 1 || candles.length < period) return [];
    // Seed with an SMA, not the first close. Do not invent the missing warm-up.
    let value = candles.slice(0, period).reduce((sum, candle) => sum + candle.close, 0) / period;
    const result = [{ time: candles[period - 1].time, value }];
    const alpha = 2 / (period + 1);
    for (let index = period; index < candles.length; index += 1) {
      value = alpha * candles[index].close + (1 - alpha) * value;
      result.push({ time: candles[index].time, value });
    }
    return result;
  }
  // Venue price grid from the server's exchange metadata (/api/scalp/instrument,
  // shared with /scalp: Binance PRICE_FILTER.tickSize, Bybit priceFilter.tickSize,
  // Hyperliquid szDecimals). Hyperliquid's tick depends on the price (≤ 5
  // significant figures, ≤ 6 − szDecimals decimals), as in footprint.hyperliquid_tick.
  const instrumentCache = new Map();
  function fetchInstrument(exchange, symbol) {
    const key = `${exchange}:${symbol}`;
    if (!instrumentCache.has(key)) instrumentCache.set(key, fetch(`/api/scalp/instrument?${new URLSearchParams({ exchange, symbol })}`, { headers: { Accept: 'application/json' } })
      .then(async response => { const data = await response.json().catch(() => null); if (!response.ok || !data) throw new Error(data?.error || `HTTP ${response.status}`); return data; })
      .catch(error => { instrumentCache.delete(key); throw error; }));
    return instrumentCache.get(key);
  }
  const venueTick = (meta, price) => { if (!meta) return null; const fixed = Number(meta.tick_size); if (fixed > 0) return fixed; if (!Number.isInteger(meta.sz_decimals) || meta.sz_decimals < 0 || !(price > 0)) return null; return Number(Math.min(1, Math.max(10 ** (Math.floor(Math.log10(price)) - 4), 10 ** -(6 - meta.sz_decimals))).toPrecision(12)); };
  // Legend value of an EMA computed over closed bars. The forming bar has no
  // closed-bar value, so it gets the provisional live EMA from the last closed
  // value and the forming close ({live: true}); 'warming up' is only for bars
  // before the first `period` closed candles.
  const legendEmaValue = (values, candle, lastClosed, period) => { if (!candle || !values) return null; if (values.has(candle.time)) return { value: values.get(candle.time), live: false }; if (!lastClosed || candle.time <= lastClosed.time || !values.has(lastClosed.time) || !Number.isFinite(candle.close)) return null; const alpha = 2 / (period + 1); return { value: alpha * candle.close + (1 - alpha) * values.get(lastClosed.time), live: true }; };
  // Rolling SMA of a single OHLC component (no 0-padding warm-up: callers get
  // nothing before `period` closed candles exist).
  function smaData(candles, period, source) {
    const key = PRICE_SOURCE[source] ? source : 'close';
    const read = PRICE_SOURCE[key];
    if (!Number.isInteger(period) || period < 1 || candles.length < period) return [];
    let sum = 0;
    for (let index = 0; index < period; index += 1) sum += read(candles[index]);
    const result = [{ time: candles[period - 1].time, value: sum / period }];
    for (let index = period; index < candles.length; index += 1) {
      sum += read(candles[index]) - read(candles[index - period]);
      result.push({ time: candles[index].time, value: sum / period });
    }
    return result;
  }
  // Independent client-side reimplementation of the backend's 3-candle FVG
  // detection (chart_data.py:_fvg_zones), so the registry's compute() stays
  // purely client-side per the plan. 'wick' policy mirrors backend behaviour
  // exactly (progressive mitigation shrinks the zone using later wick
  // extremes); 'close' is the alternate documented policy (mitigate using
  // candle closes instead of wicks) exposed as a settings choice.
  function fvgZones(candles, settings) {
    const policy = settings.fillPolicy === 'close' ? 'close' : 'wick';
    const minGapPct = Math.max(0, Number(settings.minGapPct) || 0);
    const zones = [];
    for (let idx = 2; idx < candles.length; idx += 1) {
      const first = candles[idx - 2];
      const third = candles[idx];
      let direction;
      let top;
      let bottom;
      if (third.low > first.high) { direction = 'bull'; bottom = first.high; top = third.low; }
      else if (third.high < first.low) { direction = 'bear'; top = first.low; bottom = third.high; }
      else continue;
      for (let later = idx + 1; later < candles.length; later += 1) {
        const bound = policy === 'close' ? candles[later].close : (direction === 'bull' ? candles[later].low : candles[later].high);
        if (direction === 'bull') top = Math.min(top, bound); else bottom = Math.max(bottom, bound);
      }
      if (top <= bottom) continue; // fully mitigated, same rule as the backend
      const midPrice = (top + bottom) / 2;
      if (midPrice > 0 && ((top - bottom) / midPrice) * 100 < minGapPct) continue;
      zones.push({ direction, top, bottom, startTime: third.time });
    }
    return zones.slice(-100);
  }
  const INDICATOR_DEFS = [
    // Footprint is toggled per pane (pane header "FP"); this hidden entry only owns
    // its persisted, schema-validated settings and the shared settings popover.
    { id: 'footprint', label: 'Footprint', kind: 'footprint', defaultEnabled: false, hidden: true,
      settingsSchema: [
        { key: 'cellType', label: 'Cell type', type: 'select', options: ['bidask', 'volume', 'delta', 'deltavolume'], default: 'bidask' },
        { key: 'mode', label: 'Display mode', type: 'select', options: ['box', 'profile'], default: 'box' },
        { key: 'input', label: 'Input (traded volume or number of trades)', type: 'select', options: ['volume', 'trades'], default: 'volume' },
        { key: 'grouping', label: 'Tick grouping', type: 'select', options: ['auto', 'manual'], default: 'auto' },
        { key: 'groupTicks', label: 'Manual group (ticks per row)', type: 'number', min: 1, max: 100000, step: 1, default: 10 },
        { key: 'minValue', label: 'Hide cells below (0 = off)', type: 'number', min: 0, max: 1000000000000, step: 0.00000001, default: 0 },
        { key: 'maxValue', label: 'Hide cells above (0 = off)', type: 'number', min: 0, max: 1000000000000, step: 0.00000001, default: 0 },
        { key: 'coloration', label: 'Coloration', type: 'select', options: ['none', 'fixed', 'volume', 'delta', 'imbalance'], default: 'delta' },
        { key: 'dominantOnly', label: 'Highlight only dominant side', type: 'boolean', default: false },
        { key: 'showPoc', label: 'Bar POC border', type: 'boolean', default: true },
        { key: 'showDeltaPoc', label: 'Delta POC border', type: 'boolean', default: false },
        { key: 'minDelta', label: 'Delta POC minimum |delta|', type: 'number', min: 0, max: 1000000000000, step: 0.00000001, default: 0 },
        { key: 'showVA', label: 'Value area (dim outside)', type: 'boolean', default: true },
        { key: 'vaPct', label: 'Value area %', type: 'number', min: 1, max: 100, step: 1, default: 70 },
        { key: 'imbalanceRatio', label: 'Diagonal imbalance ratio %', type: 'number', min: 100, max: 100000, step: 1, default: 300 },
        { key: 'imbalanceMinDiff', label: 'Imbalance minimum difference', type: 'number', min: 0, max: 1000000000000, step: 0.00000001, default: 0 },
        { key: 'includeZero', label: 'Imbalance vs empty opposite level', type: 'boolean', default: false },
        { key: 'stackedMin', label: 'Stacked imbalance levels', type: 'number', min: 2, max: 50, step: 1, default: 3 },
        { key: 'showFooter', label: 'Bar delta footer', type: 'boolean', default: true },
        { key: 'bars', label: 'History bars to backfill', type: 'number', min: 5, max: 300, step: 1, default: 60 }], compute: () => [] },
    { id: 'liquidity', label: 'Liquidity', kind: 'liquidity', defaultEnabled: false,
      settingsSchema: [
        { key: 'historyWindow', label: 'Recent history window (pan for older)', type: 'select', options: ['10m', '1h'], default: '10m' },
        { key: 'step', label: 'Price bucket step (0 = automatic)', type: 'number', min: 0, max: 1000000, step: 0.00000001, default: 0 },
        { key: 'minQty', label: 'Minimum bucket quantity (native units)', type: 'number', min: 0, max: 1000000000000, step: 0.00000001, default: 0 },
        { key: 'opacity', label: 'Heatmap opacity', type: 'number', min: 0, max: 0.6, step: 0.05, default: 0.3 },
        { key: 'lowColor', label: 'Low intensity', type: 'color', default: '#632ca6' },
        { key: 'midColor', label: 'Medium intensity', type: 'color', default: '#ed4eaa' },
        { key: 'highColor', label: 'High intensity', type: 'color', default: '#ffe66d' }], compute: () => [] },
    { id: 'ema200', label: 'EMA 200', kind: 'line', defaultEnabled: false,
      settingsSchema: [
        { key: 'period', label: 'Period', type: 'number', min: 2, max: 500, step: 1, default: 200 },
        { key: 'color', label: 'Color', type: 'color', default: EMA_COLORS[200] }],
      compute: (candles, settings) => emaData(candles, settings.period) },
    { id: 'fvg', label: 'FVG', kind: 'boxes', defaultEnabled: true,
      settingsSchema: [
        { key: 'minGapPct', label: 'Min gap size (% of price)', type: 'number', min: 0, max: 50, step: 0.01, default: 0 },
        // Default 'wick' matches the project's shipped behaviour (toolbar
        // label "Wick fill · closed candles"): a gap is mitigated as soon as
        // a later candle's wick (high/low) touches it. 'close' is the
        // alternate documented policy: mitigate only on candle closes.
        { key: 'fillPolicy', label: 'Fill policy', type: 'select', options: ['wick', 'close'], default: 'wick' },
        { key: 'bullColor', label: 'Bullish color', type: 'color', default: '#00c087' },
        { key: 'bearColor', label: 'Bearish color', type: 'color', default: '#f5a623' },
        { key: 'opacity', label: 'Box opacity', type: 'number', min: 0.2, max: 2, step: 0.1, default: 1 }],
      compute: fvgZones },
    // Synthetic 'band' indicator, not surfaced as a toolbar toggle: it exists
    // to prove the generic band renderer is pixel-correct (see tests) before
    // Turtle Zone plugs into it. A constant +/-offsetPct band around close.
    { id: 'demo-band', label: 'Demo band (internal)', kind: 'band', defaultEnabled: false, hidden: true,
      settingsSchema: [
        { key: 'offsetPct', label: 'Offset (% of price)', type: 'number', min: 0.1, max: 50, step: 0.1, default: 1 },
        { key: 'color', label: 'Fill color', type: 'color', default: 'rgba(130,170,255,0.18)' }],
      compute: (candles, settings) => {
        const offset = Math.max(0, Number(settings.offsetPct) || 0) / 100;
        return {
          upper: candles.map(c => ({ time: c.time, value: c.close * (1 + offset) })),
          lower: candles.map(c => ({ time: c.time, value: c.close * (1 - offset) }))
        };
      } }
  ];
  INDICATOR_DEFS.push(...(GROUPS?.definitions || []));
  if (OPENS) INDICATOR_DEFS.push({ id: 'opens', label: 'Key levels / Opens', kind: 'opens', defaultEnabled: false, settingsSchema: OPENS.settingsSchema(),
    labelForSettings: () => 'Key levels / Opens' });
  if (SESSIONS) INDICATOR_DEFS.push({ id: 'sessions', label: 'Market Sessions', kind: 'sessions', defaultEnabled: false, settingsSchema: SESSIONS.settingsSchema(),
    labelForSettings: () => 'Market Sessions' });
  // Order-flow overlays (static/indicators/bubbles*.js, volume-anomaly.js, orderflow-pane.js).
  if (window.WorkspaceBubbles && window.WorkspaceBubblesRender) INDICATOR_DEFS.push(window.WorkspaceBubbles.definition);
  if (window.WorkspaceVolumeAnomaly) INDICATOR_DEFS.push(window.WorkspaceVolumeAnomaly.definition);
  INDICATOR_DEFS.push(...(window.WorkspaceIndicatorExtras?.definitions || []).map(def => def.kind !== 'band' ? def : ({ ...def, settingsSchema: [...def.settingsSchema,
    { key: 'innerBoundary', label: 'Visible inner boundary', type: 'select', default: def.innerBoundary || 'lower', options: ['upper', 'lower'] },
    { key: 'showOuter', label: 'Show outer boundary', type: 'boolean', default: false }
  ].filter((field, index, fields) => fields.findIndex(other => other.key === field.key) === index) })));
  const INDICATOR_BY_ID = new Map(INDICATOR_DEFS.map(def => [def.id, def]));
  function defaultIndicatorSettings() {
    const defaults = {};
    INDICATOR_DEFS.forEach(def => {
      defaults[def.id] = {};
      def.settingsSchema.forEach(field => { defaults[def.id][field.key] = field.default; });
    });
    return defaults;
  }
  function sanitizeFieldValue(field, value) {
    if (field.type === 'number') {
      const num = Number(value);
      // Integer-only schemas recover corrupt persisted fractions to their
      // documented default; fractional-step fields retain their precision.
      if (!Number.isFinite(num) || (field.step === 1 && !Number.isInteger(num))) return field.default;
      const min = typeof field.min === 'number' ? field.min : -Infinity;
      const max = typeof field.max === 'number' ? field.max : Infinity;
      return Math.min(max, Math.max(min, num));
    }
    if (field.type === 'select') return field.options.includes(value) ? value : field.default;
    if (field.type === 'boolean') return typeof value === 'boolean' ? value : field.default;
    if (field.type === 'color') return typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value) ? value : field.default;
    if (field.type === 'text') return typeof value === 'string' && value.trim() ? value.trim().slice(0, field.maxLength || 40) : field.default;
    if (field.type === 'session') return typeof value === 'string' && /^(?:[01]\d|2[0-3])[0-5]\d-(?:[01]\d|2[0-3])[0-5]\d$/.test(value.trim()) && value.trim().slice(0, 4) !== value.trim().slice(5) ? value.trim() : field.default;
    return field.default;
  }
  // Merge saved (possibly stale/missing/corrupt) indicator settings onto the
  // documented defaults, field by field, so unknown/invalid values never
  // reach compute() and new fields silently pick up their default.
  function sanitizeIndicatorSettings(saved) {
    const result = defaultIndicatorSettings();
    if (!saved || typeof saved !== 'object') return result;
    INDICATOR_DEFS.forEach(def => {
      const savedEntry = saved[def.id];
      if (!savedEntry || typeof savedEntry !== 'object') return;
      def.settingsSchema.forEach(field => {
        if (Object.hasOwn(savedEntry, field.key)) result[def.id][field.key] = sanitizeFieldValue(field, savedEntry[field.key]);
      });
    });
    return result;
  }
  const $ = (selector, root = document) => root.querySelector(selector);
  const $all = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const grid = $('#chart-grid');
  const footer = $('#workspace-status');
  const panes = new Map();
  let analyst = null; // static/analyst-panel.js controller (analysis state; rendered only by the dashboard)
  let dashboard = null, disposeWheelGuard = null; // static/analysis-dashboard.js (full width, below the charts)
  // Seconds intervals are trade-built in the browser (static/seconds-candles.js); never fabricated.
  const SECONDS_API = window.WorkspaceSeconds || null;
  let intervals = FAVORITES.map(([value, label]) => ({ value, label, available: !/s$/.test(value) || Boolean(SECONDS_API), reason: /s$/.test(value) ? (SECONDS_API ? 'Built from real trades (live stream; Binance also backfills recent minutes)' : 'Seconds candles module did not load') : '' }));
  let intervalWarning = '';
  let pricesController = null;
  let pricesGeneration = 0;
  let pricesTimeout = null;
  let initialized = false;
  const marketCache = new Map(); // Metadata only; snapshot freshness is owned by /api/markets.
  let currentMarkets = [];
  let catalogReady = false;
  const numberFormat = new Intl.NumberFormat('en-US', { maximumSignificantDigits: 8 });
  const timeFormat = new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

  const defaultPanes = () => [
    { symbol: 'BTCUSDT', interval: '240' }, { symbol: 'ETHUSDT', interval: '240' },
    { symbol: 'SOLUSDT', interval: '240' }, { symbol: '1000BONKUSDT', interval: 'D' },
    { symbol: 'BNBUSDT', interval: '60' }, { symbol: 'XRPUSDT', interval: '60' }
  ].map(pane => ({ ...pane, exchange: 'bybit' }));
  const normalizeSymbol = (value, exchange = 'bybit') => typeof value !== 'string' ? '' : exchange === 'bybit' ? value.trim().toUpperCase().replace(/^BYBIT:/, '').replace(/\.P$/, '').replace(/^([A-Z0-9]+)\/(USDT|USDC)$/, '$1$2') : value.trim();
  const validSymbol = (value, exchange = 'bybit') => typeof value === 'string' && (exchange === 'hyperliquid' ? /^(?:[a-z][a-z0-9_-]{0,31}:)?[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/.test(value) && !/^(?:bybit|binance|hyperliquid):/i.test(value) : exchange === 'bybit' ? /^[A-Z0-9]{1,26}(?:USDT|USDC|PERP)$/.test(value) : exchange === 'binance' && /^[\p{L}\p{N}]{1,60}(?:USDT|USDC)$/u.test(value));
  const validInterval = value => typeof value === 'string' && /^(?:[1-9]\d{0,5}|[23]?D|W|M|1s|5s|15s|30s)$/.test(value);
  const isSecondsInterval = value => /^(?:1|5|15|30)s$/.test(value);
  function readState() {
    const fallback = { version: VERSION, layout: '4grid', active: 0, panes: defaultPanes(), watchlistExchange: 'bybit', watchlistSearch: '', watchlistFavoritesOnly: false, watchlistSort: 'none', fvg: true, auto: false, sidebar: true, localAlerts: false, indicatorEnabled: Object.fromEntries(INDICATOR_DEFS.map(d => [d.id, Boolean(d.defaultEnabled)])), indicatorSettings: defaultIndicatorSettings() };
    try {
      // v4 -> v3 -> v2 -> v1 fallback chain: read whichever key exists, newest
      // first. Every older shape is a strict subset of the v4 fields below, so
      // the same per-field merge handles all of them uniformly.
      let saved = null;
      for (const key of [STORAGE_KEY, ...PREVIOUS_KEYS]) {
        const raw = localStorage.getItem(key);
        if (raw) { saved = JSON.parse(raw); break; }
      }
      if (!saved || ![1, 2, 3, 4, VERSION].includes(saved.version) || !Object.hasOwn(LAYOUTS, saved.layout)) return fallback;
      if (saved.version < VERSION && GROUPS) { const migrated = GROUPS.migrateLegacyState(saved); saved = migrated.saved; fallback.migrationNotes = migrated.notes; }
      fallback.layout = saved.layout;
      if (Array.isArray(saved.panes)) saved.panes.slice(0, 6).forEach((pane, index) => {
        const exchange = saved.version === 1 ? 'bybit' : pane?.exchange;
        if (pane && Object.hasOwn(EXCHANGES, exchange) && validSymbol(pane.symbol, exchange) && validInterval(pane.interval)) fallback.panes[index] = { symbol: pane.symbol, interval: pane.interval, exchange, ...(pane.footprint === true ? { footprint: true } : {}) };
      });
      if (Object.hasOwn(EXCHANGES, saved.watchlistExchange)) fallback.watchlistExchange = saved.watchlistExchange;
      if (typeof saved.watchlistSearch === 'string') fallback.watchlistSearch = saved.watchlistSearch.slice(0, 80);
      if (typeof saved.watchlistFavoritesOnly === 'boolean') fallback.watchlistFavoritesOnly = saved.watchlistFavoritesOnly;
      if (['none', 'asc', 'desc'].includes(saved.watchlistSort)) fallback.watchlistSort = saved.watchlistSort;
      fallback.active = Number.isInteger(saved.active) && saved.active >= 0 && saved.active < LAYOUTS[fallback.layout] ? saved.active : 0;
      ['fvg', 'auto', 'sidebar'].forEach(key => { if (typeof saved[key] === 'boolean') fallback[key] = saved[key]; });
      // New in v4; absent in v1-v3 saves, which fall through to documented defaults.
      fallback.indicatorSettings = sanitizeIndicatorSettings(saved.indicatorSettings);
      INDICATOR_DEFS.forEach(def => {
        if (typeof saved.indicatorEnabled?.[def.id] === 'boolean') fallback.indicatorEnabled[def.id] = saved.indicatorEnabled[def.id];
      });
      if (typeof saved.localAlerts === 'boolean') fallback.localAlerts = saved.localAlerts;
    } catch (_) { /* Corrupt or unavailable browser storage must not break charts. */ }
    return fallback;
  }
  const state = readState();
  const migrationNotes = state.migrationNotes || []; delete state.migrationNotes;
  // Deep link from the /scalp DOM ("chart ↗"): ?exchange=…&symbol=… retargets the active pane only.
  try {
    const deep = new URLSearchParams(location.search), deepExchange = deep.get('exchange'), deepSymbol = deep.get('symbol');
    if (deepExchange && deepSymbol && Object.hasOwn(EXCHANGES, deepExchange) && validSymbol(deepSymbol, deepExchange)) {
      state.panes[state.active] = { ...state.panes[state.active], symbol: deepSymbol, exchange: deepExchange };
    }
  } catch (_) { /* malformed deep links leave the saved layout untouched */ }
  function saveState() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); }
    catch (_) { footer.textContent = 'Browser storage unavailable · settings last for this session'; }
  }
  function getIndicatorSettings(id) {
    return state.indicatorSettings[id] || defaultIndicatorSettings()[id];
  }
  function setIndicatorSettings(id, patch) {
    const def = INDICATOR_BY_ID.get(id);
    if (!def) return;
    const current = { ...getIndicatorSettings(id) };
    def.settingsSchema.forEach(field => {
      if (Object.hasOwn(patch, field.key)) current[field.key] = sanitizeFieldValue(field, patch[field.key]);
    });
    state.indicatorSettings[id] = current;
    saveState();
  }
  function resetIndicatorSettings(id) {
    const def = INDICATOR_BY_ID.get(id);
    if (!def) return;
    const defaults = {};
    def.settingsSchema.forEach(field => { defaults[field.key] = field.default; });
    state.indicatorSettings[id] = defaults;
    saveState();
  }
  function scaleAlpha(color, baseAlpha, multiplier) {
    const alpha = Math.max(0, Math.min(1, baseAlpha * (Number.isFinite(multiplier) ? multiplier : 1)));
    const hex = /^#([0-9a-f]{6})$/i.exec(color || '');
    if (!hex) return color || `rgba(0,0,0,${alpha})`;
    const r = parseInt(hex[1].slice(0, 2), 16);
    const g = parseInt(hex[1].slice(2, 4), 16);
    const b = parseInt(hex[1].slice(4, 6), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }
  // Band-kind indicators (e.g. the internal demo-band, and the follow-up
  // Turtle Zone) have no dedicated toolbar chip; track enablement here
  // instead of inventing a checkbox for a hidden/test-only entry.

  function isIndicatorEnabled(id) {
    if (id === 'fvg') return state.fvg;
    return state.indicatorEnabled[id] ?? false;
  }
  function setIndicatorEnabled(id, value) {
    if (!INDICATOR_BY_ID.has(id)) return;
    state.indicatorEnabled[id] = Boolean(value);
    saveState();
  }
  function readFavorites() {
    try {
      const raw = JSON.parse(localStorage.getItem(FAVORITES_KEY) || '[]');
      return new Set(Array.isArray(raw) ? raw.filter(value => typeof value === 'string') : []);
    } catch (_) { return new Set(); }
  }
  const favorites = readFavorites();
  function saveFavorites() {
    try { localStorage.setItem(FAVORITES_KEY, JSON.stringify([...favorites])); } catch (_) { /* best-effort */ }
  }
  const favoriteKey = (exchange, symbol) => `${exchange}:${symbol}`;
  const isFavorite = (exchange, symbol) => favorites.has(favoriteKey(exchange, symbol));
  function toggleFavorite(exchange, symbol) {
    const key = favoriteKey(exchange, symbol);
    if (favorites.has(key)) favorites.delete(key); else favorites.add(key);
    saveFavorites();
  }
  // Nulls always sort last, independent of direction; never NaN-compare.
  function compareChange(left, right, direction) {
    const a = typeof left === 'number' && Number.isFinite(left) ? left : null;
    const b = typeof right === 'number' && Number.isFinite(right) ? right : null;
    if (a === null && b === null) return 0;
    if (a === null) return 1;
    if (b === null) return -1;
    return direction === 'desc' ? b - a : a - b;
  }
  function statusText(message) { footer.textContent = message; }
  function formatPrice(value) { return Number.isFinite(Number(value)) ? numberFormat.format(Number(value)) : '—'; }
  function intervalLabel(value) { return intervals.find(item => item.value === value)?.label || FAVORITES.find(item => item[0] === value)?.[1] || value; }
  function sourceLabel(source) {
    if (typeof source === 'string' && source.trim()) return source.trim();
    return 'Provider unspecified';
  }
  function errorText(data, fallback) {
    if (typeof data?.error === 'string') return data.error;
    if (typeof data?.error?.message === 'string') return data.error.message;
    if (typeof data?.message === 'string') return data.message;
    return fallback;
  }
  function makeOption(item) {
    const option = document.createElement('option');
    option.value = item.value;
    option.textContent = item.available === false ? `${item.label} · unavailable` : item.label;
    option.disabled = item.available === false;
    if (item.reason) option.title = item.reason;
    return option;
  }
  function populateIntervals(select, selected) {
    select.replaceChildren(...intervals.map(makeOption));
    if (!intervals.some(item => item.value === selected)) {
      const option = makeOption({ value: selected, label: `${intervalLabel(selected)} · not listed`, available: false });
      select.append(option);
    }
    select.value = selected;
  }
  function syncControls() {
    const active = state.panes[state.active];
    $('#symbol-input').value = active.symbol;
    $('#symbol-input').setCustomValidity('');
    populateIntervals($('#interval-select'), active.interval);
    $('#layout-select').value = state.layout;
    $('#favorite-intervals').querySelectorAll('button').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.interval === active.interval));
      const metadata = intervals.find(item => item.value === button.dataset.interval);
      button.disabled = !metadata || metadata.available === false;
      button.title = metadata?.reason || `Set active chart to ${button.textContent}`;
    });
    document.querySelectorAll('.watchlist-row').forEach(button => {
      const selected = button.dataset.symbol === active.symbol && button.dataset.exchange === active.exchange;
      button.classList.toggle('is-selected', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
    $('#watchlist-toggle').setAttribute('aria-expanded', String(state.sidebar));
    $('#workspace').classList.toggle('sidebar-collapsed', !state.sidebar);
    syncSuggestions();
    analyst?.activeChanged();
  }
  function setActive(id) {
    if (!panes.has(id)) return;
    state.active = id;
    panes.forEach((pane, key) => {
      pane.element.classList.toggle('is-active', key === id);
      pane.element.setAttribute('aria-label', `Chart ${key + 1}: ${state.panes[key].exchange} ${state.panes[key].symbol}, ${intervalLabel(state.panes[key].interval)}${key === id ? ', active' : ''}`);
    });
    syncControls();
    statusText(`${LAYOUTS[state.layout]} independent ${LAYOUTS[state.layout] === 1 ? 'chart' : 'charts'} · active chart ${id + 1}${intervalWarning ? ` · ${intervalWarning}` : ' · UTC'}`);
    saveState();
  }
  function updatePaneConfig(id, patch) {
    if (!panes.has(id)) return;
    const next = { ...state.panes[id], ...patch };
    if (!Object.hasOwn(EXCHANGES, next.exchange) || !validSymbol(next.symbol, next.exchange) || !validInterval(next.interval)) return;
    state.panes[id] = next;
    setActive(id);
    panes.get(id).load(true);
  }
  function submitSymbol(input, id) {
    const exchange = state.panes[id].exchange;
    const normalized = normalizeSymbol(input.value, exchange);
    const metadata = marketCache.get(exchange) || [];
    const exact = metadata.find(market => market.symbol === normalized);
    const displayMatches = metadata.filter(market => market.display_symbol === input.value.trim());
    const value = exact?.symbol || (displayMatches.length === 1 ? displayMatches[0].symbol : normalized);
    if (!validSymbol(value, exchange)) {
      input.setCustomValidity(`Use an exact ${EXCHANGES[exchange]} native market symbol.${exchange === 'bybit' ? ' Also accepts BTC/USDT or BYBIT:BTCUSDT.P.' : ' No foreign exchange prefixes or contract-unit conversion.'}`);
      input.reportValidity();
      return false;
    }
    input.setCustomValidity('');
    updatePaneConfig(id, { symbol: value });
    return true;
  }
  function intervalSeconds(interval, anchor) {
    if (/^(?:1|5|15|30)s$/.test(interval)) return Number(interval.slice(0, -1)); // trade-built seconds
    if (interval === 'M') {
      const date = new Date(anchor * 1000);
      return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1) / 1000 - anchor;
    }
    return { D: 86400, '2D': 172800, '3D': 259200, W: 604800 }[interval] || Number(interval) * 60;
  }
  function parseCandles(raw) {
    if (!Array.isArray(raw)) throw new Error('Provider response has no candle array.');
    const seen = new Map();
    raw.forEach(candle => {
      const values = ['ts', 'o', 'h', 'l', 'c', 'v'].map(key => Number(candle?.[key]));
      if (values.some(value => !Number.isFinite(value))) throw new Error('Provider returned an invalid candle.');
      const [timestamp, open, high, low, close, volume] = values;
      if (timestamp <= 0 || Math.min(open, high, low, close) <= 0 || high < Math.max(open, close, low) || low > Math.min(open, close, high) || volume < 0) throw new Error('Provider returned an invalid OHLCV range.');
      const time = Math.floor(timestamp / 1000);
      seen.set(time, { time, open, high, low, close, volume });
    });
    return Array.from(seen.values()).sort((left, right) => left.time - right.time);
  }

  function localFvgEvents(previous, candles, settings) {
    if (!previous.length || !candles.length) return [];
    const last = previous.at(-1).time;
    const events = [];
    let zones = fvgZones(previous, settings);
    for (let i = 0; i < candles.length; i += 1) {
      const bar = candles[i]; if (bar.time <= last) continue;
      zones.forEach(zone => {
        const bound = settings.fillPolicy === 'close' ? bar.close : zone.direction === 'bull' ? bar.low : bar.high;
        if (zone.direction === 'bull' ? bound <= zone.bottom : bound >= zone.top) events.push({ type: 'fully filled', time: bar.time, startTime: zone.startTime, direction: zone.direction });
      });
      zones = fvgZones(candles.slice(0, i + 1), settings);
      zones.filter(z => z.startTime === bar.time).forEach(zone => events.push({ type: 'created', time: bar.time, startTime: zone.startTime, direction: zone.direction }));
    }
    return events;
  }
  const localAlertJournal = new Map();
  const localAlertFeed = [];
  try {
    const saved = JSON.parse(localStorage.getItem('crypto-screener.local-fvg.v1') || '{}');
    if (Array.isArray(saved.journal)) saved.journal.slice(-100).forEach(([key, time]) => { if (typeof key === 'string' && Number.isFinite(time)) localAlertJournal.set(key, time); });
    if (Array.isArray(saved.feed)) saved.feed.slice(0, 50).forEach(event => { if (typeof event?.message === 'string' && typeof event?.key === 'string') localAlertFeed.push(event); });
  } catch (_) { /* Storage optional; no external notification API. */ }
  function renderLocalAlerts() {
    const feed = $('#local-alerts-feed'); if (!feed) return;
    $('#local-alerts-count').textContent = String(localAlertFeed.length);
    feed.replaceChildren();
    localAlertFeed.forEach(event => { const item = document.createElement('li'); item.textContent = event.message; feed.append(item); });
    if (!localAlertFeed.length) { const item = document.createElement('li'); item.textContent = 'No local FVG events. Historical load is silent.'; feed.append(item); }
  }
  function observeLocalFvg(config, previous, candles) {
    if (!candles.length) return;
    const scope = JSON.stringify([config.exchange, config.symbol, config.interval]);
    const since = localAlertJournal.get(scope) ?? candles.at(-1).time;
    if (state.localAlerts) localFvgEvents(previous, candles, getIndicatorSettings('fvg')).filter(e => e.time > since).forEach(event => {
      const key = `${scope}:${event.type}:${event.startTime}`;
      if (localAlertFeed.some(item => item.key === key)) return;
      localAlertFeed.unshift({ key, message: `${EXCHANGES[config.exchange]} ${config.symbol} ${intervalLabel(config.interval)} · ${event.direction} FVG ${event.type} · ${timeFormat.format(new Date(event.time * 1000))} UTC` });
    });
    localAlertJournal.delete(scope); localAlertJournal.set(scope, Math.max(since, candles.at(-1).time));
    while (localAlertJournal.size > 100) localAlertJournal.delete(localAlertJournal.keys().next().value);
    localAlertFeed.splice(50);
    try { localStorage.setItem('crypto-screener.local-fvg.v1', JSON.stringify({ journal: [...localAlertJournal], feed: localAlertFeed })); } catch (_) { /* best effort */ }
    renderLocalAlerts();
  }
  const streamHub = window.WorkspaceMarketStream?.createHub();
  const liquidityClientId = (() => {
    const key = 'crypto-screener.liquidity.client.v1';
    try { const saved = sessionStorage.getItem(key); if (saved) return saved; } catch (_) { /* optional storage */ }
    const id = window.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    try { sessionStorage.setItem(key, id); } catch (_) { /* session-only identity */ }
    return id;
  })();

  class Pane {
    constructor(id) {
      this.id = id;
      this.disposed = false;
      this.generation = 0;
      this.lifetime = new AbortController();
      this.request = null;
      this.requestTimer = null;
      this.raf = null;
      this.interactionUntil = 0;
      this.dragging = false;
      this.candles = [];
      this.rawZones = [];
      this.emas = {};
      this.emaValues = {};
      this.priceLine = null;
      this.tickerKey = null;
      this.tickerGeneration = 0;
      this.tickerController = null;
      this.tickerTimer = null;
      this.pluginLines = new Map();
      this.contextGeneration = 0;
      this.contextController = null;
      this.element = document.createElement('section');
      this.element.className = 'chart-pane';
      this.element.dataset.paneId = String(id);
      this.element.dataset.state = 'loading';
      this.element.tabIndex = 0;
      this.element.innerHTML = `
        <header class="pane-header">
          <span class="pane-index">${id + 1}</span>
          <details class="pane-symbol-menu">
            <summary aria-label="Change symbol for chart ${id + 1}"><span class="pane-title"></span></summary>
            <form class="pane-symbol-popover">
              <label for="pane-symbol-${id}">Symbol · chart ${id + 1}</label>
              <div class="symbol-row"><input id="pane-symbol-${id}" class="pane-symbol-input" list="symbol-suggestions" maxlength="80" spellcheck="false" autocomplete="off" required><button type="submit">Apply</button></div>
              <p>Use this pane’s venue-native symbol. Bybit also accepts BTC/USDT and BYBIT:BTCUSDT.P.<br>Changes this chart only; no venue fallback.</p>
            </form>
          </details>
          <select class="pane-interval" aria-label="Interval for chart ${id + 1}"></select>
          <span class="pane-source" title="Data provider from chart response">—</span>
          <span class="pane-funding" data-sign="none" hidden></span>
          <span class="pane-closed" title="Only completed candles are requested">CLOSED ONLY</span>
          <button type="button" class="pane-footprint-toggle" aria-pressed="false" aria-label="Footprint for chart ${id + 1}" title="Footprint (bid × ask order flow) for this chart">FP</button>
          <button type="button" class="pane-footprint-settings" aria-label="Footprint settings" title="Footprint settings">⚙</button>
          <button type="button" class="pane-reload" aria-label="Reload chart ${id + 1}" title="Reload this chart">↻</button>
        </header>
        <div class="chart-stage">
          <div class="chart-host"></div><canvas class="fvg-overlay" aria-hidden="true"></canvas>
          <div class="chart-legend"><span class="ohlc">Waiting for provider…</span><span class="legend-ema" data-ema="r0"></span><span class="legend-ema" data-ema="r1"></span><span class="legend-ema" data-ema="r2"></span><span class="legend-ema ema-200" data-ema="200"></span><span class="pane-live-price" data-state="hidden"></span></div>
          <div class="chart-message"><span>Loading closed candles…</span></div>
        </div>
        <div class="pane-liquidity-status" role="status" hidden></div><div class="pane-footprint-status" role="status" hidden></div>
        <details class="pane-context"><summary>Market context · funding / OI / EMA bias</summary><div class="pane-context-body">—</div></details><div class="pane-plugin-warnings" hidden></div>
        <div class="pane-statusbar"><span class="pane-status" role="status">Connecting to market data…</span><span class="pane-count"></span></div>`;
      grid.append(this.element);
      this.host = $('.chart-host', this.element);
      this.canvas = $('.fvg-overlay', this.element);
      this.context = this.canvas.getContext('2d');
      this.message = $('.chart-message', this.element);
      this.status = $('.pane-status', this.element);
      this.count = $('.pane-count', this.element);
      this.symbolInput = $('.pane-symbol-input', this.element);
      this.livePriceEl = $('.pane-live-price', this.element);
      this.chart = LightweightCharts.createChart(this.host, {
        width: Math.max(1, this.host.clientWidth), height: Math.max(1, this.host.clientHeight),
        layout: { background: { type: 'solid', color: '#10141c' }, textColor: '#76849b', fontSize: 10, fontFamily: 'SFMono-Regular, Consolas, monospace' },
        grid: { vertLines: { color: '#1a2130' }, horzLines: { color: '#1a2130' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal, vertLine: { color: '#65748d', width: 1, style: 2, labelBackgroundColor: '#32415c' }, horzLine: { color: '#65748d', width: 1, style: 2, labelBackgroundColor: '#32415c' } },
        rightPriceScale: { borderColor: '#283145', scaleMargins: { top: 0.14, bottom: 0.23 }, minimumWidth: 65 },
        leftPriceScale: { visible: false },
        timeScale: { borderColor: '#283145', timeVisible: true, secondsVisible: false, rightOffset: 6, barSpacing: 6, minBarSpacing: 2 },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
        handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true }
      });
      this.series = this.chart.addCandlestickSeries({ upColor: '#00c087', downColor: '#ef6670', borderVisible: false, wickUpColor: '#00c087', wickDownColor: '#ef6670', priceLineVisible: true, lastValueVisible: true });
      this.initLiquidity();
      this.initFootprint();
      this.volume = this.chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'volume', lastValueVisible: false, priceLineVisible: false });
      this.chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.84, bottom: 0 }, visible: false });
      // EMA ribbon (3 lines) + EMA 200 and the MA channel's two lines are native line series.
      const lineSeries = () => this.chart.addLineSeries({ lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, visible: false });
      [...RIBBON_KEYS, '200'].forEach(key => { this.emas[key] = lineSeries(); });
      this.ma20 = { high: lineSeries(), low: lineSeries() };
      this.groupModels = {}; this.htf = null; this.htfKey = null;
      this.htfLoader = OPENS?.createLoader({ fetch: (url, options) => fetch(url, options) });
      this.initDrawings();
      this.initOrderflow();
      this.initCountdown();
      this.logicalListener = () => { this.scheduleDraw(); this.queueLiquidityRange(); };
      this.crosshairListener = parameter => {
        const candle = parameter.seriesData?.get(this.series);
        this.updateLegend(candle && typeof candle.close === 'number' ? candle : this.candles.at(-1));
        this.liquidityTooltip(parameter);
        this.orderflow?.tooltip(parameter.point);
        this.scheduleDraw();
      };
      this.chart.timeScale().subscribeVisibleLogicalRangeChange(this.logicalListener);
      this.chart.subscribeCrosshairMove(this.crosshairListener);
      const listen = (target, event, handler, options = {}) => target.addEventListener(event, handler, { ...options, signal: this.lifetime.signal });
      listen(this.element, 'pointerdown', () => { if (state.active !== id) setActive(id); });
      listen(this.element, 'focusin', () => { if (state.active !== id) setActive(id); });
      listen(this.element, 'keydown', event => { if (event.key === 'Escape') $('.pane-symbol-menu', this.element).open = false; });
      listen($('.pane-symbol-popover', this.element), 'submit', event => {
        event.preventDefault();
        if (submitSymbol(this.symbolInput, id)) $('.pane-symbol-menu', this.element).open = false;
      });
      listen(this.symbolInput, 'input', () => this.symbolInput.setCustomValidity(''));
      listen($('.pane-symbol-menu', this.element), 'toggle', () => {
        if ($('.pane-symbol-menu', this.element).open) {
          if (state.active !== id) setActive(id);
          this.symbolInput.focus();
          this.symbolInput.select();
        }
      });
      listen($('.pane-interval', this.element), 'change', event => updatePaneConfig(id, { interval: event.target.value }));
      listen($('.pane-reload', this.element), 'click', () => { setActive(id); this.load(false, true); });
      listen($('.pane-footprint-toggle', this.element), 'click', () => {
        state.panes[id] = { ...state.panes[id], footprint: !state.panes[id].footprint };
        if (!state.panes[id].footprint) delete state.panes[id].footprint;
        saveState(); this.syncFootprint(); this.scheduleDraw();
      });
      listen($('.pane-footprint-settings', this.element), 'click', event => {
        event.preventDefault(); event.stopPropagation();
        if (settingsPopoverId === 'footprint') closeSettingsPopover(); else openSettingsPopover('footprint', event.currentTarget);
      });
      listen($('.pane-context', this.element), 'toggle', () => this.refreshContext());
      this.contextTimer = setInterval(() => this.refreshContext(), 60000);
      listen(document, 'visibilitychange', () => { this.syncLiquidity(); });
      listen(grid, 'scroll', () => this.syncLiquidity(), { passive: true });
      this.liquidityTimer = setInterval(() => { this.syncLiquidity(); this.renderLiquidityStatus(); }, 1000);
      // Logical-range subscriptions cover time navigation. Price-axis drags do not
      // emit those events in 4.1, so repaint through gestures and their inertia.
      listen(this.host, 'pointerdown', () => { this.dragging = true; this.animateInteraction(); });
      listen(window, 'pointerup', () => { if (this.dragging) { this.dragging = false; this.animateInteraction(); } });
      listen(window, 'pointercancel', () => { this.dragging = false; this.animateInteraction(); });
      listen(this.host, 'wheel', () => this.animateInteraction(), { passive: true });
      listen(this.host, 'dblclick', () => this.animateInteraction());
      listen(this.host, 'touchmove', () => this.animateInteraction(), { passive: true });
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(this.host);
      this.updateHeader();
      this.load(true);
    }
    initLiquidity() {
      this.liquidityData = null; this.liquidityModel = null; this.liquidityState = {};
      this.liquidityStatus = $('.pane-liquidity-status', this.element);
      this.liquidityTip = document.createElement('div'); this.liquidityTip.className = 'liquidity-tooltip'; this.liquidityTip.hidden = true;
      $('.chart-stage', this.element).append(this.liquidityTip);
      const api = window.WorkspaceLiquidity;
      if (!api || typeof this.series.attachPrimitive !== 'function') { this.liquidityLayer = 'unavailable'; return; }
      this.liquidityLayer = 'below-candles';
      this.liquidityPrimitive = new api.HeatmapPrimitive(size => this.buildLiquidityModel(size), () => getIndicatorSettings('liquidity'));
      this.series.attachPrimitive(this.liquidityPrimitive);
      this.liquiditySession = new api.CollectionSession({owner: `${liquidityClientId}:pane${this.id}`, fetch: (url, options) => fetch(url, options),
        visible: () => !this.disposed && document.visibilityState === 'visible' && isVisible(this), range: () => this.liquidityRange(),
        onData: data => { this.liquidityData = data; this.renderLiquidityStatus(); this.liquidityPrimitive?.requestUpdate?.(); },
        onStatus: status => { this.liquidityState = status; this.renderLiquidityStatus(); }
      });
    }
    initFootprint() {
      this.footprintStatus = $('.pane-footprint-status', this.element);
      const api = window.WorkspaceFootprint;
      if (!api) return;
      this.footprint = api.createController({ fetch: (url, options) => fetch(url, options), hub: streamHub,
        visible: () => !this.disposed && document.visibilityState === 'visible' && isVisible(this),
        onChange: () => { this.renderFootprintStatus(); this.scheduleDraw(); } });
    }
    initOrderflow() {
      const api = window.WorkspaceOrderflowPane;
      if (!api) return;
      const config = () => state.panes[this.id];
      this.orderflow = api.attach({ pane: this, hub: streamHub, fetch: (url, options) => fetch(url, options), stage: $('.chart-stage', this.element),
        statusAfter: this.footprintStatus, isEnabled: isIndicatorEnabled, settings: getIndicatorSettings, config, format: price => this.series.priceFormatter().format(price),
        step: () => intervalSeconds(config().interval, this.candles.at(-1)?.time || Date.now() / 1000),
        tickKnown: () => Boolean(this.instrument || this.instrumentError), tick: () => venueTick(this.instrument, this.lastDisplayPrice ?? this.candles.at(-1)?.close),
        forming: () => (this.seconds ? this.seconds.view().forming : this.forming) || null,
        mainFootprint: () => (config().footprint ? this.footprint?.store || null : null), requestDraw: () => this.scheduleDraw() });
    }
    // Drawing tools + shared price levels live in static/drawings.js; the pane only maps pixels.
    initDrawings() {
      const api = window.WorkspaceDrawings;
      if (!api) return;
      this.drawings = api.createController({ header: $('.pane-header', this.element), stage: $('.chart-stage', this.element), paneElement: this.element,
        levels: window.ScreenerLevels || null, requestDraw: () => this.scheduleDraw(), getContext: () => this.drawingContext(),
        onError: message => { this.status.textContent = `Levels store: ${message}`; this.status.title = this.status.textContent; } });
    }
    refreshInstrument() {
      const config = state.panes[this.id], key = `${config.exchange}:${config.symbol}`;
      this.instrumentKey = key; this.instrument = null; this.instrumentError = null;
      fetchInstrument(config.exchange, config.symbol).then(meta => { if (this.instrumentKey === key && !this.disposed) { this.instrument = meta; this.scheduleDraw(); } })
        .catch(error => { if (this.instrumentKey === key) { this.instrumentError = error.message; this.instrumentKey = null; } });
    }
    drawingContext() {
      const config = state.panes[this.id];
      const tickSize = venueTick(this.instrument, this.lastDisplayPrice ?? this.candles.at(-1)?.close);
      const fp = config.footprint ? this.footprint?.store : null;
      return { exchange: config.exchange, symbol: config.symbol, interval: config.interval, candles: this.candles, chart: this.chart, series: this.series, host: this.host,
        footprint: fp || null, footprintVersion: fp ? `${fp.bars?.size}:${this.footprint.status?.state}` : 0,
        stepMs: intervalSeconds(config.interval, this.candles.at(-1)?.time || Date.now() / 1000) * 1000,
        precision: this.series.options().priceFormat?.precision ?? 2,
        tickSize, tickSource: tickSize ? `${config.exchange} ${this.instrument.tick_rule === 'fixed' ? 'tickSize' : 'szDecimals rule'}` : 'chart precision',
        plotWidth: Math.max(0, this.host.clientWidth - this.chart.priceScale('right').width()),
        plotHeight: Math.max(0, this.host.clientHeight - this.chart.timeScale().height()) };
    }
    // Countdown to the active interval's candle close, pinned under the last-price axis label.
    initCountdown() {
      this.countdownEl = document.createElement('div');
      this.countdownEl.className = 'pane-countdown'; this.countdownEl.hidden = true; this.countdownEl.title = 'Time to candle close (UTC boundaries)';
      $('.chart-stage', this.element).append(this.countdownEl);
      this.countdownTimer = setInterval(() => this.renderCountdown(), 1000);
    }
    renderCountdown() {
      const api = window.WorkspaceSeconds, el = this.countdownEl;
      if (!api || !el || this.disposed) return;
      const interval = state.panes[this.id].interval, now = Date.now();
      const close = api.nextCloseMs(interval, now);
      const price = this.lastDisplayPrice ?? this.candles.at(-1)?.close;
      const y = Number.isFinite(price) && this.candles.length ? this.series.priceToCoordinate(price) : null;
      const plotHeight = this.host.clientHeight - this.chart.timeScale().height();
      if (close === null || y === null || y < 0 || y > plotHeight - 12) { el.hidden = true; return; }
      el.hidden = false;
      el.textContent = api.formatCountdown(close - now);
      el.style.top = `${Math.round(y + 9)}px`;
      el.style.width = `${this.chart.priceScale('right').width()}px`;
      el.dataset.interval = interval;
    }
    syncFootprint() {
      if (this.disposed) return;
      const config = state.panes[this.id], enabled = Boolean(config.footprint);
      const button = $('.pane-footprint-toggle', this.element);
      button.setAttribute('aria-pressed', String(enabled)); button.classList.toggle('is-active', enabled);
      $('.pane-footprint-settings', this.element).hidden = !enabled;
      if (!this.footprint) { this.footprintStatus.hidden = !enabled; this.footprintStatus.textContent = enabled ? 'Footprint unavailable: module did not load.' : ''; return; }
      const ui = getIndicatorSettings('footprint');
      if (enabled) this.footprint.enable({ exchange: config.exchange, symbol: config.symbol, interval: config.interval, bars: ui.bars, groupTicks: ui.grouping === 'manual' ? ui.groupTicks : null });
      else this.footprint.disable();
      // Hollow candles keep OHLC visible underneath the cells; restored when off.
      if (this.footprintStyled !== enabled) {
        this.footprintStyled = enabled;
        this.series.applyOptions(enabled
          ? { upColor: 'rgba(0,192,135,0.10)', downColor: 'rgba(239,102,112,0.10)', borderVisible: true, borderUpColor: '#00c087', borderDownColor: '#ef6670' }
          : { upColor: '#00c087', downColor: '#ef6670', borderVisible: false });
      }
      this.renderFootprintStatus();
    }
    renderFootprintStatus() {
      if (!this.footprintStatus || !this.footprint) return;
      const enabled = Boolean(state.panes[this.id].footprint);
      this.footprintStatus.hidden = !enabled;
      if (!enabled) return;
      const text = window.WorkspaceFootprint.statusText(this.footprint) + (this.footprintDraw?.mode ? ` · view: ${this.footprintDraw.mode === 'cells' ? 'cells' : this.footprintDraw.mode === 'marks' ? 'zoomed out: POC/imbalance marks + delta footer' : 'delta footer only'}${this.footprintDraw.group > 1 ? ` (×${this.footprintDraw.group} rows)` : ''}` : '');
      if (text !== this.footprintStatus.textContent) { this.footprintStatus.textContent = text; this.footprintStatus.title = text; }
    }
    drawFootprint(context, plotWidth, plotHeight) {
      const store = this.footprint?.store;
      if (!store) return;
      const scale = this.chart.timeScale();
      const view = { plotWidth, plotHeight, barSpacing: scale.options().barSpacing,
        priceToY: price => this.series.priceToCoordinate(price),
        // Integer logical index only: fractional logicalToCoordinate() silently returns 0 in 4.1.
        barCenter: seconds => {
          const logical = this.logicalForTime(seconds);
          if (logical === null) return null;
          const anchor = Math.round(logical);
          return Math.abs(logical - anchor) > 0.01 ? null : scale.logicalToCoordinate(anchor);
        } };
      try { this.footprintDraw = window.WorkspaceFootprint.draw(context, store, view, getIndicatorSettings('footprint')); }
      catch (error) { this.footprintDraw = null; this.footprintStatus.textContent = `Footprint render error: ${error.message}`; }
    }
    syncLiquidity() {
      if (this.disposed || !this.liquidityStatus) return;
      const enabled = isIndicatorEnabled('liquidity'); this.liquidityStatus.hidden = !enabled;
      const config = state.panes[this.id], key = enabled ? JSON.stringify([config.exchange, config.symbol, config.interval]) : null;
      if (key !== this.liquidityKey) {
        this.liquidityKey = key; this.liquidityData = null; this.liquidityModel = null; this.liquidityState = {};
        this.liquidityTip.hidden = true;
        this.liquiditySession?.configure(null);
        if (enabled) this.liquiditySession?.configure({exchange: config.exchange, symbol: config.symbol});
        this.liquidityPrimitive?.requestUpdate?.();
      }
      const settingsKey = JSON.stringify(getIndicatorSettings('liquidity'));
      if (settingsKey !== this.liquiditySettingsKey) {
        const windowChanged = this.liquidityWindow !== getIndicatorSettings('liquidity').historyWindow;
        this.liquidityWindow = getIndicatorSettings('liquidity').historyWindow; this.liquiditySettingsKey = settingsKey;
        if (windowChanged) this.liquiditySession?.invalidate();
        this.liquidityPrimitive?.requestUpdate?.();
      }
      if (enabled) this.liquiditySession?.tick();
      this.renderLiquidityStatus();
    }
    liquidityRange() {
      const now = Date.now(), range = this.chart.timeScale().getVisibleLogicalRange();
      const duration = intervalSeconds(state.panes[this.id].interval, this.candles.at(-1)?.time || now/1000);
      const toTime = logical => {
        const index = Math.floor(logical), fraction = logical-index;
        if (index < 0) return (this.candles[0].time+logical*duration)*1000;
        if (index >= this.candles.length-1) return (this.candles.at(-1).time+(logical-this.candles.length+1)*duration)*1000;
        return (this.candles[index].time+fraction*(this.candles[index+1].time-this.candles[index].time))*1000;
      };
      if (range && this.candles.length && range.to < this.candles.length-2) return {start_ms: toTime(range.from), end_ms: toTime(range.to)};
      return {start_ms: now-(getIndicatorSettings('liquidity').historyWindow === '1h' ? 3600000 : 600000), end_ms: now};
    }
    queueLiquidityRange() {
      if (!isIndicatorEnabled('liquidity') || this.disposed) return;
      clearTimeout(this.liquidityRangeTimer);
      this.liquidityRangeTimer = setTimeout(() => {
        if (this.disposed || !isIndicatorEnabled('liquidity')) return;
        const range = this.liquidityRange();
        // Ignore a rolling live range's millisecond drift; pan changes are debounced.
        const key = `${Math.floor(range.start_ms/10000)}:${Math.floor(range.end_ms/10000)}`;
        if (key === this.liquidityRangeKey) return;
        this.liquidityRangeKey = key; this.liquiditySession?.invalidate(); this.liquiditySession?.tick();
      }, 350);
    }
    buildLiquidityModel(size) {
      if (!isIndicatorEnabled('liquidity') || !this.liquidityData || !this.candles.length) { this.liquidityModel = null; return null; }
      this.liquidityModel = window.WorkspaceLiquidity.geometry(this.liquidityData, getIndicatorSettings('liquidity'), {
        candles: this.candles, scale: this.chart.timeScale(), interval: intervalSeconds(state.panes[this.id].interval, this.candles.at(-1).time),
        priceToCoordinate: price => this.series.priceToCoordinate(price), width: size.width, height: size.height, now: Date.now(), pixelRatio: window.devicePixelRatio || 1
      });
      return this.liquidityModel;
    }
    renderLiquidityStatus() {
      if (!this.liquidityStatus || !isIndicatorEnabled('liquidity')) return;
      if (this.liquidityLayer === 'unavailable') { this.liquidityStatus.textContent = 'Liquidity unavailable: native behind-candle renderer could not load. No overlay fallback.'; return; }
      const data = this.liquidityData, latest = data?.latest, status = this.liquidityState;
      const date = ms => typeof ms === 'number' && Number.isFinite(ms) ? new Date(ms).toISOString().replace('T',' ').replace('.000Z',' UTC') : null;
      const coverage = date(data?.coverage_start), last = latest?.observed_at;
      const age = Number.isFinite(last) ? `${Math.max(0,Math.floor((Date.now()-last)/1000))}s ago` : 'waiting for first sample';
      const gaps = Array.isArray(data?.gaps) ? data.gaps.length : 0;
      const sampled = latest ? `${latest.bids?.length || 0} bid / ${latest.asks?.length || 0} ask rows` : 'venue-limited depth';
      const capacity = [...panes.values()].filter(p => !p.disposed && isVisible(p)).length;
      const mode = status.paused ? 'Paused · invisible' : status.error ? `Collection error: ${status.error}` : status.collecting ? 'Collecting' : 'Connecting collector';
      const warnings = (data?.warnings || []).filter(w => typeof w === 'string');
      this.liquidityStatus.textContent = `${mode} • ${coverage ? `history from ${coverage}` : 'No history yet'} · no history before collection · 10s snapshots, not tick-complete · last sample ${age} · gaps ${gaps}${gaps ? ' (transparent)' : ''} · depth limited: ${sampled} · native quantity (unit unknown unless venue metadata states otherwise) · ${capacity}/6 pane leases · subpixel samples: 1-device-pixel markers, not duration${this.liquidityModel ? ` · bucket ${formatPrice(this.liquidityModel.step)}${this.liquidityModel.stepRaised ? ' (raised to 1px minimum)' : ''} · book spans ${Math.round(this.liquidityModel.bookPx)}px of plot` : ''}${this.liquidityModel?.truncated ? ' · draw limit reached' : ''}${warnings.length ? ` · ${warnings.join(' · ')}` : ''}`;
      this.liquidityStatus.title = this.liquidityStatus.textContent;
    }
    liquidityTooltip(parameter) {
      if (!this.liquidityTip) return;
      const point = parameter.point;
      const hit = point && this.liquidityModel && isIndicatorEnabled('liquidity') ? window.WorkspaceLiquidity.hitTest(this.liquidityModel, point.x, point.y) : null;
      this.liquidityTip.hidden = !hit; if (!hit) return;
      this.liquidityTip.textContent = `${new Date(hit.time).toISOString()}${hit.endTime ? ` – ${new Date(hit.endTime).toISOString()}` : ' (latest snapshot)'} · ${hit.side === 'bids' ? 'Bid' : 'Ask'} · price bucket ${formatPrice(hit.price)} · ${formatPrice(hit.qty)} native qty (unit may be unknown)${hit.marker ? ' · 1px observation marker; not persistence' : ''}`;
      this.liquidityTip.style.left = `${Math.max(4,Math.min(point.x+12,this.host.clientWidth-285))}px`;
      this.liquidityTip.style.top = `${Math.max(35,Math.min(point.y+12,this.host.clientHeight-55))}px`;
    }
    updateHeader() {
      const config = state.panes[this.id];
      $('.pane-title', this.element).textContent = `${EXCHANGES[config.exchange].toUpperCase()} · ${config.symbol}`;
      $('summary', this.element).title = `Change ${config.symbol} · chart ${this.id + 1}`;
      this.symbolInput.value = config.symbol;
      populateIntervals($('.pane-interval', this.element), config.interval);
      this.element.dataset.symbol = config.symbol;
      this.element.dataset.exchange = config.exchange;
      this.element.dataset.interval = config.interval;
      this.syncLiquidity();
      this.syncFootprint();
    }
    resize() {
      if (this.disposed) return;
      const width = this.host.clientWidth;
      const height = this.host.clientHeight;
      if (!width || !height) return;
      this.chart.resize(width, height);
      const ratio = window.devicePixelRatio || 1;
      this.canvas.width = Math.round(width * ratio);
      this.canvas.height = Math.round(height * ratio);
      this.canvas.style.width = `${width}px`;
      this.canvas.style.height = `${height}px`;
      this.scheduleDraw();
    }
    animateInteraction() { this.interactionUntil = performance.now() + 850; this.scheduleDraw(); }
    scheduleDraw() {
      if (this.disposed || this.raf !== null) return;
      this.raf = requestAnimationFrame(() => {
        this.raf = null;
        if (this.disposed) return;
        this.drawOverlays();
        this.liquidityPrimitive?.requestUpdate?.();
        if (this.dragging || performance.now() < this.interactionUntil) this.scheduleDraw();
      });
    }
    // Fractional logical bar index for an arbitrary absolute time (seconds),
    // anchored so candle i's OWN time maps to logical index i exactly (same
    // interpolation Lightweight Charts itself uses internally). Works both
    // inside and outside the loaded candle range.
    logicalForTime(seconds) {
      const data = this.candles;
      if (!data.length) return null;
      let low = 0;
      let high = data.length;
      while (low < high) {
        const middle = (low + high) >>> 1;
        if (data[middle].time < seconds) low = middle + 1;
        else high = middle;
      }
      const step = intervalSeconds(state.panes[this.id].interval, low === 0 ? data[0].time : data.at(-1).time);
      if (low === 0) return (seconds - data[0].time) / step;
      if (low === data.length) return data.length - 1 + (seconds - data.at(-1).time) / step;
      return low - 1 + (seconds - data[low - 1].time) / (data[low].time - data[low - 1].time);
    }
    // Pixel X of a bar BOUNDARY (not a bar's center). Lightweight Charts'
    // logicalToCoordinate(i) returns the CENTER of candle i, and (confirmed
    // empirically) only accepts INTEGER logical indices — fractional ones
    // silently return 0, not an interpolated pixel. So: round to the nearest
    // integer logical (always exact or ~exact for our callers, which only
    // ever pass a candle's own open time or the close-time boundary of the
    // last candle), get that bar's real center pixel from the library, then
    // fold in the needed -0.5 bar (left edge) using the chart's own live
    // barSpacing (itself a legitimate per-pixel affine value, confirmed by
    // comparing consecutive integer logicalToCoordinate calls). A candle's
    // own left edge sits half a bar to the left of its center. This is the
    // fix for the FVG box-vs-candle misalignment: callers always pass either
    // a candle's own open time (-> left edge, used for the FVG start
    // boundary) or the close time of the last candle, i.e. the open-time
    // boundary of a hypothetical next candle (-> left edge of that slot ==
    // right edge of the actual last candle, used for the FVG end boundary).
    edgeCoordinate(seconds) {
      const logical = this.logicalForTime(seconds);
      if (logical === null) return null;
      const scale = this.chart.timeScale();
      const anchor = Math.round(logical);
      const centerPx = scale.logicalToCoordinate(anchor);
      if (centerPx === null || !Number.isFinite(centerPx)) return null;
      const barSpacing = scale.options().barSpacing;
      const fraction = logical - anchor; // ~0 in every real caller today
      return centerPx + (fraction - 0.5) * barSpacing;
    }
    // Effective FVG zones for current settings: default 'wick' policy reuses
    // the already-mitigated zones the backend computed (unchanged zone math,
    // per plan); 'close' policy is recomputed client-side (registry contract
    // requires compute() to work purely from already-fetched candles).
    effectiveFvgZones(settings) {
      if (settings.fillPolicy === 'close') return fvgZones(this.candles, settings);
      const minGapPct = Math.max(0, Number(settings.minGapPct) || 0);
      return this.rawZones.filter(zone => {
        const midPrice = (zone.top + zone.bottom) / 2;
        return !(midPrice > 0 && ((zone.top - zone.bottom) / midPrice) * 100 < minGapPct);
      });
    }
    drawOverlays() {
      const context = this.context;
      this.renderCountdown();
      if (!context) return;
      const ratio = window.devicePixelRatio || 1;
      const width = this.canvas.width / ratio;
      const height = this.canvas.height / ratio;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      if (!this.candles.length || !width || !height) return;
      const plotWidth = Math.max(0, width - this.chart.priceScale('right').width());
      const plotHeight = Math.max(0, height - this.chart.timeScale().height());
      context.save();
      context.beginPath(); context.rect(0, 0, plotWidth, plotHeight); context.clip();
      context.font = '9px SFMono-Regular, Consolas, monospace';
      if (state.fvg) this.drawZones(context, plotWidth, plotHeight);
      this.drawBands(context, plotWidth, plotHeight);
      this.drawGroupedOverlays(context, plotWidth, plotHeight);
      if (this.wyckoff) this.drawWyckoff(context, plotWidth);
      if (this.smcOverlay) this.drawSmc(context, plotWidth);
      if (state.panes[this.id].footprint) this.drawFootprint(context, plotWidth, plotHeight);
      this.orderflow?.draw(context, plotWidth, plotHeight);
      this.drawings?.draw(context, plotWidth, plotHeight);
      context.restore();
    }
    drawZones(context, plotWidth, plotHeight) {
      const settings = getIndicatorSettings('fvg');
      const lastCandle = this.candles.at(-1);
      const step = intervalSeconds(state.panes[this.id].interval, lastCandle.time);
      const rightBoundaryTime = lastCandle.time + step; // == close time of the last closed bar
      const rightEdge = this.edgeCoordinate(rightBoundaryTime);
      if (rightEdge === null) return;
      const opacity = settings.opacity;
      this.effectiveFvgZones(settings).forEach(zone => {
        const x1 = this.edgeCoordinate(zone.startTime);
        const x2 = rightEdge;
        const y1 = this.series.priceToCoordinate(Number(zone.top));
        const y2 = this.series.priceToCoordinate(Number(zone.bottom));
        if ([x1, x2, y1, y2].some(value => value === null || !Number.isFinite(value))) return;
        const left = Math.max(0, Math.min(x1, x2));
        const right = Math.min(plotWidth, Math.max(x1, x2));
        const top = Math.max(0, Math.min(y1, y2));
        const bottom = Math.min(plotHeight, Math.max(y1, y2));
        if (right - left < 1 || bottom - top < 1) return;
        const bull = zone.direction === 'bull';
        context.fillStyle = scaleAlpha(bull ? settings.bullColor : settings.bearColor, bull ? 0.13 : 0.14, opacity);
        context.strokeStyle = scaleAlpha(bull ? settings.bullColor : settings.bearColor, bull ? 0.42 : 0.45, opacity);
        context.fillRect(left, top, right - left, bottom - top);
        context.lineWidth = 0.75;
        context.strokeRect(left + 0.5, top + 0.5, Math.max(0, right - left - 1), Math.max(0, bottom - top - 1));
        if (right - left >= 42 && bottom - top >= 15) {
          context.fillStyle = scaleAlpha(bull ? settings.bullColor : settings.bearColor, bull ? 0.7 : 0.75, opacity);
          context.fillText('FVG', left + 5, top + 11);
        }
      });
    }
    // Generic 'band' kind renderer (see docs/INDICATOR_PLUGIN_PLAN.md #1):
    // fills the polygon between an upper and lower curve across their shared
    // time range, using the same edge-accurate coordinate conversion as
    // drawZones. No shipped indicator uses this yet; it exists so Turtle
    // Zone (a follow-up task) only has to add a registry entry.
    drawBands(context, plotWidth, plotHeight) {
      INDICATOR_DEFS.filter(def => def.kind === 'band' && isIndicatorEnabled(def.id)).forEach(def => {
        const settings = getIndicatorSettings(def.id);
        let output;
        try { output = def.compute(this.candles, { ...settings, intervalSeconds: intervalSeconds(state.panes[this.id].interval, this.candles.at(-1)?.time || 0) }) || {}; }
        catch (error) { if (this.status) this.status.textContent = `${def.label}: ${error.message}`; return; }
        const segments = Array.isArray(output.segments) ? output.segments : [output];
        const coordinate = time => output.timeCoordinates === 'edges' ? this.edgeCoordinate(time) : this.edgeCoordinateCenter(time);
        const convert = points => Array.isArray(points) ? points.map(point => ({ x: coordinate(point.time), y: this.series.priceToCoordinate(point.value) }))
          .filter(point => point.x !== null && point.y !== null && Number.isFinite(point.x) && Number.isFinite(point.y)) : [];
        segments.forEach(segment => {
          const upper = convert(segment.upper), lower = convert(segment.lower);
          if (upper.length < 2 || lower.length < 2) return;
          context.save();
          context.beginPath(); context.rect(0, 0, plotWidth, plotHeight); context.clip();
          context.beginPath(); context.moveTo(upper[0].x, upper[0].y);
          upper.slice(1).forEach(p => context.lineTo(p.x, p.y));
          [...lower].reverse().forEach(p => context.lineTo(p.x, p.y));
          context.closePath();
          context.fillStyle = settings.opacity === undefined ? settings.color || 'rgba(130,170,255,0.18)' : scaleAlpha(settings.color, settings.opacity, 1);
          context.fill();
          context.lineWidth = 1; context.strokeStyle = settings.color || '#82aaff';
          const inner = settings.innerBoundary || def.innerBoundary || 'lower';
          const stroke = points => { context.beginPath(); context.moveTo(points[0].x, points[0].y); points.slice(1).forEach(p => context.lineTo(p.x, p.y)); context.stroke(); };
          stroke(inner === 'upper' ? upper : lower);
          if (settings.showOuter) stroke(inner === 'upper' ? lower : upper);
          context.restore();
        });
      });
    }
    // Bar-center coordinate (unlike edgeCoordinate, line/band series are
    // sampled at the candle's own time, not its boundary).
    edgeCoordinateCenter(seconds) {
      const logical = this.logicalForTime(seconds);
      if (logical === null) return null;
      return this.chart.timeScale().logicalToCoordinate(logical);
    }
    // Grouped indicators (static/indicators/*): one toggle each; models are
    // computed here once per data/settings change and painted per frame.
    applyGroupedIndicators() {
      const step = intervalSeconds(state.panes[this.id].interval, this.candles.at(-1)?.time || 0);
      const options = (visible, color, width) => ({ visible: Boolean(visible), color, lineWidth: width || 1 });
      this.groupWarnings = []; this.groupModels = {};
      const ribbonOn = isIndicatorEnabled('ema-ribbon'), ribbonDef = INDICATOR_BY_ID.get('ema-ribbon');
      (ribbonDef ? ribbonDef.compute(this.candles, getIndicatorSettings('ema-ribbon')).lines : []).forEach((line, index) => {
        const key = RIBBON_KEYS[index];
        this.emaValues[key] = new Map(line.data.map(point => [point.time, point.value]));
        this.emas[key].applyOptions(options(ribbonOn && line.visible, line.color, line.width));
        this.emas[key].setData(ribbonOn && line.visible ? line.data : []);
      });
      const ema200 = getIndicatorSettings('ema200'), ema200Data = INDICATOR_BY_ID.get('ema200').compute(this.candles, ema200);
      this.emaValues['200'] = new Map(ema200Data.map(point => [point.time, point.value]));
      this.emas['200'].applyOptions(options(isIndicatorEnabled('ema200'), ema200.color, 1));
      this.emas['200'].setData(ema200Data);
      const channelOn = isIndicatorEnabled('ma-channel'), channelDef = INDICATOR_BY_ID.get('ma-channel');
      const channel = channelDef && channelOn ? channelDef.compute(this.candles, getIndicatorSettings('ma-channel')) : null;
      ['high', 'low'].forEach(side => {
        const line = channel?.[side === 'high' ? 'upper' : 'lower'];
        this.ma20[side].applyOptions(options(line?.visible, line?.color, line?.width));
        this.ma20[side].setData(line?.visible ? line.data : []);
      });
      if (channel?.fill) this.groupModels.channel = channel;
      if (isIndicatorEnabled('turtle-zone') && INDICATOR_BY_ID.has('turtle-zone')) {
        try { this.groupModels.turtle = INDICATOR_BY_ID.get('turtle-zone').compute(this.candles, getIndicatorSettings('turtle-zone')); }
        catch (_) { /* reported once by the definition's warnings() */ }
      }
      if (SESSIONS && isIndicatorEnabled('sessions') && this.candles.length) {
        const model = SESSIONS.compute(this.candles, getIndicatorSettings('sessions'), step);
        this.groupModels.sessions = model;
        this.groupWarnings.push(...model.warnings.map(text => `Market Sessions: ${text}`));
      }
      if (OPENS && isIndicatorEnabled('opens') && this.candles.length) {
        const config = state.panes[this.id];
        if (this.htf && this.htfMarket === `${config.exchange}:${config.symbol}`) {
          const model = OPENS.computeModel({ ...this.htf, chart: this.candles }, getIndicatorSettings('opens'), { candles: this.candles, step });
          this.groupModels.opens = model;
          this.groupWarnings.push(...[...this.htf.errors, ...model.warnings].map(text => `Key levels: ${text}`));
        } else this.groupWarnings.push('Key levels: loading higher-timeframe candles…');
        this.refreshHigherTimeframes(step);
      }
    }
    refreshHigherTimeframes(step) {
      const config = state.panes[this.id], tfs = OPENS.neededTimeframes(getIndicatorSettings('opens'), step);
      const key = JSON.stringify([config.exchange, config.symbol, tfs]);
      if (this.htfPending === key || (this.htfKey === key && Date.now() - this.htfAt < 60000)) return;
      this.htfPending = key;
      this.htfLoader.ensure(config.exchange, config.symbol, tfs).then(data => {
        if (this.htfPending === key) this.htfPending = null;
        const now = state.panes[this.id];
        if (this.disposed || JSON.stringify([now.exchange, now.symbol, OPENS.neededTimeframes(getIndicatorSettings('opens'), step)]) !== key) return;
        this.htf = data; this.htfKey = key; this.htfAt = Date.now(); this.htfMarket = `${config.exchange}:${config.symbol}`;
        this.applyIndicators();
      });
    }
    drawGroupedOverlays(context, plotWidth, plotHeight) {
      if (!this.candles.length || !GROUPS) return;
      const scale = this.chart.timeScale();
      const view = { x: time => this.edgeCoordinate(time), y: price => this.series.priceToCoordinate(price), barSpacing: scale.options().barSpacing,
        lastX: scale.logicalToCoordinate(this.candles.length - 1), plotWidth, plotHeight, format: price => this.series.priceFormatter().format(price) };
      const centers = points => points.map(p => ({ time: p.time, x: this.edgeCoordinateCenter(p.time), y: this.series.priceToCoordinate(p.value) }))
        .filter(p => Number.isFinite(p.x) && Number.isFinite(p.y));
      const channel = this.groupModels.channel;
      if (channel) GROUPS.fillBetween(context, centers(channel.upper.data), centers(channel.lower.data), GROUPS.rgba(channel.fillColor, channel.opacity));
      const turtle = this.groupModels.turtle;
      if (turtle) turtle.zones.filter(zone => zone.visible).forEach(zone => {
        const inner = centers(zone.inner), outer = centers(zone.outer);
        if (turtle.fill) GROUPS.fillBetween(context, outer, inner, GROUPS.rgba(zone.color, turtle.opacity));
        GROUPS.strokePolyline(context, inner, zone.color, turtle.width);
        if (turtle.showOuter) GROUPS.strokePolyline(context, outer, zone.color, turtle.width);
      });
      if (this.groupModels.sessions) SESSIONS.draw(context, view, this.groupModels.sessions, getIndicatorSettings('sessions'));
      if (this.groupModels.opens) OPENS.draw(context, view, this.groupModels.opens, getIndicatorSettings('opens'));
    }
    showMessage(text) {
      this.message.hidden = !text;
      $('span', this.message).textContent = text;
    }
    updateLegend(candle) {
      $('.ohlc', this.element).textContent = candle ? `O ${formatPrice(candle.open)}  H ${formatPrice(candle.high)}  L ${formatPrice(candle.low)}  C ${formatPrice(candle.close)}` : 'No closed candle data';
      const ribbon = getIndicatorSettings('ema-ribbon');
      [...RIBBON_KEYS, '200'].forEach((key, index) => {
        const element = $(`[data-ema="${key}"]`, this.element);
        const isRibbon = key !== '200', n = index + 1;
        element.hidden = isRibbon ? !(isIndicatorEnabled('ema-ribbon') && ribbon[`show${n}`]) : !isIndicatorEnabled('ema200');
        const effectivePeriod = isRibbon ? ribbon[`period${n}`] : getIndicatorSettings('ema200').period;
        if (isRibbon) element.style.color = ribbon[`color${n}`];
        const point = legendEmaValue(this.emaValues[key], candle, this.candles.at(-1), effectivePeriod);
        element.textContent = `EMA ${effectivePeriod} ${point === null ? '· warming up' : `${formatPrice(point.value)}${point.live ? ' (live)' : ''}`}`;
        element.title = point?.live ? 'Provisional EMA including the forming bar; the plotted line uses closed bars only' : '';
      });
    }
    applyIndicators() {
      this.syncLiquidity();
      this.syncFootprint();
      this.applyGroupedIndicators();
      INDICATOR_DEFS.filter(def => def.kind === 'line' && def.id !== 'ema200').forEach(def => {
        const settings = getIndicatorSettings(def.id);
        let series = this.pluginLines.get(def.id);
        if (!series) {
          series = this.chart.addLineSeries({ lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
          this.pluginLines.set(def.id, series);
        }
        series.applyOptions({ visible: isIndicatorEnabled(def.id), color: settings.color || '#9ab4e6', lineWidth: settings.width || 1, lineType: def.stepLine ? LightweightCharts.LineType.WithSteps : LightweightCharts.LineType.Simple });
        try { series.setData(isIndicatorEnabled(def.id) ? def.compute(this.candles, { ...settings, intervalSeconds: intervalSeconds(state.panes[this.id].interval, this.candles.at(-1)?.time || 0) }) : []); }
        catch (error) { series.setData([]); this.status.textContent = `${def.label}: ${error.message}`; }
      });
      syncIndicatorLabels();
      const warningEl = $('.pane-plugin-warnings', this.element);
      const warnings = [...(this.groupWarnings || []), ...INDICATOR_DEFS.filter(def => isIndicatorEnabled(def.id) && typeof def.warnings === 'function').flatMap(def => {
        try { return def.warnings(this.candles, { ...getIndicatorSettings(def.id), intervalSeconds: intervalSeconds(state.panes[this.id].interval, this.candles.at(-1)?.time || 0) }).map(message => `${def.label}: ${message}`); }
        catch (error) { return [`${def.label}: ${error.message}`]; }
      })];
      warningEl.textContent = warnings.join(' · '); warningEl.hidden = !warnings.length;
      this.updateLegend(this.candles.at(-1));
      this.scheduleDraw();
    }
    clearData() {
      this.candles = [];
      this.rawZones = [];
      this.emaValues = {};
      this.series.setData([]);
      this.volume.setData([]);
      Object.values(this.emas).forEach(series => series.setData([]));
      this.ma20.high.setData([]);
      this.ma20.low.setData([]);
      this.pluginLines.forEach(series => series.setData([]));
      this.count.textContent = '';
      $('.pane-source', this.element).textContent = '—';
      this.updateLegend();
      this.scheduleDraw();
    }
    resetContext() {
      this.contextGeneration += 1; this.contextController?.abort(); this.contextController = null;
      this.contextLastFetch = 0;
      const body = $('.pane-context-body', this.element); if (body) body.textContent = '—';
    }
    async refreshContext() {
      if (this.disposed || !$('.pane-context', this.element).open || document.visibilityState !== 'visible' || !isVisible(this) || this.contextController || Date.now() - (this.contextLastFetch || 0) < 60000) return;
      const config = { ...state.panes[this.id] };
      const generation = ++this.contextGeneration;
      const controller = new AbortController(); this.contextController = controller;
      this.contextLastFetch = Date.now();
      const body = $('.pane-context-body', this.element);
      const timeout = setTimeout(() => controller.abort(), 20000);
      try {
        const query = new URLSearchParams({ exchange: config.exchange, symbol: config.symbol });
        const response = await fetch(`/api/chart/context?${query}`, { signal: controller.signal, cache: 'no-store', headers: { Accept: 'application/json' } });
        const data = await response.json();
        if (this.disposed || controller.signal.aborted || generation !== this.contextGeneration || config.exchange !== state.panes[this.id].exchange || config.symbol !== state.panes[this.id].symbol) return;
        if (!response.ok || data.exchange !== config.exchange || data.symbol !== config.symbol) throw new Error('Context unavailable');
        const numeric = v => typeof v === 'number' && Number.isFinite(v);
        const funding = numeric(data.funding_rate) ? `${formatPrice(data.funding_rate * 100)}%` : '—';
        const hours = numeric(data.funding_interval_hours) ? ` / ${data.funding_interval_hours}h` : '';
        const oi = numeric(data.open_interest) ? `${formatPrice(data.open_interest)}${typeof data.open_interest_unit === 'string' ? ` ${data.open_interest_unit}` : ' · unit unknown'}` : '—';
        body.replaceChildren();
        const values = document.createElement('div');
        values.textContent = `Funding ${funding}${hours} · OI ${oi} · Mark ${numeric(data.mark_price) ? formatPrice(data.mark_price) : '—'} · Index ${numeric(data.index_price) ? formatPrice(data.index_price) : '—'}`;
        body.append(values);
        const badges = document.createElement('div'); badges.className = 'context-bias';
        [['60','1h'], ['240','4h'], ['D','1D']].forEach(([iv, label]) => {
          const bias = ['bullish','bearish','mixed','insufficient','unavailable'].includes(data.ema_bias?.[iv]) ? data.ema_bias[iv] : 'unavailable';
          const badge = document.createElement('span'); badge.dataset.bias = bias; badge.textContent = `${label} ${bias}`; badges.append(badge);
        });
        body.append(badges);
        const source = document.createElement('small');
        source.textContent = `${EXCHANGES[config.exchange]} · ${config.symbol} · snapshot ${numeric(data.as_of) ? timeFormat.format(new Date(data.as_of)) + ' UTC' : 'time unavailable'}${data.cached ? ' · cached' : ''}${Array.isArray(data.warnings) && data.warnings.length ? ` · ${data.warnings.join(' · ')}` : ''}`;
        body.append(source);
      } catch (_) {
        if (!this.disposed && generation === this.contextGeneration) body.textContent = `${EXCHANGES[config.exchange]} · context unavailable · —`;
      } finally { clearTimeout(timeout); if (generation === this.contextGeneration) this.contextController = null; }
    }
    // A forming candle is presentation-only. Never feed it to closed-candle
    // indicators, FVG, alerts or the backend decision pipeline.
    startTicker(exchange, symbol) {
      const config = { ...state.panes[this.id] };
      const key = JSON.stringify([exchange, symbol, config.interval]);
      if (key === this.tickerKey) return;
      this.stopTicker();
      this.tickerKey = key;
      const generation = this.tickerGeneration;
      this.streamStatus = 'connecting';
      this.streamOff = streamHub?.subscribe({ venue: exchange, symbol, interval: config.interval }, event => {
        if (this.disposed || this.tickerGeneration !== generation || this.tickerKey !== key) return;
        if (event.type === 'status') { this.streamStatus = event.status; this.renderStreamStatus(); return; }
        if (event.venue !== exchange || event.symbol !== symbol || event.interval !== config.interval) return;
        this.lastStreamAt = event.receivedAt;
        if (event.type === 'trade') {
          if (event.timestamp < (this.lastTradeStamp || 0)) return;
          this.lastTradeStamp = event.timestamp;
          this.lastTrade = event;
          this.setLivePrice(event.price, 'WS trade');
          // Open and volume remain from real OHLCV. Trades refine only known
          // same-bar C/H/L; no invented open and no double-counted volume.
          if (this.forming && event.timestamp >= this.forming.time * 1000 && event.timestamp < this.forming.closeTime) {
            this.forming = { ...this.forming, close: event.price, high: Math.max(this.forming.high, event.price), low: Math.min(this.forming.low, event.price) };
            this.renderForming();
          }
          return;
        }
        this.lastCandleAt = event.receivedAt;
        let candle = event.candle;
        if (this.forming?.time === candle.time && this.lastTrade?.timestamp > event.timestamp) candle = { ...candle, close: this.forming.close, high: Math.max(candle.high, this.forming.high), low: Math.min(candle.low, this.forming.low) };
        this.acceptForming(candle, 'WS', event.closed);
      });
      this.fetchTicker(); // genuine forming OHLCV bootstrap, not cached ticker
      this.tickerTimer = setInterval(() => this.renderStreamStatus(), 1000);
    }
    stopTicker(removeLine = true) {
      clearInterval(this.tickerTimer); this.tickerTimer = null;
      this.streamOff?.(); this.streamOff = null;
      this.tickerController?.abort(); this.tickerController = null;
      this.tickerGeneration += 1;
      this.resetResync();
      this.forming = null; this.lastStreamAt = 0; this.lastCandleAt = 0; this.lastTradeStamp = 0; this.lastTrade = null;
      this.streamStatus = 'disconnected';
      this.tickerKey = null;
      if (removeLine) this.removeLivePrice();
    }
    renderStreamStatus() {
      if (this.disposed) return;
      const age = this.lastStreamAt ? Math.max(0, Math.floor((Date.now() - this.lastStreamAt) / 1000)) : null;
      const mode = this.streamStatus === 'live' && age !== null && age < 30 ? 'WS LIVE' : this.streamStatus === 'unsupported' ? 'WS UNSUPPORTED' : this.streamStatus === 'live' ? 'WS STALE' : `WS ${String(this.streamStatus || 'disconnected').toUpperCase()}`;
      const label = $('.pane-closed', this.element);
      label.textContent = `${mode}${age === null ? '' : ` · ${age}s`}`;
      label.title = 'Current candle is display-only. Indicators and local FVG alerts use closed candles. No simulated ticks.';
      label.dataset.stream = mode === 'WS LIVE' ? 'live' : 'offline';
      if (this.livePriceEl.dataset.state === 'ready' && mode !== 'WS LIVE') this.livePriceEl.textContent = `${mode} · last ${formatPrice(this.lastDisplayPrice)}`;
    }
    setLivePrice(price, source = 'WS') {
      this.lastDisplayPrice = price;
      if (this.priceLine) this.priceLine.applyOptions({ price });
      else this.priceLine = this.series.createPriceLine({ price, color: '#4fc3f7', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true, title: 'Current' });
      this.livePriceEl.dataset.state = 'ready';
      this.livePriceEl.textContent = `${source} ${formatPrice(price)}`;
    }
    // Funding chip (static/funding.js): last settled → predicted · countdown, via GET /api/funding.
    async refreshFunding() {
      const config = state.panes[this.id], key = `${config.exchange}:${config.symbol}`;
      if (this.fundingKey !== key) { this.fundingKey = key; this.fundingData = null; this.renderFundingChip(); }
      if (this.fundingBusy) return;
      this.fundingBusy = true;
      try {
        const response = await fetch(`/api/funding?${new URLSearchParams({ exchange: config.exchange, symbol: config.symbol })}`, { headers: { Accept: 'application/json' } });
        const data = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
        if (this.fundingKey === key && !this.disposed) this.fundingData = response.ok ? data : { error: data.error || `HTTP ${response.status}`, detail: data.detail };
      } catch (error) {
        if (this.fundingKey === key) this.fundingData = { error: 'request failed', detail: String(error && error.message || error) };
      } finally {
        this.fundingBusy = false; this.renderFundingChip();
        // The pane switched market while this request was in flight (the new market's call
        // was skipped as busy): fetch it now instead of leaving the chip blank for up to 30 s.
        if (!this.disposed && this.fundingKey !== key) this.refreshFunding();
      }
    }
    renderFundingChip(now = Date.now()) {
      const el = $('.pane-funding', this.element), api = window.WorkspaceFunding;
      if (!el || !api) return;
      const model = api.chipModel(this.fundingData, now);
      el.hidden = !model && !this.fundingData?.error;
      el.textContent = model ? model.text : this.fundingData?.error ? 'F n/a' : '';
      el.title = model ? model.title : this.fundingData?.detail || this.fundingData?.error || '';
      el.dataset.sign = model ? model.sign : 'none';
    }
    // Wyckoff overlay (static/indicators/wyckoff.js), driven by the analysis dashboard toggle.
    setWyckoff(model) { this.wyckoff = model || null; this.scheduleDraw(); }
    drawWyckoff(context, plotWidth) {
      const c = state.panes[this.id], api = window.WorkspaceWyckoff;
      if (!api || !c || this.wyckoff.key !== `${c.exchange}:${c.symbol}:${c.interval}`) return;
      api.draw(context, { x: t => this.edgeCoordinate(t), xc: t => this.edgeCoordinateCenter(t), y: p => this.series.priceToCoordinate(p), plotWidth }, this.wyckoff);
    }
    // SMC / HTF overlay (static/indicators/smc.js), driven by the dashboard's Multi-timeframe toggles.
    setSmc(model) { this.smcOverlay = model || null; this.scheduleDraw(); }
    drawSmc(context, plotWidth) {
      const c = state.panes[this.id], api = window.WorkspaceSmc;
      if (!api || !c || this.smcOverlay.key !== `${c.exchange}:${c.symbol}:${c.interval}`) return;
      api.draw(context, { x: t => this.edgeCoordinate(t), xc: t => this.edgeCoordinateCenter(t), y: p => this.series.priceToCoordinate(p), plotWidth, first: this.candles[0]?.time }, this.smcOverlay);
    }
    // Hermes analyst levels (static/analyst-panel.js): temporary dashed price lines, never stored.
    setAnalystLines(rows) {
      this.clearAnalystLines(false);
      this.analystLines = rows.map(row => this.series.createPriceLine({ price: row.price, color: row.color, lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.LargeDashed, axisLabelVisible: true, title: `H·${row.label}` }));
    }
    clearAnalystLines(resetCenter = true) {
      (this.analystLines || []).forEach(line => { try { this.series.removePriceLine(line); } catch (_) { /* disposed */ } });
      this.analystLines = [];
      if (resetCenter) this.centerOnPrice(null);
    }
    // Centre the right price scale on `price` keeping the current span; null restores normal autoscale.
    centerOnPrice(price) {
      if (!Number.isFinite(price)) {
        if (this.analystCenter) { this.analystCenter = null; this.series.applyOptions({ autoscaleInfoProvider: original => original() }); }
        return;
      }
      const height = Math.max(0, this.host.clientHeight - this.chart.timeScale().height());
      const top = this.series.coordinateToPrice(0), bottom = this.series.coordinateToPrice(height);
      const span = Number.isFinite(top) && Number.isFinite(bottom) && top !== bottom ? Math.abs(top - bottom) : Math.abs(price) * 0.02;
      this.analystCenter = { minValue: price - span / 2, maxValue: price + span / 2 };
      this.series.applyOptions({ autoscaleInfoProvider: original => this.analystCenter ? { priceRange: this.analystCenter } : original() });
      this.chart.priceScale('right').applyOptions({ autoScale: true });
    }
    removeLivePrice() {
      if (this.priceLine) { this.series.removePriceLine(this.priceLine); this.priceLine = null; }
      if (this.livePriceEl) { this.livePriceEl.dataset.state = 'hidden'; this.livePriceEl.textContent = ''; }
    }
    requestResync(bar) {
      if (this.disposed) return;
      this.lastResyncBar = Math.max(this.lastResyncBar || 0, bar);
      const last = this.candles.at(-1);
      if (last && last.time + intervalSeconds(state.panes[this.id].interval, last.time) >= this.lastResyncBar) {
        this.resetResync(); return;
      }
      const attempts = this.resyncAttempts || 0;
      this.status.textContent = 'STALE · Closed history gap · retrying closed candles…';
      this.status.title = this.status.textContent;
      if (this.resyncTimer || this.resyncInFlight) return;
      // One budget per unresolved gap (not per WS update). Even a silent
      // socket gets retries, but a broken/stale provider cannot poll forever.
      if (attempts >= 6) {
        this.status.textContent = 'STALE · Closed history gap remains after 6 retries. Use Reload to retry.';
        this.status.title = this.status.textContent;
        return;
      }
      const generation = this.tickerGeneration;
      this.resyncTimer = setTimeout(async () => {
        this.resyncTimer = null;
        if (this.disposed || generation !== this.tickerGeneration) return;
        // Let an already active history request finish; never abort it just
        // because multiple forming updates arrived during its flight.
        if (this.request) { this.requestResync(this.lastResyncBar); return; }
        const flight = {};
        this.resyncInFlight = flight;
        this.resyncAttempts = attempts + 1;
        try { await this.load(false); }
        catch (_) { /* load normally reports errors; still retry a rejection. */ }
        finally {
          if (!this.disposed && generation === this.tickerGeneration && this.resyncInFlight === flight) {
            this.resyncInFlight = null;
            this.requestResync(this.lastResyncBar);
          }
        }
      }, Math.min(30000, 700 * 2 ** attempts));
    }
    resetResync() {
      clearTimeout(this.resyncTimer); this.resyncTimer = null;
      this.lastResyncBar = null; this.resyncAttempts = 0; this.resyncInFlight = null;
    }
    acceptForming(candle, source, closed = false) {
      if (!candle || !this.candles.length) return;
      const last = this.candles.at(-1);
      if (candle.time <= last.time || (this.forming && candle.time < this.forming.time)) return;
      if (this.forming && candle.time > this.forming.time) this.requestResync(candle.time);
      const expected = last.time + intervalSeconds(state.panes[this.id].interval, last.time);
      if (candle.time > expected) {
        // Retain the real new bar, but don't connect it across missing history.
        this.forming = candle; this.formingSource = source;
        this.requestResync(candle.time); return;
      }
      this.forming = candle; this.formingSource = source;
      this.renderForming();
      if (closed) this.requestResync(candle.time + intervalSeconds(state.panes[this.id].interval, candle.time));
    }
    renderForming() {
      if (!this.forming || !this.candles.length) return;
      const last = this.candles.at(-1);
      if (this.forming.time <= last.time || this.forming.time > last.time + intervalSeconds(state.panes[this.id].interval, last.time)) return;
      const { time, open, high, low, close, volume } = this.forming;
      this.series.update({ time, open, high, low, close });
      this.volume.update({ time, value: volume, color: close >= open ? '#00c08755' : '#ef667055' });
      this.setLivePrice(close, this.formingSource || 'WS');
      this.updateLegend(this.forming);
      this.scheduleDraw();
    }
    async fetchTicker() {
      if (this.disposed) return;
      this.tickerController?.abort();
      const generation = this.tickerGeneration;
      const controller = new AbortController(); this.tickerController = controller;
      const config = { ...state.panes[this.id] };
      const key = this.tickerKey;
      const timeout = setTimeout(() => controller.abort(), 15000);
      try {
        const query = new URLSearchParams({ exchange: config.exchange, symbol: config.symbol, interval: config.interval });
        const response = await fetch(`/api/chart/live?${query}`, { signal: controller.signal, headers: { Accept: 'application/json' }, cache: 'no-store' });
        const payload = await response.json().catch(() => null);
        if (this.disposed || generation !== this.tickerGeneration || key !== this.tickerKey || controller.signal.aborted || this.lastCandleAt) return;
        if (!response.ok || payload?.exchange !== config.exchange || payload.symbol !== config.symbol || payload.interval !== config.interval) return;
        const c = payload.candle;
        if (!c || c.is_forming !== true || c.source !== config.exchange || c.symbol !== config.symbol || c.interval !== config.interval || !Number.isFinite(c.close_ts) || c.close_ts <= c.ts) return;
        const candle = parseCandles([c])[0]; if (!candle) return; candle.closeTime = c.close_ts;
        this.acceptForming(candle, 'REST bootstrap');
      } catch (_) { /* No fake candles on network failure. WS can still recover. */ }
      finally { clearTimeout(timeout); }
    }
    // Seconds intervals: candles come only from real trades (static/seconds-candles.js).
    // /api/chart is never asked for them; gaps stay gaps; partial bars are drawn grey.
    loadSeconds(resetView, restart) {
      const api = window.WorkspaceSeconds, config = { ...state.panes[this.id] };
      const key = JSON.stringify([config.exchange, config.symbol, config.interval]);
      if (this.seconds && this.secondsKey === key && !resetView && !restart) { this.queueSecondsRender(); return; }
      this.generation += 1; this.request?.abort(); this.request = null; clearTimeout(this.requestTimer);
      this.stopSeconds(); this.stopTicker(); this.clearData();
      if (resetView) this.resetContext();
      $('.pane-source', this.element).textContent = `${config.exchange.toUpperCase()} · TRADES`;
      $('.pane-source', this.element).title = 'Seconds candles aggregated in the browser from real venue trades';
      if (!api) { this.element.dataset.state = 'error'; this.status.textContent = 'Seconds candles module did not load.'; this.showMessage(this.status.textContent); return; }
      this.chart.applyOptions({ timeScale: { secondsVisible: true } });
      this.element.dataset.state = 'loading';
      this.status.textContent = config.exchange === 'binance' ? 'Loading recent Binance aggTrades…' : 'Waiting for live trades…';
      this.showMessage(this.status.textContent);
      this.secondsKey = key; this.secondsFit = true; this.secondsPrecision = false; this.secondsPartial = undefined; this.secondsFormingTime = undefined;
      this.seconds = api.createController({ exchange: config.exchange, symbol: config.symbol, interval: config.interval, hub: streamHub,
        fetch: (url, options) => fetch(url, options), onChange: () => this.queueSecondsRender() }).start();
    }
    stopSeconds() {
      if (!this.seconds) return;
      this.seconds.dispose(); this.seconds = null; this.secondsKey = null;
      if (this.secondsRaf) { cancelAnimationFrame(this.secondsRaf); this.secondsRaf = null; }
      this.chart.applyOptions({ timeScale: { secondsVisible: false } });
    }
    queueSecondsRender() {
      if (this.disposed || this.secondsRaf) return;
      this.secondsRaf = requestAnimationFrame(() => { this.secondsRaf = null; this.renderSeconds(); });
    }
    renderSeconds() {
      if (this.disposed || !this.seconds) return;
      const { closed, forming } = this.seconds.view();
      const grey = '#5d6b80';
      const bar = c => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close, ...(c.partial ? { color: grey, wickColor: grey, borderColor: grey } : {}) });
      const vol = c => ({ time: c.time, value: c.volume, color: c.partial ? '#5d6b8055' : c.close >= c.open ? '#00c08744' : '#ef667044' });
      const partial = closed.filter(c => c.partial).length + (forming?.partial ? 1 : 0);
      const lastClosed = closed.at(-1)?.time ?? null;
      const structural = closed.length !== this.candles.length || lastClosed !== (this.candles.at(-1)?.time ?? null)
        || partial !== this.secondsPartial || (forming?.time ?? null) !== this.secondsFormingTime;
      if (structural) {
        this.secondsPartial = partial; this.secondsFormingTime = forming?.time ?? null;
        this.candles = closed.map(({ time, open, high, low, close, volume, partial: p }) => ({ time, open, high, low, close, volume, partial: p }));
        const rows = forming && forming.time > (lastClosed ?? -1) ? [...closed, forming] : closed;
        if (!this.secondsPrecision && rows.length) {
          const minPrice = Math.min(...rows.map(c => c.low));
          const precision = Math.min(10, Math.max(2, 3 - Math.floor(Math.log10(minPrice))));
          this.series.applyOptions({ priceFormat: { type: 'price', precision, minMove: 10 ** -precision } });
          this.secondsPrecision = true;
        }
        this.series.setData(rows.map(bar)); this.volume.setData(rows.map(vol));
        if (this.secondsFit && rows.length) { this.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, rows.length - 150), to: rows.length + 5 }); this.secondsFit = false; }
        this.applyIndicators();
      } else if (forming) { this.series.update(bar(forming)); this.volume.update(vol(forming)); }
      const last = forming || closed.at(-1);
      if (last) { this.setLivePrice(last.close, forming ? 'WS trade' : 'Last trade'); this.updateLegend(closed.at(-1) || last); } // legend: last closed bar (EMAs exist only for closed bars)
      const st = this.seconds.state;
      this.element.dataset.state = last ? 'ready' : st.state === 'unsupported' ? 'error' : 'loading';
      this.count.textContent = `${closed.length} closed bars${partial ? ` · ${partial} partial` : ''}`;
      this.status.textContent = this.seconds.statusText(); this.status.title = this.status.textContent;
      this.showMessage(last ? '' : st.state === 'unsupported' ? this.status.textContent : 'Waiting for the first live trade… no candles are invented.');
      const label = $('.pane-closed', this.element);
      label.textContent = `TRADES ${String(st.stream || '').toUpperCase()}`;
      label.dataset.stream = st.stream === 'live' ? 'live' : 'offline';
      label.title = 'Seconds candles from real trades only. Grey = partial coverage. Seconds without trades have no candle.';
      this.scheduleDraw();
    }
    async load(resetView = false, restart = false) {
      if (resetView) this.centerOnPrice(null);
      if (this.fundingKey !== `${state.panes[this.id].exchange}:${state.panes[this.id].symbol}`) this.refreshFunding();
      if (this.instrumentKey !== `${state.panes[this.id].exchange}:${state.panes[this.id].symbol}`) this.refreshInstrument();
      if (this.disposed) return;
      this.updateHeader();
      if (isSecondsInterval(state.panes[this.id].interval)) return this.loadSeconds(resetView, restart);
      if (this.seconds) { this.stopSeconds(); this.clearData(); }
      this.request?.abort();
      clearTimeout(this.requestTimer);
      const generation = ++this.generation;
      const request = new AbortController();
      this.request = request;
      let timedOut = false;
      this.requestTimer = setTimeout(() => { timedOut = true; request.abort(); }, 30000);
      if (resetView) this.clearData();
      this.element.dataset.state = 'loading';
      this.element.setAttribute('aria-busy', 'true');
      this.status.textContent = this.candles.length ? 'Refreshing closed candles…' : 'Loading closed candles…';
      this.showMessage(this.candles.length ? '' : 'Loading closed candles…');
      const config = { ...state.panes[this.id] };
      if (this.tickerKey && this.tickerKey !== JSON.stringify([config.exchange, config.symbol, config.interval])) this.stopTicker();
      if (resetView) this.resetContext();
      try {
        const query = new URLSearchParams({ exchange: config.exchange, symbol: config.symbol, interval: config.interval, limit: '300' });
        const response = await fetch(`/api/chart?${query}`, { signal: request.signal, headers: { Accept: 'application/json' }, cache: 'no-store' });
        const payload = await response.json();
        if (this.disposed || request.signal.aborted || generation !== this.generation || config.exchange !== state.panes[this.id].exchange || config.symbol !== state.panes[this.id].symbol || config.interval !== state.panes[this.id].interval) return;
        if (!response.ok) throw new Error(errorText(payload, `Chart request failed (HTTP ${response.status}).`));
        if (payload.exchange !== config.exchange || payload.source !== config.exchange) throw new Error('Provider returned a different exchange; chart not shown.');
        if (payload.closed_only !== true) throw new Error('Provider did not confirm closed-only candles.');
        const candles = parseCandles(payload.candles);
        const previous = this.candles;
        this.candles = candles;
        observeLocalFvg(config, previous, candles);
        this.rawZones = (candles.length && Array.isArray(payload.zones) ? payload.zones : []).filter(zone =>
          zone?.type === 'fvg' && zone.mitigated !== true && ['bull', 'bear'].includes(zone.direction) &&
          ['start_ts', 'end_ts', 'top', 'bottom'].every(key => zone[key] != null && Number.isFinite(Number(zone[key]))) &&
          Number(zone.top) > Number(zone.bottom) && Number(zone.end_ts) > Number(zone.start_ts))
          // Normalize to the registry's zone shape (seconds, startTime). The
          // backend's end_ts is ignored: the right edge is always recomputed
          // from the actual last closed candle (see drawZones), so it can
          // never drift past the current candle set even if a stale backend
          // end_ts lags behind.
          .map(zone => ({ direction: zone.direction, top: Number(zone.top), bottom: Number(zone.bottom), startTime: Number(zone.start_ts) / 1000 }));
        this.series.setData(candles.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
        const minPrice = candles.length ? Math.min(...candles.map(candle => candle.low)) : 1;
        const precision = Math.min(10, Math.max(2, 3 - Math.floor(Math.log10(minPrice))));
        this.series.applyOptions({ priceFormat: { type: 'price', precision, minMove: 10 ** -precision } });
        this.volume.setData(candles.map(candle => ({ time: candle.time, value: candle.volume, color: candle.close >= candle.open ? '#00c08726' : '#ef66702b' })));
        if (resetView && candles.length) {
          // Keep enough density to read structure; all 300 bars remain pannable.
          this.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, candles.length - 125), to: candles.length + 5 });
        }
        const provider = sourceLabel(payload.source);
        $('.pane-source', this.element).textContent = provider.toLowerCase() === config.exchange ? provider.toUpperCase() : `${EXCHANGES[config.exchange].toUpperCase()} · ${provider.toUpperCase()}`;
        $('.pane-source', this.element).title = `Candle data: ${provider}`;
        const warnings = Array.isArray(payload.warnings) ? payload.warnings.filter(value => typeof value === 'string').join(' · ') : '';
        const timestamp = timeFormat.format(new Date());
        this.element.dataset.state = candles.length ? 'ready' : 'empty';
        this.status.textContent = candles.length ? `${timestamp} UTC · ${provider}${warnings ? ` · ${warnings}` : ''}` : `No closed candles returned · ${provider}${warnings ? ` · ${warnings}` : ''}`;
        this.status.title = this.status.textContent;
        this.count.textContent = `${candles.length} bars · ${this.effectiveFvgZones(getIndicatorSettings('fvg')).length} FVG`;
        this.showMessage(candles.length ? '' : 'No closed candles available for this market and interval. Try another symbol or timeframe.');
        this.applyIndicators();
        if (this.forming?.time <= candles.at(-1)?.time) { this.forming = null; this.removeLivePrice(); }
        this.startTicker(config.exchange, config.symbol);
        if (this.lastResyncBar !== null && this.lastResyncBar !== undefined) this.requestResync(this.lastResyncBar);
        this.renderForming();
        this.refreshContext();
      } catch (error) {
        if (this.disposed || generation !== this.generation || (error.name === 'AbortError' && !timedOut)) return;
        const message = timedOut ? 'Market data request timed out. Use Reload to retry.' : error.message || 'Market data unavailable.';
        this.element.dataset.state = 'error';
        this.status.textContent = `${this.candles.length ? 'STALE · ' : ''}${message}`;
        this.status.title = this.status.textContent;
        this.showMessage(this.candles.length ? '' : message);
      } finally {
        if (generation === this.generation) { clearTimeout(this.requestTimer); this.request = null; this.element.setAttribute('aria-busy', 'false'); }
      }
    }
    destroy() {
      this.stopSeconds(); this.drawings?.dispose(); clearInterval(this.countdownTimer);
      this.footprint?.disable(); this.orderflow?.dispose();
      this.liquiditySession?.dispose(); clearInterval(this.liquidityTimer); clearTimeout(this.liquidityRangeTimer);
      this.disposed = true;
      this.generation += 1;
      this.request?.abort();
      clearTimeout(this.requestTimer);
      this.stopTicker(false);
      this.resetContext(); clearInterval(this.contextTimer);
      this.lifetime.abort();
      this.resizeObserver.disconnect();
      if (this.raf !== null) cancelAnimationFrame(this.raf);
      this.chart.timeScale().unsubscribeVisibleLogicalRangeChange(this.logicalListener);
      this.chart.unsubscribeCrosshairMove(this.crosshairListener);
      this.chart.remove();
      this.element.remove();
    }
  }

  function mountLayout() {
    const count = LAYOUTS[state.layout];
    grid.dataset.layout = state.layout;
    panes.forEach((pane, id) => { if (id >= count) { pane.destroy(); panes.delete(id); } });
    for (let id = 0; id < count; id += 1) if (!panes.has(id)) panes.set(id, new Pane(id));
    if (state.active >= count) state.active = 0;
    setActive(state.active);
    panes.forEach(pane => pane.resize());
    statusText(`${count} independent ${count === 1 ? 'chart' : 'charts'} · active chart ${state.active + 1}${intervalWarning ? ` · ${intervalWarning}` : ' · UTC'}`);
  }
  function createFavorites() {
    const group = $('#favorite-intervals');
    FAVORITES.forEach(([value, label]) => {
      const button = document.createElement('button');
      button.type = 'button'; button.dataset.interval = value; button.textContent = label;
      button.setAttribute('aria-label', `Set active chart interval to ${label}`);
      button.addEventListener('click', () => updatePaneConfig(state.active, { interval: value }));
      group.append(button);
    });
  }
  async function loadIntervals() {
    const request = new AbortController();
    const timeout = setTimeout(() => request.abort(), 15000);
    try {
      const response = await fetch('/api/chart/intervals', { signal: request.signal, headers: { Accept: 'application/json' } });
      const data = await response.json();
      if (!response.ok || !Array.isArray(data.intervals) || !data.intervals.length) throw new Error('Interval list unavailable');
      const seen = new Set();
      const available = data.intervals.filter(item => item && validInterval(String(item.value)) && typeof item.label === 'string').map(item => ({ ...item, value: String(item.value), ...(isSecondsInterval(String(item.value)) && !SECONDS_API ? { available: false, reason: 'Seconds candles module did not load' } : {}) })).filter(item => {
        if (seen.has(item.value)) return false;
        seen.add(item.value); return true;
      });
      if (!available.length) throw new Error('Interval list unavailable');
      intervals = available;
      syncControls();
      panes.forEach(pane => pane.updateHeader());
    } catch (_) {
      intervalWarning = 'Interval metadata unavailable; standard shortcuts shown';
      statusText(intervalWarning);
    } finally { clearTimeout(timeout); }
  }
  function syncSuggestions() {
    const exchange = state.panes[state.active].exchange;
    $('#symbol-input').setAttribute('aria-label', `Change active ${EXCHANGES[exchange]} chart symbol`);
    $('#symbol-suggestions').replaceChildren(...(marketCache.get(exchange) || []).map(market => {
      const option = document.createElement('option');
      option.value = market.symbol;
      option.label = market.display_symbol || market.symbol;
      return option;
    }));
  }
  // Applies the current Chg% sort order within one group of markets. Favorites
  // are sorted independently from the rest so they stay pinned to the top
  // (see renderWatchlist); 'none' restores catalog order untouched.
  function sortMarkets(list) {
    if (state.watchlistSort === 'none') return list;
    return [...list].sort((left, right) => compareChange(left.change_24h, right.change_24h, state.watchlistSort));
  }
  function updateSortIndicator() {
    const button = $('#watchlist-sort-change');
    if (!button) return;
    const arrow = $('.sort-arrow', button);
    if (arrow) arrow.textContent = state.watchlistSort === 'asc' ? '▲' : state.watchlistSort === 'desc' ? '▼' : '';
    button.classList.toggle('is-active', state.watchlistSort !== 'none');
    button.setAttribute('aria-sort', state.watchlistSort === 'asc' ? 'ascending' : state.watchlistSort === 'desc' ? 'descending' : 'none');
  }
  function renderWatchlist() {
    const rows = $('#watchlist-rows');
    const exchange = state.watchlistExchange;
    const search = state.watchlistSearch.trim().toLowerCase();
    const filtered = currentMarkets.filter(market => [market.symbol, market.display_symbol, market.base, market.quote].some(value => typeof value === 'string' && value.toLowerCase().includes(search)));
    // Favorites stay pinned to the top of the current sort order; each group
    // (favorites, rest) is sorted independently so pinning survives sorting.
    const favList = sortMarkets(filtered.filter(market => isFavorite(exchange, market.symbol)));
    const restList = sortMarkets(filtered.filter(market => !isFavorite(exchange, market.symbol)));
    const visible = state.watchlistFavoritesOnly ? favList : [...favList, ...restList];
    const fragment = document.createDocumentFragment();
    visible.forEach(market => {
      const symbol = market.symbol;
      const favorite = isFavorite(exchange, symbol);
      const row = document.createElement('div');
      row.className = 'watchlist-row'; row.dataset.symbol = symbol;
      row.dataset.exchange = exchange;
      row.tabIndex = 0;
      row.setAttribute('role', 'button');
      row.setAttribute('aria-label', `Show ${EXCHANGES[exchange]} ${symbol} in active chart`);
      row.title = `${EXCHANGES[exchange]} · ${symbol} · ${market.display_symbol || symbol}`;
      const star = document.createElement('button');
      star.type = 'button'; star.className = 'watchlist-star';
      star.setAttribute('aria-pressed', String(favorite));
      star.setAttribute('aria-label', `${favorite ? 'Remove' : 'Add'} ${symbol} ${favorite ? 'from' : 'to'} favorites`);
      star.textContent = favorite ? '★' : '☆';
      star.classList.toggle('is-favorite', favorite);
      star.addEventListener('click', event => {
        event.stopPropagation();
        toggleFavorite(exchange, symbol);
        renderWatchlist();
      });
      const name = document.createElement('span'); name.className = 'watchlist-symbol'; name.textContent = market.base || symbol;
      const quote = document.createElement('small'); quote.textContent = `${symbol} · ${market.quote || market.settle || 'PERP'}`; name.append(quote);
      const price = document.createElement('span'); price.className = 'watchlist-price'; price.textContent = typeof market.price === 'number' && Number.isFinite(market.price) && market.price > 0 ? formatPrice(market.price) : '—';
      const change = document.createElement('span'); change.className = 'watchlist-change';
      const validChange = typeof market.change_24h === 'number' && Number.isFinite(market.change_24h);
      change.textContent = validChange ? `${market.change_24h >= 0 ? '+' : ''}${market.change_24h.toFixed(2)}%` : '—';
      change.classList.toggle('positive', validChange && market.change_24h >= 0);
      change.classList.toggle('negative', validChange && market.change_24h < 0);
      row.append(star, name, price, change);
      row.addEventListener('click', () => updatePaneConfig(state.active, { exchange, symbol }));
      row.addEventListener('keydown', event => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        updatePaneConfig(state.active, { exchange, symbol });
      });
      fragment.append(row);
    });
    rows.replaceChildren(fragment);
    if (!visible.length) {
      const empty = document.createElement('p'); empty.className = 'watchlist-empty';
      empty.textContent = !filtered.length ? (currentMarkets.length ? 'No markets match your search.' : 'No perpetual markets returned by this venue.') : 'No favorite markets yet. Star a market to pin it here.';
      rows.append(empty);
    }
    $('#watchlist-count').textContent = `${visible.length} / ${currentMarkets.length}`;
    updateSortIndicator();
    syncControls();
  }
  async function refreshPrices(force = false) {
    pricesController?.abort();
    clearTimeout(pricesTimeout);
    const generation = ++pricesGeneration;
    const exchange = state.watchlistExchange;
    pricesController = new AbortController();
    const request = pricesController;
    pricesTimeout = setTimeout(() => request.abort(), 25000);
    const status = $('#prices-status');
    // Never retain quotes under a newly selected venue, including on failure.
    currentMarkets = [];
    catalogReady = false;
    $('#watchlist-rows').replaceChildren();
    $('#watchlist-count').textContent = '— / —';
    $('#watchlist').setAttribute('aria-busy', 'true');
    $('#watchlist-retry').hidden = true;
    status.textContent = `Loading ${EXCHANGES[exchange]} perpetual markets…`;
    try {
      const query = new URLSearchParams({ exchange });
      if (force === true) query.set('force', 'true');
      const response = await fetch(`/api/markets?${query}`, { signal: request.signal, cache: 'no-store', headers: { Accept: 'application/json' } });
      const data = await response.json();
      if (request.signal.aborted || generation !== pricesGeneration || exchange !== state.watchlistExchange) return;
      if (!response.ok) throw new Error(errorText(data, `Markets unavailable (HTTP ${response.status}).`));
      if (data.exchange !== exchange || !Array.isArray(data.markets)) throw new Error('Invalid venue market catalog; no prices shown.');
      if (data.markets.some(market => !market || market.exchange !== exchange || !validSymbol(market.symbol, exchange))) throw new Error('Invalid native instrument in venue catalog; no prices shown.');
      if (new Set(data.markets.map(market => market.symbol)).size !== data.markets.length) throw new Error('Duplicate instruments in venue catalog; no prices shown.');
      currentMarkets = [...data.markets].sort((left, right) => String(left.base || left.symbol).localeCompare(String(right.base || right.symbol), 'en') || left.symbol.localeCompare(right.symbol, 'en'));
      marketCache.set(exchange, currentMarkets);
      catalogReady = true;
      renderWatchlist();
      const priced = currentMarkets.filter(market => typeof market.price === 'number' && Number.isFinite(market.price) && market.price > 0).length;
      const timestamp = typeof data.as_of === 'number' && Number.isFinite(data.as_of) ? `${timeFormat.format(new Date(data.as_of))} UTC` : 'time unavailable';
      const warnings = Array.isArray(data.warnings) ? data.warnings.filter(value => typeof value === 'string').join(' · ') : '';
      status.textContent = `${EXCHANGES[exchange]} · snapshot ${timestamp}${data.cached ? ' · cached' : ''} · ${priced}/${currentMarkets.length} prices${warnings ? ` · ${warnings}` : ''}`;
    } catch (error) {
      if (generation !== pricesGeneration || exchange !== state.watchlistExchange) return;
      status.textContent = `${EXCHANGES[exchange]} · ${error.name === 'AbortError' ? 'Market request timed out.' : error.message || 'Markets unavailable.'} Retry to load this venue.`;
      $('#watchlist-count').textContent = '— / —';
      $('#watchlist-retry').hidden = false;
    } finally { if (generation === pricesGeneration) { clearTimeout(pricesTimeout); pricesController = null; $('#watchlist').setAttribute('aria-busy', 'false'); } }
  }
  function isVisible(pane) {
    const rect = pane.element.getBoundingClientRect();
    const area = grid.getBoundingClientRect();
    return rect.bottom > area.top && rect.top < area.bottom && rect.right > area.left && rect.left < area.right;
  }
  function autoRefresh() {
    if (!state.auto || document.visibilityState !== 'visible') return;
    panes.forEach(pane => { if (isVisible(pane) && !pane.request) pane.load(false); });
    if (state.sidebar && matchMedia('(min-width:1001px)').matches && !pricesController) refreshPrices();
  }
  // ─── Part A: generic settings popover, schema-driven per docs/INDICATOR_PLUGIN_PLAN.md #2 ───
  // One shared popover element, anchored near whichever ⚙ button opened it.
  // Apply recomputes immediately; Reset restores the indicator's documented
  // defaults; Escape/outside-click closes it. Design choice (documented here
  // since the plan text left it open): typed-but-not-yet-applied edits are
  // simply discarded on close, same as any standard settings dialog — they
  // are never silently committed, and reopening shows the last *applied*
  // values, not a stale draft.
  function buildSettingsPopover() {
    const popover = document.createElement('div');
    popover.className = 'indicator-settings-popover';
    popover.hidden = true;
    popover.setAttribute('role', 'dialog');
    popover.setAttribute('aria-modal', 'false');
    popover.innerHTML = '<header><span class="indicator-settings-title"></span><button type="button" class="indicator-settings-close" aria-label="Close settings">×</button></header><div class="indicator-settings-fields"></div><footer><button type="button" class="indicator-settings-reset">Reset</button><button type="button" class="indicator-settings-apply">Apply</button></footer>';
    document.body.append(popover);
    return popover;
  }
  const settingsPopover = buildSettingsPopover();
  let settingsPopoverId = null;
  function renderSettingsFields(id) {
    const def = INDICATOR_BY_ID.get(id);
    const settings = getIndicatorSettings(id);
    const fields = $('.indicator-settings-fields', settingsPopover);
    fields.innerHTML = '';
    let group = null;
    def.settingsSchema.forEach(field => {
      if (field.group && field.group !== group) {
        group = field.group;
        const heading = document.createElement('div'); heading.className = 'indicator-settings-group'; heading.textContent = group;
        fields.append(heading);
      }
      const row = document.createElement('label');
      row.className = 'indicator-settings-field';
      const labelSpan = document.createElement('span');
      labelSpan.textContent = field.label;
      row.append(labelSpan);
      let input;
      if (field.type === 'select') {
        input = document.createElement('select');
        field.options.forEach(option => {
          const optionEl = document.createElement('option');
          optionEl.value = option; optionEl.textContent = field.optionLabels?.[option] ?? option;
          input.append(optionEl);
        });
        input.value = settings[field.key];
      } else if (field.type === 'boolean') {
        input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = Boolean(settings[field.key]);
      } else if (field.type === 'color') {
        input = document.createElement('input');
        input.type = 'color';
        input.value = settings[field.key];
      } else if (field.type === 'text' || field.type === 'session') {
        input = document.createElement('input');
        input.type = 'text'; input.required = true; input.spellcheck = false;
        if (field.type === 'session') { input.pattern = '(?:[01]\\d|2[0-3])[0-5]\\d-(?:[01]\\d|2[0-3])[0-5]\\d'; input.placeholder = 'HHMM-HHMM'; input.title = 'Session window HHMM-HHMM; end before start crosses midnight'; }
        else input.maxLength = field.maxLength || 40;
        input.value = settings[field.key];
      } else {
        input = document.createElement('input');
        input.type = 'number';
        if (typeof field.min === 'number') input.min = String(field.min);
        if (typeof field.max === 'number') input.max = String(field.max);
        if (typeof field.step === 'number') input.step = String(field.step);
        input.value = settings[field.key];
      }
      input.className = 'indicator-settings-input';
      input.dataset.key = field.key;
      input.dataset.type = field.type;
      row.append(input);
      fields.append(row);
    });
  }
  function openSettingsPopover(id, anchor) {
    settingsPopoverId = id;
    $('.indicator-settings-title', settingsPopover).textContent = `${INDICATOR_BY_ID.get(id).label} settings`;
    renderSettingsFields(id);
    settingsPopover.hidden = false;
    $all('.indicator-settings-btn').forEach(button => button.setAttribute('aria-expanded', String(button === anchor)));
    const rect = anchor.getBoundingClientRect();
    settingsPopover.style.top = `${Math.round(rect.bottom + 6)}px`;
    settingsPopover.style.left = `${Math.round(rect.left)}px`;
    requestAnimationFrame(() => {
      if (settingsPopover.hidden) return;
      const popRect = settingsPopover.getBoundingClientRect();
      if (popRect.right > window.innerWidth - 8) settingsPopover.style.left = `${Math.max(8, window.innerWidth - popRect.width - 8)}px`;
      if (popRect.bottom > window.innerHeight - 8) settingsPopover.style.top = `${Math.max(8, rect.top - popRect.height - 6)}px`;
    });
  }
  function closeSettingsPopover() {
    if (settingsPopover.hidden) return;
    settingsPopover.hidden = true;
    settingsPopoverId = null;
    $all('.indicator-settings-btn').forEach(button => button.setAttribute('aria-expanded', 'false'));
  }
  function applySettingsPopover() {
    if (!settingsPopoverId) return;
    const def = INDICATOR_BY_ID.get(settingsPopoverId);
    const patch = {};
    const inputs = $all('.indicator-settings-input', settingsPopover);
    const invalid = inputs.find(input => !input.checkValidity());
    if (invalid) { invalid.reportValidity(); return; }
    inputs.forEach(input => {
      const field = def.settingsSchema.find(entry => entry.key === input.dataset.key);
      if (!field) return;
      patch[field.key] = field.type === 'boolean' ? input.checked : input.value;
    });
    setIndicatorSettings(settingsPopoverId, patch);
    panes.forEach(pane => pane.applyIndicators());
    if (settingsPopoverId === 'ema-ribbon') analyst?.activeChanged(); // analysis key includes the periods
  }
  function resetSettingsPopover() {
    if (!settingsPopoverId) return;
    resetIndicatorSettings(settingsPopoverId);
    renderSettingsFields(settingsPopoverId);
    panes.forEach(pane => pane.applyIndicators());
    if (settingsPopoverId === 'ema-ribbon') analyst?.activeChanged();
  }
  function indicatorLabel(def) {
    const settings = getIndicatorSettings(def.id);
    if (def.id === 'ema200') return `EMA ${settings.period}`;
    return typeof def.labelForSettings === 'function' ? def.labelForSettings(settings) : `${def.label}${settings.period !== undefined ? ` ${settings.period}` : settings.length !== undefined ? ` ${settings.length}` : ''}`;
  }
  function syncIndicatorLabels() {
    INDICATOR_DEFS.forEach(def => {
      const button = $(`.indicator-settings-btn[data-indicator="${def.id}"]`);
      if (!button) return;
      const label = indicatorLabel(def);
      button.title = `${label} settings`; button.setAttribute('aria-label', button.title);
      const chip = button.previousElementSibling;
      if (chip?.matches('label.toggle-chip')) $('span', chip).textContent = label;
    });
  }
  function initGenericIndicators() {
    const group = $('#extra-indicator-controls');
    // One chip + one ⚙ per indicator; grouped overlays first, liquidity last.
    const order = def => ['ema-ribbon', 'ema200', 'ma-channel', 'turtle-zone', 'vwap-daily', 'vwap-weekly', 'opens', 'sessions', 'big-trades', 'abnormal-volume'].indexOf(def.id) + 1 || 99;
    INDICATOR_DEFS.filter(def => !def.hidden && !$(`.indicator-settings-btn[data-indicator="${def.id}"]`)).sort((a, b) => order(a) - order(b)).forEach(def => {
      const label = document.createElement('label'); label.className = 'toggle-chip'; label.dataset.indicatorChip = def.id;
      const input = document.createElement('input'); input.type = 'checkbox'; input.dataset.indicatorToggle = def.id;
      input.checked = isIndicatorEnabled(def.id);
      const span = document.createElement('span'); span.textContent = indicatorLabel(def); label.append(input, span);
      const button = document.createElement('button'); button.type = 'button'; button.className = 'indicator-settings-btn'; button.dataset.indicator = def.id;
      button.textContent = '⚙'; button.setAttribute('aria-expanded', 'false'); button.setAttribute('aria-label', `${def.label} settings`);
      input.addEventListener('change', () => { setIndicatorEnabled(def.id, input.checked); panes.forEach(pane => pane.applyIndicators()); });
      group.append(label, button);
    });
    syncIndicatorLabels();
  }
  function initSettingsPopovers() {
    $all('.indicator-settings-btn').forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        const id = button.dataset.indicator;
        if (settingsPopoverId === id) { closeSettingsPopover(); return; }
        openSettingsPopover(id, button);
      });
    });
    $('.indicator-settings-close', settingsPopover).addEventListener('click', closeSettingsPopover);
    $('.indicator-settings-apply', settingsPopover).addEventListener('click', applySettingsPopover);
    $('.indicator-settings-reset', settingsPopover).addEventListener('click', resetSettingsPopover);
    document.addEventListener('keydown', event => { if (event.key === 'Escape') closeSettingsPopover(); });
    document.addEventListener('click', event => {
      if (settingsPopover.hidden) return;
      if (settingsPopover.contains(event.target) || event.target.closest?.('.indicator-settings-btn')) return;
      closeSettingsPopover();
    });
  }
  function init() {
    if (!window.LightweightCharts) {
      $('#workspace').classList.add('is-unavailable');
      grid.textContent = 'Chart library could not be loaded. Check the locally served Lightweight Charts asset and reload.';
      statusText('Chart library unavailable · no data shown');
      return;
    }
    initialized = true;
    initGenericIndicators(); renderLocalAlerts();
    $('#local-alerts-toggle').checked = state.localAlerts;
    $('#local-alerts-toggle').addEventListener('change', event => { state.localAlerts = event.target.checked; saveState(); });
    createFavorites();
    $('#exchange-select').value = state.watchlistExchange;
    $('#watchlist-search').value = state.watchlistSearch;
    $('#exchange-select').addEventListener('change', event => {
      state.watchlistExchange = event.target.value;
      state.watchlistSearch = '';
      $('#watchlist-search').value = '';
      saveState(); refreshPrices();
    });
    $('#watchlist-search').addEventListener('input', event => { state.watchlistSearch = event.target.value; saveState(); if (catalogReady && !pricesController) renderWatchlist(); });
    $('#fvg-toggle').checked = state.fvg;
    $('#auto-refresh').checked = state.auto;
    if (migrationNotes.length) statusText(`Settings migrated to grouped indicators · ${migrationNotes.join(' · ')}`);
    $('#watchlist-favorites-toggle').setAttribute('aria-pressed', String(state.watchlistFavoritesOnly));
    $('#watchlist-favorites-toggle').classList.toggle('is-active', state.watchlistFavoritesOnly);
    $('#watchlist-favorites-toggle').addEventListener('click', () => {
      state.watchlistFavoritesOnly = !state.watchlistFavoritesOnly;
      $('#watchlist-favorites-toggle').setAttribute('aria-pressed', String(state.watchlistFavoritesOnly));
      $('#watchlist-favorites-toggle').classList.toggle('is-active', state.watchlistFavoritesOnly);
      saveState();
      if (catalogReady) renderWatchlist();
    });
    updateSortIndicator();
    $('#watchlist-sort-change').addEventListener('click', () => {
      // Cycle: ascending -> descending -> default catalog order -> ascending.
      state.watchlistSort = state.watchlistSort === 'none' ? 'asc' : state.watchlistSort === 'asc' ? 'desc' : 'none';
      updateSortIndicator();
      saveState();
      if (catalogReady) renderWatchlist();
    });
    $('#symbol-form').addEventListener('submit', event => { event.preventDefault(); submitSymbol($('#symbol-input'), state.active); });
    $('#symbol-input').addEventListener('input', event => event.target.setCustomValidity(''));
    $('#interval-select').addEventListener('change', event => updatePaneConfig(state.active, { interval: event.target.value }));
    $('#layout-select').addEventListener('change', event => { state.layout = event.target.value; mountLayout(); saveState(); });
    $('#fvg-toggle').addEventListener('change', event => { state.fvg = event.target.checked; panes.forEach(pane => pane.scheduleDraw()); saveState(); });
    $('#auto-refresh').addEventListener('change', event => { state.auto = event.target.checked; saveState(); statusText(state.auto ? 'Auto refresh enabled · visible charts, every 60 seconds · closed candles only' : 'Auto refresh off · reload charts manually'); });
    $('#reload-active').addEventListener('click', () => panes.get(state.active)?.load(false));
    $('#watchlist-toggle').addEventListener('click', () => { state.sidebar = !state.sidebar; syncControls(); saveState(); });
    $('#refresh-prices').addEventListener('click', () => refreshPrices(true));
    $('#watchlist-retry').addEventListener('click', () => refreshPrices(true));
    document.addEventListener('click', event => document.querySelectorAll('.pane-symbol-menu[open]').forEach(menu => { if (!menu.contains(event.target)) menu.open = false; }));
    initSettingsPopovers();
    mountAnalyst();
    mountLayout(); loadIntervals(); refreshPrices();
  }
  function mountAnalyst() {
    const api = window.WorkspaceAnalyst;
    if (!api) return;
    analyst = api.createController({
      fetch: (url, options) => fetch(url, options),
      getActive: () => {
        const config = state.panes[state.active], pane = panes.get(state.active);
        if (!config || !pane) return null;
        const ribbon = getIndicatorSettings('ema-ribbon') || {};
        return { id: state.active, exchange: config.exchange, symbol: config.symbol, interval: config.interval,
          precision: pane.series.options().priceFormat?.precision,
          // The analysis describes the same EMA ribbon the chart draws (sent as ema_periods).
          emaPeriods: [ribbon.period1, ribbon.period2, ribbon.period3] };
      },
      drawLines: (id, rows) => panes.get(id)?.setAnalystLines(rows),
      clearLines: id => panes.get(id)?.clearAnalystLines(),
      centerOn: (id, price) => panes.get(id)?.centerOnPrice(price),
      levels: window.ScreenerLevels || null,
      visible: () => document.visibilityState === 'visible'
    });
    mountDashboard();
  }
  function scrollToAnalysis() { $('#analysis-dashboard')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  function mountDashboard() {
    const api = window.AnalysisDashboard, el = $('#analysis-dashboard');
    if (!api || !el || !analyst) return;
    dashboard = api.mount(el, {
      analyst,
      getFunding: () => panes.get(state.active)?.fundingData || null,
      fetch: (url, options) => fetch(url, options),
      IntersectionObserver: window.IntersectionObserver || null,
      pageVisible: () => document.visibilityState === 'visible',
      scrollToCharts: () => window.scrollTo({ top: 0, behavior: 'smooth' }),
      setWyckoffOverlay: model => panes.forEach((pane, id) => pane.setWyckoff(model && id === state.active ? model : null)),
      setSmcOverlay: model => panes.forEach((pane, id) => pane.setSmc(model && id === state.active ? model : null))
    });
    // Wheel over a chart zooms it and never scrolls the (now scrollable) page.
    disposeWheelGuard = api.installWheelGuard(grid);
    document.querySelectorAll('[data-jump="analysis"]').forEach(link => link.addEventListener('click', event => { event.preventDefault(); scrollToAnalysis(); }));
  }
  // Funding: refetch visible panes every 30 s (server caches 30 s too); tick countdowns every second.
  const fundingTimer = setInterval(() => {
    if (document.visibilityState !== 'visible') return;
    panes.forEach(pane => { if (isVisible(pane)) pane.refreshFunding(); });
  }, 30000);
  const fundingTick = setInterval(() => {
    if (document.visibilityState !== 'visible') return;
    const now = Date.now();
    panes.forEach(pane => pane.renderFundingChip(now));
    dashboard?.tick(now);
  }, 1000);
  const refreshTimer = setInterval(autoRefresh, 60000);
  // pagehide also covers browser navigation with bfcache; pageshow reconstructs
  // disposed instances without leaving their requests or observers alive.
  window.addEventListener('pagehide', () => {
    clearInterval(refreshTimer); clearInterval(fundingTimer); clearInterval(fundingTick); dashboard?.destroy(); disposeWheelGuard?.(); analyst?.destroy();
    pricesGeneration += 1; pricesController?.abort(); clearTimeout(pricesTimeout);
    panes.forEach(pane => pane.destroy()); panes.clear();
  });
  window.addEventListener('pageshow', event => { if (event.persisted && initialized) location.reload(); });
  // Debug/test surface only (no prod secrets, read/write of already-exposed
  // state): lets the Playwright pixel regressions drive settings and read
  // Pane internals precisely instead of poking at private closures.
  window.__workspaceDebug = { state, panes, get analyst() { return analyst; }, get dashboard() { return dashboard; }, INDICATOR_DEFS, INDICATOR_BY_ID, getIndicatorSettings, setIndicatorSettings, resetIndicatorSettings, setIndicatorEnabled, fvgZones, defaultIndicatorSettings };
  init();
})();
