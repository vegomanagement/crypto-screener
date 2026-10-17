// Live acceptance checks for /scalp (real public venue data, real headless Chrome, no mocks).
// Usage: node docs/evidence/scalping/live_acceptance.cjs <outDir>   (server on 127.0.0.1:5087)
'use strict';
const fs = require('node:fs');
const { launch, sleep } = require('./cdp.cjs');
const checks = require('./live_checks.cjs');
const out = process.argv[2] || __dirname;
const BASE = 'http://127.0.0.1:5087/scalp';

async function setup(b, columns, cols) {
  await b.goto(BASE);
  await b.evaluate(`(() => { const s = ScalpSettings.defaults(); s.columns = ${columns};
    ${JSON.stringify(cols)}.forEach(([v, sym], k) => { s.cols[k] = ScalpSettings.defaultColumn(v, sym); });
    localStorage.setItem(ScalpSettings.STORAGE_KEY, JSON.stringify(s)); })()`);
  await b.goto(`${BASE}?evidence=1`);
  await b.waitFor(`window.__scalp && __scalp.page.columns.length === ${columns} && __scalp.page.columns.every(c => c.status === 'synced' && c.lastDraw)`, 60000);
}
const columnState = `__scalp.page.columns.map(c => ({ venue: c.cfg.venue, symbol: c.cfg.symbol, status: c.status, tick: c.tick, rowN: c.rowN,
  ladder: c.lastDraw?.ladder, feed: c.lastDraw?.feed, clusters: c.lastDraw?.clusters, trades: c.data?.stats, sync: c.stream?.sync?.stats,
  counters: c.stream?.counters, market: c.market, marketError: c.marketError, header: c.element.querySelector('.dom-market').textContent,
  status_line: c.element.querySelector('.dom-status').textContent }))`;

(async () => {
  const report = { captured_at: new Date().toISOString(), runs: {} };
  for (const [tag, columns, cols, size] of [
    ['1col', 1, [['binance', 'BTCUSDT']], { width: 1280, height: 860 }],
    ['3col', 3, [['binance', 'BTCUSDT'], ['binance', 'ETHUSDT'], ['binance', 'SOLUSDT']], { width: 1680, height: 900 }]
  ]) {
    const b = await launch({ ...size, scale: 2 });
    const run = report.runs[tag] = {};
    try {
      await setup(b, columns, cols);
      run.synced_at = Date.now();
      await sleep(20000); // let feed/clusters/density fill with live data
      run.perf_before_reset = await b.evaluate('__scalp.perfSummary()');
      await b.evaluate('__scalp.page.perf.frames.length = 0');
      run.raf = await checks.rafIntervals(b, 5000);
      run.perf = await b.evaluate('__scalp.perfSummary()');
      run.columns = await b.evaluate(columnState);
      await b.screenshot(`${out}/scalp-${tag}.png`);
      if (tag === '1col') {
        run.book_vs_rest_binance = await checks.binanceBookVsRest(b, 0, 12);
        const wait = run.synced_at + 64000 - Date.now(); if (wait > 0) await sleep(wait); // ≥ 60 s window
        run.feed_vs_rest_binance = await checks.binanceFeedVsRest(b, 0);
        run.ruler = await checks.rulerAndHotkeys(b, 0);
        await b.screenshot(`${out}/scalp-${tag}-ruler-x5.png`);
      }
      run.checked_until = Date.now(); run.report = b.report;
    } catch (error) { run.error = String(error.stack || error); run.report = b.report; }
    await b.close();
  }
  // Bybit: book vs REST by update id, feed vs REST recent trades.
  {
    const b = await launch({ width: 1280, height: 860, scale: 2 });
    const run = report.runs.bybit = {};
    try {
      await setup(b, 1, [['bybit', 'BTCUSDT']]);
      run.synced_at = Date.now();
      await sleep(40000);
      run.book_vs_rest_bybit = await checks.bybitBookVsRest(b, 0, 12);
      run.feed_vs_rest_bybit = await checks.bybitFeedVsRest(b, 0);
      run.columns = await b.evaluate(columnState);
      await b.screenshot(`${out}/scalp-bybit-btcusdt.png`);
      run.checked_until = Date.now(); run.report = b.report;
    } catch (error) { run.error = String(error.stack || error); run.report = b.report; }
    await b.close();
  }
  fs.writeFileSync(`${out}/live-acceptance.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, (k, v) => (k === 'samples' ? undefined : v), 1).slice(0, 12000));
})();
