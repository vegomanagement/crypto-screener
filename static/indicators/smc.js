/* SMC chart overlay for the active pane: swings (HH/HL/LH/LL), BOS/CHoCH lines, order blocks,
 * FVGs, liquidity sweeps and the dealing-range equilibrium, from GET /api/agent/mtf. Optional
 * layers: the next higher TF's structure, and the HTF Wyckoff range box. Pure model + canvas
 * draw; UMD: window.WorkspaceSmc / module.exports. Block times are UTC ms; the chart uses s.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.WorkspaceSmc = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const isNum = v => typeof v === 'number' && Number.isFinite(v);
  const LABEL = { 5: '5m', 15: '15m', 30: '30m', 60: '1h', 240: '4h', D: '1D', W: '1W' };
  const UP = '#00c087', DOWN = '#f5465c', HTF = '#c9a227';
  const sec = ms => Math.floor(ms / 1000);

  function layer(a, tf, htf) {
    const s = (a && a.smc) || {}, prefix = htf ? `${LABEL[tf] || tf} ` : '';
    return { tf, htf, prefix,
      swings: (s.swings || []).filter(x => isNum(x.price) && isNum(x.ts)).map(x => ({ time: sec(x.ts), price: x.price, high: x.kind === 'H', label: prefix + (x.label || (x.kind === 'H' ? 'H' : 'L')) })),
      breaks: (s.breaks || []).filter(b => b && isNum(b.level) && isNum(b.ts) && isNum(b.swing_ts)).map(b => ({ from: sec(b.swing_ts), to: sec(b.ts), price: b.level, bull: b.dir === 'bull', label: prefix + b.kind })),
      zones: [...(s.order_blocks || []).map(z => ({ ...z, k: 'OB' })), ...(s.fvgs || []).map(z => ({ ...z, k: 'FVG' }))]
        .filter(z => z.state !== 'invalidated' && isNum(z.top) && isNum(z.bottom) && isNum(z.ts))
        .map(z => ({ time: sec(z.ts), top: z.top, bottom: z.bottom, bull: z.dir === 'bull', label: `${prefix}${z.k}${z.state === 'mitigated' ? ' (m)' : ''}` })),
      sweeps: (s.sweeps || []).filter(x => isNum(x.price) && isNum(x.ts)).map(x => ({ time: sec(x.ts), price: x.price, high: x.side === 'high', label: `${prefix}sweep` })),
      eq: s.dealing_range && isNum(s.dealing_range.eq) ? { price: s.dealing_range.eq, time: sec(Math.max(s.dealing_range.high_ts || 0, s.dealing_range.low_ts || 0)), label: `${prefix}EQ 50%` } : null };
  }

  /** Overlay model for one pane key; opts: {smc, htf, box}. null when nothing to draw. */
  function overlayModel(body, key, opts) {
    const o = opts || {}, b = body && body.mtf;
    if (!b || !b.tfs || !(o.smc || o.htf || o.box)) return null;
    const pane = b.interval, ladder = (b.ladder || []).slice().reverse();   // ascending
    const up = ladder[ladder.indexOf(pane) + 1];
    const layers = [];
    if (o.smc && b.tfs[pane]) layers.push(layer(b.tfs[pane], pane, false));
    if (o.htf && up && b.tfs[up]) layers.push(layer(b.tfs[up], up, true));
    let box = null;
    if (o.box) {
      const tf = ladder.slice(ladder.indexOf(pane) + 1).find(t => b.tfs[t] && b.tfs[t].wyckoff && b.tfs[t].wyckoff.status === 'structure' && b.tfs[t].wyckoff.tr
        && isNum(b.tfs[t].wyckoff.tr.support) && isNum(b.tfs[t].wyckoff.tr.resistance));   // first HTF with a defined range
      const w = tf && b.tfs[tf].wyckoff;
      if (w && isNum(w.tr.support) && isNum(w.tr.resistance) && isNum(w.tr.start_ts)) {
        box = { tf, start: sec(w.tr.start_ts), end: isNum(w.tr.end_ts) ? sec(w.tr.end_ts) : null, top: w.tr.resistance, bottom: w.tr.support,
          title: `${LABEL[tf] || tf} Wyckoff ${w.schematic} · phase ${w.phase} · ${w.tr.support}–${w.tr.resistance}`, color: w.schematic === 'accumulation' ? UP : DOWN };
      }
    }
    if (!layers.length && !box) return null;
    return { key, layers, box };
  }

  function rgba(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  /** view: { x(sec) bar left edge, xc(sec) bar centre, y(price), plotWidth, first (sec) }. */
  function draw(ctx, view, model) {
    if (!model) return 0;
    let drawn = 0;
    const clampT = t => (isNum(view.first) && t < view.first ? view.first : t);
    const X = t => view.x(clampT(t)), XC = t => view.xc(clampT(t));
    ctx.save();
    ctx.font = '10px SFMono-Regular, Consolas, monospace';
    if (model.box) {
      const b = model.box, x1 = X(b.start), x2 = b.end === null ? view.plotWidth : X(b.end), yt = view.y(b.top), yb = view.y(b.bottom);
      if ([x1, x2, yt, yb].every(isNum)) {
        ctx.fillStyle = rgba(b.color, 0.05); ctx.fillRect(Math.min(x1, x2), Math.min(yt, yb), Math.abs(x2 - x1), Math.abs(yb - yt));
        ctx.strokeStyle = rgba(b.color, 0.9); ctx.lineWidth = 1.5; ctx.setLineDash?.([8, 4]);
        ctx.strokeRect(Math.min(x1, x2), Math.min(yt, yb), Math.abs(x2 - x1), Math.abs(yb - yt)); ctx.setLineDash?.([]);
        ctx.fillStyle = b.color; ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
        ctx.fillText(b.title, Math.max(48, Math.min(x1, x2) + 6), Math.max(60, Math.min(yt, yb) - 3));   // below the OHLC legend when the top is off-screen
        drawn += 1;
      }
    }
    model.layers.forEach(L => {
      const up = L.htf ? HTF : UP, down = L.htf ? HTF : DOWN, w = L.htf ? 2 : 1;
      L.zones.forEach(z => {
        const x1 = X(z.time), yt = view.y(z.top), yb = view.y(z.bottom);
        if (![x1, yt, yb].every(isNum)) return;
        ctx.fillStyle = rgba(z.bull ? up : down, L.htf ? 0.06 : 0.07); ctx.fillRect(x1, Math.min(yt, yb), view.plotWidth - x1, Math.abs(yb - yt));
        ctx.fillStyle = z.bull ? up : down; ctx.textAlign = 'right'; ctx.textBaseline = 'top'; ctx.fillText(z.label, view.plotWidth - 4, Math.min(yt, yb) + 1);
        drawn += 1;
      });
      L.breaks.forEach(b => {
        const x1 = XC(b.from), x2 = XC(b.to), y = view.y(b.price);
        if (![x1, x2, y].every(isNum)) return;
        ctx.strokeStyle = b.bull ? up : down; ctx.lineWidth = w; ctx.setLineDash?.(b.label.endsWith('CHoCH') ? [5, 3] : []);
        ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(x2, y); ctx.stroke(); ctx.setLineDash?.([]);
        ctx.fillStyle = b.bull ? up : down; ctx.textAlign = 'center'; ctx.textBaseline = b.bull ? 'bottom' : 'top';
        ctx.fillText(b.label, (x1 + x2) / 2, y + (b.bull ? -2 : 2));
        drawn += 1;
      });
      L.swings.forEach(s => {
        if (isNum(view.first) && s.time < view.first) return;   // off the loaded window: not stacked at the edge
        const x = XC(s.time), y = view.y(s.price);
        if (!isNum(x) || !isNum(y)) return;
        ctx.fillStyle = L.htf ? HTF : '#e6e9ef'; ctx.textAlign = 'center'; ctx.textBaseline = s.high ? 'bottom' : 'top';
        ctx.fillText(s.label, x, y + (s.high ? -3 : 3));
        drawn += 1;
      });
      L.sweeps.forEach(s => {
        if (isNum(view.first) && s.time < view.first) return;
        const x = XC(s.time), y = view.y(s.price);
        if (!isNum(x) || !isNum(y)) return;
        ctx.strokeStyle = '#ffb020'; ctx.lineWidth = 1.5; ctx.beginPath();
        ctx.moveTo(x - 4, y - 4); ctx.lineTo(x + 4, y + 4); ctx.moveTo(x + 4, y - 4); ctx.lineTo(x - 4, y + 4); ctx.stroke();
        ctx.fillStyle = '#ffb020'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(s.label, x + 6, y);
        drawn += 1;
      });
      if (L.eq) {
        const x1 = X(L.eq.time), y = view.y(L.eq.price);
        if (isNum(x1) && isNum(y)) {
          ctx.strokeStyle = L.htf ? HTF : '#8a93a6'; ctx.lineWidth = 1; ctx.setLineDash?.([2, 3]);
          ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(view.plotWidth, y); ctx.stroke(); ctx.setLineDash?.([]);
          ctx.fillStyle = L.htf ? HTF : '#8a93a6'; ctx.textAlign = 'right'; ctx.textBaseline = 'bottom'; ctx.fillText(L.eq.label, view.plotWidth - 4, y - 2);
          drawn += 1;
        }
      }
    });
    ctx.restore();
    return drawn;
  }

  return { overlayModel, draw, layer };
});
