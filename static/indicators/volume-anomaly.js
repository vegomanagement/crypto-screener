/* Abnormal-volume candles: flags bars whose volume is unusual versus the previous N bars,
 * in two tiers, with an optional bar delta (taker buy − taker sell) from footprint data.
 * Pure model + canvas draw. Default method: z-score of ln(volume) over the previous
 * `lookback` bars (the bar itself excluded). A forming bar is scored against the same
 * closed window; its volume only grows, so its tier can only rise until it closes.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.WorkspaceVolumeAnomaly = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const ID = 'abnormal-volume';
  const METHODS = ['zlog', 'median', 'sma', 'percentile'];
  const schema = [
    { key: 'method', label: 'Method', type: 'select', options: METHODS, optionLabels: { zlog: 'z-score of log volume', median: 'k × median volume', sma: 'k × average volume', percentile: 'Percentile rank' }, default: 'zlog', group: 'Detection' },
    { key: 'lookback', label: 'Lookback, bars', type: 'number', min: 10, max: 500, step: 1, default: 50, group: 'Detection' },
    { key: 'zTier1', label: 'z-score tier 1, σ', type: 'number', min: 0.5, max: 10, step: 0.1, default: 2, group: 'Detection' },
    { key: 'zTier2', label: 'z-score tier 2, σ', type: 'number', min: 0.5, max: 10, step: 0.1, default: 3, group: 'Detection' },
    { key: 'kTier1', label: 'Ratio tier 1, × (median/average)', type: 'number', min: 1, max: 50, step: 0.1, default: 2.5, group: 'Detection' },
    { key: 'kTier2', label: 'Ratio tier 2, ×', type: 'number', min: 1, max: 50, step: 0.1, default: 4, group: 'Detection' },
    { key: 'pTier1', label: 'Percentile tier 1, %', type: 'number', min: 50, max: 100, step: 0.5, default: 95, group: 'Detection' },
    { key: 'pTier2', label: 'Percentile tier 2, % (100 = above every bar)', type: 'number', min: 50, max: 100, step: 0.5, default: 100, group: 'Detection' },
    { key: 'style', label: 'Candle highlight', type: 'select', options: ['outline', 'fill', 'none'], optionLabels: { outline: 'Outline body', fill: 'Recolour body', none: 'None' }, default: 'outline', group: 'Display' },
    { key: 'volumeHighlight', label: 'Highlight volume bar', type: 'boolean', default: true, group: 'Display' },
    { key: 'marker', label: 'Marker above bar', type: 'boolean', default: true, group: 'Display' },
    { key: 'showDelta', label: 'Delta sign from footprint (1m–1h)', type: 'boolean', default: true, group: 'Delta' },
    { key: 'strongDeltaPct', label: 'Strong delta, % of volume', type: 'number', min: 1, max: 100, step: 1, default: 15, group: 'Delta' },
    { key: 'tier1Color', label: 'Tier 1 colour', type: 'color', default: '#f2c94c', group: 'Display' },
    { key: 'tier2Color', label: 'Tier 2 colour', type: 'color', default: '#ff7a1a', group: 'Display' }
  ];
  const defaults = () => Object.fromEntries(schema.map(f => [f.key, f.default]));
  const definition = { id: ID, label: 'Abnormal volume', kind: 'volume-anomaly', defaultEnabled: false, settingsSchema: schema,
    labelForSettings: s => `Abnormal vol ${s.method === 'zlog' ? `${s.zTier1}/${s.zTier2}σ` : s.method === 'percentile' ? `p${s.pTier1}/${s.pTier2}` : `${s.kTier1}/${s.kTier2}× ${s.method}`}` };

  const median = sorted => { const n = sorted.length, m = n >> 1; return n % 2 ? sorted[m] : (sorted[m - 1] + sorted[m]) / 2; };
  /** Score of volume `v` against the previous-bar window (array of volumes). null = cannot score. */
  function score(v, window, method) {
    if (!(v > 0) || !window.length) return null;
    if (method === 'zlog') {
      const logs = window.filter(x => x > 0).map(Math.log);
      if (logs.length < Math.max(3, Math.ceil(window.length * 0.8))) return null;
      const mean = logs.reduce((a, x) => a + x, 0) / logs.length;
      const sd = Math.sqrt(logs.reduce((a, x) => a + (x - mean) ** 2, 0) / (logs.length - 1));
      return sd > 0 ? (Math.log(v) - mean) / sd : null;
    }
    if (method === 'median') { const m = median(window.slice().sort((a, b) => a - b)); return m > 0 ? v / m : null; }
    if (method === 'sma') { const m = window.reduce((a, x) => a + x, 0) / window.length; return m > 0 ? v / m : null; }
    if (method === 'percentile') return 100 * window.filter(x => x < v).length / window.length;
    throw new Error(`unknown method ${method}`);
  }
  function tiersFor(s) {
    if (s.method === 'zlog') return [s.zTier1, s.zTier2];
    if (s.method === 'percentile') return [s.pTier1, s.pTier2];
    return [s.kTier1, s.kTier2];
  }
  function tierOf(value, s) {
    if (value === null || !Number.isFinite(value)) return 0;
    const [t1, t2] = tiersFor(s);
    return value >= Math.max(t1, t2) ? 2 : value >= Math.min(t1, t2) ? 1 : 0;
  }
  function scoreLabel(value, s) {
    if (s.method === 'zlog') return `${value.toFixed(1)}σ`;
    if (s.method === 'percentile') return `p${Math.round(value)}`;
    return `×${value.toFixed(1)}`;
  }
  /**
   * bars: closed bars (time asc, {time, volume}); forming: optional bar newer than the last closed.
   * Returns flagged entries only: { time, index, volume, score, tier, forming }.
   */
  function compute(bars, settings, forming = null) {
    const s = { ...defaults(), ...settings }, L = Math.max(2, Math.round(s.lookback)), out = [];
    const vols = bars.map(b => Number(b.volume));
    for (let i = L; i < bars.length; i += 1) {
      const value = score(vols[i], vols.slice(i - L, i), s.method), tier = tierOf(value, s);
      if (tier) out.push({ time: bars[i].time, index: i, volume: vols[i], score: value, tier, forming: false });
    }
    if (forming && bars.length >= L && forming.time > bars.at(-1).time) {
      const value = score(Number(forming.volume), vols.slice(-L), s.method), tier = tierOf(value, s);
      if (tier) out.push({ time: forming.time, index: bars.length, volume: Number(forming.volume), score: value, tier, forming: true });
    }
    return out;
  }
  /** Delta classification: initiative (delta agrees with the bar's direction) vs absorption. */
  function classifyDelta({ delta, total, open, close, high, low }, strongPct = 15) {
    if (delta === null || delta === undefined || !(total > 0)) return { kind: 'unknown', sign: 0, pct: null };
    const pct = 100 * delta / total, sign = Math.sign(delta);
    if (Math.abs(pct) < strongPct || sign === 0) return { kind: 'balanced', sign, pct };
    const body = close - open, range = high - low;
    const dir = range > 0 && Math.abs(body) <= 0.1 * range ? 0 : Math.sign(body);
    return { kind: dir === sign ? 'initiative' : 'absorption', sign, pct };
  }
  const SCALE = 10n ** 18n;
  const big = v => Number(v / SCALE) + Number(v % SCALE) / 1e18;
  /** Delta and volume of a footprint store entry (WorkspaceFootprint LiveFootprint bar). */
  function footprintDelta(entry) {
    if (!entry?.bar?.levels) return null;
    let bid = 0n, ask = 0n;
    for (const cell of entry.bar.levels.values()) { bid += cell.bid; ask += cell.ask; }
    return { delta: big(ask - bid), total: big(ask + bid), coverage: entry.coverage, complete: entry.coverage === 'complete' && !entry.forming };
  }
  function rgba(hex, a) { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`; }
  /**
   * view: { centerX(timeSec), y(price), volY(volume)|null, volBaseY|null, barSpacing, plotWidth, plotHeight, bar(timeSec) → OHLC }
   * deltaFor(timeSec) → { delta, total, complete } | null
   */
  function draw(ctx, flags, view, settings, deltaFor = () => null) {
    const s = { ...defaults(), ...settings }, hits = [];
    const half = Math.max(1, view.barSpacing * 0.4), showText = view.barSpacing >= 5;
    ctx.save(); ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; ctx.font = '9px SFMono-Regular, Consolas, monospace';
    for (const f of flags) {
      const x = view.centerX(f.time), bar = view.bar(f.time);
      if (!Number.isFinite(x) || !bar || x < -half || x > view.plotWidth + half) continue;
      const color = f.tier === 2 ? s.tier2Color : s.tier1Color;
      const yo = view.y(bar.open), yc = view.y(bar.close), yh = view.y(bar.high), yl = view.y(bar.low);
      if (![yo, yc, yh, yl].every(Number.isFinite)) continue;
      const top = Math.min(yo, yc), height = Math.max(1, Math.abs(yo - yc));
      ctx.setLineDash(f.forming ? [3, 2] : []);
      if (s.style === 'fill') { ctx.fillStyle = rgba(color, f.tier === 2 ? 0.95 : 0.8); ctx.fillRect(x - half, top, half * 2, height); }
      else if (s.style === 'outline') { ctx.lineWidth = f.tier === 2 ? 2.5 : 1.5; ctx.strokeStyle = color; ctx.strokeRect(x - half - 1.5, top - 1.5, half * 2 + 3, height + 3); }
      if (s.volumeHighlight && view.volY) {
        const vy = view.volY(f.volume), base = view.volBaseY;
        if (Number.isFinite(vy) && Number.isFinite(base)) { ctx.fillStyle = rgba(color, f.tier === 2 ? 0.75 : 0.5); ctx.fillRect(x - half, Math.min(vy, base), half * 2, Math.max(1, Math.abs(base - vy))); }
      }
      ctx.setLineDash([]);
      let d = null;
      if (s.showDelta) {
        const raw = deltaFor(f.time);
        if (raw) d = { ...classifyDelta({ ...raw, open: bar.open, close: bar.close, high: bar.high, low: bar.low }, s.strongDeltaPct), complete: raw.complete };
      }
      if (s.marker) {
        const my = yh - 4;
        ctx.fillStyle = color;
        if (showText) {
          const dTxt = d && d.kind !== 'unknown' ? ` Δ${d.sign > 0 ? '+' : d.sign < 0 ? '−' : '0'}${d.kind === 'initiative' ? 'I' : d.kind === 'absorption' ? 'A' : ''}${d.complete ? '' : '?'}` : '';
          ctx.fillText(`${'●'.repeat(f.tier)}${dTxt}`, x, my);
        } else { ctx.beginPath(); ctx.arc(x, my - 2, f.tier === 2 ? 2.5 : 1.8, 0, Math.PI * 2); ctx.fill(); }
      }
      hits.push({ x, half, top: yh - 14, bottom: yl + 2, f, d, bar });
    }
    ctx.restore();
    return hits;
  }
  function hitTest(hits, x, y) {
    return hits.find(h => Math.abs(x - h.x) <= Math.max(3, h.half + 1) && y >= h.top && y <= h.bottom) || null;
  }
  function tooltipText(hit, settings) {
    const s = { ...defaults(), ...settings }, f = hit.f, d = hit.d;
    const method = { zlog: `z-score of ln(volume) vs previous ${s.lookback} bars`, median: `volume ÷ median of previous ${s.lookback} bars`, sma: `volume ÷ average of previous ${s.lookback} bars`, percentile: `percentile rank among previous ${s.lookback} bars` }[s.method];
    const lines = [`Abnormal volume · tier ${f.tier} · ${scoreLabel(f.score, s)} (${method})${f.forming ? ' · forming bar, provisional (can only rise)' : ''}`,
      `Volume ${+f.volume.toPrecision(8)}`];
    if (!s.showDelta) return lines.join('\n');
    if (!d) lines.push('Delta: no footprint data for this bar (footprint covers 1m–1h, recent bars only)');
    else {
      const what = d.kind === 'initiative' ? `initiative ${d.sign > 0 ? 'buying' : 'selling'} (delta agrees with the bar)` : d.kind === 'absorption' ? `absorption: strong ${d.sign > 0 ? 'buying' : 'selling'} met passive ${d.sign > 0 ? 'sellers' : 'buyers'} (bar did not follow)` : d.kind === 'balanced' ? `balanced (|delta| < ${s.strongDeltaPct}% of volume)` : 'unknown';
      lines.push(`Delta ${d.pct === null ? '—' : `${d.pct >= 0 ? '+' : ''}${d.pct.toFixed(1)}%`} · ${what}`);
      lines.push(d.complete ? 'Delta from footprint (complete bar, every trade fetched)' : 'Delta from partial/forming footprint bar — provisional, not complete');
    }
    return lines.join('\n');
  }
  return { ID, definition, schema, defaults, METHODS, score, tierOf, tiersFor, scoreLabel, compute, classifyDelta, footprintDelta, draw, hitTest, tooltipText };
});
