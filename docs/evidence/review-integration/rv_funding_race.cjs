// Live repro: switch the active pane's symbol while its /api/funding request is in flight
// (requests held 3 s via Fetch interception) and read the funding chip afterwards.
const PORT = 9568, URL_UI = process.env.UI_URL || 'http://127.0.0.1:5095/ui';
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const page = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl); await new Promise(r => ws.addEventListener('open', r));
  let n = 0; const wait = new Map();
  const cdp = (method, params = {}) => new Promise(r => { const id = ++n; wait.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
  const ev = async e => (await cdp('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true })).result.value;
  const log = [];
  ws.addEventListener('message', e => { const m = JSON.parse(e.data);
    if (m.method === 'Fetch.requestPaused') {
      log.push(new URL(m.params.request.url).search);
      setTimeout(() => cdp('Fetch.continueRequest', { requestId: m.params.requestId }), 3000);
    }
    if (wait.has(m.id)) { wait.get(m.id)(m.result); wait.delete(m.id); } });
  await cdp('Runtime.enable');
  await ev('localStorage.clear()');
  await cdp('Page.navigate', { url: URL_UI }); await sleep(9000);   // initial funding loads finish
  await cdp('Fetch.enable', { patterns: [{ urlPattern: '*/api/funding*' }] });
  const chip = () => ev(`(() => { const p = window.__workspaceDebug.panes.get(window.__workspaceDebug.state.active); const el = p.element.querySelector('.pane-funding');
    return JSON.stringify({ symbol: window.__workspaceDebug.state.panes[window.__workspaceDebug.state.active].symbol, hidden: el.hidden, text: el.textContent, key: p.fundingKey, data: p.fundingData && (p.fundingData.symbol || p.fundingData.error) }); })()`);
  const click = s => ev(`(() => { const r = [...document.querySelectorAll('.watchlist-row')][${s}]; r.click(); return r.dataset.symbol; })()`);
  console.log('switch to', await click(3));
  await sleep(300);
  console.log('switch to', await click(4), '(first request still in flight)');
  await sleep(9000);
  console.log('9 s later:', await chip(), 'funding requests:', log);
  ws.close();
})();
