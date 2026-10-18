/* Drawing tools — Measure / projection: price range, date range, date & price range, ruler,
 * long / short position (risk-reward), anchored VWAP and fixed-range volume profile.
 * VWAP and the profile are computed from the pane's real candles (or exact footprint volume).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./core.js'));
  else root.WorkspaceDrawingsMeasure = factory(root.WorkspaceDrawingsCore);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (C) {
  'use strict';
  const seg = (a, b, extra = {}) => ({ t: 'seg', a, b, ...extra });
  const text = (x, y, value, extra = {}) => ({ t: 'text', x, y, text: value, size: 11, ...extra });
  const box = (l, t, r, b, extra = {}) => ({ t: 'poly', pts: [{ x: l, y: t }, { x: r, y: t }, { x: r, y: b }, { x: l, y: b }], closed: true, ...extra });
  const UP = '#2fae86', DOWN = '#d9534f';
  // Tick: the tool's own override, else the venue tick from exchange metadata, else the chart precision.
  const tickOf = (o, view) => (o.tick > 0 ? o.tick : view.tickSize > 0 ? view.tickSize : 10 ** -(view.precision ?? 2));

  // ── ranges ──
  function rangeStats(d, view) {
    const [a, b] = d.points, pa = Number(a.price), pb = Number(b.price), m = view.mapper;
    const bars = Math.round(m.logicalForTime(b.time) - m.logicalForTime(a.time));
    const range = m.indexRange(a.time, b.time);
    let volume = 0;
    if (range) for (let i = range[0]; i <= range[1]; i += 1) volume += view.candles[i].volume || 0;
    return { dp: pb - pa, pct: pa ? (pb - pa) / pa * 100 : null, bars, ms: b.time - a.time, volume, ticks: Math.round((pb - pa) / tickOf({}, view)) };
  }
  function rangeBuild(kind) {
    return (d, view, pts, o, s) => {
      if (pts.length < 2) return [];
      const [a, b] = pts, st = rangeStats(d, view), tone = kind === 'date' ? s.color : st.dp >= 0 ? UP : DOWN;
      const l = Math.min(a.x, b.x), r = Math.max(a.x, b.x), t = Math.min(a.y, b.y), bt = Math.max(a.y, b.y), prims = [];
      prims.push(box(l, t, r, bt, { fill: C.rgba(tone, 0.16), stroke: false, hitFill: true }));
      if (kind !== 'date') prims.push(seg({ x: (l + r) / 2, y: a.y }, { x: (l + r) / 2, y: b.y }, { color: tone }), { t: 'poly', pts: C.arrowHead({ x: (l + r) / 2, y: a.y }, { x: (l + r) / 2, y: b.y }, 7), closed: true, fill: tone, color: tone });
      if (kind !== 'price') prims.push(seg({ x: a.x, y: (t + bt) / 2 }, { x: b.x, y: (t + bt) / 2 }, { color: tone }), { t: 'poly', pts: C.arrowHead({ x: a.x, y: (t + bt) / 2 }, { x: b.x, y: (t + bt) / 2 }, 7), closed: true, fill: tone, color: tone });
      const lines = [];
      if (kind !== 'date') lines.push(`${C.signed(st.dp, view.precision)} (${C.signed(st.pct, 2)}%) ${st.ticks} ticks`);
      if (kind !== 'price') lines.push(`${st.bars} bars, ${C.formatDuration(st.ms)}`, `Vol ${C.compact(st.volume)}`);
      prims.push(text((l + r) / 2, b.y >= a.y ? bt + 6 : t - 6, lines.join('\n'), { align: 'center', base: b.y >= a.y ? 'top' : 'bottom', bg: tone, textColor: '#0b0f16' }));
      return prims;
    };
  }
  C.registerTool({ type: 'pricerange', group: 'measure', label: 'Price range', icon: '↕', points: 2, build: rangeBuild('price') });
  C.registerTool({ type: 'daterange', group: 'measure', label: 'Date range', icon: '↔', points: 2, build: rangeBuild('date') });
  C.registerTool({ type: 'datepricerange', group: 'measure', label: 'Date & price range', icon: '⤡', points: 2, build: rangeBuild('both') });
  C.registerTool({ type: 'ruler', group: 'measure', label: 'Measure (temporary)', icon: '📏', points: 2, temporary: true, build: rangeBuild('both') });

  // ── long / short position ──
  function positionStats(side, entry, target, stop, { account = 1000, riskPct = 1, qtyStep = 0, tick = 0.01 } = {}) {
    const long = side === 'long', risk = Math.abs(entry - stop), reward = Math.abs(target - entry);
    const valid = long ? stop < entry && entry < target : target < entry && entry < stop;
    const riskAmount = account * riskPct / 100;
    let qty = risk > 0 ? riskAmount / risk : 0;
    if (qtyStep > 0) qty = Math.floor(qty / qtyStep + 1e-9) * qtyStep;
    return { valid, risk, reward, rr: risk > 0 ? reward / risk : null, riskAmount, qty,
      targetPnl: qty * (long ? target - entry : entry - target), stopPnl: qty * (long ? stop - entry : entry - stop),
      targetPct: (target - entry) / entry * 100, stopPct: (stop - entry) / entry * 100,
      targetTicks: Math.round(Math.abs(target - entry) / tick), stopTicks: Math.round(Math.abs(stop - entry) / tick) };
  }
  // Two clicks (entry, then target or stop) → stored [entry, target@end, stop@end] with R:R 2 by default.
  function finalizePosition(side, points, view) {
    const [e, q] = points, entry = Number(e.price), price = Number(q.price), long = side === 'long', prec = view.precision ?? 8;
    const towardsTarget = long ? price > entry : price < entry;
    const target = towardsTarget ? price : entry + 2 * (entry - price), stop = towardsTarget ? entry - (price - entry) / 2 : price;
    let end = q.time;
    if (end <= e.time) end = view.mapper.timeForLogical(view.mapper.logicalForTime(e.time) + 20);
    const p = v => C.priceString(v, prec) || C.priceString(entry * (v > entry ? 1.001 : 0.999), prec);
    return [{ time: e.time, price: e.price }, { time: end, price: p(target) }, { time: end, price: p(stop) }];
  }
  function positionTool(side) {
    return {
      type: side, group: 'measure', label: side === 'long' ? 'Long position' : 'Short position', icon: side === 'long' ? '⬆$' : '⬇$', points: 2, storedPoints: 3,
      defaults: { opts: { account: 1000, riskPct: 1, qtyStep: 0, tick: 0, showPnl: true } },
      options: [{ key: 'account', label: 'Account size', type: 'number', min: 0, step: 1 }, { key: 'riskPct', label: 'Risk %', type: 'number', min: 0, max: 100, step: 0.1 },
        { key: 'qtyStep', label: 'Quantity step (0 = exact)', type: 'number', min: 0, step: 0.0001 }, { key: 'tick', label: 'Tick size (0 = exchange tick)', type: 'number', min: 0, step: 0.0001 },
        { key: 'showPnl', label: 'Show P&L', type: 'bool' }],
      finalize: (points, view) => finalizePosition(side, points, view),
      build(d, view, pts, o) {
        if (pts.length < 2) return [];
        const points = d.points.length === 2 ? finalizePosition(side, d.points, view) : d.points;
        const m = view.mapper, e = { x: m.x(points[0].time), y: m.y(points[0].price) }, xr = m.x(points[1].time);
        const ty = m.y(points[1].price), sy = m.y(points[2].price);
        if ([e.y, ty, sy].some(v => v === null)) return [];
        const st = positionStats(side, Number(points[0].price), Number(points[1].price), Number(points[2].price), { ...o, tick: tickOf(o, view) });
        const l = Math.min(e.x, xr), r = Math.max(e.x, xr), cx = (l + r) / 2, pr = view.precision, prims = [];
        prims.push(box(l, Math.min(e.y, ty), r, Math.max(e.y, ty), { fill: C.rgba(UP, 0.2), stroke: false, hitFill: true }));
        prims.push(box(l, Math.min(e.y, sy), r, Math.max(e.y, sy), { fill: C.rgba(DOWN, 0.2), stroke: false, hitFill: true }));
        prims.push(seg({ x: l, y: e.y }, { x: r, y: e.y }, { color: '#9aa4b6' }));
        const pnl = v => (o.showPnl ? ` · P&L ${C.signed(v, 2)}` : '');
        const small = { size: 10, align: 'center', textColor: '#0b0f16' };
        prims.push(text(cx, ty + (ty < e.y ? -3 : 3), `Target ${points[1].price} (${C.signed(st.targetPct, 2)}%)\n${st.targetTicks} ticks${pnl(st.targetPnl)}`,
          { ...small, base: ty < e.y ? 'bottom' : 'top', bg: UP }));
        prims.push(text(cx, sy + (sy < e.y ? -3 : 3), `Stop ${points[2].price} (${C.signed(st.stopPct, 2)}%)\n${st.stopTicks} ticks${pnl(st.stopPnl)}`,
          { ...small, base: sy < e.y ? 'bottom' : 'top', bg: DOWN }));
        const rr = st.rr === null ? '—' : C.fmt(st.rr, 2);
        prims.push(text(cx, e.y, `${side === 'long' ? 'Long' : 'Short'} ${points[0].price} · R:R ${rr}${st.valid ? '' : ' · invalid'}\nqty ${C.compact(st.qty)} · risk ${C.fmt(st.riskAmount, 2)} (${o.riskPct}%)`,
          { ...small, bg: st.valid ? '#2a3346' : '#6b2b2b', textColor: '#e6ebf5' }));
        return prims;
      },
      handles(d, view) {
        const pts = d.points.length === 3 ? d.points : finalizePosition(side, d.points, view), m = view.mapper;
        return [{ x: m.x(pts[0].time), y: m.y(pts[0].price), part: 'p0' }, { x: m.x(pts[1].time), y: m.y(pts[1].price), part: 'p1' }, { x: m.x(pts[2].time), y: m.y(pts[2].price), part: 'p2' }]
          .filter(h => h.y !== null);
      },
      dragHandle(d, index, p) {
        const [e, t, s] = d.points;
        if (index === 0) return [{ time: p.time, price: p.price }, t, s];
        const end = Math.max(p.time, e.time + 1);
        return index === 1 ? [e, { time: end, price: p.price }, { time: end, price: s.price }] : [e, { time: end, price: t.price }, { time: end, price: p.price }];
      }
    };
  }
  C.registerTool(positionTool('long'));
  C.registerTool(positionTool('short'));

  // ── anchored VWAP ──
  const SRC = { hlc3: c => (c.high + c.low + c.close) / 3, close: c => c.close, hl2: c => (c.high + c.low) / 2, ohlc4: c => (c.open + c.high + c.low + c.close) / 4 };
  function anchoredVwap(candles, start, source = 'hlc3') {
    const fn = SRC[source] || SRC.hlc3, out = [];
    let pv = 0, v = 0, pv2 = 0;
    for (let i = Math.max(0, start); i < candles.length; i += 1) {
      const c = candles[i], tp = fn(c), vol = c.volume || 0;
      pv += tp * vol; v += vol; pv2 += tp * tp * vol;
      if (v > 0) { const vwap = pv / v; out.push({ i, vwap, sd: Math.sqrt(Math.max(0, pv2 / v - vwap * vwap)) }); }
    }
    return out;
  }
  const cache = new Map();
  function memo(key, fn) {
    if (cache.has(key)) return cache.get(key);
    const value = fn();
    if (cache.size > 400) cache.delete(cache.keys().next().value);
    cache.set(key, value);
    return value;
  }
  const candlesKey = candles => `${candles.length}:${candles[0]?.time}:${candles.at(-1)?.time}:${candles.at(-1)?.close}:${candles.at(-1)?.volume}`;
  C.registerTool({ type: 'avwap', group: 'measure', label: 'Anchored VWAP', icon: 'ⓥ', points: 1, defaults: { opts: { source: 'hlc3', bands: 0 }, style: { color: '#e0913a', width: 2 } },
    options: [{ key: 'source', label: 'Source', type: 'select', choices: Object.keys(SRC) }, { key: 'bands', label: 'Band σ (0 = off)', type: 'number', min: 0, max: 5, step: 0.5 }],
    build(d, view, [a], o, s) {
      const range = view.mapper.indexRange(d.points[0].time, Number.MAX_SAFE_INTEGER);
      if (!range) return [seg({ x: a.x, y: a.y - 8 }, { x: a.x, y: a.y + 8 })];
      const series = memo(`vwap|${d.points[0].time}|${o.source}|${candlesKey(view.candles)}`, () => anchoredVwap(view.candles, range[0], o.source));
      const m = view.mapper, line = k => series.map(p => ({ x: m.xForLogical(p.i), y: m.y(p.vwap + k * p.sd) })).filter(p => p.y !== null);
      const prims = [{ t: 'poly', pts: line(0) }];
      if (o.bands > 0) prims.push({ t: 'poly', pts: line(o.bands), dash: 'dashed', alpha: 0.7 }, { t: 'poly', pts: line(-o.bands), dash: 'dashed', alpha: 0.7 });
      const last = series.at(-1);
      if (last) prims.push(text(m.xForLogical(last.i) + 6, m.y(last.vwap), `AVWAP ${C.fmt(last.vwap, view.precision)}`, { bg: s.color, textColor: '#0b0f16', hit: false }));
      return prims;
    } });

  // ── fixed-range volume profile ──
  // Candle volume spread uniformly over each candle's [low, high] (approximation), binned in rows.
  function profileFromCandles(candles, from, to, rows = 24) {
    let low = Infinity, high = -Infinity;
    for (let i = from; i <= to; i += 1) { low = Math.min(low, candles[i].low); high = Math.max(high, candles[i].high); }
    const bins = new Array(rows).fill(0), size = (high - low) / rows || 1;
    for (let i = from; i <= to; i += 1) {
      const c = candles[i], vol = c.volume || 0;
      if (c.high <= c.low) { bins[Math.min(rows - 1, Math.floor((c.low - low) / size))] += vol; continue; }
      for (let r = 0; r < rows; r += 1) {
        const lo = low + r * size, hi = lo + size, overlap = Math.min(hi, c.high) - Math.max(lo, c.low);
        if (overlap > 0) bins[r] += vol * overlap / (c.high - c.low);
      }
    }
    return finishProfile(bins, low, size);
  }
  // Exact volume-at-price rows ([{ price, volume }]) binned the same way.
  function profileFromLevels(levels, rows = 24) {
    if (!levels.length) return null;
    const low = Math.min(...levels.map(l => l.price)), high = Math.max(...levels.map(l => l.price));
    const size = (high - low) / rows || 1, bins = new Array(rows).fill(0);
    levels.forEach(l => { bins[Math.min(rows - 1, Math.floor((l.price - low) / size))] += l.volume; });
    return finishProfile(bins, low, size);
  }
  // POC = largest row; value area grows from the POC towards the larger neighbour until ≥ vaPct.
  function finishProfile(bins, low, size, vaPct = 70) {
    const total = bins.reduce((x, y) => x + y, 0);
    let poc = 0;
    bins.forEach((v, i) => { if (v > bins[poc]) poc = i; });
    let lo = poc, hi = poc, acc = bins[poc];
    while (total > 0 && acc / total * 100 < vaPct && (lo > 0 || hi < bins.length - 1)) {
      const below = lo > 0 ? bins[lo - 1] : -1, above = hi < bins.length - 1 ? bins[hi + 1] : -1;
      if (above >= below) { hi += 1; acc += bins[hi]; } else { lo -= 1; acc += bins[lo]; }
    }
    return { rows: bins.map((volume, i) => ({ lo: low + i * size, hi: low + (i + 1) * size, volume })), total, poc, vaLow: lo, vaHigh: hi, low, high: low + bins.length * size };
  }
  // Footprint store (static/footprint.js LiveFootprint) → exact levels when every bar is complete.
  function footprintLevels(store, candles, from, to, stepMs) {
    if (!store || !store.bars || store.intervalMs !== stepMs) return null;
    const out = new Map(), scale = 10n ** 10n;
    for (let i = from; i <= to; i += 1) {
      const entry = store.bars.get(candles[i].time * 1000);
      if (!entry || entry.coverage !== 'complete' || !entry.bar?.levels) return null;
      for (const cell of entry.bar.levels.values()) {
        const price = Number(cell.index * entry.bar.step / scale) / 1e8, volume = Number((cell.bid + cell.ask) / scale) / 1e8;
        out.set(price, (out.get(price) || 0) + volume);
      }
    }
    return [...out].map(([price, volume]) => ({ price, volume }));
  }
  C.registerTool({ type: 'vbp', group: 'measure', label: 'Fixed range volume profile', icon: '▤', points: 2, defaults: { opts: { rows: 24, vaPct: 70, widthPct: 30 }, style: { color: '#5b8def' } },
    options: [{ key: 'rows', label: 'Rows', type: 'number', min: 4, max: 200, step: 1 }, { key: 'vaPct', label: 'Value area %', type: 'number', min: 10, max: 100, step: 1 },
      { key: 'widthPct', label: 'Width % of range', type: 'number', min: 5, max: 100, step: 1 }],
    build(d, view, pts, o, s) {
      if (pts.length < 2) return [];
      const range = view.mapper.indexRange(d.points[0].time, d.points[1].time), m = view.mapper;
      const l = Math.min(pts[0].x, pts[1].x), r = Math.max(pts[0].x, pts[1].x);
      if (!range) return [box(l, Math.min(pts[0].y, pts[1].y), r, Math.max(pts[0].y, pts[1].y), { dash: 'dotted' })];
      const rows = Math.max(4, Math.min(200, Math.round(o.rows) || 24));
      const key = `vbp|${range}|${rows}|${o.vaPct}|${candlesKey(view.candles)}|${view.footprintVersion || 0}`;
      const result = memo(key, () => {
        const exact = footprintLevels(view.footprint, view.candles, range[0], range[1], view.stepMs);
        const prof = exact && exact.length ? profileFromLevels(exact, rows) : profileFromCandles(view.candles, range[0], range[1], rows);
        const fin = prof && finishProfile(prof.rows.map(x => x.volume), prof.low, (prof.high - prof.low) / rows, o.vaPct);
        return fin && { ...fin, exact: Boolean(exact && exact.length), bars: range[1] - range[0] + 1 };
      });
      if (!result) return [];
      const maxVol = Math.max(...result.rows.map(x => x.volume)) || 1, width = (r - l) * o.widthPct / 100, prims = [];
      const top = m.y(result.high), bottom = m.y(result.low);
      prims.push(box(l, top, r, bottom, { dash: 'dotted', alpha: 0.6, fill: C.rgba(s.color, 0.03), hitFill: true }));
      result.rows.forEach((row, i) => {
        const y0 = m.y(row.hi), y1 = m.y(row.lo), inVa = i >= result.vaLow && i <= result.vaHigh;
        if (y0 === null || y1 === null || !row.volume) return;
        prims.push(box(l, y0 + 0.5, l + width * row.volume / maxVol, y1 - 0.5, { fill: C.rgba(i === result.poc ? '#e0913a' : s.color, inVa ? 0.55 : 0.25), stroke: false, hit: false }));
      });
      const poc = result.rows[result.poc], pocY = m.y((poc.lo + poc.hi) / 2);
      prims.push(seg({ x: l, y: pocY }, { x: r, y: pocY }, { color: '#e0913a', hit: false }));
      prims.push(text(l, top - 4, `VbP ${result.bars} bars · Σ ${C.compact(result.total)} · POC ${C.fmt((poc.lo + poc.hi) / 2, view.precision)} · ${result.exact ? 'exact from footprint trades' : 'approximated from candles'}`,
        { base: 'bottom', bg: 'rgba(16,20,28,0.85)', textColor: s.color, hit: false }));
      return prims;
    } });

  return { rangeStats, positionStats, finalizePosition, anchoredVwap, profileFromCandles, profileFromLevels, finishProfile, footprintLevels };
});
