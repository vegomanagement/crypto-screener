/* Chart drawing tools — entry point. Composes the modules under static/drawings/:
 *   geometry.js (pixel maths) → core.js (registry, schema v2, history, templates, level sync)
 *   → tools-*.js (lines/channels, fib, measure, shapes/annotation, patterns) → toolbar-model.js
 *   → controller.js (state + input + canvas) → ui.js / ui-panels.js (toolbar, mini-toolbar, dialog, tree).
 * Pane contract (unchanged): createController({ header, stage, paneElement, levels, requestDraw,
 * getContext, onError }) → { draw(context, plotWidth, plotHeight), reload, dispose, ... }.
 * Storage: localStorage 'screener.drawings.v2' (v1 is migrated once and left in place as a backup).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    const core = require('./drawings/core.js');
    ['lines', 'fib', 'measure', 'shapes', 'patterns', 'advanced'].forEach(name => require(`./drawings/tools-${name}.js`));
    module.exports = factory(core, require('./drawings/toolbar-model.js'), require('./drawings/controller.js'), null, null);
  } else {
    root.WorkspaceDrawings = factory(root.WorkspaceDrawingsCore, root.WorkspaceDrawingsToolbar, root.WorkspaceDrawingsController, root.WorkspaceDrawingsUi, root.WorkspaceDrawingsPanels);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (C, TB, CTRL, UI, PANELS) {
  'use strict';
  // Text widths from a shared offscreen canvas (cached) so labels hit-test exactly as drawn.
  function installMeasurer() {
    if (typeof document === 'undefined') return;
    const ctx = document.createElement('canvas').getContext('2d'), cache = new Map();
    C.setTextMeasurer((text, size, bold) => {
      const key = `${size}|${bold ? 1 : 0}|${text}`;
      let w = cache.get(key);
      if (w === undefined) {
        ctx.font = `${bold ? '600 ' : ''}${size}px SFMono-Regular, Consolas, monospace`; w = ctx.measureText(text).width;
        if (cache.size > 5000) cache.clear();
        cache.set(key, w);
      }
      return w;
    });
  }
  // Live controllers (one per pane) — read-only diagnostics for tests and the console.
  const instances = new Set();
  function createController(options) {
    if (!UI || !PANELS) return CTRL.createController(options); // node / headless use
    let chrome = null, panels = null;
    const controller = CTRL.createController({ ...options,
      onEditText: id => panels?.openSettings(id, 'text'),
      onOpenSettings: id => panels?.openSettings(id),
      onEscape: () => { panels?.close(false); chrome?.closeFlyout(); },
      afterDraw: (state, view) => chrome?.positionMini(state, view) });
    panels = PANELS.createPanels(controller, { stage: options.stage });
    chrome = UI.createChrome(controller, { header: options.header, stage: options.stage, panels });
    options.stage.addEventListener('dw-redraw', () => options.requestDraw());
    controller.subscribe(() => { chrome.sync(); panels.renderTree(); });
    chrome.sync();
    const dispose = controller.dispose;
    instances.add(controller);
    return Object.assign(controller, { panels, chrome, dispose() { instances.delete(controller); dispose(); chrome.dispose(); panels.dispose(); } });
  }
  installMeasurer();
  return { ...C, TB, createController, instances };
});
