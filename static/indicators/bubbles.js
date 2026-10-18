/* Big-trade bubbles: large aggressive trades as circles at (trade time, price).
 * Real public trades only (Binance aggTrades exact backfill; Bybit/Hyperliquid partial
 * backfill) + the live trade stream. Pure model first, then canvas draw and a pane controller.
 * Side: 1 = taker buy (lifted the ask), -1 = taker sell (hit the bid).
 * Print aggregation (same side, short window, price tolerance) is a HEURISTIC: a big order
 * split into several fills is merged back, but unrelated takers can be merged too.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.WorkspaceBubbles = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const ID = 'big-trades';
  const VENUE_LABEL = { binance: 'Binance', bybit: 'Bybit', hyperliquid: 'Hyperliquid' };
  const UNIT_LABEL = { binance: 'aggTrades', bybit: 'trades', hyperliquid: 'trades' };
  const schema = [
    { key: 'mode', label: 'Large means', type: 'select', options: ['both', 'fixed', 'adaptive'], optionLabels: { both: 'Fixed USD AND top percentile', fixed: 'Fixed USD notional', adaptive: 'Top percentile (adaptive)' }, default: 'both', group: 'Threshold' },
    { key: 'minUsd', label: 'Min notional, USD', type: 'number', min: 100, max: 100000000, step: 100, default: 50000, group: 'Threshold' },
    { key: 'percentile', label: 'Percentile of print sizes', type: 'number', min: 50, max: 99.99, step: 0.01, default: 99.5, group: 'Threshold' },
    { key: 'windowMin', label: 'Percentile window, minutes', type: 'number', min: 1, max: 240, step: 1, default: 30, group: 'Threshold' },
    { key: 'minSamples', label: 'Min prints before percentile is used', type: 'number', min: 20, max: 100000, step: 1, default: 300, group: 'Threshold' },
    { key: 'aggregate', label: 'Merge split fills into one print (heuristic)', type: 'boolean', default: true, group: 'Aggregation' },
    { key: 'aggWindowMs', label: 'Merge window, ms', type: 'number', min: 0, max: 2000, step: 1, default: 150, group: 'Aggregation' },
    { key: 'aggTicks', label: 'Merge price tolerance, ticks', type: 'number', min: 0, max: 1000, step: 1, default: 2, group: 'Aggregation' },
    { key: 'backfillMin', label: 'Backfill, minutes', type: 'number', min: 1, max: 60, step: 1, default: 15, group: 'History' },
    { key: 'budget', label: 'Backfill request budget (Binance)', type: 'number', min: 1, max: 20, step: 1, default: 10, group: 'History' },
    { key: 'maxStored', label: 'Max stored bubbles per chart', type: 'number', min: 100, max: 20000, step: 1, default: 5000, group: 'History' },
    { key: 'rMin', label: 'Min radius, px', type: 'number', min: 1, max: 20, step: 1, default: 3, group: 'Display' },
    { key: 'rMax', label: 'Max radius, px', type: 'number', min: 4, max: 80, step: 1, default: 26, group: 'Display' },
    { key: 'maxVisible', label: 'Max bubbles drawn (largest first)', type: 'number', min: 10, max: 10000, step: 1, default: 1500, group: 'Display' },
    { key: 'minOpacity', label: 'Opacity (smallest)', type: 'number', min: 0.05, max: 1, step: 0.05, default: 0.3, group: 'Display' },
    { key: 'maxOpacity', label: 'Opacity (largest)', type: 'number', min: 0.05, max: 1, step: 0.05, default: 0.75, group: 'Display' },
    { key: 'fitBar', label: 'Shrink to bar width when zoomed out', type: 'boolean', default: true, group: 'Display' },
    { key: 'glyphs', label: 'Show ▲/▼ inside larger bubbles', type: 'boolean', default: true, group: 'Display' },
    { key: 'buyColor', label: 'Taker buy colour (filled)', type: 'color', default: '#2ea8ff', group: 'Display' },
    { key: 'sellColor', label: 'Taker sell colour (ring)', type: 'color', default: '#ff5c8a', group: 'Display' }
  ];
  const defaults = () => Object.fromEntries(schema.map(f => [f.key, f.default]));
  const definition = { id: ID, label: 'Big trades', kind: 'bubbles', defaultEnabled: false, settingsSchema: schema,
    labelForSettings: s => `Big trades ${s.mode === 'adaptive' ? `p${s.percentile}` : s.mode === 'fixed' ? `≥$${compact(s.minUsd)}` : `≥$${compact(s.minUsd)}·p${s.percentile}`}` };

  function compact(v) {
    const a = Math.abs(v);
    if (a >= 1e9) return `${+(v / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${+(v / 1e6).toFixed(2)}M`;
    if (a >= 1e3) return `${+(v / 1e3).toFixed(1)}K`;
    return `${+v.toFixed(2)}`;
  }

  // ── trade normalisation (REST compact rows and WS payloads) ──────────────
  function tradeFromRow(row) {
    const [id, time, price, qty, side] = row;
    const p = Number(price), q = Number(qty);
    if (!(p > 0) || !(q > 0) || !Number.isFinite(time) || (side !== 1 && side !== -1)) return null;
    return { id, time, price: p, qty: q, side, notional: p * q };
  }
  function tradeFromWs(venue, r) {
    let row = null;
    if (venue === 'binance' && typeof r?.m === 'boolean') row = [Number(r.a), Number(r.T), r.p, r.q, r.m ? -1 : 1];
    else if (venue === 'bybit' && (r?.S === 'Buy' || r?.S === 'Sell')) row = [String(r.i), Number(r.T), r.p, r.v, r.S === 'Buy' ? 1 : -1];
    else if (venue === 'hyperliquid' && (r?.side === 'B' || r?.side === 'A')) row = [String(r.tid), Number(r.time), r.px, r.sz, r.side === 'B' ? 1 : -1];
    return row ? tradeFromRow(row) : null;
  }

  // ── print aggregation (heuristic) ────────────────────────────────────────
  // A print opens with a trade; later SAME-side trades join while time − firstTime ≤ windowMs
  // and |price − firstPrice| ≤ tolerance. Each side has its own open print, so interleaved
  // opposite-side trades do not split it. windowMs = 0 → every trade is its own print.
  class PrintAggregator {
    constructor({ windowMs = 0, tolerance = 0 } = {}) { this.windowMs = Math.max(0, windowMs); this.tolerance = Math.max(0, tolerance); this.open = { 1: null, '-1': null }; }
    static start(t) {
      return { side: t.side, time: t.time, lastTime: t.time, firstPrice: t.price, low: t.price, high: t.price, qty: t.qty, notional: t.notional,
        trades: 1, firstId: t.id, lastId: t.id, ids: [t.id], fromBackfill: Boolean(t.fromBackfill) };
    }
    static join(p, t) {
      p.qty += t.qty; p.notional += t.notional; p.trades += 1; p.lastTime = Math.max(p.lastTime, t.time); p.lastId = t.id;
      p.low = Math.min(p.low, t.price); p.high = Math.max(p.high, t.price); if (p.ids.length < 64) p.ids.push(t.id);
    }
    static finish(p) { p.price = p.notional / p.qty; return p; }
    push(t) {
      const out = this.flush(t.time);
      if (!this.windowMs) { out.push(PrintAggregator.finish(PrintAggregator.start(t))); return out; }
      const key = String(t.side), p = this.open[key];
      if (p && t.time - p.time <= this.windowMs && Math.abs(t.price - p.firstPrice) <= this.tolerance + 1e-12 * t.price) PrintAggregator.join(p, t);
      else { if (p) out.push(PrintAggregator.finish(p)); this.open[key] = PrintAggregator.start(t); }
      return out;
    }
    flush(nowMs, all = false) {
      const out = [];
      for (const key of ['1', '-1']) {
        const p = this.open[key];
        if (p && (all || nowMs - p.time > this.windowMs)) { out.push(PrintAggregator.finish(p)); this.open[key] = null; }
      }
      return out.sort((a, b) => a.time - b.time);
    }
  }

  // ── rolling print-size distribution for the adaptive threshold ────────────
  class RollingDistribution {
    constructor({ windowMs, maxSamples = 200000 }) { this.windowMs = windowMs; this.maxSamples = maxSamples; this.queue = []; this.head = 0; this.sorted = []; }
    get size() { return this.sorted.length; }
    static lowerBound(arr, v) { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >>> 1; if (arr[m] < v) lo = m + 1; else hi = m; } return lo; }
    add(time, value) {
      this.expire(time);
      if (this.sorted.length >= this.maxSamples) this.removeOldest();
      this.queue.push({ time, value }); this.sorted.splice(RollingDistribution.lowerBound(this.sorted, value), 0, value);
    }
    removeOldest() {
      const old = this.queue[this.head++]; this.sorted.splice(RollingDistribution.lowerBound(this.sorted, old.value), 1);
      if (this.head > 4096 && this.head * 2 > this.queue.length) { this.queue = this.queue.slice(this.head); this.head = 0; }
    }
    expire(now) { while (this.head < this.queue.length && this.queue[this.head].time < now - this.windowMs) this.removeOldest(); }
    // Nearest-rank percentile: smallest value with at least p% of samples ≤ it.
    quantile(pct) { const n = this.sorted.length; if (!n) return null; return this.sorted[Math.min(n - 1, Math.max(0, Math.ceil(pct / 100 * n) - 1))]; }
  }
  function threshold(settings, dist) {
    const fixed = Number(settings.minUsd), n = dist ? dist.size : 0;
    const ready = n >= settings.minSamples;
    const adaptive = ready ? dist.quantile(settings.percentile) : null;
    if (settings.mode === 'fixed') return { value: fixed, basis: 'fixed', n };
    if (settings.mode === 'adaptive') return { value: adaptive, basis: ready ? 'adaptive' : 'warming', n };
    return { value: Math.max(fixed, adaptive ?? fixed), basis: ready ? (adaptive > fixed ? 'adaptive' : 'fixed') : 'fixed (percentile warming up)', n, adaptive };
  }
  // Area ∝ notional: r = rMin·sqrt(notional / reference), clamped to [rMin, rMax].
  function radius(notional, reference, rMin, rMax) {
    if (!(notional > 0) || !(reference > 0)) return rMin;
    return Math.min(rMax, Math.max(rMin, rMin * Math.sqrt(notional / reference)));
  }

  // ── per-pane store: backfill + live stream, bounded memory ───────────────
  class BigTradeStore {
    constructor({ venue, settings, tick = null }) {
      this.venue = venue; this.s = { ...defaults(), ...settings }; this.tick = tick > 0 ? tick : null;
      const tolerance = this.tick ? this.s.aggTicks * this.tick : 0;
      this.agg = new PrintAggregator({ windowMs: this.s.aggregate ? this.s.aggWindowMs : 0, tolerance });
      this.dist = new RollingDistribution({ windowMs: this.s.windowMin * 60000 });
      this.big = []; this.pending = []; this.ready = false; this.backfill = null; this.nextId = null; this.seenIds = new Set();
      this.gaps = 0; this.lastTime = 0; this.version = 0; this.prints = 0; this.trades = 0; this.errors = 0; this.dropped = 0;
    }
    loadBackfill(payload) {
      this.backfill = { coverage: payload.coverage, oldestMs: payload.oldest_ms, asOf: payload.as_of, truncated: payload.truncated, count: payload.count, firstId: payload.trades?.[0]?.[0] ?? null, lastId: payload.last_trade_id ?? null, warnings: payload.warnings || [] };
      for (const row of payload.trades || []) { const t = tradeFromRow(row); if (t) this.accept(t, true); else this.errors += 1; }
      this.nextId = this.venue === 'binance' && payload.last_trade_id !== null ? payload.last_trade_id + 1 : null;
      this.ready = true;
      const buffered = this.pending.sort((a, b) => a.time - b.time); this.pending = [];
      for (const t of buffered) this.onTrade(t);
    }
    onTrade(t) {
      if (!this.ready) { if (this.pending.length < 50000) this.pending.push(t); return; }
      if (this.venue === 'binance') {
        if (this.nextId !== null && t.id < this.nextId) return; // already in the backfill
        if (this.nextId !== null && t.id > this.nextId) this.gaps += 1;
        this.nextId = t.id + 1;
      } else if (this.seenIds.has(t.id) || (this.backfill?.asOf && t.time < this.backfill.asOf - 10000)) return;
      this.accept(t, false);
    }
    onRows(rows) { for (const r of rows) { const t = tradeFromWs(this.venue, r); if (t) this.onTrade(t); else this.errors += 1; } }
    accept(t, fromBackfill) {
      t.fromBackfill = fromBackfill; this.trades += 1; this.lastTime = Math.max(this.lastTime, t.time);
      if (this.venue !== 'binance') { this.seenIds.add(t.id); if (this.seenIds.size > 20000) this.seenIds = new Set([...this.seenIds].slice(-10000)); }
      for (const p of this.agg.push(t)) this.classify(p);
    }
    flushIdle(nowMs) { let n = 0; for (const p of this.agg.flush(nowMs)) n += this.classify(p); return n; }
    classify(p) {
      const th = threshold(this.s, this.dist); // causal: distribution BEFORE this print
      this.dist.add(p.time, p.notional); this.prints += 1;
      if (th.value === null || !(p.notional >= th.value)) return 0;
      Object.assign(p, { threshold: th.value, basis: th.basis, samples: th.n, source: p.fromBackfill ? 'backfill' : 'live', venue: this.venue });
      let i = this.big.length; while (i > 0 && this.big[i - 1].time > p.time) i -= 1;
      this.big.splice(i, 0, p);
      if (this.big.length > this.s.maxStored) { this.big.splice(0, this.big.length - this.s.maxStored); this.dropped += 1; }
      this.version += 1; return 1;
    }
    range(fromMs, toMs) {
      const lb = v => { let lo = 0, hi = this.big.length; while (lo < hi) { const m = (lo + hi) >>> 1; if (this.big[m].time < v) lo = m + 1; else hi = m; } return lo; };
      return this.big.slice(lb(fromMs), lb(toMs + 1));
    }
    // Per-bar summary for other consumers (counts and net notional of big prints).
    barSummary(openMs, intervalMs) {
      const list = this.range(openMs, openMs + intervalMs - 1);
      return { count: list.length, buys: list.filter(p => p.side === 1).length, sells: list.filter(p => p.side === -1).length,
        net: list.reduce((a, p) => a + p.side * p.notional, 0) };
    }
  }

  return { ID, definition, schema, defaults, compact, tradeFromRow, tradeFromWs, PrintAggregator, RollingDistribution, threshold, radius, BigTradeStore, VENUE_LABEL, UNIT_LABEL };
});
