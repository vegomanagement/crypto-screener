/* Live L2 order book engine for the view-only scalping page (/scalp).
 * Pure bookkeeping (no DOM): venue sync rules, price grouping, $-notional, density lifetime,
 * prints-feed aggregation and cluster rows on top of footprint.js. Public market data only;
 * no level, quantity or trade is ever synthesized — unknown depth is reported, not filled in.
 *
 * Protocol references (checked 2026-10-17):
 *  - Binance USD-M "How to manage a local order book correctly": buffer <sym>@depth events,
 *    REST /fapi/v1/depth?limit=1000 snapshot, drop events with u < lastUpdateId, the first
 *    processed event must have U <= lastUpdateId AND u >= lastUpdateId, every next event's pu
 *    must equal the previous u, otherwise re-initialise from the snapshot. Quantities are
 *    absolute, 0 removes the level. High-frequency streams live under fstream.binance.com/public.
 *  - Bybit v5 orderbook.{depth}.{symbol}: snapshot then deltas; a new snapshot resets the book;
 *    u=1 is a snapshot after a service restart; size 0 deletes the level.
 *  - Hyperliquid l2Book: every message is a full snapshot (≤ 20 levels per side).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./footprint.js'));
  else root.ScalpOrderBook = factory(root.WorkspaceFootprint);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (footprint) {
  'use strict';

  // ── numbers ────────────────────────────────────────────────────────────────
  function num(value) {
    const n = typeof value === 'number' ? value : typeof value === 'string' && value.trim() !== '' ? Number(value) : NaN;
    return Number.isFinite(n) ? n : NaN;
  }
  /** Integer tick index of a price. Throws on off-grid prices instead of silently merging levels. */
  function priceIndex(price, tick) {
    const p = num(price);
    if (!(p > 0) || !(tick > 0)) throw new Error('invalid price');
    const raw = p / tick, idx = Math.round(raw);
    // Tolerance covers float division error only (0.3 / 0.1 = 2.9999999999999996), never half a tick.
    if (Math.abs(raw - idx) > Math.min(1e-3, 1e-9 * Math.max(1, Math.abs(raw)))) throw new Error(`price ${price} not on tick ${tick}`);
    return idx;
  }
  const notional = (price, qty) => price * qty; // linear USDT/USDC contracts: base qty × price
  function abbreviate(value, digits = 1) {
    const n = Math.abs(value), sign = value < 0 ? '-' : '';
    if (!Number.isFinite(value)) return '—';
    for (const [unit, size] of [['B', 1e9], ['M', 1e6], ['K', 1e3]]) {
      if (n >= size) return `${sign}${(n / size).toFixed(n / size >= 100 ? 0 : digits)}${unit}`;
    }
    if (n >= 100) return `${sign}${n.toFixed(0)}`;
    if (n >= 1) return `${sign}${n.toFixed(Math.min(2, digits + 1))}`;
    return n === 0 ? '0' : `${sign}${n.toPrecision(2)}`;
  }
  function decimalsOf(tick) {
    const text = String(tick);
    if (/e-/i.test(text)) return Number(text.split(/e-/i)[1]);
    return text.includes('.') ? text.split('.')[1].replace(/0+$/, '').length : 0;
  }
  /** Hyperliquid perp tick: ≤ 5 significant figures and ≤ (6 − szDecimals) decimals, capped at 1. */
  function hyperliquidTick(price, szDecimals) {
    if (!(price > 0) || !Number.isInteger(szDecimals) || szDecimals < 0) throw new Error('invalid hyperliquid tick input');
    const significant = 10 ** (Math.floor(Math.log10(price)) - 4);
    const decimals = 10 ** -(6 - szDecimals);
    return Number(Math.min(1, Math.max(significant, decimals)).toPrecision(12));
  }

  // ── book ───────────────────────────────────────────────────────────────────
  class Book {
    /**
     * `bids`/`asks` hold the total resting size per tick index. Venues can publish levels off their own
     * tick grid (observed live: Binance SOLUSDT, tickSize 0.0100, level at 105.0020). Such a level is kept
     * at its exact price in `off` and counted in the tick that contains it ([idx·tick, (idx+1)·tick)), so
     * a later qty 0 for that exact price removes exactly that size and nothing is merged or dropped.
     */
    constructor(tick) {
      if (!(tick > 0)) throw new Error('tick must be positive');
      this.tick = tick; this.bids = new Map(); this.asks = new Map(); this.version = 0;
      this.on = { bid: new Map(), ask: new Map() }; this.off = { bid: new Map(), ask: new Map() };
      this._best = null; this.coverage = { bidFloor: null, askCeil: null };
    }
    clear() {
      this.bids.clear(); this.asks.clear(); for (const m of [this.on.bid, this.on.ask, this.off.bid, this.off.ask]) m.clear();
      this._best = null; this.version += 1; this.coverage = { bidFloor: null, askCeil: null };
    }
    get offGridLevels() { let n = 0; for (const m of [this.off.bid, this.off.ask]) for (const cell of m.values()) n += cell.size; return n; }
    _put(side, price, q) {
      const p = num(price);
      if (!(p > 0)) throw new Error('invalid price');
      const raw = p / this.tick, near = Math.round(raw);
      const onGrid = Math.abs(raw - near) <= Math.min(1e-3, 1e-9 * Math.max(1, Math.abs(raw)));
      const idx = onGrid ? near : Math.floor(raw);
      if (onGrid) { if (q === 0) this.on[side].delete(idx); else this.on[side].set(idx, q); }
      else {
        const cells = this.off[side]; let cell = cells.get(idx);
        if (q === 0) { if (cell) { cell.delete(p); if (!cell.size) cells.delete(idx); } }
        else { if (!cell) { cell = new Map(); cells.set(idx, cell); } cell.set(p, q); }
      }
      let total = this.on[side].get(idx) || 0;
      const cell = this.off[side].get(idx);
      if (cell) for (const v of cell.values()) total += v;
      const map = side === 'bid' ? this.bids : this.asks;
      if (total > 0) map.set(idx, total); else map.delete(idx);
    }
    set(side, price, qty) {
      const q = num(qty);
      if (!(q >= 0)) throw new Error('invalid quantity');
      this._put(side, price, q);
      this._best = null; this.version += 1;
    }
    load(bids, asks) {
      this.bids.clear(); this.asks.clear(); for (const m of [this.on.bid, this.on.ask, this.off.bid, this.off.ask]) m.clear();
      for (const [side, rows] of [['bid', bids], ['ask', asks]]) for (const [p, q] of rows) {
        const n = num(q);
        if (!(n >= 0)) throw new Error('invalid quantity');
        if (n > 0) this._put(side, p, n);
      }
      this._best = null; this.version += 1;
    }
    best() {
      if (this._best) return this._best;
      let bid = -Infinity, ask = Infinity, bidMin = Infinity, askMax = -Infinity;
      for (const idx of this.bids.keys()) { if (idx > bid) bid = idx; if (idx < bidMin) bidMin = idx; }
      for (const idx of this.asks.keys()) { if (idx < ask) ask = idx; if (idx > askMax) askMax = idx; }
      this._best = { bid: Number.isFinite(bid) ? bid : null, ask: Number.isFinite(ask) ? ask : null,
        deepestBid: Number.isFinite(bidMin) ? bidMin : null, deepestAsk: Number.isFinite(askMax) ? askMax : null };
      return this._best;
    }
    crossed() {
      const b = this.best();
      if (b.bid === null || b.ask === null || b.bid < b.ask) return false;
      if (b.bid > b.ask) return true;
      // Same tick index: only off-grid levels can share it, so compare their exact prices.
      const exact = (side, pick) => {
        const out = []; if (this.on[side].has(b.bid)) out.push(b.bid * this.tick);
        for (const p of this.off[side].get(b.bid)?.keys() || []) out.push(p);
        return pick(...out);
      };
      return exact('bid', Math.max) >= exact('ask', Math.min) - this.tick * 1e-9;
    }
    qty(side, idx) { return (side === 'bid' ? this.bids : this.asks).get(idx) || 0; }
    /** Top N venue levels per side as [exact price, qty]; off-grid levels keep their own price. */
    top(n) {
      const d = decimalsOf(this.tick), px = i => Number((i * this.tick).toFixed(d));
      const levels = side => {
        const out = [...this.on[side]].map(([i, q]) => [px(i), q]);
        for (const cell of this.off[side].values()) for (const [p, q] of cell) out.push([p, q]);
        return out;
      };
      const bids = levels('bid').sort((a, b) => b[0] - a[0]).slice(0, n);
      const asks = levels('ask').sort((a, b) => a[0] - b[0]).slice(0, n);
      return { bids, asks };
    }
    /** Is this tick index inside the depth window the venue actually gave us? */
    covered(idx) {
      const c = this.coverage;
      return c.bidFloor !== null && c.askCeil !== null && idx >= c.bidFloor && idx <= c.askCeil;
    }
  }

  // ── venue sync engines (pure: messages in, actions out) ───────────────────
  /** Binance USD-M diff-depth sync. Returns { resync: reason } when a new REST snapshot is needed. */
  class BinanceSync {
    constructor(symbol, tick, { maxBuffer = 2000 } = {}) {
      this.symbol = symbol; this.book = new Book(tick); this.maxBuffer = maxBuffer;
      this.state = 'buffering'; this.buffer = []; this.lastU = null; this.snapshotId = null; this.awaitFirst = false;
      this.stats = { events: 0, applied: 0, dropped: 0, gaps: 0, resyncs: 0, crossed: 0, lastEventTime: null, reanchors: 0 };
      this.lastGap = null;
    }
    valid(ev) { return ev && ev.e === 'depthUpdate' && ev.s === this.symbol && Number.isSafeInteger(ev.U) && Number.isSafeInteger(ev.u) && Array.isArray(ev.b) && Array.isArray(ev.a); }
    onEvent(ev) {
      if (!this.valid(ev)) return null;
      this.stats.events += 1; this.stats.lastEventTime = ev.E ?? null;
      if (this.state !== 'synced' && !this.awaitFirst) {
        if (this.buffer.length >= this.maxBuffer) this.buffer.shift();
        this.buffer.push(ev); return null;
      }
      if (this.awaitFirst) return this.first(ev);
      if (ev.pu !== this.lastU) {
        this.lastGap = { expected: this.lastU, pu: ev.pu, u: ev.u };
        this.stats.gaps += 1; return this.restart('gap', ev);
      }
      return this.apply(ev);
    }
    first(ev) {
      const L = this.snapshotId;
      if (ev.u < L) { this.stats.dropped += 1; return null; }
      if (ev.U <= L && ev.u >= L) { this.awaitFirst = false; this.state = 'synced'; return this.apply(ev); }
      return this.restart('snapshot-behind', ev); // events already past the snapshot: an update range is missing
    }
    apply(ev) {
      try {
        for (const [p, q] of ev.b) this.book.set('bid', p, q);
        for (const [p, q] of ev.a) this.book.set('ask', p, q);
      } catch (_) { this.stats.invalid = (this.stats.invalid || 0) + 1; return this.restart('invalid-level'); }
      this.lastU = ev.u; this.stats.applied += 1;
      if (this.book.crossed()) { this.stats.crossed += 1; return this.restart('crossed'); }
      const best = this.book.best(), c = this.book.coverage;
      // The REST snapshot is the only proof of resting size for untouched levels: re-anchor when the
      // touch leaves the snapshot window instead of showing unverifiable depth as complete.
      if ((best.bid !== null && best.bid < c.bidFloor) || (best.ask !== null && best.ask > c.askCeil)) {
        this.stats.reanchors += 1; return this.restart('left-snapshot-window');
      }
      return { applied: true };
    }
    restart(reason, ev) {
      this.stats.resyncs += 1; this.state = 'resyncing'; this.awaitFirst = false; this.lastU = null; this.snapshotId = null;
      this.buffer = ev ? [ev] : [];
      return { resync: reason };
    }
    onSnapshot(snap) {
      if (!snap || !Number.isSafeInteger(snap.lastUpdateId) || !Array.isArray(snap.bids) || !Array.isArray(snap.asks)) throw new Error('invalid depth snapshot');
      this.book.load(snap.bids, snap.asks);
      const b = this.book.best();
      this.book.coverage = { bidFloor: b.deepestBid, askCeil: b.deepestAsk };
      this.snapshotId = snap.lastUpdateId; this.awaitFirst = true; this.state = 'syncing';
      const pending = this.buffer; this.buffer = [];
      for (let k = 0; k < pending.length; k += 1) {
        const out = this.onEvent(pending[k]);
        if (out?.resync) { this.buffer = pending.slice(k); return out; } // keep the tail for the next snapshot
      }
      return { loaded: true };
    }
  }

  /** Bybit v5 orderbook.N.SYMBOL snapshot/delta. Returns { resync } on a sequence gap. */
  class BybitSync {
    constructor(symbol, tick, depth = 200) {
      this.symbol = symbol; this.depth = depth; this.topic = `orderbook.${depth}.${symbol}`; this.book = new Book(tick);
      this.state = 'syncing'; this.lastU = null; this.lastSeq = null; this.cts = null;
      this.stats = { events: 0, applied: 0, snapshots: 0, gaps: 0, resyncs: 0, crossed: 0, restarts: 0, lastEventTime: null };
    }
    onMessage(msg) {
      if (!msg || msg.topic !== this.topic || !msg.data || msg.data.s !== this.symbol) return null;
      const d = msg.data, u = d.u;
      if (!Number.isSafeInteger(u) || !Array.isArray(d.b) || !Array.isArray(d.a)) return null;
      this.stats.events += 1; this.stats.lastEventTime = msg.ts ?? null;
      if (msg.type === 'snapshot' || u === 1) {
        if (u === 1 && msg.type !== 'snapshot') this.stats.restarts += 1;
        try { this.book.load(d.b, d.a); } catch (_) { return this.restart('invalid-level'); }
        this.stats.snapshots += 1;
      } else if (msg.type === 'delta') {
        if (this.state !== 'synced') return null; // no base snapshot yet
        if (u !== this.lastU + 1) { this.stats.gaps += 1; return this.restart('gap'); }
        try {
          for (const [p, q] of d.b) this.book.set('bid', p, q);
          for (const [p, q] of d.a) this.book.set('ask', p, q);
        } catch (_) { return this.restart('invalid-level'); }
      } else return null;
      this.lastU = u; this.lastSeq = d.seq ?? null; this.cts = msg.cts ?? null; this.state = 'synced'; this.stats.applied += 1;
      const b = this.book.best();
      this.book.coverage = { bidFloor: b.deepestBid, askCeil: b.deepestAsk }; // the venue keeps exactly this window
      if (this.book.crossed()) { this.stats.crossed += 1; return this.restart('crossed'); }
      return { applied: true };
    }
    restart(reason) { this.stats.resyncs += 1; this.state = 'resyncing'; this.lastU = null; this.book.clear(); return { resync: reason }; }
  }

  /** Hyperliquid l2Book: full snapshots, depth-capped by the venue. */
  class HyperliquidSync {
    constructor(coin, tick) {
      this.symbol = coin; this.book = new Book(tick); this.state = 'syncing'; this.depthCapped = true; this.time = null;
      this.stats = { events: 0, applied: 0, snapshots: 0, gaps: 0, resyncs: 0, crossed: 0, stale: 0, lastEventTime: null, levels: [0, 0] };
    }
    onMessage(msg) {
      if (!msg || msg.channel !== 'l2Book' || !msg.data || msg.data.coin !== this.symbol) return null;
      const { levels, time } = msg.data;
      if (!Array.isArray(levels) || levels.length !== 2 || !levels.every(Array.isArray)) return null;
      this.stats.events += 1; this.stats.lastEventTime = time ?? null;
      if (this.time !== null && Number.isFinite(time) && time < this.time) { this.stats.stale += 1; return null; }
      this.book.load(levels[0].map(l => [l.px, l.sz]), levels[1].map(l => [l.px, l.sz]));
      this.time = time ?? null; this.state = 'synced'; this.stats.snapshots += 1; this.stats.applied += 1;
      this.stats.levels = [levels[0].length, levels[1].length];
      const b = this.book.best();
      this.book.coverage = { bidFloor: b.deepestBid, askCeil: b.deepestAsk };
      if (this.book.crossed()) { this.stats.crossed += 1; this.state = 'crossed'; return { crossed: true }; }
      return { applied: true };
    }
  }

  // ── grouping ───────────────────────────────────────────────────────────────
  /** Row size in ticks for the ladder: ×N ticks, or a % of the reference price snapped to whole ticks. */
  function rowTicks({ mode = 'ticks', ticks = 1, percent = 0.01 }, refPrice, tick) {
    if (mode === 'percent') {
      if (!(refPrice > 0) || !(percent > 0)) return 1;
      return Math.max(1, Math.round((refPrice * percent / 100) / tick));
    }
    return Math.max(1, Math.floor(ticks));
  }
  /** Row (grouped) index of a tick index. Rows cover [row×N, row×N + N) ticks for both sides. */
  const rowOf = (idx, n) => Math.floor(idx / n);
  /** Aggregate quantity of a ladder row; ticks outside the venue's depth window are flagged. */
  function rowLevel(book, row, n) {
    let bid = 0, ask = 0, bidUsd = 0, askUsd = 0, covered = true;
    const tick = book.tick;
    for (let i = row * n; i < row * n + n; i += 1) {
      const b = book.bids.get(i), a = book.asks.get(i);
      if (b) { bid += b; bidUsd += b * i * tick; }
      if (a) { ask += a; askUsd += a * i * tick; }
      if (!book.covered(i)) covered = false;
    }
    return { bid, ask, bidUsd, askUsd, covered };
  }
  /** All non-empty grouped rows of one side (used for full-scale and density). */
  function groupSide(book, side, n) {
    const out = new Map(), map = side === 'bid' ? book.bids : book.asks;
    for (const [i, q] of map) {
      const r = rowOf(i, n), cur = out.get(r) || { qty: 0, usd: 0 };
      cur.qty += q; cur.usd += q * i * book.tick; out.set(r, cur);
    }
    return out;
  }

  // ── density lifetime ───────────────────────────────────────────────────────
  /** First-seen time of resting size ≥ threshold, keyed by exact price; "since this page observed it". */
  class DensityTracker {
    constructor() { this.since = new Map(); this.threshold = null; }
    update(book, thresholdUsd, now) {
      if (!(thresholdUsd > 0)) { this.since.clear(); return; }
      if (thresholdUsd !== this.threshold) { this.since.clear(); this.threshold = thresholdUsd; }
      const seen = new Set();
      for (const [side, map] of [['bid', book.bids], ['ask', book.asks]]) for (const [i, q] of map) {
        if (q * i * book.tick < thresholdUsd || !book.covered(i)) continue;
        const key = `${side}:${i}`; seen.add(key);
        if (!this.since.has(key)) this.since.set(key, now);
      }
      for (const key of this.since.keys()) if (!seen.has(key)) this.since.delete(key);
    }
    age(side, idx, now) { const t = this.since.get(`${side}:${idx}`); return t === undefined ? null : now - t; }
    /** Oldest tracked level inside a ladder row. */
    rowAge(side, row, n, now) {
      let best = null;
      for (let i = row * n; i < row * n + n; i += 1) { const a = this.age(side, i, now); if (a !== null && (best === null || a > best)) best = a; }
      return best;
    }
    get size() { return this.since.size; }
  }
  function formatAge(ms) {
    if (ms === null || ms === undefined) return '';
    const s = Math.floor(ms / 1000);
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}`;
    return `${Math.floor(s / 3600)}h${String(Math.floor(s / 60) % 60).padStart(2, '0')}`;
  }

  // ── prints (time & sales) ─────────────────────────────────────────────────
  /** Venue trade row → { time, price, qty, side: 'buy'|'sell' (taker), id, count }. */
  function normalizePrint(venue, row) {
    let out;
    if (venue === 'binance') {
      if (typeof row?.m !== 'boolean') return null;
      out = { time: num(row.T), price: num(row.p), qty: num(row.q), side: row.m ? 'sell' : 'buy', id: row.a, count: 1 };
    } else if (venue === 'bybit') {
      const side = row?.S ?? row?.side;
      if (side !== 'Buy' && side !== 'Sell') return null;
      out = { time: num(row.T ?? row.time), price: num(row.p ?? row.price), qty: num(row.v ?? row.size), side: side === 'Buy' ? 'buy' : 'sell', id: String(row.i ?? row.execId), count: 1 };
    } else if (venue === 'hyperliquid') {
      if (row?.side !== 'B' && row?.side !== 'A') return null;
      out = { time: num(row.time), price: num(row.px), qty: num(row.sz), side: row.side === 'B' ? 'buy' : 'sell', id: String(row.tid), count: 1 };
    } else return null;
    return out.time > 0 && out.price > 0 && out.qty > 0 ? out : null;
  }
  class PrintFeed {
    /** aggregateMs = 0 disables merging; aggregateTicks = 0 means "any price" while merging. */
    constructor({ tick, aggregateMs = 0, aggregateTicks = 0, maxPrints = 4000 } = {}) {
      this.tick = tick; this.aggregateMs = aggregateMs; this.aggregateTicks = aggregateTicks; this.maxPrints = maxPrints;
      this.prints = []; this.totals = { count: 0, qty: 0, usd: 0, rejected: 0 }; this.version = 0;
    }
    add(trade) {
      if (!trade || !(trade.qty > 0) || !(trade.price > 0)) { this.totals.rejected += 1; return null; }
      const usd = trade.price * trade.qty, count = trade.count || 1;
      this.totals.count += count; this.totals.qty += trade.qty; this.totals.usd += usd; this.version += 1;
      const last = this.prints.at(-1);
      if (last && this.aggregateMs > 0 && last.side === trade.side && trade.time - last.firstTime <= this.aggregateMs && trade.time >= last.lastTime
        && (this.aggregateTicks <= 0 || Math.abs(trade.price - last.firstPrice) <= this.aggregateTicks * this.tick + 1e-12)) {
        last.count += count; last.qty += trade.qty; last.usd += usd; last.lastTime = trade.time; last.price = trade.price;
        last.high = Math.max(last.high, trade.price); last.low = Math.min(last.low, trade.price); last.vwap = last.usd / last.qty;
        return last;
      }
      const print = { side: trade.side, firstTime: trade.time, lastTime: trade.time, firstPrice: trade.price, price: trade.price,
        high: trade.price, low: trade.price, vwap: trade.price, qty: trade.qty, usd, count };
      this.prints.push(print);
      if (this.prints.length > this.maxPrints) this.prints.splice(0, this.prints.length - this.maxPrints);
      return print;
    }
    /** Prints whose last trade falls inside [from, to] (ms). */
    window(from, to) { return this.prints.filter(p => p.lastTime >= from && p.lastTime <= to); }
    summary(from, to) {
      const list = this.window(from, to);
      return { prints: list.length, trades: list.reduce((a, p) => a + p.count, 0), qty: list.reduce((a, p) => a + p.qty, 0), usd: list.reduce((a, p) => a + p.usd, 0) };
    }
  }
  function printRadius(usd, { minRadius = 2, maxRadius = 18, maxSizeUsd = 250000 } = {}) {
    if (!(usd > 0)) return minRadius;
    return minRadius + (maxRadius - minRadius) * Math.min(1, Math.sqrt(usd / maxSizeUsd));
  }

  // ── clusters (exact footprint.js aggregation regrouped to ladder rows) ───
  /** Cluster rows of one footprint bar at `group` × bar-step, as plain numbers for drawing. */
  function clusterRows(bar, group) {
    if (!footprint) throw new Error('footprint.js is required for clusters');
    return footprint.groupedLevels(bar, group).map(cell => {
      const bid = Number(footprint.fmt(cell.bid));
      const ask = Number(footprint.fmt(cell.ask)), price = Number(footprint.fmt(cell.price));
      return { price, index: cell.index, bid, ask, volume: bid + ask, delta: ask - bid, trades: cell.bidTrades + cell.askTrades,
        exact: { price: footprint.fmt(cell.price), bid: footprint.fmt(cell.bid), ask: footprint.fmt(cell.ask) } };
    });
  }
  function clusterValue(row, mode, units) {
    const k = units === 'usd' ? row.price : 1;
    if (mode === 'delta') return row.delta * k;
    if (mode === 'bidask') return Math.max(row.bid, row.ask) * k;
    return row.volume * k;
  }

  // ── live stream: WS + (Binance) REST snapshot, resync and reconnect ────────
  const BOOK_URLS = {
    binance: symbol => `wss://fstream.binance.com/public/ws/${encodeURIComponent(symbol.toLowerCase())}@depth@100ms`,
    bybit: () => 'wss://stream.bybit.com/v5/public/linear',
    hyperliquid: () => 'wss://api.hyperliquid.xyz/ws'
  };
  const BINANCE_DEPTH = symbol => `https://fapi.binance.com/fapi/v1/depth?symbol=${encodeURIComponent(symbol)}&limit=1000`;
  function createBookStream(options) {
    const { venue, symbol, tick, onUpdate = () => {}, onStatus = () => {}, onApplied = null } = options;
    const WS = options.WebSocket || globalThis.WebSocket, fetchImpl = options.fetch || globalThis.fetch;
    const now = options.now || Date.now, later = options.setTimeout || setTimeout, cancel = options.clearTimeout || clearTimeout;
    const random = options.random || Math.random, staleMs = options.staleMs ?? 5000, deadMs = options.deadMs ?? 30000;
    if (!BOOK_URLS[venue]) throw new Error('unsupported venue');
    let sync = null, ws = null, generation = 0, attempt = 0, closed = false, status = 'connecting', lastMessage = 0;
    let retry = null, watchdog = null, heartbeat = null, snapshotTimer = null, snapshotInflight = false, snapshotFailures = 0;
    const counters = { connects: 0, snapshots: 0, snapshotErrors: 0, reconnects: 0, messages: 0 };
    function setStatus(value, detail) {
      if (value === status && !detail) return;
      status = value; onStatus({ status: value, detail: detail || null, at: now() });
    }
    function fresh() {
      return venue === 'binance' ? new BinanceSync(symbol, tick) : venue === 'bybit' ? new BybitSync(symbol, tick, 200) : new HyperliquidSync(symbol, tick);
    }
    function connect() {
      if (closed) return;
      const g = ++generation; counters.connects += 1;
      sync = fresh(); setStatus('connecting');
      let socket;
      const fail = () => {
        if (g !== generation || closed) return;
        generation += 1; cancel(heartbeat); cancel(snapshotTimer); snapshotInflight = false;
        try { socket?.close(); } catch (_) { /* already closed */ }
        ws = null; setStatus('disconnected'); counters.reconnects += 1;
        const delay = Math.min(30000, 1000 * 2 ** Math.min(attempt++, 5)) * (0.75 + random() * 0.25);
        retry = later(() => { retry = null; connect(); }, delay);
      };
      try { socket = new WS(BOOK_URLS[venue](symbol)); } catch (_) { fail(); return; }
      ws = socket;
      socket.onopen = () => {
        if (g !== generation) return;
        lastMessage = now();
        if (venue === 'bybit') socket.send(JSON.stringify({ op: 'subscribe', args: [`orderbook.200.${symbol}`] }));
        if (venue === 'hyperliquid') socket.send(JSON.stringify({ method: 'subscribe', subscription: { type: 'l2Book', coin: symbol } }));
        setStatus('syncing');
        if (venue === 'binance') snapshotTimer = later(snapshot, 250); // buffer a few events first (official step 2)
        const beat = () => {
          if (g !== generation) return;
          try {
            if (venue === 'bybit') socket.send(JSON.stringify({ op: 'ping' }));
            if (venue === 'hyperliquid') socket.send(JSON.stringify({ method: 'ping' }));
          } catch (_) { fail(); return; }
          heartbeat = later(beat, 20000);
        };
        heartbeat = later(beat, 20000);
      };
      socket.onmessage = message => {
        if (g !== generation) return;
        let data;
        try { data = JSON.parse(message.data); } catch (_) { return; }
        lastMessage = now(); counters.messages += 1;
        if (data?.success === false || data?.channel === 'error') { fail(); return; }
        const out = venue === 'binance' ? sync.onEvent(data) : sync.onMessage(data);
        if (!out) return;
        if (out.applied) {
          attempt = 0; snapshotFailures = 0;
          setStatus('synced');
          onApplied?.(data, sync); onUpdate(sync);
        }
        if (out.crossed) { setStatus('crossed'); onUpdate(sync); }
        if (out.resync) {
          setStatus('resyncing', out.resync); onUpdate(sync);
          if (venue === 'binance') scheduleSnapshot();
          else { attempt = 0; fail(); } // Bybit: a fresh subscription restarts from a venue snapshot
        }
      };
      socket.onclose = fail; socket.onerror = fail;
      async function snapshot() {
        snapshotTimer = null;
        if (g !== generation || snapshotInflight) return;
        snapshotInflight = true;
        try {
          const response = await fetchImpl(BINANCE_DEPTH(symbol), { cache: 'no-store' });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const json = await response.json();
          if (g !== generation) return;
          counters.snapshots += 1;
          const out = sync.onSnapshot(json);
          snapshotInflight = false;
          if (out?.resync) { setStatus('resyncing', out.resync); scheduleSnapshot(); }
          else if (sync.state === 'synced') { setStatus('synced'); onUpdate(sync); }
        } catch (error) {
          snapshotInflight = false;
          if (g !== generation) return;
          counters.snapshotErrors += 1; snapshotFailures += 1;
          setStatus('resyncing', 'snapshot-failed'); scheduleSnapshot();
        }
      }
      function scheduleSnapshot() {
        if (snapshotTimer || snapshotInflight) return;
        const delay = Math.min(30000, 1000 * 2 ** Math.min(snapshotFailures, 5));
        snapshotTimer = later(snapshot, delay);
      }
    }
    function watch() {
      if (closed) return;
      const quiet = now() - lastMessage;
      if (ws && lastMessage && quiet > deadMs) { const s = ws; ws = null; generation += 1; try { s.close(); } catch (_) { /* gone */ } connect(); }
      else if (lastMessage && quiet > staleMs && status === 'synced') setStatus('stale');
      watchdog = later(watch, 1000);
    }
    connect(); watchdog = later(watch, 1000);
    return {
      get book() { return sync?.book || null; }, get sync() { return sync; }, get status() { return status; },
      get lastMessage() { return lastMessage; }, counters,
      close() { closed = true; generation += 1; cancel(retry); cancel(watchdog); cancel(heartbeat); cancel(snapshotTimer); try { ws?.close(); } catch (_) { /* gone */ } ws = null; }
    };
  }

  return { num, priceIndex, notional, abbreviate, decimalsOf, hyperliquidTick, Book, BinanceSync, BybitSync, HyperliquidSync,
    rowTicks, rowOf, rowLevel, groupSide, DensityTracker, formatAge, normalizePrint, PrintFeed, printRadius, clusterRows, clusterValue,
    createBookStream, BOOK_URLS, BINANCE_DEPTH };
});
