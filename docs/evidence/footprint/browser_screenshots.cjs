// Real headless Chrome check of /ui with the footprint enabled (live public data, no mocks).
const { chromium } = require('playwright');
const fs = require('fs');
const out = process.argv[2];
const base = 'http://127.0.0.1:5087/ui';

async function run(page, interval, tag, report) {
  const state = { version: 4, layout: '1', active: 0, sidebar: false,
    panes: [{ symbol: 'BTCUSDT', interval, exchange: 'binance', footprint: true }] };
  await page.goto('about:blank');
  await page.goto(base);
  await page.evaluate(s => { localStorage.clear(); localStorage.setItem('crypto-screener.workspace.v4', JSON.stringify(s)); }, state);
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => {
    const pane = window.__workspaceDebug?.panes.get(0);
    return pane && pane.candles.length > 0 && pane.footprint?.status.state === 'ready';
  }, null, { timeout: 90000 });
  // Zoom into the right edge so cells are wide enough for text.
  await page.evaluate(() => {
    const pane = window.__workspaceDebug.panes.get(0);
    const n = pane.candles.length;
    pane.chart.timeScale().applyOptions({ barSpacing: 72, rightOffset: 2 });
    pane.chart.timeScale().setVisibleLogicalRange({ from: n - 14, to: n + 2 });
  });
  await page.waitForTimeout(2500);
  const zoomed = await page.evaluate(() => {
    const pane = window.__workspaceDebug.panes.get(0);
    return { draw: pane.footprintDraw, status: document.querySelector('.pane-footprint-status').textContent,
      barSpacing: pane.chart.timeScale().options().barSpacing, summary: pane.footprint.store.summary(),
      tick: pane.footprint.status.payload.tick_size, group: pane.footprint.status.payload.group_ticks,
      wsLabel: document.querySelector('.pane-closed').textContent };
  });
  await page.screenshot({ path: `${out}/footprint-btcusdt-${tag}.png` });
  await page.evaluate(() => {
    const pane = window.__workspaceDebug.panes.get(0);
    const n = pane.candles.length;
    pane.chart.timeScale().applyOptions({ barSpacing: 5 });
    pane.chart.timeScale().setVisibleLogicalRange({ from: n - 150, to: n + 3 });
  });
  await page.waitForTimeout(1500);
  const zoomedOut = await page.evaluate(() => {
    const pane = window.__workspaceDebug.panes.get(0);
    return { draw: pane.footprintDraw, barSpacing: pane.chart.timeScale().options().barSpacing };
  });
  await page.screenshot({ path: `${out}/footprint-btcusdt-${tag}-zoomed-out.png` });
  report[tag] = { zoomed, zoomedOut };
}

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  const report = { console_errors: [], page_errors: [], failed_requests: [] };
  page.on('console', m => { if (m.type() === 'error') report.console_errors.push(m.text()); });
  page.on('pageerror', e => report.page_errors.push(String(e)));
  page.on('response', r => { if (r.status() >= 400) report.failed_requests.push(`${r.status()} ${r.url()}`); });
  for (const [interval, tag] of [['1', '1m'], ['15', '15m']]) await run(page, interval, tag, report);
  report.captured_at = new Date().toISOString();
  report.user_agent = await page.evaluate(() => navigator.userAgent);
  fs.writeFileSync(`${out}/footprint-browser.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
