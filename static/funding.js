/* Funding display helpers shared by /ui (pane chip + analyst card) and /scalp.
 * Data comes only from this app's GET /api/funding (server normalises venue responses).
 * "Predicted" is the venue's live estimate for the current period; it changes until settlement.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(root);
  else root.WorkspaceFunding = factory(root);
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const VENUES = { binance: 'Binance', bybit: 'Bybit', hyperliquid: 'Hyperliquid' };
  const pad = n => String(n).padStart(2, '0');

  /** "0.00003726" → "0.0037%" (display only; exact strings stay on the server). */
  function pct(rate, digits = 4) {
    const n = Number(rate);
    if (rate === null || rate === undefined || rate === '' || !Number.isFinite(n)) return '—';
    return `${(n * 100).toFixed(digits)}%`;
  }
  function annualPct(value) {
    const n = Number(value);
    return value === null || value === undefined || !Number.isFinite(n) ? '—' : `${(n * 100).toFixed(2)}%/yr`;
  }
  function countdown(ms) {
    if (!Number.isFinite(ms) || ms <= 0) return '00:00:00';
    const s = Math.floor(ms / 1000);
    return `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;
  }
  function signOf(rate) {
    const n = Number(rate);
    return rate === null || rate === undefined || !Number.isFinite(n) ? 'none' : n > 0 ? 'pos' : n < 0 ? 'neg' : 'zero';
  }

  /** Pane-header chip: 'F 0.0037% → 0.0030% · 02:13:40', coloured by the predicted sign. */
  function chipModel(data, now) {
    if (!data || data.error) return null;
    const last = data.last_settled && data.last_settled.rate, pred = data.predicted && data.predicted.rate;
    const settles = data.predicted && data.predicted.settles_at;
    const hours = data.interval_hours && data.interval_hours.value;
    const ann = data.annualized || {};
    const text = `F ${pct(last)} → ${pct(pred)}${Number.isFinite(settles) ? ` · ${countdown(settles - now)}` : ''}`;
    const title = [
      `Funding · ${VENUES[data.exchange] || data.exchange} ${data.symbol}`,
      `Last settled: ${pct(last)} (${annualPct(ann.last_settled)})`,
      `Predicted: ${pct(pred)} (${annualPct(ann.predicted)}) — live estimate, changes until settlement`,
      `Interval: ${hours ? `${hours}h` : 'unknown'} · annualised = rate × 24/interval × 365 (simple)`,
      Number.isFinite(settles) ? `Settles: ${new Date(settles).toISOString().replace('.000Z', 'Z')}` : 'Settlement time unknown'
    ].join('\n');
    return { text, title, sign: signOf(pred ?? last) };
  }

  /** Cross-venue rows (from HL predictedFundings) with the current venue marked. */
  function crossRows(data) {
    const cv = data && data.cross_venue;
    if (!cv || !Array.isArray(cv.rows)) return [];
    const order = ['binance', 'bybit', 'hyperliquid'];
    return cv.rows.slice().sort((a, b) => order.indexOf(a.venue) - order.indexOf(b.venue)).map(r => ({
      venue: VENUES[r.venue] || r.venue, self: r.venue === data.exchange, rate: pct(r.rate), interval: r.interval_hours ? `${r.interval_hours}h` : '—',
      annual: annualPct(r.annualized), stale: !!r.stale, sign: signOf(r.rate)
    }));
  }

  /** SVG polyline points for settled history; zeroY is the 0% baseline (null if out of range). */
  function sparkline(items, width = 220, height = 34) {
    const rates = (items || []).map(i => Number(i.rate)).filter(Number.isFinite);
    if (rates.length < 2) return null;
    const lo = Math.min(0, ...rates), hi = Math.max(0, ...rates), span = hi - lo || 1;
    const y = v => +(height - 2 - ((v - lo) / span) * (height - 4)).toFixed(1);
    const step = width / (rates.length - 1);
    return { points: rates.map((v, i) => `${+(i * step).toFixed(1)},${y(v)}`).join(' '), zeroY: y(0), width, height,
      n: rates.length, min: Math.min(...rates), max: Math.max(...rates) };
  }

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }

  /** HTML for the analyst card's Funding section (all values escaped). */
  function sectionHtml(data, now) {
    if (!data) return '<p class="funding-empty">Funding: loading…</p>';
    if (data.error) return `<p class="funding-empty">Funding unavailable: ${esc(data.detail || data.error)}</p>`;
    const chip = chipModel(data, now), ann = data.annualized || {}, trend = data.trend;
    const spark = sparkline(data.history && data.history.items);
    const rows = crossRows(data).map(r => `<tr class="${r.self ? 'is-self' : ''}${r.stale ? ' is-stale' : ''}"><td>${esc(r.venue)}</td><td class="sign-${r.sign}">${esc(r.rate)}</td><td>${esc(r.interval)}</td><td>${esc(r.annual)}${r.stale ? ' <abbr title="Settlement time in the published prediction has already passed">stale</abbr>' : ''}</td></tr>`).join('');
    return `<h3>Funding <small>${esc(data.interval_hours ? `${data.interval_hours.value}h interval` : 'interval unknown')}</small></h3>
      <div class="funding-pair">
        <div><span>Last settled</span><b class="sign-${signOf(data.last_settled && data.last_settled.rate)}">${esc(pct(data.last_settled && data.last_settled.rate))}</b><em>${esc(annualPct(ann.last_settled))}</em></div>
        <div><span>Predicted <abbr title="Live estimate for the current period; changes until settlement">ⓘ</abbr></span><b class="sign-${chip ? chip.sign : 'none'}">${esc(pct(data.predicted && data.predicted.rate))}</b><em>${esc(annualPct(ann.predicted))}</em></div>
        <div><span>Settles in</span><b>${esc(data.predicted && Number.isFinite(data.predicted.settles_at) ? countdown(data.predicted.settles_at - now) : '—')}</b><em>${esc(trend ? `trend ${trend.direction} · ${trend.positive}/${trend.n} > 0` : '')}</em></div>
      </div>
      ${spark ? `<svg class="funding-spark" viewBox="0 0 ${spark.width} ${spark.height}" preserveAspectRatio="none" role="img" aria-label="Last ${spark.n} settled funding rates, ${esc(pct(spark.min))} to ${esc(pct(spark.max))}"><line x1="0" x2="${spark.width}" y1="${spark.zeroY}" y2="${spark.zeroY}"/><polyline points="${spark.points}"/></svg><p class="funding-note">Last ${spark.n} settlements · ${esc(pct(spark.min))} … ${esc(pct(spark.max))}</p>` : ''}
      ${rows ? `<table class="funding-cross"><thead><tr><th>Venue</th><th>Predicted</th><th>Int.</th><th>Annual</th></tr></thead><tbody>${rows}</tbody></table><p class="funding-note">Cross-venue: Hyperliquid predictedFundings${data.cross_venue.annualized_spread ? ` · spread ${esc(annualPct(data.cross_venue.annualized_spread))}` : ''}</p>` : `<p class="funding-note">Cross-venue: ${esc((data.missing && data.missing.cross_venue) || 'not available')}</p>`}`;
  }

  return { VENUES, pct, annualPct, countdown, signOf, chipModel, crossRows, sparkline, sectionHtml, esc };
});
