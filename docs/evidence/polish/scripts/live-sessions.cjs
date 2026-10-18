// Live Market Sessions with DST-aware zones and the 15m opening range (Binance BTCUSDT).
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { open, waitFor, seed } = require('./common.cjs');
const EV = path.join(__dirname, '..');
const ZONES = { s1Tz: 'Europe/London', s2Tz: 'America/New_York', s3Tz: 'Asia/Tokyo', s4Tz: 'Australia/Sydney' };
const CASES = [
  ['15', { ...ZONES, s1Time: '0800-1700', s2Time: '0800-1700', s3Time: '0900-1500', s4Show: true, s4Time: '0700-1600', s1Op: true, s2Op: true, orTargets: true, orBreakout: true, labelDay: true, history: 10 }, 'sessions-dst-15m'],
  ['15', { s1Op: true, orTargets: true, orBreakout: true, labelDay: true }, 'sessions-or-15m-gmt0'],
  ['240', { ...ZONES, s1Show: false, s2Show: false, s3Show: false, s4Show: true, history: 60, labelDay: true }, 'sessions-sydney-dst-4h']
];
(async () => {
  const out = [];
  for (const [interval, settings, tag] of CASES) {
    const c = await open(seed([{ exchange: 'binance', symbol: 'BTCUSDT', interval }], { sessions: true, 'ema-ribbon': false }, { sessions: settings }));
    await waitFor(c, `(() => { const p = window.__workspaceDebug?.panes.get(0); return Boolean(p && p.candles.length && p.groupModels?.sessions); })()`);
    await c.sleep(1500);
    if (tag === 'sessions-or-15m-gmt0') {
      // zoom onto the latest London session so the 2-bar opening range is legible
      await c.eval(`(() => { const p = window.__workspaceDebug.panes.get(0); const m = p.groupModels.sessions.instances.filter(i => i.n === 1 && i.or && i.or.top).at(-1);
        const idx = p.candles.findIndex(x => x.time === m.firstTime); p.chart.timeScale().setVisibleLogicalRange({ from: idx - 12, to: idx + 44 }); return true; })()`);
      await c.sleep(800);
    }
    const data = await c.eval(`(() => { const p = window.__workspaceDebug.panes.get(0), m = p.groupModels.sessions;
      return { interval: '${interval}', tag: '${tag}', firstBar: p.candles[0].time, lastBar: p.candles.at(-1).time, warnings: m.warnings,
        instances: m.instances.map(i => ({ n: i.n, name: i.name, tz: i.timezone, truncated: i.truncated, day: i.day, start: i.start, end: i.end, firstTime: i.firstTime, lastTime: i.lastTime, high: i.high, low: i.low,
          or: i.or && i.or.top !== undefined ? { minutes: i.or.minutes, bars: i.or.bars, lastOrTime: i.or.lastOrTime, top: i.or.top, btm: i.or.btm, targets: i.or.targets, breakouts: i.or.breakouts } : (i.or ? (i.or.pending ? 'pending' : i.or.truncated ? 'truncated' : 'missing') : null) })) }; })()`);
    out.push(data);
    await c.shot(path.join(EV, `${tag}.png`));
    console.log(tag, data.instances.length, 'instances', JSON.stringify(data.warnings));
    c.close();
  }
  fs.writeFileSync(path.join(EV, 'sessions-live.json'), JSON.stringify(out, null, 1));
})().catch(e => { console.error(e); process.exit(1); });
