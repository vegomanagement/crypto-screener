/* Footprint (bid x ask order flow): exact BigInt aggregation, live merge, canvas renderer.
 * Mirrors footprint.py rule-for-rule (shared fixture tests/fixtures/footprint_parity.json).
 * ask = buyer-initiated (taker buy), bid = seller-initiated (taker sell). Real trades only:
 * no trade is ever synthesized and missing bars stay blank.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.WorkspaceFootprint = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const SCALE = 18n;
  const ONE = 10n ** SCALE;
  const COVERAGE_RANK = { partial: 0, complete: 1 };
  const INTERVALS_MS = { 1: 60000, 3: 180000, 5: 300000, 15: 900000, 30: 1800000, 60: 3600000 };
  const DEFAULTS = { input: 'volume', va_pct: 70, imbalance_ratio: '300', imbalance_min_diff: '0',
    include_zero: false, stacked_min: 3, min_delta: '0' };

  // ── exact decimals as BigInt scaled by 1e18 ───────────────────────────────
  function dec(value) {
    if (typeof value === 'bigint') return value;
    if (typeof value === 'number') {
      if (!Number.isSafeInteger(value) || value < 0) throw new Error('decimal string required');
      return BigInt(value) * ONE;
    }
    if (typeof value !== 'string') throw new Error('decimal string required');
    const match = /^(\d+)(?:\.(\d+))?$/.exec(value.trim());
    if (!match) throw new Error(`invalid decimal ${value}`);
    let frac = match[2] || '';
    if (frac.length > Number(SCALE)) {
      if (/[^0]/.test(frac.slice(Number(SCALE)))) throw new Error('too many decimals');
      frac = frac.slice(0, Number(SCALE));
    }
    return BigInt(match[1]) * ONE + BigInt(frac.padEnd(Number(SCALE), '0') || '0');
  }
  function fmt(value) {
    if (value === null || value === undefined) return null;
    const negative = value < 0n, abs = negative ? -value : value;
    const frac = (abs % ONE).toString().padStart(Number(SCALE), '0').replace(/0+$/, '');
    const text = `${abs / ONE}${frac ? `.${frac}` : ''}`;
    return text === '0' ? '0' : `${negative ? '-' : ''}${text}`;
  }
  const toNumber = value => Number(fmt(value));
  // round_half_up(num * 100 / den) to 2 decimals → scaled BigInt
  function pct(num, den) {
    const negative = num < 0n, a = negative ? -num : num;
    const cents = (a * 10000n * 2n + den) / (2n * den);
    const scaled = cents * ONE / 100n;
    return negative ? -scaled : scaled;
  }
  const abs = v => (v < 0n ? -v : v);

  // ── venue normalisation (REST/WS shapes) ──────────────────────────────────
  function intField(value, name) {
    if (typeof value === 'string' && /^\d+$/.test(value)) value = Number(value);
    if (!Number.isSafeInteger(value) || value < 0) throw new Error(`invalid ${name}`);
    return value;
  }
  function normalizeTrade(venue, row) {
    if (!row || typeof row !== 'object') throw new Error('invalid trade');
    if (venue === 'binance') {
      if (typeof row.m !== 'boolean') throw new Error('binance: missing maker flag');
      const first = intField(row.f, 'first id'), last = intField(row.l, 'last id');
      if (last < first) throw new Error('binance: invalid id range');
      return { time: intField(row.T, 'time'), price: dec(row.p), qty: dec(row.q), side: row.m ? 'bid' : 'ask',
        count: last - first + 1, id: intField(row.a, 'id') };
    }
    if (venue === 'bybit') {
      const side = row.side ?? row.S;
      if (side !== 'Buy' && side !== 'Sell') throw new Error('bybit: invalid taker side');
      return { time: intField(row.time ?? row.T, 'time'), price: dec(row.price ?? row.p), qty: dec(row.size ?? row.v),
        side: side === 'Buy' ? 'ask' : 'bid', count: 1, id: String(row.execId ?? row.i) };
    }
    if (venue === 'hyperliquid') {
      if (row.side !== 'B' && row.side !== 'A') throw new Error('hyperliquid: invalid side');
      return { time: intField(row.time, 'time'), price: dec(row.px), qty: dec(row.sz),
        side: row.side === 'B' ? 'ask' : 'bid', count: 1, id: String(row.tid) };
    }
    throw new Error('unsupported venue');
  }

  // ── accumulation ───────────────────────────────────────────────────────────
  function createBar(openMs, intervalMs, step) {
    const s = dec(step);
    if (s <= 0n) throw new Error('step must be positive');
    return { openMs, intervalMs, step: s, levels: new Map(), tradeCount: 0, open: null, high: null, low: null,
      close: null, first: null, last: null, version: 0 };
  }
  function addTrade(bar, trade) {
    if (!(trade.time >= bar.openMs && trade.time <= bar.openMs + bar.intervalMs - 1)) return false;
    if (trade.side !== 'bid' && trade.side !== 'ask') throw new Error('invalid side');
    const index = trade.price / bar.step;
    const key = index.toString();
    let cell = bar.levels.get(key);
    if (!cell) { cell = { index, bid: 0n, ask: 0n, bidTrades: 0, askTrades: 0 }; bar.levels.set(key, cell); }
    if (trade.side === 'bid') { cell.bid += trade.qty; cell.bidTrades += trade.count; }
    else { cell.ask += trade.qty; cell.askTrades += trade.count; }
    bar.tradeCount += trade.count;
    if (bar.first === null || trade.time < bar.first) { bar.first = trade.time; bar.open = trade.price; }
    if (bar.last === null || trade.time >= bar.last) { bar.last = trade.time; bar.close = trade.price; }
    bar.high = bar.high === null || trade.price > bar.high ? trade.price : bar.high;
    bar.low = bar.low === null || trade.price < bar.low ? trade.price : bar.low;
    bar.version += 1;
    return true;
  }
  /** Rebuild a bar from server JSON levels at the server's grouped step. */
  function barFromServer(json, intervalMs, step) {
    const bar = createBar(json.open_time, intervalMs, step);
    for (const lv of json.levels || []) {
      const price = dec(lv.price);
      if (price % bar.step !== 0n) throw new Error('server level not on step grid');
      const index = price / bar.step;
      bar.levels.set(index.toString(), { index, bid: dec(lv.bid), ask: dec(lv.ask),
        bidTrades: intField(lv.bid_trades, 'bid_trades'), askTrades: intField(lv.ask_trades, 'ask_trades') });
    }
    bar.tradeCount = intField(json.trade_count, 'trade_count');
    for (const key of ['open', 'high', 'low', 'close']) bar[key] = json[key] === null || json[key] === undefined ? null : dec(json[key]);
    bar.first = bar.open === null ? null : json.open_time;
    bar.last = bar.close === null ? null : json.open_time;
    return bar;
  }
  function groupedLevels(bar, group) {
    if (!Number.isInteger(group) || group < 1) throw new Error('group_ticks must be a positive integer');
    const g = BigInt(group), merged = new Map();
    for (const cell of bar.levels.values()) {
      const index = cell.index / g, key = index.toString();
      let out = merged.get(key);
      if (!out) { out = { index, bid: 0n, ask: 0n, bidTrades: 0, askTrades: 0 }; merged.set(key, out); }
      out.bid += cell.bid; out.ask += cell.ask; out.bidTrades += cell.bidTrades; out.askTrades += cell.askTrades;
    }
    const step = bar.step * g;
    return [...merged.values()].sort((a, b) => (a.index < b.index ? -1 : a.index > b.index ? 1 : 0))
      .map(cell => ({ ...cell, price: cell.index * step }));
  }

  // ── analytics ──────────────────────────────────────────────────────────────
  function settingsFrom(raw = {}) {
    const s = { ...DEFAULTS, ...raw };
    if (!['volume', 'trades'].includes(s.input)) throw new Error('input must be volume or trades');
    const va = Number(s.va_pct), stacked = Number(s.stacked_min);
    if (!Number.isInteger(va) || va < 1 || va > 100) throw new Error('va_pct must be 1..100');
    if (!Number.isInteger(stacked) || stacked < 2) throw new Error('stacked_min must be >= 2');
    const ratio = dec(String(s.imbalance_ratio));
    if (ratio < 100n * ONE) throw new Error('imbalance_ratio must be >= 100');
    return { input: s.input, va_pct: BigInt(va), imbalance_ratio: ratio, imbalance_min_diff: dec(String(s.imbalance_min_diff)),
      include_zero: Boolean(s.include_zero), stacked_min: stacked, min_delta: dec(String(s.min_delta)) };
  }
  function analyze(levels, step, settings) {
    const s = settings && typeof settings.va_pct === 'bigint' ? settings : settingsFrom(settings);
    const out = { total: 0n, buy_vol: 0n, sell_vol: 0n, delta: 0n, delta_pct: null, poc: null, delta_poc: null,
      va_low: null, va_high: null, imbalances: [], stacked: [], maxVolume: 0n, maxAbsDelta: 0n };
    if (!levels.length) return out;
    const rows = new Map(levels.map(lv => [lv.index.toString(), s.input === 'trades'
      ? [BigInt(lv.bidTrades) * ONE, BigInt(lv.askTrades) * ONE] : [lv.bid, lv.ask]]));
    const lo = levels[0].index, hi = levels.at(-1).index, n = Number(hi - lo) + 1;
    const bid = new Array(n), ask = new Array(n), vol = new Array(n);
    for (let i = 0; i < n; i += 1) {
      const row = rows.get((lo + BigInt(i)).toString()) || [0n, 0n];
      bid[i] = row[0]; ask[i] = row[1]; vol[i] = row[0] + row[1];
    }
    const price = i => (lo + BigInt(i)) * step;
    const sell = bid.reduce((a, b) => a + b, 0n), buy = ask.reduce((a, b) => a + b, 0n), total = sell + buy;
    Object.assign(out, { total, buy_vol: buy, sell_vol: sell, delta: buy - sell });
    if (total > 0n) {
      out.delta_pct = pct(buy - sell, total);
      let poc = 0;
      for (let i = 1; i < n; i += 1) if (vol[i] > vol[poc]) poc = i;
      out.poc = price(poc); out.maxVolume = vol[poc];
      let acc = vol[poc], vaLo = poc, vaHi = poc;
      while (acc * 100n < total * s.va_pct && (vaLo > 0 || vaHi < n - 1)) {
        const up = vaHi < n - 1 ? vol[vaHi + 1] : null, down = vaLo > 0 ? vol[vaLo - 1] : null;
        if (down === null || (up !== null && up >= down)) { vaHi += 1; acc += up; } else { vaLo -= 1; acc += down; }
      }
      out.va_low = price(vaLo); out.va_high = price(vaHi);
    }
    let best = 0;
    const delta = i => ask[i] - bid[i];
    for (let i = 1; i < n; i += 1) if (abs(delta(i)) > abs(delta(best))) best = i;
    out.maxAbsDelta = abs(delta(best));
    if (delta(best) !== 0n && abs(delta(best)) >= s.min_delta) out.delta_poc = price(best);
    const flags = { buy: new Set(), sell: new Set() };
    for (let i = 0; i < n; i += 1) {
      for (const [side, num, den] of [['buy', ask[i], i > 0 ? bid[i - 1] : null], ['sell', bid[i], i < n - 1 ? ask[i + 1] : null]]) {
        if (den === null || num <= 0n || num - den < s.imbalance_min_diff) continue;
        let ratio;
        if (den === 0n) { if (!s.include_zero) continue; ratio = null; }
        else if (num * 100n * ONE >= s.imbalance_ratio * den) ratio = pct(num, den);
        else continue;
        flags[side].add(i);
        out.imbalances.push({ price: price(i), side, ratio, offset: i });
      }
    }
    for (const side of ['buy', 'sell']) {
      let run = [];
      for (let i = 0; i <= n; i += 1) {
        if (flags[side].has(i)) { run.push(i); continue; }
        if (run.length >= s.stacked_min) out.stacked.push({ side, from: price(run[0]), to: price(run.at(-1)), count: run.length });
        run = [];
      }
    }
    out.stacked.sort((a, b) => (a.from < b.from ? -1 : a.from > b.from ? 1 : a.side < b.side ? -1 : 1));
    return out;
  }
  function barPayload(bar, group, coverage, source, rawSettings) {
    if (!(coverage in COVERAGE_RANK)) throw new Error('coverage must be complete or partial');
    const settings = settingsFrom(rawSettings);
    const levels = groupedLevels(bar, group), step = bar.step * BigInt(group);
    const volume = analyze(levels, step, { ...settings, input: 'volume' });
    const stats = settings.input === 'volume' ? volume : analyze(levels, step, settings);
    return {
      time: Math.floor(bar.openMs / 1000), open_time: bar.openMs, close_time: bar.openMs + bar.intervalMs - 1,
      open: fmt(bar.open), high: fmt(bar.high), low: fmt(bar.low), close: fmt(bar.close),
      tick_size: fmt(bar.step), group_ticks: group,
      levels: levels.map(lv => ({ price: fmt(lv.price), bid: fmt(lv.bid), ask: fmt(lv.ask), trades: lv.bidTrades + lv.askTrades,
        bid_trades: lv.bidTrades, ask_trades: lv.askTrades })),
      trade_count: bar.tradeCount, total: fmt(volume.total), buy_vol: fmt(volume.buy_vol), sell_vol: fmt(volume.sell_vol),
      delta: fmt(volume.delta), delta_pct: fmt(volume.delta_pct), input: settings.input,
      poc: fmt(stats.poc), delta_poc: fmt(stats.delta_poc), va_low: fmt(stats.va_low), va_high: fmt(stats.va_high),
      imbalances: stats.imbalances.map(m => ({ price: fmt(m.price), side: m.side, ratio: fmt(m.ratio) })),
      stacked: stats.stacked.map(z => ({ side: z.side, from: fmt(z.from), to: fmt(z.to), count: z.count })),
      coverage, source
    };
  }
  function niceGroup(minimum) {
    for (let scale = 1; ; scale *= 10) for (const nice of [1, 2, 5]) if (nice * scale >= minimum) return nice * scale;
  }

  // ── live store: server snapshot + WS trades with provable coverage ─────────
  class LiveFootprint {
    constructor({ venue, interval, maxBars = 400, maxBuffer = 50000 }) {
      if (!INTERVALS_MS[interval]) throw new Error('unsupported footprint interval');
      this.venue = venue; this.intervalMs = INTERVALS_MS[interval];
      this.maxBars = maxBars; this.maxBuffer = maxBuffer;
      this.bars = new Map(); // openMs → { bar, coverage, forming, origin }
      this.step = null; this.serverGroup = 1; this.snapshot = null; this.pending = []; this.live = []; this.liveIds = new Set();
      this.nextId = null; this.provenSince = null; this.connectedAt = null;
      this.rolled = 0; this.errors = 0; this.gaps = 0; this.resyncWanted = false; this.lastGap = null;
    }
    get ready() { return this.snapshot !== null; }
    loadSnapshot(payload) {
      const step = dec(payload.tick_size) * BigInt(payload.group_ticks);
      if (this.step !== null && step !== this.step) { this.bars.clear(); this.live = []; this.liveIds.clear(); this.nextId = null; }
      this.step = step; this.serverGroup = payload.group_ticks;
      const formingJson = (payload.bars || []).find(b => b.forming);
      const asOf = payload.as_of;
      const formingOpen = formingJson ? formingJson.open_time : Math.floor(asOf / this.intervalMs) * this.intervalMs;
      this.snapshot = { asOf, lastId: payload.last_trade_id ?? null, recentIds: new Set((payload.recent_ids || []).map(String)),
        dedupeMs: payload.dedupe_window_ms || 5000, formingOpen };
      // Bars a replay may (re)build: server-replaced ones and anything newer than the snapshot.
      const rebuild = new Set();
      for (const [open, entry] of this.bars) if (open > formingOpen && entry.origin === 'live') { this.bars.delete(open); rebuild.add(open); }
      for (const json of payload.bars || []) {
        const existing = this.bars.get(json.open_time);
        if (existing && COVERAGE_RANK[existing.coverage] > COVERAGE_RANK[json.coverage]) continue;
        this.bars.set(json.open_time, { bar: barFromServer(json, this.intervalMs, step), coverage: json.coverage, forming: false, origin: 'server',
          klineCheck: json.kline_check?.status || 'unchecked' });
        rebuild.add(json.open_time);
      }
      const forming = this.bars.get(formingOpen);
      if (this.venue === 'binance') {
        this.nextId = this.snapshot.lastId === null ? null : this.snapshot.lastId + 1;
        this.provenSince = forming?.coverage === 'complete' ? formingOpen - 1 : asOf;
      } else {
        const continuous = this.connectedAt !== null && this.connectedAt <= asOf - this.snapshot.dedupeMs;
        this.provenSince = continuous ? (forming?.coverage === 'complete' ? formingOpen - 1 : asOf) : this.connectedAt;
        if (!continuous && forming && rebuild.has(formingOpen)) forming.coverage = 'partial';
      }
      const replay = [...this.live, ...this.pending].sort((a, b) => a.time - b.time || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
      this.pending = []; this.live = []; this.liveIds.clear();
      for (const trade of replay) this.apply(trade, open => rebuild.has(open) || open > formingOpen);
      this.markForming();
      this.trim();
    }
    markForming() {
      const latest = this.currentOpen();
      for (const entry of this.bars.values()) entry.forming = entry.bar.openMs === latest;
    }
    currentOpen() {
      let latest = -Infinity;
      for (const key of this.bars.keys()) latest = Math.max(latest, key);
      return latest;
    }
    onStatus(status, at) {
      if (status === 'connected' || status === 'live') {
        if (this.connectedAt === null) {
          this.connectedAt = at;
          if (this.venue !== 'binance' && this.ready) { this.provenSince = at; this.resyncWanted = true; }
        }
        return;
      }
      if (['connecting', 'disconnected', 'unsupported'].includes(status) && this.connectedAt !== null) {
        this.connectedAt = null;
        if (this.venue !== 'binance') {
          this.provenSince = null;
          const latest = this.bars.get(this.currentOpen());
          if (latest?.forming) latest.coverage = 'partial';
        }
      }
    }
    onTrades(rows) {
      let added = 0;
      for (const row of rows) {
        let trade;
        try { trade = normalizeTrade(this.venue, row); } catch (_) { this.errors += 1; continue; }
        if (!this.ready) { if (this.pending.length < this.maxBuffer) this.pending.push(trade); continue; }
        if (this.apply(trade)) added += 1;
      }
      this.trim();
      return added;
    }
    apply(trade, accept = () => true) {
      const snap = this.snapshot;
      if (this.venue === 'binance') {
        if (this.nextId !== null && trade.id < this.nextId) return false;
        if (this.nextId === null || trade.id > this.nextId) {
          this.lastGap = { expected: this.nextId, got: trade.id, time: trade.time };
          this.provenSince = trade.time; this.gaps += 1; this.markPartial(trade.time);
          this.resyncWanted = true; // a fresh REST snapshot (as_of after the gap) can heal it exactly
        }
        this.nextId = trade.id + 1;
      } else {
        if (trade.time <= snap.asOf - snap.dedupeMs) return false;
        if (trade.time <= snap.asOf && snap.recentIds.has(String(trade.id))) return false;
        if (this.liveIds.has(trade.id)) return false;
      }
      const open = Math.floor(trade.time / this.intervalMs) * this.intervalMs;
      this.remember(trade);
      if (!accept(open)) return false; // already counted in a bar we kept
      let entry = this.bars.get(open);
      if (!entry) {
        if (open < this.currentOpen()) return false; // late trade for a bar with no fetched window
        const proven = this.provenSince !== null && this.provenSince < open;
        entry = { bar: createBar(open, this.intervalMs, this.step), coverage: proven ? 'complete' : 'partial', forming: true, origin: 'live' };
        this.bars.set(open, entry); this.rolled += 1; this.markForming();
      }
      if (this.provenSince === null || this.provenSince >= open) entry.coverage = 'partial';
      addTrade(entry.bar, trade);
      return true;
    }
    remember(trade) {
      if (this.live.length >= this.maxBuffer) this.liveIds.delete(this.live.shift().id);
      this.live.push(trade); this.liveIds.add(trade.id);
    }
    markPartial(time) {
      const entry = this.bars.get(Math.floor(time / this.intervalMs) * this.intervalMs);
      if (entry) entry.coverage = 'partial';
    }
    trim() {
      const keys = [...this.bars.keys()].sort((a, b) => a - b);
      while (keys.length > this.maxBars) this.bars.delete(keys.shift());
      const oldest = this.currentOpen() - this.intervalMs;
      if (this.live.length && this.live[0].time < oldest) {
        this.live = this.live.filter(t => t.time >= oldest);
        this.liveIds = new Set(this.live.map(t => t.id));
      }
    }
    list() { return [...this.bars.values()].sort((a, b) => a.bar.openMs - b.bar.openMs); }
    summary() {
      const list = this.list();
      return { bars: list.length, complete: list.filter(e => e.coverage === 'complete').length,
        partial: list.filter(e => e.coverage === 'partial').length, live: list.filter(e => e.origin === 'live').length, gaps: this.gaps,
        klineExact: list.filter(e => e.klineCheck === 'exact').length, klineMismatch: list.filter(e => e.klineCheck === 'mismatch').length };
    }
  }

  // ── rendering ──────────────────────────────────────────────────────────────
  const COLORS = { bid: '#ef6670', ask: '#00c087', neutral: '#6b98cf', text: '#c9d2df', dim: '#76849b', poc: '#ffd166', dpoc: '#4fc3f7', va: '#b593d2' };
  function rgba(hex, alpha) {
    const m = /^#([0-9a-f]{6})$/i.exec(hex);
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${Math.max(0, Math.min(1, alpha)).toFixed(3)})`;
  }
  function compact(value) {
    const a = Math.abs(value);
    const text = a >= 1e6 ? `${(a / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M` : a >= 1e3 ? `${(a / 1e3).toFixed(a >= 1e4 ? 0 : 1)}k`
      : a >= 100 ? a.toFixed(0) : a >= 10 ? a.toFixed(1) : a >= 1 ? a.toFixed(2) : a === 0 ? '0' : a.toPrecision(2);
    return `${value < 0 ? '-' : ''}${text}`;
  }
  /** Level of detail: auto-raise grouping until cells are tall enough; text only if wide enough. */
  function chooseDetail({ barSpacing, pxPerStep, manual, maxLevels = 0 }) {
    const minCell = 9;
    const group = manual ? 1 : niceGroup(Math.max(1, Math.ceil(minCell / Math.max(pxPerStep, 1e-9))));
    const cell = pxPerStep * group;
    const text = barSpacing >= 40 && cell >= minCell && (!maxLevels || maxLevels / group <= 400);
    return { group, cell, mode: text ? 'cells' : barSpacing >= 3 ? 'marks' : 'footer' };
  }
  // UI numbers → plain decimal strings (String(1e-8) would be "1e-8", which dec() rejects).
  function decimalText(value, fallback) {
    const n = Number(value);
    if (!Number.isFinite(n) || n < 0) return String(fallback);
    return Number.isInteger(n) ? String(n) : n.toFixed(12).replace(/0+$/, '').replace(/\.$/, '');
  }
  function analysisSettings(ui) {
    return { input: ui.input === 'trades' ? 'trades' : 'volume', va_pct: Math.min(100, Math.max(1, Math.round(ui.vaPct ?? 70))),
      imbalance_ratio: decimalText(Math.max(100, ui.imbalanceRatio ?? 300), 300), imbalance_min_diff: decimalText(ui.imbalanceMinDiff ?? 0, 0),
      include_zero: Boolean(ui.includeZero), stacked_min: Math.max(2, Math.round(ui.stackedMin ?? 3)), min_delta: decimalText(ui.minDelta ?? 0, 0) };
  }
  const analysisCache = new WeakMap();
  function analyzedEntry(entry, group, settingsKey, settings) {
    let cache = analysisCache.get(entry.bar);
    const key = `${entry.bar.version}|${group}|${settingsKey}`;
    if (cache?.key === key) return cache.value;
    const levels = groupedLevels(entry.bar, group), step = entry.bar.step * BigInt(group);
    const stats = analyze(levels, step, settings);
    const volumeStats = settings.input === 'volume' ? stats : analyze(levels, step, { ...settings, input: 'volume' });
    cache = { key, value: { levels, step, stats, volumeStats } };
    analysisCache.set(entry.bar, cache);
    return cache.value;
  }
  /**
   * view: { barCenter(openSeconds) → px|null (integer logical index inside), barSpacing, priceToY(number) → px|null,
   *         plotWidth, plotHeight }
   */
  function draw(ctx, store, view, ui) {
    const result = { drawn: 0, mode: null, group: null, cells: 0 };
    if (!store?.step || !view.barSpacing) return result;
    const settings = settingsFrom(analysisSettings(ui));
    const settingsKey = JSON.stringify(analysisSettings(ui));
    const baseStep = toNumber(store.step);
    const ref = store.list().reverse().find(e => e.bar.close !== null)?.bar.close;
    if (ref === undefined) return result;
    const refPrice = toNumber(ref);
    const y0 = view.priceToY(refPrice), y1 = view.priceToY(refPrice + baseStep);
    if (y0 === null || y1 === null) return result;
    const visible = [];
    let maxLevels = 0;
    for (const entry of store.list()) {
      const x = view.barCenter(entry.bar.openMs / 1000);
      if (x === null || !Number.isFinite(x) || x < -view.barSpacing || x > view.plotWidth + view.barSpacing) continue;
      if (entry.bar.high !== null) maxLevels = Math.max(maxLevels, Number((entry.bar.high - entry.bar.low) / store.step) + 1);
      visible.push({ entry, x });
    }
    const detail = chooseDetail({ barSpacing: view.barSpacing, pxPerStep: Math.abs(y0 - y1), manual: ui.grouping === 'manual', maxLevels });
    Object.assign(result, { mode: detail.mode, group: detail.group });
    const footerH = ui.showFooter === false ? 0 : (view.barSpacing >= 30 ? 24 : 6);
    const bottom = view.plotHeight - footerH;
    // Raw single-tick cells are centred on their traded price; grouped cells span [p, p + step).
    const halfTick = store.serverGroup === 1 && detail.group === 1 ? baseStep / 2 : 0;
    const minFilter = Number(ui.minValue) || 0, maxFilter = Number(ui.maxValue) || 0;
    ctx.save();
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.font = `${detail.cell >= 13 ? 10 : 9}px SFMono-Regular, Consolas, monospace`;
    for (const { entry, x } of visible) {
      const { levels, step, stats, volumeStats } = analyzedEntry(entry, detail.group, settingsKey, settings);
      if (!levels.length) continue;
      const stepNum = toNumber(step);
      const width = Math.max(1, view.barSpacing - (view.barSpacing >= 8 ? 2 : 0));
      const left = x - width / 2;
      const yOf = price => view.priceToY(price);
      const span = price => {
        const p = toNumber(price) - halfTick;
        const a = yOf(p), b = yOf(p + stepNum);
        return a === null || b === null ? null : { top: Math.min(a, b), bottom: Math.max(a, b) };
      };
      const vaLow = stats.va_low, vaHigh = stats.va_high;
      const maxVol = stats.maxVolume || 1n, maxDelta = stats.maxAbsDelta || 1n;
      const imbalanced = new Map(stats.imbalances.map(m => [`${m.side}|${m.price}`, m]));
      if (detail.mode === 'cells') {
        for (const lv of levels) {
          const box = span(lv.price);
          if (!box || box.bottom < 0 || box.top > bottom) continue;
          const isTrades = settings.input === 'trades';
          const b = isTrades ? BigInt(lv.bidTrades) * ONE : lv.bid, a = isTrades ? BigInt(lv.askTrades) * ONE : lv.ask;
          const v = a + b, d = a - b, vNum = toNumber(v);
          if ((minFilter && vNum < minFilter) || (maxFilter && vNum > maxFilter)) continue;
          const inVA = ui.showVA === false || vaLow === null || (lv.price >= vaLow && lv.price <= vaHigh);
          const dim = inVA ? 1 : 0.45;
          const buyImb = imbalanced.get(`buy|${lv.price}`), sellImb = imbalanced.get(`sell|${lv.price}`);
          const h = Math.max(1, box.bottom - box.top - 1);
          const coloration = ui.coloration || 'delta';
          let fill = null;
          if (coloration === 'fixed') fill = rgba(d >= 0n ? COLORS.ask : COLORS.bid, 0.22 * dim);
          else if (coloration === 'volume') fill = rgba(COLORS.neutral, (0.08 + 0.55 * Number(v * 1000n / maxVol) / 1000) * dim);
          else if (coloration === 'delta') fill = rgba(d >= 0n ? COLORS.ask : COLORS.bid, (0.06 + 0.5 * Number(abs(d) * 1000n / maxDelta) / 1000) * dim);
          else if (coloration === 'imbalance') fill = rgba(buyImb ? COLORS.ask : sellImb ? COLORS.bid : COLORS.neutral, (buyImb || sellImb ? 0.5 : 0.06) * dim);
          if (ui.mode === 'profile') {
            const len = Math.max(1, (width - 2) * Number(v * 1000n / maxVol) / 1000);
            const bidLen = v > 0n ? len * Number(b * 1000n / v) / 1000 : 0;
            ctx.fillStyle = rgba(COLORS.bid, 0.55 * dim); ctx.fillRect(left + 1, box.top + 0.5, bidLen, h);
            ctx.fillStyle = rgba(COLORS.ask, 0.55 * dim); ctx.fillRect(left + 1 + bidLen, box.top + 0.5, len - bidLen, h);
          } else if (fill) { ctx.fillStyle = fill; ctx.fillRect(left, box.top + 0.5, width, h); }
          const cy = (box.top + box.bottom) / 2;
          const cellType = ui.cellType || 'bidask';
          const strong = (side, imb) => (imb ? (side === 'ask' ? '#3cf0b4' : '#ff8a96') : null);
          if (cellType === 'bidask') {
            const bidDominant = b > a, askDominant = a > b;
            ctx.textAlign = 'right';
            ctx.fillStyle = strong('bid', sellImb) || (ui.dominantOnly && !bidDominant ? COLORS.dim : COLORS.text);
            ctx.fillText(compact(toNumber(b)), x - 3, cy);
            ctx.textAlign = 'left';
            ctx.fillStyle = strong('ask', buyImb) || (ui.dominantOnly && !askDominant ? COLORS.dim : COLORS.text);
            ctx.fillText(compact(toNumber(a)), x + 3, cy);
            ctx.textAlign = 'center';
            ctx.fillStyle = rgba(COLORS.dim, 0.8); ctx.fillText('×', x, cy);
          } else {
            const text = cellType === 'volume' ? compact(vNum) : cellType === 'delta' ? compact(toNumber(d)) : `${compact(toNumber(d))}|${compact(vNum)}`;
            ctx.fillStyle = buyImb ? '#3cf0b4' : sellImb ? '#ff8a96' : COLORS.text;
            ctx.fillText(text, x, cy);
          }
          result.cells += 1;
        }
      } else if (detail.mode === 'marks') {
        for (const m of stats.imbalances) {
          const box = span(m.price);
          if (!box) continue;
          ctx.fillStyle = m.side === 'buy' ? COLORS.ask : COLORS.bid;
          const mx = m.side === 'buy' ? x + width / 2 - 2 : x - width / 2;
          ctx.fillRect(mx, (box.top + box.bottom) / 2 - 1, 2, 2);
        }
      }
      if (ui.showVA !== false && vaLow !== null && detail.mode !== 'footer') {
        const lo = span(vaLow), hi = span(vaHigh);
        if (lo && hi) { ctx.fillStyle = rgba(COLORS.va, 0.75); ctx.fillRect(left, hi.top, 1.5, lo.bottom - hi.top); }
      }
      if (ui.showPoc !== false && stats.poc !== null && detail.mode !== 'footer') {
        const box = span(stats.poc);
        if (box) {
          ctx.strokeStyle = COLORS.poc; ctx.lineWidth = 1;
          if (detail.mode === 'cells') ctx.strokeRect(left + 0.5, box.top + 0.5, width - 1, Math.max(1, box.bottom - box.top - 1));
          else { ctx.beginPath(); ctx.moveTo(left, (box.top + box.bottom) / 2); ctx.lineTo(left + width, (box.top + box.bottom) / 2); ctx.stroke(); }
        }
      }
      if (ui.showDeltaPoc && stats.delta_poc !== null && detail.mode === 'cells') {
        const box = span(stats.delta_poc);
        if (box) { ctx.save(); ctx.setLineDash([3, 2]); ctx.strokeStyle = COLORS.dpoc; ctx.strokeRect(left + 1.5, box.top + 1.5, width - 3, Math.max(1, box.bottom - box.top - 3)); ctx.restore(); }
      }
      for (const z of stats.stacked) {
        if (detail.mode === 'footer') break;
        const a = span(z.from), c = span(z.to);
        if (!a || !c) continue;
        ctx.fillStyle = rgba(z.side === 'buy' ? COLORS.ask : COLORS.bid, 0.85);
        ctx.fillRect(z.side === 'buy' ? left + width - 3 : left + 2, c.top, 2, a.bottom - c.top);
      }
      const flag = entry.coverage === 'partial' ? 'partial' : entry.klineCheck === 'mismatch' ? 'kline Δ' : null;
      if (flag) { // incomplete window, or venue kline attributes a boundary-straddling aggregate differently
        const hi = span(levels.at(-1).price), lo = span(levels[0].price);
        if (hi && lo) {
          const color = flag === 'partial' ? '#ba9665' : '#d4ac5c';
          ctx.save(); ctx.setLineDash([2, 3]); ctx.strokeStyle = rgba(color, 0.9); ctx.lineWidth = 1;
          ctx.strokeRect(left + 0.5, hi.top - 2.5, width - 1, lo.bottom - hi.top + 5); ctx.restore();
          if (view.barSpacing >= 14) { ctx.fillStyle = color; ctx.textAlign = 'center'; ctx.fillText(flag, x, hi.top - 8); }
        }
      }
      if (footerH) {
        const d = toNumber(volumeStats.delta);
        ctx.fillStyle = rgba(d >= 0 ? COLORS.ask : COLORS.bid, footerH > 6 ? 0.16 : 0.8);
        ctx.fillRect(left, bottom + 1, width, footerH - 2);
        if (footerH > 6) {
          ctx.textAlign = 'center'; ctx.fillStyle = d >= 0 ? '#3cf0b4' : '#ff8a96';
          ctx.fillText(compact(d), x, bottom + 7);
          if (volumeStats.delta_pct !== null) { ctx.fillStyle = COLORS.dim; ctx.fillText(`${toNumber(volumeStats.delta_pct).toFixed(0)}%`, x, bottom + 17); }
        }
      }
      result.drawn += 1;
    }
    ctx.restore();
    return result;
  }

  // ── pane controller: fetch snapshots, subscribe trades, schedule refreshes ─
  function createController(options) {
    const { fetch: fetchImpl, hub, onChange = () => {}, now = Date.now } = options;
    const later = options.setTimeout || setTimeout, cancel = options.clearTimeout || clearTimeout;
    let config = null, store = null, off = null, timer = null, inflight = null, generation = 0, currentKey = null;
    let status = { state: 'off', message: '', payload: null }, failures = 0, rolledSeen = 0, resyncFor = null;
    // One early re-fetch per bar when the live stream could not be stitched to the snapshot.
    function maybeResync() {
      if (!store?.resyncWanted) return;
      store.resyncWanted = false;
      const open = store.currentOpen();
      if (resyncFor === open) return;
      // Bybit/HL continuity needs the stream to predate the snapshot's dedupe window.
      resyncFor = open; schedule(config.exchange === 'binance' ? 2000 : (store.snapshot?.dedupeMs || 5000) + 1500);
    }
    function schedule(delay) { cancel(timer); timer = later(() => { timer = null; refresh(); }, delay); }
    async function refresh() {
      if (!config || inflight) return;
      if (options.visible && !options.visible()) { schedule(5000); return; }
      const g = generation, controller = typeof AbortController === 'function' ? new AbortController() : null;
      inflight = controller || {};
      const query = new URLSearchParams({ exchange: config.exchange, symbol: config.symbol, interval: config.interval,
        limit: String(config.bars || 60), group_ticks: config.groupTicks ? String(config.groupTicks) : 'auto' });
      try {
        const response = await fetchImpl(`/api/footprint?${query}`, { signal: controller?.signal, cache: 'no-store', headers: { Accept: 'application/json' } });
        const payload = await response.json();
        if (g !== generation) return;
        if (!response.ok) throw new Error(payload?.error || `HTTP ${response.status}`);
        if (payload.exchange !== config.exchange || payload.symbol !== config.symbol || payload.interval !== config.interval) throw new Error('footprint response for another market');
        store.loadSnapshot(payload);
        failures = 0; status = { state: 'ready', message: '', payload };
        schedule(payload.truncated ? 30000 : 60000);
        maybeResync(); // gaps found while replaying buffered trades onto this snapshot
      } catch (error) {
        if (g !== generation || error?.name === 'AbortError') return;
        failures += 1; status = { ...status, state: 'error', message: error?.message || 'footprint unavailable' };
        schedule(Math.min(60000, 5000 * 2 ** Math.min(failures - 1, 4)));
      } finally { if (g === generation) inflight = null; onChange(); }
    }
    function enable(next) {
      const key = next && JSON.stringify([next.exchange, next.symbol, next.interval, next.bars, next.groupTicks]);
      if (key && key === currentKey) return; // idempotent: candle reloads must not reset live state
      disable();
      if (!next) return;
      currentKey = key;
      if (!INTERVALS_MS[next.interval]) { status = { state: 'unsupported', message: 'Footprint needs a 1m–1h interval' }; onChange(); return; }
      config = { ...next }; generation += 1; rolledSeen = 0;
      store = new LiveFootprint({ venue: config.exchange, interval: config.interval });
      status = { state: 'loading', message: '', payload: null };
      off = hub?.subscribeTrades?.({ venue: config.exchange, symbol: config.symbol }, event => {
        if (!store) return;
        if (event.type === 'status') { store.onStatus(event.status, event.connectedAt ?? now()); maybeResync(); onChange(); return; }
        if (event.type !== 'trades') return;
        store.onTrades(event.rows); maybeResync();
        if (store.rolled > rolledSeen) { rolledSeen = store.rolled; schedule(3000); } // fetch server-complete closed bar
        onChange();
      }) || null;
      refresh();
    }
    function disable() {
      generation += 1; cancel(timer); timer = null;
      inflight?.abort?.(); inflight = null;
      off?.(); off = null; store = null; config = null; currentKey = null;
      status = { state: 'off', message: '', payload: null };
    }
    return { enable, disable, refresh, get store() { return store; }, get status() { return status; }, get config() { return config; } };
  }
  function statusText(controller) {
    const s = controller.status, store = controller.store;
    if (s.state === 'off') return '';
    if (s.state === 'unsupported') return `Footprint off: ${s.message}.`;
    if (s.state === 'loading') return 'Footprint: loading real trades…';
    const p = s.payload, sum = store?.summary();
    const parts = [];
    if (s.state === 'error') parts.push(`Footprint error: ${s.message}`);
    if (p && sum) {
      const source = p.exchange === 'binance' ? 'Binance aggTrades' : p.exchange === 'bybit' ? 'Bybit recent trades + WS' : 'Hyperliquid recent trades + WS';
      parts.push(`Footprint · ${source} · ${sum.complete} complete / ${sum.partial} partial bars · ${p.coverage.missing} of ${p.coverage.requested} requested bars missing (blank)`);
      parts.push(`group ${p.group_ticks}×${p.tick_size}`);
      if (sum.klineExact || sum.klineMismatch) parts.push(`venue kline check: ${sum.klineExact} exact / ${sum.klineMismatch} differ`);
      if (sum.gaps) parts.push(`${sum.gaps} live id gap(s) seen → affected bar marked partial, then re-fetched`);
      if (p.truncated) parts.push('budget-truncated history (refresh extends)');
      (p.warnings || []).filter(w => typeof w === 'string').forEach(w => parts.push(w));
    }
    return parts.join(' · ');
  }
  return { dec, fmt, pct, normalizeTrade, createBar, addTrade, barFromServer, groupedLevels, settingsFrom, analyze, barPayload,
    niceGroup, chooseDetail, analysisSettings, decimalText, LiveFootprint, draw, createController, statusText, INTERVALS_MS };
});
