/* Alerts for /scalp: signal-level evaluation, volume alerts (book + prints) and the notifications log.
 * Pure (no DOM, no network, no audio): the page feeds it live book/trade events, tests feed fixtures.
 * Every alert comes from an observed venue book change or trade; "pulled" is only claimed when no
 * observed trade at that price explains the drop. View only: nothing here can place or cancel orders.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./levels.js'));
  else root.ScalpAlerts = factory(root.ScreenerLevels);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (levels) {
  'use strict';
  /** Beep pitch(es) per alert type, Hz. Distinct so alerts can be told apart by ear. */
  const SOUNDS = { signal: [880, 1320, 880], appear: [660], decrease: [520], gone: [392], print: [990, 990] };
  const TYPE_LABELS = { signal: 'Signal level', appear: 'Large volume appeared', decrease: 'Large volume decreased', gone: 'Large volume gone', print: 'Large print' };

  // ── signal levels ─────────────────────────────────────────────────────────
  /** Exact venue price string of a raw trade row (Binance/Bybit `p`, Hyperliquid `px`), or null. */
  function tradePriceString(venue, row) {
    const raw = venue === 'hyperliquid' ? row?.px : venue === 'bybit' ? (row?.p ?? row?.price) : venue === 'binance' ? row?.p : null;
    if (typeof raw !== 'string') return null;
    const d = levels.parseDecimal(raw);
    return d && d.n > 0n ? raw.trim() : null;
  }
  /**
   * One live trade against the column's signal levels. Returns { trigger, rearm } (level lists):
   * armed levels crossed between prevPrice and price trigger; triggered repeat levels re-arm once the
   * price is ≥ 1 tick away from them. Decimal strings, exact compare (levels.js).
   */
  function evaluateSignals(list, prevPrice, price, tick) {
    const trigger = [], rearm = [];
    for (const level of list) {
      if (level.kind !== 'signal') continue;
      if (level.armed) { if (levels.crossed(level, prevPrice, price)) trigger.push(level); }
      else if (level.repeat && level.triggeredAt !== null && levels.distanceAtLeast(price, level.price, tick)) rearm.push(level);
    }
    return { trigger, rearm };
  }
  /** Store patch for a triggered level: non-repeat levels are disarmed and recoloured, repeat ones wait to re-arm. */
  const triggerPatch = (level, now) => ({ armed: false, triggeredAt: now, ...(level.repeat ? {} : { color: levels.TRIGGERED_COLOR }) });

  // ── rate limit ────────────────────────────────────────────────────────────
  class RateLimiter {
    constructor(windowMs = 3000) { this.windowMs = windowMs; this.last = new Map(); }
    /** true (and remembered) when (type, price) has not fired within the window. */
    allow(type, price, now) {
      const key = `${type}:${price}`, prev = this.last.get(key);
      if (prev !== undefined && now - prev < this.windowMs) return false;
      this.last.set(key, now);
      if (this.last.size > 2000) for (const [k, t] of this.last) if (now - t >= this.windowMs) this.last.delete(k);
      return true;
    }
  }

  // ── volume alerts ─────────────────────────────────────────────────────────
  const DEFAULT_CONFIG = { appear: false, appearUsd: 1000000, withinPct: 0.5, decrease: false, decreasePct: 50,
    print: false, printUsd: 500000, printAggMs: 0, sound: true };
  function tickIndex(price, tick) {
    const raw = price / tick, near = Math.round(raw);
    return Math.abs(raw - near) <= Math.min(1e-3, 1e-9 * Math.max(1, Math.abs(raw))) ? near : Math.floor(raw);
  }

  /**
   * Per-column volume alerts over a ScalpOrderBook Book (tick-index maps) and normalised prints.
   *  - appear:   resting size ≥ appearUsd within withinPct % of mid that was not there before (the first
   *              synced book after start/resync only seeds the tracker, it never alerts);
   *  - decrease: a tracked level falls ≥ decreasePct % below its peak, or disappears ('gone');
   *              each book step's drop is credited to trades seen at that exact tick on the consuming
   *              side (sell takers hit bids, buy takers lift asks). Classification waits `graceMs`
   *              because the trade and depth streams are separate sockets with no common ordering;
   *  - print:    single print (or same-side prints within printAggMs) ≥ printUsd.
   * Rate limit: one alert per (type, price) per rateMs.
   */
  class VolumeAlerts {
    constructor({ tick, tickStr, rateMs = 3000, graceMs = 400, tradeWindowMs = 3000 } = {}) {
      this.tick = tick; this.tickStr = tickStr ?? String(tick); this.cfg = { ...DEFAULT_CONFIG };
      this.limiter = new RateLimiter(rateMs); this.graceMs = graceMs; this.tradeWindowMs = tradeWindowMs;
      this.tracked = new Map(); this.traded = new Map(); this.pending = []; this.seeded = false; this.group = null;
      this.stats = { seeds: 0, resets: 0, suppressed: 0, emitted: 0 };
    }
    configure(cfg = {}) {
      const was = this.cfg; this.cfg = { ...DEFAULT_CONFIG, ...cfg };
      if (was.appearUsd !== this.cfg.appearUsd || was.withinPct !== this.cfg.withinPct || (!was.appear && !was.decrease)) this.reset();
    }
    /** Book resynced / restarted: forget tracked levels; the next synced book seeds silently. */
    reset() { this.tracked.clear(); this.pending = []; this.seeded = false; this.stats.resets += 1; }
    priceOf(idx) { return levels.fromTicks(idx, this.tickStr) ?? (idx * this.tick).toString(); }
    emit(out, alert) {
      if (!this.limiter.allow(alert.type, alert.price, alert.at)) { this.stats.suppressed += 1; return; }
      this.stats.emitted += 1; out.push(alert);
    }
    /** print: { time, price, qty, side: 'buy'|'sell' } (orderbook.normalizePrint). */
    onTrade(print, now) {
      const out = [];
      if (!print || !(print.price > 0) || !(print.qty > 0)) return out;
      const idx = tickIndex(print.price, this.tick), bookSide = print.side === 'sell' ? 'bid' : 'ask', key = `${bookSide}:${idx}`;
      const bucket = this.traded.get(key) || []; bucket.push({ at: now, qty: print.qty }); this.traded.set(key, bucket);
      if (this.cfg.print) this.checkPrint(out, print, idx, now);
      this.flush(out, now);
      return out;
    }
    checkPrint(out, print, idx, now) {
      const usd = print.price * print.qty, agg = this.cfg.printAggMs;
      let g = this.group;
      if (agg > 0 && g && g.side === print.side && print.time - g.firstTime <= agg && print.time >= g.lastTime) {
        g.usd += usd; g.qty += print.qty; g.count += 1; g.lastTime = print.time; g.idx = idx;
      } else g = this.group = { side: print.side, firstTime: print.time, lastTime: print.time, usd, qty: print.qty, count: 1, idx, fired: false };
      if (!g.fired && g.usd >= this.cfg.printUsd) {
        g.fired = true;
        this.emit(out, { type: 'print', side: print.side, price: this.priceOf(g.idx), usd: g.usd, qty: g.qty, count: g.count,
          at: now, tradeTime: print.time, detail: `${print.side === 'buy' ? 'buy' : 'sell'} ${g.count > 1 ? `${g.count} prints` : 'print'}` });
      }
    }
    /** Take observed traded qty at a book key received at or after `since` (consumed so it is credited once). */
    takeTraded(key, since, maxQty) {
      const bucket = this.traded.get(key);
      if (!bucket || !(maxQty > 0)) return 0;
      let got = 0;
      for (const t of bucket) {
        if (t.at < since || !(t.qty > 0) || got >= maxQty) continue;
        const use = Math.min(t.qty, maxQty - got); t.qty -= use; got += use;
      }
      return got;
    }
    prune(now) {
      for (const [key, bucket] of this.traded) {
        const keep = bucket.filter(t => now - t.at <= this.tradeWindowMs && t.qty > 0);
        if (keep.length) this.traded.set(key, keep); else this.traded.delete(key);
      }
    }
    /** book: Book with bids/asks Maps (tick index → qty), covered(idx), best(). Call on each synced update. */
    onBook(book, now) {
      const out = [], cfg = this.cfg;
      if (!cfg.appear && !cfg.decrease) { this.tracked.clear(); this.flush(out, now); return out; }
      const best = book?.best?.();
      if (!best || best.bid === null || best.ask === null) return out;
      const mid = (best.bid + best.ask) / 2 * this.tick, lo = mid * (1 - cfg.withinPct / 100), hi = mid * (1 + cfg.withinPct / 100);
      const inRange = idx => { const p = idx * this.tick; return p >= lo && p <= hi && book.covered(idx); };
      for (const [side, map] of [['bid', book.bids], ['ask', book.asks]]) for (const [idx, qty] of map) {
        const usd = qty * idx * this.tick, key = `${side}:${idx}`;
        if (usd < cfg.appearUsd || this.tracked.has(key) || !inRange(idx)) continue;
        this.tracked.set(key, { side, idx, peakQty: qty, lastQty: qty, lastAt: now, traded: 0, pulled: 0, since: now });
        if (this.seeded && cfg.appear) this.emit(out, { type: 'appear', side, price: this.priceOf(idx), usd, qty, at: now, detail: `${side} ≥ $${cfg.appearUsd}` });
      }
      if (!this.seeded) { this.seeded = true; this.stats.seeds += 1; }
      for (const [key, t] of this.tracked) {
        if (!inRange(t.idx)) { this.tracked.delete(key); continue; } // left the watched window: not a decrease
        const qty = book.qty(t.side, t.idx), step = t.lastQty - qty;
        if (step > 0) {
          const credit = this.takeTraded(key, t.lastAt - this.tradeWindowMs, step);
          t.traded += credit; t.pulled += step - credit;
        }
        if (qty > t.peakQty) { t.peakQty = qty; t.traded = 0; t.pulled = 0; }
        t.lastQty = qty; t.lastAt = now;
        if (!cfg.decrease) { if (qty * t.idx * this.tick < cfg.appearUsd / 2) this.tracked.delete(key); continue; } // hysteresis: no appear flapping
        const gone = qty === 0, drop = (t.peakQty - qty) / t.peakQty * 100;
        if (gone || drop >= cfg.decreasePct) {
          this.pending.push({ key, dueAt: now + this.graceMs, type: gone ? 'gone' : 'decrease', side: t.side, idx: t.idx, peakQty: t.peakQty, qty,
            traded: t.traded, pulled: t.pulled, at: now });
          if (gone) this.tracked.delete(key); else { t.peakQty = qty; t.traded = 0; t.pulled = 0; }
        }
      }
      this.flush(out, now); this.prune(now);
      return out;
    }
    /** Emit decrease/gone alerts whose grace period ended; late trades at that price are credited first. */
    flush(out, now) {
      const due = this.pending.filter(p => now >= p.dueAt);
      if (!due.length) return out;
      this.pending = this.pending.filter(p => now < p.dueAt);
      for (const p of due) {
        if (p.pulled > 0) { const late = this.takeTraded(p.key, p.at - this.tradeWindowMs, p.pulled); p.traded += late; p.pulled -= late; }
        const total = p.traded + p.pulled, cause = total <= 0 ? 'unknown' : p.pulled <= total * 1e-9 ? 'traded' : p.traded <= 0 ? 'pulled' : 'partly traded';
        const price = this.priceOf(p.idx), usd = (p.peakQty - p.qty) * p.idx * this.tick;
        this.emit(out, { type: p.type, side: p.side, price, usd, qty: p.peakQty - p.qty, cause, tradedQty: p.traded, pulledQty: p.pulled, at: now,
          detail: `${p.side} ${p.type === 'gone' ? 'gone' : `−${((p.peakQty - p.qty) / p.peakQty * 100).toFixed(0)}%`} · ${cause}` });
      }
      return out;
    }
  }

  // ── notifications log (Triggered tab) and Active rows ───────────────────────
  const LOG_KEY = 'screener.scalp.alerts.v1', LOG_VERSION = 1, LOG_CAP = 200;
  const ENTRY_TYPES = ['signal', 'appear', 'decrease', 'gone', 'print'];
  const ENTRY_SOURCES = ['dom', 'chart', 'book', 'trades'];
  function sanitizeEntry(e) {
    if (!e || typeof e !== 'object') return null;
    const ok = typeof e.id === 'string' && e.id.length <= 64 && Number.isFinite(e.at) && levels.EXCHANGES.includes(e.exchange)
      && typeof e.symbol === 'string' && e.symbol.length <= 40 && typeof e.price === 'string' && levels.parseDecimal(e.price)
      && ENTRY_TYPES.includes(e.type) && ENTRY_SOURCES.includes(e.source);
    if (!ok) return null;
    const out = { id: e.id, at: e.at, exchange: e.exchange, symbol: e.symbol, source: e.source, price: e.price, type: e.type,
      detail: typeof e.detail === 'string' ? e.detail.slice(0, 160) : '' };
    for (const k of ['levelId', 'tradePrice', 'prevPrice', 'tradeTime', 'tradeId', 'usd']) if (e[k] !== undefined && e[k] !== null) out[k] = e[k];
    return out;
  }
  /** Persisted list of fired alerts, newest first, capped at 200. Storage-like object injected. */
  class AlertLog {
    constructor(storage, { now = Date.now, cap = LOG_CAP } = {}) { this.storage = storage; this.now = now; this.cap = cap; this.memory = null; this.seq = 0; }
    list() {
      if (this.memory) return this.memory.slice();
      let raw = null;
      try { raw = JSON.parse(this.storage?.getItem(LOG_KEY) || 'null'); } catch (_) { raw = null; }
      if (!raw || raw.version !== LOG_VERSION || !Array.isArray(raw.entries)) return [];
      return raw.entries.map(sanitizeEntry).filter(Boolean).slice(0, this.cap);
    }
    save(entries) {
      const list = entries.slice(0, this.cap);
      try { this.storage.setItem(LOG_KEY, JSON.stringify({ version: LOG_VERSION, entries: list })); this.memory = null; return true; }
      catch (_) { this.memory = list; return false; }
    }
    add(entry) {
      const e = sanitizeEntry({ id: `a${this.now().toString(36)}${(this.seq++).toString(36)}`, at: this.now(), ...entry });
      if (!e) throw new Error('alert log: invalid entry');
      this.save([e, ...this.list()]);
      return e;
    }
    remove(id) { const list = this.list(), next = list.filter(e => e.id !== id); this.save(next); return next.length !== list.length; }
    clear() { this.save([]); }
  }
  /** Active tab: signal levels that will still fire (armed, or repeat waiting to re-arm), newest first. */
  function activeRows(all) {
    return all.filter(l => l.kind === 'signal' && (l.armed || l.repeat)).sort((a, b) => b.createdAt - a.createdAt)
      .map(l => ({ id: l.id, at: l.createdAt, exchange: l.exchange, symbol: l.symbol, source: l.source, price: l.price,
        type: l.repeat ? 'signal · repeat' : 'signal', status: l.armed ? 'armed' : 'waiting to re-arm (price must leave by ≥ 1 tick)', note: l.note }));
  }

  return { SOUNDS, TYPE_LABELS, DEFAULT_CONFIG, tradePriceString, evaluateSignals, triggerPatch, RateLimiter, VolumeAlerts, tickIndex,
    LOG_KEY, LOG_CAP, AlertLog, sanitizeEntry, activeRows };
});
