/* Big-trade bubbles: canvas renderer, hit-test/tooltip text and the per-pane controller.
 * Model lives in static/indicators/bubbles.js (WorkspaceBubbles).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./bubbles.js'));
  else root.WorkspaceBubblesRender = factory(root.WorkspaceBubbles);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (B) {
  'use strict';
  const DATA_KEYS = ['mode', 'minUsd', 'percentile', 'windowMin', 'minSamples', 'aggregate', 'aggWindowMs', 'aggTicks', 'backfillMin', 'budget', 'maxStored'];
  const rgba = (hex, a) => { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${Math.max(0, Math.min(1, a)).toFixed(3)})`; };

  // Largest `max` prints, ordered biggest → smallest so small bubbles paint on top.
  function selectVisible(list, max) {
    const sorted = list.slice().sort((a, b) => b.notional - a.notional);
    return sorted.length > max ? sorted.slice(0, max) : sorted;
  }
  function effectiveMaxRadius(s, barSpacing) {
    if (!s.fitBar) return s.rMax;
    return Math.max(s.rMin + 1, Math.min(s.rMax, barSpacing * 1.5));
  }
  /** view: { fromMs, toMs, xForMs(ms), y(price), barSpacing, plotWidth, plotHeight } */
  function draw(ctx, store, view, settings) {
    const s = { ...B.defaults(), ...settings };
    const all = store ? store.range(view.fromMs, view.toMs) : [];
    const chosen = selectVisible(all, s.maxVisible);
    const rMax = effectiveMaxRadius(s, view.barSpacing), hits = [];
    const buckets = 4, paths = { 1: [], '-1': [] };
    for (const side of ['1', '-1']) for (let b = 0; b < buckets; b += 1) paths[side].push([]);
    for (const p of chosen) {
      const x = view.xForMs(p.time), y = view.y(p.price);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      const r = B.radius(p.notional, p.threshold, s.rMin, rMax);
      if (x + r < 0 || x - r > view.plotWidth || y + r < 0 || y - r > view.plotHeight) continue;
      const t = rMax > s.rMin ? (r - s.rMin) / (rMax - s.rMin) : 1;
      const bucket = Math.min(buckets - 1, Math.floor(t * buckets));
      paths[String(p.side)][bucket].push([x, y, r]);
      hits.push({ x, y, r, p });
    }
    const TAU = Math.PI * 2;
    ctx.save();
    for (let b = 0; b < buckets; b += 1) {
      const alpha = s.minOpacity + (s.maxOpacity - s.minOpacity) * (b + 0.5) / buckets;
      for (const side of ['1', '-1']) {
        const circles = paths[side][b];
        if (!circles.length) continue;
        const color = side === '1' ? s.buyColor : s.sellColor;
        ctx.beginPath();
        for (const [x, y, r] of circles) { ctx.moveTo(x + r, y); ctx.arc(x, y, r, 0, TAU); }
        // Non-colour cue: buys are solid discs, sells are rings with a faint fill.
        ctx.fillStyle = rgba(color, side === '1' ? alpha : alpha * 0.18); ctx.fill();
        ctx.lineWidth = side === '1' ? 1 : 1.8; ctx.strokeStyle = rgba(color, Math.min(1, alpha + 0.25)); ctx.stroke();
      }
    }
    if (s.glyphs) {
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; let glyphs = 0;
      for (let i = hits.length - 1; i >= 0 && glyphs < 300; i -= 1) {
        const { x, y, r, p } = hits[i];
        if (r < 7) continue;
        ctx.font = `${Math.min(14, Math.round(r * 0.9))}px sans-serif`;
        ctx.fillStyle = p.side === 1 ? 'rgba(255,255,255,0.9)' : rgba(s.sellColor, 0.95);
        ctx.fillText(p.side === 1 ? '▲' : '▼', x, y + 0.5); glyphs += 1;
      }
    }
    ctx.restore();
    return { hits, drawn: hits.length, inRange: all.length, capped: all.length > chosen.length };
  }
  function hitTest(hits, x, y) {
    for (let i = hits.length - 1; i >= 0; i -= 1) {
      const h = hits[i], dx = x - h.x, dy = y - h.y;
      if (dx * dx + dy * dy <= (h.r + 2) ** 2) return h.p;
    }
    return null;
  }
  const iso = ms => new Date(ms).toISOString().replace('T', ' ').replace('Z', ' UTC');
  function tooltipText(p, { format = v => String(v), settings = {}, tick = null } = {}) {
    const s = { ...B.defaults(), ...settings };
    const venue = B.VENUE_LABEL[p.venue] || p.venue, unit = B.UNIT_LABEL[p.venue] || 'trades';
    const lines = [
      `${p.side === 1 ? '▲ Taker BUY (lifted the ask)' : '▼ Taker SELL (hit the bid)'} · ${venue}`,
      `Size ${+p.qty.toPrecision(8)} · notional $${B.compact(p.notional)} (${Math.round(p.notional).toLocaleString('en-US')} USD quote)`,
      `Price ${format(p.price)}${p.trades > 1 ? ` VWAP (range ${format(p.low)} – ${format(p.high)})` : ''}`,
      `Time ${iso(p.time)}${p.lastTime > p.time ? ` → +${p.lastTime - p.time} ms` : ''}`,
      p.trades > 1
        ? `Aggregated from ${p.trades} ${unit} (heuristic: same side within ${s.aggWindowMs} ms and ±${s.aggTicks} tick${tick ? ` of ${tick}` : ''}; may merge different takers)`
        : `Single ${p.venue === 'binance' ? 'aggTrade (one taker order at one price, exchange-aggregated)' : 'trade'}`,
      `Threshold then $${B.compact(p.threshold)} · ${p.basis}${p.basis === 'adaptive' ? ` (p${s.percentile} of ${p.samples} prints, last ${s.windowMin} min)` : ''} · ${p.source}`,
      `ids ${p.firstId}${p.lastId !== p.firstId ? ` … ${p.lastId}` : ''}`
    ];
    return lines.join('\n');
  }

  function createController(options) {
    const { fetch: fetchImpl, hub, onChange = () => {}, now = Date.now } = options;
    const every = options.setInterval || setInterval, stopEvery = options.clearInterval || clearInterval;
    let store = null, config = null, key = null, off = null, timer = null, generation = 0, status = { state: 'off', message: '' };
    async function backfill(g) {
      const q = new URLSearchParams({ exchange: config.exchange, symbol: config.symbol, minutes: String(config.settings.backfillMin), max_requests: String(config.settings.budget) });
      try {
        const response = await fetchImpl(`/api/trades/recent?${q}`, { cache: 'no-store', headers: { Accept: 'application/json' } });
        const payload = await response.json();
        if (g !== generation) return;
        if (!response.ok) throw new Error(payload?.error || `HTTP ${response.status}`);
        if (payload.exchange !== config.exchange || payload.symbol !== config.symbol) throw new Error('trades response for another market');
        store.loadBackfill(payload); status = { state: 'ready', message: '' };
      } catch (error) {
        if (g !== generation) return;
        store.loadBackfill({ trades: [], coverage: 'none', oldest_ms: null, as_of: now(), last_trade_id: null, warnings: [] });
        status = { state: 'live-only', message: error?.message || 'backfill unavailable' };
      }
      onChange();
    }
    function enable(next) {
      const k = JSON.stringify([next.exchange, next.symbol, next.tick, DATA_KEYS.map(x => next.settings[x])]);
      if (k === key) return;
      disable(); key = k; config = { ...next }; generation += 1;
      store = new B.BigTradeStore({ venue: next.exchange, settings: next.settings, tick: next.tick });
      status = { state: 'loading', message: '' };
      off = hub?.subscribeTrades?.({ venue: next.exchange, symbol: next.symbol }, event => {
        if (!store) return;
        if (event.type === 'status') { status = { ...status, stream: event.status }; return; }
        if (event.type !== 'trades') return;
        const before = store.version; store.onRows(event.rows); if (store.version !== before) onChange();
      }) || null;
      timer = every(() => { if (store && store.flushIdle(now() - 1000)) onChange(); }, 500);
      backfill(generation);
    }
    function disable() {
      generation += 1; off?.(); off = null; if (timer) stopEvery(timer); timer = null;
      store = null; config = null; key = null; status = { state: 'off', message: '' };
    }
    function statusText() {
      if (!store || status.state === 'off') return '';
      if (status.state === 'loading') return 'Big trades: loading real trades…';
      const bf = store.backfill, venue = B.VENUE_LABEL[config.exchange];
      const parts = [`Big trades · ${venue} ${config.exchange === 'binance' ? 'aggTrades' : 'trades'}`];
      if (status.state === 'live-only') parts.push(`backfill failed (${status.message}); live stream only`);
      else if (bf) parts.push(`${bf.coverage === 'exact' ? 'exact' : 'PARTIAL'} history since ${bf.oldestMs ? iso(bf.oldestMs).slice(11, 19) : '—'}${bf.truncated ? ' (budget-truncated)' : ''} + live`);
      const th = B.threshold(store.s, store.dist);
      parts.push(`threshold now ${th.value === null ? `— (percentile warming up: ${th.n}/${store.s.minSamples} prints)` : `$${B.compact(th.value)} (${th.basis})`}`);
      parts.push(`${store.big.length} stored`);
      if (!store.tick && store.s.aggregate) parts.push('tick size unknown: merging exact-price fills only');
      if (store.gaps) parts.push(`${store.gaps} live id gap(s)`);
      (bf?.warnings || []).forEach(w => parts.push(w));
      return parts.join(' · ');
    }
    return { enable, disable, statusText, get store() { return store; }, get status() { return status; }, get config() { return config; } };
  }
  return { draw, hitTest, tooltipText, createController, selectVisible, effectiveMaxRadius, DATA_KEYS };
});
