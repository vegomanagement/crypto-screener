// Heap snapshot → shortest retainer path (from GC roots) for retained Pane objects after layout churn.
const PORT = 9568, URL_UI = process.env.UI_URL || 'http://127.0.0.1:5095/ui';
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const page = (await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()).find(t => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl); await new Promise(r => ws.addEventListener('open', r));
  let n = 0; const wait = new Map(); let chunks = [];
  ws.addEventListener('message', e => { const m = JSON.parse(e.data);
    if (m.method === 'HeapProfiler.addHeapSnapshotChunk') chunks.push(m.params.chunk);
    if (wait.has(m.id)) { wait.get(m.id)(m.result); wait.delete(m.id); } });
  const cdp = (method, params = {}) => new Promise(r => { const id = ++n; wait.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
  const ev = async e => (await cdp('Runtime.evaluate', { expression: e, returnByValue: true, awaitPromise: true })).result.value;
  await cdp('Runtime.enable'); await cdp('HeapProfiler.enable'); await cdp('Page.navigate', { url: URL_UI }); await sleep(8000);
  for (let i = 0; i < 10; i += 1) {
    await ev(`(() => { const s = document.querySelector('#layout-select'); s.value = '${i % 2 ? '4grid' : '1'}'; s.dispatchEvent(new Event('change')); return 1; })()`);
    await sleep(1500);
  }
  await cdp('HeapProfiler.collectGarbage');
  await cdp('HeapProfiler.takeHeapSnapshot', { reportProgress: false });
  const snap = JSON.parse(chunks.join(''));
  const m = snap.snapshot.meta, nf = m.node_fields.length, ef = m.edge_fields.length;
  const nodeTypes = m.node_types[0], edgeTypes = m.edge_types[0];
  const N = snap.nodes, E = snap.edges, S = snap.strings;
  const iType = m.node_fields.indexOf('type'), iName = m.node_fields.indexOf('name'), iEC = m.node_fields.indexOf('edge_count');
  const eType = m.edge_fields.indexOf('type'), eName = m.edge_fields.indexOf('name_or_index'), eTo = m.edge_fields.indexOf('to_node');
  const count = N.length / nf, firstEdge = new Uint32Array(count + 1);
  for (let i = 0, e = 0; i < count; i += 1) { firstEdge[i] = e; e += N[i * nf + iEC] * ef; } firstEdge[count] = E.length;
  const name = i => S[N[i * nf + iName]], type = i => nodeTypes[N[i * nf + iType]];
  // reverse edges
  const rev = Array.from({ length: count }, () => []);
  for (let i = 0; i < count; i += 1) for (let e = firstEdge[i]; e < firstEdge[i + 1]; e += ef) {
    const t = edgeTypes[E[e + eType]]; if (t === 'weak' || t === 'shortcut') continue;
    const to = E[e + eTo] / nf, label = t === 'element' || t === 'hidden' ? `[${E[e + eName]}]` : S[E[e + eName]];
    rev[to].push([i, `${t}:${label}`]);
  }
  const panes = []; for (let i = 0; i < count; i += 1) if (type(i) === 'object' && name(i) === 'Pane') panes.push(i);
  console.log('Pane objects in snapshot', panes.length);
  // BFS from each pane back to the root (node 0) and print the path
  for (const start of panes) {
    const prev = new Map([[start, null]]), q = [start]; let found = null;
    while (q.length && found === null) { const x = q.shift(); for (const [p, lab] of rev[x]) { if (prev.has(p)) continue; prev.set(p, [x, lab]); if (p === 0) { found = p; break; } q.push(p); } }
    const path = []; let cur = found; while (cur !== null && cur !== undefined && prev.get(cur)) { const [child, lab] = prev.get(cur); path.push(`${type(cur)}:${name(cur).slice(0, 40)} -${lab}->`); cur = child; }
    console.log(path.slice(-30).join(' '), 'Pane');
  }
  ws.close();
})();
