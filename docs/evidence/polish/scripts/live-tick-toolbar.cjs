// Live: position tool ticks use the venue tick (Binance/Bybit BTCUSDT 0.1, Hyperliquid BTC rule),
// and each pane has exactly one vertical drawing toolbar + one favourites strip, no old horizontal toolbar.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { open, waitFor, seed } = require('./common.cjs');
const EV = path.join(__dirname, '..');
(async () => {
  const results = {};
  const venues = [['binance', 'BTCUSDT'], ['bybit', 'BTCUSDT'], ['hyperliquid', 'BTC']];
  for (const [exchange, symbol] of venues) {
    const c = await open(seed([{ exchange, symbol, interval: '15' }]));
    await waitFor(c, `(() => { const p = window.__workspaceDebug?.panes.get(0); return Boolean(p && p.candles.length && p.instrument && p.drawings); })()`);
    await c.sleep(1500);
    const r = await c.eval(`(() => { const p = window.__workspaceDebug.panes.get(0), ctx = p.drawingContext(), last = p.candles.at(-1);
      const ctl = p.drawings, v = ctl.view();
      // Long from the last close: target +200 ticks, stop −100 ticks of the venue tick.
      const t = ctx.tickSize, e = last.close, dec = Math.max(0, -Math.floor(Math.log10(t)));
      const pts = [e, e + 200 * t, e - 100 * t].map(price => ({ time: p.candles.at(-40).time * 1000, price: price.toFixed(dec) }));
      ctl.actions.clearAll?.();
      ctl.actions.addDrawing('long', pts);
      p.scheduleDraw();
      return { instrument: p.instrument, tickSize: ctx.tickSize, tickSource: ctx.tickSource, precision: ctx.precision, viewTick: v.tickSize, points: pts.map(q => q.price) }; })()`);
    await c.sleep(1200);
    r.labels = await c.eval(`(() => { const p = window.__workspaceDebug.panes.get(0), ctl = p.drawings; const f = ctl.state.frame; const out = [];
      f.prims.forEach((list, d) => { if (d.type === 'long') list.filter(q => q.t === 'text').forEach(q => out.push(q.text)); }); return out; })()`);
    r.toolbars = await c.eval(`(() => [...document.querySelectorAll('.chart-pane')].map(pane => ({
      dwToolbar: pane.querySelectorAll('.dw-toolbar').length, favs: pane.querySelectorAll('.dw-favs').length,
      oldDrawToolbar: pane.querySelectorAll('.draw-toolbar').length,
      toolbarRect: (() => { const b = pane.querySelector('.dw-toolbar')?.getBoundingClientRect(); return b && { left: b.left, top: b.top, width: b.width, height: b.height }; })(),
      paneLeft: pane.getBoundingClientRect().left })))()`);
    results[`${exchange}:${symbol}`] = r;
    await c.shot(path.join(EV, `position-tick-${exchange}.png`));
    console.log(exchange, JSON.stringify({ tick: r.tickSize, src: r.tickSource, labels: r.labels.slice(0, 3), toolbars: r.toolbars }));
    await c.eval(`window.__workspaceDebug.panes.get(0).drawings.actions.clearAll?.(); true`);
    c.close();
  }
  // Four-pane layout: one toolbar per pane.
  const c = await open(seed([{ exchange: 'binance', symbol: 'BTCUSDT', interval: '15' }, { exchange: 'bybit', symbol: 'BTCUSDT', interval: '60' }, { exchange: 'hyperliquid', symbol: 'BTC', interval: '15' }, { exchange: 'binance', symbol: 'ETHUSDT', interval: '240' }], {}, {}, '4'));
  await waitFor(c, `(() => { const d = window.__workspaceDebug; return d && [0,1,2,3].every(i => d.panes.get(i)?.candles.length); })()`);
  await c.sleep(2000);
  results.layout4 = await c.eval(`(() => [...document.querySelectorAll('.chart-pane')].map(pane => ({ dwToolbar: pane.querySelectorAll('.dw-toolbar').length, favs: pane.querySelectorAll('.dw-favs').length, oldDrawToolbar: pane.querySelectorAll('.draw-toolbar').length,
    vertical: (() => { const b = pane.querySelector('.dw-toolbar')?.getBoundingClientRect(); return b ? b.height > b.width : null; })() })))()`);
  await c.shot(path.join(EV, 'toolbar-layout4.png'));
  console.log('layout4', JSON.stringify(results.layout4));
  c.close();
  fs.writeFileSync(path.join(EV, 'live-tick-toolbar.json'), JSON.stringify(results, null, 1));
})().catch(e => { console.error(e); process.exit(1); });
