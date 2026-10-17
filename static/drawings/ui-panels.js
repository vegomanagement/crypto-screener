/* Drawing tools — properties dialog (style, text, coordinates, per-interval visibility, tool options,
 * templates) and the per-pane object tree (rename, hide, lock, delete, z-order).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./core.js'), require('./ui.js'));
  else root.WorkspaceDrawingsPanels = factory(root.WorkspaceDrawingsCore, root.WorkspaceDrawingsUi);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (C, U) {
  'use strict';
  const { el, btn } = U;
  const GROUP_LABELS = { seconds: 'Seconds', minutes: 'Minutes', hours: 'Hours', days: 'Days', weeks: 'Weeks / months' };
  const toLocalInput = ms => new Date(ms).toISOString().slice(0, 19); // UTC, shown as such
  const fromLocalInput = text => { const t = Date.parse(`${text}Z`); return Number.isFinite(t) ? t : null; };
  function field(label, input) { const row = el('label', 'dw-field'); row.append(el('span', 'dw-field-label', { text: label }), input); return row; }
  function input(type, value, attrs = {}) { const i = el('input', 'dw-input', { type, ...attrs }); if (type === 'checkbox') i.checked = Boolean(value); else i.value = value ?? ''; return i; }

  function createPanels(controller, { stage }) {
    const dialog = el('div', 'dw-ui dw-dialog', { role: 'dialog', 'aria-label': 'Drawing settings' });
    const tree = el('div', 'dw-ui dw-tree', { role: 'region', 'aria-label': 'Object tree' });
    dialog.hidden = true; tree.hidden = true;
    stage.append(dialog, tree);
    let editing = null; // { id, original, draft, tab }
    const a = () => controller.actions;

    function preview() { a().updateItem(editing.id, { points: editing.draft.points, style: editing.draft.style, text: editing.draft.text, opts: editing.draft.opts, visibility: editing.draft.visibility, name: editing.draft.name }, { save: false }); controller.state && stage.dispatchEvent(new Event('dw-redraw')); }
    function close(apply) {
      if (!editing) return;
      const { id, original, draft } = editing;
      editing = null; dialog.hidden = true;
      a().updateItem(id, original, { save: false });
      if (apply && JSON.stringify(original) !== JSON.stringify({ ...original, ...draft })) a().updateItem(id, { points: draft.points, style: draft.style, text: draft.text, opts: draft.opts, visibility: draft.visibility, name: draft.name });
      else stage.dispatchEvent(new Event('dw-redraw'));
    }
    function tabStyle(d) {
      const s = d.style, out = [];
      const color = input('color', s.color); color.dataset.k = 'style.color';
      const width = el('select', 'dw-input'); [1, 2, 3, 4].forEach(w => width.append(el('option', '', { value: String(w), text: `${w}px` }))); width.value = String(s.width); width.dataset.k = 'style.width';
      const dash = el('select', 'dw-input'); C.DASH_NAMES.forEach(n => dash.append(el('option', '', { value: n, text: n }))); dash.value = s.dash; dash.dataset.k = 'style.dash';
      const fill = input('color', s.fill || s.color); fill.dataset.k = 'style.fill';
      const alpha = input('number', s.fillAlpha, { min: '0', max: '1', step: '0.05' }); alpha.dataset.k = 'style.fillAlpha';
      out.push(field('Line colour', color), field('Line width', width), field('Line style', dash), field('Fill colour', fill), field('Fill opacity', alpha));
      return out;
    }
    function tabText(d) {
      const t = el('textarea', 'dw-input dw-textarea', { rows: '4', maxlength: '2000' }); t.value = d.text; t.dataset.k = 'text';
      const tc = input('color', d.style.textColor || d.style.color); tc.dataset.k = 'style.textColor';
      const fs = input('number', d.style.fontSize, { min: '8', max: '40', step: '1' }); fs.dataset.k = 'style.fontSize';
      const name = input('text', d.name, { maxlength: '80', placeholder: C.getTool(d.type)?.label || '' }); name.dataset.k = 'name';
      return [field('Text', t), field('Text colour', tc), field('Font size', fs), field('Name (object tree)', name)];
    }
    function tabCoords(d) {
      return d.points.map((p, i) => {
        const row = el('div', 'dw-coord');
        const time = input('datetime-local', toLocalInput(p.time), { step: '1' }); time.dataset.k = `points.${i}.time`;
        const price = input('text', p.price, { inputmode: 'decimal' }); price.dataset.k = `points.${i}.price`;
        row.append(el('span', 'dw-field-label', { text: `#${i + 1} (UTC)` }), time, price);
        return row;
      });
    }
    function tabVisibility(d) {
      return C.INTERVAL_GROUPS.map(g => { const c = input('checkbox', d.visibility[g] !== false); c.dataset.k = `visibility.${g}`; return field(GROUP_LABELS[g], c); });
    }
    function tabOptions(d) {
      const def = C.getTool(d.type), o = C.optsOf(d), out = [];
      for (const opt of def.options || []) {
        if (opt.type === 'bool') { const c = input('checkbox', o[opt.key]); c.dataset.k = `opts.${opt.key}`; out.push(field(opt.label, c)); }
        else if (opt.type === 'number') { const n = input('number', o[opt.key], { min: String(opt.min ?? ''), max: String(opt.max ?? ''), step: String(opt.step ?? 'any') }); n.dataset.k = `opts.${opt.key}`; out.push(field(opt.label, n)); }
        else if (opt.type === 'select') { const s = el('select', 'dw-input'); opt.choices.forEach(c => s.append(el('option', '', { value: c, text: c }))); s.value = o[opt.key]; s.dataset.k = `opts.${opt.key}`; out.push(field(opt.label, s)); }
        else if (opt.type === 'levels') {
          const box = el('div', 'dw-levels');
          (o.levels || []).forEach((l, i) => {
            const on = input('checkbox', l.on !== false); on.dataset.k = `level.${i}.on`;
            const v = input('number', l.v, { step: 'any' }); v.dataset.k = `level.${i}.v`;
            const c = input('color', l.color || d.style.color); c.dataset.k = `level.${i}.color`;
            const row = el('div', 'dw-level'); row.append(on, v, c); box.append(row);
          });
          const add = btn('dw-btn dw-wide', '+ level', 'Add level'); add.dataset.act = 'addLevel';
          out.push(el('div', 'dw-field-label', { text: opt.label }), box, add);
        }
      }
      return out.length ? out : [el('p', 'dw-muted', { text: 'No extra options for this tool.' })];
    }
    function tabTemplate(d) {
      const t = controller.templates, names = t.list(d.type), out = [];
      const sel = el('select', 'dw-input'); sel.append(el('option', '', { value: '', text: names.length ? '— choose —' : '(none saved)' }));
      names.forEach(n => sel.append(el('option', '', { value: n, text: `${n}${t.defaultName(d.type) === n ? ' (default)' : ''}` }))); sel.dataset.act = 'tplPick';
      const name = input('text', '', { placeholder: 'Template name', maxlength: '40' }); name.className += ' dw-tpl-name';
      const save = btn('dw-btn dw-wide', 'Save as template', 'Save current style as a template'); save.dataset.act = 'tplSave';
      const def = btn('dw-btn dw-wide', 'Use as default for new', 'Apply this template to new drawings of this tool'); def.dataset.act = 'tplDefault';
      const reset = btn('dw-btn dw-wide', 'Reset default', 'New drawings use built-in defaults'); reset.dataset.act = 'tplReset';
      out.push(field('Apply template', sel), field('Save as', name), save, def, reset);
      return out;
    }
    const TABS = [['style', 'Style', tabStyle], ['text', 'Text', tabText], ['coords', 'Coordinates', tabCoords], ['visibility', 'Visibility', tabVisibility], ['options', 'Options', tabOptions], ['template', 'Template', tabTemplate]];
    function render() {
      const d = editing.draft, def = C.getTool(d.type);
      const head = el('div', 'dw-dialog-head'); head.append(el('strong', '', { text: `${def?.label || d.type} settings` }), Object.assign(btn('dw-btn', '✕', 'Close'), { onclick: () => close(false) }));
      const tabs = el('div', 'dw-tabs', { role: 'tablist' });
      TABS.forEach(([key, label]) => { const b = btn(`dw-tab${editing.tab === key ? ' is-on' : ''}`, label, label); b.dataset.tab = key; b.setAttribute('role', 'tab'); b.setAttribute('aria-selected', String(editing.tab === key)); tabs.append(b); });
      const body = el('div', 'dw-dialog-body'); body.append(...TABS.find(t => t[0] === editing.tab)[2](d));
      const foot = el('div', 'dw-dialog-foot');
      const ok = btn('dw-btn dw-primary', 'OK', 'Apply'); ok.dataset.act = 'ok';
      const cancel = btn('dw-btn', 'Cancel', 'Discard changes'); cancel.dataset.act = 'cancel';
      foot.append(cancel, ok);
      dialog.replaceChildren(head, tabs, body, foot);
    }
    function openSettings(id, tab = 'style') {
      const d = a().find(id);
      if (!d || d.virtual) return;
      if (editing) close(false);
      editing = { id, original: C.clone(d), draft: C.clone(d), tab };
      render(); dialog.hidden = false;
      const first = dialog.querySelector(tab === 'text' ? 'textarea' : '.dw-input');
      first?.focus?.();
    }
    function setPath(draft, key, value) {
      const [head, ...rest] = key.split('.');
      if (head === 'points') { const i = Number(rest[0]); draft.points = draft.points.map((p, j) => (j === i ? { ...p, [rest[1]]: value } : p)); return; }
      if (head === 'level') { const i = Number(rest[0]); const levels = (C.optsOf(draft).levels || []).map((l, j) => (j === i ? { ...l, [rest[1]]: value } : l)); draft.opts = { ...draft.opts, levels }; return; }
      if (rest.length) draft[head] = { ...(head === 'opts' ? C.optsOf(draft) : draft[head]), [rest[0]]: value };
      else draft[head] = value;
    }
    function readValue(t) {
      const k = t.dataset.k;
      if (t.type === 'checkbox') return t.checked;
      if (/^points\.\d+\.time$/.test(k)) return fromLocalInput(t.value);
      if (/^points\.\d+\.price$/.test(k)) return C.positiveDecimal(t.value.trim()) ? t.value.trim() : null;
      if (t.type === 'number' || k === 'style.width') { const n = Number(t.value); return Number.isFinite(n) ? n : null; }
      return t.value;
    }
    function onEdit(event) {
      const t = event.target;
      if (!editing || !t.dataset.k) return;
      const value = readValue(t);
      t.classList.toggle('is-invalid', value === null);
      if (value === null) return;
      setPath(editing.draft, t.dataset.k, value);
      preview();
    }
    dialog.addEventListener('input', onEdit); dialog.addEventListener('change', onEdit);
    dialog.addEventListener('keydown', event => { if (event.key === 'Escape') { event.stopPropagation(); close(false); } if (event.key === 'Enter' && event.target.tagName !== 'TEXTAREA') { event.preventDefault(); close(true); } });
    dialog.addEventListener('click', event => {
      const tab = event.target.closest('[data-tab]'), act = event.target.closest('[data-act]');
      if (tab && editing) { editing.tab = tab.dataset.tab; render(); return; }
      if (!act || !editing) return;
      const t = controller.templates, d = editing.draft, nameInput = dialog.querySelector('.dw-tpl-name');
      switch (act.dataset.act) {
        case 'ok': close(true); break;
        case 'cancel': close(false); break;
        case 'addLevel': { const levels = [...(C.optsOf(d).levels || []), { v: 1, on: true, color: d.style.color }]; d.opts = { ...d.opts, levels }; render(); preview(); break; }
        case 'tplSave': if (nameInput?.value.trim()) { t.save(d.type, nameInput.value.trim(), d); render(); } break;
        case 'tplDefault': { const n = nameInput?.value.trim() || dialog.querySelector('[data-act="tplPick"]')?.value; if (n) { if (!t.get(d.type, n)) t.save(d.type, n, d); t.setDefault(d.type, n); render(); } break; }
        case 'tplReset': t.setDefault(d.type, null); render(); break;
        default: break;
      }
    });
    dialog.addEventListener('change', event => {
      if (event.target.dataset.act !== 'tplPick' || !editing || !event.target.value) return;
      editing.draft = controller.templates.apply(editing.draft, event.target.value); render(); preview();
    });

    // ── object tree ──
    function renderTree() {
      if (tree.hidden) return;
      const s = controller.state, rows = [];
      const head = el('div', 'dw-dialog-head'); head.append(el('strong', '', { text: `Objects (${s.items.length})` }), Object.assign(btn('dw-btn', '✕', 'Close object tree'), { onclick: () => toggleTree(false) }));
      for (const d of s.items.slice().reverse()) {
        const row = el('div', `dw-tree-row${s.selected === d.id ? ' is-selected' : ''}${d.hidden ? ' is-hidden' : ''}`); row.dataset.id = d.id;
        const name = input('text', d.name || C.getTool(d.type)?.label || d.type, { title: 'Rename', 'aria-label': 'Name' }); name.dataset.tree = 'name';
        const b = (text, title, key) => { const x = btn('dw-btn', text, title); x.dataset.tree = key; return x; };
        row.append(el('span', 'dw-icon', { text: C.getTool(d.type)?.icon || '•' }), name, b(d.hidden ? '◌' : '👁', d.hidden ? 'Show' : 'Hide', 'hide'),
          b(d.locked ? '🔒' : '🔓', d.locked ? 'Unlock' : 'Lock', 'lock'), b('▲', 'Bring forward', 'up'), b('▼', 'Send backward', 'down'), b('✕', 'Delete', 'delete'));
        rows.push(row);
      }
      if (!rows.length) rows.push(el('p', 'dw-muted', { text: 'No drawings for this symbol yet.' }));
      const list = el('div', 'dw-tree-list'); list.append(...rows);
      tree.replaceChildren(head, list);
    }
    tree.addEventListener('click', event => {
      const row = event.target.closest('[data-id]'), act = event.target.closest('[data-tree]');
      if (!row) return;
      const id = row.dataset.id, d = a().find(id);
      if (!d) return;
      const key = act?.dataset.tree;
      if (key === 'hide') a().updateItem(id, { hidden: !d.hidden });
      else if (key === 'lock') a().updateItem(id, { locked: !d.locked });
      else if (key === 'up' || key === 'down') a().reorder(id, key);
      else if (key === 'delete') a().removeItem(id);
      else if (key !== 'name') controller.select(id);
    });
    tree.addEventListener('change', event => { const row = event.target.closest('[data-id]'); if (row && event.target.dataset.tree === 'name') a().updateItem(row.dataset.id, { name: event.target.value.slice(0, 80) }); });
    tree.addEventListener('keydown', event => { if (event.key === 'Enter' && event.target.dataset.tree === 'name') event.target.blur(); });
    function toggleTree(force) { tree.hidden = force === undefined ? !tree.hidden : !force; renderTree(); }
    return { openSettings, close, isOpen: () => Boolean(editing), toggleTree, treeOpen: () => !tree.hidden, renderTree, dispose() { dialog.remove(); tree.remove(); }, elements: { dialog, tree } };
  }
  return { createPanels, toLocalInput, fromLocalInput };
});
