/* Analysis dashboard controller (/ui, full width below the charts).
 * Renders only while on screen (IntersectionObserver) or when Analyze is clicked; the
 * positioning poll runs only while visible; funding comes from the pane's own cache via
 * tick(). One root click/change listener, one observer, one timer — all removed in destroy().
 * UMD: window.AnalysisDashboard / module.exports.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./analysis-dashboard-model.js'), require('./analysis-dashboard-render.js'));
  else root.AnalysisDashboard = factory(root.AnalysisDashboardModel, root.AnalysisDashboardRender);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (M, R) {
  'use strict';
  const { A } = M;
  const POLL_MS = 30000;
  const SLOTS = ['verdict', 'summary', 'levels', 'factors', 'metrics', 'risks'];

  /** True when a wheel event at `target` lands on a chart (must zoom it, never scroll the page). */
  function isChartWheelTarget(target) {
    return !!(target && typeof target.closest === 'function' && target.closest('.chart-stage'));
  }
  /** Wheel over a chart: the chart library zooms it; we only stop the page from scrolling too.
   *  Bubble phase, so the chart's own handlers run first. Returns a disposer. */
  function installWheelGuard(grid) {
    const onWheel = event => { if (isChartWheelTarget(event.target) && event.cancelable) event.preventDefault(); };
    grid.addEventListener('wheel', onWheel, { passive: false });
    return () => grid.removeEventListener('wheel', onWheel, { passive: false });
  }

  /**
   * deps: { analyst (WorkspaceAnalyst controller), getFunding(): pane funding cache | null,
   *         fetch, now(), IntersectionObserver (ctor or null → always visible),
   *         pageVisible(): bool, setInterval, clearInterval }
   */
  function mount(el, deps) {
    const now = deps.now || (() => Date.now());
    const setI = deps.setInterval || setInterval, clearI = deps.clearInterval || clearInterval;
    const st = { visible: !deps.IntersectionObserver, posKey: '', positioning: null, whales: null, seq: 0, poll: null,
      funding: null, html: {}, alive: true, renders: 0 };
    el.innerHTML = `<div class="ad-sticky" data-slot="header" role="status"></div><div class="ad-body">${SLOTS.map(s => `<div class="ad-slot" data-slot="${s}"></div>`).join('')}</div>`;
    const slot = name => el.querySelector(`[data-slot="${name}"]`);

    function input(view) {
      const a = view.active || {};
      return { result: view.result, stale: view.stale, resultKey: view.resultKey, exchange: a.exchange, precision: a.precision,
        funding: st.funding, positioning: st.positioning, whales: st.whales, now: now() };
    }
    function put(name, html) {
      if (st.html[name] === html) return;
      st.html[name] = html; slot(name).innerHTML = html;
    }
    function render() {
      if (!st.alive || !st.visible) return;
      st.renders += 1;
      const view = { ...deps.analyst.view(), now: now() }, t = view.now;
      el.classList.toggle('is-stale', !!view.stale);
      put('header', R.headerHtml(view, t));
      put('verdict', R.verdictHtml(view, t));
      put('summary', R.summaryHtml(view));
      put('levels', R.levelsHtml(view, view.active && view.active.precision));
      put('factors', R.factorsHtml(view));
      put('metrics', R.metricsHtml(input(view)));
      put('risks', R.risksHtml(view));
    }

    async function refreshPositioning() {
      if (!st.alive || !st.visible || (deps.pageVisible && !deps.pageVisible())) return;
      const target = A.positioningTarget(deps.analyst.view().active), my = ++st.seq;
      if (target.error) { st.positioning = { error: target.error }; st.whales = { error: target.error }; render(); return; }
      const q = new URLSearchParams({ exchange: target.exchange, symbol: target.symbol, period: target.period, owner: 'analysis-dashboard' });
      const get = url => deps.fetch(url).then(async r => { const d = await r.json().catch(() => ({})); return r.ok ? d : { error: d.error || `HTTP ${r.status}` }; })
        .catch(err => ({ error: `request failed: ${err && err.message || err}` }));
      const [pos, wh] = await Promise.all([get(`/api/positioning?${q}`),
        get(`/api/positioning/whales?${new URLSearchParams({ coin: target.coin, owner: 'analysis-dashboard' })}`)]);
      if (!st.alive || my !== st.seq) return; // pane/symbol switched while in flight
      st.positioning = pos; st.whales = wh; render();
    }

    function syncPosKey() {
      const t = A.positioningTarget(deps.analyst.view().active), key = t.error ? `err:${t.error}` : t.key;
      if (key === st.posKey) return false;
      st.posKey = key; st.positioning = null; st.whales = null; st.seq += 1;
      return true;
    }
    function startPoll() { if (st.poll === null) st.poll = setI(refreshPositioning, POLL_MS); }
    function stopPoll() { if (st.poll !== null) { clearI(st.poll); st.poll = null; } }

    function setVisible(on) {
      if (!st.alive || on === st.visible) return;
      st.visible = on;
      if (on) { syncPosKey(); st.funding = deps.getFunding(); render(); refreshPositioning(); startPoll(); } else stopPoll();
    }
    const onAnalyst = () => {
      const changed = syncPosKey();
      if (!st.visible) return;
      if (changed) { st.funding = deps.getFunding(); refreshPositioning(); }
      render();
    };
    const unsubscribe = deps.analyst.subscribe(onAnalyst);

    function onClick(event) {
      const b = event.target.closest && event.target.closest('[data-act]');
      if (!b || !el.contains(b) || b.tagName === 'INPUT') return;
      const act = b.dataset.act;
      if (act === 'run') { setVisible(true); deps.analyst.run(true); }
      else if (act === 'center') deps.analyst.centerOn(Number(b.dataset.price));
      else if (act === 'save') deps.analyst.addLevel(Number(b.dataset.index));
      else if (act === 'top') { event.preventDefault(); if (deps.scrollToCharts) deps.scrollToCharts(); }
    }
    function onChange(event) {
      const act = event.target && event.target.dataset && event.target.dataset.act;
      if (act === 'auto') deps.analyst.setAuto(event.target.checked);
      else if (act === 'lines') deps.analyst.setLines(event.target.checked);
    }
    el.addEventListener('click', onClick);
    el.addEventListener('change', onChange);

    let io = null;
    if (deps.IntersectionObserver) {
      // The dashboard starts exactly at the fold (charts are 100dvh) and edge-adjacent counts as
      // intersecting, so require it to be 24 px into the viewport before rendering/polling.
      io = new deps.IntersectionObserver(entries => entries.forEach(e => setVisible(e.isIntersecting)), { rootMargin: '0px 0px -24px 0px' });
      io.observe(el);
    } else { syncPosKey(); st.funding = deps.getFunding(); render(); refreshPositioning(); startPoll(); }

    /** Called by the workspace's existing 1 s funding tick: no timer of our own. */
    function tick() {
      if (!st.alive || !st.visible) return;
      const f = deps.getFunding();
      if (f !== st.funding) { st.funding = f; render(); return; }
      const view = { ...deps.analyst.view() };
      put('header', R.headerHtml(view, now()));
      el.querySelectorAll('[data-countdown]').forEach(n => { n.textContent = M.F.countdown(Number(n.dataset.countdown) - now()); });
    }

    function destroy() {
      if (!st.alive) return;
      st.alive = false; st.seq += 1; stopPoll(); unsubscribe();
      if (io) { io.disconnect(); io = null; }
      el.removeEventListener('click', onClick);
      el.removeEventListener('change', onChange);
    }
    return { render, tick, destroy, setVisible, refreshPositioning, _state: st,
      debug: () => ({ visible: st.visible, polling: st.poll !== null, observer: !!io, renders: st.renders, alive: st.alive }) };
  }

  return { mount, installWheelGuard, isChartWheelTarget, POLL_MS };
});
