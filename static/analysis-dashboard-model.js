/* Analysis dashboard (/ui, below the charts): pure view-model. No DOM, no network.
 * Inputs are this app's own API payloads: POST /api/agent/analysis (incl. the analysed
 * `snapshot`), GET /api/agent/stats, GET /api/funding, GET /api/positioning(+/whales).
 * Missing values become '—' with the reason the API gave; nothing is invented.
 * UMD: window.AnalysisDashboardModel in browsers, module.exports under node --test.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory(require('./analyst-panel.js'), require('./funding.js'), require('./positioning-panel.js'));
  } else root.AnalysisDashboardModel = factory(root.WorkspaceAnalyst, root.WorkspaceFunding, root.PositioningPanel);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (A, F, P) {
  'use strict';
  const GROUPS = ['Trend', 'Momentum', 'Volume', 'Funding', 'Positioning', 'Liquidity', 'Structure', 'Sentiment'];
  const VENUES = { binance: 'Binance', bybit: 'Bybit', hyperliquid: 'Hyperliquid' };
  const isNum = v => typeof v === 'number' && Number.isFinite(v);
  const num = v => (v === null || v === undefined || v === '' ? null : Number(v));
  const signed = (v, d = 2, suffix = '') => (isNum(v) ? `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(d)}${suffix}` : '—');
  const arrow = v => (isNum(v) ? (v > 0 ? '▲' : v < 0 ? '▼' : '◆') : '');
  const tone = v => (isNum(v) ? (v > 0 ? 'up' : v < 0 ? 'down' : '') : '');
  const ageText = (ts, now) => (isNum(ts) && isNum(now) ? `${A.ago(now - ts)} ago` : 'age unknown');
  const utc = ms => (isNum(ms) ? new Date(ms).toISOString().replace('T', ' ').slice(0, 19) + ' UTC' : '—');

  /** Factor group for one snapshot path (first segment, with a few field-level exceptions). */
  function groupOfPath(path) {
    const p = String(path || '').split('.');
    const [s, f] = p;
    if (s === 'indicators') return f === 'rsi14' ? 'Momentum' : /^ema/.test(f || '') ? 'Trend' : 'Structure';
    if (s === 'htf_bias' || s === 'regime') return 'Trend';
    if (s === 'derivatives') return f === 'ema_bias' ? 'Trend' : f === 'open_interest' ? 'Positioning' : 'Structure';
    if (s === 'funding') return 'Funding';
    if (s === 'positioning') return f === 'liquidations' ? 'Liquidity' : 'Positioning';
    if (s === 'liquidity' || s === 'orderbook') return 'Liquidity';
    if (s === 'footprint') return 'Volume';
    if (s === 'flow') return f === 'fear_greed' ? 'Sentiment' : f === 'cvd_1h' ? 'Volume' : f === 'bybit_oi_change_pct' ? 'Positioning' : 'Momentum';
    if (s === 'candles' && p[1] === 'recent' && p[3] === '5') return 'Volume';
    return 'Structure';
  }
  /** Most-cited group of a factor's fields; ties go to the first field's group. */
  function factorGroup(fields) {
    const list = (fields || []).map(groupOfPath);
    if (!list.length) return 'Structure';
    const counts = {};
    list.forEach(g => { counts[g] = (counts[g] || 0) + 1; });
    return list.reduce((best, g) => (counts[g] > counts[best] ? g : best), list[0]);
  }

  /** Value at a dotted snapshot path ('fvg.nearest.0.top'); undefined when absent. */
  function resolvePath(obj, path) {
    let cur = obj;
    for (const part of String(path || '').split('.')) {
      if (cur === null || cur === undefined || typeof cur !== 'object') return undefined;
      cur = Array.isArray(cur) ? cur[Number(part)] : cur[part];
    }
    return cur;
  }
  /** Nearest enclosing object carrying `source` (its source/ts describe the cited value). */
  function provenance(snapshot, path) {
    const parts = String(path || '').split('.');
    let cur = snapshot, best = null;
    for (const part of parts) {
      if (!cur || typeof cur !== 'object') break;
      if (typeof cur.source === 'string') best = cur;
      cur = Array.isArray(cur) ? cur[Number(part)] : cur[part];
    }
    if (cur && typeof cur === 'object' && typeof cur.source === 'string') best = cur;
    return best ? { source: best.source, ts: best.ts } : { source: '', ts: null };
  }
  const RECENT_NAMES = { open_ts: 'open time', o: 'open', h: 'high', l: 'low', c: 'close', v: 'volume' };
  function pathLabel(snapshot, path) {
    const p = String(path || '').split('.');
    if (p[0] === 'candles' && p[1] === 'recent' && p.length === 4) {
      const rows = resolvePath(snapshot, 'candles.recent') || [], fields = resolvePath(snapshot, 'candles.recent_fields') || [];
      const back = rows.length ? rows.length - 1 - Number(p[2]) : null, name = RECENT_NAMES[fields[Number(p[3])]] || `field ${p[3]}`;
      return back === 0 ? `${name} · last bar` : isNum(back) ? `${name} · ${back} bar${back === 1 ? '' : 's'} ago` : name;
    }
    const names = p.filter(x => !/^\d+$/.test(x)), last = names[names.length - 1] || '';
    if (names.length < 2 || !GENERIC.has(last)) return last.replace(/_/g, ' ');
    const prev = names[names.length - 2];
    return `${(CONTAINERS.has(prev) ? names[0] : prev).replace(/_/g, ' ')} ${last.replace(/_/g, ' ')}`;
  }
  const GENERIC = new Set(['top', 'bottom', 'low', 'high', 'price', 'value', 'state', 'delta', 'total', 'level', 'rate', 'poc', 'direction', 'trend', 'label', 'strength', 'long_share', 'short_share']);
  const CONTAINERS = new Set(['nearest', 'unmitigated', 'bars', 'recent', 'pools', 'items', 'rows', 'current', 'recent_events']);
  function fmtValue(v) {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'boolean') return v ? 'yes' : 'no';
    if (isNum(v)) return Math.abs(v) >= 1000 ? v.toLocaleString('en-US', { maximumFractionDigits: 2 }) : String(+v.toPrecision(6));
    if (typeof v === 'string') return v.replace(/_/g, ' ');
    return Array.isArray(v) ? `${v.length} items` : '{…}';
  }
  function missingReason(snapshot, path) {
    const p = String(path || '').split('.'), miss = (snapshot && snapshot.missing) || {};
    if (miss[p[0]]) return miss[p[0]];
    const pm = resolvePath(snapshot, 'positioning.missing') || {};
    return (p[0] === 'positioning' && (pm[p[1]] || pm[p.slice(1).join('.')])) || 'not in the analysed snapshot';
  }
  /** A cited field as a small tag: label, value at analysis time, and its provenance. */
  function metricTag(snapshot, path, now) {
    const value = resolvePath(snapshot, path), pv = provenance(snapshot, path);
    const missing = value === null || value === undefined;
    return { path, label: pathLabel(snapshot, path), value: fmtValue(value), missing,
      hint: missing ? `${path}: — (${missingReason(snapshot, path)})`
        : `${path} = ${fmtValue(value)} · ${pv.source || 'source not recorded'} · ${ageText(pv.ts, now)} (value at analysis time)` };
  }

  /** Long vs short evidence, each sorted strength 3→1 (stable within a strength). */
  function factorColumns(result, now) {
    const snap = result && result.snapshot, list = (result && result.analysis && result.analysis.factor_scores) || [];
    const rows = list.map((f, i) => ({ i, text: f.factor, direction: f.direction, strength: Math.max(1, Math.min(3, Number(f.strength) || 1)),
      group: factorGroup(f.source_fields), tags: (f.source_fields || []).map(path => metricTag(snap, path, now)) }));
    const sort = side => rows.filter(r => r.direction === side).sort((a, b) => b.strength - a.strength || a.i - b.i);
    const long = sort('long'), short = sort('short'), pts = xs => xs.reduce((s, r) => s + r.strength, 0);
    return { long, short, longPoints: pts(long), shortPoints: pts(short) };
  }

  /** Key levels sorted by absolute distance from the analysed price (nearest first). */
  function levelTable(result, now, precision) {
    const snap = (result && result.snapshot) || {};
    const ref = result && result.price && result.price.value, atr = num(resolvePath(snap, 'indicators.atr14'));
    return A.levelRows(result).map(row => {
      const diff = isNum(ref) ? row.price - ref : null;
      return { ...row, priceText: A.fmtPrice(row.price, precision), distPctText: signed(row.distPct, 2, '%'),
        distAtr: isNum(diff) && isNum(atr) && atr > 0 ? diff / atr : null,
        distAtrText: isNum(diff) && isNum(atr) && atr > 0 ? `${signed(diff / atr, 2)} ATR` : '— (no ATR)',
        side: !isNum(diff) ? '' : diff > 0 ? 'above' : diff < 0 ? 'below' : 'at', arrow: arrow(diff),
        tags: row.sources.map(path => metricTag(snap, path, now)) };
    }).sort((a, b) => (a.distPct === null) - (b.distPct === null) || Math.abs(a.distPct) - Math.abs(b.distPct) || b.price - a.price);
  }

  /** Verdict strip: split, verdict word, the maths in one plain sentence, calibration, meta. */
  function verdictModel(result, stats) {
    const g = A.gauge(result, stats);
    if (!g) return null;
    const a = result.analysis, sp = a.split || {}, w = sp.weights || {};
    const pl = isNum(sp.p_long) ? sp.p_long.toFixed(1) : String(g.long), band = isNum(sp.neutral_band) ? sp.neutral_band : 5;
    const wa = num(w.agent) || 0, wb = num(w.baseline) || 0;
    const calibrated = isNum(sp.baseline_cap); // split from the calibrated rule (cap + conflict rule)
    const pct = x => `${Math.round(x * 100)}%`;
    let sentence;
    if (!calibrated) { // results computed before the calibration (renormalised weights)
      const tot = wa + wb || 1;
      sentence = result.source === 'baseline' || !isNum(sp.agent_net)
        ? `Hermes did not answer, so the split comes only from the deterministic confluence baseline (net ${signed(sp.baseline_net)}), turned into ${pl}% long by a logistic curve; within ±${band} points of 50 reads Neutral.`
        : `The split blends Hermes' scored factors (${sp.agent_long_points} long vs ${sp.agent_short_points} short points, net ${signed(sp.agent_net)}, weight ${Math.round(wa / tot * 100)}%) with the deterministic confluence baseline (net ${signed(sp.baseline_net)}, weight ${Math.round(wb / tot * 100)}%) into net ${signed(sp.net)}, which a logistic curve turns into ${pl}% long; within ±${band} points of 50 reads Neutral.`;
    } else {
      const cw = sp.configured_weights || {}, cap = sp.baseline_cap;
      const baseText = isNum(sp.baseline_net) ? `net ${signed(sp.baseline_net)}${isNum(sp.baseline_raw) && sp.baseline_raw !== sp.baseline_net ? ` after the ±${cap} cap (raw ${signed(sp.baseline_raw)})` : `, capped at ±${cap}`}` : 'unavailable';
      sentence = result.source === 'baseline' || !isNum(sp.agent_net)
        ? `Hermes did not answer, so only the deterministic confluence baseline contributes (${baseText}, weight ${pct(num(cw.baseline) || wb)}): net ${signed(sp.net)} → ${pl}% long. A backtest found no edge for the baseline, so on its own it cannot leave the ±${band}-point Neutral band.`
        : `The split weighs Hermes' scored factors (${sp.agent_long_points} long vs ${sp.agent_short_points} short points, net ${signed(sp.agent_net)}, weight ${pct(wa)}) plus the deterministic confluence baseline (${baseText}, weight ${pct(wb)}) into net ${signed(sp.net)}, which a logistic curve turns into ${pl}% long; within ±${band} points of 50 reads Neutral.` +
          (sp.conflict ? ` The factor tally is clear (|net| ≥ ${sp.conflict_min_agent}) and the baseline points the other way, so the baseline is ignored.` : '') +
          (sp.flip_guard ? ' The baseline would have flipped the factors\u2019 direction, so the result is held at 50/50.' : '') +
          ' The baseline is only a tie-breaker: a backtest found no edge for it.';
    }
    return { long: g.long, short: g.short, word: A.verdictWord(g.bias), bias: g.bias, confidence: a.confidence || '—',
      horizon: a.horizon || '', sentence, maths: A.splitLine(result), calibrated: g.calibrated, calibration: A.statsLine(stats),
      calibrationNote: g.calibrated ? 'calibrated on ≥ 20 scored outcomes' : 'not a calibrated probability',
      disagreement: A.disagreementText(result), status: A.statusText(result), source: result.source,
      timestamp: utc(result.generated_at), generatedAt: result.generated_at,
      latency: isNum(result.latency_ms) ? `${(result.latency_ms / 1000).toFixed(1)} s model` : 'no model call',
      snapshotTime: isNum(result.snapshot_ms) ? `${(result.snapshot_ms / 1000).toFixed(1)} s snapshot` : '',
      cached: result.cached ? `cached ${Math.round(result.age_s || 0)} s` : '' };
  }

  /** First price-like number in free text: within ±50% of `ref` when given ('No 1 ATR move' → null). */
  function firstNumber(text, ref) {
    for (const m of String(text || '').matchAll(/-?\d[\d,]*(?:\.\d+)?/g)) {
      const n = Number(m[0].replace(/,/g, ''));
      if (!isNum(ref) || (ref > 0 && Math.abs(n - ref) / ref <= 0.5)) return n;
    }
    return null;
  }
  /** Scenario cards ordered bull → base → bear (direction from the target vs the analysed price). */
  function scenarioCards(result) {
    const ref = result && result.price && result.price.value, bias = result && result.analysis && result.analysis.bias;
    const items = ((result && result.analysis && result.analysis.scenarios) || []).map((s, i) => {
      const t = firstNumber(s.target, ref);
      const dir = isNum(t) && isNum(ref) ? (t > ref ? 'bull' : t < ref ? 'bear' : 'flat') : 'flat';
      return { i, name: s.name || `Scenario ${i + 1}`, trigger: s.trigger || '', target: s.target || '', invalidation: s.invalidation || '', dir };
    });
    const side = bias === 'long' ? 'bull' : bias === 'short' ? 'bear' : 'flat';
    const dist = s => (isNum(firstNumber(s.target, ref)) && isNum(ref) ? Math.abs(firstNumber(s.target, ref) - ref) : Infinity);
    const base = items.filter(s => s.dir === side).sort((a, b) => dist(a) - dist(b) || a.i - b.i)[0];
    const RANK = { bull: 0, base: 1, flat: 1.5, bear: 2 };
    return items.map(s => ({ ...s, kind: s === base ? 'base' : s.dir,
      kindLabel: s === base ? 'Base case' : s.dir === 'bull' ? 'Bull case' : s.dir === 'bear' ? 'Bear case' : 'Range / unclear',
      arrow: s.dir === 'bull' ? '▲' : s.dir === 'bear' ? '▼' : '◆' }))
      .sort((a, b) => RANK[a.kind] - RANK[b.kind] || a.i - b.i);
  }

  /** Stale = the shown analysis belongs to another venue/symbol/interval than the active pane. */
  function staleState(view) {
    if (!view || !view.result) return { stale: false, label: '' };
    const stale = view.resultKey !== view.activeKey;
    return { stale, label: stale ? `for ${A.keyLabel(view.resultKey)} — re-analyze` : '' };
  }

  /** Sticky mini-header text: venue · symbol · interval and freshness of the analysis. */
  function headerModel(view, now) {
    const a = view && view.active;
    const target = a ? `${VENUES[a.exchange] || a.exchange} · ${a.symbol} · ${A.intervalLabel(a.interval)}` : 'No active chart';
    const r = view && view.result, st = staleState(view);
    let fresh = 'not analysed yet';
    if (view && view.busy) fresh = 'analysing…';
    else if (r && st.stale) fresh = `showing ${A.keyLabel(view.resultKey)} — re-analyze`;
    else if (r) fresh = `analysed ${ageText(r.generated_at, now)} · last closed bar ${ageText(r.freshness && r.freshness.last_close_ts, now)}`;
    return { target, fresh, stale: st.stale };
  }

  return { GROUPS, VENUES, groupOfPath, factorGroup, resolvePath, provenance, metricTag, fmtValue, missingReason,
    factorColumns, levelTable, verdictModel, scenarioCards, staleState, headerModel, firstNumber,
    signed, arrow, tone, ageText, utc, isNum, num, A, F, P };
});
