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

  /** Gauge model; percentages are clamped integers summing to 100. */
  function gauge(result, stats) {
    const a = result && result.analysis;
    if (!a) return null;
    const long = Math.max(0, Math.min(100, Math.round(Number(a.long_pct) || 0)));
    const calibrated = !!(stats && stats.overall && stats.overall.n >= 20);
    return { long, short: 100 - long, bias: a.bias, confidence: a.confidence,
      source: result.source, calibrated,
      caption: result.source === 'baseline' ? 'Deterministic confluence baseline (Hermes did not answer)'
        : calibrated ? 'Agent estimate · see hit rate below' : 'Agent estimate, not a calibrated probability' };
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

  function statsLine(stats) {
    if (!stats || !stats.overall) return 'Calibration: no data yet';
    const o = stats.overall;
    if (!o.n) return `Calibration: ${stats.total || 0} logged, ${stats.pending || 0} pending — no outcomes scored yet`;
    return `Calibration: hit rate ${(o.hit_rate * 100).toFixed(0)}% over n=${o.n} (win ${o.win} · loss ${o.loss} · timeout ${o.timeout} · ambiguous ${o.ambiguous})${o.n < 20 ? ' · small sample' : ''}`;
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

  function statusText(result) {
    if (!result) return '';
    return { ok: 'Hermes analysis', hermes_unavailable: 'Hermes unavailable — baseline shown',
      invalid_output: 'Hermes output failed validation — baseline shown' }[result.status] || result.status;
  }

  const model = { AUTO_MIN_MS, TYPE_COLORS, TYPE_LABELS, requestFor, keyOf, gauge, levelRows, freshness, statsLine,
    autoDue, toStoreLevel, statusText, fmtPrice, decimals, ago };

  // ── DOM controller ─────────────────────────────────────────────────────────
  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }

  /**
   * deps: { fetch, getActive(): {id, exchange, symbol, interval, precision} | null,
   *         drawLines(paneId, rows) , clearLines(paneId), centerOn(paneId, price),
   *         levels (ScreenerLevels or null), visible(): bool, now(): ms }
   */
  function mount(el, deps) {
    const now = deps.now || (() => Date.now());
    const st = { key: '', result: null, stats: null, error: '', busy: false, auto: false, lastRun: NaN,
      showLines: true, linesPane: null, generation: 0 };
    el.innerHTML = `<div class="analyst-head"><h2>Hermes analyst</h2><span class="analyst-target" data-role="target">—</span></div>
      <div class="analyst-actions"><button type="button" data-role="run">Analyze</button>
        <label title="Re-run at most every 2 minutes while the page is visible"><input type="checkbox" data-role="auto"> Auto · 2m</label>
        <label title="Draw agent levels on the active chart"><input type="checkbox" data-role="lines" checked> Lines</label></div>
      <p class="analyst-status" data-role="status" role="status">Analyze the blue-bordered chart from its real data.</p>
      <div data-role="body"></div>
      <p class="analyst-stats" data-role="stats"></p>
      <p class="analyst-disclaimer">Not financial advice. Research only — no orders are placed.</p>`;
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

    function render() {
      const active = deps.getActive();
      $('target').textContent = active ? `${active.exchange} · ${active.symbol} · ${active.interval}` : '—';
      $('run').disabled = st.busy;
      $('run').textContent = st.busy ? 'Analyzing…' : 'Analyze';
      $('status').textContent = st.busy ? 'Building snapshot and asking Hermes (up to ~60 s)…' : st.error || statusText(st.result) || 'Analyze the blue-bordered chart from its real data.';
      $('status').classList.toggle('is-error', !!st.error);
      $('stats').textContent = statsLine(st.stats);
      const r = st.result, g = gauge(r, st.stats);
      if (!r || !g) { $('body').innerHTML = ''; return; }
      const a = r.analysis, precision = active && keyOf(active) === st.key ? active.precision : undefined;
      const rows = levelRows(r).map(row => `<li class="analyst-level${row.unanchored ? ' is-unanchored' : ''}">
          <button type="button" class="analyst-level-go" data-price="${row.price}" title="${esc(row.reason)}\nsource: ${esc(row.sources.join(', '))}">
            <i style="background:${row.color}"></i><span>${esc(row.label)}</span><b>${esc(fmtPrice(row.price, precision))}</b>
            <em>${row.distPct === null ? '' : `${row.distPct >= 0 ? '+' : ''}${row.distPct.toFixed(2)}%`}</em></button>
          <button type="button" class="analyst-level-add" data-index="${row.index}" title="Add to shared levels (source chart, note hermes)" aria-label="Add ${esc(row.label)} ${esc(fmtPrice(row.price))} to levels">＋</button></li>`).join('');
      const list = (items, fn) => items.length ? `<ul>${items.map(fn).join('')}</ul>` : '<p class="analyst-empty">None</p>';
      $('body').innerHTML = `
        <div class="analyst-gauge" role="img" aria-label="Long ${g.long}%, short ${g.short}%">
          <div class="analyst-bar"><span class="long" style="width:${g.long}%"></span><span class="short" style="width:${g.short}%"></span></div>
          <div class="analyst-pcts"><span class="long">LONG ${g.long}%</span><span class="analyst-bias bias-${esc(g.bias)}">${esc(g.bias)} · ${esc(g.confidence)}</span><span class="short">${g.short}% SHORT</span></div>
          <p class="analyst-caption">${esc(g.caption)}${a.horizon ? ` · horizon ${esc(a.horizon)}` : ''}</p></div>
        ${a.summary_ru ? `<p class="analyst-summary" lang="ru">${esc(a.summary_ru)}</p>` : ''}
        ${r.warnings && r.warnings.length ? `<details class="analyst-warnings"><summary>⚠ ${r.warnings.length} validation note(s)</summary>${list(r.warnings, w => `<li>${esc(w)}</li>`)}</details>` : ''}
        <h3>Levels <small>click to centre · ＋ to save</small><button type="button" data-role="fit" title="Restore normal price autoscale">Fit</button></h3><ul class="analyst-levels">${rows || '<li class="analyst-empty">No levels</li>'}</ul>
        <details class="analyst-section"><summary>Scenarios · ${a.scenarios.length}</summary>${list(a.scenarios, s => `<li><b>${esc(s.name)}</b>: ${esc(s.trigger)}${s.target ? ` → ${esc(s.target)}` : ''}${s.invalidation ? ` <span class="inval">✕ ${esc(s.invalidation)}</span>` : ''}</li>`)}</details>
        <details class="analyst-section"><summary>Key factors · ${a.key_factors.length}</summary>${list(a.key_factors, f => `<li title="${esc(f.source_fields.join(', '))}">${esc(f.text)}</li>`)}</details>
        <details class="analyst-section"><summary>Risks · ${a.risks.length}</summary>${list(a.risks, x => `<li>${esc(x)}</li>`)}</details>
        <p class="analyst-fresh">${esc(freshness(r, now()))}</p>`;
    }

    async function loadStats() {
      const active = deps.getActive();
      if (!active) return;
      const q = new URLSearchParams({ exchange: active.exchange, symbol: active.symbol, interval: active.interval });
      try { const resp = await deps.fetch(`/api/agent/stats?${q}`); if (resp.ok) st.stats = await resp.json(); } catch (_) { /* keep previous */ }
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
        st.key = key; st.result = data;
      } catch (err) {
        if (generation === st.generation) st.error = `Analysis request failed: ${err && err.message || err}`;
      } finally {
        if (generation === st.generation) { st.busy = false; render(); drawLines(); loadStats(); }
      }
    }

    function activeChanged() {
      const active = deps.getActive(), key = keyOf(active);
      if (key === st.key && active) {
        $('target').textContent = `${active.exchange} · ${active.symbol} · ${active.interval}`;
        if (!st.result || !st.showLines || st.linesPane === active.id) return;
      }
      if (key !== st.key) { st.generation += 1; st.busy = false; st.result = null; st.error = ''; st.key = key; st.stats = null; clearLines(); loadStats(); }
      render(); drawLines();
    }

    el.addEventListener('click', event => {
      const go = event.target.closest('.analyst-level-go'), add = event.target.closest('.analyst-level-add');
      const active = deps.getActive();
      if (go && active) deps.centerOn(active.id, Number(go.dataset.price));
      if (event.target.closest('[data-role="fit"]') && active) deps.centerOn(active.id, null);
      if (add && active && st.result && deps.levels) {
        const level = st.result.analysis.levels[Number(add.dataset.index)];
        const input = level && toStoreLevel(level, active, active.precision);
        try { if (input) { deps.levels.add(input); add.textContent = '✓'; add.disabled = true; } }
        catch (err) { st.error = `Levels store: ${err.message}`; render(); }
      }
    });
    $('run').addEventListener('click', () => run(true));
    $('auto').addEventListener('change', e => { st.auto = e.target.checked; });
    $('lines').addEventListener('change', e => { st.showLines = e.target.checked; drawLines(); });
    const timer = setInterval(() => { if (autoDue(st.auto, deps.visible(), st.lastRun, now(), st.busy)) run(false); }, 15000);
    activeChanged();
    return { activeChanged, run, redraw: drawLines, destroy() { clearInterval(timer); clearLines(); }, _state: st };
  }

  return Object.assign({ mount, esc }, model);
});
