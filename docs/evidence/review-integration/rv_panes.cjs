// Count live Pane instances after layout churn (CDP Runtime.queryObjects) on the given /ui URL.
const PORT = 9568, URL_UI = process.env.UI_URL || 'http://127.0.0.1:5095/ui';
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const page = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl); await new Promise(r => ws.addEventListener('open', r));
  let n = 0; const wait = new Map();
  ws.addEventListener('message', e => { const m = JSON.parse(e.data); if (wait.has(m.id)) { wait.get(m.id)(m.result); wait.delete(m.id); } });
  const cdp = (method, params = {}) => new Promise(r => { const id = ++n; wait.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
  const ev = async e => (await cdp('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true })).result.value;
  await cdp('Runtime.enable'); await cdp('Page.navigate', { url: URL_UI }); await sleep(8000);
  const count = async () => {
    await cdp('HeapProfiler.collectGarbage');
    const proto = await cdp('Runtime.evaluate', { expression: 'window.__workspaceDebug.panes.values().next().value.constructor.prototype' });
    const q = await cdp('Runtime.queryObjects', { prototypeObjectId: proto.result.objectId });
    const len = await cdp('Runtime.callFunctionOn', { objectId: q.objects.objectId, functionDeclaration: 'function(){ return this.length; }', returnByValue: true });
    return len.result.value;
  };
  console.log('Pane instances at start', await count());
  for (let i = 0; i < 10; i += 1) {
    await ev(`(() => { const s = document.querySelector('#layout-select'); s.value = '${i % 2 ? '4grid' : '1'}'; s.dispatchEvent(new Event('change')); return 1; })()`);
    await sleep(1500);
  }
  console.log('Pane instances after 10 layout switches (live panes:', await ev('window.__workspaceDebug.panes.size'), ')', await count());
  await sleep(8000);
  console.log('after 8 s idle', await count());
  ws.close();
})();
