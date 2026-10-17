/* /scalp page shell: columns grid, one rAF paint per frame for all dirty columns, view hotkeys,
 * per-column settings popover and versioned persistence. View only — there is no order path.
 */
(function () {
  'use strict';
  const S = window.ScalpSettings, Column = window.ScalpColumn, U = window.ScalpPageUtil;
  const $ = (sel, el = document) => el.querySelector(sel);
  const grid = $('#scalp-grid'), footer = $('#scalp-status');

  const FIELDS = [
    ['Ladder', [['units', 'Volumes', ['usd', 'coin']], ['scaleMode', 'Scale', ['ticks', 'percent']], ['scaleIndex', 'Scale step (1–5)', [0, 1, 2, 3, 4]],
      ['rowPx', 'Row height px', 'number'], ['grid', 'Grid every N rows', [0, 10, 50]], ['abbreviate', 'Abbreviate (1.2K)', 'bool'],
      ['autocenter', 'Autocenter', 'bool'], ['autocenterPct', 'Autocenter distance %', 'number'], ['fullScaleUsd', 'Full-scale $ (0 = auto)', 'number'],
      ['big1Usd', 'Big volume 1 ≥ $', 'number'], ['big2Usd', 'Big volume 2 ≥ $', 'number'], ['densityUsd', 'Density lifetime ≥ $', 'number']]],
    ['Prints', [['feed.spanMs', 'Visible span ms', 'number'], ['feed.minUsd', 'Hide prints < $', 'number'], ['feed.labelMinUsd', 'Label prints ≥ $', 'number'],
      ['feed.aggregateMs', 'Aggregate same side within ms', 'number'], ['feed.aggregateTicks', '…and within ticks (0 = any)', 'number'],
      ['feed.line', 'Connecting line', 'bool'], ['feed.maxRadius', 'Max radius px', 'number'], ['feed.maxSizeUsd', 'Max radius at $', 'number']]],
    ['Clusters', [['clusters.count', 'Columns (0–8)', 'number'], ['clusters.intervalMs', 'Timeframe', [60000, 300000, 900000]],
      ['clusters.mode', 'Mode', ['volume', 'delta', 'bidask']], ['clusters.showMinUsd', 'Show values ≥ $', 'number'], ['clusters.markMinUsd', 'Mark cells ≥ $', 'number']]]
  ];
  const getPath = (obj, path) => path.split('.').reduce((o, k) => o?.[k], obj);
  function setPath(obj, path, value) {
    const keys = path.split('.'), last = keys.pop(), target = keys.reduce((o, k) => o[k], obj);
    target[last] = value;
  }

  const page = {
    settings: S.load(localStorage), columns: [], dirtySet: new Set(), frame: null, perf: { frames: [], paints: 0 },
    persist() { if (!S.save(localStorage, this.settings)) footer.textContent = 'Browser storage unavailable · settings last for this session'; },
    dirty(col) { this.dirtySet.add(col); if (this.frame === null) this.frame = requestAnimationFrame(t => this.paint(t)); },
    paint() {
      this.frame = null;
      const start = performance.now(), now = Date.now();
      for (const col of this.dirtySet) if (col.element.isConnected) col.draw(now);
      const count = this.dirtySet.size; this.dirtySet.clear();
      const ms = performance.now() - start;
      this.perf.paints += 1; this.perf.frames.push({ ms, columns: count });
      if (this.perf.frames.length > 1200) this.perf.frames.shift();
    },
    layout() {
      const n = this.settings.columns;
      grid.dataset.columns = String(n); $('#scalp-columns').value = String(n);
      while (this.columns.length > n) this.columns.pop().dispose();
      while (this.columns.length < n) { const col = new Column(this.columns.length, this); this.columns.push(col); grid.append(col.element); }
      this.setActive(Math.min(this.settings.active, n - 1));
    },
    setActive(index) {
      this.settings.active = index;
      this.columns.forEach((c, k) => c.element.classList.toggle('active', k === index));
      this.persist();
    },
    /** Apply a column change: 'restart' (venue/symbol), 'regroup', 'feed', 'clusters', 'density' or a plain redraw. */
    updateColumn(index, patch, action) {
      const col = this.columns[index];
      const next = S.sanitizeColumn({ ...col.cfg, ...patch }, col.cfg);
      this.settings.cols[index] = next; col.cfg = next; col.view.rowPx = next.rowPx; this.persist();
      if (action === 'restart') { const form = $('form', col.element); form.venue.value = next.venue; form.symbol.value = next.symbol; col.start(); }
      if (action === 'regroup') col.regroup();
      if (action === 'feed') col.data?.setFeedAggregation(next.feed);
      if (action === 'clusters') col.data?.setClusters(next.clusters);
      if (action === 'density') col.density.since.clear();
      col.renderText(); this.dirty(col);
    },
    toggleSettings(col) {
      const open = $('.dom-settings', col.element);
      if (open) { open.remove(); $('.settings-btn', col.element).setAttribute('aria-expanded', 'false'); return; }
      $('.settings-btn', col.element).setAttribute('aria-expanded', 'true');
      const panel = document.createElement('form'); panel.className = 'dom-settings'; panel.setAttribute('aria-label', 'Column settings');
      panel.innerHTML = `<h3>${col.cfg.symbol} · view settings</h3>`;
      for (const [legend, fields] of FIELDS) {
        const set = document.createElement('fieldset'); set.innerHTML = `<legend>${legend}</legend>`;
        for (const [path, label, type] of fields) {
          const row = document.createElement('label'), value = getPath(col.cfg, path);
          row.append(document.createTextNode(label));
          let input;
          if (Array.isArray(type)) {
            input = document.createElement('select');
            for (const opt of type) { const o = document.createElement('option'); o.value = String(opt); o.textContent = path === 'clusters.intervalMs' ? S.CLUSTER_INTERVALS[opt] : path === 'scaleIndex' ? String(opt + 1) : String(opt); input.append(o); }
            input.value = String(value);
          } else if (type === 'bool') { input = document.createElement('input'); input.type = 'checkbox'; input.checked = Boolean(value); }
          else { input = document.createElement('input'); input.type = 'number'; input.step = 'any'; input.value = String(value); }
          input.dataset.path = path; row.append(input); set.append(row);
        }
        panel.append(set);
      }
      const foot = document.createElement('footer');
      foot.innerHTML = '<button type="button" data-act="reset">Defaults</button><button type="button" data-act="close">Close</button>';
      panel.append(foot);
      panel.addEventListener('change', event => {
        const input = event.target, path = input.dataset.path;
        if (!path) return;
        const raw = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
        const value = typeof getPath(col.cfg, path) === 'number' ? Number(raw) : raw;
        const draft = structuredClone(col.cfg); setPath(draft, path, value);
        const action = path.startsWith('feed.aggregate') ? 'feed' : path.startsWith('clusters.') ? 'clusters'
          : ['scaleMode', 'scaleIndex'].includes(path) ? 'regroup' : path === 'densityUsd' ? 'density' : 'redraw';
        this.updateColumn(col.index, draft, action);
        const shown = getPath(col.cfg, path);
        if (input.type === 'checkbox') input.checked = shown; else input.value = String(shown); // show the sanitised value
      });
      foot.addEventListener('click', event => {
        const act = event.target.dataset.act;
        if (act === 'close') this.toggleSettings(col);
        if (act === 'reset') {
          const d = S.defaultColumn(col.cfg.venue, col.cfg.symbol);
          this.updateColumn(col.index, d, 'regroup'); col.data?.setFeedAggregation(d.feed); col.data?.setClusters(d.clusters);
          this.toggleSettings(col); this.toggleSettings(col);
        }
      });
      $('.dom-body', col.element).append(panel);
    }
  };

  document.addEventListener('keydown', event => {
    if (event.target.closest('input, select, textarea') || event.altKey || event.ctrlKey || event.metaKey) return;
    const col = page.columns[page.settings.active];
    if (!col) return;
    const { column, action } = S.hotkey(col.cfg, event.key);
    if (!action) return;
    event.preventDefault();
    if (action === 'center') { page.updateColumn(col.index, column, 'redraw'); col.center(); }
    else if (action === 'clear-ruler') { col.ruler = { anchorRow: null, hoverRow: null }; page.dirty(col); }
    else page.updateColumn(col.index, column, action);
  });
  $('#scalp-columns').addEventListener('change', event => { page.settings.columns = Number(event.target.value); page.persist(); page.layout(); });
  $('#scalp-help-toggle').addEventListener('click', event => {
    const help = $('#scalp-help'); help.hidden = !help.hidden; event.currentTarget.setAttribute('aria-expanded', String(!help.hidden));
  });
  // The feed scrolls with time and density ages tick: repaint at ≤10 Hz even without book traffic.
  setInterval(() => { if (document.visibilityState === 'visible') page.columns.forEach(c => page.dirty(c)); }, 100);
  setInterval(() => { if (document.visibilityState === 'visible') page.columns.forEach(c => c.renderText()); }, 500);
  window.addEventListener('pagehide', () => { page.columns.forEach(c => c.stop()); U.hub.dispose(); });
  page.layout();

  function perfSummary() {
    const ms = page.perf.frames.map(f => f.ms).sort((a, b) => a - b), q = p => ms[Math.min(ms.length - 1, Math.floor(p * ms.length))] ?? null;
    return { paints: page.perf.paints, sampled: ms.length, avgMs: ms.length ? ms.reduce((a, b) => a + b, 0) / ms.length : null, p50Ms: q(0.5), p95Ms: q(0.95), maxMs: ms.at(-1) ?? null };
  }
  window.__scalp = { page, perfSummary, evidence: U.evidence };
})();
