/* Hermes analyst card for /ui (right sidebar). View only: no orders, no trading APIs.
 * The browser talks only to this app's Flask routes (/api/agent/*); the Hermes gateway
 * URL/key never reach the page. Pure model functions are exported for node --test;
 * the DOM controller (mount) is used by workspace.js.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(root);
  else root.WorkspaceAnalyst = factory(root);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';
  const AUTO_MIN_MS = 120000;
  const EXCHANGES = ['binance', 'bybit', 'hyperliquid'];
  const TYPE_COLORS = { support: '#00c087', resistance: '#ef6670', entry: '#4fc3f7', stop: '#ff9f43',
    target: '#b388ff', liquidity: '#f5d76e', poi: '#9aa7bd' };
  const TYPE_LABELS = { support: 'Support', resistance: 'Resistance', entry: 'Entry', stop: 'Stop',
    target: 'Target', liquidity: 'Liquidity', poi: 'POI' };

  // ── pure model ─────────────────────────────────────────────────────────────
  /** Request body for the active pane, or { error } when the pane cannot be analysed. */
  function requestFor(active) {
    if (!active || !EXCHANGES.includes(active.exchange) || typeof active.symbol !== 'string' || !active.symbol) return { error: 'No active chart' };
    if (/s$/.test(active.interval)) return { error: 'Seconds charts are built in the browser; pick 1m or higher to analyse' };
    return { body: { exchange: active.exchange, symbol: active.symbol, interval: active.interval } };
  }
  function keyOf(active) { return active ? `${active.exchange}:${active.symbol}:${active.interval}` : ''; }

  /** Gauge model. The split is computed server-side from the scored factors + baseline. */
  function gauge(result, stats) {
    const a = result && result.analysis;
    if (!a) return null;
    const long = Math.max(0, Math.min(100, Math.round(Number(a.long_pct) || 0)));
    const calibrated = !!(stats && stats.overall && stats.overall.n >= 20);
    const n = (a.factor_scores || []).length;
    return { long, short: 100 - long, bias: a.bias, confidence: a.confidence,
      source: result.source, calibrated,
      caption: result.source === 'baseline' ? 'Deterministic confluence baseline only (Hermes did not answer)'
        : `Computed from ${n} scored factor${n === 1 ? '' : 's'} + baseline` +
          (calibrated ? ' · see hit rate below' : ' · not a calibrated probability') };
  }

  /** One chip per validated factor: green long / red short, strength 1-3, cited fields. */
  function factorChips(result) {
    const factors = (result && result.analysis && result.analysis.factor_scores) || [];
    return factors.map(f => ({ label: f.factor, direction: f.direction, strength: f.strength,
      cls: f.direction === 'long' ? 'is-long' : 'is-short', sign: f.direction === 'long' ? '+' : '−',
      sources: f.source_fields || [], funding: (f.source_fields || []).some(x => x.startsWith('funding.')),
      positioning: (f.source_fields || []).some(x => x.startsWith('positioning.')) }));
  }

  function signed(x) { return !Number.isFinite(x) ? '—' : `${x >= 0 ? '+' : '−'}${Math.abs(x).toFixed(2)}`; }
  /** "agent +0.33 (L 7 · S 3) ×0.65 · baseline −0.10 ×0.35 → net +0.18 → p_long 63.1" */
  function splitLine(result) {
    const sp = result && result.analysis && result.analysis.split;
    if (!sp) return '';
    const parts = [];
    if (sp.agent_net !== null && sp.agent_net !== undefined) parts.push(`factors ${signed(sp.agent_net)} (L ${sp.agent_long_points} · S ${sp.agent_short_points}) ×${sp.weights.agent}`);
    if (sp.baseline_net !== null && sp.baseline_net !== undefined) parts.push(`baseline ${signed(sp.baseline_net)} ×${sp.weights.baseline}`);
    if (!parts.length) parts.push('no evidence');
    return `${parts.join(' · ')} → net ${signed(sp.net)} → p_long ${Number(sp.p_long).toFixed(1)} (k ${sp.k}, neutral ±${sp.neutral_band})`;
  }

  /** Warning when the agent's own bias disagrees with the computed split, else ''. */
  function disagreementText(result) {
    const d = result && result.analysis && result.analysis.disagreement;
    if (!d) return '';
    return `${d.severity === 'opposite' ? 'Disagreement' : 'Partial disagreement'}: agent says ${d.agent_bias}, scored evidence computes ${d.computed_bias}`;
  }

  function decimals(price) {
    const a = Math.abs(price);
    return a >= 1000 ? 1 : a >= 10 ? 2 : a >= 1 ? 4 : a >= 0.01 ? 5 : 8;
  }
  function fmtPrice(price, precision) {
    if (!Number.isFinite(price)) return '—';
    return price.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: precision ?? decimals(price) });
  }

  /** Levels sorted high → low with signed distance from the analysed price. */
  function levelRows(result) {
    const levels = (result && result.analysis && result.analysis.levels) || [];
    const ref = result && result.price && result.price.value;
    return levels.filter(l => Number.isFinite(l.price)).map((l, index) => ({
      index, type: l.type, label: TYPE_LABELS[l.type] || l.type, price: l.price, color: TYPE_COLORS[l.type] || '#9aa7bd',
      reason: l.reason || '', sources: l.source_fields || [], unanchored: !!l.unanchored,
      distPct: Number.isFinite(ref) && ref ? ((l.price - ref) / ref) * 100 : null
    })).sort((a, b) => b.price - a.price);
  }

  function ago(ms) {
    if (!Number.isFinite(ms) || ms < 0) return '—';
    const s = Math.round(ms / 1000);
    return s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${(s / 3600).toFixed(1)}h`;
  }
  /** "Data: last close 3m ago · model 23.4 s · cached 12s" */
  function freshness(result, now) {
    if (!result) return '';
    const parts = [`last closed bar ${ago(now - (result.freshness && result.freshness.last_close_ts))} ago`];
    if (Number.isFinite(result.latency_ms)) parts.push(`${result.model || 'model'} ${(result.latency_ms / 1000).toFixed(1)} s`);
    else parts.push('no model call');
    if (Number.isFinite(result.snapshot_ms)) parts.push(`snapshot ${(result.snapshot_ms / 1000).toFixed(1)} s`);
    if (result.cached) parts.push(`cached ${Math.round(result.age_s || 0)}s`);
    const missing = Object.keys((result.freshness && result.freshness.missing) || {});
    if (missing.length) parts.push(`missing: ${missing.join(', ')}`);
    return parts.join(' · ');
  }

  function splitStats(stats) {
    const sp = stats && stats.split;
    if (!sp || !sp.n) return '';
    const fund = sp.hermes_runs ? ` · funding cited ${sp.hermes_runs_citing_funding}/${sp.hermes_runs}` +
      (Number.isFinite(sp.hermes_runs_citing_positioning) ? ` · positioning cited ${sp.hermes_runs_citing_positioning}/${sp.hermes_runs}` : '') : '';
    return ` · p_long ${sp.p_long_min}–${sp.p_long_max} over ${sp.n} (${sp.distinct_long_pct} distinct)${fund}`;
  }
  function statsLine(stats) {
    if (!stats || !stats.overall) return 'Calibration: no data yet';
    const o = stats.overall;
    if (!o.n) return `Calibration: ${stats.total || 0} logged, ${stats.pending || 0} pending — no outcomes scored yet${splitStats(stats)}`;
    return `Calibration: hit rate ${(o.hit_rate * 100).toFixed(0)}% over n=${o.n} (win ${o.win} · loss ${o.loss} · timeout ${o.timeout} · ambiguous ${o.ambiguous})${o.n < 20 ? ' · small sample' : ''}${splitStats(stats)}`;
  }

  /** Auto refresh is allowed only when enabled, visible and ≥ 2 min since the last run. */
  function autoDue(enabled, visible, lastMs, now, inFlight) {
    return !!enabled && !!visible && !inFlight && (!Number.isFinite(lastMs) || now - lastMs >= AUTO_MIN_MS);
  }

  /** Input for ScreenerLevels.add (source 'chart', note 'hermes'); price as exact decimal string. */
  function toStoreLevel(level, active, precision) {
    const p = Number(level.price);
    if (!Number.isFinite(p) || p <= 0 || !active) return null;
    const digits = Number.isInteger(precision) ? precision : decimals(p);
    const price = p.toFixed(Math.min(12, Math.max(0, digits))).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
    return { exchange: active.exchange, symbol: active.symbol, price, kind: 'simple', source: 'chart',
      color: TYPE_COLORS[level.type] || '#9aa7bd', note: 'hermes' };
  }

  // Chart interval → positioning period (mirrors analyst_positioning.PERIOD_FOR_INTERVAL).
  const POSITIONING_PERIOD = { 1: '5m', 3: '5m', 5: '5m', 15: '15m', 30: '30m', 60: '1h', 120: '1h', 180: '1h',
    240: '4h', 480: '4h', 720: '4h', D: '1d', '2D': '1d', '3D': '1d', W: '1d', M: '1d' };
  const POSITIONING_SYMBOL = { binance: /^[A-Z0-9]{2,30}$/, bybit: /^[A-Z0-9]{2,30}$/, hyperliquid: /^[A-Za-z0-9]{1,20}$/ };
  const POSITIONING_REFRESH_MS = 60000; // server caches ratios 60 s / OI 30 s
  /** Positioning panel target for the active pane, or { error } (seconds chart, HIP-3 symbol, …). */
  function positioningTarget(active) {
    if (!active || !EXCHANGES.includes(active.exchange) || typeof active.symbol !== 'string') return { error: 'No active chart' };
    const period = POSITIONING_PERIOD[active.interval];
    if (!period) return { error: `No positioning period for interval ${active.interval}` };
    if (!POSITIONING_SYMBOL[active.exchange].test(active.symbol)) return { error: `Positioning has no data for ${active.symbol}` };
    const coin = active.exchange === 'hyperliquid' ? active.symbol : active.symbol.replace(/^1000/, '').replace(/(USDT|USDC)$/, '');
    return { exchange: active.exchange, symbol: active.symbol, period, coin, key: `${active.exchange}:${active.symbol}:${period}` };
  }

  function statusText(result) {
    if (!result) return '';
    return { ok: 'Hermes analysis', hermes_unavailable: 'Hermes unavailable — baseline shown',
      invalid_output: 'Hermes output failed validation — baseline shown' }[result.status] || result.status;
  }

  /** 'long' → 'Long'; anything else → 'Neutral'. */
  function verdictWord(bias) { return bias === 'long' ? 'Long' : bias === 'short' ? 'Short' : 'Neutral'; }
  /** Chart interval code → short label: '15' → '15m', '60' → '1h', 'D' → '1D', '5s' → '5s'. */
  function intervalLabel(iv) {
    const s = String(iv ?? '');
    if (/^\d+$/.test(s)) { const m = Number(s); return m % 60 === 0 ? `${m / 60}h` : `${m}m`; }
    return /^\d*[DWM]$/.test(s) ? (/^\d/.test(s) ? s : `1${s}`) : s;
  }
  /** 'binance:BTCUSDT:15' → 'BTCUSDT 15m' (stale-analysis label). */
  function keyLabel(key) {
    const [, symbol, iv] = String(key || '').split(':');
    return symbol ? `${symbol} ${intervalLabel(iv)}` : '';
  }

  const model = { AUTO_MIN_MS, TYPE_COLORS, TYPE_LABELS, POSITIONING_PERIOD, POSITIONING_REFRESH_MS, positioningTarget, requestFor, keyOf, gauge, factorChips, splitLine, disagreementText, levelRows, freshness, statsLine,
    autoDue, toStoreLevel, statusText, fmtPrice, decimals, ago, verdictWord, intervalLabel, keyLabel };

  // ── DOM controller ─────────────────────────────────────────────────────────
  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }

  /**
   * Owns the analysis state (run, stats, auto refresh, chart lines) and renders the compact
   * sidebar summary. The full read lives in static/analysis-dashboard.js, which subscribes.
   * deps: { fetch, getActive(): {id, exchange, symbol, interval, precision} | null,
   *         drawLines(paneId, rows), clearLines(paneId), centerOn(paneId, price),
   *         levels (ScreenerLevels or null), visible(): bool, now(): ms }
   * A result for another pane/symbol/interval is kept (shown greyed as stale) until a new one arrives.
   */
  function mount(el, deps) {
    const now = deps.now || (() => Date.now());
    const st = { key: '', activeKey: '', result: null, stats: null, error: '', busy: false, auto: false, lastRun: NaN,
      showLines: true, linesPane: null, generation: 0, added: {} };
    const listeners = new Set();
    el.innerHTML = `<div class="analyst-head"><h2>Hermes analyst</h2><span class="analyst-target" data-role="target">—</span></div>
      <div data-role="summary" class="analyst-mini"></div>
      <p class="analyst-status" data-role="status" role="status">Analyze the blue-bordered chart from its real data.</p>
      <div class="analyst-actions"><button type="button" data-role="run">Analyze</button>
        <a class="analyst-open" data-role="open" href="#analysis-dashboard">Open full analysis ↓</a></div>`;
    const $ = role => el.querySelector(`[data-role="${role}"]`);

    function clearLines() {
      if (st.linesPane !== null) { try { deps.clearLines(st.linesPane); } catch (_) { /* pane gone */ } }
      st.linesPane = null;
    }
    function drawLines() {
      clearLines();
      const active = deps.getActive();
      if (!st.showLines || !st.result || !active || keyOf(active) !== st.key) return;
      try { deps.drawLines(active.id, levelRows(st.result)); st.linesPane = active.id; } catch (_) { /* pane not ready */ }
    }

    /** Read-only view for the dashboard (stale = the shown result belongs to another chart). */
    function view() {
      return { active: deps.getActive(), activeKey: st.activeKey, resultKey: st.key, result: st.result, stats: st.stats,
        error: st.error, busy: st.busy, auto: st.auto, showLines: st.showLines, lastRun: st.lastRun, added: st.added,
        stale: !!st.result && st.key !== st.activeKey };
    }

    function render() {
      const active = deps.getActive(), v = view();
      $('target').textContent = active ? `${active.exchange} · ${active.symbol} · ${intervalLabel(active.interval)}` : '—';
      $('run').disabled = st.busy;
      $('run').textContent = st.busy ? 'Analyzing…' : 'Analyze';
      $('status').textContent = st.busy ? 'Building snapshot and asking Hermes (up to ~60 s)…'
        : st.error || (v.stale ? `Shown: ${keyLabel(st.key)} — re-analyze for this chart` : statusText(st.result)) || 'Analyze the blue-bordered chart from its real data.';
      $('status').classList.toggle('is-error', !!st.error);
      const g = gauge(st.result, st.stats);
      $('summary').innerHTML = !g ? '' : `<div class="analyst-gauge${v.stale ? ' is-stale' : ''}" role="img" aria-label="${v.stale ? `Previous analysis for ${esc(keyLabel(st.key))}: ` : ''}Long ${g.long}%, short ${g.short}%">
          <div class="analyst-bar"><span class="long" style="width:${g.long}%"></span><span class="short" style="width:${g.short}%"></span></div>
          <div class="analyst-pcts"><span class="long">▲ ${g.long}%</span><span class="analyst-bias bias-${esc(g.bias)}">${esc(verdictWord(g.bias))} · ${esc(g.confidence)}</span><span class="short">${g.short}% ▼</span></div></div>`;
      listeners.forEach(fn => { try { fn(v); } catch (_) { /* subscriber error must not break the card */ } });
    }

    async function loadStats() {
      const active = deps.getActive();
      if (!active) return;
      const key = keyOf(active);
      const q = new URLSearchParams({ exchange: active.exchange, symbol: active.symbol, interval: active.interval });
      try {
        const resp = await deps.fetch(`/api/agent/stats?${q}`);
        // The pane may have switched market while this was in flight: drop stale stats.
        if (resp.ok) { const data = await resp.json(); if (keyOf(deps.getActive()) !== key) return; st.stats = data; }
      } catch (_) { /* keep previous */ }
      render();
    }

    async function run(force) {
      const active = deps.getActive(), req = requestFor(active);
      if (req.error) { st.error = req.error; render(); return; }
      const generation = ++st.generation, key = keyOf(active);
      st.busy = true; st.error = ''; st.lastRun = now(); render();
      try {
        const resp = await deps.fetch('/api/agent/analysis', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...req.body, force: !!force }) });
        const data = await resp.json().catch(() => ({}));
        if (generation !== st.generation) return;
        if (!resp.ok) { st.error = resp.status === 429 ? `Rate limited — retry in ${data.retry_after_s || 60}s` : `Analysis failed: ${data.error || resp.status}${data.detail ? ` (${data.detail})` : ''}`; return; }
        st.key = key; st.result = data; st.added = {};
      } catch (err) {
        if (generation === st.generation) st.error = `Analysis request failed: ${err && err.message || err}`;
      } finally {
        if (generation === st.generation) { st.busy = false; render(); drawLines(); loadStats(); }
      }
    }

    function activeChanged() {
      const active = deps.getActive(), key = keyOf(active);
      if (key !== st.activeKey) {
        // New chart: cancel an in-flight run, keep the old result (rendered as stale), refetch stats.
        st.activeKey = key; st.generation += 1; st.busy = false; st.error = ''; st.stats = null;
        clearLines(); loadStats();
      } else if (st.result && st.showLines && active && st.linesPane === active.id) { render(); return; }
      render(); drawLines();
    }

    function centerOn(price) { const active = deps.getActive(); if (active) deps.centerOn(active.id, price); }
    /** Save analysis level #index to the shared levels store; true on success. */
    function addLevel(index) {
      const active = deps.getActive();
      if (!active || !st.result || !deps.levels || st.key !== keyOf(active)) return false;
      const level = st.result.analysis.levels[Number(index)];
      const input = level && toStoreLevel(level, active, active.precision);
      try { if (!input) return false; deps.levels.add(input); st.added[index] = true; render(); return true; }
      catch (err) { st.error = `Levels store: ${err.message}`; render(); return false; }
    }

    $('run').addEventListener('click', () => run(true));
    const timer = setInterval(() => { if (autoDue(st.auto, deps.visible(), st.lastRun, now(), st.busy)) run(false); }, 15000);
    activeChanged();
    return { activeChanged, run, redraw: drawLines, view, centerOn, addLevel, render,
      setAuto(on) { st.auto = !!on; render(); },
      setLines(on) { st.showLines = !!on; drawLines(); render(); },
      subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
      listenerCount: () => listeners.size,
      destroy() { clearInterval(timer); clearLines(); listeners.clear(); }, _state: st };
  }

  return Object.assign({ mount, esc }, model);
});
