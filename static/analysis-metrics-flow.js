/* Analysis dashboard: positioning / liquidations / whales / liquidity / sentiment cards (pure).
 * Positioning, liquidations and whales come from the live /api/positioning(+/whales) payloads
 * via PositioningPanel.viewModel (single source of truth); liquidity/volume/sentiment come
 * from the analysed snapshot. UMD: window.AnalysisFlowModel / module.exports.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./analysis-metrics.js'));
  else root.AnalysisFlowModel = factory(root.AnalysisMetricsModel);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (X) {
  'use strict';
  const { row, sec, snapshotFor, MARKET, M } = X;
  const { A, P, isNum, num, signed, arrow, tone, VENUES } = M;
  const word = v => (v === null || v === undefined ? null : String(v).replace(/_/g, ' '));

  /** Positioning payload state → reason shown on every row when it cannot be used. */
  function posReason(pos) {
    if (!pos) return 'positioning not loaded yet (loads when the dashboard is on screen)';
    if (pos.error) return `positioning unavailable: ${pos.error}`;
    return '';
  }
  const metaOf = (m, now) => ({ source: m && m.source, ts: m && m.timestamp, venue: VENUES[m && m.venue] || (m && m.venue), now });

  function positioningCard(input) {
    const pos = input.positioning, now = input.now, reason = posReason(pos);
    const labels = ['Top traders (positions)', 'Top traders (accounts)', 'Crowd (all accounts)', 'Taker buy/sell', 'Open interest', 'OI change 1h', 'OI change 4h', 'OI change 24h', 'Basis'];
    if (reason) return { id: 'positioning', title: 'Positioning', rows: labels.map(l => row(l, null, null, { reason })), signals: [], bars: [] };
    const vm = P.viewModel(pos, null, now), t = pos.top_traders || {}, oi = pos.open_interest || {};
    const bars = [['Top traders (positions)', t.position], ['Top traders (accounts)', t.account], ['Crowd (all accounts)', pos.crowd]].map(([label, m]) => {
      const share = m && m.value !== null && m.value !== undefined ? num(m.long) : null;
      return { label, share: isNum(share) ? share : null, ratio: m && m.value, reason: (m && m.reason) || 'no data',
        hint: isNum(share) ? `${label}: ${(share * 100).toFixed(1)}% long · L/S ${m.value} · ${m.source} · ${m.period} · ${M.ageText(m.timestamp, now)}` : `${label}: — (${(m && m.reason) || 'no data'})` };
    });
    const tk = pos.taker || {}, cur = oi.current || {}, ch = oi.changes || {}, bs = pos.basis || {};
    const oiRow = w => { const c = ch[w] || {}, v = num(c.value);
      return row(`OI change ${w}`, isNum(v) ? `${signed(v, 2)}%` : null, metaOf(c, now), { tone: tone(v), arrow: arrow(v),
        sub: c.price_pct !== null && c.price_pct !== undefined ? `price ${signed(num(c.price_pct), 2)}%` : '', reason: c.reason || 'no OI history' }); };
    const takerShare = vm.taker.available && isNum(vm.taker.share) ? vm.taker.share : null;
    return { id: 'positioning', title: 'Positioning', note: `${VENUES[pos.exchange] || pos.exchange} · period ${pos.period}. Ratios are descriptive, not signals.`, bars,
      rows: [
        row('Taker buy/sell', vm.taker.available ? `${vm.taker.ratio}` : null, metaOf(tk, now), { sub: isNum(takerShare) ? `${(takerShare * 100).toFixed(1)}% buy volume` : '',
          tone: isNum(takerShare) ? tone(takerShare - 0.5) : '', arrow: isNum(takerShare) ? arrow(takerShare - 0.5) : '', reason: tk.reason || 'no taker data' }),
        row('Open interest', cur.value === null || cur.value === undefined ? null : `${P.fmtUsd(cur.value_usd)}`, metaOf(cur, now),
          { sub: `${Number(cur.value).toLocaleString('en-US', { maximumFractionDigits: 0 })} ${cur.unit || ''}`.trim(), reason: cur.reason || 'no OI' }),
        oiRow('1h'), oiRow('4h'), oiRow('24h'),
        row('Basis', bs.value === null || bs.value === undefined ? null : `${signed(num(bs.value), 2)}`, metaOf(bs, now),
          { sub: bs.basis_rate !== undefined && bs.basis_rate !== null ? `rate ${signed(num(bs.basis_rate) * 100, 3)}%` : '', tone: tone(num(bs.value)), reason: bs.reason || 'no basis for this venue' })],
      signals: vm.signals.map(s => ({ label: s.label, state: s.state, hint: `${s.formula || ''}${s.reason ? ` — ${s.reason}` : ''}` })) };
  }

  function liquidationsCard(input) {
    const pos = input.positioning, now = input.now, reason = posReason(pos);
    if (reason) return { id: 'liquidations', title: 'Liquidations', available: false, reason, rows: [], largest: [] };
    const L = P.viewModel(pos, null, now).liquidations, liq = pos.liquidations || {};
    if (!L.available) return { id: 'liquidations', title: 'Liquidations', available: false, reason: L.reason, label: L.label, rows: [], largest: [] };
    return { id: 'liquidations', title: 'Liquidations', available: true, label: L.label, lowerBound: L.lowerBound, status: L.status,
      hint: `${liq.source || ''} · ${VENUES[liq.venue] || liq.venue || ''}`, rows: L.rows, largest: L.largest.slice(0, 5) };
  }

  /** Whales card: every tracked position for this coin (the panel shows 6; here up to 25). */
  function whalesCard(input) {
    const w = input.whales, now = input.now;
    if (!w || w.error) return { id: 'whales', title: 'Hyperliquid whales', available: false, reason: !w ? 'not loaded yet (loads when the dashboard is on screen)' : `unavailable: ${w.error}`, positions: [], changes: [] };
    const vm = P.viewModel({ symbol: '', exchange: '', period: '' }, w, now, { whaleRows: 25 }).whales;
    return { id: 'whales', title: 'Hyperliquid whales', ...vm, coin: w.aggregate && w.aggregate.coin,
      hint: `${vm.label}${vm.poll ? ` · ${vm.poll}` : ''}` };
  }

  function liquidityCard(input) {
    const { snap, reason } = snapshotFor(input), now = input.now, venue = VENUES[input.exchange] || input.exchange;
    const labels = ['CVD (1h, estimated)', 'Order book imbalance', 'Footprint delta (last bar)', 'Footprint coverage'];
    if (!snap) return { id: 'liquidity', title: 'Liquidity & volume', rows: labels.map(l => row(l, null, null, { reason })), pools: [] };
    const f = snap.flow || {}, cvd = f.cvd_1h, ob = snap.orderbook, fp = snap.footprint, prec = input.precision;
    const bars = (fp && fp.bars) || [], last = bars[bars.length - 1];
    const cov = bars.length ? `${bars.filter(b => b.coverage === 'complete').length}/${bars.length} bars complete` : null;
    const imb = ob && num(ob.imbalance);
    const ref = snap.liquidity && snap.liquidity.reference_price;
    const pools = ((snap.liquidity && snap.liquidity.pools) || []).slice().sort((a, b) => Math.abs(a.dist_pct) - Math.abs(b.dist_pct)).slice(0, 6)
      .map(p => ({ kind: word(p.kind), side: p.side, price: A.fmtPrice(p.price, prec), dist: `${signed(p.dist_pct, 2)}%`, arrow: arrow(p.price - ref), strength: p.strength }));
    return { id: 'liquidity', title: 'Liquidity & volume', note: `CVD and pools from a ${MARKET}; order book and footprint from ${venue}.`, pools,
      poolsReason: pools.length ? '' : (snap.missing && snap.missing.liquidity) || 'no pools found near price',
      rows: [
        row('CVD (1h, estimated)', cvd ? word(cvd.trend) : null, { ...sec(snap, 'flow', now), venue: MARKET }, { tone: cvd && /bull|up|rising/.test(cvd.trend) ? 'up' : cvd && /bear|down|falling/.test(cvd.trend) ? 'down' : '',
          sub: cvd ? `price ${word(cvd.price_trend) || '—'}${cvd.divergence ? ` · divergence ${word(cvd.divergence)}` : ''}` : '', reason: (snap.missing && snap.missing.flow) || 'CVD trend unknown' }),
        row('Order book imbalance', isNum(imb) ? `${signed(imb * 100, 1)}%` : null, sec(snap, 'orderbook', now, venue), { tone: tone(imb), arrow: arrow(imb),
          sub: ob ? `top ${ob.levels_per_side}/side · bid wall ${A.fmtPrice(ob.bid_wall, prec)} · ask wall ${A.fmtPrice(ob.ask_wall, prec)}` : '', reason: (snap.missing && snap.missing.orderbook) || 'no depth snapshot' }),
        row('Footprint delta (last bar)', last && isNum(last.delta) ? signed(last.delta, 2) : null, sec(snap, 'footprint', now, venue), { tone: tone(last && last.delta), arrow: arrow(last && last.delta),
          sub: last && isNum(last.total) ? `of ${last.total.toLocaleString('en-US', { maximumFractionDigits: 2 })} traded · POC ${A.fmtPrice(last.poc, prec)}` : '', reason: (snap.missing && snap.missing.footprint) || 'no footprint bars' }),
        row('Footprint coverage', cov, sec(snap, 'footprint', now, venue), { reason: (snap.missing && snap.missing.footprint) || 'no footprint bars' })] };
  }

  function sentimentCard(input) {
    const { snap, reason } = snapshotFor(input), now = input.now;
    if (!snap) return { id: 'sentiment', title: 'Sentiment & session', rows: ['Fear & greed', 'Session'].map(l => row(l, null, null, { reason })) };
    const fg = snap.flow && snap.flow.fear_greed, s = snap.session;
    const v = fg && num(fg.value);
    return { id: 'sentiment', title: 'Sentiment & session', gauge: isNum(v) ? v : null, rows: [
      row('Fear & greed', isNum(v) ? `${v} / 100` : null, { source: fg && fg.source, ts: snap.flow && snap.flow.ts, venue: 'market-wide', now },
        { sub: fg ? word(fg.label) : '', tone: isNum(v) ? (v >= 55 ? 'up' : v <= 45 ? 'down' : '') : '', reason: 'alternative.me index not available' }),
      row('Session', s ? (s.active_killzone ? word(s.active_killzone) : 'outside killzones') : null, sec(snap, 'session', now, 'UTC'), { sub: s ? s.describe : '', reason: 'session section missing' })] };
  }

  /** All metric cards in reading order. */
  function metricCards(input) {
    return [X.priceCard(input), X.trendCard(input), X.fundingCard(input), positioningCard(input), liquidationsCard(input),
      whalesCard(input), liquidityCard(input), sentimentCard(input)];
  }

  return { positioningCard, liquidationsCard, whalesCard, liquidityCard, sentimentCard, metricCards, posReason, X, M };
});
