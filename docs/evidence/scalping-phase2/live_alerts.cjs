// Live check of /scalp signal levels and alerts on Binance BTCUSDT (real venue data, real Chrome, real UI input).
// Usage: node docs/evidence/scalping-phase2/live_alerts.cjs [baseUrl]   (server must be running)
// Writes live-alerts.json + screenshots next to this file. Nothing is simulated: the level is set by a real
// Shift+Alt+click, triggered by a real Binance aggTrade, and the trade is re-checked against Binance REST.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { launch, sleep } = require('../scalping/cdp.cjs');

const BASE = process.argv[2] || 'http://127.0.0.1:5088';
const OUT = __dirname;
const shot = name => path.join(OUT, name);

async function click(b, x, y, { modifiers = 0, clickCount = 1 } = {}) {
  for (const type of ['mousePressed', 'mouseReleased']) await b.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount, modifiers });
}
/** Viewport coordinates of the ladder row containing tick index `idx` in column 0. */
const rowXY = (b, idx) => b.evaluate(`(() => { const c = __scalp.page.columns[0], r = c.canvas.getBoundingClientRect();
  const L = ScalpRender.layout(r.width, { clusterCount: c.cfg.clusters.count });
  const row = Math.floor(${idx} / c.rowN), y = ScalpRender.rowCenterY(c.view, row, r.height);
  return { x: r.left + L.price.x + L.price.w / 2, y: r.top + y, inView: y > 0 && y < r.height }; })()`);
const bookTop = b => b.evaluate(`(() => { const c = __scalp.page.columns[0], s = c.stream.sync, best = s.book.best();
  return { state: s.state, bid: best.bid, ask: best.ask, tick: c.tick, tickStr: c.tickStr, rowN: c.rowN, last: c.lastTradePrice }; })()`);

(async () => {
  const b = await launch({ width: 1600, height: 900, scale: 1, port: 9561 });
  const result = { base: BASE, startedAt: new Date().toISOString(), steps: {} };
  try {
    await b.send('Browser.grantPermissions', { origin: BASE, permissions: ['notifications'] }).catch(e => { result.steps.grant = String(e.message); });
    await b.goto(`${BASE}/scalp`);
    await b.waitFor('window.__scalp && __scalp.page.columns[0]?.stream?.sync?.state === "synced" && __scalp.page.columns[0].lastTradePrice', 60000);
    await b.evaluate('__scalp.page.columns[0].follow = false, true'); // freeze the view so the click lands on the computed row
    // open the drawer and enable browser notifications (permission requested from this click only)
    const tg = await b.evaluate('(() => { const r = document.querySelector("#scalp-alerts-toggle").getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()');
    await click(b, tg.x, tg.y);
    await sleep(300);
    const nb = await b.evaluate('(() => { const r = document.querySelector("[data-act=notify]").getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()');
    await click(b, nb.x, nb.y);
    await sleep(500);
    result.steps.notifications = await b.evaluate('({ permission: Notification.permission, prefNotify: __scalp.alerts.prefs.notify, audio: __scalp.alerts.audioState() })');

    // signal level 1 tick above the best ask, via a real Shift+Alt+click
    await sleep(300);
    let top = await bookTop(b);
    let pos = await rowXY(b, top.ask + 1);
    await click(b, pos.x, pos.y, { modifiers: 1 | 8 });
    await sleep(300);
    const lvAbove = await b.evaluate('ScreenerLevels.list({ exchange: "binance", symbol: "BTCUSDT", kind: "signal" })');
    result.steps.signalAbove = { bookAtClick: top, click: pos, levels: lvAbove,
      expectedPrice: (await b.evaluate(`ScreenerLevels.fromTicks(${top.ask + 1}, ${JSON.stringify(top.tickStr)})`)) };

    // a repeat signal 1 tick below the best bid: Shift+Alt+click, then double-click → editor → Repeat checkbox
    top = await bookTop(b);
    pos = await rowXY(b, top.bid - 1);
    await click(b, pos.x, pos.y, { modifiers: 1 | 8 });
    await sleep(300);
    await click(b, pos.x - 120, pos.y, { clickCount: 1 }); await click(b, pos.x - 120, pos.y, { clickCount: 2 });
    await sleep(300);
    const editorOpen = await b.evaluate('Boolean(document.querySelector(".level-editor"))');
    if (editorOpen) {
      await b.evaluate('(() => { const f = document.querySelector(".level-editor"); f.repeat.click(); f.note.value = "live repeat"; f.note.dispatchEvent(new Event("change", { bubbles: true })); })()');
    }
    await b.evaluate('document.querySelector(".level-editor [data-act=close]")?.click(), true');
    // a simple level 5 ticks below the bid (Alt+click), drawn but never alerting
    pos = await rowXY(b, top.bid - 5);
    await click(b, pos.x, pos.y, { modifiers: 1 });
    await sleep(300);
    result.steps.editor = { opened: editorOpen, levels: await b.evaluate('ScreenerLevels.list({ exchange: "binance", symbol: "BTCUSDT" })') };
    await b.screenshot(shot('scalp-levels-armed.png'));

    // wait for the real cross
    const t0 = Date.now();
    const aboveId = result.steps.signalAbove.levels[0]?.id;
    // the one-shot level set 1 tick above the ask is the acceptance target; the repeat level below may fire first
    await b.waitFor(`__scalp.alerts.log.list().some(e => e.levelId === ${JSON.stringify(aboveId)})`, 15 * 60000);
    await sleep(400);
    await b.screenshot(shot('scalp-signal-blink.png'));
    result.steps.trigger = await b.evaluate(`({ waitedMs: ${Date.now() - t0}, stats: __scalp.alerts.stats, log: __scalp.alerts.log.list(),
      levels: ScreenerLevels.list({ exchange: 'binance', symbol: 'BTCUSDT' }), headClass: document.querySelector('.dom-head').className })`);
    await sleep(5200);
    result.steps.after5s = await b.evaluate('({ headClass: document.querySelector(".dom-head").className })');
    await b.screenshot(shot('scalp-signal-hold-panel.png'));

    // independent check: the triggering trade exists on Binance REST with the same id and price, and it crosses the level
    const fired = result.steps.trigger.log.find(e => e.levelId === aboveId);
    const sig = { price: fired.price, tradePrice: fired.tradePrice, prevPrice: fired.prevPrice, tradeId: fired.tradeId, tradeTime: fired.tradeTime };
    result.steps.aboveLevelAfter = result.steps.trigger.levels.find(l => l.id === aboveId);
    const rest = await (await fetch(`https://fapi.binance.com/fapi/v1/aggTrades?symbol=BTCUSDT&fromId=${sig.tradeId - 1}&limit=2`)).json();
    const restTrade = rest.find(t => t.a === sig.tradeId), restPrev = rest.find(t => t.a === sig.tradeId - 1);
    result.steps.restCheck = { tradeId: sig.tradeId, level: sig.price, uiTrade: sig.tradePrice, uiPrev: sig.prevPrice, rest: rest.map(t => ({ a: t.a, p: t.p, q: t.q, T: t.T, m: t.m })),
      samePrice: restTrade?.p === sig.tradePrice, prevMatches: restPrev ? restPrev.p === sig.prevPrice : null,
      crossedPerRest: restTrade && restPrev ? await b.evaluate(`ScreenerLevels.crossed({ price: ${JSON.stringify(sig.price)} }, ${JSON.stringify(restPrev.p)}, ${JSON.stringify(restTrade.p)})`) : null };

    // header click clears the highlight; panel row click centres the column on the price
    const head = await b.evaluate('(() => { const r = document.querySelector(".dom-scale").getBoundingClientRect(); return { x: r.x + 2, y: r.y + r.height / 2 }; })()');
    await click(b, head.x, head.y);
    const row = await b.evaluate('(() => { const r = document.querySelector("#scalp-alerts tbody tr[data-id] td:nth-child(3)").getBoundingClientRect(); return { x: r.x + 4, y: r.y + r.height / 2 }; })()');
    await click(b, row.x, row.y);
    await sleep(300);
    result.steps.panelClick = await b.evaluate(`(() => { const c = __scalp.page.columns[0];
      return { headClass: document.querySelector('.dom-head').className, centerRow: c.view.centerRow, follow: c.follow,
        expectedRow: Math.floor(Number(${JSON.stringify(sig.price)}) / (c.rowN * c.tick) + 1e-6), msg: document.querySelector('.al-msg').textContent }; })()`);

    // volume alerts on the same live column (thresholds lowered so a real event is likely within the window)
    await b.evaluate(`(() => { const c = __scalp.page.columns[0]; __scalp.page.updateColumn(0, { alerts: { ...c.cfg.alerts, print: true, printUsd: 100000,
      appear: true, appearUsd: 500000, withinPct: 0.2, decrease: true, decreasePct: 60 } }, 'redraw'); return true; })()`);
    const v0 = Date.now();
    await sleep(90000);
    result.steps.volume = await b.evaluate(`(() => { const c = __scalp.page.columns[0];
      return { windowMs: ${90000}, cfg: c.cfg.alerts, detector: c.volAlerts.stats, tracked: c.volAlerts.tracked.size,
        entries: __scalp.alerts.log.list().filter(e => e.type !== 'signal' && e.at >= ${v0}) }; })()`);
    const causes = {};
    for (const e of result.steps.volume.entries) { const k = `${e.type}${['decrease', 'gone'].includes(e.type) ? `:${e.detail.split(' · ')[1]}` : ''}`; causes[k] = (causes[k] || 0) + 1; }
    result.steps.volumeSummary = causes;
    // REST re-check of the large-print alerts: each needs a Binance aggTrade at that time and price with p·q ≥ $100k
    const prints = result.steps.volume.entries.filter(e => e.type === 'print' && e.tradeTime).slice(0, 5);
    result.steps.printRestCheck = [];
    for (const e of prints) {
      const rows = await (await fetch(`https://fapi.binance.com/fapi/v1/aggTrades?symbol=BTCUSDT&startTime=${e.tradeTime}&endTime=${e.tradeTime}`)).json();
      const big = rows.filter(t => Number(t.p) * Number(t.q) >= 100000);
      result.steps.printRestCheck.push({ alert: { price: e.price, usd: e.usd, detail: e.detail, tradeTime: e.tradeTime }, restTradesAtMs: rows.length,
        restBigAtMs: big.map(t => ({ a: t.a, p: t.p, q: t.q, usd: Math.round(Number(t.p) * Number(t.q)) })) });
    }
    await b.evaluate('document.querySelector("[data-tab=triggered]").click(), true');
    await b.screenshot(shot('scalp-alerts-triggered-tab.png'));
    await b.evaluate('document.querySelector("[data-tab=active]").click(), true');
    await b.screenshot(shot('scalp-alerts-active-tab.png'));
    result.steps.perf = await b.evaluate('__scalp.perfSummary()');
  } catch (error) {
    result.error = String(error.stack || error);
    try { await b.screenshot(shot('scalp-alerts-error.png')); } catch (_) { /* browser gone */ }
  } finally {
    result.browser = b.report; result.finishedAt = new Date().toISOString();
    fs.writeFileSync(path.join(OUT, 'live-alerts.json'), JSON.stringify(result, null, 2));
    await b.close();
  }
  console.log(JSON.stringify({ error: result.error || null, waited: result.steps.trigger?.waitedMs, rest: result.steps.restCheck, above: result.steps.aboveLevelAfter, volume: result.steps.volumeSummary }, null, 1));
})();
