/* Grouped overlay indicators for /ui: EMA ribbon, MA channel, Turtle Zone.
 * One toggle + one settings popover per indicator (TradingView style).
 * Pure functions only (no DOM, no network); the pane renders the returned
 * models. Turtle Zone math is delegated to static/indicator-extras.js
 * (MPL-2.0 port, see NOTICE); this file is project code.
 */
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory(require('../indicator-extras.js'));
  else root.WorkspaceIndicatorGroups = factory(root.WorkspaceIndicatorExtras);
}(typeof globalThis !== 'undefined' ? globalThis : this, function (extras) {
  'use strict';
  const SOURCES = ['open', 'high', 'low', 'close'];
  const RIBBON_DEFAULTS = [[7, '#d4ac5c'], [14, '#b593d2'], [21, '#6b98cf']];
  const OLD_EMA_DEFAULTS = { ema7: 7, ema14: 14, ema28: 28 };

  // SMA-seeded EMA, identical contract to workspace.js emaData: nothing is
  // plotted before `period` closed candles exist (no invented warm-up).
  function ema(candles, period) {
    if (!Number.isInteger(period) || period < 1 || !Array.isArray(candles) || candles.length < period) return [];
    let value = 0;
    for (let i = 0; i < period; i += 1) value += candles[i].close;
    value /= period;
    const out = [{ time: candles[period - 1].time, value }];
    const alpha = 2 / (period + 1);
    for (let i = period; i < candles.length; i += 1) {
      value = alpha * candles[i].close + (1 - alpha) * value;
      out.push({ time: candles[i].time, value });
    }
    return out;
  }
  function sma(candles, period, source) {
    const key = SOURCES.includes(source) ? source : 'close';
    if (!Number.isInteger(period) || period < 1 || !Array.isArray(candles) || candles.length < period) return [];
    let sum = 0;
    for (let i = 0; i < period; i += 1) sum += candles[i][key];
    const out = [{ time: candles[period - 1].time, value: sum / period }];
    for (let i = period; i < candles.length; i += 1) {
      sum += candles[i][key] - candles[i - period][key];
      out.push({ time: candles[i].time, value: sum / period });
    }
    return out;
  }

  function computeRibbon(candles, s) {
    return { lines: [1, 2, 3].map(n => ({ key: `r${n - 1}`, period: s[`period${n}`], color: s[`color${n}`], width: s[`width${n}`],
      visible: s[`show${n}`] !== false, data: ema(candles, s[`period${n}`]) })) };
  }
  function computeChannel(candles, s) {
    return { upper: { data: sma(candles, s.period, s.highSource), color: s.highColor, width: s.width, visible: s.showHigh !== false },
      lower: { data: sma(candles, s.period, s.lowSource), color: s.lowColor, width: s.width, visible: s.showLow !== false },
      fill: Boolean(s.fill), fillColor: s.fillColor, opacity: s.opacity };
  }
  function computeTurtle(candles, s) {
    const out = extras.computeTurtleZone(candles, { length: s.length, inner: s.inner, outer: s.outer, source: s.source });
    // Upper zone: outer line above, inner line below; lower zone mirrored.
    return { zones: [
      { side: 'upper', visible: s.showUpper !== false, color: s.upperColor, inner: out.upper.lower, outer: out.upper.upper },
      { side: 'lower', visible: s.showLower !== false, color: s.lowerColor, inner: out.lower.upper, outer: out.lower.lower }
    ], fill: Boolean(s.fill), opacity: s.opacity, showOuter: Boolean(s.showOuter), width: s.width };
  }
  function turtleWarnings(candles, s) {
    try {
      computeTurtle(candles, s);
      const need = Math.max(200, s.length);
      return candles.length < need ? [`Insufficient history: Turtle Zone requires ${need} closed bars; received ${candles.length}`] : [];
    } catch (error) { return [error.message]; }
  }

  const ribbonSchema = RIBBON_DEFAULTS.flatMap(([period, color], i) => {
    const n = i + 1, group = `Line ${n}`;
    return [
      { key: `show${n}`, label: 'Visible', type: 'boolean', default: true, group },
      { key: `period${n}`, label: 'Period', type: 'number', min: 2, max: 500, step: 1, default: period, group },
      { key: `color${n}`, label: 'Color', type: 'color', default: color, group },
      { key: `width${n}`, label: 'Width', type: 'number', min: 1, max: 4, step: 1, default: 1, group }];
  });
  const definitions = [
    { id: 'ema-ribbon', label: 'EMA ribbon', kind: 'ribbon', defaultEnabled: true, settingsSchema: ribbonSchema,
      labelForSettings: s => `EMA ${[1, 2, 3].map(n => s[`period${n}`]).join('/')}`, compute: computeRibbon },
    { id: 'ma-channel', label: 'MA channel', kind: 'channel', defaultEnabled: false,
      settingsSchema: [
        { key: 'period', label: 'Period', type: 'number', min: 2, max: 500, step: 1, default: 20 },
        { key: 'width', label: 'Line width', type: 'number', min: 1, max: 4, step: 1, default: 1 },
        { key: 'showHigh', label: 'Visible', type: 'boolean', default: true, group: 'Upper line' },
        { key: 'highSource', label: 'Source', type: 'select', options: SOURCES.slice(), default: 'high', group: 'Upper line' },
        { key: 'highColor', label: 'Color', type: 'color', default: '#f0b429', group: 'Upper line' },
        { key: 'showLow', label: 'Visible', type: 'boolean', default: true, group: 'Lower line' },
        { key: 'lowSource', label: 'Source', type: 'select', options: SOURCES.slice(), default: 'low', group: 'Lower line' },
        { key: 'lowColor', label: 'Color', type: 'color', default: '#26c6da', group: 'Lower line' },
        { key: 'fill', label: 'Fill between lines', type: 'boolean', default: false, group: 'Fill' },
        { key: 'fillColor', label: 'Fill color', type: 'color', default: '#8fa3bf', group: 'Fill' },
        { key: 'opacity', label: 'Fill opacity', type: 'number', min: 0, max: 1, step: 0.01, default: 0.08, group: 'Fill' }],
      labelForSettings: s => `MA ${s.period} H/L`, compute: computeChannel },
    { id: 'turtle-zone', label: 'Turtle Zone', kind: 'turtle', defaultEnabled: false,
      settingsSchema: [
        { key: 'length', label: 'Length (min 200 bars warm-up)', type: 'number', min: 1, max: 1000, step: 1, default: 200 },
        { key: 'inner', label: 'Inner amplitude', type: 'number', min: 0, max: 100, step: 0.1, default: 5.6 },
        { key: 'outer', label: 'Outer amplitude (>= inner)', type: 'number', min: 0, max: 100, step: 0.1, default: 9.6 },
        { key: 'source', label: 'Price source', type: 'select', options: ['hlc3', 'open', 'high', 'low', 'close', 'hl2', 'ohlc4'], default: 'hlc3' },
        { key: 'width', label: 'Line width', type: 'number', min: 1, max: 4, step: 1, default: 1 },
        { key: 'showUpper', label: 'Upper zone', type: 'boolean', default: true, group: 'Lines' },
        { key: 'upperColor', label: 'Upper color', type: 'color', default: '#ef4444', group: 'Lines' },
        { key: 'showLower', label: 'Lower zone', type: 'boolean', default: true, group: 'Lines' },
        { key: 'lowerColor', label: 'Lower color', type: 'color', default: '#22c55e', group: 'Lines' },
        { key: 'showOuter', label: 'Show outer boundary', type: 'boolean', default: false, group: 'Lines' },
        { key: 'fill', label: 'Fill inner→outer', type: 'boolean', default: true, group: 'Fill' },
        { key: 'opacity', label: 'Fill opacity', type: 'number', min: 0, max: 1, step: 0.01, default: 0.1, group: 'Fill' }],
      labelForSettings: s => `Turtle Zone ${s.length}`, compute: computeTurtle, warnings: turtleWarnings }
  ];

  // ── Migration of saved v1–v4 workspace state to grouped ids ──
  const isObj = v => v && typeof v === 'object' && !Array.isArray(v);
  const bool = v => typeof v === 'boolean' ? v : undefined;
  function migrateLegacyState(saved) {
    if (!isObj(saved)) return { saved, notes: [] };
    const next = { ...saved };
    const enabled = { ...(isObj(saved.indicatorEnabled) ? saved.indicatorEnabled : {}) };
    const settings = { ...(isObj(saved.indicatorSettings) ? saved.indicatorSettings : {}) };
    const notes = [];
    const ema = isObj(saved.ema) ? saved.ema : {};
    const old = id => isObj(settings[id]) ? settings[id] : {};
    // EMA 7/14/28 → one EMA ribbon. A non-default EMA 28 period is kept; the
    // old default 28 becomes the new default 21.
    const ribbonFlags = ['7', '14', '28'].map(p => bool(ema[p]));
    if (ribbonFlags.some(v => v !== undefined) || ['ema7', 'ema14', 'ema28'].some(id => isObj(settings[id]))) {
      if (!isObj(settings['ema-ribbon'])) {
        const ribbon = {};
        ['ema7', 'ema14', 'ema28'].forEach((id, i) => {
          const n = i + 1, s = old(id);
          if (Number.isInteger(s.period) && s.period !== OLD_EMA_DEFAULTS[id]) { ribbon[`period${n}`] = s.period; notes.push(`EMA ribbon line ${n}: kept custom period ${s.period}`); }
          if (typeof s.color === 'string') ribbon[`color${n}`] = s.color;
          if (ribbonFlags[i] !== undefined) ribbon[`show${n}`] = ribbonFlags[i];
        });
        // Ribbon enabled but every old line off would draw nothing; show all.
        if (ribbonFlags.every(v => v === false)) [1, 2, 3].forEach(n => { delete ribbon[`show${n}`]; });
        settings['ema-ribbon'] = ribbon;
      }
      if (typeof enabled['ema-ribbon'] !== 'boolean' && ribbonFlags.some(v => v !== undefined)) {
        enabled['ema-ribbon'] = ribbonFlags.some(Boolean);
        if (ema['28'] === true) notes.push('EMA 28 was enabled → EMA ribbon enabled');
      }
    }
    if (typeof bool(ema['200']) === 'boolean' && typeof enabled.ema200 !== 'boolean') enabled.ema200 = ema['200'];
    // MA20 High + MA20 Low → MA channel.
    const ma = isObj(saved.ma20hl) ? saved.ma20hl : {};
    if (bool(ma.high) !== undefined || bool(ma.low) !== undefined || isObj(settings.ma20high) || isObj(settings.ma20low)) {
      if (!isObj(settings['ma-channel'])) {
        const hi = old('ma20high'), lo = old('ma20low'), channel = {};
        if (Number.isInteger(hi.period)) channel.period = hi.period; else if (Number.isInteger(lo.period)) channel.period = lo.period;
        if (typeof hi.source === 'string') channel.highSource = hi.source;
        if (typeof lo.source === 'string') channel.lowSource = lo.source;
        if (typeof hi.color === 'string') channel.highColor = hi.color;
        if (typeof lo.color === 'string') channel.lowColor = lo.color;
        if (ma.high === true || ma.low === true) { channel.showHigh = ma.high === true; channel.showLow = ma.low === true; }
        settings['ma-channel'] = channel;
      }
      if (typeof enabled['ma-channel'] !== 'boolean') enabled['ma-channel'] = ma.high === true || ma.low === true;
    }
    // Turtle Zone upper + lower → one Turtle Zone.
    const tu = enabled['turtle-upper'], tl = enabled['turtle-lower'];
    if (typeof tu === 'boolean' || typeof tl === 'boolean' || isObj(settings['turtle-upper']) || isObj(settings['turtle-lower'])) {
      if (!isObj(settings['turtle-zone'])) {
        const up = old('turtle-upper'), down = old('turtle-lower'), base = isObj(settings['turtle-upper']) ? up : down, zone = {};
        ['length', 'inner', 'outer', 'source', 'showOuter'].forEach(key => { if (base[key] !== undefined) zone[key] = base[key]; });
        if (base.opacity !== undefined) zone.opacity = base.opacity;
        if (typeof up.color === 'string') zone.upperColor = up.color;
        if (typeof down.color === 'string') zone.lowerColor = down.color;
        if (tu === true || tl === true) { zone.showUpper = tu === true; zone.showLower = tl === true; }
        settings['turtle-zone'] = zone;
      }
      if (typeof enabled['turtle-zone'] !== 'boolean') enabled['turtle-zone'] = tu === true || tl === true;
    }
    // Previous day/week high/low → sub-levels of the Opens / Key levels indicator.
    const prev = { 'prev-day-high': 'pdh', 'prev-day-low': 'pdl', 'prev-week-high': 'pwh', 'prev-week-low': 'pwl' };
    if (Object.keys(prev).some(id => typeof enabled[id] === 'boolean')) {
      const opens = isObj(settings.opens) ? { ...settings.opens } : {};
      Object.entries(prev).forEach(([id, key]) => { if (typeof enabled[id] === 'boolean' && opens[`${key}Show`] === undefined) opens[`${key}Show`] = enabled[id]; });
      settings.opens = opens;
      if (typeof enabled.opens !== 'boolean') {
        enabled.opens = Object.keys(prev).some(id => enabled[id] === true);
        if (enabled.opens) notes.push('Previous day/week high/low → Key levels / Opens enabled (period opens use the script defaults D/W/M/Y)');
      }
    }
    // Asia/London/New York shading → Market Sessions (fixed GMT windows of the ported script).
    const sess = { 'session-asia': 3, 'session-london': 1, 'session-ny': 2 };
    if (Object.keys(sess).some(id => typeof enabled[id] === 'boolean')) {
      const sessions = isObj(settings.sessions) ? { ...settings.sessions } : {};
      Object.entries(sess).forEach(([id, n]) => { if (typeof enabled[id] === 'boolean' && sessions[`s${n}Show`] === undefined) sessions[`s${n}Show`] = enabled[id]; });
      settings.sessions = sessions;
      if (typeof enabled.sessions !== 'boolean') {
        enabled.sessions = Object.keys(sess).some(id => enabled[id] === true);
        if (enabled.sessions) notes.push('Asia/London/New York sessions → Market Sessions enabled (windows reset to the script defaults in GMT+0)');
      }
    }
    ['ema7', 'ema14', 'ema28', 'ma20high', 'ma20low', 'turtle-upper', 'turtle-lower', ...Object.keys(prev), ...Object.keys(sess)].forEach(id => { delete enabled[id]; delete settings[id]; });
    delete next.ema; delete next.ma20hl;
    next.indicatorEnabled = enabled; next.indicatorSettings = settings;
    return { saved: next, notes };
  }

  // Canvas helpers shared by the grouped renderers. `points` are {x, y} in CSS px.
  function strokePolyline(context, points, color, width, dash) {
    if (points.length < 2) return;
    context.save();
    context.strokeStyle = color; context.lineWidth = width || 1; context.setLineDash?.(dash || []);
    context.beginPath(); context.moveTo(points[0].x, points[0].y);
    for (let i = 1; i < points.length; i += 1) context.lineTo(points[i].x, points[i].y);
    context.stroke(); context.restore();
  }
  // Fill between two curves over their common times only (no extrapolation).
  function fillBetween(context, upper, lower, fillStyle) {
    const byTime = new Map(lower.map(p => [p.time, p]));
    const pairs = upper.filter(p => byTime.has(p.time)).map(p => [p, byTime.get(p.time)]);
    if (pairs.length < 2) return 0;
    context.save(); context.fillStyle = fillStyle; context.beginPath();
    context.moveTo(pairs[0][0].x, pairs[0][0].y);
    for (let i = 1; i < pairs.length; i += 1) context.lineTo(pairs[i][0].x, pairs[i][0].y);
    for (let i = pairs.length - 1; i >= 0; i -= 1) context.lineTo(pairs[i][1].x, pairs[i][1].y);
    context.closePath(); context.fill(); context.restore();
    return pairs.length;
  }
  function rgba(hex, alpha) {
    const m = /^#([0-9a-f]{6})$/i.exec(hex || '');
    if (!m) return hex || `rgba(130,170,255,${alpha})`;
    const n = parseInt(m[1], 16);
    return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${Math.max(0, Math.min(1, alpha))})`;
  }

  return { definitions, ema, sma, computeRibbon, computeChannel, computeTurtle, migrateLegacyState, strokePolyline, fillBetween, rgba };
}));
