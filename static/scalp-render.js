/* Canvas renderer for the view-only scalping page (/scalp): DOM ladder, prints feed, cluster columns.
 * Draws only what the book/feed/cluster models hold. Rows outside the venue's depth window are drawn as
 * "unknown" (hatched, no size) instead of empty, so missing depth never looks like an empty level.
 * Geometry is pure (unit-tested); drawing takes any CanvasRenderingContext2D-like object.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./orderbook.js'));
  else root.ScalpRender = factory(root.ScalpOrderBook);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (ob) {
  'use strict';

  const COLORS = { bg: '#0c0f15', text: '#d8dfeb', muted: '#778397', line: '#242b3a', grid: '#323b4f',
    bid: '#00c087', ask: '#ef5466', big1: '#f5a623', big2: '#c77dff', spread: '#1a2232', unknown: '#151a22',
    ruler: '#527dff', mark: '#8fb0ff', age: '#f5d76e', bidZone: 'rgba(0,192,135,0.05)', askZone: 'rgba(239,84,102,0.05)' };
  const FONT = '11px "SF Mono", Menlo, Consolas, monospace';

  // ── geometry ───────────────────────────────────────────────────────────────
  /** Column split: [clusters][feed][price][volume]. Volume bars are capped in width; the feed takes the rest; narrow columns drop clusters first. */
  function layout(width, { clusterCount = 3, clusterW = 46, priceW = 74, minVolW = 84, maxVolW = 240, volRatio = 0.4 } = {}) {
    const w = Math.max(0, width);
    let clusters = Math.max(0, Math.floor(clusterCount));
    const fixed = () => priceW + minVolW + clusters * clusterW;
    while (clusters > 0 && w - fixed() < 80) clusters -= 1;
    const rest = Math.max(0, w - priceW - clusters * clusterW);
    const volW = Math.min(rest, Math.max(minVolW, Math.min(maxVolW, Math.round(rest * volRatio))));
    const feedW = Math.max(0, rest - volW);
    const clusterX = 0, feedX = clusters * clusterW, priceX = feedX + feedW, volX = priceX + priceW;
    return { width: w, clusters: { x: clusterX, w: clusters * clusterW, each: clusterW, count: clusters },
      feed: { x: feedX, w: feedW }, price: { x: priceX, w: priceW }, volume: { x: volX, w: volW } };
  }
  /** Row r (price grows upwards) is centred at H/2 + (centerRow − r)·rowPx. */
  const rowCenterY = (view, row, height) => height / 2 + (view.centerRow - row) * view.rowPx;
  const rowAtY = (view, y, height) => Math.round(view.centerRow - (y - height / 2) / view.rowPx);
  /** Continuous price → y, consistent with rows: row r spans prices [r·n·tick, (r+1)·n·tick). */
  const priceY = (view, price, rowSize, height) => height / 2 + (view.centerRow - (price / rowSize - 0.5)) * view.rowPx;
  function visibleRows(view, height) {
    const half = Math.ceil(height / 2 / view.rowPx) + 1;
    const c = Math.round(view.centerRow);
    return { top: c + half, bottom: c - half };
  }
  /**
   * Autocenter: keep the touch near the middle. Re-centre only when the mid row drifts more than
   * `distancePct` % of the half-height away (0 = always centred), so the ladder does not jitter every tick.
   */
  function autocenter(centerRow, midRow, height, rowPx, distancePct = 30) {
    if (!Number.isFinite(midRow)) return centerRow;
    if (!Number.isFinite(centerRow)) return midRow;
    const limit = (height / 2 / rowPx) * Math.max(0, Math.min(95, distancePct)) / 100;
    return Math.abs(midRow - centerRow) > limit ? midRow : centerRow;
  }
  /** Full-scale value for bars: a fixed setting, or the largest visible row (auto). */
  function fullScale(values, fixed) {
    if (fixed > 0) return fixed;
    let max = 0;
    for (const v of values) if (Math.abs(v) > max) max = Math.abs(v);
    return max;
  }
  function bigColor(usd, big1, big2) {
    if (big2 > 0 && usd >= big2) return COLORS.big2;
    if (big1 > 0 && usd >= big1) return COLORS.big1;
    return null;
  }
  function formatVolume(value, abbreviate) {
    if (!Number.isFinite(value)) return '—';
    if (abbreviate) return ob.abbreviate(value);
    const a = Math.abs(value);
    return value.toLocaleString('en-US', { maximumFractionDigits: a >= 100 ? 0 : a >= 1 ? 2 : 4 });
  }
  const formatPrice = (price, decimals) => price.toFixed(Math.max(0, Math.min(10, decimals)));
  const pctBetween = (from, to) => (from > 0 ? (to - from) / from * 100 : NaN);

  function rgba(hex, alpha) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${Math.max(0, Math.min(1, alpha)).toFixed(3)})`;
  }

  // ── ladder ────────────────────────────────────────────────────────────────
  /**
   * Draw the DOM ladder. model: { book, rowN, decimals, density, now, mark, ruler:{anchorRow,hoverRow} }.
   * Returns stats used by the page and tests (rows drawn, full scale, unknown rows).
   */
  function drawLadder(ctx, L, height, view, model, s) {
    const { book, rowN: n } = model, rowSize = n * (book ? book.tick : 1);
    const { top, bottom } = visibleRows(view, height);
    const px = L.price, vol = L.volume, rowPx = view.rowPx;
    const stats = { rows: 0, withVolume: 0, unknown: 0, scale: 0, bestBidRow: null, bestAskRow: null, aged: 0 };
    ctx.fillStyle = COLORS.bg; ctx.fillRect(px.x, 0, px.w + vol.w, height);
    if (!book) {
      ctx.font = FONT; ctx.fillStyle = COLORS.muted; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText('book not synced · no levels shown', px.x + (px.w + vol.w) / 2, height / 2);
      return stats;
    }
    const bids = ob.groupSide(book, 'bid', n), asks = ob.groupSide(book, 'ask', n), best = book.best();
    const bestBidRow = best.bid === null ? null : ob.rowOf(best.bid, n), bestAskRow = best.ask === null ? null : ob.rowOf(best.ask, n);
    stats.bestBidRow = bestBidRow; stats.bestAskRow = bestAskRow;
    const usdMode = s.units !== 'coin', value = cell => (cell ? (usdMode ? cell.usd : cell.qty) : 0);
    const visible = [];
    for (let r = bottom; r <= top; r += 1) visible.push(value(bids.get(r)) + value(asks.get(r)));
    const scale = stats.scale = fullScale(visible, usdMode ? s.fullScaleUsd : 0);
    ctx.font = FONT; ctx.textBaseline = 'middle';
    for (let r = bottom; r <= top; r += 1) {
      const cy = rowCenterY(view, r, height), y = Math.round(cy - rowPx / 2);
      if (y > height || y + rowPx < 0) continue;
      stats.rows += 1;
      const covered = book.covered(r * n) && book.covered(r * n + n - 1);
      const bid = bids.get(r), ask = asks.get(r);
      const askSide = bestAskRow !== null && r >= bestAskRow, bidSide = bestBidRow !== null && r <= bestBidRow;
      const spread = bestBidRow !== null && bestAskRow !== null && r > bestBidRow && r < bestAskRow;
      ctx.fillStyle = spread ? COLORS.spread : askSide ? COLORS.askZone : bidSide ? COLORS.bidZone : COLORS.bg;
      ctx.fillRect(px.x, y, px.w + vol.w, rowPx);
      if (!covered && !spread) {
        stats.unknown += 1;
        ctx.fillStyle = COLORS.unknown; ctx.fillRect(vol.x, y, vol.w, rowPx);
        ctx.strokeStyle = COLORS.line; ctx.beginPath(); ctx.moveTo(vol.x, y + rowPx); ctx.lineTo(vol.x + Math.min(vol.w, rowPx * 2), y); ctx.stroke();
      } else {
        let offset = 0;
        for (const [cell, color] of [[ask, COLORS.ask], [bid, COLORS.bid]]) {
          const v = value(cell);
          if (!(v > 0)) continue;
          stats.withVolume += 1;
          const w = scale > 0 ? Math.max(1, Math.min(1, v / scale) * (vol.w - 2)) : 0;
          const big = bigColor(cell.usd, s.big1Usd, s.big2Usd);
          ctx.fillStyle = rgba(big || color, big ? 0.75 : 0.45);
          const h = bid && ask ? Math.max(1, rowPx / 2 - 1) : rowPx - 1;
          ctx.fillRect(vol.x + 1, y + offset, w, h);
          ctx.fillStyle = big ? '#fff' : COLORS.text; ctx.textAlign = 'left';
          if (rowPx >= 10) ctx.fillText(formatVolume(v, s.abbreviate), vol.x + 4, y + offset + h / 2);
          if (bid && ask) offset += rowPx / 2;
        }
        if (model.density && rowPx >= 10) {
          const age = Math.max(model.density.rowAge('bid', r, n, model.now) ?? -1, model.density.rowAge('ask', r, n, model.now) ?? -1);
          if (age >= 0) {
            stats.aged += 1; ctx.fillStyle = COLORS.age; ctx.textAlign = 'right';
            ctx.fillText(ob.formatAge(age), vol.x + vol.w - 3, cy);
          }
        }
      }
      const isBest = r === bestBidRow || r === bestAskRow, onGrid = s.grid > 0 && r % s.grid === 0;
      if (onGrid) { ctx.fillStyle = COLORS.grid; ctx.fillRect(px.x, y, px.w + vol.w, 1); }
      if (rowPx >= 9) {
        ctx.textAlign = 'right';
        ctx.fillStyle = r === bestAskRow ? COLORS.ask : r === bestBidRow ? COLORS.bid : onGrid ? COLORS.text : COLORS.muted;
        ctx.font = isBest || onGrid ? `bold ${FONT}` : FONT;
        ctx.fillText(formatPrice(r * rowSize, model.decimals), px.x + px.w - 4, cy);
        ctx.font = FONT;
      }
    }
    ctx.fillStyle = COLORS.line; ctx.fillRect(px.x + px.w, 0, 1, height); ctx.fillRect(px.x, 0, 1, height);
    if (Number.isFinite(model.mark) && model.mark > 0) {
      const y = priceY(view, model.mark, rowSize, height);
      ctx.fillStyle = COLORS.mark; ctx.beginPath(); ctx.moveTo(px.x, y - 4); ctx.lineTo(px.x + 6, y); ctx.lineTo(px.x, y + 4); ctx.fill();
    }
    drawRuler(ctx, L, height, view, model, s, { bids, asks, bestBidRow, bestAskRow, rowSize });
    return stats;
  }

  /** % ruler from a clicked price to the hovered row, with resting liquidity between touch and that row. */
  function drawRuler(ctx, L, height, view, model, s, g) {
    const ruler = model.ruler || {};
    if (!Number.isFinite(ruler.anchorRow)) return;
    const x0 = L.feed.x, x1 = L.volume.x + L.volume.w, ya = rowCenterY(view, ruler.anchorRow, height);
    ctx.fillStyle = COLORS.ruler; ctx.fillRect(x0, Math.round(ya), x1 - x0, 1);
    if (!Number.isFinite(ruler.hoverRow) || ruler.hoverRow === ruler.anchorRow) return;
    const yh = rowCenterY(view, ruler.hoverRow, height);
    const from = (ruler.anchorRow + 0.5) * g.rowSize, to = (ruler.hoverRow + 0.5) * g.rowSize;
    const pct = pctBetween(from, to);
    const usdMode = s.units !== 'coin';
    let liq = 0;
    if (g.bestAskRow !== null && ruler.hoverRow >= g.bestAskRow) for (let r = g.bestAskRow; r <= ruler.hoverRow; r += 1) { const c = g.asks.get(r); if (c) liq += usdMode ? c.usd : c.qty; }
    if (g.bestBidRow !== null && ruler.hoverRow <= g.bestBidRow) for (let r = g.bestBidRow; r >= ruler.hoverRow; r -= 1) { const c = g.bids.get(r); if (c) liq += usdMode ? c.usd : c.qty; }
    const label = `${pct >= 0 ? '+' : ''}${pct.toFixed(3)}% · ${Math.abs(ruler.hoverRow - ruler.anchorRow)} rows · liq ${usdMode ? '$' : ''}${formatVolume(liq, true)}`;
    ctx.fillStyle = rgba(COLORS.ruler, 0.12);
    ctx.fillRect(L.price.x, Math.min(ya, yh), L.price.w + L.volume.w, Math.abs(yh - ya));
    ctx.font = FONT; ctx.textAlign = 'left';
    const w = Math.min(L.volume.w + L.price.w, label.length * 6.6 + 8), lx = L.price.x + 2, ly = yh + (yh > ya ? 9 : -9);
    ctx.fillStyle = '#1d2740'; ctx.fillRect(lx, ly - 8, w, 16);
    ctx.fillStyle = COLORS.text; ctx.fillText(label, lx + 4, ly);
  }

  // ── prints feed ───────────────────────────────────────────────────────────
  /** x of a print time: newest at the right edge (next to the ladder), `spanMs` across the feed width. */
  const feedX = (area, time, now, spanMs) => area.x + area.w - (now - time) / spanMs * area.w;
  /** Prints inside the visible time span that pass the "hide < X $" filter (render-level accounting). */
  function visiblePrints(feed, now, f) {
    const from = now - f.spanMs, list = [];
    let hidden = 0;
    for (let k = feed.prints.length - 1; k >= 0; k -= 1) {
      const p = feed.prints[k];
      if (p.lastTime < from) break;
      if (p.lastTime > now) continue;
      if (p.usd < (f.minUsd || 0)) { hidden += 1; continue; }
      list.push(p);
    }
    list.reverse();
    return { list, hidden };
  }
  function drawFeed(ctx, L, height, view, model, s) {
    const area = L.feed, f = s.feed, stats = { drawn: 0, hidden: 0, trades: 0, labels: 0 };
    ctx.fillStyle = COLORS.bg; ctx.fillRect(area.x, 0, area.w, height);
    if (!(area.w > 0) || !model.feed || !(model.tick > 0)) return stats;
    const rowSize = model.rowN * model.tick, now = model.now;
    ctx.save(); ctx.beginPath(); ctx.rect(area.x, 0, area.w, height); ctx.clip();
    ctx.fillStyle = COLORS.line;
    for (let t = Math.ceil((now - f.spanMs) / 10000) * 10000; t <= now; t += 10000) ctx.fillRect(Math.round(feedX(area, t, now, f.spanMs)), 0, 1, height);
    const { list, hidden } = visiblePrints(model.feed, now, f);
    stats.hidden = hidden;
    const pts = list.map(p => ({ p, x: feedX(area, p.lastTime, now, f.spanMs), y: priceY(view, p.vwap, rowSize, height),
      r: ob.printRadius(p.usd, { minRadius: f.minRadius, maxRadius: f.maxRadius, maxSizeUsd: f.maxSizeUsd }) }));
    if (f.line && pts.length > 1) {
      ctx.strokeStyle = 'rgba(160,170,190,0.45)'; ctx.lineWidth = 1; ctx.beginPath();
      pts.forEach((q, k) => (k ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y))); ctx.stroke();
    }
    ctx.font = FONT; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    const placed = [];
    for (const q of pts) {
      const color = q.p.side === 'buy' ? COLORS.bid : COLORS.ask;
      ctx.fillStyle = rgba(color, 0.55); ctx.strokeStyle = color; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(q.x, q.y, q.r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      stats.drawn += 1; stats.trades += q.p.count;
      if (q.p.usd >= (f.labelMinUsd || Infinity)) {
        const label = ob.abbreviate(s.units === 'coin' ? q.p.qty : q.p.usd), nearEdge = q.x > area.x + area.w - 24;
        const w = label.length * 6.6 + 4, ly = nearEdge || q.r >= 9 ? q.y : q.y - q.r - 7, lx = nearEdge ? q.x - q.r - 2 - w : q.x - w / 2;
        // Older labels are already painted; a colliding newer label is skipped (counted) rather than overprinted.
        if (placed.some(b => lx < b.x + b.w && lx + w > b.x && Math.abs(ly - b.y) < 12)) { stats.labelsSkipped = (stats.labelsSkipped || 0) + 1; continue; }
        placed.push({ x: lx, y: ly, w }); if (placed.length > 40) placed.shift();
        stats.labels += 1; ctx.fillStyle = COLORS.text;
        if (nearEdge) { ctx.textAlign = 'right'; ctx.fillText(label, q.x - q.r - 2, q.y); ctx.textAlign = 'center'; }
        else ctx.fillText(label, q.x, ly);
      }
    }
    ctx.restore();
    return stats;
  }

  // ── cluster columns ─────────────────────────────────────────────────────────
  function cachedRows(entry, n) {
    const c = entry.cache;
    if (c && c.version === entry.bar.version && c.n === n) return c.rows;
    const rows = ob.clusterRows(entry.bar, n);
    entry.cache = { version: entry.bar.version, n, rows };
    return rows;
  }
  /** Newest column is rightmost (next to the feed). Values are footprint.js aggregates of observed trades. */
  function drawClusters(ctx, L, height, view, model, s) {
    const area = L.clusters, c = s.clusters, stats = { columns: 0, cells: 0, marked: 0 };
    ctx.fillStyle = COLORS.bg; ctx.fillRect(area.x, 0, area.w, height);
    if (!(area.count > 0) || !model.clusters?.length) return stats;
    const n = model.rowN, entries = model.clusters.slice(-area.count), { top, bottom } = visibleRows(view, height);
    const usd = s.units !== 'coin';
    ctx.font = FONT; ctx.textBaseline = 'middle';
    entries.forEach((entry, k) => {
      const x = area.x + area.w - (entries.length - k) * area.each, w = area.each - 2;
      const rows = cachedRows(entry, n), values = rows.map(r => ob.clusterValue(r, c.mode, usd ? 'usd' : 'coin'));
      const scale = fullScale(values, 0);
      stats.columns += 1;
      rows.forEach((row, j) => {
        const idx = Number(row.index);
        if (idx < bottom || idx > top) return;
        const y = Math.round(rowCenterY(view, idx, height) - view.rowPx / 2), v = values[j];
        const usdValue = Math.abs(ob.clusterValue(row, c.mode, 'usd'));
        const share = scale > 0 ? Math.abs(v) / scale : 0;
        const color = (c.mode === 'delta' ? v : row.delta) >= 0 ? COLORS.bid : COLORS.ask;
        ctx.fillStyle = rgba(color, 0.15 + 0.6 * share); ctx.fillRect(x, y, Math.max(1, share * w), view.rowPx - 1);
        stats.cells += 1;
        if (c.markMinUsd > 0 && usdValue >= c.markMinUsd) { stats.marked += 1; ctx.strokeStyle = COLORS.big1; ctx.strokeRect(x + 0.5, y + 0.5, w - 1, view.rowPx - 2); }
        if (view.rowPx >= 10 && usdValue >= (c.showMinUsd || 0)) {
          ctx.fillStyle = COLORS.text; ctx.textAlign = 'right';
          ctx.fillText(c.mode === 'bidask' ? `${ob.abbreviate(row.bid * (usd ? row.price : 1))}×${ob.abbreviate(row.ask * (usd ? row.price : 1))}` : ob.abbreviate(v), x + w - 2, y + view.rowPx / 2);
        }
      });
      const d = new Date(entry.openMs), label = `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}${entry.partial ? '~' : ''}`;
      ctx.fillStyle = '#10141c'; ctx.fillRect(x, 0, w, 14);
      ctx.fillStyle = entry.partial ? COLORS.big1 : COLORS.muted; ctx.textAlign = 'center'; ctx.fillText(label, x + w / 2, 7);
      ctx.fillStyle = COLORS.line; ctx.fillRect(x + w + 1, 0, 1, height);
    });
    return stats;
  }

  // ── price levels (levels.js) ──────────────────────────────────────────────
  /** A level is drawn on the centre of the ladder row that contains its price (where that row's price label is). */
  function levelY(view, price, rowSize, height) {
    const p = Number(price);
    if (!(p > 0) || !(rowSize > 0)) return NaN;
    return rowCenterY(view, Math.floor(p / rowSize + 1e-6), height);
  }
  /** Nearest drawn level within `tol` px of y (for drag / double-click), or null. */
  function levelAtY(list, view, y, height, rowSize, tol = 4) {
    let best = null, dist = Infinity;
    for (const level of list || []) {
      const d = Math.abs(levelY(view, level.price, rowSize, height) - y);
      if (d <= tol && d < dist) { best = level; dist = d; }
    }
    return best;
  }
  /** Lines across clusters, feed and ladder. Signal levels dashed; a fired one-shot level is drawn in its (grey) colour, dotted. */
  function drawLevels(ctx, L, height, view, model) {
    const list = model.levels || [], stats = { drawn: 0, offscreen: 0 };
    if (!list.length || !(model.tick > 0)) return stats;
    const rowSize = model.rowN * model.tick, x1 = L.volume.x + L.volume.w;
    ctx.save(); ctx.font = FONT; ctx.textBaseline = 'middle';
    for (const level of list) {
      const y = levelY(view, level.dragPrice ?? level.price, rowSize, height);
      if (!Number.isFinite(y) || y < -2 || y > height + 2) { stats.offscreen += 1; continue; }
      const yy = Math.round(y) + (level.width % 2 ? 0.5 : 0), signal = level.kind === 'signal', fired = signal && !level.armed;
      ctx.strokeStyle = level.color; ctx.lineWidth = level.width;
      ctx.setLineDash?.(signal ? (fired ? [2, 3] : [6, 4]) : []);
      ctx.globalAlpha = level.dragPrice ? 0.6 : 1;
      ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(x1, yy); ctx.stroke();
      ctx.setLineDash?.([]); ctx.globalAlpha = 1;
      const tag = `${signal ? (fired ? '◇' : '◆') : '—'} ${level.note ? level.note.slice(0, 24) : signal ? (fired ? 'fired' : level.repeat ? 'signal ↻' : 'signal') : ''}`.trim();
      const w = tag.length * 6.6 + 8, tx = Math.max(0, L.price.x - w - 2);
      ctx.fillStyle = level.color; ctx.fillRect(tx, yy - 7, w, 14);
      ctx.fillStyle = '#0c0f15'; ctx.textAlign = 'left'; ctx.fillText(tag, tx + 4, yy);
      stats.drawn += 1;
    }
    ctx.restore();
    return stats;
  }

  /** One full column paint. Returns per-part stats (also used for render-level feed accounting). */
  function drawColumn(ctx, size, view, model, s) {
    const L = layout(size.width, { clusterCount: s.clusters.count });
    ctx.clearRect(0, 0, size.width, size.height);
    const clusters = drawClusters(ctx, L, size.height, view, model, s);
    const feed = drawFeed(ctx, L, size.height, view, model, s);
    const ladder = drawLadder(ctx, L, size.height, view, model, s);
    const levels = drawLevels(ctx, L, size.height, view, model);
    return { layout: L, clusters, feed, ladder, levels };
  }

  return { COLORS, layout, rowCenterY, rowAtY, priceY, visibleRows, autocenter, fullScale, bigColor, formatVolume,
    formatPrice, pctBetween, rgba, drawLadder, drawRuler, feedX, visiblePrints, drawFeed, drawClusters, levelY, levelAtY, drawLevels, drawColumn, FONT };
});
