// Live check 2: editing UX through real input — long/short tool, mini-toolbar, properties dialog
// (style/text/coords/visibility/options/template), drag + Ctrl+Z / Shift+Z, Del + undo, level mode,
// object tree (rename/hide/z-order), style template default, interval switch 15m → 1h, page reload.
'use strict';
const fs = require('node:fs');
const { open, waitChart, geometry, stored, EV } = require('./live-common.cjs');
const MOD = { alt: 1, ctrl: 2, meta: 4, shift: 8 };
(async () => {
  const c = await open({ interval: '15', reset: true });
  const g = await geometry(c);
  const out = { when: new Date().toISOString(), checks: {} };
  const check = (name, ok, detail) => { out.checks[name] = { ok: Boolean(ok), detail }; console.log(ok ? 'OK ' : 'FAIL', name, detail === undefined ? '' : JSON.stringify(detail).slice(0, 200)); };
  const key = async (k, code, modifiers = 0, vk = 0) => { await c.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: k, code, modifiers, windowsVirtualKeyCode: vk }); await c.send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code, modifiers, windowsVirtualKeyCode: vk }); await c.sleep(120); };
  const center = async sel => c.eval(`(() => { const r = document.querySelector(${JSON.stringify(sel)})?.getBoundingClientRect(); return r && r.width ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : null; })()`);
  const clickSel = async sel => { const r = await center(sel); if (!r) throw new Error(`missing ${sel}`); await c.click(r.x, r.y); await c.sleep(150); };
  const CTL = '[...WorkspaceDrawings.instances][0]';
  const px = async (time, price) => c.eval(`(() => { const v = ${CTL}.view(); const r = document.querySelector('.chart-host').getBoundingClientRect(); return { x: r.left + v.mapper.x(${time}), y: r.top + v.mapper.y(${JSON.stringify(price)}) }; })()`);
  const frameCount = async () => c.eval(`${CTL}.state.frame.prims.size`);
  const X = f => g.host.left + g.host.width * f, Y = f => g.host.top + g.host.height * f;

  // 1. long + short via the favourites strip
  await clickSel('.dw-favs [data-tool="long"]'); await c.click(X(0.3), Y(0.5)); await c.click(X(0.45), Y(0.35)); await key('Escape', 'Escape', 0, 27);
  await clickSel('.dw-favs [data-tool="short"]'); await c.click(X(0.55), Y(0.45)); await c.click(X(0.72), Y(0.6)); await key('Escape', 'Escape', 0, 27);
  let items = await stored(c);
  check('long_short_created', items.length === 2 && items[0].type === 'long' && items[1].type === 'short' && items.every(d => d.points.length === 3), items.map(d => d.points.map(p => p.price)));
  await c.shot(`${EV}/long_short_position.png`);
  // 2. select the long position by clicking its target zone → mini-toolbar
  const lp = items[0].points, tz = await px(lp[0].time, String((Number(lp[0].price) + Number(lp[1].price)) / 2));
  await c.click(tz.x + 15, tz.y);
  check('select_click', (await c.eval(`${CTL}.state.selected`)) === items[0].id);
  check('mini_toolbar_visible', await c.eval(`!document.querySelector('.dw-mini').hidden`));
  await c.shot(`${EV}/mini_toolbar.png`);
  // 3. properties dialog via the mini-toolbar gear; tabs; per-interval visibility (hide on minutes)
  await clickSel('.dw-mini [data-mini="settings"]');
  check('dialog_open', await c.eval(`!document.querySelector('.dw-dialog').hidden`));
  await c.shot(`${EV}/properties_style.png`);
  await clickSel('.dw-dialog [data-tab="options"]'); await c.shot(`${EV}/properties_options.png`);
  // risk 2% via typing into the field
  await c.eval(`(() => { const i = [...document.querySelectorAll('.dw-dialog .dw-field')].find(f => /Risk %/.test(f.textContent)).querySelector('input'); i.focus(); i.select(); return true; })()`);
  await c.send('Input.insertText', { text: '2' });
  await clickSel('.dw-dialog [data-tab="coords"]'); await c.shot(`${EV}/properties_coords.png`);
  await clickSel('.dw-dialog [data-tab="visibility"]');
  const minutesBox = await c.eval(`(() => { const f = [...document.querySelectorAll('.dw-dialog .dw-field')].find(x => /Minutes/.test(x.textContent)); const r = f.querySelector('input').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
  await c.click(minutesBox.x, minutesBox.y);
  await c.shot(`${EV}/properties_visibility.png`);
  await clickSel('.dw-dialog [data-act="ok"]');
  items = await stored(c);
  check('dialog_applied', items[0].visibility.minutes === false && items[0].opts.riskPct === 2, { visibility: items[0].visibility, riskPct: items[0].opts.riskPct });
  await c.sleep(300);
  check('hidden_on_15m', (await frameCount()) === 1, await frameCount());
  // 4. trend line via Alt+T, body drag, Ctrl+Z / Shift+Z
  await key('t', 'KeyT', MOD.alt); await c.click(X(0.2), Y(0.75)); await c.click(X(0.4), Y(0.68));
  await key('Escape', 'Escape', 0, 27);
  items = await stored(c); const trend = items.at(-1);
  check('alt_t_trend', trend.type === 'trend');
  const mid = await px((trend.points[0].time + trend.points[1].time) / 2, String((Number(trend.points[0].price) + Number(trend.points[1].price)) / 2));
  await c.drag(mid.x, mid.y, mid.x + 60, mid.y - 40, 10);
  const moved = (await stored(c)).at(-1).points;
  check('drag_moves', moved[0].time !== trend.points[0].time && moved[0].price !== trend.points[0].price, { before: trend.points, after: moved });
  await key('z', 'KeyZ', MOD.ctrl);
  check('ctrl_z_undo_drag', JSON.stringify((await stored(c)).at(-1).points) === JSON.stringify(trend.points));
  await key('Z', 'KeyZ', MOD.shift);
  check('shift_z_redo', JSON.stringify((await stored(c)).at(-1).points) === JSON.stringify(moved));
  // 5. Del + undo
  await c.eval(`${CTL}.select(${JSON.stringify(trend.id)})`);
  await key('Delete', 'Delete', 0, 46);
  check('del_removes', (await stored(c)).length === 2);
  await key('z', 'KeyZ', MOD.meta);
  check('meta_z_restores', (await stored(c)).length === 3);
  // 6. level mode + Alt+H → shared level
  await clickSel('.dw-toolbar [data-opt="level"]');
  await key('h', 'KeyH', MOD.alt); await c.click(X(0.5), Y(0.3)); await key('Escape', 'Escape', 0, 27);
  items = await stored(c); const hl = items.at(-1);
  const lv = await c.eval(`ScreenerLevels.createStore().list({ exchange: 'binance', symbol: 'BTCUSDT' })`);
  check('level_sync', hl.type === 'hline' && hl.levelId && lv.some(l => l.id === hl.levelId && l.price === hl.points[0].price), { hl: hl.points, lv: lv.map(l => l.price) });
  await clickSel('.dw-toolbar [data-opt="level"]');
  // 7. template: save the trend's style as default, new trend uses it
  await c.eval(`${CTL}.actions.updateItem(${JSON.stringify(trend.id)}, { style: { color: '#ff00aa', width: 3 } })`);
  await c.eval(`${CTL}.panels.openSettings(${JSON.stringify(trend.id)}, 'template')`);
  await c.eval(`(() => { const i = document.querySelector('.dw-tpl-name'); i.focus(); return true; })()`);
  await c.send('Input.insertText', { text: 'Pink 3px' });
  await clickSel('.dw-dialog [data-act="tplDefault"]');
  await c.shot(`${EV}/properties_template.png`);
  await clickSel('.dw-dialog [data-act="ok"]');
  await key('t', 'KeyT', MOD.alt); await c.click(X(0.6), Y(0.8)); await c.click(X(0.75), Y(0.72)); await key('Escape', 'Escape', 0, 27);
  const t2 = (await stored(c)).at(-1);
  check('template_default_applied', t2.type === 'trend' && t2.style.color === '#ff00aa' && t2.style.width === 3, t2.style);
  // 8. object tree: rename, hide, z-order (real clicks / typing)
  await clickSel('.dw-toolbar [data-opt="tree"]');
  const firstRow = await c.eval(`document.querySelector('.dw-tree-row').dataset.id`);
  await c.eval(`(() => { const i = document.querySelector('.dw-tree-row input'); i.focus(); i.select(); return true; })()`);
  await c.send('Input.insertText', { text: 'My trend' }); await key('Enter', 'Enter', 0, 13); await c.sleep(150);
  await clickSel(`.dw-tree-row[data-id="${firstRow}"] [data-tree="down"]`);
  await c.shot(`${EV}/object_tree.png`);
  items = await stored(c);
  check('tree_rename_and_zorder', items.find(d => d.id === firstRow)?.name === 'My trend' && items.at(-1).id !== firstRow, items.map(d => [d.type, d.name]));
  await clickSel(`.dw-tree-row[data-id="${firstRow}"] [data-tree="hide"]`);
  check('tree_hide', (await stored(c)).find(d => d.id === firstRow).hidden === true);
  await clickSel(`.dw-tree-row[data-id="${firstRow}"] [data-tree="hide"]`);
  await clickSel('.dw-toolbar [data-opt="tree"]');
  const before = await stored(c);
  out.frame15 = await frameCount();
  await c.shot(`${EV}/edit_15m.png`);
  // 9. interval switch via the toolbar interval button (real click), per-interval visibility
  const btn1h = await c.eval(`(() => { const b = [...document.querySelectorAll('#favorite-intervals button')].find(x => x.textContent.trim() === '1h'); const r = b.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
  await c.click(btn1h.x, btn1h.y); await c.sleep(4000);
  const interval = await c.eval(`${CTL}.context().interval`);
  out.frame1h = await frameCount();
  check('interval_1h_same_drawings', interval === '60' && JSON.stringify(await stored(c)) === JSON.stringify(before), { interval });
  check('minutes_hidden_object_visible_on_1h', out.frame1h === out.frame15 + 1, { frame15: out.frame15, frame1h: out.frame1h });
  await c.shot(`${EV}/edit_1h.png`);
  // 10. reload → everything persists
  await c.send('Page.reload'); await waitChart(c);
  const afterReload = await stored(c);
  check('reload_persists', JSON.stringify(afterReload) === JSON.stringify(before), { count: afterReload.length });
  check('reload_rendered', (await frameCount()) === out.frame1h, await frameCount());
  out.final = afterReload.map(d => ({ type: d.type, points: d.points, style: d.style, visibility: d.visibility, name: d.name, levelId: d.levelId }));
  fs.writeFileSync(`${EV}/live_edit.json`, JSON.stringify(out, null, 1));
  console.log('passed', Object.values(out.checks).filter(x => x.ok).length, '/', Object.keys(out.checks).length);
  c.close(); process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
