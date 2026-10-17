// In-page live checks used by live_acceptance.cjs. Every number comes from a real venue response.
'use strict';
const { sleep } = require('./cdp.cjs');

/** Independent rAF loop: distribution of frame intervals while the page runs normally. */
async function rafIntervals(b, ms) {
  return b.evaluate(`new Promise(resolve => { const d = []; let last = null; const end = performance.now() + ${ms};
    const step = t => { if (last !== null) d.push(t - last); last = t; if (t < end) requestAnimationFrame(step); else {
      d.sort((a, b) => a - b); const q = p => d[Math.min(d.length - 1, Math.floor(p * d.length))];
      resolve({ frames: d.length, p50: q(0.5), p95: q(0.95), max: d.at(-1), over50ms: d.filter(x => x > 50).length }); } };
    requestAnimationFrame(step); })`);
}

// Binance: REST lastUpdateId L must fall inside some diff event [U, u] (or between two events). The local book
// after that event equals the venue book at L except on prices the event itself touched.
const binanceSample = col => `(async () => {
  const c = __scalp.page.columns[${col}], sym = c.cfg.symbol;
  const rest = await (await fetch('https://fapi.binance.com/fapi/v1/depth?symbol=' + sym + '&limit=50', { cache: 'no-store' })).json();
  const L = rest.lastUpdateId;
  for (let k = 0; k < 40 && !(c.records.at(-1)?.u >= L); k += 1) await new Promise(r => setTimeout(r, 100));
  const recs = c.records, i = recs.findIndex(r => r.U <= L && L <= r.u);
  let rec = null, mode = 'none';
  if (i >= 0) { rec = recs[i]; mode = rec.u === L ? 'exact' : 'bracket'; }
  else { const j = recs.findIndex((r, k) => r.u < L && recs[k + 1] && recs[k + 1].U > L); if (j >= 0) { rec = recs[j]; mode = 'between'; } }
  if (!rec) return { L, mode, firstU: recs[0]?.U, lastU: recs.at(-1)?.u };
  const touched = new Set(mode === 'bracket' ? rec.touched : []);
  const cmp = (restSide, localSide, lo, hi) => {
    const a = new Map(restSide.map(([p, q]) => [Number(p), Number(q)])), b = new Map(localSide.filter(([p]) => p >= lo && p <= hi));
    const prices = new Set([...a.keys(), ...b.keys()]); let mism = 0, unexplained = 0; const examples = [];
    for (const p of prices) { const x = a.get(p) || 0, y = b.get(p) || 0; if (Math.abs(x - y) > 1e-9) { mism += 1; if (!touched.has(p)) { unexplained += 1; if (examples.length < 5) examples.push({ p, rest: x, local: y }); } } }
    return { compared: prices.size, mismatched: mism, unexplained, examples };
  };
  const rb = rest.bids.map(x => Number(x[0])), ra = rest.asks.map(x => Number(x[0]));
  const bids = cmp(rest.bids, rec.top.bids, Math.min(...rb), Math.max(...rb)), asks = cmp(rest.asks, rec.top.asks, Math.min(...ra), Math.max(...ra));
  return { L, mode, event: { U: rec.U, u: rec.u, pu: rec.pu, touched: rec.touched.length }, bids, asks, restBest: [rest.bids[0], rest.asks[0]], localBest: [rec.top.bids[0], rec.top.asks[0]] };
})()`;
function repeated(samples) {
  const seen = new Map();
  for (const s of samples) for (const side of ['bids', 'asks']) for (const e of s[side]?.examples || []) { const k = `${side}:${e.p}`; seen.set(k, (seen.get(k) || 0) + 1); }
  return [...seen].filter(([, n]) => n > 1).map(([k, n]) => ({ level: k, samples: n }));
}
async function binanceBookVsRest(b, col, n) {
  const samples = [];
  for (let k = 0; k < n; k += 1) { samples.push(await b.evaluate(binanceSample(col))); await sleep(1500); }
  const ok = samples.filter(s => s.bids && s.bids.unexplained === 0 && s.asks.unexplained === 0).length;
  return { samples, matched_samples: ok, total: n, unexplained_levels_repeated_across_samples: repeated(samples), unexplained_levels_total: samples.reduce((a, s) => a + (s.bids?.unexplained || 0) + (s.asks?.unexplained || 0), 0), levels_compared: samples.reduce((a, s) => a + (s.bids?.compared || 0) + (s.asks?.compared || 0), 0) };
}

// Bybit: REST and WS orderbook.200 use different update-id (u) spaces, but both carry the cross sequence
// (seq). Compare at equal seq when recorded; otherwise against the last push before the REST seq, where any
// mismatch must sit on a price that changed between that push and the next one.
const bybitSample = col => `(async () => {
  const c = __scalp.page.columns[${col}], sym = c.cfg.symbol;
  const j = await (await fetch('https://api.bybit.com/v5/market/orderbook?category=linear&limit=50&symbol=' + sym, { cache: 'no-store' })).json();
  const r = j.result, S = Number(r.seq);
  for (let k = 0; k < 30 && !(c.records.at(-1)?.seq >= S); k += 1) await new Promise(res => setTimeout(res, 100));
  const exact = c.records.find(x => x.seq === S), before = c.records.filter(x => x.seq < S).at(-1), after = c.records.find(x => x.seq > S);
  const rec = exact || before;
  if (!rec) return { seq: S, mode: 'no-record' };
  const changed = new Set();
  if (!exact && after) for (const side of ['bids', 'asks']) {
    const m1 = new Map(before.top[side]), m2 = new Map(after.top[side]);
    for (const p of new Set([...m1.keys(), ...m2.keys()])) if ((m1.get(p) || 0) !== (m2.get(p) || 0)) changed.add(p);
  }
  const cmp = (restSide, localSide, lo, hi) => {
    const a = new Map(restSide.map(([p, q]) => [Number(p), Number(q)])), b = new Map(localSide.filter(([p]) => p >= lo && p <= hi));
    const prices = new Set([...a.keys(), ...b.keys()]); let mism = 0, unexplained = 0; const examples = [];
    for (const p of prices) { const x = a.get(p) || 0, y = b.get(p) || 0; if (Math.abs(x - y) > 1e-9) { mism += 1; if (!changed.has(p)) { unexplained += 1; if (examples.length < 5) examples.push({ p, rest: x, local: y }); } } }
    return { compared: prices.size, mismatched: mism, unexplained, examples };
  };
  const rb = r.b.map(x => Number(x[0])), ra = r.a.map(x => Number(x[0]));
  return { seq: S, mode: exact ? 'exact-seq' : 'between', pushes: [before?.seq, after?.seq], changed: changed.size,
    bids: cmp(r.b, rec.top.bids, Math.min(...rb), Math.max(...rb)), asks: cmp(r.a, rec.top.asks, Math.min(...ra), Math.max(...ra)) };
})()`;
async function bybitBookVsRest(b, col, n) {
  const samples = [];
  for (let k = 0; k < n; k += 1) { samples.push(await b.evaluate(bybitSample(col))); await sleep(1500); }
  const ok = samples.filter(s => s.bids && s.bids.unexplained === 0 && s.asks.unexplained === 0).length;
  return { samples, matched_samples: ok, unexplained_levels_repeated_across_samples: repeated(samples), unexplained_levels_total: samples.reduce((a, s) => a + (s.bids?.unexplained || 0) + (s.asks?.unexplained || 0), 0), levels_compared: samples.reduce((a, s) => a + (s.bids?.compared || 0) + (s.asks?.compared || 0), 0), compared_samples: samples.filter(s => s.bids).length, total: n };
}

// Feed vs venue REST trades over a closed window that is fully inside our subscription.
const feedWindow = col => `(() => { const d = __scalp.page.columns[${col}].data, t1 = Date.now() - 3000, t0 = Math.max(d.raw[0]?.time ?? t1, t1 - 60000) + 1;
  return { t0, t1, ids: d.raw.filter(p => p.time >= t0 && p.time <= t1).map(p => String(p.id)), counts: d.windowCounts(t0, t1),
    rendered: __scalp.page.columns[${col}].lastDraw?.feed, idGaps: d.stats.idGaps, rejected: d.stats.rejected }; })()`;
function diffIds(local, remote) {
  const a = new Set(local), r = new Set(remote);
  return { local: a.size, rest: r.size, missing_locally: [...r].filter(x => !a.has(x)).length, extra_locally: [...a].filter(x => !r.has(x)).length };
}
async function binanceFeedVsRest(b, col) {
  const w = await b.evaluate(feedWindow(col));
  const sym = await b.evaluate(`__scalp.page.columns[${col}].cfg.symbol`);
  const rest = [];
  let url = `https://fapi.binance.com/fapi/v1/aggTrades?symbol=${sym}&startTime=${w.t0}&endTime=${w.t1}&limit=1000`;
  for (let page = 0; page < 10; page += 1) {
    const rows = await (await fetch(url)).json();
    rest.push(...rows.filter(r => r.T >= w.t0 && r.T <= w.t1));
    if (rows.length < 1000) break;
    url = `https://fapi.binance.com/fapi/v1/aggTrades?symbol=${sym}&fromId=${rows.at(-1).a + 1}&limit=1000`;
  }
  return { window: [w.t0, w.t1], ...diffIds(w.ids, rest.map(r => String(r.a))), feed_counts: w.counts, idGaps: w.idGaps, rejected: w.rejected };
}
async function bybitFeedVsRest(b, col) {
  const w = await b.evaluate(feedWindow(col));
  const sym = await b.evaluate(`__scalp.page.columns[${col}].cfg.symbol`);
  const j = await (await fetch(`https://api.bybit.com/v5/market/recent-trade?category=linear&limit=1000&symbol=${sym}`)).json();
  const list = j.result.list, oldest = Math.min(...list.map(r => Number(r.time)));
  const t0 = Math.max(w.t0, oldest + 1), inWin = r => Number(r.time) >= t0 && Number(r.time) <= w.t1;
  const local = await b.evaluate(`__scalp.page.columns[${col}].data.raw.filter(p => p.time >= ${t0} && p.time <= ${w.t1}).map(p => String(p.id))`);
  return { window: [t0, w.t1], rest_window_limited_by_1000_rows: oldest > w.t0, ...diffIds(local, list.filter(inWin).map(r => r.execId)), feed_counts: w.counts };
}

/** View-only interactions via real CDP input: hotkey 3 (×5 ticks), click + hover ruler. */
async function rulerAndHotkeys(b, col) {
  const rect = await b.evaluate(`(() => { const r = __scalp.page.columns[${col}].canvas.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; })()`);
  await b.send('Input.dispatchKeyEvent', { type: 'keyDown', key: '3', text: '3' }); await b.send('Input.dispatchKeyEvent', { type: 'keyUp', key: '3' });
  await sleep(600);
  const x = rect.x + rect.w - 120, y0 = rect.y + rect.h / 2, y1 = rect.y + rect.h * 0.2;
  await b.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y: y0 });
  await b.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y: y0, button: 'left', clickCount: 1 });
  await b.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y: y0, button: 'left', clickCount: 1 });
  await b.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y: y1 });
  await sleep(400);
  return b.evaluate(`(() => { const c = __scalp.page.columns[${col}]; return { scaleIndex: c.cfg.scaleIndex, rowN: c.rowN, label: c.element.querySelector('.dom-scale').textContent,
    ruler: c.ruler, persisted: JSON.parse(localStorage.getItem(ScalpSettings.STORAGE_KEY)).cols[${col}].scaleIndex }; })()`);
}

module.exports = { rafIntervals, binanceBookVsRest, bybitBookVsRest, binanceFeedVsRest, bybitFeedVsRest, rulerAndHotkeys };
