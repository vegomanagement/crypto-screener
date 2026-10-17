/* Shared price-levels store (contract: docs/SCALPING_PHASE2_PLAN.md, "Shared contract").
 * Used by /scalp (source 'dom') and /ui (source 'chart'). Pure logic plus a tiny storage adapter:
 *   localStorage['screener.levels.v1'] = JSON { version: 1, levels: Level[] }
 *   Level = { id, exchange, symbol, price (exact decimal string), kind, source, color, width, repeat,
 *             armed, createdAt, triggeredAt, note }
 * Prices are compared as exact decimals (BigInt scaling), never as floats.
 * View only: a level is a drawing/alert marker, never an order.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(root);
  else root.ScreenerLevels = factory(root);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';
  const KEY = 'screener.levels.v1';
  const VERSION = 1;
  const MAX_LEVELS = 500;
  const EXCHANGES = ['binance', 'bybit', 'hyperliquid'];
  const KINDS = ['simple', 'signal'];
  const SOURCES = ['dom', 'chart'];
  const FIELDS = ['id', 'exchange', 'symbol', 'price', 'kind', 'source', 'color', 'width', 'repeat', 'armed', 'createdAt', 'triggeredAt', 'note'];
  const DEFAULT_COLORS = { simple: '#527dff', signal: '#f5a623' };
  const TRIGGERED_COLOR = '#5a6273';

  // ── exact decimals ────────────────────────────────────────────────────────
  const DEC = /^(?:0|[1-9]\d*)(?:\.\d+)?$/;
  /** Finite number → plain decimal string from its shortest round-trip form (handles 1e-7, 1e21). */
  function numberToDecimal(n) {
    if (typeof n !== 'number' || !Number.isFinite(n) || n < 0) return null;
    const s = String(n);
    if (!/e/i.test(s)) return s;
    const [mant, e] = s.split(/e/i), exp = Number(e), [i, f = ''] = mant.split('.');
    const digits = (i + f).replace(/^0+(?=\d)/, ''), point = i.length + exp;
    if (point <= 0) return `0.${'0'.repeat(-point)}${digits}`;
    if (point >= digits.length) return digits + '0'.repeat(point - digits.length);
    return `${digits.slice(0, point)}.${digits.slice(point)}`;
  }
  /** Decimal string (or finite number) → { n: BigInt, scale } or null. Negative / malformed → null. */
  function parseDecimal(value) {
    const s = typeof value === 'number' ? numberToDecimal(value) : typeof value === 'string' ? value.trim() : null;
    if (s === null || s.length > 60 || !DEC.test(s)) return null;
    const [i, f = ''] = s.split('.');
    return { n: BigInt(i + f), scale: f.length };
  }
  function align(a, b) {
    const scale = Math.max(a.scale, b.scale);
    return [a.n * 10n ** BigInt(scale - a.scale), b.n * 10n ** BigInt(scale - b.scale), scale];
  }
  /** -1 / 0 / 1 for exact decimals; null when either side is not a valid decimal. */
  function compareDecimal(a, b) {
    const x = typeof a === 'object' && a ? a : parseDecimal(a), y = typeof b === 'object' && b ? b : parseDecimal(b);
    if (!x || !y) return null;
    const [p, q] = align(x, y);
    return p < q ? -1 : p > q ? 1 : 0;
  }
  function formatDecimal({ n, scale }) {
    if (scale === 0) return n.toString();
    const s = n.toString().padStart(scale + 1, '0');
    return `${s.slice(0, -scale)}.${s.slice(-scale)}`;
  }
  /** Exact price of tick index `idx` for a tick given as a decimal string ("0.10" → idx·0.10). */
  function fromTicks(idx, tick) {
    const t = parseDecimal(tick);
    if (!t || !Number.isSafeInteger(idx) || idx <= 0) return null;
    return formatDecimal({ n: BigInt(idx) * t.n, scale: t.scale });
  }
  /** |a − b| ≥ step, exactly. */
  function distanceAtLeast(a, b, step) {
    const x = parseDecimal(a), y = parseDecimal(b), s = parseDecimal(step);
    if (!x || !y || !s) return false;
    const [p, q, scale] = align(x, y), [d, st] = align({ n: p > q ? p - q : q - p, scale }, s);
    return d >= st;
  }
  const positiveDecimal = v => typeof v === 'string' && parseDecimal(v)?.n > 0n;

  /**
   * Inclusive cross of `level.price` between two consecutive trade prices: true when the level lies in
   * [min(prev, price), max(prev, price)], so gaps/jumps over the level count. Without a previous price
   * only an exact touch counts.
   */
  function crossed(level, prevPrice, price) {
    const L = parseDecimal(level?.price), p = parseDecimal(price);
    if (!L || !p) return false;
    const prev = prevPrice === null || prevPrice === undefined ? null : parseDecimal(prevPrice);
    if (!prev) return compareDecimal(p, L) === 0;
    const lo = compareDecimal(prev, p) <= 0 ? prev : p, hi = lo === prev ? p : prev;
    return compareDecimal(lo, L) <= 0 && compareDecimal(L, hi) <= 0;
  }

  // ── validation ────────────────────────────────────────────────────────────
  function fail(msg) { throw new Error(`levels: ${msg}`); }
  /** Full Level from a (partial) input with defaults. Throws on unknown fields or invalid values. */
  function normalize(raw, now = Date.now) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) fail('level must be an object');
    for (const k of Object.keys(raw)) if (!FIELDS.includes(k)) fail(`unknown field ${k}`);
    const kind = raw.kind ?? 'simple';
    const out = {
      id: raw.id ?? null, exchange: raw.exchange, symbol: raw.symbol, price: raw.price, kind, source: raw.source,
      color: raw.color ?? DEFAULT_COLORS[kind], width: raw.width ?? 1, repeat: raw.repeat ?? false,
      armed: raw.armed ?? kind === 'signal', createdAt: raw.createdAt ?? now(), triggeredAt: raw.triggeredAt ?? null, note: raw.note ?? ''
    };
    if (typeof out.id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(out.id)) fail('invalid id');
    if (!EXCHANGES.includes(out.exchange)) fail('invalid exchange');
    if (typeof out.symbol !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/.test(out.symbol)) fail('invalid symbol');
    if (!positiveDecimal(out.price)) fail('price must be a positive decimal string');
    if (!KINDS.includes(out.kind)) fail('invalid kind');
    if (!SOURCES.includes(out.source)) fail('invalid source');
    if (typeof out.color !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(out.color)) fail('invalid color');
    if (!Number.isInteger(out.width) || out.width < 1 || out.width > 4) fail('invalid width');
    if (typeof out.repeat !== 'boolean' || typeof out.armed !== 'boolean') fail('repeat/armed must be boolean');
    if (!Number.isFinite(out.createdAt) || out.createdAt < 0) fail('invalid createdAt');
    if (out.triggeredAt !== null && (!Number.isFinite(out.triggeredAt) || out.triggeredAt < 0)) fail('invalid triggeredAt');
    if (typeof out.note !== 'string' || out.note.length > 80) fail('note must be a string ≤ 80 chars');
    out.color = out.color.toLowerCase(); out.price = out.price.trim();
    return out;
  }
  function valid(level) { try { normalize(level); return true; } catch (_) { return false; } }
  /** Keep MAX_LEVELS: drop the oldest triggered levels first, then the oldest created. */
  function capLevels(levels, max = MAX_LEVELS) {
    if (levels.length <= max) return levels;
    const order = [...levels].sort((a, b) => {
      const ta = a.triggeredAt !== null, tb = b.triggeredAt !== null;
      if (ta !== tb) return ta ? -1 : 1;
      return ta ? a.triggeredAt - b.triggeredAt : a.createdAt - b.createdAt;
    });
    const drop = new Set(order.slice(0, levels.length - max).map(l => l.id));
    return levels.filter(l => !drop.has(l.id));
  }

  // ── storage adapter ───────────────────────────────────────────────────────
  function memoryStorage() {
    const m = new Map();
    return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => { m.set(k, String(v)); }, removeItem: k => { m.delete(k); } };
  }
  function defaultStorage() {
    // Browser only (window === globalThis); in node the default store is in-memory.
    try { if (root && root.window === root && root.localStorage) { root.localStorage.getItem(KEY); return root.localStorage; } } catch (_) { /* blocked */ }
    return null;
  }
  let seq = 0;
  const makeIdDefault = now => `l${now().toString(36)}${(seq++).toString(36)}${Math.floor(Math.random() * 1679616).toString(36)}`;

  /**
   * Store over a Storage-like object. Every operation re-reads storage before writing, so changes made
   * by another tab are not clobbered. If storage is unavailable (or a write fails) the store keeps working
   * in memory for this page and reports it through `lastError`.
   * options: { storage, target (for 'storage' events), now, makeId }
   */
  function createStore(options = {}) {
    const now = options.now || Date.now, makeId = options.makeId || (() => makeIdDefault(now));
    const target = options.target === undefined ? root : options.target;
    let storage = options.storage === undefined ? defaultStorage() : options.storage;
    const fallback = memoryStorage();
    const listeners = new Set();
    let bound = null;
    const store = { lastError: storage ? null : 'storage unavailable · levels last for this page only' };
    const src = () => storage || fallback;

    function readRaw() {
      try { return JSON.parse(src().getItem(KEY) || 'null'); } catch (_) { return null; }
    }
    /** { version, levels } with invalid / duplicate entries dropped (never throws). */
    function load() {
      const raw = readRaw();
      if (!raw || typeof raw !== 'object' || raw.version !== VERSION || !Array.isArray(raw.levels)) return { version: VERSION, levels: [] };
      const seen = new Set(), levels = [];
      for (const item of raw.levels) {
        if (!valid(item) || seen.has(item.id)) continue;
        seen.add(item.id); levels.push(normalize(item, now));
      }
      return { version: VERSION, levels };
    }
    function write(levels, info) {
      const text = JSON.stringify({ version: VERSION, levels });
      try { src().setItem(KEY, text); }
      catch (_) {
        if (storage) { storage = null; store.lastError = 'storage write failed · levels last for this page only'; }
        fallback.setItem(KEY, text);
      }
      emit({ origin: 'local', ...info });
    }
    function emit(info) {
      const all = load().levels;
      for (const fn of [...listeners]) { try { fn(all, info); } catch (_) { /* a broken listener must not stop the others */ } }
    }
    function list({ exchange, symbol, kind } = {}) {
      return load().levels.filter(l => (exchange === undefined || l.exchange === exchange) && (symbol === undefined || l.symbol === symbol)
        && (kind === undefined || l.kind === kind));
    }
    function add(input) {
      const level = normalize({ ...input, id: input?.id ?? makeId() }, now);
      const levels = load().levels;
      if (levels.some(l => l.id === level.id)) fail(`duplicate id ${level.id}`);
      const next = capLevels([...levels, level]);
      write(next, { op: 'add', id: level.id, dropped: levels.length + 1 - next.length });
      return level;
    }
    function update(id, patch) {
      if (!patch || typeof patch !== 'object') fail('patch must be an object');
      if ('id' in patch && patch.id !== id) fail('id cannot change');
      const levels = load().levels, k = levels.findIndex(l => l.id === id);
      if (k < 0) return null;
      const next = normalize({ ...levels[k], ...patch }, now);
      levels[k] = next; write(levels, { op: 'update', id });
      return next;
    }
    function remove(id) {
      const levels = load().levels, next = levels.filter(l => l.id !== id);
      if (next.length === levels.length) return false;
      write(next, { op: 'remove', id });
      return true;
    }
    function clear({ exchange, symbol, kind } = {}) {
      if (!EXCHANGES.includes(exchange) || typeof symbol !== 'string') fail('clear needs exchange and symbol');
      const levels = load().levels, next = levels.filter(l => !(l.exchange === exchange && l.symbol === symbol && (kind === undefined || l.kind === kind)));
      if (next.length !== levels.length) write(next, { op: 'clear', exchange, symbol, kind: kind ?? null });
      return levels.length - next.length;
    }
    function onStorage(event) {
      if (event && event.key !== KEY && event.key !== null) return;
      emit({ origin: 'storage' });
    }
    function subscribe(fn) {
      if (typeof fn !== 'function') fail('subscribe needs a function');
      listeners.add(fn);
      if (!bound && target && typeof target.addEventListener === 'function') { bound = onStorage; target.addEventListener('storage', bound); }
      return () => {
        listeners.delete(fn);
        if (!listeners.size && bound) { target.removeEventListener?.('storage', bound); bound = null; }
      };
    }
    return Object.assign(store, { load, list, add, update, remove, clear, subscribe, crossed, _onStorage: onStorage });
  }

  const shared = createStore();
  return { KEY, VERSION, MAX_LEVELS, EXCHANGES, KINDS, SOURCES, FIELDS, DEFAULT_COLORS, TRIGGERED_COLOR,
    numberToDecimal, parseDecimal, compareDecimal, formatDecimal, fromTicks, distanceAtLeast, crossed, normalize, valid, capLevels,
    createStore, memoryStorage, store: shared,
    load: shared.load, list: shared.list, add: shared.add, update: shared.update, remove: shared.remove, clear: shared.clear,
    subscribe: shared.subscribe };
});
