/* Drawing tools — core: tool registry, storage schema v2 (+ lossless v1 migration), per-interval
 * visibility, undo/redo history, style templates, generic build/hit-test/handles and level sync.
 * Tool modules (static/drawings/tools-*.js) register themselves here. No DOM.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./geometry.js'));
  else root.WorkspaceDrawingsCore = factory(root.WorkspaceDrawingsGeometry);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (G) {
  'use strict';
  const KEY_V1 = 'screener.drawings.v1';
  const KEY = 'screener.drawings.v2';
  const VERSION = 2;
  const PER_SYMBOL = 1000;
  const MAX_POINTS = 400;
  const LEVEL_TYPES = ['hline', 'hray'];
  const FIELDS = ['id', 'type', 'points', 'style', 'text', 'opts', 'name', 'locked', 'hidden', 'visibility', 'level', 'levelId', 'createdAt'];
  const INTERVAL_GROUPS = ['seconds', 'minutes', 'hours', 'days', 'weeks'];
  const DASH_NAMES = ['solid', 'dashed', 'dotted'];
  const COLOR = /^#[0-9a-fA-F]{6}$/;
  const DEFAULT_STYLE = { color: '#4f8cff', width: 1, dash: 'solid', fill: null, fillAlpha: 0.12, textColor: null, fontSize: 12 };
  const symbolKey = (exchange, symbol) => `${exchange}:${symbol}`;

  // ── registry ──
  const TOOLS = new Map();
  function registerTool(def) {
    if (!def || !/^[a-z][a-z0-9_]{0,31}$/.test(def.type) || typeof def.build !== 'function') throw new Error('drawings: bad tool definition');
    TOOLS.set(def.type, { mode: 'click', group: 'lines', label: def.type, icon: '•', options: [], ...def,
      defaults: { style: { ...(def.defaults?.style || {}) }, opts: { ...(def.defaults?.opts || {}) }, text: def.defaults?.text ?? '' } });
    return TOOLS.get(def.type);
  }
  const getTool = type => TOOLS.get(type) || null;
  const toolList = () => [...TOOLS.values()];
  function pointRange(def) {
    if (!def) return [1, MAX_POINTS];
    if (def.mode === 'multi' || def.mode === 'free') return [def.minPoints || 2, def.maxPoints || MAX_POINTS];
    return [def.storedPoints || def.points, def.storedPoints || def.points];
  }
  const optsOf = d => ({ ...(getTool(d.type)?.defaults.opts || {}), ...(d.opts || {}) });

  // ── schema v2 ──
  function validPoint(point) {
    return point && typeof point === 'object' && Object.keys(point).length === 2
      && Number.isSafeInteger(point.time) && point.time >= 0 && G.positiveDecimal(point.price);
  }
  function normalizeStyle(raw = {}, base = DEFAULT_STYLE) {
    const s = { ...DEFAULT_STYLE, ...base };
    if (raw && typeof raw === 'object') {
      if (COLOR.test(raw.color)) s.color = raw.color.toLowerCase();
      if (Number.isInteger(raw.width) && raw.width >= 1 && raw.width <= 4) s.width = raw.width;
      if (DASH_NAMES.includes(raw.dash)) s.dash = raw.dash;
      if (raw.fill === null || COLOR.test(raw.fill)) s.fill = raw.fill === null ? null : raw.fill.toLowerCase();
      if (Number.isFinite(raw.fillAlpha) && raw.fillAlpha >= 0 && raw.fillAlpha <= 1) s.fillAlpha = raw.fillAlpha;
      if (raw.textColor === null || COLOR.test(raw.textColor)) s.textColor = raw.textColor === null ? null : raw.textColor.toLowerCase();
      if (Number.isInteger(raw.fontSize) && raw.fontSize >= 8 && raw.fontSize <= 40) s.fontSize = raw.fontSize;
    }
    return s;
  }
  // Tool options: small JSON-only objects (numbers, strings, booleans, arrays of those / of flat objects).
  function cleanOpts(raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
    const out = {};
    const flat = v => v === null || ['number', 'string', 'boolean'].includes(typeof v);
    for (const [k, v] of Object.entries(raw)) {
      if (!/^[A-Za-z][\w]{0,31}$/.test(k)) continue;
      if (typeof v === 'number' && !Number.isFinite(v)) continue;
      if (flat(v) && (typeof v !== 'string' || v.length <= 200)) out[k] = v;
      else if (Array.isArray(v) && v.length <= 40 && v.every(item => flat(item) || (item && typeof item === 'object' && !Array.isArray(item) && Object.values(item).every(flat)))) out[k] = JSON.parse(JSON.stringify(v));
    }
    return JSON.stringify(out).length <= 6000 ? out : {};
  }
  function normalizeVisibility(raw) {
    const out = {};
    INTERVAL_GROUPS.forEach(g => { out[g] = !(raw && raw[g] === false); });
    return out;
  }
  // Returns a clean v2 drawing or null (unknown keys dropped; bad points/ids reject the drawing).
  function normalizeDrawing(d) {
    if (!d || typeof d !== 'object' || Array.isArray(d)) return null;
    if (typeof d.id !== 'string' || !/^[\w-]{1,64}$/.test(d.id) || typeof d.type !== 'string' || !/^[a-z][a-z0-9_]{0,31}$/.test(d.type)) return null;
    const def = getTool(d.type), [min, max] = pointRange(def);
    if (!Array.isArray(d.points) || d.points.length < min || d.points.length > max || !d.points.every(validPoint)) return null;
    const level = d.level === true && LEVEL_TYPES.includes(d.type);
    const levelId = level && typeof d.levelId === 'string' && d.levelId.length <= 64 ? d.levelId : null;
    return { id: d.id, type: d.type, points: d.points.map(p => ({ time: p.time, price: p.price })),
      style: normalizeStyle(d.style, def?.defaults.style), text: typeof d.text === 'string' ? d.text.slice(0, 2000) : '',
      opts: cleanOpts(d.opts), name: typeof d.name === 'string' ? d.name.slice(0, 80) : '', locked: d.locked === true, hidden: d.hidden === true,
      visibility: normalizeVisibility(d.visibility), level, levelId,
      createdAt: Number.isSafeInteger(d.createdAt) && d.createdAt >= 0 ? d.createdAt : 0 };
  }
  const validDrawing = d => { const n = normalizeDrawing(d); return Boolean(n) && JSON.stringify(n) === JSON.stringify(d); };
  const newId = () => `dr-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e9).toString(36)}`;
  function makeDrawing(type, points, options = {}) {
    const def = getTool(type);
    if (!def) throw new Error(`drawings: unknown tool ${type}`);
    const d = normalizeDrawing({ id: options.id || newId(), type, points, style: { ...def.defaults.style, ...(options.style || {}) },
      text: options.text ?? def.defaults.text, opts: { ...(options.opts || {}) }, name: options.name || '', locked: Boolean(options.locked),
      hidden: Boolean(options.hidden), visibility: options.visibility, level: Boolean(options.level), levelId: options.levelId ?? null,
      createdAt: options.createdAt ?? Date.now() });
    if (!d || (options.level && !d.level)) throw new Error('drawings: invalid drawing');
    return d;
  }
  const clone = d => JSON.parse(JSON.stringify(d));

  // ── persistence: { version:2, symbols: { "venue:SYMBOL": { hidden, locked, items } } } ──
  const SYMBOL_KEY = /^(binance|bybit|hyperliquid):[^\s]{1,80}$/;
  function parseState(raw) {
    const empty = { version: VERSION, symbols: {} };
    let data;
    try { data = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch (_) { return empty; }
    if (!data || data.version !== VERSION || !data.symbols || typeof data.symbols !== 'object') return empty;
    const out = { version: VERSION, symbols: {} };
    for (const [key, entry] of Object.entries(data.symbols)) {
      if (!SYMBOL_KEY.test(key) || !entry || !Array.isArray(entry.items)) continue;
      const seen = new Set(), items = [];
      for (const raw of entry.items) { const d = normalizeDrawing(raw); if (d && !seen.has(d.id)) { seen.add(d.id); items.push(d); } }
      out.symbols[key] = { hidden: entry.hidden === true, locked: entry.locked === true, items: items.slice(-PER_SYMBOL) };
    }
    return out;
  }
  const serialize = data => JSON.stringify(parseState(data));
  // v1 → v2: 'line' becomes 'trend'; color/width move into style; every other field is kept.
  const V1_TYPES = { line: 'trend', ray: 'ray', hline: 'hline', hray: 'hray', rect: 'rect' };
  function migrateV1(raw) {
    let data;
    try { data = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch (_) { return null; }
    if (!data || data.version !== 1 || !data.symbols || typeof data.symbols !== 'object') return null;
    const out = { version: VERSION, symbols: {} };
    for (const [key, entry] of Object.entries(data.symbols)) {
      if (!entry || !Array.isArray(entry.items)) continue;
      out.symbols[key] = { hidden: entry.hidden === true, locked: false, items: entry.items.filter(d => d && V1_TYPES[d.type]).map(d => ({
        id: d.id, type: V1_TYPES[d.type], points: d.points, style: { ...DEFAULT_STYLE, color: d.color, width: d.width }, text: '', opts: {}, name: '',
        locked: d.locked === true, hidden: false, visibility: normalizeVisibility(null), level: d.level === true, levelId: d.levelId ?? null, createdAt: d.createdAt })) };
    }
    return parseState(out);
  }
  function createStore(storage) {
    let memory = { version: VERSION, symbols: {} };
    function read() {
      if (!storage) return parseState(memory);
      try {
        const v2 = storage.getItem(KEY);
        if (v2 !== null && v2 !== undefined) return parseState(v2);
        const migrated = migrateV1(storage.getItem(KEY_V1) || 'null');
        if (migrated) { write(migrated); return migrated; } // v1 key is left untouched as a backup
        return parseState(memory);
      } catch (_) { return parseState(memory); }
    }
    function write(data) { memory = parseState(data); if (storage) { try { storage.setItem(KEY, JSON.stringify(memory)); } catch (_) { /* session-only */ } } }
    return {
      read, write,
      get(exchange, symbol) { const e = read().symbols[symbolKey(exchange, symbol)]; return e ? { hidden: e.hidden, locked: e.locked, items: e.items.map(clone) } : { hidden: false, locked: false, items: [] }; },
      set(exchange, symbol, entry) {
        const data = read(), key = symbolKey(exchange, symbol);
        if (!entry.items.length && !entry.hidden && !entry.locked) delete data.symbols[key];
        else data.symbols[key] = { hidden: Boolean(entry.hidden), locked: Boolean(entry.locked), items: entry.items.map(normalizeDrawing).filter(Boolean).slice(-PER_SYMBOL) };
        write(data);
      }
    };
  }

  // ── per-interval visibility ──
  function intervalGroup(interval) {
    const v = String(interval ?? '');
    if (/^\d+s$/i.test(v)) return 'seconds';
    if (/^\d+$/.test(v)) return Number(v) < 60 ? 'minutes' : 'hours';
    if (/^\d+m$/.test(v)) return 'minutes';
    if (/^\d+h$/i.test(v)) return 'hours';
    if (/^\d*D$/i.test(v)) return 'days';
    if (/^\d*[WM]$/.test(v) || /^\d*w$/.test(v)) return 'weeks';
    return 'minutes';
  }
  const visibleOn = (d, interval) => !d.hidden && (d.visibility?.[intervalGroup(interval)] ?? true) !== false;

  // ── undo/redo: whole-list snapshots (items are small; ≤ 1000 per symbol) ──
  function createHistory(limit = 100) {
    let past = [], future = [];
    return {
      record(items) { past.push(JSON.stringify(items)); if (past.length > limit) past.shift(); future = []; },
      undo(current) { if (!past.length) return null; future.push(JSON.stringify(current)); return JSON.parse(past.pop()); },
      redo(current) { if (!future.length) return null; past.push(JSON.stringify(current)); return JSON.parse(future.pop()); },
      get canUndo() { return past.length > 0; }, get canRedo() { return future.length > 0; },
      clear() { past = []; future = []; }
    };
  }

  // ── style templates: named { style, opts } per tool; one may be the tool's default ──
  const TEMPLATES_KEY = 'screener.drawings.templates.v1';
  function createTemplates(storage) {
    let memory = { tools: {}, defaults: {} };
    const read = () => {
      try { const raw = JSON.parse(storage?.getItem(TEMPLATES_KEY) || 'null'); if (raw && raw.tools && raw.defaults) return raw; } catch (_) { /* fall through */ }
      return JSON.parse(JSON.stringify(memory));
    };
    const write = data => { memory = data; try { storage?.setItem(TEMPLATES_KEY, JSON.stringify(data)); } catch (_) { /* session only */ } };
    const okName = name => typeof name === 'string' && name.trim().length > 0 && name.length <= 40;
    return {
      list: type => Object.keys(read().tools[type] || {}),
      get: (type, name) => read().tools[type]?.[name] || null,
      save(type, name, d) {
        if (!okName(name)) throw new Error('drawings: template name must be 1–40 characters');
        const data = read(); data.tools[type] = { ...(data.tools[type] || {}), [name]: { style: normalizeStyle(d.style), opts: cleanOpts(d.opts), text: d.text || '' } };
        write(data); return data.tools[type][name];
      },
      remove(type, name) { const data = read(); if (data.tools[type]) delete data.tools[type][name]; if (data.defaults[type] === name) delete data.defaults[type]; write(data); },
      setDefault(type, name) { const data = read(); if (name === null) delete data.defaults[type]; else if (data.tools[type]?.[name]) data.defaults[type] = name; write(data); },
      defaultName: type => read().defaults[type] || null,
      defaultFor(type) { const data = read(), name = data.defaults[type]; return name ? data.tools[type]?.[name] || null : null; },
      apply(d, name) { const t = read().tools[d.type]?.[name]; return t ? { ...d, style: normalizeStyle(t.style), opts: cleanOpts(t.opts), text: d.text || t.text } : d; }
    };
  }

  // ── generic tool plumbing (pixel space) ──
  const px = (view, p) => ({ x: view.mapper.x(p.time), y: view.mapper.y(p.price) });
  function build(d, view) {
    const def = getTool(d.type);
    if (!def) return [];
    const pts = d.points.map(p => px(view, p));
    if (pts.some(p => p.y === null || !Number.isFinite(p.x))) return [];
    try { return def.build(d, view, pts, optsOf(d), d.style) || []; } catch (_) { return []; }
  }
  function handles(d, view) {
    const def = getTool(d.type);
    if (def?.handles) return def.handles(d, view, optsOf(d)) || [];
    return d.points.map((p, i) => ({ ...px(view, p), part: `p${i}` })).filter(h => h.y !== null && Number.isFinite(h.x));
  }
  // Topmost first; handles beat bodies. primsFor lets the renderer's per-frame cache be reused.
  function hitTest(items, view, x, y, tolerance = 6, primsFor = d => build(d, view)) {
    for (let i = items.length - 1; i >= 0; i -= 1) {
      const d = items[i];
      if (d.hidden) continue;
      const h = handles(d, view).find(q => Math.hypot(q.x - x, q.y - y) <= tolerance + 1);
      if (h) return { id: d.id, part: h.part };
      if (primsFor(d).some(p => G.hitPrimitive(p, x, y, tolerance))) return { id: d.id, part: 'body' };
    }
    return null;
  }
  function dragHandle(d, part, point, view) {
    const def = getTool(d.type), index = Number(String(part).slice(1));
    if (def?.dragHandle) return def.dragHandle(d, index, point, view, optsOf(d));
    return d.points.map((p, i) => (i === index ? { time: point.time, price: point.price } : p));
  }
  function reorder(items, id, where) {
    const i = items.findIndex(d => d.id === id);
    if (i < 0) return items;
    const out = items.slice(), [d] = out.splice(i, 1);
    const j = where === 'top' ? out.length : where === 'bottom' ? 0 : where === 'up' ? Math.min(out.length, i + 1) : Math.max(0, i - 1);
    out.splice(j, 0, d);
    return out;
  }

  // ── level sync (shared levels store is the source of truth for linked lines) ──
  function levelFromDrawing(d, exchange, symbol) {
    return { exchange, symbol, price: d.points[0].price, kind: 'simple', source: 'chart', color: d.style.color, width: Math.min(4, d.style.width), note: d.type === 'hray' ? 'chart ray' : 'chart line' };
  }
  function reconcile(items, levels) {
    const byId = new Map(levels.map(level => [level.id, level]));
    let changed = false;
    const out = [];
    for (const d of items) {
      if (!d.levelId) { out.push(d); continue; }
      const level = byId.get(d.levelId);
      if (!level) { changed = true; continue; }
      if (level.price !== d.points[0].price || level.color !== d.style.color || level.width !== d.style.width) {
        out.push({ ...d, points: d.points.map(p => ({ ...p, price: level.price })), style: { ...d.style, color: level.color, width: level.width } });
        changed = true;
      } else out.push(d);
    }
    const linked = new Set(out.map(d => d.levelId).filter(Boolean));
    const virtual = levels.filter(level => !linked.has(level.id)).map(level => ({ id: `level-${level.id}`, type: 'hline',
      points: [{ time: 0, price: level.price }], style: normalizeStyle({ color: level.color, width: level.width, dash: level.kind === 'signal' ? 'dashed' : 'solid' }),
      text: '', opts: { showPrice: true }, name: `Level ${level.price}`, locked: false, hidden: false, visibility: normalizeVisibility(null), level: true,
      levelId: level.id, createdAt: level.createdAt, virtual: true, kind: level.kind, armed: level.armed }));
    return { items: out, virtual, changed };
  }

  return { ...G, KEY, KEY_V1, VERSION, PER_SYMBOL, MAX_POINTS, LEVEL_TYPES, FIELDS, INTERVAL_GROUPS, DASH_NAMES, DEFAULT_STYLE, symbolKey,
    registerTool, getTool, toolList, pointRange, optsOf, validPoint, normalizeStyle, cleanOpts, normalizeVisibility, normalizeDrawing, validDrawing,
    makeDrawing, clone, newId, parseState, serialize, migrateV1, createStore, intervalGroup, visibleOn, createHistory, TEMPLATES_KEY,
    createTemplates, px, build, handles, hitTest, dragHandle, reorder, levelFromDrawing, reconcile };
});
