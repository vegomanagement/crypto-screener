/* Drawing tools — toolbar model and keyboard shortcuts (pure; the DOM layer renders it).
 * Groups → flyouts; remembers the last tool per group and a favourites strip in prefs.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./core.js'));
  else root.WorkspaceDrawingsToolbar = factory(root.WorkspaceDrawingsCore);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (C) {
  'use strict';
  const GROUPS = [
    { id: 'lines', label: 'Lines' }, { id: 'channels', label: 'Channels' }, { id: 'fib', label: 'Fibonacci / Gann' },
    { id: 'measure', label: 'Measure / projection' }, { id: 'shapes', label: 'Shapes / brushes' },
    { id: 'annotation', label: 'Annotation / text' }, { id: 'patterns', label: 'Patterns' }];
  const PREFS_KEY = 'screener.drawings.prefs.v2';
  const PREFS_V1 = 'screener.drawings.prefs.v1';
  const MAGNETS = ['off', 'weak', 'strong'];
  const DEFAULT_PREFS = { magnet: 'off', level: false, session: false, stayInDrawing: false, lastByGroup: {}, favourites: ['trend', 'hline', 'fib', 'rect', 'long', 'short'], favouritesVisible: true };

  function readPrefs(storage) {
    let raw = null;
    try { raw = JSON.parse(storage?.getItem(PREFS_KEY) || 'null'); } catch (_) { raw = null; }
    if (!raw) { // v1 prefs: { magnet: bool, level, session }
      try { const v1 = JSON.parse(storage?.getItem(PREFS_V1) || 'null'); if (v1) raw = { magnet: v1.magnet === true ? 'strong' : 'off', level: v1.level === true, session: v1.session === true }; } catch (_) { raw = null; }
    }
    const p = { ...DEFAULT_PREFS, lastByGroup: {}, favourites: DEFAULT_PREFS.favourites.slice() };
    if (raw && typeof raw === 'object') {
      if (MAGNETS.includes(raw.magnet)) p.magnet = raw.magnet;
      ['level', 'session', 'stayInDrawing', 'favouritesVisible'].forEach(k => { if (typeof raw[k] === 'boolean') p[k] = raw[k]; });
      if (raw.lastByGroup && typeof raw.lastByGroup === 'object') for (const [g, t] of Object.entries(raw.lastByGroup)) if (C.getTool(t)?.group === g) p.lastByGroup[g] = t;
      if (Array.isArray(raw.favourites)) p.favourites = [...new Set(raw.favourites.filter(t => C.getTool(t)))].slice(0, 20);
    }
    return p;
  }
  const savePrefs = (storage, prefs) => { try { storage?.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (_) { /* session only */ } };

  // Toolbar model: one entry per group with its tools and the "current" (last used) tool.
  function model(prefs) {
    return GROUPS.map(g => {
      const tools = C.toolList().filter(t => t.group === g.id && !t.hidden).map(t => ({ type: t.type, label: t.label, icon: t.icon, shortcut: shortcutFor(t.type), favourite: prefs.favourites.includes(t.type) }));
      const current = tools.find(t => t.type === prefs.lastByGroup[g.id]) || tools[0] || null;
      return { ...g, tools, current: current?.type || null };
    }).filter(g => g.tools.length);
  }
  function chooseTool(prefs, type) {
    const t = C.getTool(type);
    if (!t) return prefs;
    return { ...prefs, lastByGroup: { ...prefs.lastByGroup, [t.group]: type } };
  }
  function toggleFavourite(prefs, type) {
    if (!C.getTool(type)) return prefs;
    const has = prefs.favourites.includes(type);
    return { ...prefs, favourites: has ? prefs.favourites.filter(t => t !== type) : [...prefs.favourites, type].slice(0, 20) };
  }
  const nextMagnet = mode => MAGNETS[(MAGNETS.indexOf(mode) + 1) % MAGNETS.length];

  // ── keyboard ──
  const TOOL_KEYS = { t: 'trend', h: 'hline', v: 'vline', c: 'cross', f: 'fib', r: 'rect' };
  const shortcutFor = type => { const k = Object.keys(TOOL_KEYS).find(key => TOOL_KEYS[key] === type); return k ? `Alt+${k.toUpperCase()}` : ''; };
  // Maps a keydown-like { key, code, altKey, ctrlKey, metaKey, shiftKey } to an action, or null.
  function shortcut(event) {
    const key = String(event.key || ''), lower = key.toLowerCase();
    const letter = /^Key[A-Z]$/.test(event.code || '') ? event.code.slice(3).toLowerCase() : lower; // Alt on macOS changes event.key
    const mod = event.ctrlKey || event.metaKey;
    if (event.altKey && !mod && TOOL_KEYS[letter]) return { action: 'tool', tool: TOOL_KEYS[letter] };
    if (key === 'Escape') return { action: 'cancel' };
    if (key === 'Delete' || key === 'Backspace') return { action: 'delete' };
    if (mod && !event.altKey && letter === 'z') return { action: event.shiftKey ? 'redo' : 'undo' };
    if (mod && !event.altKey && letter === 'y') return { action: 'redo' };
    if (!mod && !event.altKey && event.shiftKey && letter === 'z') return { action: 'redo' };
    if (mod && !event.altKey && letter === 'd') return { action: 'clone' };
    return null;
  }

  return { GROUPS, PREFS_KEY, PREFS_V1, MAGNETS, DEFAULT_PREFS, readPrefs, savePrefs, model, chooseTool, toggleFavourite, nextMagnet, TOOL_KEYS, shortcutFor, shortcut };
});
