// Live check of the /ui Analysis dashboard in headless Chrome (CDP 9572, server :5097).
// Usage: node live.cjs <width> <height> <tag> [stage...]   stages: load analyze shots wheel panes layouts
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { connect } = require('./cdp.cjs');
const OUT = __dirname;
const [W, H, TAG] = [Number(process.argv[2] || 1600), Number(process.argv[3] || 1000), process.argv[4] || 'w1600'];
const STAGES = process.argv.slice(5);
const BASE = 'http://127.0.0.1:5097';
const log = {};
const save = () => fs.writeFileSync(path.join(OUT, `live_${TAG}.json`), JSON.stringify(log, null, 1));

(async () => {
  const c = await connect();
  await c.send('Network.enable'); await c.send('Network.setCacheDisabled', { cacheDisabled: true });
  await c.send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  const state = { version: 5, layout: '2cols', active: 0, sidebar: true, fvg: true, auto: false,
    panes: [{ exchange: 'binance', symbol: 'BTCUSDT', interval: '15' }, { exchange: 'bybit', symbol: 'ETHUSDT', interval: '60' },
      { exchange: 'bybit', symbol: 'SOLUSDT', interval: '240' }, { exchange: 'bybit', symbol: '1000BONKUSDT', interval: 'D' },
      { exchange: 'bybit', symbol: 'BNBUSDT', interval: '60' }, { exchange: 'bybit', symbol: 'XRPUSDT', interval: '60' }] };
  const go = async () => {
    await c.send('Page.navigate', { url: `${BASE}/ui` }); await c.sleep(1500);
    await c.eval(`localStorage.setItem('crypto-screener.workspace.v5', ${JSON.stringify(JSON.stringify(state))}); true`);
    await c.send('Page.navigate', { url: `${BASE}/ui` }); await c.sleep(6000);
  };
  const scrollY = () => c.eval('Math.round(window.scrollY)');
  const fullShot = async name => {
    const m = await c.send('Page.getLayoutMetrics');
    const h = Math.ceil(m.cssContentSize.height);
    const r = await c.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: 0, width: W, height: h, scale: 1 } });
    const file = path.join(OUT, `${TAG}_${name}.png`); fs.writeFileSync(file, Buffer.from(r.data, 'base64')); return { file: path.basename(file), height: h };
  };
  const sectionShot = async (sel, name) => {
    const box = await c.eval(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return null; const r = e.getBoundingClientRect(); return { x: 0, y: Math.round(r.top + scrollY), w: ${W}, h: Math.round(r.height) + 16 }; })()`);
    if (!box) return null;
    const r = await c.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: Math.max(0, box.y - 8), width: box.w, height: box.h, scale: 1 } });
    const file = path.join(OUT, `${TAG}_${name}.png`); fs.writeFileSync(file, Buffer.from(r.data, 'base64')); return path.basename(file);
  };
  const analyze = async (paneIndex, label) => {
    await c.eval(`(() => { const p = document.querySelectorAll('.chart-pane')[${paneIndex}]; p.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })); p.click(); return true; })()`);
    await c.sleep(800);
    const active = await c.eval('JSON.stringify(window.__workspaceDebug.analyst.view().active)');
    const t0 = Date.now();
    await c.eval(`window.__workspaceDebug.analyst.run(true).then(() => true)`);
    const v = await c.eval(`(() => { const v = window.__workspaceDebug.analyst.view(); const r = v.result || {}; return { error: v.error, status: r.status, source: r.source, latency_ms: r.latency_ms, snapshot_ms: r.snapshot_ms, long: r.analysis && r.analysis.long_pct, short: r.analysis && r.analysis.short_pct, bias: r.analysis && r.analysis.bias, factors: r.analysis && r.analysis.factor_scores.length, levels: r.analysis && r.analysis.levels.length, scenarios: r.analysis && r.analysis.scenarios.length, has_snapshot: !!r.snapshot, key: v.resultKey, stale: v.stale }; })()`);
    log[`analyze_${label}`] = { active: JSON.parse(active), wall_ms: Date.now() - t0, ...v };
    save(); return v;
  };
  if (STAGES.includes('load')) { await go(); log.load = { title: await c.eval('document.title'), panes: await c.eval('document.querySelectorAll(".chart-pane").length'), docHeight: await c.eval('document.documentElement.scrollHeight'), viewport: [W, H] }; save(); }
  if (STAGES.includes('analyze')) {
    await analyze(0, 'binance_BTCUSDT_15');
    const r = await c.send('Runtime.evaluate', { expression: 'JSON.stringify(window.__workspaceDebug.analyst.view().result)', returnByValue: true });
    fs.writeFileSync(path.join(OUT, `${TAG}_analysis_binance_BTCUSDT_15.json`), r.result.value);
  }
  if (STAGES.includes('shots')) {
    log.shots = log.shots || {};
    await c.eval(`document.getElementById('analysis-dashboard').scrollIntoView(); true`); await c.sleep(2500);
    log.shots.full = await fullShot('full_btc');
    for (const [sel, n] of [['#ad-verdict, [data-slot=verdict]', 'verdict'], ['[data-slot=summary]', 'summary'], ['[data-slot=levels]', 'levels'], ['[data-slot=factors]', 'factors'], ['[data-slot=metrics]', 'metrics'], ['[data-slot=risks]', 'risks']]) log.shots[n] = await sectionShot(sel, `section_${n}`);
    log.shots.sticky = await c.eval(`(() => { window.scrollTo(0, document.querySelector('[data-slot=metrics]').getBoundingClientRect().top + scrollY); const r = document.querySelector('.ad-sticky').getBoundingClientRect(); return { top: Math.round(r.top), text: document.querySelector('.ad-sticky').innerText }; })()`);
    await c.sleep(300); await c.shot(path.join(OUT, `${TAG}_viewport_sticky.png`));
    await c.eval('window.scrollTo(0, 0); true'); await c.sleep(500); await c.shot(path.join(OUT, `${TAG}_viewport_top.png`));
    log.sidebar = await c.eval(`document.getElementById('analyst-panel').innerText`);
    save();
  }
  if (STAGES.includes('wheel')) {
    await c.eval('window.scrollTo(0, 0); true'); await c.sleep(300);
    const box = await c.eval(`(() => { const r = document.querySelectorAll('.chart-stage')[0].getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; })()`);
    const range = () => c.eval(`(() => { const r = window.__workspaceDebug.panes.get(0).chart.timeScale().getVisibleLogicalRange(); return r ? +(r.to - r.from).toFixed(2) : null; })()`);
    const before = { scrollY: await scrollY(), bars: await range() };
    for (let i = 0; i < 5; i += 1) { await c.send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: box.x, y: box.y, deltaX: 0, deltaY: 120 }); await c.sleep(120); }
    await c.sleep(400);
    const afterChart = { scrollY: await scrollY(), bars: await range() };
    const outside = await c.eval(`(() => { const r = document.querySelector('.workspace-footer').getBoundingClientRect(); return { x: Math.round(r.left + 200), y: Math.round(r.top + r.height / 2) }; })()`);
    for (let i = 0; i < 3; i += 1) { await c.send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: outside.x, y: outside.y, deltaX: 0, deltaY: 200 }); await c.sleep(150); }
    await c.sleep(500);
    const afterOutside = { scrollY: await scrollY() };
    await c.eval('window.scrollTo(0, 0); true'); await c.sleep(300);
    const axis = await c.eval(`(() => { const s = document.querySelectorAll('.chart-stage')[0].getBoundingClientRect(); return { x: Math.round(s.right - 25), y: Math.round(s.top + s.height / 2) }; })()`);
    for (let i = 0; i < 3; i += 1) { await c.send('Input.dispatchMouseEvent', { type: 'mouseWheel', x: axis.x, y: axis.y, deltaX: 0, deltaY: 120 }); await c.sleep(120); }
    await c.sleep(300);
    log.wheel = { chart_point: box, before, afterChart, footer_point: outside, afterOutside, price_axis_point: axis, afterPriceAxis: { scrollY: await scrollY() } };
    save();
  }
  if (STAGES.includes('panes')) {
    await c.eval(`document.getElementById('analysis-dashboard').scrollIntoView(); true`); await c.sleep(800);
    await c.eval(`(() => { const p = document.querySelectorAll('.chart-pane')[1]; p.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })); p.click(); return true; })()`);
    await c.sleep(2500);
    log.pane_switch = await c.eval(`(() => ({ header: document.querySelector('.ad-sticky').innerText, stale_note: (document.querySelector('.ad-stale-note') || {}).innerText || null, verdict_stale: !!document.querySelector('.ad-verdict.is-stale'), sidebar_status: document.querySelector('#analyst-panel [data-role=status]').innerText }))()`);
    log.shots_stale = await sectionShot('[data-slot=verdict]', 'stale_verdict_eth');
    const v = await analyze(1, 'bybit_ETHUSDT_60');
    const r = await c.send('Runtime.evaluate', { expression: 'JSON.stringify(window.__workspaceDebug.analyst.view().result)', returnByValue: true });
    fs.writeFileSync(path.join(OUT, `${TAG}_analysis_bybit_ETHUSDT_60.json`), r.result.value);
    await c.eval(`document.getElementById('analysis-dashboard').scrollIntoView(); true`); await c.sleep(4000);
    log.after_eth = { result: v, header: await c.eval(`document.querySelector('.ad-sticky').innerText`) };
    log.shots_eth = await fullShot('full_eth');
    for (const n of ['verdict', 'levels', 'factors', 'metrics']) log[`shot_eth_${n}`] = await sectionShot(`[data-slot=${n}]`, `eth_section_${n}`);
    save();
  }
  if (STAGES.includes('layouts')) {
    const metrics = async () => { await c.send('HeapProfiler.collectGarbage'); await c.sleep(300);
      const h = await c.send('Runtime.getHeapUsage');
      const dom = await c.send('Memory.getDOMCounters').catch(() => null);
      const extra = await c.eval(`(() => { const d = window.__workspaceDebug; return { panes: d.panes.size, analystSubscribers: d.analyst.listenerCount(), dashboard: d.dashboard.debug() }; })()`);
      return { heapUsedMB: +(h.usedSize / 1048576).toFixed(2), dom, ...extra }; };
    await c.send('HeapProfiler.enable');
    const layouts = ['1', '2cols', '2rows', '3cols', '4grid', '6grid'];
    const samples = [{ i: 0, ...(await metrics()) }];
    const listenerCount = async () => { const doc = await c.send('Runtime.evaluate', { expression: 'document' }); const win = await c.send('Runtime.evaluate', { expression: 'window' });
      const a = await c.send('DOMDebugger.getEventListeners', { objectId: doc.result.objectId }); const b = await c.send('DOMDebugger.getEventListeners', { objectId: win.result.objectId }); return { document: a.listeners.length, window: b.listeners.length }; };
    samples[0].listeners = await listenerCount();
    for (let i = 1; i <= 20; i += 1) {
      const lay = layouts[i % layouts.length];
      await c.eval(`(() => { const s = document.getElementById('layout-select'); s.value = ${JSON.stringify(lay)}; s.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`);
      await c.sleep(1500);
      samples.push({ i, layout: lay, ...(await metrics()), listeners: await listenerCount() });
    }
    log.layouts = samples; save();
  }
  c.close();
  console.log(JSON.stringify(log, null, 1).slice(0, 6000));
})().catch(e => { console.error(e); process.exit(1); });
