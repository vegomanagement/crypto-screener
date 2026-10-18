/* Drawing tools — Fibonacci: retracement, trend-based extension, time zones, channel.
 * Level maths are exported for tests. Own implementation.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./core.js'));
  else root.WorkspaceDrawingsFib = factory(root.WorkspaceDrawingsCore);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (C) {
  'use strict';
  const seg = (a, b, extra = {}) => ({ t: 'seg', a, b, ...extra });
  const text = (x, y, value, extra = {}) => ({ t: 'text', x, y, text: value, size: 10, ...extra });
  const PALETTE = ['#8b93a7', '#e5534b', '#e0913a', '#c9b33b', '#4fae6a', '#36a3a8', '#8b93a7', '#5b8def', '#9d6ad6', '#d65a9a', '#7b6fd0'];
  const lv = (values, off = []) => values.map((v, i) => ({ v, on: !off.includes(v), color: PALETTE[i % PALETTE.length] }));
  const RETRACEMENT_LEVELS = lv([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618, 2.618, 3.618, 4.236], [3.618, 4.236]);
  const EXTENSION_LEVELS = lv([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.272, 1.618, 2, 2.618]);
  const CHANNEL_LEVELS = lv([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.618, 2.618], [2.618]);
  const TIME_ZONES = [0, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233];

  // Retracement from A (start) to B (end): level 0 sits on B, level 1 on A. Reverse flips it.
  const retracementPrice = (a, b, v, reverse = false) => (reverse ? a + (b - a) * v : b + (a - b) * v);
  // Trend-based extension: the A→B move projected from C.
  const extensionPrice = (a, b, c, v) => c + (b - a) * v;
  // Time zones: logical bar offsets from A in units of the A→B bar distance.
  const timeZoneLogicals = (la, lb, zones = TIME_ZONES) => { const unit = lb - la; return unit ? zones.map(k => ({ k, logical: la + k * unit })) : []; };
  const enabledLevels = levels => (Array.isArray(levels) ? levels : []).filter(l => l && l.on !== false && Number.isFinite(Number(l.v))).map(l => ({ ...l, v: Number(l.v) }));
  function levelLabel(level, price, o, view) {
    const parts = [];
    if (o.showLevels !== false) parts.push(String(level.v));
    if (o.showPrices !== false) parts.push(`(${C.fmt(price, view.precision)})`);
    return parts.join(' ');
  }
  const LEVEL_OPTIONS = [
    { key: 'levels', label: 'Levels', type: 'levels' }, { key: 'reverse', label: 'Reverse', type: 'bool' },
    { key: 'extendLeft', label: 'Extend lines left', type: 'bool' }, { key: 'extendRight', label: 'Extend lines right', type: 'bool' },
    { key: 'showPrices', label: 'Show prices', type: 'bool' }, { key: 'showLevels', label: 'Show level values', type: 'bool' },
    { key: 'fillBackground', label: 'Fill bands', type: 'bool' }];

  // Horizontal level lines between x0..x1 with optional extension and band fills.
  function horizontalLevels(levels, priceOf, x0, x1, o, view, s) {
    const left = o.extendLeft ? -10 : Math.min(x0, x1), right = o.extendRight ? view.w + 10 : Math.max(x0, x1), prims = [], rows = [];
    for (const level of levels) {
      const price = priceOf(level.v), y = view.mapper.y(price);
      if (y === null || !(price > 0)) continue;
      rows.push({ level, price, y });
    }
    if (o.fillBackground) {
      const sorted = rows.slice().sort((p, q) => p.level.v - q.level.v);
      for (let i = 1; i < sorted.length; i += 1) {
        prims.push({ t: 'poly', pts: [{ x: left, y: sorted[i - 1].y }, { x: right, y: sorted[i - 1].y }, { x: right, y: sorted[i].y }, { x: left, y: sorted[i].y }],
          closed: true, fill: C.rgba(sorted[i].level.color || s.color, 0.07), stroke: false, hit: false });
      }
    }
    for (const r of rows) {
      const color = o.useLevelColors === false ? s.color : r.level.color || s.color;
      prims.push(seg({ x: left, y: r.y }, { x: right, y: r.y }, { color }));
      const label = levelLabel(r.level, r.price, o, view);
      if (label) prims.push(text(o.labelsRight ? right - 4 : left + 4, r.y - 7, label, { align: o.labelsRight ? 'right' : 'left', textColor: color, hit: false }));
    }
    return { prims, rows };
  }

  C.registerTool({ type: 'fib', group: 'fib', label: 'Fib retracement', icon: 'Φ', points: 2,
    defaults: { opts: { levels: RETRACEMENT_LEVELS, reverse: false, extendLeft: false, extendRight: false, showPrices: true, showLevels: true, fillBackground: true, useLevelColors: true } },
    options: [...LEVEL_OPTIONS, { key: 'useLevelColors', label: 'Per-level colours', type: 'bool' }],
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const [a, b] = pts, pa = Number(d.points[0].price), pb = Number(d.points[1].price);
      const { prims } = horizontalLevels(enabledLevels(o.levels), v => retracementPrice(pa, pb, v, o.reverse), a.x, b.x, o, view, s);
      prims.push(seg(a, b, { dash: 'dashed', alpha: 0.6 }));
      return prims;
    } });

  C.registerTool({ type: 'fibext', group: 'fib', label: 'Trend-based Fib extension', icon: 'Φ↗', points: 3,
    defaults: { opts: { levels: EXTENSION_LEVELS, extendLeft: false, extendRight: false, showPrices: true, showLevels: true, fillBackground: true, useLevelColors: true } },
    options: LEVEL_OPTIONS.filter(x => x.key !== 'reverse'),
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const [a, b, c] = pts, prims = [seg(a, b, { dash: 'dashed', alpha: 0.6 })];
      if (!c) return prims;
      prims.push(seg(b, c, { dash: 'dashed', alpha: 0.6 }));
      const pa = Number(d.points[0].price), pb = Number(d.points[1].price), pc = Number(d.points[2].price);
      const x1 = c.x + Math.max(40, Math.abs(b.x - a.x));
      return prims.concat(horizontalLevels(enabledLevels(o.levels), v => extensionPrice(pa, pb, pc, v), c.x, x1, o, view, s).prims);
    } });

  C.registerTool({ type: 'fibtime', group: 'fib', label: 'Fib time zone', icon: 'Φ│', points: 2,
    defaults: { opts: { showLabels: true } }, options: [{ key: 'showLabels', label: 'Show labels', type: 'bool' }],
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const m = view.mapper, la = m.logicalForTime(d.points[0].time), lb = m.logicalForTime(d.points[1].time), prims = [];
      for (const z of timeZoneLogicals(la, lb)) {
        const x = m.xForLogical(z.logical);
        if (x < -50 || x > view.w + 50) continue;
        prims.push(seg({ x, y: -10 }, { x, y: view.h + 10 }, { color: PALETTE[TIME_ZONES.indexOf(z.k) % PALETTE.length] || s.color }));
        if (o.showLabels) prims.push(text(x + 3, 12, String(z.k), { textColor: s.color, hit: false }));
      }
      prims.push(seg(pts[0], pts[1], { dash: 'dotted', alpha: 0.6 }));
      return prims;
    } });

  C.registerTool({ type: 'fibchannel', group: 'fib', label: 'Fib channel', icon: 'Φ▱', points: 3,
    defaults: { opts: { levels: CHANNEL_LEVELS, extendLeft: false, extendRight: false, showLevels: true, fillBackground: true, useLevelColors: true } },
    options: LEVEL_OPTIONS.filter(x => !['reverse', 'showPrices'].includes(x.key)),
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const [a, b, c] = pts;
      if (!c) return [seg(a, b)];
      const base = C.extendSegment(a, b, o.extendLeft, o.extendRight, view.w + view.h);
      const dy = c.y - (b.x === a.x ? a.y : a.y + (b.y - a.y) * (c.x - a.x) / (b.x - a.x));
      const levels = enabledLevels(o.levels).sort((p, q) => p.v - q.v), prims = [];
      levels.forEach((l, i) => {
        const la = { x: base.a.x, y: base.a.y + dy * l.v }, lb = { x: base.b.x, y: base.b.y + dy * l.v };
        const color = o.useLevelColors === false ? s.color : l.color || s.color;
        if (o.fillBackground && i > 0) {
          const p = levels[i - 1];
          prims.push({ t: 'poly', pts: [{ x: base.a.x, y: base.a.y + dy * p.v }, { x: base.b.x, y: base.b.y + dy * p.v }, lb, la], closed: true, fill: C.rgba(color, 0.07), stroke: false, hit: false });
        }
        prims.push(seg(la, lb, { color }));
        if (o.showLevels !== false) { const s0 = a.x <= b.x ? a : b; prims.push(text(s0.x + 4, s0.y + dy * l.v - 7, String(l.v), { textColor: color, hit: false })); }
      });
      return prims;
    } });

  return { PALETTE, RETRACEMENT_LEVELS, EXTENSION_LEVELS, CHANNEL_LEVELS, TIME_ZONES, retracementPrice, extensionPrice, timeZoneLogicals, enabledLevels, horizontalLevels };
});
