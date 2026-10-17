// Live check 4: magnet modes cycle from the toolbar and snap a click to a candle's O/H/L/C; the fixed-range
// volume profile uses exact footprint volume-at-price when the footprint overlay has complete bars.
'use strict';
const fs = require('node:fs');
const { open, stored, EV } = require('./live-common.cjs');
(async () => {
  const c = await open({ interval: '15', reset: true });
  const CTL = '[...WorkspaceDrawings.instances][0]', out = { when: new Date().toISOString() };
  const center = async sel => c.eval(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
  const key = async (k, code, modifiers = 0) => { await c.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: k, code, modifiers }); await c.send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code, modifiers }); await c.sleep(120); };
  // magnet: off → weak → strong (real clicks), then Alt+H near a candle high snaps to it exactly
  const modes = [];
  for (let i = 0; i < 2; i += 1) { const b = await center('.dw-toolbar [data-opt="magnet"]'); await c.click(b.x, b.y); await c.sleep(150); modes.push(await c.eval(`${CTL}.state.prefs.magnet`)); }
  const target = await c.eval(`(() => { const v = ${CTL}.view(), i = v.candles.length - 30, bar = v.candles[i], r = document.querySelector('.chart-host').getBoundingClientRect();
    return { x: r.left + v.mapper.xForLogical(i), y: r.top + v.mapper.y(bar.high) - 9, high: String(bar.high), time: bar.time * 1000 }; })()`);
  await key('h', 'KeyH', 1); await c.click(target.x, target.y); await key('Escape', 'Escape');
  const snapped = (await stored(c)).at(-1);
  out.magnet = { modes, target, stored: snapped.points[0], exact: snapped.points[0].price === target.high };
  console.log('magnet', JSON.stringify(out.magnet));
  // footprint on, wait for complete bars, then a VbP over the last 4 closed bars
  const fp = await center('.pane-footprint-toggle'); await c.click(fp.x, fp.y);
  let fpState = null;
  for (let i = 0; i < 40; i += 1) { await c.sleep(1000); fpState = await c.eval(`(() => { const s = ${CTL}.context().footprint; if (!s) return null; const e = [...s.bars.values()]; return { bars: e.length, complete: e.filter(x => x.coverage === 'complete').length }; })()`); if (fpState && fpState.complete >= 6) break; }
  out.footprint = fpState;
  const range = await c.eval(`(() => { const v = ${CTL}.view(), n = v.candles.length, s = ${CTL}.context().footprint;
    const done = i => s && s.bars.get(v.candles[i].time * 1000)?.coverage === 'complete';
    let to = n - 2; while (to > 0 && !done(to)) to -= 1;
    const from = to - 3, r = document.querySelector('.chart-host').getBoundingClientRect();
    return { from, to, allComplete: [from, from + 1, from + 2, to].every(done), x0: r.left + v.mapper.xForLogical(from), x1: r.left + v.mapper.xForLogical(to), y0: r.top + 150, y1: r.top + 350 }; })()`);
  const vbpBtn = await center('.dw-more[data-group="measure"]'); await c.click(vbpBtn.x, vbpBtn.y); await c.sleep(150);
  const item = await center('.dw-flyout-tool[data-tool="vbp"]'); await c.click(item.x, item.y);
  await c.click(range.x0, range.y0); await c.click(range.x1, range.y1); await key('Escape', 'Escape'); await c.sleep(500);
  out.vbp = { range, klineVolumeSum: await c.eval(`(() => { const v = ${CTL}.view(); let s = 0; for (let i = ${range.from}; i <= ${range.to}; i += 1) s += v.candles[i].volume; return s; })()`),
    footprintVolumeSum: await c.eval(`(() => { const v = ${CTL}.view(), st = ${CTL}.context().footprint; let s = 0; for (let i = ${range.from}; i <= ${range.to}; i += 1) { const e = st.bars.get(v.candles[i].time * 1000); for (const cell of e.bar.levels.values()) s += Number((cell.bid + cell.ask) / 10n ** 10n) / 1e8; } return s; })()`), label: await c.eval(`(() => { const ctl = ${CTL}, d = ctl.state.items.at(-1); return (ctl.state.frame.prims.get(ctl.state.items.find(x => x.id === d.id)) || []).find(p => p.t === 'text')?.text; })()`) };
  console.log('vbp', JSON.stringify(out.vbp));
  await c.shot(`${EV}/vbp_footprint.png`);
  fs.writeFileSync(`${EV}/live_magnet_vbp.json`, JSON.stringify(out, null, 1));
  c.close(); process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
