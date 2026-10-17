/* Drawing tools — Lines and Channels. Each tool builds pixel-space primitives from its points;
 * builds must tolerate partial point lists (creation preview). Own implementation.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./core.js'));
  else root.WorkspaceDrawingsLines = factory(root.WorkspaceDrawingsCore);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (C) {
  'use strict';
  const seg = (a, b, extra = {}) => ({ t: 'seg', a, b, ...extra });
  const text = (x, y, value, extra = {}) => ({ t: 'text', x, y, text: value, size: 11, ...extra });
  const fillOf = s => C.rgba(s.fill || s.color, s.fillAlpha);

  // Δprice, Δ%, bars, angle — shared by trend/info lines and the measure tools.
  function lineInfo(d, view, a, b, o) {
    const pa = Number(d.points[0].price), pb = Number(d.points[1].price), parts = [];
    const bars = Math.round(view.mapper.logicalForTime(d.points[1].time) - view.mapper.logicalForTime(d.points[0].time));
    if (o.showPriceRange) parts.push(C.signed(pb - pa, view.precision));
    if (o.showPercent) parts.push(`${C.signed(pa ? (pb - pa) / pa * 100 : NaN, 2)}%`);
    if (o.showBars) parts.push(`${bars} bars · ${C.formatDuration(d.points[1].time - d.points[0].time)}`);
    if (o.showAngle) parts.push(`${C.fmt(angleDeg(a, b), 1)}°`);
    return parts.join('  ');
  }
  const angleDeg = (a, b) => -Math.atan2(b.y - a.y, b.x - a.x) * 180 / Math.PI;

  function trendBuild(d, view, pts, o, s) {
    if (pts.length < 2) return [];
    const [a, b] = pts, line = C.extendSegment(a, b, o.extendLeft, o.extendRight, view.w + view.h);
    const prims = [seg(line.a, line.b)];
    const info = lineInfo(d, view, a, b, o);
    if (info) prims.push(text(b.x + 6, b.y + (b.y >= a.y ? 14 : -14), info, { bg: 'rgba(16,20,28,0.85)', textColor: s.color, border: s.color }));
    if (d.text) prims.push(text((a.x + b.x) / 2, (a.y + b.y) / 2 - 10, d.text, { align: 'center', size: s.fontSize, textColor: s.textColor || s.color }));
    return prims;
  }
  const INFO_OPTIONS = [
    { key: 'extendLeft', label: 'Extend left', type: 'bool' }, { key: 'extendRight', label: 'Extend right', type: 'bool' },
    { key: 'showPriceRange', label: 'Show price range', type: 'bool' }, { key: 'showPercent', label: 'Show percent change', type: 'bool' },
    { key: 'showBars', label: 'Show bars / duration', type: 'bool' }, { key: 'showAngle', label: 'Show angle', type: 'bool' }];
  const NO_INFO = { extendLeft: false, extendRight: false, showPriceRange: false, showPercent: false, showBars: false, showAngle: false };

  C.registerTool({ type: 'trend', group: 'lines', label: 'Trend line', icon: '╱', points: 2, defaults: { opts: NO_INFO }, options: INFO_OPTIONS, build: trendBuild });
  C.registerTool({ type: 'ray', group: 'lines', label: 'Ray', icon: '↗', points: 2, defaults: { opts: { ...NO_INFO, extendRight: true } }, options: INFO_OPTIONS, build: trendBuild });
  C.registerTool({ type: 'info', group: 'lines', label: 'Info line', icon: 'ⓘ', points: 2,
    defaults: { opts: { ...NO_INFO, showPriceRange: true, showPercent: true, showBars: true, showAngle: true } }, options: INFO_OPTIONS, build: trendBuild });
  C.registerTool({ type: 'extended', group: 'lines', label: 'Extended line', icon: '⟷', points: 2,
    defaults: { opts: { ...NO_INFO, extendLeft: true, extendRight: true } }, options: INFO_OPTIONS, build: trendBuild });
  C.registerTool({ type: 'angle', group: 'lines', label: 'Trend angle', icon: '∠', points: 2, defaults: { opts: { ...NO_INFO, showAngle: true } }, options: INFO_OPTIONS,
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const [a, b] = pts, deg = angleDeg(a, b), r = Math.min(40, Math.max(18, Math.hypot(b.x - a.x, b.y - a.y) / 3));
      const prims = trendBuild(d, view, pts, { ...o, showAngle: false }, s);
      prims.push(seg(a, { x: a.x + r * 1.6, y: a.y }, { dash: 'dotted', hit: false }));
      const end = -deg * Math.PI / 180;
      prims.push({ t: 'poly', pts: C.ellipsePoints(a.x, a.y, r, r, 0, 24, Math.min(0, end), Math.max(0, end)), hit: false });
      prims.push(text(a.x + r + 6, a.y + (deg >= 0 ? -10 : 10), `${C.fmt(deg, 1)}°`, { textColor: s.color }));
      return prims;
    } });

  // Horizontal / vertical tools: handles sit mid-plot for full-width lines.
  function priceTag(d, view, y, s) {
    return text(view.w - 4, y, `${d.virtual && d.kind === 'signal' ? '⚑ ' : ''}${d.points[0].price}`, { align: 'right', bg: s.color, textColor: '#0b0f16', hit: false });
  }
  C.registerTool({ type: 'hline', group: 'lines', label: 'Horizontal line', icon: '─', points: 1, defaults: { opts: { showPrice: false } },
    options: [{ key: 'showPrice', label: 'Show price label', type: 'bool' }],
    build(d, view, [a], o, s) {
      const prims = [seg({ x: -10, y: a.y }, { x: view.w + 10, y: a.y })];
      if (o.showPrice || d.level) prims.push(priceTag(d, view, a.y, s));
      if (d.text) prims.push(text(8, a.y - 10, d.text, { size: s.fontSize, textColor: s.textColor || s.color }));
      return prims;
    },
    handles: (d, view) => { const y = view.mapper.y(d.points[0].price); return y === null ? [] : [{ x: Math.max(20, view.w / 2), y, part: 'p0' }]; },
    dragHandle: (d, i, p) => [{ time: d.points[0].time, price: p.price }] });
  C.registerTool({ type: 'hray', group: 'lines', label: 'Horizontal ray', icon: '⟶', points: 1, defaults: { opts: { showPrice: false } },
    options: [{ key: 'showPrice', label: 'Show price label', type: 'bool' }],
    build(d, view, [a], o, s) {
      const prims = [seg(a, { x: Math.max(a.x, view.w) + 10, y: a.y })];
      if (o.showPrice || d.level) prims.push(priceTag(d, view, a.y, s));
      if (d.text) prims.push(text(a.x + 6, a.y - 10, d.text, { size: s.fontSize, textColor: s.textColor || s.color }));
      return prims;
    } });
  C.registerTool({ type: 'vline', group: 'lines', label: 'Vertical line', icon: '│', points: 1, defaults: { opts: { showTime: true } },
    options: [{ key: 'showTime', label: 'Show time label', type: 'bool' }],
    build(d, view, [a], o, s) {
      const prims = [seg({ x: a.x, y: -10 }, { x: a.x, y: view.h + 10 })];
      if (o.showTime) prims.push(text(a.x, view.h - 10, new Date(d.points[0].time).toISOString().slice(5, 16).replace('T', ' '), { align: 'center', bg: s.color, textColor: '#0b0f16', hit: false }));
      if (d.text) prims.push(text(a.x + 6, 16, d.text, { size: s.fontSize, textColor: s.textColor || s.color }));
      return prims;
    },
    handles: (d, view) => [{ x: view.mapper.x(d.points[0].time), y: view.h / 2, part: 'p0' }],
    dragHandle: (d, i, p) => [{ time: p.time, price: d.points[0].price }] });
  C.registerTool({ type: 'cross', group: 'lines', label: 'Cross line', icon: '┼', points: 1, defaults: { opts: { showPrice: true } },
    options: [{ key: 'showPrice', label: 'Show price label', type: 'bool' }],
    build(d, view, [a], o, s) {
      const prims = [seg({ x: -10, y: a.y }, { x: view.w + 10, y: a.y }), seg({ x: a.x, y: -10 }, { x: a.x, y: view.h + 10 })];
      if (o.showPrice) prims.push(priceTag(d, view, a.y, s));
      return prims;
    } });

  // ── channels ──
  // Offset (px along y) of point c from the line a→b, measured at c's x.
  function lineYAt(a, b, x) { return b.x === a.x ? a.y : a.y + (b.y - a.y) * (x - a.x) / (b.x - a.x); }
  function band(a, b, dy, o, view) {
    const l1 = C.extendSegment(a, b, o.extendLeft, o.extendRight, view.w + view.h);
    return [l1, { a: { x: l1.a.x, y: l1.a.y + dy }, b: { x: l1.b.x, y: l1.b.y + dy } }];
  }
  const CHANNEL_OPTIONS = [{ key: 'midline', label: 'Middle line', type: 'bool' }, { key: 'extendLeft', label: 'Extend left', type: 'bool' },
    { key: 'extendRight', label: 'Extend right', type: 'bool' }, { key: 'fillBackground', label: 'Fill background', type: 'bool' }];
  C.registerTool({ type: 'channel', group: 'channels', label: 'Parallel channel', icon: '▱', points: 3,
    defaults: { opts: { midline: true, extendLeft: false, extendRight: false, fillBackground: true }, style: { fillAlpha: 0.1 } }, options: CHANNEL_OPTIONS,
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const [a, b, c] = pts;
      if (!c) return [seg(a, b)];
      const dy = c.y - lineYAt(a, b, c.x), [l1, l2] = band(a, b, dy, o, view);
      const prims = [];
      if (o.fillBackground) prims.push({ t: 'poly', pts: [l1.a, l1.b, l2.b, l2.a], closed: true, fill: fillOf(s), stroke: false, hitFill: true });
      prims.push(seg(l1.a, l1.b), seg(l2.a, l2.b));
      if (o.midline) prims.push(seg({ x: l1.a.x, y: l1.a.y + dy / 2 }, { x: l1.b.x, y: l1.b.y + dy / 2 }, { dash: 'dashed' }));
      return prims;
    } });

  // Linear regression of the chosen source over the real candles between the anchors.
  const SOURCE = { close: c => c.close, hl2: c => (c.high + c.low) / 2, hlc3: c => (c.high + c.low + c.close) / 3, ohlc4: c => (c.open + c.high + c.low + c.close) / 4 };
  function regressionTrend(candles, from, to, source = 'close') {
    if (!(to > from)) return null;
    const fn = SOURCE[source] || SOURCE.close, values = [];
    for (let i = from; i <= to; i += 1) values.push(fn(candles[i]));
    const reg = C.linearRegression(values);
    return reg && { ...reg, from, to, start: reg.at(0), end: reg.at(to - from) };
  }
  C.registerTool({ type: 'regression', group: 'channels', label: 'Regression trend', icon: '≋', points: 2,
    defaults: { opts: { devUp: 2, devDown: 2, source: 'close', extendRight: false, fillBackground: true }, style: { fillAlpha: 0.08 } },
    options: [{ key: 'devUp', label: 'Upper deviation (σ)', type: 'number', min: 0, max: 10, step: 0.1 }, { key: 'devDown', label: 'Lower deviation (σ)', type: 'number', min: 0, max: 10, step: 0.1 },
      { key: 'source', label: 'Source', type: 'select', choices: Object.keys(SOURCE) }, { key: 'extendRight', label: 'Extend right', type: 'bool' },
      { key: 'fillBackground', label: 'Fill background', type: 'bool' }],
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const range = view.mapper.indexRange(d.points[0].time, d.points[1].time);
      const reg = range && regressionTrend(view.candles, range[0], range[1], o.source);
      if (!reg) return [seg(pts[0], pts[1], { dash: 'dotted' })];
      const m = view.mapper, x0 = m.xForLogical(reg.from), x1 = m.xForLogical(reg.to);
      const line = k => { const a = { x: x0, y: m.y(reg.start + k) }, b = { x: x1, y: m.y(reg.end + k) }; return C.extendSegment(a, b, false, o.extendRight, view.w + view.h); };
      const mid = line(0), up = line(o.devUp * reg.stdDev), dn = line(-o.devDown * reg.stdDev);
      if ([mid, up, dn].some(l => l.a.y === null || l.b.y === null)) return [];
      const prims = [];
      if (o.fillBackground) prims.push({ t: 'poly', pts: [up.a, up.b, dn.b, dn.a], closed: true, fill: fillOf(s), stroke: false });
      prims.push(seg(mid.a, mid.b, { dash: 'dashed' }), seg(up.a, up.b), seg(dn.a, dn.b));
      prims.push(text(x1 + 6, mid.b.y, `σ ${C.fmt(reg.stdDev, view.precision)} · ${reg.n} bars`, { textColor: s.color, hit: false }));
      return prims;
    } });
  C.registerTool({ type: 'flattop', group: 'channels', label: 'Flat top/bottom', icon: '⊤', points: 3,
    defaults: { opts: { fillBackground: true }, style: { fillAlpha: 0.1 } }, options: [CHANNEL_OPTIONS[3]],
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const [a, b, c] = pts;
      if (!c) return [seg(a, b)];
      const fa = { x: a.x, y: c.y }, fb = { x: b.x, y: c.y }, prims = [];
      if (o.fillBackground) prims.push({ t: 'poly', pts: [a, b, fb, fa], closed: true, fill: fillOf(s), stroke: false, hitFill: true });
      prims.push(seg(a, b), seg(fa, fb));
      return prims;
    } });
  C.registerTool({ type: 'disjoint', group: 'channels', label: 'Disjoint channel', icon: '⋎', points: 3,
    defaults: { opts: { fillBackground: true }, style: { fillAlpha: 0.1 } }, options: [CHANNEL_OPTIONS[3]],
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const [a, b, c] = pts;
      if (!c) return [seg(a, b)];
      // Second line starts at (a.x, c.y) and mirrors the first line's slope.
      const ma = { x: a.x, y: c.y }, mb = { x: b.x, y: c.y - (b.y - a.y) }, prims = [];
      if (o.fillBackground) prims.push({ t: 'poly', pts: [a, b, mb, ma], closed: true, fill: fillOf(s), stroke: false, hitFill: true });
      prims.push(seg(a, b), seg(ma, mb));
      return prims;
    } });

  return { lineInfo, angleDeg, lineYAt, regressionTrend, SOURCE };
});
