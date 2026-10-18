/* Funding carry panel (/carry): delta-neutral long spot + short perp. Input: this app's read-only
 * /api/carry. Every APR shown is an ESTIMATE from past funding. UMD: window.CarryPanel / node.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.CarryPanel = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const isNum = v => typeof v === 'number' && Number.isFinite(v);
  const apr = v => (isNum(v) ? `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(100 * v).toFixed(1)}%` : '—');
  const esc = s => String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const tone = v => (isNum(v) ? (v > 0 ? 'up' : v < 0 ? 'down' : '') : '');

  /** Table rows sorted by the estimated net APR (missing last). */
  function rows(data) {
    return ((data && data.rows) || []).map(r => {
      const v = r.venues || {};
      const venue = k => (v[k] ? apr(v[k].ann) : '—');
      return { symbol: r.symbol, binance: venue('binance'), bybit: venue('bybit'), hyperliquid: venue('hyperliquid'),
        d7: apr(r.ann_7d), d30: apr(r.ann_30d), d90: apr(r.ann_90d),
        neg: isNum(r.neg_share_90d) ? `${(100 * r.neg_share_90d).toFixed(0)}%` : '—',
        net: apr(r.net_apr_est), netBnb: apr(r.net_apr_est_bnb), sortKey: isNum(r.net_apr_est) ? r.net_apr_est : -Infinity,
        tone: tone(r.net_apr_est), spread: spread(v) };
    }).sort((a, b) => b.sortKey - a.sortKey);
  }

  /** Largest current annualised gap between venues (context for the cross-venue variant). */
  function spread(v) {
    const xs = ['binance', 'bybit', 'hyperliquid'].filter(k => v[k] && isNum(v[k].ann)).map(k => [k, v[k].ann]);
    if (xs.length < 2) return '—';
    xs.sort((a, b) => a[1] - b[1]);
    return `${apr(xs[xs.length - 1][1] - xs[0][1])} (${xs[xs.length - 1][0]} − ${xs[0][0]})`;
  }

  const COLS = [['symbol', 'Coin'], ['binance', 'Binance now'], ['bybit', 'Bybit next'], ['hyperliquid', 'HL now'], ['spread', 'Venue gap'],
    ['d7', '7d'], ['d30', '30d'], ['d90', '90d'], ['neg', 'Neg. prints 90d'], ['net', 'Est. net APR*'], ['netBnb', 'w/ BNB fee']];

  function render(el, data) {
    const c = (data && data.costs) || {};
    const list = rows(data);
    const miss = Object.entries((data && data.missing) || {});
    el.innerHTML = `
      <div class="banner">RESEARCH · funding carry analytics · <b>estimates, not a signal</b> · no orders</div>
      <p class="meta">Current annualised funding per venue and trailing Binance carry, per coin. Short-leg margin L = ${esc(data && data.L)}x
        (capital = spot notional × (L+1)/L). Round trip ${isNum(c.round_trip) ? (100 * c.round_trip).toFixed(2) : '—'}% of notional
        (spot ${isNum(c.spot_taker) ? (100 * c.spot_taker).toFixed(3) : '—'}% or ${isNum(c.spot_taker_bnb) ? (100 * c.spot_taker_bnb).toFixed(3) : '—'}% with BNB, perp ${isNum(c.perp_taker) ? (100 * c.perp_taker).toFixed(3) : '—'}%, slippage ${isNum(c.slippage_per_leg) ? (100 * c.slippage_per_leg).toFixed(2) : '—'}% per leg and side), amortised over ${esc(c.hold_days)} days.</p>
      <table><thead><tr>${COLS.map(k => `<th>${esc(k[1])}</th>`).join('')}</tr></thead><tbody>${list.map(r =>
        `<tr class="${esc(r.tone)}">${COLS.map(k => `<td>${esc(r[k[0]])}</td>`).join('')}</tr>`).join('')}</tbody></table>
      <p class="warn">* ${esc(data && data.label)}</p>
      ${miss.length ? `<p class="meta">Missing: ${miss.map(([k, v]) => `${esc(k)} (${esc(v)})`).join(' · ')}</p>` : ''}
      <p class="meta">History and net-of-cost backtest of 11 pre-registered variants: ${esc(data && data.result_doc)}. Sources: ${esc(Object.values((data && data.sources) || {}).join(' | '))}</p>`;
  }

  return { rows, spread, render };
});
