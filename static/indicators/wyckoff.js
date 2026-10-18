/* Wyckoff chart overlay for the active pane: trading-range box, creek/ice line and event
 * markers from GET /api/agent/structure (wyckoff block). Pure model + canvas draw; UMD:
 * window.WorkspaceWyckoff / module.exports. Times in the block are UTC ms; the chart uses s.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.WorkspaceWyckoff = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const isNum = v => typeof v === 'number' && Number.isFinite(v);
  const COLORS = { accumulation: '#00c087', distribution: '#f5465c', unclear: '#8a93a6' };
  const BELOW = new Set(['SC', 'ST', 'Spring', 'Test', 'LPS', 'PS']);

  /** Overlay model for one pane key ('exchange:symbol:interval'); null when nothing to draw. */
  function overlayModel(block, key) {
    if (!block || !block.tr || !isNum(block.tr.support) || !isNum(block.tr.start_ts)) return null;
    const kind = block.status === 'structure' ? block.schematic : 'unclear';
    const tr = block.tr, acc = block.schematic === 'accumulation';
    const marks = (block.events || []).filter(e => isNum(e.ts) && isNum(e.price)).map(e => {
      // markers sit on the side of the bar where the event happened (lows vs highs)
      const low = acc ? BELOW.has(e.event) : ['AR', 'SOW', 'Markdown'].includes(e.event);
      return { time: Math.floor(e.ts / 1000), price: e.price, label: e.event, below: low, confidence: e.confidence };
    });
    return { key, kind, color: COLORS[kind] || COLORS.unclear, phase: block.phase || null,
      start: Math.floor(tr.start_ts / 1000), end: isNum(tr.end_ts) ? Math.floor(tr.end_ts / 1000) : null,
      top: isNum(tr.resistance) ? tr.resistance : null, bottom: tr.support,
      edge: acc ? block.creek : block.ice, edgeLabel: acc ? 'creek' : 'ice', marks,
      title: block.status === 'structure' ? `Wyckoff ${block.schematic} · phase ${block.phase}` : 'Wyckoff: unclear (invalidated)' };
  }

  function rgba(hex, a) {
    const m = /^#([0-9a-f]{6})$/i.exec(hex || '');
    if (!m) return `rgba(138,147,166,${a})`;
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  /** view: { x(sec) bar left edge, xc(sec) bar centre, y(price), plotWidth }. Returns items drawn. */
  function draw(ctx, view, model) {
    if (!model) return 0;
    let drawn = 0;
    const x1 = view.x(model.start);
    const x2 = model.end === null ? view.plotWidth : view.x(model.end);
    const yb = view.y(model.bottom), yt = model.top === null ? null : view.y(model.top);
    ctx.save();
    ctx.font = '10px SFMono-Regular, Consolas, monospace';
    if ([x1, x2, yb, yt].every(isNum)) {
      ctx.fillStyle = rgba(model.color, 0.07);
      ctx.fillRect(Math.min(x1, x2), Math.min(yt, yb), Math.abs(x2 - x1), Math.abs(yb - yt));
      ctx.strokeStyle = rgba(model.color, 0.7); ctx.lineWidth = 1; ctx.setLineDash?.([4, 3]);
      ctx.strokeRect(Math.min(x1, x2), Math.min(yt, yb), Math.abs(x2 - x1), Math.abs(yb - yt));
      ctx.setLineDash?.([]);
      ctx.fillStyle = model.color; ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
      ctx.fillText(model.title, Math.max(2, Math.min(x1, x2) + 3), Math.min(yt, yb) - 2);
      drawn += 1;
    }
    const ye = isNum(model.edge) ? view.y(model.edge) : null;
    if (isNum(ye) && isNum(x1)) {
      ctx.strokeStyle = model.color; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(x1, ye); ctx.lineTo(view.plotWidth, ye); ctx.stroke();
      ctx.fillStyle = model.color; ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
      ctx.fillText(model.edgeLabel, view.plotWidth - 4, ye - 2);
      drawn += 1;
    }
    model.marks.forEach(m => {
      const x = view.xc(m.time), y = view.y(m.price);
      if (!isNum(x) || !isNum(y)) return;
      const dy = m.below ? 9 : -9;
      ctx.fillStyle = model.color; ctx.beginPath();
      ctx.moveTo(x, y + (m.below ? 2 : -2)); ctx.lineTo(x - 4, y + dy); ctx.lineTo(x + 4, y + dy); ctx.closePath(); ctx.fill();
      ctx.textAlign = 'center'; ctx.textBaseline = m.below ? 'top' : 'bottom';
      ctx.fillStyle = '#e6e9ef'; ctx.fillText(m.label, x, y + dy + (m.below ? 1 : -1));
      drawn += 1;
    });
    ctx.restore();
    return drawn;
  }

  return { overlayModel, draw, COLORS };
});
