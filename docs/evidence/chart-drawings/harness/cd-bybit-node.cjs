// Bybit + Hyperliquid live-only seconds candles in Node (same UMD modules, real venue WebSockets).
'use strict';
const sc = require('/Users/maxbelov/Projects/wt/chart-drawings/static/seconds-candles.js');
const ms = require('/Users/maxbelov/Projects/wt/chart-drawings/static/market-stream.js');
const fs = require('node:fs');
(async () => {
  const out = {};
  for (const [exchange, symbol] of [['bybit', 'BTCUSDT'], ['hyperliquid', 'BTC']]) {
    const hub = ms.createHub();
    const ctl = sc.createController({ exchange, symbol, interval: '1s', hub, fetch: null }).start();
    await new Promise(r => setTimeout(r, 60000));
    const v = ctl.view();
    out[exchange] = { status: ctl.statusText(), stream: ctl.state.stream, closed: v.closed.length, partial: v.closed.filter(b => b.partial).length,
      first: v.closed[0], last: v.closed.at(-1), gaps_in_seconds: v.closed.slice(1).filter((b, i) => b.time - v.closed[i].time > 1).length };
    console.log(exchange, JSON.stringify(out[exchange]).slice(0, 500));
    ctl.dispose(); hub.dispose();
  }
  fs.writeFileSync('/Users/maxbelov/Projects/wt/chart-drawings/docs/evidence/chart-drawings/live_only_venues_node.json', JSON.stringify(out, null, 1));
  process.exit(0);
})();
