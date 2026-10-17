// Capture Key levels / Opens and Market Sessions models live on /ui for Bybit and Hyperliquid panes.
// Usage: node live-venues.cjs  → ../venues-live-models.json + screenshots
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { open, waitFor, seed } = require('./common.cjs');
const EV = path.join(__dirname, '..');
const CASES = [['bybit', 'BTCUSDT', '15'], ['bybit', 'BTCUSDT', '60'], ['hyperliquid', 'BTC', '15'], ['binance', 'BTCUSDT', '15']];
const SETTINGS = { sessions: { s1Op: true, orTargets: true, orBreakout: true, labelDay: true }, opens: { labelPrice: true, o4Show: true } };
(async () => {
  const results = [];
  for (const [exchange, symbol, interval] of CASES) {
    const c = await open(seed([{ exchange, symbol, interval }], { opens: true, sessions: true }, SETTINGS));
    const ready = await waitFor(c, `(() => { const p = window.__workspaceDebug?.panes.get(0); return Boolean(p && p.candles.length && p.groupModels?.opens && p.groupModels?.sessions); })()`);
    await c.sleep(2000);
    const data = await c.eval(`(() => { const p = window.__workspaceDebug.panes.get(0), m = p.groupModels;
      return { exchange: '${exchange}', symbol: '${symbol}', interval: '${interval}', bars: p.candles.length, firstBar: p.candles[0].time, lastBar: p.candles.at(-1).time,
        opens: m.opens.levels.map(l => ({ kind: l.kind, key: l.key || l.tf, name: l.name, price: l.price, startTime: l.startTime, current: l.current, source: l.source || null, sourceTime: l.sourceTime || null })),
        htf: Object.fromEntries(['D', 'W', 'M'].map(tf => [tf, (p.htf?.[tf] || []).slice(-3).map(x => [x.time, x.open, x.closed])])),
        htfErrors: p.htf?.errors, opensWarnings: m.opens.warnings,
        sessions: m.sessions.instances.map(i => ({ n: i.n, name: i.name, start: i.start, end: i.end, firstTime: i.firstTime, lastTime: i.lastTime, high: i.high, low: i.low, ended: i.ended,
          or: i.or && !i.or.pending && !i.or.missing && !i.or.truncated ? { top: i.or.top, btm: i.or.btm, bars: i.or.bars, lastOrTime: i.or.lastOrTime, minutes: i.or.minutes, targets: i.or.targets, breakouts: i.or.breakouts } : (i.or ? (i.or.pending ? 'pending' : i.or.truncated ? 'truncated' : 'missing') : null) })),
        warnings: document.querySelector('.pane-plugin-warnings')?.textContent || '' }; })()`);
    data.ready = ready;
    results.push(data);
    await c.shot(path.join(EV, `venue-${exchange}-${symbol}-${interval}m.png`));
    console.log(exchange, symbol, interval, 'ready', ready, 'bars', data.bars, 'opens', data.opens.length, 'sessions', data.sessions.length, 'htfErr', JSON.stringify(data.htfErrors), 'warn', data.warnings.slice(0, 200));
    c.close();
  }
  fs.writeFileSync(path.join(EV, 'venues-live-models.json'), JSON.stringify(results, null, 1));
})().catch(e => { console.error(e); process.exit(1); });
