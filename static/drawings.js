/* Chart drawing tools for /ui panes: model, persistence, pixel mapping, hit-testing,
 * magnet snapping and price-level sync (via the shared levels store). View-only.
 * Points are stored as { time: ms, price: exact decimal string } per (venue, symbol), so
 * the same drawings appear on every interval of that symbol.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.WorkspaceDrawings = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const KEY = 'screener.drawings.v1';
  const VERSION = 1;
  const PER_SYMBOL = 200;
  const POINTS = { line: 2, ray: 2, hline: 1, hray: 1, rect: 2 };
  const TOOLS = ['line', 'ray', 'hline', 'hray', 'rect', 'ruler'];
  const LEVEL_TYPES = ['hline', 'hray'];
  const FIELDS = ['id', 'type', 'points', 'color', 'width', 'locked', 'level', 'levelId', 'createdAt'];
  const DECIMAL = /^(?:0|[1-9]\d*)(?:\.\d+)?$/;
  const COLOR = /^#[0-9a-fA-F]{6}$/;

  const symbolKey = (exchange, symbol) => `${exchange}:${symbol}`;
  const positiveDecimal = value => typeof value === 'string' && DECIMAL.test(value) && /[1-9]/.test(value);

  // Number -> exact plain decimal string at the chart's precision (no exponent, no trailing zeros).
  function priceString(value, precision = 8) {
    if (typeof value === 'string') return positiveDecimal(value) ? value : null;
    if (!Number.isFinite(value) || value <= 0) return null;
    const digits = Math.max(0, Math.min(12, Math.round(precision)));
    const text = value.toFixed(digits).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
    return positiveDecimal(text) ? text : null;
  }
  // Exact candle values (already decimals from the venue) without float re-rounding.
  function exactValue(value) {
    if (!Number.isFinite(value) || value <= 0) return null;
    const text = String(value);
    return /e/i.test(text) ? priceString(value, 12) : text;
  }

  function validPoint(point) {
    return point && typeof point === 'object' && Object.keys(point).length === 2
      && Number.isSafeInteger(point.time) && point.time >= 0 && positiveDecimal(point.price);
  }
  function validDrawing(d) {
    if (!d || typeof d !== 'object' || Array.isArray(d)) return false;
    const keys = Object.keys(d);
    if (keys.length !== FIELDS.length || !keys.every(key => FIELDS.includes(key))) return false;
    return typeof d.id === 'string' && /^[\w-]{1,64}$/.test(d.id) && Object.hasOwn(POINTS, d.type)
      && Array.isArray(d.points) && d.points.length === POINTS[d.type] && d.points.every(validPoint)
      && COLOR.test(d.color) && Number.isInteger(d.width) && d.width >= 1 && d.width <= 4
      && typeof d.locked === 'boolean' && typeof d.level === 'boolean' && (d.level === false || LEVEL_TYPES.includes(d.type))
      && (d.levelId === null || (typeof d.levelId === 'string' && d.levelId.length <= 64 && d.level))
      && Number.isSafeInteger(d.createdAt) && d.createdAt >= 0;
  }
  function makeDrawing(type, points, options = {}) {
    const d = { id: options.id || `dr-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e9).toString(36)}`, type,
      points: points.map(p => ({ time: p.time, price: p.price })), color: options.color || '#4f8cff', width: options.width || 1,
      locked: Boolean(options.locked), level: Boolean(options.level) && LEVEL_TYPES.includes(type), levelId: options.levelId ?? null,
      createdAt: options.createdAt ?? Date.now() };
    if (!validDrawing(d)) throw new Error('drawings: invalid drawing');
    return d;
  }

  // ── persistence: { version, symbols: { "venue:SYMBOL": { hidden, items } } } ──
  function parseState(raw) {
    const empty = { version: VERSION, symbols: {} };
    let data;
    try { data = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch (_) { return empty; }
    if (!data || data.version !== VERSION || !data.symbols || typeof data.symbols !== 'object') return empty;
    const out = { version: VERSION, symbols: {} };
    for (const [key, entry] of Object.entries(data.symbols)) {
      if (!/^(binance|bybit|hyperliquid):[^\s]{1,80}$/.test(key) || !entry || !Array.isArray(entry.items)) continue;
      const seen = new Set();
      const items = entry.items.filter(d => validDrawing(d) && !seen.has(d.id) && seen.add(d.id)).slice(-PER_SYMBOL);
      out.symbols[key] = { hidden: entry.hidden === true, items };
    }
    return out;
  }
  const serialize = data => JSON.stringify(parseState(data));
  function createStore(storage) {
    let memory = { version: VERSION, symbols: {} };
    const read = () => { if (!storage) return parseState(memory); try { return parseState(storage.getItem(KEY) || 'null'); } catch (_) { return parseState(memory); } };
    const write = data => { memory = parseState(data); if (storage) { try { storage.setItem(KEY, JSON.stringify(memory)); } catch (_) { /* session-only */ } } };
    return {
      get(exchange, symbol) { const e = read().symbols[symbolKey(exchange, symbol)]; return e ? { hidden: e.hidden, items: e.items.map(d => ({ ...d, points: d.points.map(p => ({ ...p })) })) } : { hidden: false, items: [] }; },
      set(exchange, symbol, entry) {
        const data = read(); const key = symbolKey(exchange, symbol);
        if (!entry.items.length && !entry.hidden) delete data.symbols[key];
        else data.symbols[key] = { hidden: Boolean(entry.hidden), items: entry.items.filter(validDrawing).slice(-PER_SYMBOL) };
        write(data);
      }
    };
  }

  // ── pixel mapping (integer logical calls only; see the overlay notes in workspace.js) ──
  // candles: [{ time: seconds, open, high, low, close }], stepMs: interval duration.
  function createMapper({ chart, series, candles, stepMs }) {
    const scale = chart.timeScale();
    const n = candles.length;
    if (!n) return null;
    const spacing = scale.options().barSpacing;
    const anchor = n - 1;
    const anchorX = scale.logicalToCoordinate(anchor);
    if (anchorX === null || !Number.isFinite(anchorX) || !(spacing > 0)) return null;
    const ms = i => candles[i].time * 1000;
    function logicalForTime(t) {
      let low = 0, high = n;
      while (low < high) { const mid = (low + high) >>> 1; if (ms(mid) < t) low = mid + 1; else high = mid; }
      if (low === 0) return (t - ms(0)) / stepMs;
      if (low === n) return anchor + (t - ms(anchor)) / stepMs;
      return low - 1 + (t - ms(low - 1)) / (ms(low) - ms(low - 1));
    }
    function timeForLogical(l) {
      if (l <= 0) return Math.round(ms(0) + l * stepMs);
      if (l >= anchor) return Math.round(ms(anchor) + (l - anchor) * stepMs);
      const i = Math.floor(l);
      return Math.round(ms(i) + (l - i) * (ms(i + 1) - ms(i)));
    }
    const logicalAt = px => anchor + (px - anchorX) / spacing;
    return {
      spacing, logicalForTime, timeForLogical, logicalAt,
      x: t => anchorX + (logicalForTime(t) - anchor) * spacing,
      y: price => { const v = series.priceToCoordinate(Number(price)); return v === null || !Number.isFinite(v) ? null : v; },
      price: py => { const v = series.coordinateToPrice(py); return v === null || !Number.isFinite(v) ? null : v; },
      // Slot time under px: bar open time inside the data, interval-stepped slots outside it.
      slotTime: px => timeForLogical(Math.round(logicalAt(px))),
      barAt: px => { const i = Math.round(logicalAt(px)); return i >= 0 && i < n ? candles[i] : null; }
    };
  }

  // ── geometry & hit-testing (pure, pixel space) ─────────────────────────────
  // Returns { kind:'segment'|'rect', a:{x,y}, b:{x,y} } clipped to the plot, or null.
  function geometry(d, mapper, plotWidth) {
    const pts = d.points.map(p => ({ x: d.type === 'hline' ? 0 : mapper.x(p.time), y: mapper.y(p.price) }));
    if (pts.some(p => p.y === null || !Number.isFinite(p.x))) return null;
    const [a, b] = pts;
    if (d.type === 'hline') return { kind: 'segment', a: { x: 0, y: a.y }, b: { x: plotWidth, y: a.y } };
    if (d.type === 'hray') return { kind: 'segment', a, b: { x: Math.max(a.x, plotWidth), y: a.y } };
    if (d.type === 'rect') return { kind: 'rect', a, b };
    if (d.type === 'ray') {
      const dx = b.x - a.x, dy = b.y - a.y;
      if (!dx && !dy) return { kind: 'segment', a, b };
      // Extend past b to the plot edge in the ray's direction (far enough for any zoom).
      const reach = 4 * Math.max(plotWidth, 1000) / Math.max(Math.hypot(dx, dy), 1e-9);
      return { kind: 'segment', a, b: { x: a.x + dx * reach, y: a.y + dy * reach } };
    }
    return { kind: 'segment', a, b };
  }
  function distanceToSegment(p, a, b) {
    const dx = b.x - a.x, dy = b.y - a.y, len = dx * dx + dy * dy;
    const t = len ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len)) : 0;
    return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
  }
  // Topmost hit first; handles beat bodies. Returns { id, part: 'p0'|'p1'|'body' } or null.
  function hitTest(items, mapper, plotWidth, x, y, tolerance = 6) {
    const p = { x, y };
    for (let i = items.length - 1; i >= 0; i -= 1) {
      const d = items[i];
      const g = geometry(d, mapper, plotWidth);
      if (!g) continue;
      if (d.type !== 'hline') {
        const handles = d.points.map(pt => ({ x: mapper.x(pt.time), y: mapper.y(pt.price) }));
        const index = handles.findIndex(h => Math.hypot(h.x - x, h.y - y) <= tolerance + 1);
        if (index >= 0) return { id: d.id, part: `p${index}` };
      }
      if (g.kind === 'rect') {
        const [l, r] = [Math.min(g.a.x, g.b.x), Math.max(g.a.x, g.b.x)], [t, bt] = [Math.min(g.a.y, g.b.y), Math.max(g.a.y, g.b.y)];
        if (x >= l - tolerance && x <= r + tolerance && y >= t - tolerance && y <= bt + tolerance) return { id: d.id, part: 'body' };
      } else if (distanceToSegment(p, g.a, g.b) <= tolerance) return { id: d.id, part: 'body' };
    }
    return null;
  }

  // Magnet: the bar under the cursor; its O/H/L/C closest to the cursor in pixels.
  function snap(mapper, x, y, { magnet = false, precision = 8 } = {}) {
    const bar = mapper.barAt(x);
    if (magnet && bar) {
      let best = null;
      for (const key of ['open', 'high', 'low', 'close']) {
        const py = mapper.y(bar[key]);
        if (py === null) continue;
        const distance = Math.abs(py - y);
        if (!best || distance < best.distance) best = { distance, key, value: bar[key] };
      }
      if (best) return { time: bar.time * 1000, price: exactValue(best.value), snapped: best.key };
    }
    const raw = mapper.price(y);
    const price = raw === null ? null : priceString(raw, precision);
    return price === null ? null : { time: mapper.slotTime(x), price, snapped: null };
  }

  // Move every point by the pointer delta (bars along time, price along y); exact strings out.
  function movePoints(d, mapper, from, to, precision = 8) {
    const dl = Math.round(mapper.logicalAt(to.x) - mapper.logicalAt(from.x));
    const p0 = mapper.price(from.y), p1 = mapper.price(to.y);
    if (p0 === null || p1 === null) return null;
    const points = d.points.map(pt => {
      const price = priceString(Number(pt.price) + (p1 - p0), precision);
      return { time: d.type === 'hline' ? pt.time : Math.max(0, mapper.timeForLogical(mapper.logicalForTime(pt.time) + dl)), price };
    });
    return points.every(pt => pt.price !== null) ? points : null;
  }

  // Ruler: Δprice, Δ%, bars and wall time between two points (exact-ish via Number of decimals).
  function measure(a, b, mapper) {
    const pa = Number(a.price), pb = Number(b.price);
    const bars = Math.round(mapper.logicalForTime(b.time) - mapper.logicalForTime(a.time));
    return { deltaPrice: pb - pa, deltaPct: pa ? (pb - pa) / pa * 100 : null, bars, deltaMs: b.time - a.time };
  }
  function formatDuration(ms) {
    const sign = ms < 0 ? '-' : '', s = Math.round(Math.abs(ms) / 1000);
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60), sec = s % 60;
    if (d) return `${sign}${d}d ${h}h`;
    if (h) return `${sign}${h}h ${m}m`;
    if (m) return `${sign}${m}m${sec ? ` ${sec}s` : ''}`;
    return `${sign}${sec}s`;
  }

  // ── level sync (shared levels store) ──────────────────────────────────────
  function levelFromDrawing(d, exchange, symbol) {
    return { exchange, symbol, price: d.points[0].price, kind: 'simple', source: 'chart', color: d.color, width: d.width, note: d.type === 'hray' ? 'chart ray' : 'chart line' };
  }
  // Store is the source of truth for linked lines: a vanished level drops its drawing, a moved
  // or restyled level updates it. Store levels no drawing links to become virtual lines.
  function reconcile(items, levels) {
    const byId = new Map(levels.map(level => [level.id, level]));
    let changed = false;
    const out = [];
    for (const d of items) {
      if (!d.levelId) { out.push(d); continue; }
      const level = byId.get(d.levelId);
      if (!level) { changed = true; continue; }
      if (level.price !== d.points[0].price || level.color !== d.color || level.width !== d.width) {
        out.push({ ...d, points: d.points.map(p => ({ ...p, price: level.price })), color: level.color, width: level.width });
        changed = true;
      } else out.push(d);
    }
    const linked = new Set(out.map(d => d.levelId).filter(Boolean));
    const virtual = levels.filter(level => !linked.has(level.id)).map(level => ({ id: `level-${level.id}`, type: 'hline',
      points: [{ time: 0, price: level.price }], color: level.color, width: level.width, locked: false, level: true,
      levelId: level.id, createdAt: level.createdAt, dashed: level.kind === 'signal', virtual: true, kind: level.kind, armed: level.armed }));
    return { items: out, virtual, changed };
  }

  // ── pane controller (DOM, pointer, keyboard, canvas) ──────────────────────
  const PREFS_KEY = 'screener.drawings.prefs.v1';
  const TOOL_LABELS = { cursor: ['↖', 'Select / move (Esc)'], line: ['╱', 'Line (2 points)'], ray: ['↗', 'Ray (2 points)'],
    hline: ['─', 'Horizontal line'], hray: ['⟶', 'Horizontal ray'], rect: ['▭', 'Rectangle'], ruler: ['📏', 'Ruler / measure (temporary)'] };
  function readPrefs(storage) {
    try { const p = JSON.parse(storage?.getItem(PREFS_KEY) || '{}'); return { magnet: p.magnet === true, level: p.level === true, session: p.session === true }; }
    catch (_) { return { magnet: false, level: false, session: false }; }
  }
  function button(cls, text, title) {
    const el = document.createElement('button');
    el.type = 'button'; el.className = cls; el.textContent = text; el.title = title; el.setAttribute('aria-label', title);
    return el;
  }

  function createController(options) {
    const { header, stage, paneElement, getContext, requestDraw } = options;
    const storage = options.storage === undefined ? (typeof localStorage !== 'undefined' ? localStorage : null) : options.storage;
    const levels = options.levels || null;
    const store = createStore(storage);
    const prefs = readPrefs(storage);
    const savePrefs = () => { try { storage?.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (_) { /* session only */ } };
    const lifetime = new AbortController();
    const listen = (target, type, fn, opts = {}) => target.addEventListener(type, fn, { ...opts, signal: lifetime.signal });
    const self = { tool: 'cursor', items: [], virtual: [], hidden: false, selected: null, pending: null, ruler: null, drag: null, key: null, kinds: new Map(), prefs };

    // Toolbar: a header toggle opens a compact tool strip over the chart's left edge.
    const toggle = button('pane-draw-toggle', '✎', 'Drawing tools for this chart');
    toggle.setAttribute('aria-pressed', 'false');
    header.insertBefore(toggle, header.querySelector('.pane-reload'));
    const bar = document.createElement('div'); bar.className = 'draw-toolbar'; bar.hidden = true; bar.setAttribute('role', 'toolbar');
    const toolButtons = new Map(['cursor', ...TOOLS].map(tool => { const b = button('draw-tool', TOOL_LABELS[tool][0], TOOL_LABELS[tool][1]); b.dataset.tool = tool; bar.append(b); return [tool, b]; }));
    const magnet = button('draw-opt', '🧲', 'Magnet: snap to the bar’s O/H/L/C');
    const levelMode = button('draw-opt', 'L', 'Level: new horizontal lines/rays are also saved as shared price levels');
    const session = button('draw-opt', 'S', 'Session start lines (00:00 UTC)');
    const hideAll = button('draw-opt', '👁', 'Hide / show all drawings and levels');
    const clearAll = button('draw-opt draw-danger', '🗑', 'Clear all drawings of this symbol (click twice)');
    bar.append(magnet, levelMode, session, hideAll, clearAll);
    const sel = document.createElement('div'); sel.className = 'draw-selection'; sel.hidden = true;
    const color = document.createElement('input'); color.type = 'color'; color.title = 'Colour'; color.setAttribute('aria-label', 'Drawing colour');
    const width = document.createElement('select'); width.title = 'Line width'; width.setAttribute('aria-label', 'Line width');
    [1, 2, 3, 4].forEach(w => { const o = document.createElement('option'); o.value = String(w); o.textContent = `${w}px`; width.append(o); });
    const lock = button('draw-opt', '🔒', 'Lock / unlock'), dup = button('draw-opt', '⧉', 'Duplicate'), del = button('draw-opt draw-danger', '✕', 'Delete (Del)');
    sel.append(color, width, lock, dup, del);
    bar.append(sel);
    stage.append(bar);

    const all = () => (self.hidden ? [] : [...self.virtual, ...self.items]);
    const find = id => all().find(d => d.id === id) || null;
    function syncToolbar() {
      toggle.setAttribute('aria-pressed', String(!bar.hidden)); toggle.classList.toggle('is-active', !bar.hidden || self.items.length > 0);
      toolButtons.forEach((b, tool) => b.setAttribute('aria-pressed', String(self.tool === tool)));
      magnet.setAttribute('aria-pressed', String(prefs.magnet)); levelMode.setAttribute('aria-pressed', String(prefs.level));
      session.setAttribute('aria-pressed', String(prefs.session)); hideAll.setAttribute('aria-pressed', String(self.hidden));
      const d = self.selected && find(self.selected);
      sel.hidden = !d;
      if (d) { color.value = d.color; width.value = String(d.width); lock.hidden = Boolean(d.virtual); dup.hidden = Boolean(d.virtual); lock.setAttribute('aria-pressed', String(d.locked)); }
      stage.dataset.drawTool = self.tool;
    }
    function persist() {
      const { exchange, symbol } = getContext();
      store.set(exchange, symbol, { hidden: self.hidden, items: self.items });
    }
    function reload() {
      const { exchange, symbol } = getContext();
      self.key = symbolKey(exchange, symbol);
      const entry = store.get(exchange, symbol);
      const list = levels ? levels.list({ exchange, symbol }) : [];
      const rec = reconcile(entry.items, list);
      self.items = rec.items; self.virtual = rec.virtual; self.hidden = entry.hidden;
      self.kinds = new Map(list.map(level => [level.id, level.kind]));
      if (rec.changed) persist();
      if (self.selected && !find(self.selected)) self.selected = null;
      syncToolbar(); requestDraw();
    }
    const unsubscribe = levels?.subscribe ? levels.subscribe(() => reload()) : null;
    function safeLevels(fn) { try { return fn(); } catch (error) { options.onError?.(String(error?.message || error)); return null; } }

    // ── actions ──
    function addDrawing(type, points) {
      const { exchange, symbol } = getContext();
      let d = makeDrawing(type, points, { color: prefs.level && LEVEL_TYPES.includes(type) ? '#f0b429' : '#4f8cff', level: prefs.level });
      if (d.level && levels) {
        const level = safeLevels(() => levels.add(levelFromDrawing(d, exchange, symbol)));
        d = level ? { ...d, levelId: level.id } : { ...d, level: false };
      }
      self.items = [...self.items, d];
      self.selected = d.id; persist(); reload();
    }
    function updateItem(id, patch, { save = true } = {}) {
      const d = find(id);
      if (!d) return;
      if (d.virtual) {
        if (save) safeLevels(() => levels.update(d.levelId, { ...(patch.points ? { price: patch.points[0].price } : {}), ...(patch.color ? { color: patch.color } : {}), ...(patch.width ? { width: patch.width } : {}) }));
        else self.virtual = self.virtual.map(v => (v.id === id ? { ...v, ...patch } : v));
        return;
      }
      self.items = self.items.map(item => (item.id === id ? { ...item, ...patch } : item));
      if (!save) return;
      const next = self.items.find(item => item.id === id);
      if (next.levelId && levels) safeLevels(() => levels.update(next.levelId, { price: next.points[0].price, color: next.color, width: next.width }));
      persist();
    }
    function removeItem(id) {
      const d = find(id);
      if (!d) return;
      if (d.levelId && levels) safeLevels(() => levels.remove(d.levelId));
      if (!d.virtual) { self.items = self.items.filter(item => item.id !== id); persist(); }
      self.selected = null; reload();
    }
    function duplicate(id) {
      const d = self.items.find(item => item.id === id);
      if (!d) return;
      const copy = makeDrawing(d.type, d.points, { color: d.color, width: d.width });
      self.items = [...self.items, copy]; self.selected = copy.id; persist(); reload();
    }
    function setTool(tool) {
      self.tool = tool; self.pending = null;
      if (tool !== 'ruler') self.ruler = null;
      syncToolbar(); requestDraw();
    }
    let clearArmed = 0;
    listen(toggle, 'click', event => { event.stopPropagation(); bar.hidden = !bar.hidden; if (bar.hidden) setTool('cursor'); syncToolbar(); });
    toolButtons.forEach((b, tool) => listen(b, 'click', () => setTool(tool)));
    listen(magnet, 'click', () => { prefs.magnet = !prefs.magnet; savePrefs(); syncToolbar(); });
    listen(levelMode, 'click', () => { prefs.level = !prefs.level; savePrefs(); syncToolbar(); });
    listen(session, 'click', () => { prefs.session = !prefs.session; savePrefs(); syncToolbar(); requestDraw(); });
    listen(hideAll, 'click', () => { self.hidden = !self.hidden; self.selected = null; persist(); syncToolbar(); requestDraw(); });
    listen(clearAll, 'click', () => {
      if (Date.now() - clearArmed > 3000) { clearArmed = Date.now(); clearAll.textContent = '✓?'; setTimeout(() => { clearAll.textContent = '🗑'; }, 3000); return; }
      clearArmed = 0; clearAll.textContent = '🗑';
      self.items.forEach(d => { if (d.levelId && levels) safeLevels(() => levels.remove(d.levelId)); });
      self.items = []; self.selected = null; persist(); reload();
    });
    listen(color, 'input', () => self.selected && (updateItem(self.selected, { color: color.value }), requestDraw()));
    listen(width, 'change', () => self.selected && (updateItem(self.selected, { width: Number(width.value) }), requestDraw()));
    listen(lock, 'click', () => { const d = find(self.selected); if (d && !d.virtual) { updateItem(d.id, { locked: !d.locked }); syncToolbar(); } });
    listen(dup, 'click', () => self.selected && duplicate(self.selected));
    listen(del, 'click', () => self.selected && removeItem(self.selected));

    // ── pointer handling (capture phase: stop the chart from panning while drawing) ──
    function local(event) {
      const ctx = getContext();
      const rect = ctx.host.getBoundingClientRect();
      const x = event.clientX - rect.left, y = event.clientY - rect.top;
      const inside = x >= 0 && y >= 0 && x <= ctx.plotWidth && y <= ctx.plotHeight;
      return { x, y, inside, ctx, mapper: ctx.candles.length ? createMapper(ctx) : null };
    }
    const snapAt = (m, x, y) => snap(m.mapper, x, y, { magnet: prefs.magnet, precision: m.ctx.precision });
    function stop(event) { event.stopPropagation(); event.preventDefault(); }
    function finishPending(b) {
      const p = self.pending; self.pending = null;
      if (p.type === 'ruler') { self.ruler = { a: p.a, b }; requestDraw(); return; }
      if (p.a.time === b.time && p.a.price === b.price) { requestDraw(); return; }
      addDrawing(p.type, [p.a, b]); setTool('cursor');
    }
    listen(stage, 'pointerdown', event => {
      if (event.button !== 0 || event.target.closest('.draw-toolbar')) return;
      const m = local(event);
      if (!m.inside || !m.mapper) return;
      if (self.tool !== 'cursor') {
        stop(event);
        const p = snapAt(m, m.x, m.y);
        if (!p) return;
        if (self.tool === 'hline' || self.tool === 'hray') { addDrawing(self.tool, [p]); setTool('cursor'); return; }
        if (self.pending) { finishPending(p); return; }
        self.ruler = null;
        self.pending = { type: self.tool, a: p, b: p, down: { x: m.x, y: m.y } };
        stage.setPointerCapture?.(event.pointerId);
        requestDraw(); return;
      }
      if (self.ruler) { self.ruler = null; requestDraw(); }
      const hit = hitTest(all(), m.mapper, m.ctx.plotWidth, m.x, m.y);
      if (!hit) { if (self.selected) { self.selected = null; syncToolbar(); requestDraw(); } return; }
      stop(event);
      paneElement.focus({ preventScroll: true });
      self.selected = hit.id; syncToolbar();
      const d = find(hit.id);
      if (!d.locked) { self.drag = { id: d.id, part: hit.part, from: { x: m.x, y: m.y }, original: { ...d, points: d.points.map(pt => ({ ...pt })) }, moved: false }; stage.setPointerCapture?.(event.pointerId); }
      requestDraw();
    }, { capture: true });
    listen(stage, 'pointermove', event => {
      const m = local(event);
      if (!m.mapper) return;
      if (self.pending) {
        const p = snapAt(m, m.x, m.y);
        if (p) { self.pending.b = p; requestDraw(); }
        return;
      }
      if (self.drag) {
        stop(event);
        const { original, part, from } = self.drag;
        let points = null;
        if (part === 'body') points = movePoints(original, m.mapper, from, { x: m.x, y: m.y }, m.ctx.precision);
        else {
          const p = snapAt(m, m.x, m.y);
          if (p) points = original.points.map((pt, i) => (`p${i}` === part ? { time: p.time, price: p.price } : pt));
        }
        if (points) { self.drag.moved = true; updateItem(original.id, { points }, { save: false }); requestDraw(); }
        return;
      }
      if (self.tool === 'cursor' && m.inside) stage.dataset.drawHover = hitTest(all(), m.mapper, m.ctx.plotWidth, m.x, m.y) ? 'true' : 'false';
    }, { capture: true });
    listen(stage, 'pointerup', event => {
      if (self.pending) {
        const m = local(event);
        const moved = Math.hypot(m.x - self.pending.down.x, m.y - self.pending.down.y) > 4;
        if (moved && m.mapper) { const p = snapAt(m, m.x, m.y); if (p) finishPending(p); }
        return; // a click without movement waits for the second click
      }
      if (!self.drag) return;
      const drag = self.drag; self.drag = null;
      if (drag.moved) { const d = find(drag.id); if (d) updateItem(d.id, { points: d.points }); reload(); }
    }, { capture: true });
    ['mousedown', 'touchstart', 'click', 'dblclick'].forEach(type => listen(stage, type, event => {
      if (event.target.closest?.('.draw-toolbar')) return;
      if (self.tool !== 'cursor' || self.drag) event.stopPropagation();
    }, { capture: true }));
    listen(paneElement, 'keydown', event => {
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(event.target.tagName)) return;
      if ((event.key === 'Delete' || event.key === 'Backspace') && self.selected) { event.preventDefault(); removeItem(self.selected); }
      else if (event.key === 'Escape') { self.pending = null; self.ruler = null; self.drag = null; self.selected = null; setTool('cursor'); }
    });

    // ── canvas rendering (called inside the pane's clipped overlay pass) ──
    function label(context, text, x, y, bg) {
      context.font = '10px SFMono-Regular, Consolas, monospace';
      const w = context.measureText(text).width + 8;
      context.fillStyle = bg; context.fillRect(x, y - 8, w, 16);
      context.fillStyle = '#0b0f16'; context.fillText(text, x + 4, y + 3.5);
      return w;
    }
    function drawItem(context, d, mapper, plotWidth, ctx) {
      const g = geometry(d, mapper, plotWidth);
      if (!g) return;
      const dashed = d.dashed || (d.levelId && self.kinds.get(d.levelId) === 'signal');
      context.strokeStyle = d.color; context.lineWidth = d.width; context.setLineDash(dashed ? [6, 4] : []);
      context.beginPath();
      if (g.kind === 'rect') {
        const x = Math.min(g.a.x, g.b.x), y = Math.min(g.a.y, g.b.y), w = Math.abs(g.b.x - g.a.x), h = Math.abs(g.b.y - g.a.y);
        context.fillStyle = `${d.color}22`; context.fillRect(x, y, w, h); context.rect(x, y, w, h);
      } else { context.moveTo(g.a.x, g.a.y); context.lineTo(g.b.x, g.b.y); }
      context.stroke(); context.setLineDash([]);
      if (d.level) {
        const text = `${d.virtual && d.kind === 'signal' ? '⚑ ' : ''}${d.points[0].price}`;
        context.font = '10px SFMono-Regular, Consolas, monospace';
        label(context, text, plotWidth - context.measureText(text).width - 10, g.a.y, d.color);
      }
      if (self.selected === d.id) {
        const handles = d.type === 'hline' ? [{ x: Math.min(plotWidth - 20, Math.max(20, ctx.plotWidth / 2)), y: g.a.y }] : d.points.map(p => ({ x: mapper.x(p.time), y: mapper.y(p.price) }));
        context.fillStyle = '#0b0f16'; context.strokeStyle = d.color; context.lineWidth = 1.5;
        handles.forEach(h => { context.fillRect(h.x - 4, h.y - 4, 8, 8); context.strokeRect(h.x - 4, h.y - 4, 8, 8); });
      }
    }
    function drawRuler(context, a, b, mapper) {
      const ax = mapper.x(a.time), ay = mapper.y(a.price), bx = mapper.x(b.time), by = mapper.y(b.price);
      if ([ax, ay, bx, by].some(v => v === null || !Number.isFinite(v))) return;
      const m = measure(a, b, mapper), up = m.deltaPrice >= 0, tone = up ? '#26a69a' : '#ef5350';
      context.fillStyle = `${tone}2e`; context.fillRect(Math.min(ax, bx), Math.min(ay, by), Math.abs(bx - ax), Math.abs(by - ay));
      context.strokeStyle = tone; context.lineWidth = 1; context.setLineDash([4, 3]);
      context.beginPath(); context.moveTo(ax, ay); context.lineTo(bx, by); context.stroke(); context.setLineDash([]);
      const precision = Math.max(0, Math.min(10, (b.price.split('.')[1] || '').length, (a.price.split('.')[1] || '').length) || 2);
      const text = `${up ? '+' : ''}${m.deltaPrice.toFixed(precision)} (${m.deltaPct === null ? '—' : `${up ? '+' : ''}${m.deltaPct.toFixed(2)}%`}) · ${m.bars} bars · ${formatDuration(m.deltaMs)}`;
      label(context, text, Math.min(ax, bx), Math.max(ay, by) + 14, tone);
      self.lastMeasure = { ...m, text };
    }
    function drawSessions(context, mapper, plotWidth, plotHeight) {
      const from = mapper.timeForLogical(mapper.logicalAt(0)), to = mapper.timeForLogical(mapper.logicalAt(plotWidth));
      const day = 86400000;
      context.strokeStyle = 'rgba(160,174,200,0.35)'; context.lineWidth = 1; context.setLineDash([2, 4]);
      for (let t = Math.ceil(from / day) * day, n = 0; t <= to && n < 400; t += day, n += 1) {
        const x = Math.round(mapper.x(t)) + 0.5;
        context.beginPath(); context.moveTo(x, 0); context.lineTo(x, plotHeight); context.stroke();
      }
      context.setLineDash([]);
    }
    function draw(context, plotWidth, plotHeight) {
      const ctx = getContext();
      const key = symbolKey(ctx.exchange, ctx.symbol);
      if (key !== self.key) reload();
      if (!ctx.candles.length) return;
      const mapper = createMapper(ctx);
      if (!mapper) return;
      context.save();
      if (prefs.session) drawSessions(context, mapper, plotWidth, plotHeight);
      all().forEach(d => drawItem(context, d, mapper, plotWidth, ctx));
      if (self.pending) {
        const p = self.pending;
        if (p.type === 'ruler') drawRuler(context, p.a, p.b, mapper);
        else drawItem(context, { id: '_pending', type: p.type, points: [p.a, p.b], color: '#4f8cff', width: 1, level: false }, mapper, plotWidth, ctx);
      }
      if (self.ruler) drawRuler(context, self.ruler.a, self.ruler.b, mapper);
      context.restore();
    }
    function dispose() { lifetime.abort(); unsubscribe?.(); toggle.remove(); bar.remove(); }
    reload();
    return { draw, reload, dispose, setTool, get state() { return self; }, actions: { addDrawing, updateItem, removeItem, duplicate } };
  }

  return { KEY, VERSION, PER_SYMBOL, TOOLS, POINTS, FIELDS, symbolKey, priceString, exactValue, validDrawing, makeDrawing,
    parseState, serialize, createStore, createMapper, geometry, distanceToSegment, hitTest, snap, movePoints, measure,
    formatDuration, levelFromDrawing, reconcile, createController };
});
