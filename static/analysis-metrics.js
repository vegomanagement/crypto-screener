/* Analysis dashboard: market-metrics view-model (pure). Groups every number into titled
 * cards; each row carries its source · venue · age for the hover title, or '—' plus the
 * reason when the API did not provide it. Never fabricates a value.
 * UMD: window.AnalysisMetricsModel / module.exports.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./analysis-dashboard-model.js'));
  else root.AnalysisMetricsModel = factory(root.AnalysisDashboardModel);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (M) {
  'use strict';
  const { A, F, P, isNum, num, signed, arrow, tone, ageText, VENUES } = M;
  const MARKET = 'Bybit-first USDT aggregate (not venue-pinned)';

  /** One metric row. value null/undefined/'—' → missing with `reason`. */
  function row(label, value, prov, opts) {
    const o = opts || {}, missing = value === null || value === undefined || value === '' || value === '—';
    const where = [prov && prov.source, prov && prov.venue, prov && (isNum(prov.ts) ? ageText(prov.ts, prov.now) : prov.age)].filter(Boolean).join(' · ');
    return { label, value: missing ? '—' : String(value), missing, reason: missing ? (o.reason || 'not provided by the API') : '',
      tone: missing ? '' : (o.tone || ''), countdown: missing ? null : (o.countdown || null), arrow: missing ? '' : (o.arrow || ''), sub: missing ? '' : (o.sub || ''),
      hint: missing ? `${label}: — (${o.reason || 'not provided by the API'})${where ? ` · ${where}` : ''}` : `${label}: ${value}${o.sub ? ` ${o.sub}` : ''}${where ? ` · ${where}` : ''}` };
  }
  const sec = (snap, name, now, venue) => {
    const s = snap && snap[name];
    return s ? { source: s.source, ts: s.ts, venue, now } : { source: '', venue, now };
  };
  const miss = (snap, name, fallback) => (snap && snap.missing && snap.missing[name]) || fallback || `${name} section missing from the snapshot`;
  const word = v => (v === null || v === undefined ? null : String(v).replace(/_/g, ' '));
  const pctOf = (v, d = 2) => (isNum(v) ? `${signed(v, d)}%` : null);

  /** Snapshot cards are only for the chart they describe: stale → every row says why. */
  function snapshotFor(input) {
    if (!input.result) return { snap: null, reason: 'run Analyze to load this chart\u2019s snapshot' };
    if (input.stale) return { snap: null, reason: `analysis is for ${A.keyLabel(input.resultKey)} — re-analyze` };
    return { snap: input.result.snapshot || null, reason: input.result.snapshot ? '' : 'response has no snapshot (older server)' };
  }

  function priceCard(input) {
    const { snap, reason } = snapshotFor(input), now = input.now, venue = VENUES[input.exchange] || input.exchange;
    if (!snap) return { id: 'price', title: 'Price & volatility', rows: ['Price', '24h change', 'ATR 14', 'ATR %', 'Range'].map(l => row(l, null, null, { reason })) };
    const ind = snap.indicators || {}, c = snap.candles || {}, price = snap.price && snap.price.value, atr = ind.atr14;
    const ch = snap.flow && snap.flow.change_24h_pct, d = snap.derivatives || {}, prec = input.precision;
    return { id: 'price', title: 'Price & volatility', note: `Closed candles on ${venue}; 24h change is a ${MARKET}.`, rows: [
      row('Price', A.fmtPrice(price, prec), { source: snap.price && snap.price.source, ts: snap.price && snap.price.ts, venue, now }, { sub: 'last closed bar' }),
      row('24h change', pctOf(ch), { ...sec(snap, 'flow', now), venue: MARKET }, { tone: tone(ch), arrow: arrow(ch), reason: miss(snap, 'flow', 'not in market data') }),
      row('ATR 14', isNum(atr) ? A.fmtPrice(atr, prec) : null, sec(snap, 'indicators', now, venue), { reason: 'fewer than 15 closed bars' }),
      row('ATR %', isNum(atr) && isNum(price) && price ? `${(atr / price * 100).toFixed(2)}%` : null, sec(snap, 'indicators', now, venue), { sub: 'of price', reason: 'needs ATR and price' }),
      row('Range', isNum(c.window_low) && isNum(c.window_high) ? `${A.fmtPrice(c.window_low, prec)} – ${A.fmtPrice(c.window_high, prec)}` : null, sec(snap, 'candles', now, venue),
        { sub: isNum(c.count) ? `${c.count} bars` : '', reason: 'candle window missing' }),
      row('Mark / index', isNum(d.mark_price) ? `${A.fmtPrice(d.mark_price, prec)} / ${isNum(d.index_price) ? A.fmtPrice(d.index_price, prec) : '—'}` : null,
        sec(snap, 'derivatives', now, venue), { reason: miss(snap, 'derivatives') })] };
  }

  /** EMA ribbon of a snapshot's indicators: current shape (ema_periods + ema_fast/mid/slow) or the
   *  legacy 7/14/28 one (ema7/ema14/ema28 + ema_stack_7_14_28) from older results. */
  const RIBBON_NAMES = { 1: ['ema_fast'], 2: ['ema_fast', 'ema_slow'], 3: ['ema_fast', 'ema_mid', 'ema_slow'] };
  function ribbonOf(ind) {
    const i = ind || {};
    if (Array.isArray(i.ema_periods) && i.ema_periods.length) {
      return { periods: i.ema_periods, values: (RIBBON_NAMES[i.ema_periods.length] || []).map(n => i[n]), stack: i.ema_stack };
    }
    const legacy = [7, 14, 28].filter(p => `ema${p}` in i);
    return { periods: legacy, values: legacy.map(p => i[`ema${p}`]), stack: i.ema_stack_7_14_28 };
  }
  const ribbonLabel = r => (r.periods.length ? `EMA ribbon ${r.periods.join('/')}` : 'EMA ribbon');

  function trendCard(input) {
    const { snap, reason } = snapshotFor(input), now = input.now, venue = VENUES[input.exchange] || input.exchange;
    if (!snap) return { id: 'trend', title: 'Trend', rows: ['EMA ribbon', 'HTF bias', 'Regime', 'Structure', 'RSI 14'].map(l => row(l, null, null, { reason })) };
    const ind = snap.indicators || {}, h = snap.htf_bias, rg = snap.regime, s = snap.structure, prec = input.precision;
    const rb = ribbonOf(ind), stack = rb.stack, stackTone = stack === 'bullish' ? 'up' : stack === 'bearish' ? 'down' : '';
    const vs200 = isNum(ind.close) && isNum(ind.ema200) ? (ind.close >= ind.ema200 ? 'above' : 'below') : null;
    const ev = s && s.recent_events && s.recent_events.length ? s.recent_events[s.recent_events.length - 1] : null;
    const dirTone = d => (/bull|long|up/.test(String(d)) ? 'up' : /bear|short|down/.test(String(d)) ? 'down' : '');
    const dirArrow = d => (dirTone(d) === 'up' ? '▲' : dirTone(d) === 'down' ? '▼' : '');
    return { id: 'trend', title: 'Trend', note: `EMA/RSI/structure from ${venue} closed candles; HTF bias and regime from a ${MARKET}.`, rows: [
      row(ribbonLabel(rb), word(stack), sec(snap, 'indicators', now, venue), { tone: stackTone, arrow: dirArrow(stack),
        sub: rb.values.length && rb.values.every(isNum) ? rb.values.map(v => A.fmtPrice(v, prec)).join(' / ') : '',
        reason: rb.periods.length < 2 ? 'stack needs at least two ribbon periods' : `fewer than ${Math.max(...rb.periods)} closed bars` }),
      row('Price vs EMA 200', vs200, sec(snap, 'indicators', now, venue), { tone: vs200 === 'above' ? 'up' : vs200 === 'below' ? 'down' : '', arrow: vs200 === 'above' ? '▲' : vs200 === 'below' ? '▼' : '',
        sub: isNum(ind.ema200) ? `EMA200 ${A.fmtPrice(ind.ema200, prec)}` : '', reason: 'fewer than 200 closed bars' }),
      row('HTF bias', h ? `${word(h.direction)}${h.strength !== undefined && h.strength !== null ? ` · strength ${h.strength}` : ''}` : null,
        { ...sec(snap, 'htf_bias', now), venue: MARKET }, { tone: h && dirTone(h.direction), arrow: h && dirArrow(h.direction), reason: miss(snap, 'htf_bias') }),
      row('Regime', rg ? [rg.phase, rg.bias, rg.zone].filter(Boolean).map(word).join(' · ') : null, { ...sec(snap, 'regime', now), venue: MARKET },
        { sub: rg && rg.range_state ? `range ${word(rg.range_state)}${isNum(rg.pos_in_range) ? ` @ ${(rg.pos_in_range * 100).toFixed(0)}%` : ''}` : '', tone: rg && dirTone(rg.bias), reason: miss(snap, 'regime') }),
      row('Structure', s ? word(s.trend) : null, sec(snap, 'structure', now, venue), { tone: s && dirTone(s.trend), arrow: s && dirArrow(s.trend),
        sub: ev ? `last ${ev.kind} ${ev.direction} @ ${A.fmtPrice(ev.level, prec)}` : '', reason: miss(snap, 'structure') }),
      row('RSI 14', isNum(ind.rsi14) ? ind.rsi14.toFixed(1) : null, sec(snap, 'indicators', now, venue),
        { sub: isNum(ind.rsi14) ? (ind.rsi14 >= 70 ? 'overbought zone' : ind.rsi14 <= 30 ? 'oversold zone' : 'mid-range') : '', reason: 'fewer than 15 closed bars' })] };
  }

  /** Funding card from the pane's own /api/funding cache (no extra request). */
  function fundingCard(input) {
    const d = input.funding, now = input.now;
    if (!d || d.error) {
      const reason = !d ? 'funding not loaded yet for this chart' : `funding unavailable: ${d.detail || d.error}`;
      return { id: 'funding', title: 'Funding', rows: ['Last → predicted', 'Annualised (predicted)', 'Settles in'].map(l => row(l, null, null, { reason })), cross: [], spark: null };
    }
    const prov = { source: `GET /api/funding (${VENUES[d.exchange] || d.exchange} public endpoints)`, ts: d.as_of, venue: VENUES[d.exchange] || d.exchange, now };
    const last = d.last_settled && d.last_settled.rate, pred = d.predicted && d.predicted.rate, ann = d.annualized || {}, settles = d.predicted && d.predicted.settles_at;
    const pn = num(pred), tr = d.trend;
    return { id: 'funding', title: 'Funding', note: 'Predicted is the venue\u2019s live estimate; it changes until settlement. Annualised = rate × 24/interval × 365 (simple).', rows: [
      row('Last → predicted', `${F.pct(last)} → ${F.pct(pred)}`, prov, { tone: tone(pn), arrow: arrow(pn), sub: d.interval_hours ? `${d.interval_hours.value}h interval` : 'interval unknown' }),
      row('Annualised (predicted)', ann.predicted === null || ann.predicted === undefined ? null : F.annualPct(ann.predicted), prov,
        { sub: ann.last_settled !== undefined && ann.last_settled !== null ? `last ${F.annualPct(ann.last_settled)}` : '', reason: (d.missing && d.missing.annualized) || 'no predicted rate' }),
      row('Settles in', isNum(settles) ? F.countdown(settles - now) : null, prov, { countdown: isNum(settles) ? settles : null, sub: isNum(settles) ? M.utc(settles) : '', reason: 'settlement time not published' }),
      row('Trend', tr ? `${word(tr.direction)} · ${tr.positive}/${tr.n} positive` : null, prov, { reason: 'not enough settlements' })],
    cross: F.crossRows(d), crossNote: (d.missing && d.missing.cross_venue) || '', spread: d.cross_venue && d.cross_venue.annualized_spread ? F.annualPct(d.cross_venue.annualized_spread) : '',
    spark: F.sparkline(d.history && d.history.items, 300, 40) };
  }

  return { row, sec, snapshotFor, priceCard, trendCard, fundingCard, ribbonOf, MARKET, M };
});
