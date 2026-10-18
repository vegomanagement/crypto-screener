/* Analysis dashboard: Wyckoff and order-flow "Signals" sections (view-model + HTML).
 * Input: GET /api/agent/structure for the active chart ({wyckoff, signals, missing}), or the
 * same blocks inside an analysis snapshot. Pure (no DOM, no network); UMD:
 * window.AnalysisWyckoff in browsers, module.exports under node --test.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.AnalysisWyckoff = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const isNum = v => typeof v === 'number' && Number.isFinite(v);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const utc = ms => (isNum(ms) ? new Date(ms).toISOString().replace('T', ' ').slice(0, 16) + ' UTC' : '—');
  const px = (v, p) => (isNum(v) ? (isNum(p) ? v.toFixed(Math.max(0, Math.min(10, p))) : String(+v.toPrecision(8))) : '—');
  const pct = v => (isNum(v) ? `${(v * 100).toFixed(1)}%` : '—');

  /** Wyckoff view: status line, TR, creek/ice, event timeline (oldest first), confirm/invalidate. */
  function wyckoffView(block, precision) {
    if (!block) return { available: false, reason: 'not loaded yet' };
    const w = block;
    const tr = w.tr || null;
    const structure = w.status === 'structure';
    const title = structure ? `${w.schematic === 'distribution' ? 'Distribution' : 'Accumulation'} · ${w.phase_label || `Phase ${w.phase}`}`
      : (w.schematic ? `Unclear — ${w.schematic} attempt invalidated` : 'No clear structure / trending');
    const tone = !structure ? 'flat' : w.schematic === 'accumulation' ? 'up' : 'down';
    const events = (w.events || []).map(e => ({ time: utc(e.ts), knownAt: utc(e.known_at), code: e.event, label: e.label, price: px(e.price, precision),
      rule: e.rule, conf: isNum(e.confidence) ? e.confidence.toFixed(2) : '—',
      vol: isNum(e.volume_rel) ? `${e.volume_rel.toFixed(1)}×` : '—', spread: isNum(e.spread_atr) ? `${e.spread_atr.toFixed(1)} ATR` : '—',
      close: isNum(e.close_pos) ? pct(e.close_pos) : '—', delta: isNum(e.delta_ratio) ? `${e.delta_ratio > 0 ? '+' : ''}${(e.delta_ratio * 100).toFixed(0)}%` : '—',
      tone: w.schematic === 'accumulation' ? 'up' : w.schematic === 'distribution' ? 'down' : '' }));
    return { available: true, structure, title, tone, phase: w.phase || null, schematic: w.schematic || null,
      confidence: isNum(w.confidence) ? `${w.confidence_label} (${w.confidence.toFixed(2)})` : '—',
      support: px(tr && tr.support, precision), resistance: px(tr && tr.resistance, precision),
      height: px(tr && tr.height, precision), trBars: tr ? tr.bars : null, trFrom: utc(tr && tr.start_ts), trTo: tr && tr.end_ts ? utc(tr.end_ts) : 'ongoing',
      creek: isNum(w.creek) ? px(w.creek, precision) : null, ice: isNum(w.ice) ? px(w.ice, precision) : null,
      events, confirm: w.confirm || '', invalidate: w.invalidate || '', reason: w.reason || '', pending: w.pending || '',
      source: w.source || '', deltaUsed: !!w.delta_used, bars: w.bars, note: w.note || '' };
  }

  function statsText(s) {
    if (!s || !isNum(s.n) || !s.n) return 'no out-of-sample stats';
    const ci = s.expectancy_ci ? ` [${s.expectancy_ci[0].toFixed(2)}, ${s.expectancy_ci[1].toFixed(2)}]` : '';
    const hc = s.hit_ci95_decided ? ` (${pct(s.hit_ci95_decided[0])}–${pct(s.hit_ci95_decided[1])})` : '';
    return `n ${s.n} · ±1 ATR hit ${pct(s.hit_rate_decided)}${hc} · E ${s.expectancy_r >= 0 ? '+' : ''}${s.expectancy_r.toFixed(2)}R${ci} net`;
  }
  const NAMES = { sweep_reclaim: 'Sweep & reclaim', failed_auction: 'Failed auction (tail)', vwap_reentry: 'VWAP 2σ re-entry',
    va_reentry: 'Value-area re-entry', wyckoff_spring: 'Wyckoff Spring/UTAD', wyckoff_sos: 'Wyckoff SOS/SOW',
    sweep_delta: 'Sweep + delta', cvd_divergence: 'CVD divergence', absorption: 'Absorption', exhaustion: 'Delta exhaustion', va_reentry_delta: 'VA re-entry + delta' };

  function signalsView(block, precision) {
    if (!block) return { available: false, reason: 'not loaded yet' };
    const s = block;
    const events = (s.events || []).map(e => ({ name: NAMES[e.signal] || e.signal, dir: e.direction, time: utc(e.ts), ago: e.bars_ago,
      entry: px(e.entry, precision), stop: px(e.stop, precision), target: px(e.target, precision), stats: statsText(e.stats),
      negative: !!(e.stats && isNum(e.stats.expectancy_r) && e.stats.expectancy_r <= 0) }));
    const vp = s.volume_profile, vw = s.session_vwap;
    return { available: true, verdict: s.verdict || '', edge: s.edge || '', passed: s.passed || [], events,
      statsNote: s.stats_note || '', rule: s.rule || '', statsTf: s.stats_timeframe || null,
      profile: vp ? { bars: vp.bars, poc: px(vp.poc, precision), vah: px(vp.vah, precision), val: px(vp.val, precision),
        hvn: (vp.hvn || []).map(x => px(x, precision)).join(', ') || '—', lvn: (vp.lvn || []).map(x => px(x, precision)).join(', ') || '—', where: vp.price_vs_value } : null,
      vwap: vw ? { vwap: px(vw.vwap, precision), upper: px(vw.upper_2s, precision), lower: px(vw.lower_2s, precision),
        z: isNum(vw.z) ? `${vw.z >= 0 ? '+' : ''}${vw.z.toFixed(2)}σ` : '—', bars: vw.bars_into_session } : null,
      unavailable: Object.keys(s.unavailable || {}).map(k => NAMES[k] || k), source: s.source || '' };
  }

  /** Blocks for the active chart: the structure endpoint first, else the shown analysis snapshot. */
  function pickBlocks(structure, result, activeKey, resultKey) {
    if (structure && structure.key === activeKey && !structure.error) return { ...structure.body, origin: 'live', key: structure.key };
    const snap = result && result.snapshot;
    if (snap && resultKey === activeKey && (snap.wyckoff || snap.signals)) return { wyckoff: snap.wyckoff, signals: snap.signals, missing: snap.missing || {}, origin: 'snapshot', key: resultKey };
    return { wyckoff: null, signals: null, missing: {}, origin: 'none', error: structure && structure.key === activeKey ? structure.error : '', key: activeKey };
  }

  const sectionWrap = (id, title, sub, body) => `<section class="ad-section" id="ad-${id}" aria-labelledby="ad-${id}-h"><header class="ad-section-head"><h2 id="ad-${id}-h">${esc(title)}</h2>${sub ? `<p>${esc(sub)}</p>` : ''}</header>${body}</section>`;
  const emptyP = t => `<p class="ad-empty">${esc(t)}</p>`;

  function wyckoffHtml(blocks, opts) {
    const o = opts || {}, v = wyckoffView(blocks.wyckoff, o.precision);
    const toggle = `<label class="ad-wy-toggle" title="Mark the trading range and the events on the active chart"><input type="checkbox" data-act="wyckoff-overlay" ${o.overlay ? 'checked' : ''} ${v.available ? '' : 'disabled'}> Wyckoff on chart</label>`;
    if (!v.available) return sectionWrap('wyckoff', 'Wyckoff', '', `${toggle}${emptyP(blocks.error ? `— ${blocks.error}` : blocks.missing && blocks.missing.wyckoff ? `— ${blocks.missing.wyckoff}` : 'Loading the trading-range read for this chart…')}`);
    const head = `<div class="ad-wy-head"><span class="ad-wy-title ${esc(v.tone)}">${esc(v.title)}</span><span class="ad-wy-conf">confidence ${esc(v.confidence)}</span>${toggle}</div>`;
    const tr = v.support !== '—' || v.resistance !== '—' ? `<dl class="ad-rows ad-wy-tr"><div class="ad-row"><dt>Range support</dt><dd><b>${esc(v.support)}</b></dd></div><div class="ad-row"><dt>Range resistance</dt><dd><b>${esc(v.resistance)}</b></dd></div>
      <div class="ad-row"><dt>${v.ice ? 'Ice (AR low)' : 'Creek (AR high)'}</dt><dd><b>${esc(v.ice || v.creek || '—')}</b></dd></div><div class="ad-row"><dt>Height · bars</dt><dd><b>${esc(v.height)}</b><small>${esc(v.trBars ?? '—')} bars · ${esc(v.trFrom)} → ${esc(v.trTo)}</small></dd></div></dl>` : '';
    const why = v.structure ? `<div class="ad-wy-why"><p><b class="up">Confirms:</b> ${esc(v.confirm || '—')}</p><p><b class="down">Invalidates:</b> ${esc(v.invalidate || '—')}</p>${v.pending ? `<p class="ad-note">${esc(v.pending)}</p>` : ''}</div>`
      : `<p class="ad-note">${esc(v.reason || 'No schematic fits; nothing is forced.')}</p>`;
    const rows = v.events.length ? `<div class="ad-table-wrap"><table class="ad-table compact ad-wy-events"><thead><tr><th>Bar (UTC)</th><th>Event</th><th class="num">Price</th><th class="num">Vol</th><th class="num">Spread</th><th class="num">Close pos</th><th class="num">Delta</th><th class="num">Conf</th><th>Rule that fired</th></tr></thead><tbody>${v.events.map(e => `<tr><td class="mono">${esc(e.time)}</td><td class="${esc(e.tone)}" title="${esc(e.label)}"><b>${esc(e.code)}</b> <small>${esc(e.label)}</small></td><td class="num">${esc(e.price)}</td><td class="num">${esc(e.vol)}</td><td class="num">${esc(e.spread)}</td><td class="num">${esc(e.close)}</td><td class="num">${esc(e.delta)}</td><td class="num">${esc(e.conf)}</td><td class="reason" title="known at ${esc(e.knownAt)}">${esc(e.rule)}</td></tr>`).join('')}</tbody></table></div>` : '';
    const foot = `<p class="ad-note">${esc(v.source)} · ${esc(v.bars)} bars${blocks.origin === 'snapshot' ? ' · from the analysed snapshot' : ''}. ${esc(v.note)}. Backtest: Spring/UTAD and SOS/SOW events showed no out-of-sample edge.</p>`;
    return sectionWrap('wyckoff', 'Wyckoff', 'Deterministic trading-range read: volume, spread, close position and effort vs result on this chart\u2019s closed candles.', head + tr + why + rows + foot);
  }

  function signalsHtml(blocks, opts) {
    const o = opts || {}, v = signalsView(blocks.signals, o.precision);
    if (!v.available) return sectionWrap('signals', 'Signals · no proven edge', '', emptyP(blocks.missing && blocks.missing.signals ? `— ${blocks.missing.signals}` : 'Loading…'));
    const banner = `<p class="ad-warn-box">⚠ ${esc(v.verdict)}. Stats below are measured out of sample (${esc(v.rule)}).</p>`;
    const list = v.events.length ? `<div class="ad-table-wrap"><table class="ad-table compact ad-sig-events"><thead><tr><th>Signal</th><th>Side</th><th>Bar (UTC)</th><th class="num">Entry</th><th class="num">Stop</th><th class="num">Target</th><th>Historical (${esc(v.statsTf || '—')}, OOS)</th></tr></thead><tbody>${v.events.map(e => `<tr><td>${esc(e.name)}</td><td class="${e.dir === 'long' ? 'up' : 'down'}">${e.dir === 'long' ? '▲ long' : '▼ short'}</td><td class="mono">${esc(e.time)} <small>${esc(e.ago)} bars ago</small></td><td class="num">${esc(e.entry)}</td><td class="num down">${esc(e.stop)}</td><td class="num up">${esc(e.target)}</td><td class="${e.negative ? 'warn' : ''}">${esc(e.stats)}</td></tr>`).join('')}</tbody></table></div>`
      : emptyP('No candidate event in the last 24 closed bars.');
    const ctx = `<dl class="ad-rows">${v.profile ? `<div class="ad-row"><dt>Volume profile (${esc(v.profile.bars)} bars)</dt><dd><b>POC ${esc(v.profile.poc)}</b><small>VA ${esc(v.profile.val)} – ${esc(v.profile.vah)} · price ${esc(v.profile.where)} · HVN ${esc(v.profile.hvn)} · LVN ${esc(v.profile.lvn)}</small></dd></div>` : ''}
      ${v.vwap ? `<div class="ad-row"><dt>Session VWAP (UTC day)</dt><dd><b>${esc(v.vwap.vwap)} · ${esc(v.vwap.z)}</b><small>±2σ ${esc(v.vwap.lower)} – ${esc(v.vwap.upper)} · ${esc(v.vwap.bars)} bars in</small></dd></div>` : ''}</dl>`;
    const foot = `<p class="ad-note">${v.statsNote ? `${esc(v.statsNote)}. ` : ''}Not computed live (need per-bar taker delta): ${esc(v.unavailable.join(', ') || '—')}. ${esc(v.source)}.</p>`;
    return sectionWrap('signals', 'Signals · no proven edge', 'Candidate events from the order-flow / structure study, shown as context only.', banner + list + ctx + foot);
  }

  return { wyckoffView, signalsView, pickBlocks, statsText, wyckoffHtml, signalsHtml, NAMES };
});
