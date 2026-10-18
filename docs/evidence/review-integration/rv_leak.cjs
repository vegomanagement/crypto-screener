// Leak probe: /ui in headless Chrome (CDP :9568), 30 rounds of layout + symbol switches.
// Prints timer / WebSocket / ResizeObserver / listener / DOM / heap counters per round.
const PORT = 9568, URL_UI = process.env.UI_URL || 'http://127.0.0.1:5095/ui';
const ROUNDS = Number(process.env.ROUNDS || 30);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const INSTR = `(() => {
  const live = { intervals: new Set(), timeouts: new Set(), ws: new Set(), ro: 0, raf: new Set() };
  const si = window.setInterval, ci = window.clearInterval, st = window.setTimeout, ct = window.clearTimeout;
  window.setInterval = function (fn, ms, ...a) { const id = si.call(window, fn, ms, ...a); live.intervals.add(id); return id; };
  window.clearInterval = function (id) { live.intervals.delete(id); return ci.call(window, id); };
  window.setTimeout = function (fn, ms, ...a) { let id; id = st.call(window, function () { live.timeouts.delete(id); return typeof fn === 'function' ? fn.apply(this, arguments) : undefined; }, ms, ...a); live.timeouts.add(id); return id; };
  window.clearTimeout = function (id) { live.timeouts.delete(id); return ct.call(window, id); };
  const W = window.WebSocket;
  window.WebSocket = function (...a) { const w = new W(...a); live.ws.add(w); w.addEventListener('close', () => live.ws.delete(w)); return w; };
  window.WebSocket.prototype = W.prototype; Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  const RO = window.ResizeObserver;
  window.ResizeObserver = class extends RO { constructor(cb) { super(cb); this.__n = 0; }
    observe(...a) { if (!this.__n++) live.ro++; return super.observe(...a); }
    disconnect() { if (this.__n) { live.ro--; this.__n = 0; } return super.disconnect(); } };
  window.__leak = () => ({ intervals: live.intervals.size, timeouts: live.timeouts.size,
    ws: [...live.ws].filter(w => w.readyState < 2).length, ro: live.ro });
})();`;

async function main() {
  const tabs = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
  const page = tabs.find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r));
  let n = 0; const waiting = new Map();
  ws.addEventListener('message', ev => { const m = JSON.parse(ev.data); if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); } });
  const cdp = (method, params = {}) => new Promise(r => { const id = ++n; waiting.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
  const ev = async expr => { const m = await cdp('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }); return m.result?.result?.value ?? m.result?.exceptionDetails?.exception?.description; };
  await cdp('Page.enable'); await cdp('Runtime.enable');
  await cdp('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await cdp('Page.addScriptToEvaluateOnNewDocument', { source: INSTR });
  await ev(`localStorage.clear()`);
  await cdp('Page.navigate', { url: URL_UI });
  await sleep(8000);
  const sample = async label => {
    await cdp('HeapProfiler.collectGarbage');
    const dom = (await cdp('Memory.getDOMCounters')).result;
    const heap = (await cdp('Runtime.getHeapUsage')).result;
    const leak = await ev('JSON.stringify(window.__leak())');
    const extra = await ev(`JSON.stringify({ panes: window.__workspaceDebug.panes.size, layout: window.__workspaceDebug.state.layout,
      htfCache: [...window.__workspaceDebug.panes.values()].reduce((s, p) => s + (p.htfLoader?.cache?.size || 0), 0) })`);
    console.log(label, leak, JSON.stringify({ nodes: dom.nodes, listeners: dom.jsEventListeners, docs: dom.documents, heapMB: +(heap.usedSize / 1e6).toFixed(1) }), extra);
  };
  await sample('start');
  const symbols = JSON.parse(await ev(`JSON.stringify([...document.querySelectorAll('.watchlist-row')].slice(0, 12).map(r => r.dataset.symbol))`) || '[]');
  console.log('symbols', symbols.length);
  const layouts = ['1', '4grid', '2cols', '6grid', '3cols', '2rows'];
  if (process.env.ENABLE) await ev(`(() => { const d = window.__workspaceDebug; ${process.env.ENABLE}.split(',').forEach(id => d.setIndicatorEnabled(id, true)); d.panes.forEach(p => p.applyIndicators()); return 1; })()`);
  if (process.env.ANALYST_OPEN) await ev(`(() => { const d = document.querySelector('[data-role="positioning"]'); d.open = true; d.dispatchEvent(new Event('toggle')); return 1; })()`);
  for (let i = 0; i < ROUNDS; i += 1) {
    if (process.env.MODE !== 'symbol') await ev(`(() => { const s = document.querySelector('#layout-select'); s.value = ${JSON.stringify(layouts[i % layouts.length])}; s.dispatchEvent(new Event('change')); return 1; })()`);
    await sleep(400);
    if (process.env.MODE !== 'layout') await ev(`(() => { const r = [...document.querySelectorAll('.watchlist-row')].find(x => x.dataset.symbol === ${JSON.stringify(symbols[i % Math.max(1, symbols.length)] || 'BTCUSDT')}); r && r.click(); return 1; })()`);
    await sleep(Number(process.env.STEP_MS || 1200));
    if (i % 5 === 4 || i === ROUNDS - 1) await sample(`round ${i + 1}`);
  }
  await ev(`(() => { const s = document.querySelector('#layout-select'); s.value = '4grid'; s.dispatchEvent(new Event('change')); return 1; })()`);
  await sleep(6000);
  await sample('final 4grid');
  ws.close();
}
main().catch(e => { console.error(e); process.exit(1); });
