// Live check 1: Binance BTCUSDT 5s on /ui for >= 2 minutes, real network, no mocks.
'use strict';
const { connect } = require('./cd-cdp.cjs');
const fs = require('node:fs');
const EV = '/Users/maxbelov/Projects/wt/chart-drawings/docs/evidence/chart-drawings';
(async () => {
  const c = await connect();
  await c.send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 900, deviceScaleFactor: 1, mobile: false });
  await c.send('Page.navigate', { url: 'http://127.0.0.1:5089/ui' });
  await c.sleep(2500);
  await c.eval(`(() => { const s = JSON.parse(localStorage.getItem('crypto-screener.workspace.v4') || 'null');
    const st = s || {}; localStorage.clear();
    const base = { version: 4, layout: '1', active: 0, panes: [{ symbol: 'BTCUSDT', interval: '5s', exchange: 'binance' }, { symbol: 'ETHUSDT', interval: '240', exchange: 'bybit' }, { symbol: 'SOLUSDT', interval: '240', exchange: 'bybit' }, { symbol: '1000BONKUSDT', interval: 'D', exchange: 'bybit' }, { symbol: 'XRPUSDT', interval: '60', exchange: 'bybit' }, { symbol: 'DOGEUSDT', interval: '60', exchange: 'bybit' }] };
    localStorage.setItem('crypto-screener.workspace.v4', JSON.stringify({ ...st, ...base })); return true; })()`);
  await c.send('Page.reload', { ignoreCache: true });
  await c.sleep(1500);
  // Independent probe on the same code path (real hub + real /api/seconds) for exact per-minute volume checks.
  await c.eval(`(() => { window.__probe = WorkspaceSeconds.createController({ exchange: 'binance', symbol: 'BTCUSDT', interval: '5s', hub: WorkspaceMarketStream.createHub(), fetch: (u, o) => fetch(u, o) }).start(); return true; })()`);
  const started = Date.now();
  const samples = [];
  const read = () => c.eval(`(() => { const p = document.querySelector('.chart-pane');
    return { interval: p.dataset.interval, exchange: p.dataset.exchange, state: p.dataset.state, status: p.querySelector('.pane-status').textContent,
      count: p.querySelector('.pane-count').textContent, label: p.querySelector('.pane-closed').textContent, source: p.querySelector('.pane-source').textContent,
      live: p.querySelector('.pane-live-price').textContent, countdown: p.querySelector('.pane-countdown')?.textContent, countdownHidden: p.querySelector('.pane-countdown')?.hidden,
      favorite: [...document.querySelectorAll('#favorite-intervals button')].filter(b => /s$/.test(b.dataset.interval)).map(b => [b.dataset.interval, b.disabled]) }; })()`);
  for (let i = 0; i < Number(process.env.SAMPLES || 14); i += 1) {
    await c.sleep(i === 0 ? 4000 : 10000);
    const s = await read(); s.elapsed_s = Math.round((Date.now() - started) / 1000); samples.push(s);
    console.log(s.elapsed_s, s.state, s.count, '|', s.label, '|', s.countdown, '|', s.status.slice(0, 150));
    if (i === 1 && !process.env.SHOT_OUT) await c.shot(`${EV}/5s_chart_start.png`);
  }
  await c.shot(`${EV}/${process.env.SHOT_OUT || '5s_chart_after_2min.png'}`);
  const probe = await c.eval(`(() => { const p = window.__probe; return { state: p.state, status: p.statusText(), bars: p.aggregator.list().map(b => ({ t: b.t, v: WorkspaceSeconds.fmtUnits(b.volume), n: b.trades, partial: b.partial, origin: b.origin, o: b.open, h: b.high, l: b.low, c: b.close })) }; })()`);
  const groups = new Map();
  for (const b of probe.bars) { const m = Math.floor(b.t / 60000) * 60000; const g = groups.get(m) || { bars: [], partial: false, origins: new Set() }; g.bars.push(b); g.partial ||= b.partial; g.origins.add(b.origin); groups.set(m, g); }
  const nowMs = Date.now();
  const mins = [...groups.keys()].sort((a, b) => a - b).filter(m => m + 60000 <= nowMs - 2000);
  const kl = await (await fetch(`https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1m&startTime=${mins[0]}&endTime=${mins.at(-1)}&limit=100`)).json();
  const K = new Map(kl.map(k => [k[0], k]));
  const dec = s => { const [w, f = ''] = s.split('.'); return BigInt(w) * 10n ** 18n + BigInt((f + '0'.repeat(18)).slice(0, 18)); };
  const rows = mins.slice(1).map(m => { const g = groups.get(m); const sum = g.bars.reduce((a, b) => a + dec(b.v), 0n); const k = K.get(m);
    return { minute_utc: new Date(m).toISOString().slice(11, 19), origins: [...g.origins].join('+'), partial: g.partial, bars_5s: g.bars.length, sum_5s_volume_units1e18: String(sum), kline_volume: k?.[5], equal: Boolean(k) && sum === dec(k[5]),
      ohlc_equal: Boolean(k) && g.bars[0].o === Number(k[1]) && Math.max(...g.bars.map(b => b.h)) === Number(k[2]) && Math.min(...g.bars.map(b => b.l)) === Number(k[3]) && g.bars.at(-1).c === Number(k[4]) }; });
  rows.forEach(r => console.log(r.minute_utc, r.origins, r.partial ? 'PARTIAL' : '', r.kline_volume, r.equal, 'ohlc', r.ohlc_equal));
  fs.writeFileSync(`${EV}/${process.env.PROBE_OUT || 'live_probe_volume_vs_kline.json'}`, JSON.stringify({ probe_status: probe.status, probe_state: probe.state, note: 'first group skipped (starts before complete_from); live = built in browser from WS aggTrades', rows }, null, 1));
  const errors = c.events.filter(e => e.method === 'Runtime.exceptionThrown' || (e.method === 'Log.entryAdded' && e.params.entry.level === 'error')).map(e => JSON.stringify(e.params).slice(0, 300));
  fs.writeFileSync(`${EV}/${process.env.SAMPLES_OUT || 'live_5s_samples.json'}`, JSON.stringify({ started_utc: new Date(started).toISOString(), samples, js_errors: errors }, null, 1));
  console.log('errors', errors);
  c.close();
})().catch(e => { console.error(e); process.exit(1); });
