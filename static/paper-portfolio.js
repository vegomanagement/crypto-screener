/* Paper journal — portfolio strategies (kind 'portfolio'): forward equity in % against BTC
 * buy-and-hold from the same start close and the backtest expectation band, positions, next-open
 * orders. Input: this app's read-only /api/paper/portfolio. Paper results only — not a signal.
 * UMD: window.PaperPortfolio, module.exports under node.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.PaperPortfolio = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const isNum = v => typeof v === 'number' && Number.isFinite(v);
  const pct = (v, d = 2) => (isNum(v) ? `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(d)}%` : '—');
  const day = ms => (isNum(ms) ? new Date(ms).toISOString().slice(0, 10) : '—');
  const price = v => (isNum(v) ? (Math.abs(v) >= 100 ? v.toFixed(2) : Math.abs(v) >= 1 ? v.toFixed(4) : v.toPrecision(5)) : '—');
  const esc = s => String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /** Headline cards. Days < 30: says so instead of implying a read. */
  function summary(d) {
    const s = d || {};
    const n = s.n_days || 0;
    const exp = s.expectation || {};
    const last = (s.curve || [])[(s.curve || []).length - 1] || {};
    return {
      n, ret: pct(s.total_return_pct), btc: pct(s.btc_return_pct), vsBtc: isNum(s.total_return_pct) && isNum(s.btc_return_pct) ? pct(s.total_return_pct - s.btc_return_pct) : '—',
      band: isNum(last.band_lo_pct) ? `${pct(last.band_lo_pct, 1)} … ${pct(last.band_hi_pct, 1)} (mid ${pct(last.band_mid_pct, 1)})` : '—',
      inBand: s.in_band === true ? 'inside the band' : s.in_band === false ? 'OUTSIDE the band' : '—',
      maxDd: isNum(s.max_dd_pct) ? `${s.max_dd_pct.toFixed(2)}%` : '—', sharpe: isNum(s.sharpe_ann) ? s.sharpe_ann.toFixed(2) : '—',
      vol: isNum(s.vol_ann_pct) ? `${s.vol_ann_pct.toFixed(1)}%` : '—', fees: pct(isNum(s.fees_pct) ? -s.fees_pct : null, 3), funding: pct(s.funding_pct, 3),
      start: day(s.start_ts), last: day(s.last_ts), late: s.late_days || 0,
      dev: exp.dev ? `dev CAGR ${pct(100 * exp.dev.cagr, 1)} · vol ${pct(100 * exp.dev.vol, 1)} · Sharpe ${exp.dev.sharpe}` : '—',
      holdout: exp.holdout_2026 ? `2026 holdout ${pct(100 * exp.holdout_2026.total_return, 1)} (Sharpe ${exp.holdout_2026.sharpe})` : '',
      verdict: n < 30 ? `${n} days: far too short to conclude anything` : n < 90 ? `${n} days: early read only` : `${n} days`,
      note: s.note || '', caveat: exp.caveat || '' };
  }

  /** SVG geometry: strategy / BTC polylines and the band polygon, all in % since the start close. */
  function chart(curve, w, h, pad = 8) {
    const pts = curve || [];
    if (!pts.length) return null;
    const keys = ['ret_pct', 'btc_pct', 'band_lo_pct', 'band_hi_pct'];
    const ys = [0];
    pts.forEach(p => keys.forEach(k => { if (isNum(p[k])) ys.push(p[k]); }));
    const min = Math.min(...ys), max = Math.max(...ys), span = max - min || 1;
    const X = i => pad + (i * (w - 2 * pad)) / Math.max(1, pts.length - 1);
    const Y = v => pad + ((max - v) * (h - 2 * pad)) / span;
    const line = k => pts.map((p, i) => (isNum(p[k]) ? `${X(i).toFixed(1)},${Y(p[k]).toFixed(1)}` : null)).filter(Boolean).join(' ');
    const hasBand = pts.every(p => isNum(p.band_lo_pct) && isNum(p.band_hi_pct));
    const band = hasBand ? pts.map((p, i) => `${X(i).toFixed(1)},${Y(p.band_hi_pct).toFixed(1)}`)
      .concat(pts.slice().reverse().map((p, j) => `${X(pts.length - 1 - j).toFixed(1)},${Y(p.band_lo_pct).toFixed(1)}`)).join(' ') : '';
    return { strategy: line('ret_pct'), btc: line('btc_pct'), band, zeroY: Y(0), min, max };
  }

  function positionRows(positions, open) {
    return (positions || []).filter(p => (p.exit_ts === null || p.exit_ts === undefined) === open)
      .sort((a, b) => (open ? b.entry_ts - a.entry_ts : b.exit_ts - a.exit_ts)).map(p => ({
        symbol: p.symbol, side: p.side, entry: day(p.entry_ts), entryPx: price(p.entry_px), weight: isNum(p.weight) ? `${(100 * p.weight).toFixed(1)}%` : '—',
        exit: day(p.exit_ts), exitPx: price(p.exit_px), why: p.why === 'open' ? 'open (marked)' : p.why,
        pnl: pct(isNum(p.pnl) ? 100 * p.pnl : null, 3), funding: pct(isNum(p.funding) ? 100 * p.funding : null, 3),
        tone: isNum(p.pnl) ? (p.pnl > 0 ? 'up' : p.pnl < 0 ? 'down' : '') : '' }));
  }

  function ordersText(next) {
    if (!next) return 'none recorded yet';
    const o = Object.entries(next.orders || {}).map(([s, x]) => `${s} → w ${isNum(x.w) ? x.w.toFixed(3) : '—'} (${x.tag || x.type || 'market'})`);
    const st = Object.entries(next.stops || {}).map(([s, v]) => `${s} stop ${price(v)}`);
    return (o.length ? o.join(' · ') : 'no orders') + (st.length ? ` | resting stops: ${st.join(' · ')}` : '');
  }

  function table(cols, rows, empty) {
    if (!rows.length) return `<p class="empty">${esc(empty)}</p>`;
    return `<table><thead><tr>${cols.map(c => `<th>${esc(c[1])}</th>`).join('')}</tr></thead><tbody>${rows.map(r =>
      `<tr class="${esc(r.tone || '')}">${cols.map(c => `<td>${esc(r[c[0]])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  }

  function render(el, strat, worker, d, tabsHtml) {
    const s = summary(d);
    const g = chart(d.curve, 640, 180);
    const cols = [['symbol', 'Coin'], ['side', 'Side'], ['entry', 'Entry (open)'], ['entryPx', 'Px'], ['weight', 'Weight'], ['exit', 'Exit'], ['exitPx', 'Px'], ['why', 'Why'], ['pnl', 'P&L (% start eq.)'], ['funding', 'Funding']];
    el.innerHTML = `${tabsHtml || ''}
      <div class="banner">PAPER TRADING · forward test · <b>not a signal</b> · no orders, no exchange keys</div>
      <h2>${esc(strat.title || strat.id)} <small>v${esc(strat.version)} · ${esc(strat.params_hash || '')} · portfolio, daily close → next open</small></h2>
      <p class="meta">Worker: ${worker && worker.enabled ? (worker.running ? `running · ${esc(worker.cycles)} cycles` : 'enabled, not running in this process') : 'off (set PAPER_JOURNAL=1)'} · started flat at the ${esc(s.start)} close · last close ${esc(s.last)} · late days ${esc(s.late)}</p>
      <section class="cards">
        <div class="card"><span>Forward return</span><b>${esc(s.ret)}</b><i>${esc(s.verdict)}</i></div>
        <div class="card"><span>BTC buy &amp; hold (same start)</span><b>${esc(s.btc)}</b><i>strategy − BTC: ${esc(s.vsBtc)}</i></div>
        <div class="card wide"><span>Backtest band (95 %)</span><b>${esc(s.band)}</b><i>${esc(s.inBand)} · ${esc(s.dev)} ${esc(s.holdout ? '· ' + s.holdout : '')}</i></div>
        <div class="card"><span>Max drawdown</span><b>${esc(s.maxDd)}</b><i>vol ${esc(s.vol)} · Sharpe ${esc(s.sharpe)}</i></div>
        <div class="card"><span>Costs</span><b>${esc(s.fees)}</b><i>funding ${esc(s.funding)}</i></div>
      </section>
      <p class="warn">${esc(s.note)} ${esc(s.caveat)}</p>
      <h3>Equity since start (%) — <span style="color:#5aa0ff">strategy</span> · <span style="color:#f5a623">BTC</span> · band</h3>
      <svg class="equity" viewBox="0 0 640 180" role="img" aria-label="Forward equity vs BTC and the backtest band">${!g ? '<text x="10" y="90">no closes recorded yet</text>'
        : `${g.band ? `<polygon points="${g.band}" class="band"/>` : ''}<line x1="0" x2="640" y1="${g.zeroY.toFixed(1)}" y2="${g.zeroY.toFixed(1)}" class="zero"/><polyline points="${g.btc}" class="btc"/><polyline points="${g.strategy}" class="strat"/>`}</svg>
      <p class="meta">Next open (decided at the last close): ${esc(ordersText(d.next_orders))}</p>
      <h3>Open positions</h3>${table(cols, positionRows(d.positions, true), 'Flat.')}
      <h3>Closed positions</h3>${table(cols, positionRows(d.positions, false), 'No closed positions yet.')}`;
  }

  return { summary, chart, positionRows, ordersText, render };
});
