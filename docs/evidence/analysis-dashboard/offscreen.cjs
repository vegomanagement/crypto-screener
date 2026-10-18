// Live: count dashboard positioning requests (server log) for 65 s off-screen vs 65 s on-screen.
'use strict';
const fs = require('node:fs');
const { connect } = require('./cdp.cjs');
const LOG = '/Users/maxbelov/.hermes/cache/scratch/analysis-dashboard-server.log';
const count = () => (fs.readFileSync(LOG, 'utf8').match(/GET \/api\/positioning\?[^"]*owner=analysis-dashboard/g) || []).length;
(async () => {
  const c = await connect();
  await c.eval('window.scrollTo(0, 0); true'); await c.sleep(2000);
  const a0 = count(), d0 = await c.eval('JSON.stringify(window.__workspaceDebug.dashboard.debug())');
  await c.sleep(65000);
  const a1 = count();
  await c.eval(`document.getElementById('analysis-dashboard').scrollIntoView(); true`); await c.sleep(1000);
  const d1 = await c.eval('JSON.stringify(window.__workspaceDebug.dashboard.debug())');
  await c.sleep(64000);
  const a2 = count();
  const out = { offscreen: { requests_65s: a1 - a0, debug: JSON.parse(d0) }, onscreen: { requests_65s: a2 - a1, debug: JSON.parse(d1) } };
  fs.writeFileSync(`${__dirname}/live_offscreen_poll.json`, JSON.stringify(out, null, 1));
  console.log(JSON.stringify(out)); c.close();
})();
