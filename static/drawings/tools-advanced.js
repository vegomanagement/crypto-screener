/* Drawing tools — Tier 2: pitchforks (+Schiff, modified Schiff, inside), pitchfan, Gann fan/box/square,
 * Fib speed fan / arcs / circles / spiral / wedge, trend-based Fib time, cyclic lines, time cycles,
 * sine line, projection, bars pattern, ghost feed, signpost and table. Own implementation.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./core.js'), require('./tools-fib.js'));
  else root.WorkspaceDrawingsAdvanced = factory(root.WorkspaceDrawingsCore, root.WorkspaceDrawingsFib);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (C, F) {
  'use strict';
  const seg = (a, b, extra = {}) => ({ t: 'seg', a, b, ...extra });
  const text = (x, y, value, extra = {}) => ({ t: 'text', x, y, text: value, size: 10, hit: false, ...extra });
  const ray = (a, b, view, extra) => seg(a, C.extendSegment(a, b, false, true, view.w + view.h).b, extra);
  const mid = (p, q) => ({ x: (p.x + q.x) / 2, y: (p.y + q.y) / 2 });
  const color = i => F.PALETTE[i % F.PALETTE.length];
  const PITCH_LEVELS = [0.25, 0.5, 0.75, 1, 1.5, 2];

  // ── pitchforks: median from the (variant) origin through the B–C midpoint; parallels through B and C ──
  function pitchOrigin(variant, a, b) {
    if (variant === 'schiff') return { x: a.x, y: (a.y + b.y) / 2 };
    if (variant === 'modschiff') return mid(a, b);
    return a;
  }
  function pitchfork(variant) {
    return (d, view, pts, o, s) => {
      if (pts.length < 2) return [];
      const [a, b, c] = pts;
      if (!c) return [seg(a, b)];
      const origin = pitchOrigin(variant, a, b), m = mid(b, c), dir = { x: m.x - origin.x, y: m.y - origin.y };
      const far = p => ({ x: p.x + dir.x * 50, y: p.y + dir.y * 50 }), prims = [seg(b, c, { dash: 'dashed', alpha: 0.6 })];
      if (origin !== a) prims.push(seg(a, origin, { dash: 'dotted', alpha: 0.6 }));
      const halfB = { x: b.x - m.x, y: b.y - m.y };
      const levels = variant === 'inside' ? [0.25, 0.5, 0.75, 1] : (o.levels ? PITCH_LEVELS.filter(k => k <= (o.extraLevels ? 2 : 1)) : [1]);
      const lines = [{ k: 0, p: m }];
      levels.forEach(k => { lines.push({ k, p: { x: m.x + halfB.x * k, y: m.y + halfB.y * k } }, { k: -k, p: { x: m.x - halfB.x * k, y: m.y - halfB.y * k } }); });
      lines.sort((p, q) => p.k - q.k);
      if (o.fillBackground) for (let i = 1; i < lines.length; i += 1) prims.push({ t: 'poly', pts: [lines[i - 1].p, far(lines[i - 1].p), far(lines[i].p), lines[i].p], closed: true, fill: C.rgba(color(Math.abs(Math.round(lines[i].k * 4))), 0.06), stroke: false, hit: false });
      prims.push(seg(origin, far(m)));
      lines.filter(l => l.k !== 0).forEach(l => prims.push(seg(l.p, far(l.p), { color: Math.abs(l.k) === 1 ? s.color : color(Math.round(Math.abs(l.k) * 4)) })));
      return prims;
    };
  }
  const PF_OPTIONS = [{ key: 'fillBackground', label: 'Fill background', type: 'bool' }, { key: 'levels', label: 'Extra quarter lines', type: 'bool' }, { key: 'extraLevels', label: 'Warning lines (1.5, 2)', type: 'bool' }];
  [['pitchfork', 'Pitchfork', 'standard', '⋔'], ['schiff', 'Schiff pitchfork', 'schiff', '⋔ˢ'], ['modschiff', 'Modified Schiff pitchfork', 'modschiff', '⋔ᵐ'], ['insidepitchfork', 'Inside pitchfork', 'inside', '⋔ⁱ']]
    .forEach(([type, label, variant, icon]) => C.registerTool({ type, group: 'channels', label, icon, points: 3, defaults: { opts: { fillBackground: true, levels: false, extraLevels: false } }, options: PF_OPTIONS, build: pitchfork(variant) }));
  C.registerTool({ type: 'pitchfan', group: 'channels', label: 'Pitchfan', icon: '⋎', points: 3, build(d, view, pts, o, s) {
    if (pts.length < 2) return [];
    const [a, b, c] = pts;
    if (!c) return [seg(a, b)];
    const m = mid(b, c);
    return [seg(b, c, { dash: 'dashed', alpha: 0.6 }), ray(a, b, view), ray(a, m, view, { color: s.color }), ray(a, c, view), ray(a, mid(b, m), view, { alpha: 0.6 }), ray(a, mid(m, c), view, { alpha: 0.6 })];
  } });

  // ── Gann ──
  const GANN_RATIOS = [[1, 8], [1, 4], [1, 3], [1, 2], [1, 1], [2, 1], [3, 1], [4, 1], [8, 1]];
  // Ray i passes through A + (dx·t, dy·p) of the A→B box with t/p the ratio pair: 1×1 is the A→B diagonal.
  function gannFanRays(a, b) { return GANN_RATIOS.map(([t, p]) => ({ label: `${t}×${p}`, to: { x: a.x + (b.x - a.x) * Math.min(1, t / p), y: a.y + (b.y - a.y) * Math.min(1, p / t) } })); }
  C.registerTool({ type: 'gannfan', group: 'fib', label: 'Gann fan', icon: '⟋', points: 2, defaults: { opts: { labels: true } }, options: [{ key: 'labels', label: 'Labels', type: 'bool' }],
    build(d, view, pts, o) {
      if (pts.length < 2) return [];
      const [a, b] = pts, prims = [];
      gannFanRays(a, b).forEach((r, i) => { prims.push(ray(a, r.to, view, { color: color(i + 1) })); if (o.labels) prims.push(text(r.to.x + 3, r.to.y, r.label, { textColor: color(i + 1) })); });
      return prims;
    } });
  const GANN_BOX = [0, 0.25, 0.382, 0.5, 0.618, 0.75, 1];
  function boxGrid(a, b, levels, s, extra = []) {
    const l = Math.min(a.x, b.x), r = Math.max(a.x, b.x), t = Math.min(a.y, b.y), bt = Math.max(a.y, b.y), prims = [];
    prims.push({ t: 'poly', pts: [{ x: l, y: t }, { x: r, y: t }, { x: r, y: bt }, { x: l, y: bt }], closed: true, fill: C.rgba(s.color, 0.05), hitFill: true });
    levels.forEach((k, i) => {
      const x = a.x + (b.x - a.x) * k, y = a.y + (b.y - a.y) * k;
      prims.push(seg({ x, y: t }, { x, y: bt }, { color: color(i), alpha: 0.8 }), seg({ x: l, y }, { x: r, y }, { color: color(i), alpha: 0.8 }));
      prims.push(text(x, bt + 8, String(k), { align: 'center', textColor: color(i) }), text(l - 4, y, String(k), { align: 'right', textColor: color(i) }));
    });
    return prims.concat(extra);
  }
  C.registerTool({ type: 'gannbox', group: 'fib', label: 'Gann box', icon: '▦', points: 2, build: (d, view, pts, o, s) => (pts.length < 2 ? [] : boxGrid(pts[0], pts[1], GANN_BOX, s)) });
  C.registerTool({ type: 'gannsquare', group: 'fib', label: 'Gann square', icon: '⊠', points: 2, build(d, view, pts, o, s) {
    if (pts.length < 2) return [];
    const [a, b] = pts, side = Math.max(Math.abs(b.x - a.x), Math.abs(b.y - a.y));
    const sq = { x: a.x + Math.sign(b.x - a.x || 1) * side, y: a.y + Math.sign(b.y - a.y || 1) * side };
    const c1 = { x: sq.x, y: a.y }, c2 = { x: a.x, y: sq.y };
    const from = sq.x >= a.x ? 0 : Math.PI, to = sq.y >= a.y ? Math.PI / 2 : -Math.PI / 2;
    const arcs = [0.5, 1].map(k => ({ t: 'poly', pts: C.ellipsePoints(a.x, a.y, side * k, side * k, 0, 24, from, from === 0 ? to : (to > 0 ? Math.PI / 2 : 1.5 * Math.PI)), alpha: 0.6, hit: false }));
    return boxGrid(a, sq, [0, 0.25, 0.5, 0.75, 1], s, [seg(a, sq), seg(c1, c2), ...arcs]);
  } });

  // ── Fibonacci speed/arc family ──
  const SPEED = [0.25, 0.382, 0.5, 0.618, 0.75, 1];
  C.registerTool({ type: 'fibspeedfan', group: 'fib', label: 'Fib speed resistance fan', icon: 'Φ⟋', points: 2, build(d, view, pts) {
    if (pts.length < 2) return [];
    const [a, b] = pts, prims = boxGrid(a, b, [0, ...SPEED], { color: '#5b8def' }).filter(p => p.t !== 'text');
    SPEED.forEach((k, i) => { prims.push(ray(a, { x: b.x, y: a.y + (b.y - a.y) * k }, view, { color: color(i + 1) }), ray(a, { x: a.x + (b.x - a.x) * k, y: b.y }, view, { color: color(i + 1), dash: 'dashed' })); });
    return prims;
  } });
  const ARC_LEVELS = [0.236, 0.382, 0.5, 0.618, 0.786, 1];
  function arcs(full) {
    return (d, view, pts) => {
      if (pts.length < 2) return [];
      const [a, b] = pts, r = Math.hypot(b.x - a.x, b.y - a.y), prims = [seg(a, b, { dash: 'dashed', alpha: 0.6 })];
      const towards = b.y <= a.y ? [Math.PI, 2 * Math.PI] : [0, Math.PI];
      ARC_LEVELS.forEach((k, i) => {
        const p = full ? C.ellipsePoints(a.x, a.y, r * k, r * k, 0, 72) : C.ellipsePoints(b.x, b.y, r * k, r * k, 0, 48, towards[0] + Math.PI, towards[1] + Math.PI);
        prims.push({ t: 'poly', pts: p, color: color(i + 1), closed: full });
        prims.push(text((full ? a.x : b.x) + r * k + 3, full ? a.y : b.y, String(k), { textColor: color(i + 1) }));
      });
      return prims;
    };
  }
  C.registerTool({ type: 'fibarcs', group: 'fib', label: 'Fib speed resistance arcs', icon: 'Φ◠', points: 2, build: arcs(false) });
  C.registerTool({ type: 'fibcircles', group: 'fib', label: 'Fib circles', icon: 'Φ◯', points: 2, build: arcs(true) });
  // Golden spiral: radius grows by φ every quarter turn, starting at |AB| in the direction of B.
  const PHI = (1 + Math.sqrt(5)) / 2;
  function spiralPoints(a, b, turns = 3, steps = 240) {
    const r0 = Math.hypot(b.x - a.x, b.y - a.y), t0 = Math.atan2(b.y - a.y, b.x - a.x), k = Math.log(PHI) / (Math.PI / 2), out = [];
    for (let i = 0; i <= steps; i += 1) { const th = -turns * 2 * Math.PI * (1 - i / steps), r = r0 * Math.exp(k * th); out.push({ x: a.x + r * Math.cos(t0 + th), y: a.y + r * Math.sin(t0 + th) }); }
    for (let i = 1; i <= 60; i += 1) { const th = (i / 60) * Math.PI, r = r0 * Math.exp(k * th); out.push({ x: a.x + r * Math.cos(t0 + th), y: a.y + r * Math.sin(t0 + th) }); }
    return out;
  }
  C.registerTool({ type: 'fibspiral', group: 'fib', label: 'Fib spiral', icon: 'Φ@', points: 2, build: (d, view, pts) => (pts.length < 2 ? [] : [seg(pts[0], pts[1], { dash: 'dashed', alpha: 0.6 }), { t: 'poly', pts: spiralPoints(pts[0], pts[1]) }]) });
  C.registerTool({ type: 'fibwedge', group: 'fib', label: 'Fib wedge', icon: 'Φ◁', points: 3, build(d, view, pts) {
    if (pts.length < 2) return [];
    const [a, b, c] = pts;
    if (!c) return [seg(a, b)];
    const r = Math.hypot(b.x - a.x, b.y - a.y), t1 = Math.atan2(b.y - a.y, b.x - a.x);
    let t2 = Math.atan2(c.y - a.y, c.x - a.x);
    if (t2 - t1 > Math.PI) t2 -= 2 * Math.PI; if (t1 - t2 > Math.PI) t2 += 2 * Math.PI;
    const prims = [seg(a, b), seg(a, { x: a.x + r * Math.cos(t2), y: a.y + r * Math.sin(t2) })];
    ARC_LEVELS.forEach((k, i) => prims.push({ t: 'poly', pts: C.ellipsePoints(a.x, a.y, r * k, r * k, 0, 32, Math.min(t1, t2), Math.max(t1, t2)), color: color(i + 1) }));
    return prims;
  } });
  const TIME_EXT = [0, 0.382, 0.5, 0.618, 1, 1.382, 1.618, 2, 2.618, 4.236];
  // Trend-based Fib time: vertical lines at C + k·(B − A) measured in bars.
  const trendFibTimeLogicals = (la, lb, lc, levels = TIME_EXT) => levels.map(k => ({ k, logical: lc + k * (lb - la) }));
  C.registerTool({ type: 'fibtrendtime', group: 'fib', label: 'Trend-based Fib time', icon: 'Φ⇥', points: 3, build(d, view, pts) {
    if (pts.length < 2) return [];
    const prims = [{ t: 'poly', pts, dash: 'dashed', alpha: 0.6 }];
    if (pts.length < 3) return prims;
    const m = view.mapper, [la, lb, lc] = d.points.map(p => m.logicalForTime(p.time));
    trendFibTimeLogicals(la, lb, lc).forEach((z, i) => { const x = m.xForLogical(z.logical); prims.push(seg({ x, y: -10 }, { x, y: view.h + 10 }, { color: color(i) }), text(x + 3, 12, String(z.k), { textColor: color(i) })); });
    return prims;
  } });

  // ── cycles, sine, projection ──
  function cycleXs(view, la, lb) {
    const m = view.mapper, period = lb - la, out = [];
    if (Math.abs(period) < 1e-9) return out;
    const p = Math.abs(period), first = la - Math.ceil((la - m.logicalAt(0)) / p) * p;
    for (let l = first, n = 0; m.xForLogical(l) <= view.w + 10 && n < 500; l += p, n += 1) out.push(m.xForLogical(l));
    return out;
  }
  C.registerTool({ type: 'cyclic', group: 'measure', label: 'Cyclic lines', icon: '⦀', points: 2, build(d, view) {
    const m = view.mapper, xs = cycleXs(view, m.logicalForTime(d.points[0].time), m.logicalForTime(d.points[1]?.time ?? d.points[0].time));
    return xs.map(x => seg({ x, y: -10 }, { x, y: view.h + 10 }));
  } });
  C.registerTool({ type: 'timecycles', group: 'measure', label: 'Time cycles', icon: '⌒⌒', points: 2, build(d, view, pts) {
    if (pts.length < 2) return [];
    const m = view.mapper, xs = cycleXs(view, m.logicalForTime(d.points[0].time), m.logicalForTime(d.points[1].time)), base = pts[0].y, prims = [];
    for (let i = 1; i < xs.length; i += 1) { const r = (xs[i] - xs[i - 1]) / 2; prims.push({ t: 'poly', pts: C.ellipsePoints(xs[i - 1] + r, base, r, r, 0, 32, Math.PI, 2 * Math.PI) }); }
    return prims;
  } });
  // Sine through A (crest) and B (next trough): period = 2·(B − A) in bars, amplitude = (A − B)/2 in pixels.
  function sinePoints(view, a, b) {
    const half = b.x - a.x, amp = (b.y - a.y) / 2, midY = (a.y + b.y) / 2, out = [];
    if (Math.abs(half) < 1) return out;
    for (let x = -10; x <= view.w + 10; x += 3) out.push({ x, y: midY - amp * Math.cos(Math.PI * (x - a.x) / half) });
    return out;
  }
  C.registerTool({ type: 'sine', group: 'measure', label: 'Sine line', icon: '∿', points: 2, build: (d, view, pts) => (pts.length < 2 ? [] : [{ t: 'poly', pts: sinePoints(view, pts[0], pts[1]) }]) });
  C.registerTool({ type: 'projection', group: 'measure', label: 'Projection', icon: '⇉', points: 3, build(d, view, pts, o, s) {
    if (pts.length < 2) return [];
    const [a, b, c] = pts, prims = [seg(a, b)];
    if (!c) return prims;
    const end = { x: c.x + (b.x - a.x), y: c.y + (b.y - a.y) }, pa = Number(d.points[0].price), pb = Number(d.points[1].price), pc = Number(d.points[2].price);
    const target = pc + (pb - pa);
    prims.push(seg(b, c, { dash: 'dotted' }), seg(c, end, { dash: 'dashed' }), { t: 'poly', pts: C.arrowHead(c, end, 9), closed: true, fill: s.color });
    prims.push(text(end.x + 6, end.y, `${C.fmt(target, view.precision)} (${C.signed(pc ? (target - pc) / pc * 100 : NaN, 2)}%)`, { bg: 'rgba(16,20,28,0.85)', textColor: s.color }));
    return prims;
  } });

  // ── bars pattern / ghost feed: drawn candles ──
  function candlePrims(list, view, alpha, up = '#2fae86', down = '#d9534f') {
    const w = Math.max(1, view.mapper.spacing * 0.6), prims = [];
    for (const k of list) {
      const yo = view.mapper.y(k.open), yc = view.mapper.y(k.close), yh = view.mapper.y(k.high), yl = view.mapper.y(k.low);
      if ([yo, yc, yh, yl].some(v => v === null)) continue;
      const col = k.close >= k.open ? up : down;
      prims.push(seg({ x: k.x, y: yh }, { x: k.x, y: yl }, { color: col, alpha }), { t: 'poly', pts: [{ x: k.x - w / 2, y: yo }, { x: k.x + w / 2, y: yo }, { x: k.x + w / 2, y: yc }, { x: k.x - w / 2, y: yc }], closed: true, fill: C.rgba(col, alpha * 0.6), color: col, alpha });
    }
    return prims;
  }
  // Source bars [A, B] copied so the first bar's open sits at C (time and price).
  function barsPattern(candles, range, anchorLogical, anchorPrice) {
    if (!range) return [];
    const first = candles[range[0]], shift = anchorPrice - first.open, out = [];
    for (let i = range[0]; i <= range[1]; i += 1) { const k = candles[i]; out.push({ logical: anchorLogical + (i - range[0]), open: k.open + shift, high: k.high + shift, low: k.low + shift, close: k.close + shift }); }
    return out;
  }
  C.registerTool({ type: 'barspattern', group: 'measure', label: 'Bars pattern', icon: '⫼', points: 3, build(d, view, pts) {
    if (pts.length < 2) return [];
    const m = view.mapper, range = m.indexRange(d.points[0].time, d.points[1].time);
    const box = [seg({ x: pts[0].x, y: pts[0].y }, { x: pts[1].x, y: pts[0].y }, { dash: 'dotted' })];
    if (pts.length < 3) return box;
    const copy = barsPattern(view.candles, range, m.logicalForTime(d.points[2].time), Number(d.points[2].price)).map(k => ({ ...k, x: m.xForLogical(k.logical) }));
    return box.concat(candlePrims(copy, view, 0.55));
  } });
  // Ghost feed: each path segment becomes ⌈bars⌉ synthetic candles walking from point to point.
  function ghostCandles(points, view) {
    const m = view.mapper, out = [];
    for (let i = 1; i < points.length; i += 1) {
      const l0 = m.logicalForTime(points[i - 1].time), l1 = m.logicalForTime(points[i].time), p0 = Number(points[i - 1].price), p1 = Number(points[i].price);
      const n = Math.max(1, Math.round(Math.abs(l1 - l0)));
      for (let j = 0; j < n; j += 1) {
        const o = p0 + (p1 - p0) * j / n, c = p0 + (p1 - p0) * (j + 1) / n, wick = Math.abs(p1 - p0) / n * 0.4 + Math.abs(p0) * 0.0002;
        out.push({ logical: l0 + Math.sign(l1 - l0 || 1) * j, open: o, close: c, high: Math.max(o, c) + wick, low: Math.min(o, c) - wick });
      }
    }
    return out;
  }
  C.registerTool({ type: 'ghostfeed', group: 'measure', label: 'Ghost feed', icon: '👻', mode: 'multi', minPoints: 2, maxPoints: 30, build(d, view, pts) {
    if (pts.length < 2) return [];
    const ks = ghostCandles(d.points, view).map(k => ({ ...k, x: view.mapper.xForLogical(k.logical) }));
    return [{ t: 'poly', pts, dash: 'dotted', alpha: 0.4 }, ...candlePrims(ks, view, 0.5)];
  } });

  // ── signpost, table ──
  C.registerTool({ type: 'signpost', group: 'annotation', label: 'Signpost', icon: '🪧', points: 1, editText: true, defaults: { text: 'Signpost' }, build(d, view, [a], o, s) {
    const top = a.y - 46;
    return [seg(a, { x: a.x, y: top }, { width: 2 }), { t: 'poly', pts: C.ellipsePoints(a.x, a.y, 3, 3, 0, 12), closed: true, fill: s.color },
      { t: 'text', x: a.x, y: top, text: d.text || ' ', align: 'center', base: 'bottom', size: s.fontSize, bg: s.color, textColor: s.textColor || '#0b0f16', pad: 5 }];
  } });
  // Table: rows separated by newlines, cells by "|"; column widths from the widest cell.
  function tableLayout(textValue, size, measure) {
    const rows = String(textValue || '').split('\n').map(r => r.split('|').map(c => c.trim()));
    const cols = Math.max(...rows.map(r => r.length)), widths = Array.from({ length: cols }, (_, i) => Math.max(...rows.map(r => measure(r[i] || '', size))) + 10);
    return { rows, cols, widths, rowH: size + 8 };
  }
  C.registerTool({ type: 'table', group: 'annotation', label: 'Table', icon: '▦', points: 1, editText: true, defaults: { text: 'Level | Price\nEntry | \nStop | ' }, build(d, view, [a], o, s) {
    const size = s.fontSize, L = tableLayout(d.text, size, (t, z) => C.textBox({ text: t || ' ', size: z, pad: 0, x: 0, y: 0 }).w), prims = [];
    const W = L.widths.reduce((x, y) => x + y, 0), H = L.rows.length * L.rowH;
    prims.push({ t: 'poly', pts: [a, { x: a.x + W, y: a.y }, { x: a.x + W, y: a.y + H }, { x: a.x, y: a.y + H }], closed: true, fill: 'rgba(18,23,34,0.92)', color: s.color, hitFill: true });
    L.rows.forEach((row, r) => {
      if (r) prims.push(seg({ x: a.x, y: a.y + r * L.rowH }, { x: a.x + W, y: a.y + r * L.rowH }, { alpha: 0.5, hit: false }));
      let x = a.x;
      row.forEach((cell, i) => { prims.push(text(x + 5, a.y + r * L.rowH + L.rowH / 2, cell, { size, bold: r === 0, textColor: s.textColor || '#dbe3f1' })); x += L.widths[i]; });
    });
    let x = a.x;
    L.widths.slice(0, -1).forEach(w => { x += w; prims.push(seg({ x, y: a.y }, { x, y: a.y + H }, { alpha: 0.5, hit: false })); });
    return prims;
  } });

  return { pitchOrigin, gannFanRays, spiralPoints, PHI, trendFibTimeLogicals, cycleXs, sinePoints, barsPattern, ghostCandles, tableLayout, TIME_EXT };
});
