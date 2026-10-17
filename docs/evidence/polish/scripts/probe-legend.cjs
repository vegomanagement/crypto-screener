// Reproduce/verify the 'EMA · warming up' legend on a 4h pane with a forming bar.
'use strict';
const fs = require('node:fs');
const { open, waitFor, seed } = require('./common.cjs');
(async () => {
  const out = process.argv[2];
  const cdp = await open(seed([{ exchange: 'binance', symbol: 'BTCUSDT', interval: '240' }], { ema200: true }));
  await waitFor(cdp, `(() => { const p = window.__workspaceDebug?.panes.get(0); return Boolean(p && p.candles.length && p.forming); })()`);
  await cdp.sleep(4000);
  const r = await cdp.eval(`(() => { const p = window.__workspaceDebug.panes.get(0);
    return { bars: p.candles.length, lastClosed: p.candles.at(-1).time, forming: p.forming && { time: p.forming.time, close: p.forming.close },
      emaLastClosed: Object.fromEntries(Object.entries(p.emaValues).map(([k, m]) => [k, [...m.entries()].at(-1)])),
      emaAtForming: Object.fromEntries(Object.entries(p.emaValues).map(([k, m]) => [k, m.get(p.forming?.time) ?? null])),
      legend: [...document.querySelectorAll('[data-ema]')].filter(e => !e.hidden).map(e => e.textContent) }; })()`);
  console.log(JSON.stringify(r, null, 1));
  if (out) { fs.writeFileSync(out, JSON.stringify(r, null, 1)); await cdp.shot(out.replace(/\.json$/, '.png')); }
  cdp.close();
})().catch(e => { console.error(e); process.exit(1); });
