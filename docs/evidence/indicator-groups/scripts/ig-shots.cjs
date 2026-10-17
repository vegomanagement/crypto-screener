// Screenshots of the overlay list and each grouped indicator's settings popover.
'use strict';
const path = require('node:path');
const { connect } = require('./cd-cdp.cjs');
const OUT = process.argv[2];
(async () => {
  const cdp = await connect(9564);
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await cdp.send('Page.navigate', { url: 'http://127.0.0.1:5091/ui' });
  for (let i = 0; i < 40; i += 1) { await cdp.sleep(500); if (await cdp.eval('Boolean(window.__workspaceDebug?.panes.get(0)?.candles.length)').catch(() => false)) break; }
  await cdp.sleep(1500);
  const bar = await cdp.eval(`(() => { const r = document.querySelector('.indicator-toolbar').getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; })()`);
  await cdp.shot(path.join(OUT, 'overlay-list.png'), bar);
  console.log('chips', await cdp.eval(`[...document.querySelectorAll('.indicator-toolbar .toggle-chip')].map(l => l.textContent.trim()).join(' | ')`));
  for (const id of ['ema-ribbon', 'ma-channel', 'turtle-zone', 'opens', 'sessions']) {
    await cdp.eval(`document.querySelector('.indicator-settings-btn[data-indicator="${id}"]').click(); true`);
    await cdp.sleep(400);
    const parts = await cdp.eval(`(() => { const f = document.querySelector('.indicator-settings-fields'); return Math.max(1, Math.ceil(f.scrollHeight / f.clientHeight)); })()`);
    for (let part = 0; part < parts; part += 1) {
      await cdp.eval(`(() => { const f = document.querySelector('.indicator-settings-fields'); f.scrollTop = ${part} * f.clientHeight; return true; })()`);
      await cdp.sleep(200);
      const box = await cdp.eval(`(() => { const r = document.querySelector('.indicator-settings-popover').getBoundingClientRect(); return { x: r.x - 4, y: r.y - 4, width: r.width + 8, height: r.height + 8 }; })()`);
      await cdp.shot(path.join(OUT, `settings-${id}${parts > 1 ? `-${part + 1}` : ''}.png`), box);
    }
    const fields = await cdp.eval(`[...document.querySelectorAll('.indicator-settings-group')].map(g => g.textContent).join(', ')`);
    console.log(id, 'pages', parts, 'groups:', fields);
    await cdp.eval(`document.querySelector('.indicator-settings-close').click(); true`);
    await cdp.sleep(200);
  }
  cdp.close();
})().catch(error => { console.error(error); process.exit(1); });
