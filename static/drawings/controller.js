/* Drawing tools — pane controller: state, actions with undo/redo, pointer/keyboard input and
 * canvas rendering inside the pane's overlay pass. DOM chrome lives in ui.js / ui-panels.js.
 * Public shape kept compatible with the old static/drawings.js controller.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./core.js'), require('./toolbar-model.js'));
  else root.WorkspaceDrawingsController = factory(root.WorkspaceDrawingsCore, root.WorkspaceDrawingsToolbar);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (C, TB) {
  'use strict';
  const HANDLE = 4;
  function createController(options) {
    const { stage, paneElement, getContext, requestDraw } = options;
    const storage = options.storage === undefined ? (typeof localStorage !== 'undefined' ? localStorage : null) : options.storage;
    const levels = options.levels || null;
    const store = C.createStore(storage), templates = C.createTemplates(storage), history = C.createHistory(150);
    const lifetime = new AbortController();
    const listen = (target, type, fn, opts = {}) => target?.addEventListener?.(type, fn, { ...opts, signal: lifetime.signal });
    const self = { tool: 'cursor', items: [], virtual: [], hidden: false, lockedAll: false, selected: null, pending: null, ruler: null, drag: null,
      key: null, interval: null, prefs: TB.readPrefs(storage), frame: null, lastMeasure: null, perf: { frames: 0, total: 0, max: 0, last: 0 } };
    const listeners = new Set();
    const notify = () => listeners.forEach(fn => { try { fn(self); } catch (_) { /* ui errors must not break drawing */ } });
    const savePrefs = () => { TB.savePrefs(storage, self.prefs); notify(); };
    const all = () => (self.hidden ? [] : [...self.virtual, ...self.items]);
    const find = id => [...self.virtual, ...self.items].find(d => d.id === id) || null;
    // Our own level writes must not trigger a reload mid-action (the store would overwrite self.items).
    let muteLevels = false;
    const safeLevels = fn => { muteLevels = true; try { return fn(); } catch (error) { options.onError?.(String(error?.message || error)); return null; } finally { muteLevels = false; } };

    // ── persistence / sync ──
    function persist() { const { exchange, symbol } = getContext(); store.set(exchange, symbol, { hidden: self.hidden, locked: self.lockedAll, items: self.items }); }
    function reload() {
      const { exchange, symbol } = getContext(), key = C.symbolKey(exchange, symbol);
      if (key !== self.key) { history.clear(); self.selected = null; self.pending = null; self.ruler = null; }
      self.key = key;
      const entry = store.get(exchange, symbol), list = levels ? levels.list({ exchange, symbol }) : [];
      const rec = C.reconcile(entry.items, list);
      self.items = rec.items; self.virtual = rec.virtual; self.hidden = entry.hidden; self.lockedAll = entry.locked;
      if (rec.changed) persist();
      if (self.selected && !find(self.selected)) self.selected = null;
      notify(); requestDraw();
    }
    const unsubscribe = levels?.subscribe ? levels.subscribe(() => { if (!muteLevels) reload(); }) : null;
    function linkLevel(d) {
      if (!d.level || !levels) return d;
      const { exchange, symbol } = getContext(), level = safeLevels(() => levels.add(C.levelFromDrawing(d, exchange, symbol)));
      return level ? { ...d, levelId: level.id } : { ...d, level: false, levelId: null };
    }
    // After undo/redo: re-create levels for restored linked lines, drop levels whose lines vanished.
    function syncLevels(before, after) {
      if (!levels) return after;
      const { exchange, symbol } = getContext(), live = new Set(levels.list({ exchange, symbol }).map(l => l.id));
      const kept = new Set(after.map(d => d.levelId).filter(Boolean));
      before.forEach(d => { if (d.levelId && !kept.has(d.levelId) && live.has(d.levelId)) safeLevels(() => levels.remove(d.levelId)); });
      return after.map(d => {
        if (!d.levelId) return d;
        if (!live.has(d.levelId)) return linkLevel({ ...d, levelId: null });
        safeLevels(() => levels.update(d.levelId, { price: d.points[0].price, color: d.style.color, width: Math.min(4, d.style.width) }));
        return d;
      });
    }

    // ── actions (each mutating action records one undo step) ──
    function commit(next, { record = true } = {}) {
      if (record) history.record(self.items);
      self.items = next; persist(); reload();
    }
    function addDrawing(type, points, extra = {}) {
      const def = C.getTool(type), tpl = templates.defaultFor(type);
      const level = Boolean(self.prefs.level && C.LEVEL_TYPES.includes(type));
      let d = C.makeDrawing(type, points, { style: { ...(tpl?.style || {}), ...(level ? { color: '#f0b429' } : {}), ...(extra.style || {}) },
        opts: { ...(tpl?.opts || {}), ...(extra.opts || {}) }, text: extra.text ?? tpl?.text ?? def.defaults.text, level });
      d = linkLevel(d);
      commit([...self.items, d]);
      self.selected = d.id; notify(); requestDraw();
      return d;
    }
    function updateItem(id, patch, { save = true, record = true } = {}) {
      const d = find(id);
      if (!d) return;
      if (d.virtual) {
        if (!save) { self.virtual = self.virtual.map(v => (v.id === id ? { ...v, ...patch } : v)); return; }
        const style = patch.style || {};
        safeLevels(() => levels.update(d.levelId, { ...(patch.points ? { price: patch.points[0].price } : {}), ...(style.color ? { color: style.color } : {}), ...(style.width ? { width: Math.min(4, style.width) } : {}) }));
        reload(); return;
      }
      const next = self.items.map(item => (item.id === id ? C.normalizeDrawing({ ...item, ...patch, style: { ...item.style, ...(patch.style || {}) } }) || item : item));
      if (!save) { self.items = next; return; }
      const after = next.find(item => item.id === id);
      if (after.levelId && levels) safeLevels(() => levels.update(after.levelId, { price: after.points[0].price, color: after.style.color, width: Math.min(4, after.style.width) }));
      commit(next, { record });
    }
    function removeItem(id) {
      const d = find(id);
      if (!d) return;
      if (d.levelId && levels) safeLevels(() => levels.remove(d.levelId));
      if (self.selected === id) self.selected = null;
      if (!d.virtual) commit(self.items.filter(item => item.id !== id)); else reload();
    }
    function cloneItem(id) {
      const d = self.items.find(item => item.id === id);
      if (!d) return null;
      const copy = linkLevel({ ...C.clone(d), id: C.newId(), levelId: null, locked: false, createdAt: Date.now(), name: d.name ? `${d.name} copy` : '' });
      commit([...self.items, copy]); self.selected = copy.id; notify();
      return copy;
    }
    function undo() { const prev = history.undo(self.items); if (!prev) return false; const restored = syncLevels(self.items, prev); self.items = restored; persist(); reload(); return true; }
    function redo() { const next = history.redo(self.items); if (!next) return false; const restored = syncLevels(self.items, next); self.items = restored; persist(); reload(); return true; }
    function clearAll() {
      self.items.forEach(d => { if (d.levelId && levels) safeLevels(() => levels.remove(d.levelId)); });
      self.selected = null; commit([]);
    }
    function setHiddenAll(value) { self.hidden = value; self.selected = null; persist(); notify(); requestDraw(); }
    function setLockedAll(value) { self.lockedAll = value; persist(); notify(); }
    function reorder(id, where) { commit(C.reorder(self.items, id, where)); }
    function setTool(type) {
      if (type !== 'cursor' && !C.getTool(type)) return;
      self.tool = type; self.pending = null;
      if (type !== 'cursor') { self.prefs = TB.chooseTool(self.prefs, type); savePrefs(); }
      stage.dataset.drawTool = type; notify(); requestDraw();
    }
    function select(id) { self.selected = id && find(id) ? id : null; notify(); requestDraw(); }
    function applyTemplate(id, name) { const d = self.items.find(item => item.id === id); if (d) updateItem(id, templates.apply(d, name)); }

    // ── geometry helpers ──
    function view(ctx = getContext()) {
      if (!ctx.candles?.length) return null;
      const mapper = C.createMapper(ctx);
      return mapper && { mapper, candles: ctx.candles, w: ctx.plotWidth, h: ctx.plotHeight, precision: ctx.precision, stepMs: ctx.stepMs,
        footprint: ctx.footprint || null, footprintVersion: ctx.footprintVersion || 0 };
    }
    const visible = () => all().filter(d => C.visibleOn(d, self.interval ?? getContext().interval));
    const primsFor = (d, v) => self.frame?.prims.get(d) || C.build(d, v);
    const locked = d => d.locked || self.lockedAll;

    // ── creation ──
    function previewDrawing() {
      const p = self.pending;
      if (!p) return null;
      const points = p.free ? p.points : [...p.points, p.cursor].filter(Boolean);
      return { id: '_pending', type: p.type, points, style: C.normalizeStyle(templates.defaultFor(p.type)?.style || {}, C.getTool(p.type).defaults.style),
        text: C.getTool(p.type).defaults.text, opts: { ...C.getTool(p.type).defaults.opts, ...(templates.defaultFor(p.type)?.opts || {}) }, visibility: C.normalizeVisibility(null) };
    }
    function finishPending(v) {
      const p = self.pending, def = C.getTool(p.type);
      self.pending = null;
      let points = p.points;
      if (p.free) {
        const simplified = C.simplify(p.pix, 1.5).slice(0, C.MAX_POINTS);
        points = simplified.map(q => ({ time: v.mapper.slotTime(q.x), price: C.priceString(v.mapper.price(q.y), v.precision) })).filter(q => q.price);
      }
      const [min] = C.pointRange(def);
      const distinct = new Set(points.map(q => `${q.time}|${q.price}`)).size;
      if (points.length < (def.mode === 'click' ? def.points : min) || distinct < Math.min(2, points.length)) { requestDraw(); return null; }
      if (def.finalize) points = def.finalize(points, v);
      if (def.temporary) { self.ruler = { type: p.type, points }; self.tool = 'cursor'; stage.dataset.drawTool = 'cursor'; notify(); requestDraw(); return null; }
      const d = addDrawing(p.type, points);
      if (!self.prefs.stayInDrawing) setTool('cursor');
      if (def.editText) options.onEditText?.(d.id);
      return d;
    }
    function snapAt(v, x, y, event) {
      const magnet = event && (event.ctrlKey || event.metaKey) ? (self.prefs.magnet === 'off' ? 'strong' : 'off') : self.prefs.magnet;
      const p = C.snap(v.mapper, x, y, { magnet, precision: v.precision });
      return p && { time: p.time, price: p.price };
    }

    // ── pointer handling (capture phase so the chart does not pan while drawing) ──
    function local(event) {
      const ctx = getContext(), rect = ctx.host.getBoundingClientRect();
      const x = event.clientX - rect.left, y = event.clientY - rect.top;
      return { x, y, inside: x >= 0 && y >= 0 && x <= ctx.plotWidth && y <= ctx.plotHeight, v: view(ctx) };
    }
    const stop = event => { event.stopPropagation(); event.preventDefault(); };
    const onChrome = event => Boolean(event.target?.closest?.('.dw-ui'));
    listen(stage, 'pointerdown', event => {
      if (event.button !== 0 || onChrome(event)) return;
      const m = local(event);
      if (!m.inside || !m.v) return;
      paneElement?.focus?.({ preventScroll: true });
      if (self.tool !== 'cursor') {
        stop(event);
        const def = C.getTool(self.tool), p = snapAt(m.v, m.x, m.y, event);
        if (!p) return;
        self.ruler = null;
        if (def.mode === 'free') { self.pending = { type: self.tool, free: true, points: [p], pix: [{ x: m.x, y: m.y }] }; stage.setPointerCapture?.(event.pointerId); requestDraw(); return; }
        if (def.mode === 'click' && def.points === 1) { self.pending = { type: self.tool, points: [p] }; finishPending(m.v); return; }
        if (self.pending) {
          const last = self.pending.points.at(-1);
          if (def.mode === 'multi' && last && last.time === p.time && last.price === p.price) { finishPending(m.v); return; } // double click ends a path
          self.pending.points.push(p); self.pending.cursor = p;
          if (def.mode === 'click' && self.pending.points.length >= def.points) finishPending(m.v);
          else requestDraw();
          return;
        }
        self.pending = { type: self.tool, points: [p], cursor: p, down: { x: m.x, y: m.y } };
        stage.setPointerCapture?.(event.pointerId); requestDraw(); return;
      }
      if (self.ruler) { self.ruler = null; requestDraw(); }
      const hit = C.hitTest(visible(), m.v, m.x, m.y, 6, d => primsFor(d, m.v));
      if (!hit) { if (self.selected) select(null); return; }
      stop(event);
      select(hit.id);
      const d = find(hit.id);
      if (!locked(d)) { self.drag = { id: d.id, part: hit.part, from: { x: m.x, y: m.y }, original: C.clone(d), moved: false }; stage.setPointerCapture?.(event.pointerId); }
    }, { capture: true });
    listen(stage, 'pointermove', event => {
      if (onChrome(event) && !self.drag && !self.pending) return;
      const m = local(event);
      if (!m.v) return;
      if (self.pending) {
        if (self.pending.free) {
          if (event.buttons & 1) { const last = self.pending.pix.at(-1); if (Math.hypot(last.x - m.x, last.y - m.y) >= 2) { self.pending.pix.push({ x: m.x, y: m.y }); self.pending.points.push(snapAt(m.v, m.x, m.y, null) || self.pending.points.at(-1)); } }
        } else { const p = snapAt(m.v, m.x, m.y, event); if (p) self.pending.cursor = p; }
        requestDraw(); return;
      }
      if (self.drag) {
        stop(event);
        const { original, part, from } = self.drag;
        let points = null;
        if (part === 'body') points = C.movePoints(original.points, m.v.mapper, from, { x: m.x, y: m.y }, m.v.precision, original.type === 'hline');
        else { const p = snapAt(m.v, m.x, m.y, event); if (p) points = C.dragHandle(original, part, p, m.v); }
        if (points) { self.drag.moved = true; updateItem(original.id, { points }, { save: false }); requestDraw(); }
        return;
      }
      if (self.tool === 'cursor' && m.inside) stage.dataset.drawHover = C.hitTest(visible(), m.v, m.x, m.y, 6, d => primsFor(d, m.v)) ? 'true' : 'false';
    }, { capture: true });
    listen(stage, 'pointerup', event => {
      const p = self.pending;
      if (p) {
        const m = local(event);
        if (!m.v) return;
        if (p.free) { finishPending(m.v); return; }
        const def = C.getTool(p.type);
        // Press-drag-release on the first point places the second point (2-point tools finish).
        if (p.down && p.points.length === 1 && Math.hypot(m.x - p.down.x, m.y - p.down.y) > 4) {
          const q = snapAt(m.v, m.x, m.y, event);
          if (q) { p.points.push(q); p.cursor = q; if (def.mode === 'click' && p.points.length >= def.points) finishPending(m.v); else requestDraw(); }
        }
        if (self.pending) self.pending.down = null;
        return;
      }
      if (!self.drag) return;
      const drag = self.drag; self.drag = null;
      if (drag.moved) {
        const d = find(drag.id);
        if (d?.virtual) updateItem(d.id, { points: d.points });
        else if (d) { self.items = self.items.map(item => (item.id === d.id ? drag.original : item)); updateItem(d.id, { points: d.points }); }
      }
    }, { capture: true });
    ['mousedown', 'touchstart', 'click', 'dblclick'].forEach(type => listen(stage, type, event => {
      if (onChrome(event)) return;
      if (self.tool !== 'cursor' || self.drag || self.pending) event.stopPropagation();
      if (type === 'dblclick' && self.tool === 'cursor' && self.selected && !onChrome(event)) { const d = find(self.selected); if (d && !d.virtual) options.onOpenSettings?.(d.id); }
    }, { capture: true }));

    // ── keyboard: document-level, only for the active pane ──
    function handleKey(event) {
      const action = TB.shortcut(event);
      if (!action) return false;
      if (action.action === 'tool') setTool(action.tool);
      else if (action.action === 'cancel') {
        const p = self.pending;
        if (p && C.getTool(p.type).mode === 'multi' && p.points.length >= 2) { const v = view(); if (v) finishPending(v); return true; }
        self.pending = null; self.ruler = null; self.drag = null; self.selected = null; setTool('cursor'); options.onEscape?.();
      } else if (action.action === 'delete') { if (!self.selected) return false; removeItem(self.selected); }
      else if (action.action === 'undo') undo();
      else if (action.action === 'redo') redo();
      else if (action.action === 'clone') { if (!self.selected) return false; cloneItem(self.selected); }
      return true;
    }
    listen(typeof document !== 'undefined' ? document : null, 'keydown', event => {
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(event.target?.tagName) || event.target?.isContentEditable) return;
      if (paneElement && !paneElement.classList.contains('is-active')) return;
      if (event.key === 'Enter' && self.pending && C.getTool(self.pending.type).mode === 'multi') { const v = view(); if (v) finishPending(v); event.preventDefault(); return; }
      if (handleKey(event)) { event.preventDefault(); event.stopPropagation(); }
    });

    // ── rendering ──
    function drawHandles(c, d, v) {
      c.setLineDash([]); c.lineWidth = 1.5; c.strokeStyle = d.style.color; c.fillStyle = '#0b0f16';
      C.handles(d, v).forEach(h => { c.beginPath(); c.arc(h.x, h.y, HANDLE, 0, Math.PI * 2); c.fill(); c.stroke(); });
    }
    function drawSessions(c, v) {
      const m = v.mapper, from = m.timeForLogical(m.logicalAt(0)), to = m.timeForLogical(m.logicalAt(v.w)), day = 86400000;
      c.strokeStyle = 'rgba(160,174,200,0.35)'; c.lineWidth = 1; c.setLineDash([2, 4]);
      for (let t = Math.ceil(from / day) * day, n = 0; t <= to && n < 400; t += day, n += 1) { const x = Math.round(m.x(t)) + 0.5; c.beginPath(); c.moveTo(x, 0); c.lineTo(x, v.h); c.stroke(); }
      c.setLineDash([]);
    }
    function draw(context, plotWidth, plotHeight) {
      const t0 = typeof performance !== 'undefined' ? performance.now() : Date.now();
      const ctx = getContext();
      if (C.symbolKey(ctx.exchange, ctx.symbol) !== self.key) reload();
      if (ctx.interval !== self.interval) { self.interval = ctx.interval; notify(); }
      const v = view(ctx);
      if (!v) return;
      v.w = plotWidth; v.h = plotHeight;
      const prims = new Map();
      context.save();
      if (self.prefs.session) drawSessions(context, v);
      for (const d of visible()) {
        const list = C.build(d, v);
        prims.set(d, list);
        C.render(context, list, d.style);
      }
      self.frame = { view: v, prims };
      const sel = self.selected && find(self.selected);
      if (sel && prims.has(sel)) drawHandles(context, sel, v);
      const preview = previewDrawing();
      if (preview && preview.points.length) { C.render(context, C.build(preview, v), preview.style); if (!self.pending.free) drawHandles(context, { ...preview, style: { ...preview.style } }, v); }
      if (self.ruler) {
        const r = { id: '_ruler', type: self.ruler.type, points: self.ruler.points, style: C.normalizeStyle({}), text: '', opts: {} };
        C.render(context, C.build(r, v), r.style);
        self.lastMeasure = C.measure(r.points[0], r.points[1], v.mapper);
      }
      context.restore();
      const dt = (typeof performance !== 'undefined' ? performance.now() : Date.now()) - t0;
      self.perf.frames += 1; self.perf.total += dt; self.perf.max = Math.max(self.perf.max, dt); self.perf.last = dt;
      options.afterDraw?.(self, v);
    }
    function dispose() { lifetime.abort(); unsubscribe?.(); listeners.clear(); }
    reload();
    return {
      draw, reload, dispose, setTool, select, view, handleKey, subscribe: fn => { listeners.add(fn); return () => listeners.delete(fn); },
      get state() { return self; }, history, templates, store,
      setPrefs(patch) { self.prefs = { ...self.prefs, ...patch }; savePrefs(); requestDraw(); },
      actions: { addDrawing, updateItem, removeItem, cloneItem, duplicate: cloneItem, undo, redo, clearAll, setHiddenAll, setLockedAll, reorder, applyTemplate, find }
    };
  }
  return { createController };
});
