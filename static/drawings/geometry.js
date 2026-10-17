/* Drawing tools — pure geometry: exact price strings, time↔pixel mapping on Lightweight Charts
 * (integer logical calls only), primitive hit-testing/rendering, magnet snapping, simplification
 * and small statistics helpers. No DOM. Own implementation (Apache-2.0 chart underneath).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.WorkspaceDrawingsGeometry = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const DECIMAL = /^(?:0|[1-9]\d*)(?:\.\d+)?$/;
  const positiveDecimal = value => typeof value === 'string' && DECIMAL.test(value) && /[1-9]/.test(value);

  // Number -> exact plain decimal string at the chart's precision (no exponent, no trailing zeros).
  function priceString(value, precision = 8) {
    if (typeof value === 'string') return positiveDecimal(value) ? value : null;
    if (!Number.isFinite(value) || value <= 0) return null;
    const digits = Math.max(0, Math.min(12, Math.round(precision)));
    const text = value.toFixed(digits).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
    return positiveDecimal(text) ? text : null;
  }
  // Exact candle values (already decimals from the venue) without float re-rounding.
  function exactValue(value) {
    if (!Number.isFinite(value) || value <= 0) return null;
    const text = String(value);
    return /e/i.test(text) ? priceString(value, 12) : text;
  }
  const fmt = (value, precision = 2) => (Number.isFinite(value) ? value.toFixed(Math.max(0, Math.min(10, precision))) : '—');
  const signed = (value, precision = 2) => (Number.isFinite(value) ? `${value > 0 ? '+' : ''}${fmt(value, precision)}` : '—');
  function compact(value) {
    const a = Math.abs(value);
    if (!Number.isFinite(value)) return '—';
    if (a >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
    if (a >= 1e3) return `${(value / 1e3).toFixed(2)}K`;
    return a >= 1 ? value.toFixed(2) : value.toPrecision(3);
  }
  function rgba(hex, alpha) {
    const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || '');
    if (!m) return hex || 'rgba(79,140,255,0.2)';
    return `rgba(${parseInt(m[1], 16)},${parseInt(m[2], 16)},${parseInt(m[3], 16)},${Math.max(0, Math.min(1, alpha))})`;
  }

  // ── time/price ↔ pixel (see skill: never pass a fractional logical to the library) ──
  // candles: [{ time: seconds, open, high, low, close, volume }], stepMs: interval duration.
  function createMapper({ chart, series, candles, stepMs }) {
    const scale = chart.timeScale();
    const n = candles.length;
    if (!n) return null;
    const spacing = scale.options().barSpacing;
    const anchor = n - 1;
    const anchorX = scale.logicalToCoordinate(anchor);
    if (anchorX === null || !Number.isFinite(anchorX) || !(spacing > 0)) return null;
    const ms = i => candles[i].time * 1000;
    // Bar index for a time: interpolated between bars, stepped by the interval outside the data.
    function logicalForTime(t) {
      let low = 0, high = n;
      while (low < high) { const mid = (low + high) >>> 1; if (ms(mid) < t) low = mid + 1; else high = mid; }
      if (low === 0) return (t - ms(0)) / stepMs;
      if (low === n) return anchor + (t - ms(anchor)) / stepMs;
      return low - 1 + (t - ms(low - 1)) / (ms(low) - ms(low - 1));
    }
    function timeForLogical(l) {
      if (l <= 0) return Math.round(ms(0) + l * stepMs);
      if (l >= anchor) return Math.round(ms(anchor) + (l - anchor) * stepMs);
      const i = Math.floor(l);
      return Math.round(ms(i) + (l - i) * (ms(i + 1) - ms(i)));
    }
    const logicalAt = px => anchor + (px - anchorX) / spacing;
    const xForLogical = l => anchorX + (l - anchor) * spacing;
    return {
      spacing, anchor, anchorX, logicalForTime, timeForLogical, logicalAt, xForLogical, candles, stepMs,
      x: t => xForLogical(logicalForTime(t)),
      y: price => { const v = series.priceToCoordinate(Number(price)); return v === null || !Number.isFinite(v) ? null : v; },
      price: py => { const v = series.coordinateToPrice(py); return v === null || !Number.isFinite(v) ? null : v; },
      // Slot time under px: bar open time inside the data, interval-stepped slots outside it.
      slotTime: px => timeForLogical(Math.round(logicalAt(px))),
      barAt: px => { const i = Math.round(logicalAt(px)); return i >= 0 && i < n ? candles[i] : null; },
      // Candle index range [from, to] whose open times lie inside [t0, t1] (ms), or null.
      indexRange(t0, t1) {
        const lo = Math.min(t0, t1), hi = Math.max(t0, t1);
        let a = 0, b = n;
        while (a < b) { const m = (a + b) >>> 1; if (ms(m) < lo) a = m + 1; else b = m; }
        let c = a, d = n;
        while (c < d) { const m = (c + d) >>> 1; if (ms(m) <= hi) c = m + 1; else d = m; }
        return c - 1 >= a ? [a, c - 1] : null;
      }
    };
  }

  // ── plane geometry ──
  function distanceToSegment(p, a, b) {
    const dx = b.x - a.x, dy = b.y - a.y, len = dx * dx + dy * dy;
    const t = len ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len)) : 0;
    return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
  }
  function distanceToPolyline(p, pts, closed = false) {
    let best = Infinity;
    for (let i = 1; i < pts.length; i += 1) best = Math.min(best, distanceToSegment(p, pts[i - 1], pts[i]));
    if (closed && pts.length > 2) best = Math.min(best, distanceToSegment(p, pts[pts.length - 1], pts[0]));
    if (pts.length === 1) best = Math.hypot(p.x - pts[0].x, p.y - pts[0].y);
    return best;
  }
  function pointInPolygon(p, pts) {
    let inside = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i, i += 1) {
      const a = pts[i], b = pts[j];
      if ((a.y > p.y) !== (b.y > p.y) && p.x < (b.x - a.x) * (p.y - a.y) / (b.y - a.y) + a.x) inside = !inside;
    }
    return inside;
  }
  // Segment a→b optionally extended past a ("left") and/or past b ("right") far beyond the plot.
  function extendSegment(a, b, extendA, extendB, size = 4000) {
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy);
    if (!len) return { a, b };
    const k = (size * 4) / len;
    return { a: extendA ? { x: a.x - dx * k, y: a.y - dy * k } : a, b: extendB ? { x: b.x + dx * k, y: b.y + dy * k } : b };
  }
  const lerp = (a, b, t) => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
  function ellipsePoints(cx, cy, rx, ry, rotation = 0, steps = 72, from = 0, to = Math.PI * 2) {
    const out = [], cos = Math.cos(rotation), sin = Math.sin(rotation);
    for (let i = 0; i <= steps; i += 1) {
      const t = from + (to - from) * i / steps, ex = rx * Math.cos(t), ey = ry * Math.sin(t);
      out.push({ x: cx + ex * cos - ey * sin, y: cy + ex * sin + ey * cos });
    }
    return out;
  }
  // Quadratic curve through a, c with control derived so the curve passes through m at t=0.5.
  function curveThrough(a, m, c, steps = 40) {
    const ctrl = { x: 2 * m.x - (a.x + c.x) / 2, y: 2 * m.y - (a.y + c.y) / 2 }, out = [];
    for (let i = 0; i <= steps; i += 1) {
      const t = i / steps, u = 1 - t;
      out.push({ x: u * u * a.x + 2 * u * t * ctrl.x + t * t * c.x, y: u * u * a.y + 2 * u * t * ctrl.y + t * t * c.y });
    }
    return out;
  }
  function arrowHead(from, to, size = 10) {
    const angle = Math.atan2(to.y - from.y, to.x - from.x), spread = Math.PI / 7;
    return [to, { x: to.x - size * Math.cos(angle - spread), y: to.y - size * Math.sin(angle - spread) },
      { x: to.x - size * Math.cos(angle + spread), y: to.y - size * Math.sin(angle + spread) }];
  }
  // Ramer–Douglas–Peucker in pixel space; returns the kept indices' points.
  function simplify(points, epsilon = 1.5) {
    if (points.length < 3) return points.slice();
    const keep = new Uint8Array(points.length); keep[0] = 1; keep[points.length - 1] = 1;
    const stack = [[0, points.length - 1]];
    while (stack.length) {
      const [s, e] = stack.pop();
      let best = -1, index = -1;
      for (let i = s + 1; i < e; i += 1) { const d = distanceToSegment(points[i], points[s], points[e]); if (d > best) { best = d; index = i; } }
      if (best > epsilon) { keep[index] = 1; stack.push([s, index], [index, e]); }
    }
    return points.filter((_, i) => keep[i]);
  }
  // Least squares y = a + b·x over x = 0..n-1; σ = population std dev of residuals.
  function linearRegression(values) {
    const n = values.length;
    if (n < 2) return null;
    let sx = 0, sy = 0, sxx = 0, sxy = 0;
    values.forEach((y, x) => { sx += x; sy += y; sxx += x * x; sxy += x * y; });
    const slope = (n * sxy - sx * sy) / (n * sxx - sx * sx), intercept = (sy - slope * sx) / n;
    let ss = 0;
    values.forEach((y, x) => { const r = y - (intercept + slope * x); ss += r * r; });
    return { slope, intercept, stdDev: Math.sqrt(ss / n), n, at: x => intercept + slope * x };
  }

  // ── primitives: { t:'seg'|'poly'|'text', ... } built by tools, drawn and hit-tested here ──
  let measurer = (text, size) => text.length * size * 0.6;
  function setTextMeasurer(fn) { if (typeof fn === 'function') measurer = fn; }
  function textBox(p) {
    const size = p.size || 11, pad = p.pad ?? (p.bg ? 4 : 1), lines = String(p.text).split('\n');
    const w = Math.max(...lines.map(line => measurer(line, size, p.bold))) + pad * 2, h = lines.length * (size + 3) + pad * 2 - 3;
    const left = p.align === 'center' ? p.x - w / 2 : p.align === 'right' ? p.x - w : p.x;
    const top = p.base === 'top' ? p.y : p.base === 'bottom' ? p.y - h : p.y - h / 2;
    return { left, top, w, h, lines, size, pad };
  }
  function hitPrimitive(p, x, y, tolerance) {
    if (p.hit === false) return false;
    const pt = { x, y };
    if (p.t === 'seg') return distanceToSegment(pt, p.a, p.b) <= tolerance + (p.width || 1) / 2;
    if (p.t === 'poly') {
      if (p.closed && p.fill && p.hitFill !== false && pointInPolygon(pt, p.pts)) return true;
      return p.stroke !== false && distanceToPolyline(pt, p.pts, p.closed) <= tolerance + (p.width || 1) / 2;
    }
    if (p.t === 'text') { const b = textBox(p); return x >= b.left - 2 && x <= b.left + b.w + 2 && y >= b.top - 2 && y <= b.top + b.h + 2; }
    return false;
  }
  function bounds(prims) {
    let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
    const add = q => { if (Number.isFinite(q.x) && Number.isFinite(q.y) && Math.abs(q.x) < 1e5 && Math.abs(q.y) < 1e5) { l = Math.min(l, q.x); r = Math.max(r, q.x); t = Math.min(t, q.y); b = Math.max(b, q.y); } };
    for (const p of prims) {
      if (p.t === 'seg') { add(p.a); add(p.b); } else if (p.t === 'poly') p.pts.forEach(add);
      else if (p.t === 'text') { const box = textBox(p); add({ x: box.left, y: box.top }); add({ x: box.left + box.w, y: box.top + box.h }); }
    }
    return Number.isFinite(l) ? { left: l, top: t, right: r, bottom: b } : null;
  }
  const DASHES = { solid: [], dashed: [6, 4], dotted: [1.5, 3] };
  function render(c, prims, style) {
    for (const p of prims) {
      const color = p.color || style.color, width = p.width ?? style.width ?? 1;
      c.globalAlpha = p.alpha ?? 1;
      if (p.t === 'text') {
        const box = textBox(p);
        c.font = `${p.bold ? '600 ' : ''}${box.size}px SFMono-Regular, Consolas, monospace`;
        if (p.bg) { c.fillStyle = p.bg; c.fillRect(box.left, box.top, box.w, box.h); }
        if (p.border) { c.strokeStyle = p.border; c.lineWidth = 1; c.setLineDash([]); c.strokeRect(box.left + 0.5, box.top + 0.5, box.w - 1, box.h - 1); }
        c.fillStyle = p.textColor || style.textColor || color; c.textBaseline = 'middle'; c.textAlign = 'left';
        box.lines.forEach((line, i) => c.fillText(line, box.left + box.pad, box.top + box.pad + i * (box.size + 3) + box.size / 2));
        continue;
      }
      c.lineWidth = width; c.strokeStyle = color; c.setLineDash(DASHES[p.dash ?? style.dash] || []);
      c.lineJoin = 'round'; c.lineCap = p.cap || 'butt';
      c.beginPath();
      if (p.t === 'seg') { c.moveTo(p.a.x, p.a.y); c.lineTo(p.b.x, p.b.y); }
      else if (p.t === 'poly' && p.pts.length) {
        c.moveTo(p.pts[0].x, p.pts[0].y);
        for (let i = 1; i < p.pts.length; i += 1) c.lineTo(p.pts[i].x, p.pts[i].y);
        if (p.closed) c.closePath();
        if (p.fill) { c.fillStyle = p.fill; c.fill(); }
      }
      if (p.stroke !== false) c.stroke();
    }
    c.globalAlpha = 1; c.setLineDash([]);
  }

  // ── magnet: 'off' | 'weak' (within 14 px of O/H/L/C) | 'strong' (always the nearest) ──
  const WEAK_PX = 14;
  function snap(mapper, x, y, { magnet = 'off', precision = 8 } = {}) {
    const mode = magnet === true ? 'strong' : magnet;
    const bar = mode === 'off' ? null : mapper.barAt(x);
    if (bar) {
      let best = null;
      for (const key of ['open', 'high', 'low', 'close']) {
        const py = mapper.y(bar[key]);
        if (py === null) continue;
        const distance = Math.abs(py - y);
        if (!best || distance < best.distance) best = { distance, key, value: bar[key] };
      }
      if (best && (mode === 'strong' || best.distance <= WEAK_PX)) return { time: bar.time * 1000, price: exactValue(best.value), snapped: best.key };
    }
    const raw = mapper.price(y);
    const price = raw === null ? null : priceString(raw, precision);
    return price === null ? null : { time: mapper.slotTime(x), price, snapped: null };
  }
  // Move every point by the pointer delta (whole bars along time, price along y).
  function movePoints(points, mapper, from, to, precision = 8, keepTime = false) {
    const dl = Math.round(mapper.logicalAt(to.x) - mapper.logicalAt(from.x));
    const p0 = mapper.price(from.y), p1 = mapper.price(to.y);
    if (p0 === null || p1 === null) return null;
    const out = points.map(pt => ({ time: keepTime ? pt.time : Math.max(0, mapper.timeForLogical(mapper.logicalForTime(pt.time) + dl)),
      price: priceString(Number(pt.price) + (p1 - p0), precision) }));
    return out.every(pt => pt.price !== null) ? out : null;
  }
  function measure(a, b, mapper) {
    const pa = Number(a.price), pb = Number(b.price);
    const bars = Math.round(mapper.logicalForTime(b.time) - mapper.logicalForTime(a.time));
    return { deltaPrice: pb - pa, deltaPct: pa ? (pb - pa) / pa * 100 : null, bars, deltaMs: b.time - a.time };
  }
  function formatDuration(ms) {
    const sign = ms < 0 ? '-' : '', s = Math.round(Math.abs(ms) / 1000);
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60), sec = s % 60;
    if (d) return `${sign}${d}d ${h}h`;
    if (h) return `${sign}${h}h ${m}m`;
    if (m) return `${sign}${m}m${sec ? ` ${sec}s` : ''}`;
    return `${sign}${sec}s`;
  }

  return { positiveDecimal, priceString, exactValue, fmt, signed, compact, rgba, createMapper, distanceToSegment, distanceToPolyline,
    pointInPolygon, extendSegment, lerp, ellipsePoints, curveThrough, arrowHead, simplify, linearRegression, setTextMeasurer, textBox,
    hitPrimitive, bounds, DASHES, render, WEAK_PX, snap, movePoints, measure, formatDuration };
});
