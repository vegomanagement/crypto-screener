/* Drawing tools — Patterns: harmonic (XABCD, Cypher, ABCD), Head & Shoulders, Triangle pattern,
 * Three drives and Elliott waves. Labelled polylines with ratio annotations. Own implementation.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./core.js'));
  else root.WorkspaceDrawingsPatterns = factory(root.WorkspaceDrawingsCore);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (C) {
  'use strict';
  const seg = (a, b, extra = {}) => ({ t: 'seg', a, b, ...extra });
  const text = (x, y, value, extra = {}) => ({ t: 'text', x, y, text: value, size: 11, ...extra });
  // |move(b→c)| / |move(a→b)| on prices — the usual harmonic retracement ratio.
  const ratio = (pa, pb, pc) => (pb === pa ? null : Math.abs(pc - pb) / Math.abs(pb - pa));
  function labels(pts, names, s) {
    return pts.map((p, i) => {
      if (!names[i]) return null;
      const prev = pts[i - 1] || pts[i + 1], above = prev ? p.y <= prev.y : true;
      return text(p.x, p.y + (above ? -10 : 10), names[i], { align: 'center', base: above ? 'bottom' : 'top', bg: s.color, textColor: '#0b0f16', bold: true, hit: false });
    });
  }
  function ratioLabel(a, c, value, s) { return value === null ? null : text((a.x + c.x) / 2, (a.y + c.y) / 2, C.fmt(value, 3), { align: 'center', textColor: s.color, bg: 'rgba(16,20,28,0.8)', hit: false }); }
  function harmonic(type, label, icon, names) {
    C.registerTool({ type, group: 'patterns', label, icon, points: names.length, defaults: { opts: { fillBackground: true }, style: { fillAlpha: 0.12 } },
      options: [{ key: 'fillBackground', label: 'Fill triangles', type: 'bool' }],
      build(d, view, pts, o, s) {
        if (pts.length < 2) return [];
        const pr = d.points.map(p => Number(p.price)), prims = [];
        if (o.fillBackground && names.length === 5) {
          if (pts.length >= 3) prims.push({ t: 'poly', pts: pts.slice(0, 3), closed: true, fill: C.rgba(s.color, s.fillAlpha), stroke: false, hit: false });
          if (pts.length >= 5) prims.push({ t: 'poly', pts: pts.slice(2, 5), closed: true, fill: C.rgba(s.color, s.fillAlpha), stroke: false, hit: false });
        }
        prims.push({ t: 'poly', pts });
        for (let i = 2; i < pts.length; i += 1) {
          prims.push(seg(pts[i - 2], pts[i], { dash: 'dotted', alpha: 0.6, hit: false }));
          const r = ratioLabel(pts[i - 2], pts[i], ratio(pr[i - 2], pr[i - 1], pr[i]), s);
          if (r) prims.push(r);
        }
        return prims.concat(labels(pts, names, s).filter(Boolean));
      } });
  }
  harmonic('xabcd', 'XABCD pattern', 'Ⅹ', ['X', 'A', 'B', 'C', 'D']);
  harmonic('cypher', 'Cypher pattern', 'Ⓒ', ['X', 'A', 'B', 'C', 'D']);
  harmonic('abcd', 'ABCD pattern', 'ⓐ', ['A', 'B', 'C', 'D']);
  function wave(type, label, icon, names, extra) {
    C.registerTool({ type, group: 'patterns', label, icon, points: names.length, build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const prims = [{ t: 'poly', pts }, ...labels(pts, names, s).filter(Boolean)];
      return extra ? prims.concat(extra(pts, s, view) || []) : prims;
    } });
  }
  // Necklines / boundary lines extended to the right edge of the plot.
  const extended = (p, q, view, extra = {}) => { const l = C.extendSegment(p, q, false, true, view.w + view.h); return seg(p, l.b, { dash: 'dashed', ...extra }); };
  wave('headshoulders', 'Head and shoulders', '⩙', ['', 'LS', '', 'H', '', 'RS', ''], (pts, s, view) => (pts.length >= 5 ? [extended(pts[2], pts[4], view)] : []));
  wave('trianglepattern', 'Triangle pattern', '◁', ['A', 'B', 'C', 'D'], (pts, s, view) => (pts.length >= 4 ? [extended(pts[0], pts[2], view), extended(pts[1], pts[3], view)] : []));
  wave('threedrives', 'Three drives pattern', '⫶', ['', '1', 'A', '2', 'B', '3', '']);
  wave('elliottimpulse', 'Elliott impulse wave (12345)', '⑤', ['0', '1', '2', '3', '4', '5']);
  wave('elliottcorrection', 'Elliott correction wave (ABC)', 'Ⓐ', ['0', 'A', 'B', 'C']);
  wave('elliotttriangle', 'Elliott triangle wave (ABCDE)', 'Ⓔ', ['0', 'A', 'B', 'C', 'D', 'E']);
  wave('elliottdouble', 'Elliott double combo (WXY)', 'Ⓦ', ['0', 'W', 'X', 'Y']);
  wave('elliotttriple', 'Elliott triple combo (WXYXZ)', 'Ⓩ', ['0', 'W', 'X', 'Y', 'X', 'Z']);
  return { ratio };
});
