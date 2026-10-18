/* Paper journal — observer strategies (kind 'observer'): hypothetical follow trades after recorded
 * Hyperliquid whale events. Input: this app's read-only /api/paper/observer. No edge claimed.
 * UMD: window.PaperObserver, module.exports under node.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.PaperObserver = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const isNum = v => typeof v === 'number' && Number.isFinite(v);
  const pct = (v, d = 2) => (isNum(v) ? `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(100 * v).toFixed(d)}%` : '—');
  const utc = ms => (isNum(ms) ? new Date(ms).toISOString().replace('T', ' ').slice(0, 16) + ' UTC' : '—');
  const esc = s => String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const short = a => (typeof a === 'string' && a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a || '—');

  function horizonRows(byH) {
    return Object.entries(byH || {}).map(([h, v]) => ({ h: `${h} d`, rows: v.rows, closed: v.closed, open: v.open, waiting: v.waiting,
      noPrice: v.no_price, mean: pct(v.mean_net), median: pct(v.median_net), hit: isNum(v.hit) ? `${(100 * v.hit).toFixed(0)}%` : '—' }));
  }

  function tradeRows(rows) {
    return (rows || []).map(r => ({ when: utc(r.knowable_ts), wallet: `#${r.wallet_rank} ${short(r.address)}`, coin: r.coin, side: r.side,
      kind: r.event_kind, h: `${r.horizon_d} d`, entry: utc(r.entry_ts), state: r.state, net: pct(r.ret_net),
      tone: isNum(r.ret_net) ? (r.ret_net > 0 ? 'up' : r.ret_net < 0 ? 'down' : '') : '' }));
  }

  function table(cols, rows, empty) {
    if (!rows.length) return `<p class="empty">${esc(empty)}</p>`;
    return `<table><thead><tr>${cols.map(c => `<th>${esc(c[1])}</th>`).join('')}</tr></thead><tbody>${rows.map(r =>
      `<tr class="${esc(r.tone || '')}">${cols.map(c => `<td>${esc(r[c[0]])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  }

  function render(el, strat, worker, d, tabsHtml) {
    const data = d || {};
    el.innerHTML = `${tabsHtml || ''}
      <div class="banner">OBSERVER · hypothetical follow trades · <b>no edge claimed</b> · no orders</div>
      <h2>${esc(strat.title || strat.id)} <small>v${esc(strat.version)} · ${esc(strat.params_hash || '')}</small></h2>
      <p class="meta">Started ${esc(utc(data.state && data.state.started_as_of))} · events before the start are never followed. ${esc(data.note || '')}</p>
      <h3>By holding period</h3>${table([['h', 'Hold'], ['rows', 'Rows'], ['closed', 'Closed'], ['open', 'Open'], ['waiting', 'Waiting entry'], ['noPrice', 'No price'], ['mean', 'Mean net'], ['median', 'Median net'], ['hit', 'Hit']], horizonRows(data.by_horizon), 'No followed events yet (needs WHALE_RECORDER=1 and recorded opens/flips by top-10 wallets).')}
      <h3>Recent follow rows</h3>${table([['when', 'Event knowable'], ['wallet', 'Wallet (rank by 30d PnL)'], ['coin', 'Coin'], ['side', 'Side'], ['kind', 'Event'], ['h', 'Hold'], ['entry', 'Entry (next 1m open)'], ['state', 'State'], ['net', 'Net']], tradeRows(data.rows), 'None yet.')}`;
  }

  return { horizonRows, tradeRows, render };
});
