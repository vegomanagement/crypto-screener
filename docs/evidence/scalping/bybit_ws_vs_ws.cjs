// Incrementally maintained Bybit book (connection A) vs fresh WS snapshots (connection B) at the same seq.
const ob = require('../../../static/orderbook.js');
const SYM = process.argv[2] || 'BTCUSDT';
const sync = new ob.BybitSync(SYM, 0.1, 200); const recs = [];
const a = new WebSocket('wss://stream.bybit.com/v5/public/linear');
a.onopen = () => a.send(JSON.stringify({ op: 'subscribe', args: [`orderbook.200.${SYM}`] }));
a.onmessage = m => { const d = JSON.parse(m.data); const out = sync.onMessage(d); if (out?.applied) { recs.push({ seq: d.data.seq, u: d.data.u, top: sync.book.top(200) }); if (recs.length > 900) recs.shift(); } };
function snapshot() { return new Promise(res => { const b = new WebSocket('wss://stream.bybit.com/v5/public/linear');
  b.onopen = () => b.send(JSON.stringify({ op: 'subscribe', args: [`orderbook.200.${SYM}`] }));
  b.onmessage = m => { const d = JSON.parse(m.data); if (d.type === 'snapshot') { b.close(); res(d.data); } }; }); }
const cmp = (x, y) => { let n = 0, c = 0; for (const side of ['bids', 'asks']) { const p = new Map(x[side]), q = new Map(y[side]); for (const k of new Set([...p.keys(), ...q.keys()])) { c++; if (Math.abs((p.get(k) || 0) - (q.get(k) || 0)) > 1e-9) n++; } } return { compared: c, mismatched: n }; };
(async () => {
  await new Promise(r => setTimeout(r, 3000)); const out = [];
  for (let k = 0; k < 12; k++) {
    const s = await snapshot(); await new Promise(r => setTimeout(r, 500));
    const fresh = new ob.Book(0.1); fresh.load(s.b, s.a); const ft = fresh.top(200);
    const eq = recs.find(r => r.seq === s.seq);
    out.push({ seq: s.seq, equalSeqRecorded: !!eq, ...(eq ? cmp(eq.top, ft) : {}) });
    await new Promise(r => setTimeout(r, 1500));
  }
  console.log(JSON.stringify({ symbol: SYM, at: new Date().toISOString(), samples: out, equalSeq: out.filter(o => o.equalSeqRecorded).length, exactMatches: out.filter(o => o.equalSeqRecorded && o.mismatched === 0).length }));
  a.close(); process.exit(0);
})();
