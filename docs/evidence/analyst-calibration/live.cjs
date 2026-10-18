// Live check of the calibrated split + EMA ribbon periods in headless Chrome (CDP 9573, server :5098).
// Usage: node live.cjs analyze | ribbon       Results → live_*.json and *.png in this folder.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { connect } = require('./cdp.cjs');
const OUT = __dirname, BASE = 'http://127.0.0.1:5098', W = 1600, H = 1000;
const TARGETS = [['binance', 'BTCUSDT', '15'], ['binance', 'BTCUSDT', '60'], ['binance', 'ETHUSDT', '15'],
  ['bybit', 'SOLUSDT', '15'], ['hyperliquid', 'BTC', '15'], ['bybit', 'ETHUSDT', '60']];
const STAGE = process.argv[2] || 'analyze';
const logFile = path.join(OUT, `live_${STAGE}.json`);
const log = { started: new Date().toISOString(), runs: [] };
const save = () => fs.writeFileSync(logFile, JSON.stringify(log, null, 1));

(async () => {
  const c = await connect();
  await c.send('Network.enable'); await c.send('Network.setCacheDisabled', { cacheDisabled: true });
  await c.send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  const open = async (pane, ribbon) => {
    await c.send('Page.navigate', { url: `${BASE}/ui` }); await c.sleep(1500);
    const state = { version: 5, layout: '1', active: 0, sidebar: true, fvg: true, auto: false, panes: [pane],
      ...(ribbon ? { indicatorSettings: { 'ema-ribbon': ribbon } } : {}) };
    await c.eval(`localStorage.clear(); localStorage.setItem('crypto-screener.workspace.v5', ${JSON.stringify(JSON.stringify(state))}); true`);
    await c.send('Page.navigate', { url: `${BASE}/ui` }); await c.sleep(7000);
  };
  const chartRibbon = () => c.eval(`(() => { const s = document.querySelector('[data-indicator-chip="ema-ribbon"] span'); const st = window.__workspaceDebug.getIndicatorSettings('ema-ribbon'); return { chip: s && s.textContent, periods: [st.period1, st.period2, st.period3] }; })()`);
  const run = async tag => {
    const t0 = Date.now();
    await c.eval('window.__workspaceDebug.analyst.run(true).then(() => true)');
    await c.eval(`document.getElementById('analysis-dashboard').scrollIntoView(); true`); await c.sleep(2500);
    const v = await c.eval(`(() => {
      const v = window.__workspaceDebug.analyst.view(), r = v.result || {}, a = r.analysis || {}, sp = a.split || {};
      const ind = (r.snapshot || {}).indicators || {};
      const rows = [...document.querySelectorAll('#analysis-dashboard *')].filter(e => e.children.length === 0 && /^EMA ribbon/.test(e.textContent.trim()));
      const sentence = document.querySelector('#analysis-dashboard .ad-sentence, #analysis-dashboard [data-role=sentence]');
      const maths = document.querySelector('#analysis-dashboard .ad-maths code');
      return { key: v.resultKey, stale: v.stale, error: v.error, status: r.status, source: r.source, latency_ms: r.latency_ms,
        bias: a.bias, agent_bias: a.agent_bias, long_pct: a.long_pct, short_pct: a.short_pct,
        split: sp, n_factors: (a.factor_scores || []).length,
        snapshot_ema: { periods: ind.ema_periods, fast: ind.ema_fast, mid: ind.ema_mid, slow: ind.ema_slow, stack: ind.ema_stack },
        trend_label: rows.map(e => e.textContent.trim()), maths: maths && maths.textContent,
        verdict_text: (document.querySelector('[data-slot=verdict]') || {}).innerText };
    })()`);
    const a = v.split.agent_net, clear = typeof a === 'number' && Math.abs(a) >= 0.15;
    v.check = { tally_clear: clear, tally_sign: Math.sign(a || 0), split_sign: v.bias === 'long' ? 1 : v.bias === 'short' ? -1 : 0,
      agrees: !clear || (v.bias === 'long' ? 1 : v.bias === 'short' ? -1 : 0) === Math.sign(a) };
    v.chart_ribbon = await chartRibbon(); v.wall_ms = Date.now() - t0;
    // The chart's own ribbon values at the analysed bar (same candles the pane draws).
    v.chart_values_at_bar = await c.eval(`(() => {
      const r = window.__workspaceDebug.analyst.view().result, last = r.snapshot.candles.recent.slice(-1)[0][0];
      const pane = window.__workspaceDebug.panes.get(0), dbg = window.__workspaceDebug;
      const model = dbg.INDICATOR_BY_ID.get('ema-ribbon').compute(pane.candles, dbg.getIndicatorSettings('ema-ribbon'));
      return model.lines.map(l => { const p = l.data.find(d => d.time * 1000 === last || d.time === last); return { period: l.period, value: p ? p.value : null }; });
    })()`);
    for (const [sel, n] of [['[data-slot=verdict]', 'verdict'], ['[data-slot=metrics]', 'metrics'], ['[data-slot=factors]', 'factors']]) {
      const box = await c.eval(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); const r = e.getBoundingClientRect(); return { y: Math.round(r.top + window.scrollY), h: Math.round(r.height) + 16 }; })()`);
      const shot = await c.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip: { x: 0, y: Math.max(0, box.y - 8), width: W, height: Math.min(box.h, 1400), scale: 1 } });
      fs.writeFileSync(path.join(OUT, `${tag}_${n}.png`), Buffer.from(shot.data, 'base64'));
    }
    log.runs.push({ tag, ...v }); save();
    console.log(tag, v.status, `L${v.split.agent_long_points}/S${v.split.agent_short_points}`, 'a', a, 'b', v.split.baseline_net,
      'raw', v.split.baseline_raw, '→', v.bias, `${v.long_pct}/${v.short_pct}`, 'conflict', v.split.conflict, 'guard', v.split.flip_guard,
      '| agrees', v.check.agrees, '| trend', v.trend_label.join(','), '| chart', v.chart_ribbon.chip);
  };
  if (STAGE === 'analyze') {
    for (const [exchange, symbol, interval] of TARGETS) {
      await open({ exchange, symbol, interval });
      await run(`${exchange}_${symbol}_${interval}`);
    }
  }
  if (STAGE === 'ribbon') {
    await open({ exchange: 'binance', symbol: 'BTCUSDT', interval: '15' });
    log.before = await chartRibbon();
    await run('ribbon_default');
    // Change the ribbon through the real settings popover (⚙ → periods → Apply).
    await c.eval(`document.querySelector('.indicator-settings-btn[data-indicator="ema-ribbon"]').click(); true`); await c.sleep(400);
    await c.eval(`(() => { const set = (k, v) => { const i = document.querySelector('.indicator-settings-input[data-key="' + k + '"]'); i.value = String(v); i.dispatchEvent(new Event('input', { bubbles: true })); };
      set('period1', 9); set('period2', 21); set('period3', 55); document.querySelector('.indicator-settings-apply').click(); return true; })()`);
    await c.sleep(1500);
    log.after_change = { ribbon: await chartRibbon(), view: await c.eval(`(() => { const v = window.__workspaceDebug.analyst.view(); return { stale: v.stale, resultKey: v.resultKey, activeKey: v.activeKey }; })()`),
      trend_while_stale: await c.eval(`[...document.querySelectorAll('#analysis-dashboard *')].filter(e => e.children.length === 0 && /^EMA ribbon/.test(e.textContent.trim())).map(e => e.closest('[title]') ? e.closest('[title]').title : e.textContent)`) };
    await c.eval(`window.scrollTo(0, 0); true`); await c.sleep(500);
    await c.shot(path.join(OUT, 'ribbon_9_21_55_chart.png'));
    save();
    await run('ribbon_9_21_55');
  }
  log.finished = new Date().toISOString(); save(); c.close(); process.exit(0);
})().catch(err => { console.error(err); log.error = String(err); save(); process.exit(1); });
