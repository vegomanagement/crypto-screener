/* Drawing tools — DOM chrome: left toolbar with grouped flyouts, favourites strip and the floating
 * mini-toolbar for the selected object. Pure rendering of TB.model(); actions go to the controller.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./core.js'), require('./toolbar-model.js'));
  else root.WorkspaceDrawingsUi = factory(root.WorkspaceDrawingsCore, root.WorkspaceDrawingsToolbar);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (C, TB) {
  'use strict';
  function el(tag, cls, attrs = {}) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'text') node.textContent = v;
      else if (k === 'title') { node.title = v; node.setAttribute('aria-label', v); }
      else node.setAttribute(k, v);
    }
    return node;
  }
  const btn = (cls, text, title) => { const b = el('button', cls, { type: 'button', text, title }); return b; };
  const MAGNET_TITLES = { off: 'Magnet off', weak: 'Weak magnet: snaps to O/H/L/C within 14 px', strong: 'Strong magnet: always snaps to the nearest O/H/L/C' };

  function createChrome(controller, { header, stage, panels }) {
    // Every listener shares one lifetime: dispose() aborts it, so the document-level
    // pointerdown listener cannot keep a destroyed pane alive.
    const lifetime = new AbortController();
    const listen = (t, type, fn, capture = false) => t.addEventListener(type, fn, { capture, signal: lifetime.signal });
    const ui = { flyout: null };
    // Header toggle (kept from v1) shows/hides the left toolbar.
    const toggle = btn('pane-draw-toggle', '✎', 'Drawing tools for this chart');
    header?.insertBefore(toggle, header.querySelector('.pane-reload'));
    const bar = el('div', 'dw-ui dw-toolbar', { role: 'toolbar', 'aria-label': 'Drawing tools', 'aria-orientation': 'vertical' });
    const favs = el('div', 'dw-ui dw-favs', { role: 'toolbar', 'aria-label': 'Favourite drawing tools' });
    const mini = el('div', 'dw-ui dw-mini', { role: 'toolbar', 'aria-label': 'Selected drawing' });
    const fly = el('div', 'dw-ui dw-flyout', { role: 'menu' });
    fly.hidden = true; mini.hidden = true;
    stage.append(bar, favs, mini, fly);
    const state = () => controller.state;
    bar.hidden = state().prefs.toolbarHidden === true;

    function closeFlyout() { fly.hidden = true; ui.flyout = null; bar.querySelectorAll('[aria-expanded="true"]').forEach(b => b.setAttribute('aria-expanded', 'false')); }
    function openFlyout(group, anchor) {
      if (ui.flyout === group.id) { closeFlyout(); return; }
      ui.flyout = group.id; fly.replaceChildren(el('div', 'dw-flyout-title', { text: group.label }));
      for (const tool of group.tools) {
        const row = el('div', 'dw-flyout-row');
        const pick = btn('dw-flyout-tool', '', `${tool.label}${tool.shortcut ? ` (${tool.shortcut})` : ''}`);
        pick.dataset.tool = tool.type; pick.setAttribute('role', 'menuitem');
        pick.append(el('span', 'dw-icon', { text: tool.icon }), el('span', 'dw-name', { text: tool.label }), el('span', 'dw-key', { text: tool.shortcut }));
        if (state().tool === tool.type) pick.setAttribute('aria-current', 'true');
        const star = btn(`dw-star${tool.favourite ? ' is-on' : ''}`, tool.favourite ? '★' : '☆', tool.favourite ? 'Remove from favourites' : 'Add to favourites');
        star.dataset.fav = tool.type;
        row.append(pick, star); fly.append(row);
      }
      const r = anchor.getBoundingClientRect(), s = stage.getBoundingClientRect();
      fly.style.left = `${r.right - s.left + 4}px`; fly.style.top = `${Math.max(0, r.top - s.top)}px`;
      fly.hidden = false; anchor.setAttribute('aria-expanded', 'true');
    }
    listen(fly, 'click', event => {
      const fav = event.target.closest('[data-fav]'), pick = event.target.closest('[data-tool]');
      if (fav) { controller.setPrefs(TB.toggleFavourite(state().prefs, fav.dataset.fav)); const g = TB.model(state().prefs).find(x => x.id === ui.flyout); const anchor = bar.querySelector(`[data-group="${ui.flyout}"]`); ui.flyout = null; if (g && anchor) openFlyout(g, anchor); return; }
      if (pick) { controller.setTool(pick.dataset.tool); closeFlyout(); }
    });

    function renderBar() {
      const s = state(), groups = TB.model(s.prefs), nodes = [];
      const cursor = btn('dw-btn', '↖', 'Cursor / select (Esc)'); cursor.dataset.tool = 'cursor'; cursor.setAttribute('aria-pressed', String(s.tool === 'cursor'));
      nodes.push(cursor);
      for (const g of groups) {
        const current = g.tools.find(t => t.type === g.current), wrap = el('div', 'dw-group');
        const main = btn('dw-btn', current.icon, `${current.label}${current.shortcut ? ` (${current.shortcut})` : ''}`);
        main.dataset.tool = current.type; main.setAttribute('aria-pressed', String(g.tools.some(t => t.type === s.tool) && s.tool === current.type));
        const more = btn('dw-more', '›', `${g.label} tools`); more.dataset.group = g.id; more.setAttribute('aria-haspopup', 'menu'); more.setAttribute('aria-expanded', String(ui.flyout === g.id));
        wrap.append(main, more); nodes.push(wrap);
      }
      nodes.push(el('div', 'dw-sep'));
      const opt = (key, text, title, pressed) => { const b = btn('dw-btn', text, title); b.dataset.opt = key; b.setAttribute('aria-pressed', String(Boolean(pressed))); return b; };
      nodes.push(opt('magnet', s.prefs.magnet === 'strong' ? '🧲⁺' : '🧲', MAGNET_TITLES[s.prefs.magnet], s.prefs.magnet !== 'off'));
      nodes.push(opt('stay', '✎⁺', 'Stay in drawing mode', s.prefs.stayInDrawing));
      nodes.push(opt('level', 'L', 'Level mode: new horizontal lines/rays are also shared price levels', s.prefs.level));
      nodes.push(opt('session', 'S', 'Session start lines (00:00 UTC)', s.prefs.session));
      nodes.push(opt('lockall', s.lockedAll ? '🔒' : '🔓', 'Lock all drawings', s.lockedAll));
      nodes.push(opt('hideall', '👁', 'Hide all drawings', s.hidden));
      nodes.push(opt('tree', '☰', 'Object tree', panels.treeOpen()));
      nodes.push(opt('favs', '★', 'Show favourites strip', s.prefs.favouritesVisible));
      nodes.push(opt('undo', '↶', 'Undo (Ctrl/⌘+Z)', false), opt('redo', '↷', 'Redo (Ctrl/⌘+Shift+Z)', false));
      nodes.push(opt('clear', '🗑', 'Remove all drawings of this symbol (click twice)', false));
      bar.replaceChildren(...nodes);
      bar.querySelector('[data-opt="undo"]').disabled = !controller.history.canUndo;
      bar.querySelector('[data-opt="redo"]').disabled = !controller.history.canRedo;
      favs.hidden = bar.hidden || !s.prefs.favouritesVisible || !s.prefs.favourites.length;
      favs.replaceChildren(...s.prefs.favourites.map(type => { const t = C.getTool(type); const b = btn('dw-btn', t.icon, t.label); b.dataset.tool = type; b.setAttribute('aria-pressed', String(s.tool === type)); return b; }));
      toggle.setAttribute('aria-pressed', String(!bar.hidden)); toggle.classList.toggle('is-active', !bar.hidden || s.items.length > 0);
    }
    let clearArmed = 0;
    listen(toggle, 'click', event => { event.stopPropagation(); bar.hidden = !bar.hidden; controller.setPrefs({ toolbarHidden: bar.hidden }); if (bar.hidden) { controller.setTool('cursor'); closeFlyout(); } renderBar(); });
    function onBar(event) {
      const tool = event.target.closest('[data-tool]'), more = event.target.closest('[data-group]'), opt = event.target.closest('[data-opt]');
      if (more) { const g = TB.model(state().prefs).find(x => x.id === more.dataset.group); if (g) openFlyout(g, more); return; }
      closeFlyout();
      if (tool) { controller.setTool(state().tool === tool.dataset.tool ? 'cursor' : tool.dataset.tool); return; }
      if (!opt) return;
      const p = state().prefs, a = controller.actions;
      switch (opt.dataset.opt) {
        case 'magnet': controller.setPrefs({ magnet: TB.nextMagnet(p.magnet) }); break;
        case 'stay': controller.setPrefs({ stayInDrawing: !p.stayInDrawing }); break;
        case 'level': controller.setPrefs({ level: !p.level }); break;
        case 'session': controller.setPrefs({ session: !p.session }); break;
        case 'lockall': a.setLockedAll(!state().lockedAll); break;
        case 'hideall': a.setHiddenAll(!state().hidden); break;
        case 'tree': panels.toggleTree(); renderBar(); break;
        case 'favs': controller.setPrefs({ favouritesVisible: !p.favouritesVisible }); break;
        case 'undo': a.undo(); break;
        case 'redo': a.redo(); break;
        case 'clear':
          if (Date.now() - clearArmed > 3000) { clearArmed = Date.now(); opt.textContent = '✓?'; setTimeout(renderBar, 3000); return; }
          clearArmed = 0; a.clearAll(); break;
        default: break;
      }
    }
    listen(bar, 'click', onBar); listen(favs, 'click', onBar);
    // Long-press / right-click on a group button opens its flyout as well.
    listen(bar, 'contextmenu', event => { const wrap = event.target.closest('.dw-group'); if (!wrap) return; event.preventDefault(); const more = wrap.querySelector('[data-group]'); const g = TB.model(state().prefs).find(x => x.id === more.dataset.group); if (g) openFlyout(g, more); });
    listen(document, 'pointerdown', event => { if (!fly.hidden && !event.target.closest?.('.dw-flyout, .dw-more')) closeFlyout(); }, true);

    // ── mini toolbar next to the selected object ──
    const miniBtn = (text, title, key, cls = 'dw-btn') => { const b = btn(cls, text, title); b.dataset.mini = key; return b; };
    function renderMini() {
      const s = state(), d = s.selected && controller.actions.find(s.selected);
      mini.hidden = !d;
      if (!d) return;
      const color = el('input', 'dw-color', { type: 'color', title: 'Colour' }); color.value = d.style.color; color.dataset.mini = 'color';
      const width = el('select', 'dw-select', { title: 'Line width' }); [1, 2, 3, 4].forEach(w => width.append(el('option', '', { value: String(w), text: `${w}px` }))); width.value = String(d.style.width); width.dataset.mini = 'width';
      const dash = el('select', 'dw-select', { title: 'Line style' }); C.DASH_NAMES.forEach(n => dash.append(el('option', '', { value: n, text: { solid: '──', dashed: '- -', dotted: '···' }[n] }))); dash.value = d.style.dash; dash.dataset.mini = 'dash';
      const label = el('span', 'dw-mini-label', { text: d.virtual ? `Level ${d.points[0].price}` : (d.name || C.getTool(d.type)?.label || d.type) });
      const nodes = [label, color, width];
      if (!d.virtual) {
        nodes.push(dash);
        nodes.push(miniBtn(d.locked ? '🔒' : '🔓', d.locked ? 'Unlock' : 'Lock', 'lock'), miniBtn('⧉', 'Clone (Ctrl/⌘+D)', 'clone'), miniBtn('⚙', 'Settings', 'settings'));
      }
      nodes.push(miniBtn('✕', 'Delete (Del)', 'delete', 'dw-btn dw-danger'));
      mini.replaceChildren(...nodes);
    }
    function positionMini(s, v) {
      const d = s.selected && controller.actions.find(s.selected);
      if (!d || mini.hidden || !s.frame) return;
      const b = C.bounds(s.frame.prims.get(d) || []);
      const host = stage.querySelector('.chart-host') || stage, offset = host.getBoundingClientRect().top - stage.getBoundingClientRect().top;
      let x = b ? (b.left + b.right) / 2 : v.w / 2, y = b ? b.top - 40 : 8;
      if (y < 4) y = b ? Math.min(v.h - 36, b.bottom + 10) : 8;
      x = Math.max(4, Math.min(v.w - mini.offsetWidth - 4, x - mini.offsetWidth / 2));
      mini.style.left = `${Math.round(x)}px`; mini.style.top = `${Math.round(Math.max(4, y) + offset)}px`;
    }
    listen(mini, 'input', event => { const id = state().selected, t = event.target; if (id && t.dataset.mini === 'color') controller.actions.updateItem(id, { style: { color: t.value } }, { save: false }); });
    listen(mini, 'change', event => {
      const id = state().selected, t = event.target;
      if (!id) return;
      if (t.dataset.mini === 'color') controller.actions.updateItem(id, { style: { color: t.value } });
      if (t.dataset.mini === 'width') controller.actions.updateItem(id, { style: { width: Number(t.value) } });
      if (t.dataset.mini === 'dash') controller.actions.updateItem(id, { style: { dash: t.value } });
    });
    listen(mini, 'click', event => {
      const t = event.target.closest('button'), id = state().selected;
      if (!t || !id) return;
      const a = controller.actions, d = a.find(id);
      if (t.dataset.mini === 'lock') a.updateItem(id, { locked: !d.locked });
      if (t.dataset.mini === 'clone') a.cloneItem(id);
      if (t.dataset.mini === 'settings') panels.openSettings(id);
      if (t.dataset.mini === 'delete') a.removeItem(id);
    });
    let lastSel = null;
    function sync() {
      renderBar();
      const s = state(), key = s.selected && JSON.stringify(controller.actions.find(s.selected));
      if (key !== lastSel) { lastSel = key; renderMini(); }
    }
    return { sync, positionMini, closeFlyout, openFlyout: groupId => { const g = TB.model(state().prefs).find(x => x.id === groupId); const more = bar.querySelector(`[data-group="${groupId}"]`); if (g && more) { ui.flyout = null; openFlyout(g, more); } },
      dispose() { lifetime.abort(); toggle.remove(); bar.remove(); favs.remove(); mini.remove(); fly.remove(); }, elements: { toggle, bar, favs, mini, fly } };
  }
  return { el, btn, createChrome };
});
