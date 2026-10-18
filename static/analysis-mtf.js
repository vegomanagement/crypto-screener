/* Analysis dashboard: "Multi-timeframe" section (view-model + HTML). Input: GET /api/agent/mtf
 * ({mtf: block}) for the active chart. Pure (no DOM, no network); UMD: window.AnalysisMtf in
 * browsers, module.exports under node --test.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.AnalysisMtf = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const isNum = v => typeof v === 'number' && Number.isFinite(v);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const utc = ms => (isNum(ms) ? new Date(ms).toISOString().replace('T', ' ').slice(5, 16) : '—');
  const px = (v, p) => (isNum(v) ? (isNum(p) ? v.toFixed(Math.max(0, Math.min(10, p))) : String(+v.toPrecision(8))) : '—');
  const LABEL = { 1: '1m', 3: '3m', 5: '5m', 15: '15m', 30: '30m', 60: '1h', 120: '2h', 240: '4h', D: '1D', W: '1W' };
  const lab = tf => LABEL[tf] || tf || '—';
  const arrow = t => (t === 'bull' ? '▲' : t === 'bear' ? '▼' : '◆');
  const tone = t => (t === 'bull' ? 'up' : t === 'bear' ? 'down' : 'flat');
  const VERDICT = { aligned_long: 'Aligned long', aligned_short: 'Aligned short', mixed_counter_trend: 'Mixed · counter-trend',
    range_bound: 'Range-bound', unclear: 'Unclear' };
  const POS = { at_htf_support: 'at HTF support', at_htf_resistance: 'at HTF resistance', inside: 'inside HTF range',
    above: 'above HTF range', below: 'below HTF range', overlapping: 'overlaps HTF range' };
  const STATE = { waiting_for_poi: 'waiting for HTF POI', at_poi_waiting_ltf_choch: 'at POI · waiting for LTF CHoCH',
    triggered_waiting_retest: 'triggered · waiting for retest', triggered_filled: 'triggered · filled' };
  const PAIR = { aligned: 'aligned', divergence: 'DIVERGENCE', counter_trend: 'counter-trend', pullback_ended: 'pullback ended',
    reversal_propagated: 'reversal propagated', no_structure: 'no structure' };

  /** One ladder row per TF (highest first). */
  function ladderRows(block, precision) {
    const pairs = block.pairs || [];
    return (block.ladder || []).map(tf => {
      const a = (block.tfs || {})[tf] || {}, s = a.smc || {}, w = a.wyckoff || {}, b = a.bias || {}, lb = s.last_break;
      const dr = s.dealing_range, zone = [...(s.order_blocks || []).map(z => ({ ...z, k: 'OB' })), ...(s.fvgs || []).map(z => ({ ...z, k: 'FVG' }))]
        .filter(z => z.state === 'unmitigated').sort((x, y) => Math.abs(a.price - (x.top + x.bottom) / 2) - Math.abs(a.price - (y.top + y.bottom) / 2))[0];
      const pair = pairs.find(p => p.ltf === tf);
      const brk = (s.breakers || []).find(z => z.state !== 'invalidated'), idm = s.inducement;
      const liq = [brk ? `${brk.dir === 'bull' ? '▲' : '▼'} Breaker ${px(brk.bottom, precision)}–${px(brk.top, precision)}${brk.state === 'mitigated' ? ' (m)' : ''}` : null,
        idm && isNum(idm.price) ? `IDM ${px(idm.price, precision)} ${idm.state === 'swept' ? 'swept' : 'resting'}` : null,
        ...(s.equal_levels || []).slice(-2).map(q => `${q.kind} ${px(q.price, precision)}`)].filter(Boolean);
      return { tf, label: lab(tf), trend: s.trend || 'none', arrow: arrow(s.trend), tone: tone(s.trend), internal: s.internal_trend || 'none',
        lastBreak: lb ? `${lb.kind} ${lb.dir === 'bull' ? '▲' : '▼'} ${px(lb.level, precision)}` : '—', lastBreakTime: lb ? utc(lb.ts) : '',
        lastBreakTone: lb ? tone(lb.dir) : '', zone: dr ? dr.zone : '—', eq: dr ? px(dr.eq, precision) : '—',
        poi: zone ? `${zone.dir === 'bull' ? '▲' : '▼'} ${zone.k} ${px(zone.bottom, precision)}–${px(zone.top, precision)}` : '—',
        liquidity: liq.length ? liq.join(' · ') : '—',
        bias: b.bias || '—', biasScore: isNum(b.score) ? `${b.score > 0 ? '+' : ''}${b.score}/5` : '', biasTone: tone(b.bias),
        wyckoff: w.status === 'structure' ? `${w.schematic === 'accumulation' ? 'Acc' : 'Dist'} ${w.phase} · ${px(w.tr && w.tr.support, precision)}–${px(w.tr && w.tr.resistance, precision)}` : 'none',
        wyTone: w.status === 'structure' ? (w.schematic === 'accumulation' ? 'up' : 'down') : 'flat',
        position: POS[(block.position_vs_htf || {})[tf]] || '—',
        divergence: !!(pair && pair.divergence), pairStatus: pair ? PAIR[pair.status] || pair.status : '',
        sync: tf === block.sync_tf, entry: tf === block.entry_tf, dominant: tf === block.dominant_tf, pane: tf === block.interval };
    });
  }

  function mtfView(body, precision) {
    if (!body || !body.mtf) return { available: false, reason: body && body.error ? body.error : 'not loaded yet' };
    const b = body.mtf;
    return { available: true, verdict: b.verdict, verdictLabel: VERDICT[b.verdict] || b.verdict,
      verdictTone: b.verdict === 'aligned_long' ? 'up' : b.verdict === 'aligned_short' ? 'down' : b.verdict === 'mixed_counter_trend' ? 'warn' : 'flat',
      dominant: lab(b.dominant_tf), sync: b.sync_tf ? lab(b.sync_tf) : '—', entry: b.entry_tf ? lab(b.entry_tf) : '—',
      rows: ladderRows(b, precision),
      conflicts: (b.conflicts || []).map(c => ({ id: c.id, tfs: (c.tfs || []).map(lab).join(' vs '), text: c.text, resolve: c.resolve || [] })),
      aligned: (b.aligned || []).map(c => ({ id: c.id, tfs: (c.tfs || []).map(lab).join(' & '), text: c.text })),
      resolve: b.resolve || [],
      levels: (b.levels || []).map(l => ({ price: px(l.price, precision), raw: l.price, tfs: l.tfs || [], kinds: (l.kinds || []).join(', '), n: l.n_tfs,
        score: l.score, side: l.side, dist: isNum(l.distance_atr) ? `${l.distance_atr > 0 ? '+' : ''}${l.distance_atr.toFixed(2)} ATR` : '—' })),
      setups: (b.setups || []).map(s => ({ pair: s.pair, state: STATE[s.state] || s.state, raw: s.state, htf: s.htf_trend, dir: s.direction,
        entry: px(s.entry, precision), stop: px(s.stop, precision), target: px(s.target, precision), partial: px(s.partial_2r, precision),
        poi: s.poi ? `${s.poi.kind.toUpperCase()} ${px(s.poi.bottom, precision)}–${px(s.poi.top, precision)}` : '—', edge: s.edge, note: s.note || '' })),
      missing: Object.entries(b.missing || {}).map(([k, v]) => `${lab(k)}: ${v}`), edge: b.edge || '', source: b.source || '' };
  }

  const sectionWrap = (id, title, sub, body) => `<section class="ad-section" id="ad-${id}" aria-labelledby="ad-${id}-h"><header class="ad-section-head"><h2 id="ad-${id}-h">${esc(title)}</h2>${sub ? `<p>${esc(sub)}</p>` : ''}</header>${body}</section>`;
  const badge = t => `<span class="ad-tf-badge">${esc(t)}</span>`;

  function toggles(o, on) {
    const box = (act, label, checked, title) => `<label class="ad-wy-toggle" title="${esc(title)}"><input type="checkbox" data-act="${act}" ${checked ? 'checked' : ''} ${on ? '' : 'disabled'}> ${esc(label)}</label>`;
    return `<div class="ad-mtf-toggles">${box('smc-overlay', 'SMC on chart', o.smc, 'Swings, BOS/CHoCH, order blocks, FVGs, breakers, inducement, equal highs/lows, sweeps and equilibrium of this chart')}${box('smc-htf', 'HTF structure on chart', o.smcHtf, 'Also draw the next higher timeframe\u2019s swings, breaks and zones on this chart')}${box('htf-box', 'HTF range box', o.htfBox, 'Draw the higher timeframe\u2019s Wyckoff trading range on this chart')}</div>`;
  }

  function mtfHtml(body, opts) {
    const o = opts || {}, v = mtfView(body, o.precision);
    if (!v.available) return sectionWrap('mtf', 'Multi-timeframe', '', `${toggles(o, false)}<p class="ad-empty">${esc(v.reason === 'not loaded yet' ? 'Loading the timeframe ladder…' : `— ${v.reason}`)}</p>`);
    const strip = `<div class="ad-mtf-strip" role="img" aria-label="Structure ladder">${v.rows.map(r => `<span class="ad-mtf-chip ${esc(r.tone)}${r.sync ? ' is-sync' : ''}${r.pane ? ' is-pane' : ''}" title="${esc(`${r.label}: external ${r.trend}, internal ${r.internal}${r.divergence ? ' — divergence vs the TF above' : ''}`)}">${esc(r.label)} ${esc(r.arrow)}${r.divergence ? ' ⚡' : ''}${r.sync ? ' ⇄' : ''}</span>`).join('<i>›</i>')}</div>`;
    const head = `<div class="ad-mtf-head"><span class="ad-mtf-verdict ${esc(v.verdictTone)}">${esc(v.verdictLabel)}</span><span>dominant <b>${esc(v.dominant)}</b></span><span>sync TF <b>${esc(v.sync)}</b></span><span>entry TF <b>${esc(v.entry)}</b></span>${toggles(o, true)}</div>${strip}`;
    const table = `<div class="ad-table-wrap"><table class="ad-table compact ad-mtf-ladder"><thead><tr><th>TF</th><th>External</th><th>Last BOS/CHoCH</th><th>Internal</th><th>Prem/Disc (EQ)</th><th>Nearest unmitigated OB/FVG</th><th>Breaker · IDM · EQH/EQL</th><th>Bias</th><th>Wyckoff</th><th>vs HTF range</th><th>vs TF above</th></tr></thead><tbody>${v.rows.map(r => `<tr class="${r.pane ? 'is-self' : ''}"><td><b>${esc(r.label)}</b>${r.dominant ? ' <small>dominant</small>' : ''}${r.sync ? ' <small class="up">sync</small>' : ''}${r.entry ? ' <small>entry</small>' : ''}</td><td class="${esc(r.tone)}">${esc(r.arrow)} ${esc(r.trend)}</td><td class="${esc(r.lastBreakTone)}">${esc(r.lastBreak)} <small>${esc(r.lastBreakTime)}</small></td><td class="${esc(tone(r.internal))}">${esc(arrow(r.internal))} ${esc(r.internal)}</td><td>${esc(r.zone)} <small>${esc(r.eq)}</small></td><td>${esc(r.poi)}</td><td class="reason">${esc(r.liquidity)}</td><td class="${esc(r.biasTone)}">${esc(r.bias)} <small>${esc(r.biasScore)}</small></td><td class="${esc(r.wyTone)}">${esc(r.wyckoff)}</td><td>${esc(r.position)}</td><td class="${r.divergence ? 'warn' : ''}">${r.divergence ? '⚡ ' : ''}${esc(r.pairStatus || '—')}</td></tr>`).join('')}</tbody></table></div>`;
    const conflicts = v.conflicts.length ? `<div class="ad-mtf-conflicts"><h3>Conflicts (higher timeframe has priority)</h3><ul>${v.conflicts.map(c => `<li><b>${esc(c.id)}</b> <small>${esc(c.tfs)}</small> ${esc(c.text)}${c.resolve.length ? `<ul class="ad-mtf-resolve">${c.resolve.map(r => `<li>⟶ ${esc(r)}</li>`).join('')}</ul>` : ''}</li>`).join('')}</ul></div>` : '<p class="ad-note">No conflicts between the timeframes.</p>';
    const aligned = v.aligned.length ? `<p class="ad-note">${v.aligned.map(a => `<b class="up">${esc(a.id)}</b> ${esc(a.text)}`).join(' · ')}</p>` : '';
    const levels = v.levels.length ? `<div class="ad-table-wrap"><table class="ad-table compact ad-mtf-levels"><thead><tr><th class="num">Level</th><th>Timeframes</th><th class="num">Weight</th><th>Sources</th><th class="num">Distance</th></tr></thead><tbody>${v.levels.map(l => `<tr><td class="num ${l.side === 'above' ? 'down' : 'up'}"><b>${esc(l.price)}</b></td><td>${l.tfs.map(badge).join('')}</td><td class="num">${esc(l.score)}</td><td class="reason">${esc(l.kinds)}</td><td class="num">${esc(l.dist)}</td></tr>`).join('')}</tbody></table></div>` : '';
    const setups = v.setups.length ? `<div class="ad-table-wrap"><table class="ad-table compact ad-mtf-setups"><caption class="ad-warn-box">Swing setups (HTF POI → LTF CHoCH → retest) · no proven out-of-sample edge (0 of 962 variants incl. breaker/IDM, 1d→4h, 1w→1d) — context only</caption><thead><tr><th>Pair</th><th>State</th><th>HTF trend</th><th>POI</th><th>Side</th><th class="num">Entry</th><th class="num">Stop</th><th class="num">2R partial</th><th class="num">Target</th></tr></thead><tbody>${v.setups.map(s => `<tr><td>${esc(s.pair)}</td><td>${esc(s.state)}${s.note ? ` <small>${esc(s.note)}</small>` : ''}</td><td class="${esc(tone(s.htf))}">${esc(s.htf)}</td><td>${esc(s.poi)}</td><td class="${s.dir === 'long' ? 'up' : s.dir === 'short' ? 'down' : ''}">${esc(s.dir || '—')}</td><td class="num">${esc(s.entry)}</td><td class="num down">${esc(s.stop)}</td><td class="num">${esc(s.partial)}</td><td class="num up">${esc(s.target)}</td></tr>`).join('')}</tbody></table></div>` : '';
    const foot = `<p class="ad-note">${esc(v.source)}. ${esc(v.edge)}.${v.missing.length ? ` Missing: ${esc(v.missing.join('; '))}.` : ''}</p>`;
    return sectionWrap('mtf', 'Multi-timeframe', 'SMC swing structure top-down: where the lower timeframes diverge and where they synchronise; Wyckoff ranges nested in the higher-TF context; levels merged across timeframes.',
      head + table + conflicts + aligned + `<h3 class="ad-mtf-h3">Merged levels (HTF weighted)</h3>` + levels + setups + foot);
  }

  /** 'exchange:symbol:interval' match for a fetched body. */
  const pick = (mtf, activeKey) => (mtf && mtf.key === String(activeKey || '').split(':').slice(0, 3).join(':') ? mtf : null);

  return { mtfView, mtfHtml, ladderRows, pick, lab, VERDICT, STATE };
});
