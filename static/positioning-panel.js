/* Positioning panel: pure view-model + DOM renderer, mountable into any container.
 * UMD: window.PositioningPanel in browsers, module.exports under node (view-model tests).
 * Data comes from GET /api/positioning and /api/positioning/whales. Descriptive only — no trade signals.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.PositioningPanel = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const num = v => (v === null || v === undefined || v === '' ? null : Number(v));
  const isNum = v => typeof v === 'number' && Number.isFinite(v);

  function fmtPct(share, digits = 1) { const n = num(share); return isNum(n) ? `${(n * 100).toFixed(digits)}%` : '—'; }
  function fmtSigned(v, digits = 2, suffix = '') {
    const n = num(v); if (!isNum(n)) return '—';
    return `${n > 0 ? '+' : ''}${n.toFixed(digits)}${suffix}`;
  }
  function fmtUsd(v) {
    const n = num(v); if (!isNum(n)) return '—';
    const a = Math.abs(n), s = n < 0 ? '−' : '';
    if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(2)}M`;
    if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(1)}K`;
    return `${s}$${a.toFixed(0)}`;
  }
  function fmtTime(ms) {
    if (!isNum(ms)) return '—';
    return new Date(ms).toISOString().slice(11, 16) + ' UTC';
  }
  function fmtAge(ms, now) {
    if (!isNum(ms) || !isNum(now)) return '';
    const s = Math.max(0, Math.round((now - ms) / 1000));
    return s < 90 ? `${s}s ago` : s < 5400 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;
  }
  /** Price display: 6 significant digits, no exponent (raw exact value stays in the API). */
  function fmtPx(v) {
    const n = num(v); if (!isNum(n)) return '—';
    const a = Math.abs(n), d = a >= 1e5 ? 0 : Math.max(0, 5 - Math.floor(Math.log10(a || 1)));
    return n.toFixed(Math.min(d, 8));
  }
  const short = addr => (typeof addr === 'string' && addr.length > 12 ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : addr || '');

  /** A bar row: long share 0..1 split; missing → null share with the reason shown instead. */
  function bar(label, metric, extra) {
    if (!metric || metric.value === null || metric.value === undefined) {
      return { label, share: null, text: '—', reason: (metric && metric.reason) || 'no data', meta: '' };
    }
    const share = num(metric.long);
    return { label, share: isNum(share) ? share : null, text: `${fmtPct(share)} long · L/S ${metric.value}`,
      reason: null, meta: [metric.venue, metric.period, fmtTime(metric.timestamp)].filter(Boolean).join(' · '),
      ...(extra || {}) };
  }

  function liquidationsVM(liq, now) {
    if (!liq) return { available: false, reason: 'not requested', rows: [], largest: [] };
    if (!liq.windows) return { available: false, reason: liq.reason || 'no data', rows: [], largest: [], label: liq.label };
    const rows = ['1h', '4h', '24h'].map(w => {
      const x = liq.windows[w], l = num(x.long_usd), s = num(x.short_usd), t = l + s;
      return { window: w, long: fmtUsd(l), short: fmtUsd(s), longCount: x.long_count, shortCount: x.short_count,
        share: t > 0 ? l / t : null, complete: !!x.complete_coverage,
        coverage: x.complete_coverage ? 'full window' : `covered ${Math.round(x.covered_ms / 60000)} of ${Math.round(x.window_ms / 60000)} min` };
    });
    const largest = (liq.largest || []).map(e => ({ side: e.side, usd: fmtUsd(e.notional_usd), price: fmtPx(e.price), time: fmtTime(e.ts), age: fmtAge(e.ts, now) }));
    const c = liq.collector || {};
    return { available: true, lowerBound: liq.lower_bound === true, label: liq.label, rows, largest,
      status: c.connected ? `connected · ${c.events || 0} events stored` : (c.error ? `reconnecting: ${c.error}` : 'connecting…') };
  }

  function whalesVM(w, now, limit) {
    if (!w) return { available: false, reason: 'not loaded', changes: [], positions: [] };
    const a = w.aggregate;
    const head = { label: w.label || 'Hyperliquid top-N accounts by account value', poll: w.last_poll ?
      `poll #${w.last_poll.poll_number} · ${w.last_poll.ok}/${w.last_poll.requested} accounts · ${(w.last_poll.duration_ms / 1000).toFixed(0)} s · ${fmtAge(w.last_poll.finished_at, now)}` : '' };
    if (!a) return { ...head, available: false, reason: w.reason || 'warming up', changes: [], positions: [] };
    const l = num(a.long_notional), s = num(a.short_notional), t = l + s;
    return { ...head, available: true, share: t > 0 ? l / t : null, long: fmtUsd(l), short: fmtUsd(s), net: fmtUsd(a.net_notional),
      longCount: a.long_count, shortCount: a.short_count, avgLong: fmtPx(a.avg_entry_long), avgShort: fmtPx(a.avg_entry_short),
      empty: a.long_count + a.short_count === 0,
      positions: (a.positions || []).slice(0, limit || 6).map(p => ({ who: short(p.address), side: p.side, usd: fmtUsd(p.notional), entry: fmtPx(p.entry), liq: fmtPx(p.liquidation_px), lev: p.leverage })),
      changes: (w.changes || []).slice(0, 8).map(c => ({ kind: c.kind, side: c.side, who: short(c.address), usd: fmtUsd(c.delta_notional), age: fmtAge(c.ts, now), detail: `${c.from_szi} → ${c.to_szi}` })) };
  }

  function flowsVM(f) {
    if (!f || !f.available) return { available: false, reason: (f && f.reason) || 'requires CoinGlass API key (paid)' };
    return { available: true, label: f.label, value: fmtSigned(f.value, 2, ` ${f.unit}`), totals: f.totals,
      exchanges: (f.exchanges || []).slice(0, 6).map(e => ({ name: e.exchange, d1: fmtSigned(e.change_1d, 2), d7: fmtSigned(e.change_7d, 2) })) };
  }

  /** Pure: API snapshot (+ optional whales response) → render-ready strings. */
  function viewModel(snap, whales, now, opts) {
    if (!snap) return { error: 'no data' };
    if (snap.error) return { error: snap.error };
    const t = snap.top_traders || {}, oi = snap.open_interest || {}, cur = oi.current || {};
    const taker = snap.taker || {};
    const buy = num(taker.buy_volume), sell = num(taker.sell_volume);
    return {
      title: `${snap.symbol} · ${snap.exchange} · ${snap.period}`,
      updated: fmtTime(snap.generated_at),
      topVsCrowd: [bar('Top traders · positions', t.position), bar('Top traders · accounts', t.account), bar('All accounts (crowd)', snap.crowd)],
      taker: taker.value === null || taker.value === undefined ? { available: false, reason: taker.reason }
        : { available: true, ratio: taker.value, share: buy + sell > 0 ? buy / (buy + sell) : null, meta: `${taker.period} · ${fmtTime(taker.timestamp)}`, buy: taker.buy_volume, sell: taker.sell_volume },
      oi: { value: cur.value === null || cur.value === undefined ? '—' : cur.value, usd: fmtUsd(cur.value_usd), reason: cur.reason || null,
        changes: ['1h', '4h', '24h'].map(w => { const c = (oi.changes || {})[w] || {};
          return { window: w, pct: fmtSigned(c.value, 2, '%'), price: fmtSigned(c.price_pct, 2, '%'), dir: Math.sign(num(c.value) || 0), reason: c.value == null ? c.reason : null }; }) },
      basis: snap.basis && snap.basis.value != null ? { value: snap.basis.value, funding: fmtSigned(num(snap.basis.funding_rate) * 100, 4, '%'), source: snap.basis.source } : { reason: snap.basis && snap.basis.reason },
      signals: (snap.signals || []).map(s => ({ id: s.id, label: s.label, state: s.state ? s.state.replace(/_/g, ' ') : 'n/a', formula: s.formula, reason: s.reason })),
      liquidations: liquidationsVM(snap.liquidations, now),
      whales: whalesVM(whales || snap.whales, now, opts && opts.whaleRows),
      flows: flowsVM(snap.exchange_flows),
      warnings: snap.warnings || [],
    };
  }

  // ---------- DOM rendering (browser only) ----------
  const esc = s => String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const CSS = `.pp{font:12px/1.4 ui-sans-serif,system-ui,sans-serif;color:#d8dfeb;background:#10141c;border:1px solid #242b3a;border-radius:6px;padding:10px;display:grid;gap:10px}
.pp h3{margin:0 0 4px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#778397}.pp .pp-head{display:flex;justify-content:space-between;gap:8px;font-weight:600}
.pp .pp-muted{color:#778397;font-size:11px}.pp .pp-bar{position:relative;height:16px;background:#ef5466;border-radius:3px;overflow:hidden;margin:2px 0}
.pp .pp-bar>i{position:absolute;inset:0 auto 0 0;background:#00c087}.pp .pp-bar.na{background:#242b3a}.pp .pp-row{margin-bottom:6px}
.pp .pp-row b{font-weight:500}.pp table{width:100%;border-collapse:collapse}.pp td,.pp th{padding:2px 4px;text-align:right;white-space:nowrap}
.pp td:first-child,.pp th:first-child{text-align:left}.pp th{color:#778397;font-weight:500}.pp .up{color:#00c087}.pp .dn{color:#ef5466}
.pp .pp-warn{color:#f5a623;font-size:11px}.pp section{border-top:1px solid #242b3a;padding-top:8px}.pp .pp-chips{display:flex;flex-wrap:wrap;gap:4px}
.pp .pp-chip{border:1px solid #242b3a;border-radius:10px;padding:1px 8px;background:#171d28}`;

  function ensureCss(doc) {
    if (doc.getElementById('positioning-panel-css')) return;
    const st = doc.createElement('style'); st.id = 'positioning-panel-css'; st.textContent = CSS; doc.head.appendChild(st);
  }
  const barHtml = share => share === null ? '<div class="pp-bar na"></div>' : `<div class="pp-bar"><i style="width:${(share * 100).toFixed(1)}%"></i></div>`;
  const cls = d => (d > 0 ? 'up' : d < 0 ? 'dn' : '');

  function renderHtml(vm) {
    if (vm.error) return `<div class="pp-warn">${esc(vm.error)}</div>`;
    const top = vm.topVsCrowd.map(b => `<div class="pp-row"><div><b>${esc(b.label)}</b> <span class="pp-muted">${esc(b.reason ? b.reason : b.text)}</span></div>${barHtml(b.share)}<div class="pp-muted">${esc(b.meta)}</div></div>`).join('');
    const tk = vm.taker.available ? `<div>Buy/Sell ratio <b>${esc(vm.taker.ratio)}</b> <span class="pp-muted">${esc(vm.taker.meta)}</span></div>${barHtml(vm.taker.share)}<div class="pp-muted">taker buy ${esc(vm.taker.buy)} / sell ${esc(vm.taker.sell)} (base)</div>` : `<div class="pp-muted">${esc(vm.taker.reason)}</div>`;
    const oi = `<div>OI <b>${esc(vm.oi.value)}</b> <span class="pp-muted">${esc(vm.oi.usd)}${vm.oi.reason ? ' · ' + esc(vm.oi.reason) : ''}</span></div><table><tr><th>Window</th><th>OI Δ</th><th>Price Δ</th></tr>${vm.oi.changes.map(c => `<tr><td>${c.window}</td><td class="${cls(c.dir)}">${esc(c.pct)}</td><td>${esc(c.reason ? c.reason : c.price)}</td></tr>`).join('')}</table>${vm.basis.value ? `<div class="pp-muted">basis ${esc(vm.basis.value)} · funding ${esc(vm.basis.funding)}</div>` : ''}`;
    const L = vm.liquidations;
    const liq = L.available ? `<div class="pp-warn">${esc(L.label)}</div><table><tr><th>Window</th><th>Longs liq.</th><th>Shorts liq.</th><th>Coverage</th></tr>${L.rows.map(r => `<tr><td>${r.window}</td><td class="dn">${esc(r.long)} (${r.longCount})</td><td class="up">${esc(r.short)} (${r.shortCount})</td><td class="pp-muted">${esc(r.coverage)}</td></tr>`).join('')}</table><div class="pp-muted">${esc(L.status)}</div>${L.largest.length ? `<div class="pp-muted">Largest: ${L.largest.map(e => `${esc(e.side)} ${esc(e.usd)} @ ${esc(e.price)} ${esc(e.age)}`).join(' · ')}</div>` : ''}` : `<div class="pp-muted">${esc(L.reason)}</div>`;
    const W = vm.whales;
    const wh = `<div class="pp-muted">${esc(W.label)}${W.poll ? ' · ' + esc(W.poll) : ''}</div>` + (W.available ? (W.empty ? '<div class="pp-muted">No tracked account holds this coin.</div>' : `${barHtml(W.share)}<div><span class="up">Long ${esc(W.long)} · ${W.longCount}</span> / <span class="dn">Short ${esc(W.short)} · ${W.shortCount}</span> <span class="pp-muted">net ${esc(W.net)} · avg entry L ${esc(W.avgLong)} / S ${esc(W.avgShort)}</span></div><table><tr><th>Account</th><th>Side</th><th>Notional</th><th>Entry</th><th>Liq.</th></tr>${W.positions.map(p => `<tr><td>${esc(p.who)}</td><td class="${p.side === 'long' ? 'up' : 'dn'}">${esc(p.side)} ${esc(p.lev)}x</td><td>${esc(p.usd)}</td><td>${esc(p.entry)}</td><td>${esc(p.liq)}</td></tr>`).join('')}</table>`) + `<div class="pp-muted">Recent changes: ${W.changes.length ? W.changes.map(c => `${esc(c.who)} ${esc(c.kind)} ${esc(c.side)} ${esc(c.usd)} (${esc(c.age)})`).join(' · ') : 'none detected yet'}</div>` : `<div class="pp-muted">${esc(W.reason)}</div>`);
    const F = vm.flows;
    const fl = F.available ? `<div>1d net exchange balance change <b>${esc(F.value)}</b></div><div class="pp-muted">${esc(F.label)}</div>` : `<div class="pp-muted">${esc(F.reason)}</div>`;
    const sig = `<div class="pp-chips">${vm.signals.map(s => `<span class="pp-chip" title="${esc(s.formula)}${s.reason ? ' — ' + esc(s.reason) : ''}">${esc(s.label)}: ${esc(s.state)}</span>`).join('')}</div><div class="pp-muted">Descriptive labels, not trade signals (hover for formula).</div>`;
    const sec = (title, body) => `<section><h3>${title}</h3>${body}</section>`;
    return `<div class="pp-head"><span>${esc(vm.title)}</span><span class="pp-muted">${esc(vm.updated)}</span></div>${sig}` +
      sec('Top traders vs crowd', top) + sec('Taker flow', tk) + sec('Open interest', oi) +
      sec('Liquidations (lower bound)', liq) + sec('HL whales', wh) + sec('Exchange flows (on-chain)', fl) +
      (vm.warnings.length ? `<div class="pp-warn">${vm.warnings.map(esc).join('<br>')}</div>` : '');
  }

  /** Mount into `el`; polls the API. Returns {refresh, update(opts), destroy}.
   *  opts.visible() (optional): timer ticks are skipped while it returns false (hidden tab, closed section). */
  function mount(el, opts) {
    const o = Object.assign({ exchange: 'binance', symbol: 'BTCUSDT', period: '1h', owner: 'positioning', intervalMs: 30000, visible: null, fetch: (typeof fetch !== 'undefined' ? fetch.bind(globalThis) : null) }, opts || {});
    const doc = el.ownerDocument; ensureCss(doc);
    const box = doc.createElement('div'); box.className = 'pp'; el.appendChild(box);
    let timer = null, alive = true, seq = 0;
    async function refresh() {
      const my = ++seq;
      const q = new URLSearchParams({ exchange: o.exchange, symbol: o.symbol, period: o.period, owner: o.owner });
      const coin = o.coin || o.symbol.replace(/^1000/, '').replace(/(USDT|USDC)$/, '');
      try {
        const [snap, whales] = await Promise.all([
          o.fetch(`/api/positioning?${q}`).then(r => r.json()),
          o.fetch(`/api/positioning/whales?${new URLSearchParams({ coin, owner: o.owner })}`).then(r => r.json()).catch(() => null)]);
        if (alive && my === seq) box.innerHTML = renderHtml(viewModel(snap, whales && !whales.error ? whales : null, Date.now()));
      } catch (err) { if (alive && my === seq) box.innerHTML = renderHtml({ error: `positioning unavailable: ${err.message || err}` }); }
    }
    refresh(); timer = setInterval(() => { if (!o.visible || o.visible()) refresh(); }, o.intervalMs);
    return { refresh, update(next) { Object.assign(o, next); if (!('coin' in (next || {}))) delete o.coin; box.innerHTML = '<div class="pp-muted">Loading…</div>'; refresh(); },
      destroy() { alive = false; seq += 1; clearInterval(timer); box.remove(); } };
  }

  return { viewModel, renderHtml, mount, fmtUsd, fmtPct, fmtSigned, fmtPx, fmtAge, fmtTime };
});
