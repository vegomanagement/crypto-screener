/* Paper journal (/paper): pure view-model + DOM renderer. Inputs are this app's own read-only
 * endpoints /api/paper/strategies, /api/paper/trades, /api/paper/stats. Paper results only —
 * not a signal; nothing here places orders. UMD: window.PaperJournal, module.exports under node.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.PaperJournal = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const isNum = v => typeof v === 'number' && Number.isFinite(v);
  const signed = (v, d = 3, suffix = '') => (isNum(v) ? `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(d)}${suffix}` : '—');
  const utc = ms => (isNum(ms) ? new Date(ms).toISOString().replace('T', ' ').slice(0, 16) + ' UTC' : '—');
  const price = v => (isNum(v) ? (Math.abs(v) >= 100 ? v.toFixed(2) : Math.abs(v) >= 1 ? v.toFixed(4) : v.toPrecision(5)) : '—');
  const pct = v => (isNum(v) ? `${(100 * v).toFixed(1)}%` : '—');
  const ci = (pair, f) => (Array.isArray(pair) && pair.length === 2 ? `[${f(pair[0])} … ${f(pair[1])}]` : 'n/a');
  const esc = s => String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const STATE_LABEL = { pending: 'pending (limit resting)', filled: 'filled', be: 'filled · stop at BE', target: 'target', stop: 'stop',
    breakeven: 'break-even', timeout: 'time exit', expired: 'expired' };
  const OPEN = ['pending', 'filled', 'be'];

  /** Rows of the open-setups table (pending + filled), newest first. Warm-up rows flagged. */
  function openRows(trades) {
    return trades.filter(t => OPEN.includes(t.state)).sort((a, b) => b.knowable_ts - a.knowable_ts).map(t => ({
      id: t.id, symbol: t.symbol, side: t.side, state: STATE_LABEL[t.state] || t.state, stateKey: t.state,
      knowable: utc(t.knowable_ts), recorded: utc(t.recorded_ts), poi: t.inputs && t.inputs.poi ? `${t.inputs.poi.kind.toUpperCase()} ${price(t.inputs.poi.bottom)}–${price(t.inputs.poi.top)}` : '—',
      entry: price(isNum(t.entry) ? t.entry : t.planned_entry), stop: price(isNum(t.stop) ? t.stop : t.planned_stop),
      target: price(isNum(t.target) ? t.target : t.planned_target), fill: utc(t.fill_ts),
      tag: t.seen_at_start ? 'seen at start (not counted)' : t.late ? 'late (recorded after a gap)' : '' }));
  }

  function closedRows(trades) {
    return trades.filter(t => ['target', 'stop', 'breakeven', 'timeout'].includes(t.state)).sort((a, b) => b.exit_ts - a.exit_ts).map(t => ({
      id: t.id, symbol: t.symbol, side: t.side, exit: STATE_LABEL[t.state] || t.state, fill: utc(t.fill_ts), exitTs: utc(t.exit_ts),
      entry: price(t.entry), gross: signed(t.r_gross, 2, 'R'), fee: signed(isNum(t.fee_r) ? -t.fee_r : null, 3, 'R'), fund: signed(t.fund_r, 3, 'R'),
      net: signed(t.net_r, 3, 'R'), netPct: signed(t.net_pct, 2, '%'), tone: isNum(t.net_r) ? (t.net_r > 0 ? 'up' : t.net_r < 0 ? 'down' : '') : '',
      tag: t.seen_at_start ? 'seen at start (not counted)' : !isNum(t.net_r) ? `costs pending: ${t.fund_status || 'funding'}` : '' }));
  }

  /** Headline numbers + the comparison against the backtest expectation. */
  function summary(stats) {
    const s = stats || {};
    const vb = s.vs_backtest || {};
    const n = s.n || 0;
    return {
      n, hit: pct(s.hit), hitCi: ci(s.hit_ci, pct), meanR: signed(s.mean_r, 3, 'R'), meanCi: ci(s.mean_r_ci, v => signed(v, 3)),
      cumR: signed(s.cum_r, 2, 'R'), maxDd: isNum(s.max_dd_r) ? `${s.max_dd_r.toFixed(2)}R` : '—',
      expected: isNum(vb.expected_mean_r) ? `${signed(vb.expected_mean_r, 3, 'R')} OOS (n=${vb.expected_n}, CI ${ci(vb.expected_ci, v => signed(v, 3))})` : '—',
      diff: signed(vb.diff_r, 3, 'R'),
      verdict: n < 30 ? `n=${n}: too few trades to conclude anything (need 30–50+)` : n < 50
        ? `n=${n}: early read only — CI still wide` : `n=${n}: compare the CI with the backtest's`,
      enough: n >= 30, counts: s.counts || {}, note: s.note || '' };
  }

  /** SVG path for the cumulative-R equity curve (0-line included in the y range). */
  function equityPath(points, w, h, pad = 6) {
    if (!points || !points.length) return { d: '', zeroY: null, min: 0, max: 0 };
    const ys = [0].concat(points.map(p => p.cum_r));
    const min = Math.min(...ys), max = Math.max(...ys), span = max - min || 1;
    const X = i => pad + (i * (w - 2 * pad)) / Math.max(1, points.length);
    const Y = v => pad + ((max - v) * (h - 2 * pad)) / span;
    const d = [`M${X(0).toFixed(1)},${Y(0).toFixed(1)}`].concat(points.map((p, i) => `L${X(i + 1).toFixed(1)},${Y(p.cum_r).toFixed(1)}`)).join(' ');
    return { d, zeroY: Y(0), min, max };
  }

  function table(cols, rows, empty) {
    if (!rows.length) return `<p class="empty">${esc(empty)}</p>`;
    return `<table><thead><tr>${cols.map(c => `<th>${esc(c[1])}</th>`).join('')}</tr></thead><tbody>${rows.map(r =>
      `<tr class="${esc(r.tone || '')}${r.tag ? ' tagged' : ''}">${cols.map(c => `<td>${esc(r[c[0]])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  }

  function render(el, data) {
    const strat = data.strategy || {};
    const s = summary(data.stats);
    const eq = equityPath((data.stats || {}).equity, 640, 160);
    const coins = Object.entries((data.stats || {}).per_coin || {}).map(([k, v]) => ({ symbol: k, n: v.n, hit: pct(v.hit), mean: signed(v.mean_r, 3, 'R'), sum: signed(v.sum_r, 2, 'R') }));
    const w = data.worker || {};
    el.innerHTML = `
      <div class="banner">PAPER TRADING · forward test · <b>not a signal</b> · no orders, no exchange keys</div>
      <h2>${esc(strat.title || strat.id || 'strategy')} <small>v${esc(strat.version)} · ${esc(strat.params_hash || '')}</small></h2>
      <p class="meta">Worker: ${w.enabled ? (w.running ? `running · ${esc(w.cycles)} cycles` : 'enabled, not running in this process') : 'off (set PAPER_JOURNAL=1)'}
        ${w.last ? ` · last cycle ${esc(utc(w.last.ts))} (${esc(w.last.reason)})` : ''}${w.next ? ` · next ${esc(utc(w.next.ts))} (${esc(w.next.reason)})` : ''}</p>
      <section class="cards">
        <div class="card"><span>Counted trades</span><b>${s.n}</b><i>${esc(s.verdict)}</i></div>
        <div class="card"><span>Mean net R</span><b>${esc(s.meanR)}</b><i>95% CI ${esc(s.meanCi)}</i></div>
        <div class="card"><span>Hit rate</span><b>${esc(s.hit)}</b><i>Wilson ${esc(s.hitCi)}</i></div>
        <div class="card"><span>Cumulative</span><b>${esc(s.cumR)}</b><i>max DD ${esc(s.maxDd)}</i></div>
        <div class="card wide"><span>Backtest expectation</span><b>${esc(s.expected)}</b><i>paper − backtest: ${esc(s.diff)} · post-hoc pick, flat in-sample</i></div>
      </section>
      <p class="warn">${esc(s.note)}</p>
      <p class="meta">Pending ${esc(s.counts.pending || 0)} · open ${esc(s.counts.open || 0)} · expired ${esc(s.counts.expired || 0)} · seen at start (excluded) ${esc(s.counts.seen_at_start || 0)} · late ${esc(s.counts.late || 0)}</p>
      <h3>Equity curve (net R, counted trades)</h3>
      <svg class="equity" viewBox="0 0 640 160" role="img" aria-label="Cumulative net R">${eq.zeroY === null ? '<text x="10" y="80">no closed counted trades yet</text>'
        : `<line x1="0" x2="640" y1="${eq.zeroY.toFixed(1)}" y2="${eq.zeroY.toFixed(1)}" class="zero"/><path d="${eq.d}"/>`}</svg>
      <h3>Open setups</h3>${table([['symbol', 'Coin'], ['side', 'Side'], ['state', 'State'], ['knowable', 'Knowable at (bar close)'], ['poi', 'POI'], ['entry', 'Entry'], ['stop', 'Stop'], ['target', 'Target 2R'], ['fill', 'Filled'], ['tag', '']], openRows(data.trades || []), 'No open setups.')}
      <h3>Closed trades</h3>${table([['symbol', 'Coin'], ['side', 'Side'], ['exit', 'Exit'], ['fill', 'Filled'], ['exitTs', 'Closed'], ['entry', 'Entry'], ['gross', 'Gross'], ['fee', 'Fees'], ['fund', 'Funding'], ['net', 'Net'], ['netPct', 'Net %'], ['tag', '']], closedRows(data.trades || []), 'No closed trades yet.')}
      <h3>Per coin</h3>${table([['symbol', 'Coin'], ['n', 'n'], ['hit', 'Hit'], ['mean', 'Mean'], ['sum', 'Sum']], coins, 'No counted trades yet.')}`;
  }

  async function load(fetchJson, sid) {
    const meta = await fetchJson('/api/paper/strategies');
    const versions = (meta.strategies || []).filter(r => !sid || r.id === sid);
    const strat = versions.filter(r => r.active).pop() || versions.pop() || {};
    if (!strat.id) return { strategy: {}, worker: meta.worker, trades: [], stats: null };
    const q = `strategy=${encodeURIComponent(strat.id)}&version=${strat.version}`;
    const [trades, stats] = await Promise.all([fetchJson(`/api/paper/trades?${q}`), fetchJson(`/api/paper/stats?${q}`)]);
    return { strategy: strat, worker: meta.worker, trades: trades.trades || [], stats };
  }

  return { openRows, closedRows, summary, equityPath, render, load, utc, signed };
});
