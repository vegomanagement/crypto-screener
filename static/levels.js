/* Shared price-levels store (contract: docs/SCALPING_PHASE2_PLAN.md, "Shared contract").
 * NOTE: this copy was written on the chart-drawings branch strictly from the spec because the
 * DOM-side branch owns the canonical file; the two are reconciled at merge.
 * Pure helpers + a tiny storage adapter. View-only: levels never place orders.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.ScreenerLevels = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const KEY = 'screener.levels.v1';
  const VERSION = 1;
  const CAP = 500;
  const EXCHANGES = ['binance', 'bybit', 'hyperliquid'];
  const FIELDS = ['id', 'exchange', 'symbol', 'price', 'kind', 'source', 'color', 'width', 'repeat', 'armed', 'createdAt', 'triggeredAt', 'note'];
  const DECIMAL = /^(?:0|[1-9]\d*)(?:\.\d+)?$/;

  // Exact decimal string -> { int: BigInt, scale } ; null when not a positive finite decimal.
  function parseDecimal(value) {
    const text = typeof value === 'number' ? (Number.isFinite(value) ? numberToDecimal(value) : '') : value;
    if (typeof text !== 'string' || !DECIMAL.test(text)) return null;
    const [whole, frac = ''] = text.split('.');
    return { int: BigInt(whole + frac), scale: frac.length };
  }
  // Shortest round-trip representation, without exponent notation.
  function numberToDecimal(value) {
    if (!Number.isFinite(value) || value < 0) return '';
    const text = String(value);
    if (!/e/i.test(text)) return text;
    return value.toFixed(20).replace(/\.?0+$/, '');
  }
  function align(a, b) {
    const scale = Math.max(a.scale, b.scale);
    return [a.int * 10n ** BigInt(scale - a.scale), b.int * 10n ** BigInt(scale - b.scale)];
  }
  function compareDecimal(left, right) {
    const a = parseDecimal(left), b = parseDecimal(right);
    if (!a || !b) return null;
    const [x, y] = align(a, b);
    return x < y ? -1 : x > y ? 1 : 0;
  }
  function positivePrice(value) {
    const parsed = typeof value === 'string' ? parseDecimal(value) : null;
    return Boolean(parsed && parsed.int > 0n);
  }
  // Inclusive cross: the level lies within [min(prev, price), max(prev, price)].
  // Exact decimal comparison (BigInt scaling); no floats are compared.
  function crossed(level, prevPrice, price) {
    const target = level && typeof level === 'object' ? level.price : level;
    const l = parseDecimal(target), p = parseDecimal(prevPrice), c = parseDecimal(price);
    if (!l || !p || !c) return false;
    const scale = Math.max(l.scale, p.scale, c.scale);
    const up = d => d.int * 10n ** BigInt(scale - d.scale);
    const lv = up(l), a = up(p), b = up(c);
    const lo = a < b ? a : b, hi = a < b ? b : a;
    return lo <= lv && lv <= hi;
  }

  function validLevel(level) {
    if (!level || typeof level !== 'object' || Array.isArray(level)) return false;
    const keys = Object.keys(level);
    if (keys.length !== FIELDS.length || !keys.every(key => FIELDS.includes(key))) return false;
    return typeof level.id === 'string' && level.id.length > 0 && level.id.length <= 64
      && EXCHANGES.includes(level.exchange)
      && typeof level.symbol === 'string' && level.symbol.length > 0 && level.symbol.length <= 80
      && positivePrice(level.price)
      && ['simple', 'signal'].includes(level.kind)
      && ['dom', 'chart'].includes(level.source)
      && typeof level.color === 'string' && /^#[0-9a-fA-F]{6}$/.test(level.color)
      && Number.isInteger(level.width) && level.width >= 1 && level.width <= 4
      && typeof level.repeat === 'boolean' && typeof level.armed === 'boolean'
      && Number.isFinite(level.createdAt) && level.createdAt >= 0
      && (level.triggeredAt === null || (Number.isFinite(level.triggeredAt) && level.triggeredAt >= 0))
      && typeof level.note === 'string' && level.note.length <= 80;
  }
  // Oldest triggered levels are dropped first, then the oldest created ones.
  function enforceCap(levels) {
    if (levels.length <= CAP) return levels;
    const order = [...levels].sort((a, b) => {
      const at = a.triggeredAt !== null, bt = b.triggeredAt !== null;
      if (at !== bt) return at ? -1 : 1;
      if (at && a.triggeredAt !== b.triggeredAt) return a.triggeredAt - b.triggeredAt;
      return a.createdAt - b.createdAt;
    });
    const drop = new Set(order.slice(0, levels.length - CAP).map(level => level.id));
    return levels.filter(level => !drop.has(level.id));
  }

  function createStore(options = {}) {
    const storage = options.storage === undefined ? (typeof window !== 'undefined' && window.localStorage ? window.localStorage : null) : options.storage;
    const now = options.now || Date.now;
    const random = options.random || Math.random;
    const target = options.eventTarget === undefined ? (typeof window !== 'undefined' ? window : null) : options.eventTarget;
    const listeners = new Set();
    let memory = { version: VERSION, levels: [] };
    let counter = 0;

    function read() {
      if (!storage) return { version: VERSION, levels: memory.levels.map(level => ({ ...level })) };
      try {
        const raw = JSON.parse(storage.getItem(KEY) || 'null');
        if (!raw || raw.version !== VERSION || !Array.isArray(raw.levels)) return { version: VERSION, levels: [] };
        const seen = new Set();
        const levels = raw.levels.filter(level => validLevel(level) && !seen.has(level.id) && seen.add(level.id));
        return { version: VERSION, levels: enforceCap(levels) };
      } catch (_) { return { version: VERSION, levels: [] }; }
    }
    function write(levels, change) {
      const data = { version: VERSION, levels: enforceCap(levels) };
      if (storage) {
        try { storage.setItem(KEY, JSON.stringify(data)); } catch (_) { memory = data; }
      } else memory = data;
      notify(change);
      return data;
    }
    function notify(change) { listeners.forEach(fn => { try { fn(change); } catch (_) { /* listener errors stay local */ } }); }
    function newId() {
      counter += 1;
      return `lv-${now().toString(36)}-${counter.toString(36)}-${Math.floor(random() * 1e9).toString(36)}`;
    }
    const load = () => read();
    function list(filter = {}) {
      return read().levels.filter(level => (!filter.exchange || level.exchange === filter.exchange) && (!filter.symbol || level.symbol === filter.symbol));
    }
    function add(input) {
      if (!input || typeof input !== 'object' || Object.keys(input).some(key => !FIELDS.includes(key))) throw new Error('levels: unknown field');
      const level = { id: input.id ?? newId(), exchange: input.exchange, symbol: input.symbol, price: input.price,
        kind: input.kind ?? 'simple', source: input.source ?? 'chart', color: input.color ?? (input.kind === 'signal' ? '#f0b429' : '#4f8cff'),
        width: input.width ?? 1, repeat: input.repeat ?? false, armed: input.armed ?? (input.kind === 'signal'),
        createdAt: input.createdAt ?? now(), triggeredAt: input.triggeredAt ?? null, note: input.note ?? '' };
      if (!validLevel(level)) throw new Error('levels: invalid level');
      const current = read().levels;
      if (current.some(item => item.id === level.id)) throw new Error('levels: duplicate id');
      write([...current, level], { type: 'add', id: level.id, origin: 'local' });
      return { ...level };
    }
    function update(id, patch) {
      if (!patch || typeof patch !== 'object' || Object.keys(patch).some(key => !FIELDS.includes(key) || key === 'id')) throw new Error('levels: unknown field');
      const current = read().levels;
      const index = current.findIndex(level => level.id === id);
      if (index < 0) return null;
      const next = { ...current[index], ...patch };
      if (!validLevel(next)) throw new Error('levels: invalid level');
      current[index] = next;
      write(current, { type: 'update', id, origin: 'local' });
      return { ...next };
    }
    function remove(id) {
      const current = read().levels;
      const next = current.filter(level => level.id !== id);
      if (next.length === current.length) return false;
      write(next, { type: 'remove', id, origin: 'local' });
      return true;
    }
    function clear(filter = {}) {
      if (!filter.exchange || !filter.symbol) throw new Error('levels: clear needs exchange and symbol');
      const current = read().levels;
      const next = current.filter(level => !(level.exchange === filter.exchange && level.symbol === filter.symbol && (!filter.kind || level.kind === filter.kind)));
      if (next.length !== current.length) write(next, { type: 'clear', origin: 'local' });
      return current.length - next.length;
    }
    let storageListener = null;
    function subscribe(fn) {
      listeners.add(fn);
      if (!storageListener && target?.addEventListener) {
        storageListener = event => { if (event.key === KEY || event.key === null) notify({ type: 'storage', origin: 'remote' }); };
        target.addEventListener('storage', storageListener);
      }
      return () => {
        listeners.delete(fn);
        if (!listeners.size && storageListener) { target.removeEventListener('storage', storageListener); storageListener = null; }
      };
    }
    return { load, list, add, update, remove, clear, subscribe, crossed };
  }

  const shared = createStore();
  return { KEY, VERSION, CAP, FIELDS, createStore, crossed, compareDecimal, parseDecimal, validLevel, numberToDecimal,
    load: shared.load, list: shared.list, add: shared.add, update: shared.update, remove: shared.remove, clear: shared.clear, subscribe: shared.subscribe };
});
