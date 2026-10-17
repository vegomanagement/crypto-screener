/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/.
 *
 * JavaScript port of the TradingView script "Daily Weekly Monthly Yearly Opens"
 * by sbtnc (© sbtnc), version 7.0 (Pine v6, last modified 2024-12-11),
 * https://www.tradingview.com/script/2lN76Vax-Daily-Weekly-Monthly-Yearly-Opens/
 * Original source with its notice: third_party/tradingview/sbtnc-daily-weekly-monthly-yearly-opens-v7.pine
 * License text: third_party/tradingview/LICENSE-MPL-2.0.
 *
 * This is a port, not the original: opens are taken from real higher-timeframe
 * candles served by /api/chart (and the venue's forming D/W bar from
 * /api/chart/live) in UTC; previous-period highs/lows (D/W/M) were added as
 * individually toggleable sub-levels. Port differences: docs/INDICATOR_GROUPS_RESULT.md.
 */
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.WorkspaceOpens = factory();
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const DAY = 86400;
  const TIMEFRAMES = ['1h', '4h', 'D', 'W', 'M', '3M', '6M', 'Y'];
  // Nominal seconds, only used for the script's isVisible() rule
  // (an open is shown only when its timeframe is larger than the chart's).
  const NOMINAL = { '1h': 3600, '4h': 14400, D: DAY, W: 7 * DAY, M: 30 * DAY, '3M': 91 * DAY, '6M': 182 * DAY, Y: 365 * DAY };
  const NAMES = { '1h': 'H', '4h': '4H', D: 'D', W: 'W', M: 'M', '3M': '3M', '6M': '6M', Y: 'Y' };
  // Script defaults: #1 D green, #2 W orange, #3 M red, #4 3M purple (off), #5 12M blue.
  const SLOT_DEFAULTS = [['D', '#4caf50', true], ['W', '#ff9800', true], ['M', '#f23645', true], ['3M', '#9c27b0', false], ['Y', '#2962ff', true]];
  const PREV = [['pdh', 'D', 'high', 'PDH', '#fb7185'], ['pdl', 'D', 'low', 'PDL', '#34d399'], ['pwh', 'W', 'high', 'PWH', '#f472b6'],
    ['pwl', 'W', 'low', 'PWL', '#2dd4bf'], ['pmh', 'M', 'high', 'PMH', '#e879f9'], ['pml', 'M', 'low', 'PML', '#22d3ee']];
  const OFFSET_PADDING = 4, HEAD_PADDING = -2;

  // ── UTC anchoring (crypto venues are 24/7: no session, 00:00 UTC days,
  // Monday 00:00 UTC weeks, calendar months/quarters, 1 January years) ──
  function periodStart(time, tf) {
    if (tf === '1h' || tf === '4h') { const size = NOMINAL[tf]; return Math.floor(time / size) * size; }
    const day = Math.floor(time / DAY) * DAY;
    if (tf === 'D') return day;
    if (tf === 'W') return day - ((new Date(day * 1000).getUTCDay() + 6) % 7) * DAY;
    const d = new Date(day * 1000), y = d.getUTCFullYear(), m = d.getUTCMonth();
    if (tf === 'M') return Date.UTC(y, m, 1) / 1000;
    if (tf === '3M') return Date.UTC(y, m - m % 3, 1) / 1000;
    if (tf === '6M') return Date.UTC(y, m - m % 6, 1) / 1000;
    if (tf === 'Y') return Date.UTC(y, 0, 1) / 1000;
    throw new RangeError(`unknown timeframe ${tf}`);
  }
  function shiftPeriod(start, tf, count) {
    if (tf === '1h' || tf === '4h' || tf === 'D' || tf === 'W') return start + count * ({ D: DAY, W: 7 * DAY }[tf] || NOMINAL[tf]);
    const months = { M: 1, '3M': 3, '6M': 6, Y: 12 }[tf] * count, d = new Date(start * 1000);
    return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + months, 1) / 1000;
  }
  const nextPeriodStart = (start, tf) => shiftPeriod(start, tf, 1);
  const prevPeriodStart = (start, tf) => shiftPeriod(start, tf, -1);
  const isVisible = (tf, chartStep) => NOMINAL[tf] > chartStep;

  function parseCandles(raw) {
    if (!Array.isArray(raw)) return [];
    const out = new Map();
    raw.forEach(c => {
      const v = ['ts', 'o', 'h', 'l', 'c'].map(k => Number(c?.[k]));
      if (v.some(x => !Number.isFinite(x)) || v[0] <= 0 || Math.min(...v.slice(1)) <= 0 || v[2] < v[3]) return;
      const time = Math.floor(v[0] / 1000);
      out.set(time, { time, open: v[1], high: v[2], low: v[3], close: v[4], closed: c.is_forming !== true });
    });
    return [...out.values()].sort((a, b) => a.time - b.time);
  }

  // Opens of `tf`, keyed by period start, taken ONLY from a real candle that
  // starts exactly at the period start (never from a later bar: that would
  // fabricate a price). Finest source first: daily, then native W / M, then
  // the chart's own candles.
  function collectOpens(sources, tf) {
    const out = new Map();
    const take = (candles, accept) => (candles || []).forEach(c => {
      if (!accept(c)) return;
      const start = periodStart(c.time, tf);
      if (c.time === start && !out.has(start)) out.set(start, { start, price: c.open, source: accept.name });
    });
    const daily = function daily() { return true; };
    if (tf !== '1h' && tf !== '4h') take(sources.D, daily);
    if (tf === 'W') take(sources.W, function weekly() { return true; });
    if (['M', '3M', '6M', 'Y'].includes(tf)) take(sources.M, function monthly() { return true; });
    take(sources.chart, function chart() { return true; });
    return [...out.values()].sort((a, b) => a.start - b.start);
  }
  function atr(candles, length = 10) {
    if (!Array.isArray(candles) || candles.length < length) return null;
    let value = null;
    candles.forEach((c, i) => {
      const prev = i ? candles[i - 1].close : c.close;
      const tr = Math.max(c.high - c.low, Math.abs(c.high - prev), Math.abs(c.low - prev));
      if (i < length - 1) value = (value || 0) + tr;
      else if (i === length - 1) value = (value + tr) / length;
      else value = (value * (length - 1) + tr) / length; // Pine ta.atr = RMA of true range
    });
    return value;
  }
  function settingsDefaults() {
    const s = { rightOffset: 20, tails: false, heads: false, labels: true, labelPrice: false, prevLookback: 1, prevWidth: 1 };
    SLOT_DEFAULTS.forEach(([tf, color, on], i) => Object.assign(s, { [`o${i + 1}Show`]: on, [`o${i + 1}Tf`]: tf, [`o${i + 1}Color`]: color, [`o${i + 1}Lookback`]: 1 }));
    PREV.forEach(([key, , , , color]) => Object.assign(s, { [`${key}Show`]: true, [`${key}Color`]: color }));
    return s;
  }

  // Pure model: every level that should be drawn, in time/price space.
  // chart = { candles (closed, chart interval), step (seconds) }.
  function computeModel(sources, settings, chart) {
    const s = { ...settingsDefaults(), ...(settings || {}) };
    const candles = chart?.candles || [];
    const warnings = [], levels = [], heads = [], tails = [];
    if (!candles.length) return { levels, heads, tails, warnings: ['No closed chart candles'], refTime: null };
    const step = chart.step, refTime = candles.at(-1).time;
    const threshold = (atr(candles, 10) ?? 0) / 2;
    const current = [];
    for (let i = 1; i <= 5; i += 1) {
      const tf = s[`o${i}Tf`];
      if (!s[`o${i}Show`] || !TIMEFRAMES.includes(tf)) continue;
      if (!isVisible(tf, step)) continue;
      const lookback = Math.max(1, Math.min(500, Math.trunc(s[`o${i}Lookback`]) || 1));
      const all = collectOpens(sources, tf).filter(o => o.start <= refTime);
      const currentStart = periodStart(refTime, tf);
      const last = all.at(-1);
      if (!last || last.start !== currentStart) warnings.push(`${NAMES[tf]} open of the current period is unavailable (no ${tf} candle starting ${new Date(currentStart * 1000).toISOString().slice(0, 10)} UTC); not drawn`);
      const picked = all.slice(-lookback);
      picked.forEach(o => {
        const isCurrent = o.start === currentStart;
        const level = { kind: 'open', slot: i, tf, name: NAMES[tf], color: s[`o${i}Color`], price: o.price, startTime: o.start,
          endTime: isCurrent ? null : Math.min(nextPeriodStart(o.start, tf), refTime + step), current: isCurrent, source: o.source, padding: 0 };
        levels.push(level);
        if (isCurrent) current.push(level);
        else if (s.heads) heads.push({ ...level, fromTime: level.endTime });
      });
      const cur = picked.at(-1);
      if (s.tails && cur && cur.start === currentStart) tails.push({ color: s[`o${i}Color`], price: cur.price, toTime: cur.start });
    }
    // Previous-period highs / lows from CLOSED higher-timeframe candles.
    const prevLookback = Math.max(1, Math.min(50, Math.trunc(s.prevLookback) || 1));
    PREV.forEach(([key, tf, field, name]) => {
      if (!s[`${key}Show`] || !isVisible(tf, step)) return;
      const closed = new Map((sources[tf] || []).filter(c => c.closed !== false && c.time === periodStart(c.time, tf)).map(c => [c.time, c]));
      let start = periodStart(refTime, tf), missing = 0;
      for (let k = 0; k < prevLookback; k += 1, start = prevPeriodStart(start, tf)) {
        const source = closed.get(prevPeriodStart(start, tf));
        if (!source) { missing += 1; continue; }
        const isCurrent = k === 0;
        levels.push({ kind: 'prev', key, tf, name, color: s[`${key}Color`], price: source[field], startTime: start,
          endTime: isCurrent ? null : nextPeriodStart(start, tf), current: isCurrent, sourceTime: source.time, padding: 0, dash: [4, 3] });
      }
      if (missing === prevLookback) warnings.push(`${name}: previous ${tf} candle not available; not drawn`);
    });
    // Label de-overlap, as in the script: each later slot is pushed right by
    // OFFSET_PADDING bars per earlier displayed level within ATR(10)/2
    // (extended here to the added previous-high/low labels).
    levels.filter(l => l.kind === 'prev' && l.current).forEach(l => current.push(l));
    current.forEach((level, idx) => {
      level.padding = current.slice(0, idx).filter(other => Math.abs(other.price - level.price) <= threshold).length;
    });
    return { levels, heads, tails, warnings, refTime, step, threshold };
  }
  function neededTimeframes(settings, chartStep) {
    const s = { ...settingsDefaults(), ...(settings || {}) }, need = new Set();
    for (let i = 1; i <= 5; i += 1) {
      const tf = s[`o${i}Tf`];
      if (!s[`o${i}Show`] || !isVisible(tf, chartStep)) continue;
      if (tf === 'W') need.add('W');
      if (['M', '3M', '6M', 'Y'].includes(tf)) need.add('M');
      if (tf !== '1h' && tf !== '4h') need.add('D');
    }
    PREV.forEach(([key, tf]) => { if (s[`${key}Show`] && isVisible(tf, chartStep)) need.add(tf); });
    return [...need].sort();
  }

  // ── Settings schema (rendered by the shared popover; `group` = heading) ──
  function settingsSchema() {
    const d = settingsDefaults(), fields = [];
    for (let i = 1; i <= 5; i += 1) {
      const group = `Open #${i}`;
      fields.push({ key: `o${i}Show`, label: 'Enabled', type: 'boolean', default: d[`o${i}Show`], group },
        { key: `o${i}Tf`, label: 'Timeframe', type: 'select', options: TIMEFRAMES.slice(), default: d[`o${i}Tf`], group },
        { key: `o${i}Color`, label: 'Color', type: 'color', default: d[`o${i}Color`], group },
        { key: `o${i}Lookback`, label: 'Lookback (opens)', type: 'number', min: 1, max: 500, step: 1, default: 1, group });
    }
    fields.push({ key: 'rightOffset', label: 'Right offset (bars)', type: 'number', min: 1, max: 200, step: 1, default: 20, group: 'Style' },
      { key: 'tails', label: 'Show tails (current open ← left)', type: 'boolean', default: false, group: 'Style' },
      { key: 'heads', label: 'Show projections (previous opens →)', type: 'boolean', default: false, group: 'Style' },
      { key: 'labels', label: 'Labels', type: 'boolean', default: true, group: 'Style' },
      { key: 'labelPrice', label: 'Price in labels', type: 'boolean', default: false, group: 'Style' });
    PREV.forEach(([key, , , name]) => fields.push({ key: `${key}Show`, label: name, type: 'boolean', default: true, group: 'Previous highs / lows' },
      { key: `${key}Color`, label: `${name} color`, type: 'color', default: d[`${key}Color`], group: 'Previous highs / lows' }));
    fields.push({ key: 'prevLookback', label: 'Prev H/L history (periods)', type: 'number', min: 1, max: 50, step: 1, default: 1, group: 'Previous highs / lows' },
      { key: 'prevWidth', label: 'Prev H/L width', type: 'number', min: 1, max: 4, step: 1, default: 1, group: 'Previous highs / lows' });
    return fields;
  }

  // ── Canvas renderer. view = { x(time) → px (bar-boundary), lastX (px centre of
  // last closed bar), barSpacing, y(price), plotWidth, format(price) } ──
  function rgba(hex, alpha) {
    const m = /^#([0-9a-f]{6})$/i.exec(hex || ''); if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${alpha})`;
  }
  function draw(context, view, model, settings) {
    const s = { ...settingsDefaults(), ...(settings || {}) };
    if (!model || !model.levels) return 0;
    let drawn = 0;
    const line = (x1, x2, y, color, width, dash) => {
      if (![x1, x2, y].every(Number.isFinite)) return;
      context.save(); context.strokeStyle = color; context.lineWidth = width; context.setLineDash?.(dash || []);
      context.beginPath(); context.moveTo(x1, Math.round(y) + 0.5); context.lineTo(x2, Math.round(y) + 0.5); context.stroke(); context.restore();
      drawn += 1;
    };
    const rightX = padding => view.lastX + (s.rightOffset + padding * OFFSET_PADDING) * view.barSpacing;
    model.tails.forEach(t => line(0, view.x(t.toTime), view.y(t.price), t.color, 1, [1, 3]));
    model.heads.forEach(h => line(view.x(h.fromTime), view.lastX + (s.rightOffset + HEAD_PADDING * OFFSET_PADDING) * view.barSpacing, view.y(h.price), rgba(h.color, 0.4), 1));
    model.levels.forEach(level => {
      const x1 = view.x(level.startTime), x2 = level.current ? rightX(level.padding) : view.x(level.endTime), y = view.y(level.price);
      line(x1, x2, y, level.color, level.kind === 'prev' ? s.prevWidth : 1, level.dash);
      if (s.labels && level.current && Number.isFinite(y) && Number.isFinite(x2)) {
        context.save(); context.fillStyle = level.color; context.font = '10px SFMono-Regular, Consolas, monospace'; context.textBaseline = 'middle';
        const label = s.labelPrice ? `${level.name} ${view.format(level.price)}` : level.name;
        const width = context.measureText ? context.measureText(label).width : label.length * 6;
        let labelX = x2 + 3, labelY = y;
        // The script's offset assumes empty space right of the last bar; when the
        // label would leave the plot, pin it inside the edge (one label width per
        // overlapping level) just above its line instead of losing it.
        if (Number.isFinite(view.plotWidth) && labelX + width > view.plotWidth) { labelX = view.plotWidth - width - 2 - level.padding * (width + 6); labelY = y - 7; }
        context.fillText(label, labelX, labelY);
        context.restore();
      }
    });
    return drawn;
  }

  // ── Higher-timeframe loader: real candles via /api/chart (closed) and
  // /api/chart/live (the venue's forming D/W bar). Cached 60 s per market. ──
  function createLoader({ fetch: fetchFn, ttlMs = 60000, now = () => Date.now() }) {
    const cache = new Map();
    const limits = { D: 500, W: 500, M: 60 };
    async function getJson(url) {
      const response = await fetchFn(url, { headers: { Accept: 'application/json' }, cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.error || `HTTP ${response.status}`);
      return payload;
    }
    async function loadOne(exchange, symbol, tf) {
      const q = new URLSearchParams({ exchange, symbol, interval: tf, limit: String(limits[tf]) });
      const payload = await getJson(`/api/chart?${q}`);
      if (payload.exchange !== exchange || payload.closed_only !== true) throw new Error('provider mismatch');
      let candles = parseCandles(payload.candles).map(c => ({ ...c, closed: true }));
      if (tf === 'D' || tf === 'W') {
        try {
          const live = await getJson(`/api/chart/live?${new URLSearchParams({ exchange, symbol, interval: tf })}`);
          const c = live?.candle;
          if (c && c.is_forming === true && c.interval === tf && c.symbol === symbol) {
            const forming = parseCandles([c])[0];
            if (forming && forming.time > (candles.at(-1)?.time ?? 0)) candles = [...candles, { ...forming, closed: false }];
          }
        } catch (_) { /* forming bar optional: the current open may come from chart candles */ }
      }
      return candles;
    }
    function ensure(exchange, symbol, tfs) {
      // Expired entries would be refetched anyway; dropping them keeps the cache to the
      // markets of the last ttlMs instead of every market this pane ever showed.
      for (const [key, hit] of cache) if (!hit.pending && now() - hit.at >= ttlMs) cache.delete(key);
      return Promise.all(tfs.map(tf => {
        const key = `${exchange}:${symbol}:${tf}`, hit = cache.get(key);
        if (hit && (hit.pending || now() - hit.at < ttlMs)) return (hit.pending || Promise.resolve(hit)).then(h => [tf, h]);
        const pending = loadOne(exchange, symbol, tf).then(candles => ({ candles, at: now() }), error => ({ candles: [], error: error.message, at: now() }))
          .then(entry => { cache.set(key, entry); return entry; });
        cache.set(key, { pending });
        return pending.then(h => [tf, h]);
      })).then(rows => {
        const out = { errors: [] };
        rows.forEach(([tf, h]) => { out[tf] = h.candles; if (h.error) out.errors.push(`${tf}: ${h.error}`); });
        return out;
      });
    }
    return { ensure, cache };
  }

  return { TIMEFRAMES, NOMINAL, NAMES, SLOT_DEFAULTS, PREV, OFFSET_PADDING, HEAD_PADDING, periodStart, nextPeriodStart, prevPeriodStart,
    isVisible, parseCandles, collectOpens, atr, settingsDefaults, settingsSchema, computeModel, neededTimeframes, draw, createLoader };
}));
