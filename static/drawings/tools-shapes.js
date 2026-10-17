/* Drawing tools — Shapes / brushes and Annotation / text. Own implementation.
 * Freehand tools (brush, highlighter) store a pixel-simplified polyline as (time, price) points.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./core.js'));
  else root.WorkspaceDrawingsShapes = factory(root.WorkspaceDrawingsCore);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (C) {
  'use strict';
  const seg = (a, b, extra = {}) => ({ t: 'seg', a, b, ...extra });
  const text = (x, y, value, extra = {}) => ({ t: 'text', x, y, text: value, ...extra });
  const fillOf = s => C.rgba(s.fill || s.color, s.fillAlpha);
  const FILL = [{ key: 'fillBackground', label: 'Fill background', type: 'bool' }];
  const centreText = (d, s, x, y) => (d.text ? [text(x, y, d.text, { align: 'center', size: s.fontSize, textColor: s.textColor || s.color })] : []);
  const shape = (pts, o, s, extra = {}) => ({ t: 'poly', pts, closed: true, fill: o.fillBackground ? fillOf(s) : null, hitFill: Boolean(o.fillBackground), ...extra });

  C.registerTool({ type: 'rect', group: 'shapes', label: 'Rectangle', icon: '▭', points: 2, defaults: { opts: { fillBackground: true }, style: { fillAlpha: 0.13 } }, options: FILL,
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const [a, b] = pts, corners = [a, { x: b.x, y: a.y }, b, { x: a.x, y: b.y }];
      return [shape(corners, o, s), ...centreText(d, s, (a.x + b.x) / 2, (a.y + b.y) / 2)];
    } });
  // Rotated rectangle: a→b is one side; c sets the signed width along the side's normal.
  function rotatedCorners(a, b, c) {
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1, nx = -dy / len, ny = dx / len;
    const w = (c.x - a.x) * nx + (c.y - a.y) * ny;
    return [a, b, { x: b.x + nx * w, y: b.y + ny * w }, { x: a.x + nx * w, y: a.y + ny * w }];
  }
  C.registerTool({ type: 'rotrect', group: 'shapes', label: 'Rotated rectangle', icon: '◇', points: 3, defaults: { opts: { fillBackground: true }, style: { fillAlpha: 0.13 } }, options: FILL,
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      if (pts.length < 3) return [seg(pts[0], pts[1])];
      return [shape(rotatedCorners(...pts), o, s)];
    } });
  C.registerTool({ type: 'circle', group: 'shapes', label: 'Circle', icon: '◯', points: 2, defaults: { opts: { fillBackground: true }, style: { fillAlpha: 0.13 } }, options: FILL,
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const [a, b] = pts, r = Math.hypot(b.x - a.x, b.y - a.y);
      return [shape(C.ellipsePoints(a.x, a.y, r, r), o, s), ...centreText(d, s, a.x, a.y)];
    } });
  C.registerTool({ type: 'ellipse', group: 'shapes', label: 'Ellipse', icon: '⬭', points: 2, defaults: { opts: { fillBackground: true }, style: { fillAlpha: 0.13 } }, options: FILL,
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const [a, b] = pts;
      return [shape(C.ellipsePoints((a.x + b.x) / 2, (a.y + b.y) / 2, Math.abs(b.x - a.x) / 2, Math.abs(b.y - a.y) / 2), o, s), ...centreText(d, s, (a.x + b.x) / 2, (a.y + b.y) / 2)];
    } });
  C.registerTool({ type: 'triangle', group: 'shapes', label: 'Triangle', icon: '△', points: 3, defaults: { opts: { fillBackground: true }, style: { fillAlpha: 0.13 } }, options: FILL,
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      return pts.length < 3 ? [seg(pts[0], pts[1])] : [shape(pts, o, s)];
    } });
  C.registerTool({ type: 'path', group: 'shapes', label: 'Path', icon: '⤳', mode: 'multi', minPoints: 2, maxPoints: 100, defaults: { opts: { arrowEnd: true } },
    options: [{ key: 'arrowEnd', label: 'Arrow at end', type: 'bool' }],
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const prims = [{ t: 'poly', pts }];
      if (o.arrowEnd) prims.push({ t: 'poly', pts: C.arrowHead(pts.at(-2), pts.at(-1), 8 + s.width * 2), closed: true, fill: s.color });
      return prims;
    } });
  C.registerTool({ type: 'polyline', group: 'shapes', label: 'Polyline', icon: '⬠', mode: 'multi', minPoints: 2, maxPoints: 100,
    defaults: { opts: { closed: true, fillBackground: true }, style: { fillAlpha: 0.1 } }, options: [{ key: 'closed', label: 'Closed', type: 'bool' }, ...FILL],
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const closed = o.closed && pts.length > 2;
      return [{ t: 'poly', pts, closed, fill: closed && o.fillBackground ? fillOf(s) : null, hitFill: closed && o.fillBackground }];
    } });
  C.registerTool({ type: 'arc', group: 'shapes', label: 'Arc', icon: '⌒', points: 3, build(d, view, pts) {
    if (pts.length < 2) return [];
    return pts.length < 3 ? [seg(pts[0], pts[1])] : [{ t: 'poly', pts: C.curveThrough(pts[0], pts[2], pts[1]) }];
  } });
  C.registerTool({ type: 'brush', group: 'shapes', label: 'Brush', icon: '🖌', mode: 'free', minPoints: 2, maxPoints: C.MAX_POINTS, defaults: { style: { width: 2 } },
    build: (d, view, pts) => (pts.length < 2 ? [] : [{ t: 'poly', pts, cap: 'round' }]) });
  C.registerTool({ type: 'highlighter', group: 'shapes', label: 'Highlighter', icon: '🖍', mode: 'free', minPoints: 2, maxPoints: C.MAX_POINTS, defaults: { style: { color: '#e0c33a', width: 4 } },
    build: (d, view, pts, o, s) => (pts.length < 2 ? [] : [{ t: 'poly', pts, cap: 'round', width: s.width * 3, alpha: 0.35 }]) });
  C.registerTool({ type: 'arrow', group: 'shapes', label: 'Arrow', icon: '➝', points: 2, build(d, view, pts, o, s) {
    if (pts.length < 2) return [];
    return [seg(pts[0], pts[1]), { t: 'poly', pts: C.arrowHead(pts[0], pts[1], 9 + s.width * 2), closed: true, fill: s.color }];
  } });
  function markTool(type, label, icon, up) {
    return { type, group: 'shapes', label, icon, points: 1, defaults: { style: { color: up ? '#2fae86' : '#d9534f' } },
      build(d, view, [a], o, s) {
        const k = up ? 1 : -1, tip = { x: a.x, y: a.y + 4 * k }, h = 18;
        const pts = [tip, { x: a.x + 9, y: tip.y + 9 * k }, { x: a.x + 4, y: tip.y + 9 * k }, { x: a.x + 4, y: tip.y + h * k }, { x: a.x - 4, y: tip.y + h * k }, { x: a.x - 4, y: tip.y + 9 * k }, { x: a.x - 9, y: tip.y + 9 * k }];
        const prims = [{ t: 'poly', pts, closed: true, fill: s.color, hitFill: true }];
        if (d.text) prims.push(text(a.x, tip.y + (h + 4) * k, d.text, { align: 'center', base: up ? 'top' : 'bottom', size: s.fontSize, textColor: s.textColor || s.color }));
        return prims;
      } };
  }
  C.registerTool(markTool('arrowup', 'Arrow mark up', '⇧', true));
  C.registerTool(markTool('arrowdown', 'Arrow mark down', '⇩', false));

  // ── annotation ──
  const TEXT_OPTIONS = [{ key: 'background', label: 'Background', type: 'bool' }, { key: 'bold', label: 'Bold', type: 'bool' }];
  C.registerTool({ type: 'text', group: 'annotation', label: 'Text', icon: 'T', points: 1, editText: true, defaults: { text: 'Text', opts: { background: false, bold: false }, style: { fontSize: 14 } },
    options: TEXT_OPTIONS,
    build: (d, view, [a], o, s) => [text(a.x, a.y, d.text || ' ', { size: s.fontSize, bold: o.bold, textColor: s.textColor || s.color, base: 'bottom',
      bg: o.background ? C.rgba(s.fill || s.color, Math.max(0.15, s.fillAlpha)) : null, pad: 3 })] });
  C.registerTool({ type: 'note', group: 'annotation', label: 'Note', icon: '🗒', points: 1, editText: true, defaults: { text: 'Note', style: { color: '#c9b33b' } },
    build(d, view, [a], o, s) {
      return [{ t: 'poly', pts: C.ellipsePoints(a.x, a.y, 5, 5, 0, 16), closed: true, fill: s.color, hitFill: true },
        seg(a, { x: a.x + 10, y: a.y - 10 }, { hit: false }),
        text(a.x + 10, a.y - 10, d.text || ' ', { base: 'bottom', size: s.fontSize, bg: 'rgba(24,28,38,0.95)', border: s.color, textColor: s.textColor || '#e6ebf5', pad: 6 })];
    } });
  C.registerTool({ type: 'pricelabel', group: 'annotation', label: 'Price label', icon: '🏷', points: 1, build(d, view, [a], o, s) {
    const label = text(a.x + 12, a.y - 14, d.text || d.points[0].price, { base: 'middle', size: s.fontSize, bg: s.color, textColor: s.textColor || '#0b0f16', pad: 4 });
    const box = C.textBox(label);
    return [{ t: 'poly', pts: [a, { x: box.left, y: box.top + box.h * 0.25 }, { x: box.left, y: box.top + box.h * 0.75 }], closed: true, fill: s.color, hitFill: true }, label];
  } });
  C.registerTool({ type: 'callout', group: 'annotation', label: 'Callout', icon: '💬', points: 2, editText: true, defaults: { text: 'Callout' },
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const [a, b] = pts;
      return [seg(a, b), text(b.x, b.y, d.text || ' ', { align: 'center', size: s.fontSize, bg: s.color, textColor: s.textColor || '#0b0f16', pad: 6 })];
    } });
  C.registerTool({ type: 'flag', group: 'annotation', label: 'Flag mark', icon: '⚑', points: 1, defaults: { style: { color: '#e5534b' } }, build(d, view, [a], o, s) {
    const top = a.y - 22, prims = [seg(a, { x: a.x, y: top }, { width: 2 }), { t: 'poly', pts: [{ x: a.x, y: top }, { x: a.x + 14, y: top + 4 }, { x: a.x, y: top + 9 }], closed: true, fill: s.color, hitFill: true }];
    if (d.text) prims.push(text(a.x + 16, top + 4, d.text, { size: s.fontSize, textColor: s.textColor || s.color }));
    return prims;
  } });

  return { rotatedCorners };
});
