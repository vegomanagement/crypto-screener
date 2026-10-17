// Cross-document check of the shared levels store: a level added in another same-origin document (iframe, so
// the browser delivers a real `storage` event) appears in the /scalp column without reload, and removing it
// there removes it here. Also checks that /ui still loads cleanly next to the new files.
// Usage: node docs/evidence/scalping-phase2/live_storage.cjs [baseUrl]
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { launch, sleep } = require('../scalping/cdp.cjs');
const BASE = process.argv[2] || 'http://127.0.0.1:5088';

(async () => {
  const b = await launch({ width: 1400, height: 800, scale: 1, port: 9561 });
  const out = { base: BASE, at: new Date().toISOString() };
  try {
    await b.goto(`${BASE}/scalp`);
    await b.waitFor('window.__scalp && __scalp.page.columns[0]?.stream?.sync?.state === "synced"', 60000);
    await b.evaluate(`new Promise(res => { const f = document.createElement('iframe'); f.id = 'other'; f.style.cssText = 'width:10px;height:10px';
      f.onload = () => res(true); f.src = '/static/levels.js?probe'; document.body.append(f); })`);
    // the iframe document is a different Document on the same origin: load levels.js into it and write through it
    await b.evaluate(`new Promise(res => { const d = document.getElementById('other').contentDocument, s = d.createElement('script');
      s.src = '/static/levels.js'; s.onload = () => res(true); d.head.append(s); })`);
    const before = await b.evaluate('__scalp.page.columns[0].levels.length');
    const added = await b.evaluate(`(() => { const w = document.getElementById('other').contentWindow;
      return w.ScreenerLevels.add({ exchange: 'binance', symbol: 'BTCUSDT', price: '12345.6', kind: 'simple', source: 'chart', note: 'from other document' }); })()`);
    await sleep(500);
    const afterAdd = await b.evaluate(`__scalp.page.columns[0].levels.map(l => ({ id: l.id, price: l.price, source: l.source, note: l.note }))`);
    await b.evaluate(`document.getElementById('other').contentWindow.ScreenerLevels.remove(${JSON.stringify(added.id)})`);
    await sleep(500);
    const afterRemove = await b.evaluate('__scalp.page.columns[0].levels.length');
    out.storage = { before, added, afterAdd, seenAfterAdd: afterAdd.some(l => l.id === added.id), afterRemove };
    await b.goto(`${BASE}/ui`);
    await sleep(4000);
    out.ui = { title: await b.evaluate('document.title') };
  } catch (error) { out.error = String(error.stack || error); }
  out.browser = b.report;
  fs.writeFileSync(path.join(__dirname, 'live-storage.json'), JSON.stringify(out, null, 2));
  await b.close();
  console.log(JSON.stringify(out, null, 1));
})();
