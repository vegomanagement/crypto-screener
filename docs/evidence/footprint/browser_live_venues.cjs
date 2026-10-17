const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  const base = 'http://127.0.0.1:5087/ui';
  const out = {};
  for (const [exchange, symbol] of [['bybit', 'BTCUSDT'], ['hyperliquid', 'BTC']]) {
    await page.goto(base);
    await page.evaluate(s => { localStorage.clear(); localStorage.setItem('crypto-screener.workspace.v4', JSON.stringify(s)); },
      { version: 4, layout: '1', active: 0, panes: [{ symbol, interval: '1', exchange, footprint: true }] });
    await page.goto(base);
    await page.waitForFunction(() => window.__workspaceDebug?.panes.get(0)?.footprint?.status.state === 'ready', null, { timeout: 60000 });
    const t0 = await page.evaluate(() => window.__workspaceDebug.panes.get(0).footprint.store.list().at(-1).bar.tradeCount);
    await page.waitForTimeout(20000);
    out[exchange] = await page.evaluate(t0 => { const p = window.__workspaceDebug.panes.get(0), st = p.footprint.store, last = st.list().at(-1);
      return { summary: st.summary(), formingTradesAtReady: t0, formingTradesAfter20s: last.bar.tradeCount, formingCoverage: last.coverage, draw: p.footprintDraw,
        status: document.querySelector('.pane-footprint-status').textContent }; }, t0);
  }
  out.errors = errors;
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
