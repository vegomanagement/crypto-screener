// Live check 1: draw every Tier-1 tool on Binance BTCUSDT 15m through real CDP mouse/keyboard input
// (toolbar flyouts + Alt shortcuts), verify each lands in localStorage v2, screenshot the groups.
'use strict';
const fs = require('node:fs');
const { open, waitChart, geometry, stored, EV } = require('./live-common.cjs');
const START = process.argv[2] || '15', OTHER = START === '15' ? '60' : '15', LABEL = { 15: '15m', 60: '1h' };
const MOD = { alt: 1, ctrl: 2, meta: 4, shift: 8 };
const SHORTCUT = { trend: 't', hline: 'h', vline: 'v', cross: 'c', fib: 'f', rect: 'r' };

(async () => {
  const c = await open({ interval: START, reset: true });
  const g = await geometry(c);
  const keyEv = async (key, code, modifiers = 0, vk = 0) => {
    await c.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key, code, modifiers, windowsVirtualKeyCode: vk });
    await c.send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, modifiers, windowsVirtualKeyCode: vk });
  };
  const rect = async sel => c.eval(`(() => { const r = document.querySelector(${JSON.stringify(sel)})?.getBoundingClientRect(); return r && { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
  const clickSel = async sel => { const r = await rect(sel); if (!r) throw new Error(`missing ${sel}`); await c.click(r.x, r.y); await c.sleep(120); };
  const tools = await c.eval(`WorkspaceDrawings.toolList().map(t => ({ type: t.type, group: t.group, mode: t.mode, points: t.points, editText: Boolean(t.editText), temporary: Boolean(t.temporary) }))`);
  const results = [];
  // cell grid over the plot (right of the toolbar, below the legend)
  const cols = 10, x0 = g.host.left + 70, y0 = g.host.top + 60, cw = (g.host.width - 140) / cols, ch = Math.min(85, (g.host.height - 110) / Math.ceil(tools.length / cols));
  let n = 0;
  const shotGroups = new Set();
  for (const t of tools) {
    const col = n % cols, row = Math.floor(n / cols); n += 1;
    const cx = x0 + col * cw, cy = y0 + row * ch;
    const before = (await stored(c)).length;
    // choose the tool: Alt shortcut where defined, otherwise group flyout → menu item (real clicks)
    if (SHORTCUT[t.type]) { await keyEv(SHORTCUT[t.type], `Key${SHORTCUT[t.type].toUpperCase()}`, MOD.alt); }
    else {
      await clickSel(`.dw-more[data-group="${t.group}"]`);
      if (!shotGroups.has(t.group)) { shotGroups.add(t.group); await c.shot(`${EV}/flyout_${t.group}.png`); }
      await clickSel(`.dw-flyout-tool[data-tool="${t.type}"]`);
    }
    const active = await c.eval(`[...WorkspaceDrawings.instances][0].state.tool`);
    const pts = [[cx + 10, cy + 10], [cx + cw - 25, cy + ch - 25], [cx + cw / 2, cy + 5], [cx + cw - 10, cy + ch / 2], [cx + 20, cy + ch - 10], [cx + cw / 3, cy + ch / 2], [cx + cw - 5, cy + 15]];
    if (t.mode === 'free') await c.drag(pts[0][0], pts[0][1], pts[1][0], pts[1][1], 14);
    else if (t.mode === 'multi') { for (const p of pts.slice(0, 4)) await c.click(p[0], p[1]); await keyEv('Enter', 'Enter', 0, 13); }
    else if (t.points === 2 && n % 2 === 0) await c.drag(pts[0][0], pts[0][1], pts[1][0], pts[1][1], 6); // press-drag-release variant
    else for (const p of pts.slice(0, t.points)) await c.click(p[0], p[1]);
    await c.sleep(150);
    let textOk = null;
    if (t.editText) {
      const open = await c.eval(`!document.querySelector('.dw-dialog').hidden`);
      if (open) {
        await c.eval(`(() => { const t = document.querySelector('.dw-dialog textarea'); t.focus(); t.select(); return true; })()`);
        await c.send('Input.insertText', { text: `${t.type} live` });
        await clickSel('.dw-dialog [data-act="ok"]');
      }
      textOk = (await stored(c)).at(-1)?.text === `${t.type} live`;
    }
    await keyEv('Escape', 'Escape', 0, 27);
    const after = await stored(c);
    const last = after.at(-1);
    results.push({ type: t.type, group: t.group, viaShortcut: Boolean(SHORTCUT[t.type]), activeTool: active, stored: t.temporary ? 'temporary' : after.length === before + 1 && last.type === t.type,
      points: last?.type === t.type ? last.points.length : null, textOk });
  }
  const ok = results.filter(r => r.stored === true).length;
  console.log(JSON.stringify({ tools: tools.length, storedOk: ok, failed: results.filter(r => r.stored === false) }, null, 1));
  await c.shot(`${EV}/all_tools_${LABEL[START]}.png`);
  // switch interval with a real click on the interval button, then reload: everything must persist
  const CTL = '[...WorkspaceDrawings.instances][0]';
  const drawn = await stored(c), frameStart = await c.eval(`${CTL}.state.frame.prims.size`);
  const b = await c.eval(`(() => { const b = [...document.querySelectorAll('#favorite-intervals button')].find(x => x.textContent.trim() === ${JSON.stringify(LABEL[OTHER])}); const r = b.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
  await c.click(b.x, b.y); await c.sleep(4000);
  const switched = { interval: await c.eval(`${CTL}.context().interval`), same: JSON.stringify(await stored(c)) === JSON.stringify(drawn), rendered: await c.eval(`${CTL}.state.frame.prims.size`) };
  await c.shot(`${EV}/all_tools_${LABEL[START]}_then_${LABEL[OTHER]}.png`);
  await c.send('Page.reload'); await waitChart(c);
  const reloaded = { interval: await c.eval(`${CTL}.context().interval`), same: JSON.stringify(await stored(c)) === JSON.stringify(drawn), count: (await stored(c)).length, rendered: await c.eval(`${CTL}.state.frame.prims.size`) };
  console.log(JSON.stringify({ frameStart, switched, reloaded }));
  fs.writeFileSync(`${EV}/live_draw_all_${LABEL[START]}.json`, JSON.stringify({ when: new Date().toISOString(), start: START, geometry: g, results, frameStart, switched, reloaded }, null, 1));
  c.close(); process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
