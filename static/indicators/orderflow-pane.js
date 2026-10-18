/* Pane glue for the order-flow overlays (big-trade bubbles + abnormal volume).
 * Keeps static/workspace.js to a few wiring lines: the pane passes accessors in,
 * this module owns the controllers, per-frame drawing, tooltip and status line.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory;
  else root.WorkspaceOrderflowPane = factory(root);
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';
  const B = root.WorkspaceBubbles, BR = root.WorkspaceBubblesRender, V = root.WorkspaceVolumeAnomaly, FP = root.WorkspaceFootprint;
  const FP_INTERVALS = new Set(['1', '3', '5', '15', '30', '60']);
  /**
   * deps: { pane, hub, fetch, stage, statusAfter, isEnabled(id), settings(id), config(), step() (s), tick(),
   *         tickKnown(), forming() → bar|null, mainFootprint() → store|null, format(price), requestDraw() }
   */
  function attach(deps) {
    const { pane } = deps;
    const tip = document.createElement('div'); tip.className = 'orderflow-tooltip'; tip.hidden = true; deps.stage.append(tip);
    const status = document.createElement('div'); status.className = 'pane-orderflow-status'; status.setAttribute('role', 'status'); status.hidden = true;
    deps.statusAfter.after(status);
    const bubbles = B && BR ? BR.createController({ fetch: deps.fetch, hub: deps.hub, onChange: deps.requestDraw }) : null;
    const deltaFp = FP ? FP.createController({ fetch: deps.fetch, hub: deps.hub, onChange: deps.requestDraw,
      visible: () => document.visibilityState === 'visible' }) : null;
    let bubbleHits = [], volumeHits = [], cache = { key: null, flags: [], bars: new Map() }, deltaCache = new Map();
    const frameStats = { last: 0, drawn: 0 };

    function sync() {
      const config = deps.config();
      if (bubbles) {
        if (deps.isEnabled(B.ID) && deps.tickKnown()) bubbles.enable({ exchange: config.exchange, symbol: config.symbol, tick: deps.tick(), settings: deps.settings(B.ID) });
        else if (!deps.isEnabled(B.ID)) bubbles.disable();
      }
      if (deltaFp) {
        const s = V ? deps.settings(V.ID) : null;
        const want = V && deps.isEnabled(V.ID) && s.showDelta && FP_INTERVALS.has(config.interval) && !deps.mainFootprint();
        if (want) deltaFp.enable({ exchange: config.exchange, symbol: config.symbol, interval: config.interval, bars: Math.min(300, Math.max(60, s.lookback * 2)), groupTicks: null });
        else deltaFp.disable();
      }
    }
    function flagsFor(candles, forming, s) {
      const last = candles.at(-1);
      const key = `${candles.length}:${candles[0]?.time}:${last?.time}:${last?.volume}:${JSON.stringify(s)}`;
      if (cache.key !== key || cache.candles !== candles) {
        cache = { key, candles, flags: V.compute(candles, s), bars: new Map(candles.map(c => [c.time, c])) };
        deltaCache = new Map();
      }
      const out = cache.flags.slice();
      const live = forming && last && forming.time > last.time ? V.compute(candles.slice(-Math.round(s.lookback)), s, forming).filter(f => f.forming) : [];
      return { flags: out.concat(live), bar: t => (forming && t === forming.time ? forming : cache.bars.get(t)) };
    }
    function deltaFor(timeSec) {
      const store = deps.mainFootprint() || deltaFp?.store;
      const entry = store?.bars?.get(timeSec * 1000);
      if (!entry) return null;
      const hit = deltaCache.get(timeSec);
      if (hit && hit.version === entry.bar.version && hit.coverage === entry.coverage && hit.forming === entry.forming) return hit.value;
      const value = V.footprintDelta(entry);
      deltaCache.set(timeSec, { version: entry.bar.version, coverage: entry.coverage, forming: entry.forming, value });
      return value;
    }
    function draw(ctx, plotWidth, plotHeight) {
      sync();
      const candles = pane.candles; bubbleHits = []; volumeHits = [];
      if (!candles.length) { renderStatus(); return; }
      const scale = pane.chart.timeScale(), bs = scale.options().barSpacing, anchor = candles.length - 1;
      const anchorX = scale.logicalToCoordinate(anchor);
      if (anchorX === null || !Number.isFinite(anchorX)) return;
      const logicalX = l => anchorX + (l - anchor) * bs; // affine from ONE integer anchor (never a fractional library call)
      const centerX = t => { const l = pane.logicalForTime(t); return l === null ? NaN : logicalX(l); };
      const y = price => pane.series.priceToCoordinate(price);
      if (V && deps.isEnabled(V.ID)) {
        const s = deps.settings(V.ID), { flags, bar } = flagsFor(candles, deps.forming(), s);
        const base = pane.volume.priceToCoordinate(0);
        volumeHits = V.draw(ctx, flags, { centerX, y, bar, volY: v => pane.volume.priceToCoordinate(v), volBaseY: base, barSpacing: bs, plotWidth, plotHeight }, s, deltaFor);
      }
      if (bubbles?.store && deps.isEnabled(B.ID)) {
        const range = scale.getVisibleLogicalRange(), step = deps.step();
        const timeAt = l => { const i = Math.max(0, Math.min(anchor, Math.floor(l))); return candles[i].time + (l - i) * step; };
        const view = { fromMs: range ? timeAt(range.from - 1) * 1000 : 0, toMs: range ? timeAt(range.to + 1) * 1000 : Infinity,
          xForMs: ms => { const l = pane.logicalForTime(ms / 1000); return l === null ? NaN : logicalX(l - 0.5); }, y, barSpacing: bs, plotWidth, plotHeight };
        const t0 = performance.now();
        const result = BR.draw(ctx, bubbles.store, view, deps.settings(B.ID));
        frameStats.last = performance.now() - t0; frameStats.drawn = result.drawn; frameStats.capped = result.capped;
        bubbleHits = result.hits;
      }
      renderStatus();
    }
    function renderStatus() {
      const parts = [];
      if (bubbles && deps.isEnabled(B.ID)) parts.push(deps.tickKnown() ? bubbles.statusText() + (frameStats.capped ? ` · showing the ${frameStats.drawn} largest in view` : '') : 'Big trades: waiting for instrument tick size…');
      if (V && deps.isEnabled(V.ID)) {
        const s = deps.settings(V.ID), interval = deps.config().interval;
        if (s.showDelta) parts.push(!FP_INTERVALS.has(interval) ? 'Abnormal volume: delta needs footprint (1m–1h); none on this interval'
          : deps.mainFootprint() ? 'Abnormal volume: delta from this chart\'s footprint' : deltaFp?.status?.state === 'error' ? `Abnormal volume: footprint for delta failed (${deltaFp.status.message})` : 'Abnormal volume: delta from footprint (recent bars only; ? = partial/forming)');
      }
      const text = parts.join(' · ');
      status.hidden = !text; if (status.textContent !== text) { status.textContent = text; status.title = text; }
    }
    function tooltip(point) {
      let text = '';
      if (point) {
        const p = bubbleHits.length ? BR.hitTest(bubbleHits, point.x, point.y) : null;
        if (p) text = BR.tooltipText(p, { format: deps.format, settings: deps.settings(B.ID), tick: deps.tick() });
        else { const h = volumeHits.length ? V.hitTest(volumeHits, point.x, point.y) : null; if (h) text = V.tooltipText(h, deps.settings(V.ID)); }
      }
      tip.hidden = !text; if (!text) return;
      tip.textContent = text;
      tip.style.left = `${Math.max(4, Math.min(point.x + 14, deps.stage.clientWidth - 330))}px`;
      tip.style.top = `${Math.max(30, Math.min(point.y + 14, deps.stage.clientHeight - 140))}px`;
    }
    function dispose() { bubbles?.disable(); deltaFp?.disable(); tip.remove(); status.remove(); }
    return { sync, draw, tooltip, dispose, get bubbles() { return bubbles; }, get deltaFootprint() { return deltaFp; }, get hits() { return { bubbles: bubbleHits, volume: volumeHits }; }, frameStats };
  }
  return { attach };
});
