/* Research-only workspace. Pinned Lightweight Charts 4.1.3; no trading APIs. */
(() => {
  'use strict';
  const STORAGE_KEY = 'crypto-screener.workspace.v1';
  const VERSION = 1;
  const LAYOUTS = { '1': 1, '2cols': 2, '2rows': 2, '3cols': 3, '4grid': 4, '6grid': 6 };
  const FAVORITES = [['30s', '30s'], ['1', '1m'], ['3', '3m'], ['5', '5m'], ['10', '10m'], ['12', '12m'], ['15', '15m'], ['30', '30m'], ['45', '45m'], ['60', '1h'], ['120', '2h'], ['180', '3h'], ['240', '4h'], ['480', '8h'], ['720', '12h'], ['D', 'D'], ['2D', '2D'], ['3D', '3D'], ['W', 'W'], ['M', 'M']];
  const WATCHLIST = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', '1000BONKUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'SUIUSDT', 'AVAXUSDT', 'LINKUSDT'];
  const EMA_COLORS = { 7: '#d4ac5c', 14: '#b593d2', 28: '#6b98cf', 200: '#c9d2df' };
  const $ = (selector, root = document) => root.querySelector(selector);
  const grid = $('#chart-grid');
  const footer = $('#workspace-status');
  const panes = new Map();
  let intervals = FAVORITES.map(([value, label]) => ({ value, label, available: value !== '30s', reason: value === '30s' ? 'Real 30-second candles require trade/tick data' : '' }));
  let intervalWarning = '';
  let pricesController = null;
  let pricesGeneration = 0;
  let pricesTimeout = null;
  let initialized = false;
  const numberFormat = new Intl.NumberFormat('en-US', { maximumSignificantDigits: 8 });
  const timeFormat = new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

  const defaultPanes = () => [
    { symbol: 'BTCUSDT', interval: '240' }, { symbol: 'ETHUSDT', interval: '240' },
    { symbol: 'SOLUSDT', interval: '240' }, { symbol: '1000BONKUSDT', interval: 'D' },
    { symbol: 'BNBUSDT', interval: '60' }, { symbol: 'XRPUSDT', interval: '60' }
  ];
  const normalizeSymbol = value => typeof value === 'string' ? value.trim().toUpperCase().replace(/^BYBIT:/, '').replace(/\.P$/, '').replace(/^([A-Z0-9]+)\/USDT$/, '$1USDT') : '';
  const validSymbol = value => typeof value === 'string' && /^[A-Z0-9]{2,26}USDT$/.test(value);
  const validInterval = value => typeof value === 'string' && /^(?:[1-9]\d{0,5}|[23]?D|W|M)$/.test(value);
  function readState() {
    const fallback = { version: VERSION, layout: '4grid', active: 0, panes: defaultPanes(), fvg: true, ema: { 7: true, 14: true, 28: true, 200: false }, auto: false, sidebar: true };
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
      if (!saved || saved.version !== VERSION || !Object.hasOwn(LAYOUTS, saved.layout)) return fallback;
      fallback.layout = saved.layout;
      if (Array.isArray(saved.panes)) saved.panes.slice(0, 6).forEach((pane, index) => {
        if (pane && validSymbol(pane.symbol) && validInterval(pane.interval)) fallback.panes[index] = { symbol: pane.symbol, interval: pane.interval };
      });
      fallback.active = Number.isInteger(saved.active) && saved.active >= 0 && saved.active < LAYOUTS[fallback.layout] ? saved.active : 0;
      ['fvg', 'auto', 'sidebar'].forEach(key => { if (typeof saved[key] === 'boolean') fallback[key] = saved[key]; });
      Object.keys(fallback.ema).forEach(period => { if (saved.ema && typeof saved.ema[period] === 'boolean') fallback.ema[period] = saved.ema[period]; });
    } catch (_) { /* Corrupt or unavailable browser storage must not break charts. */ }
    return fallback;
  }
  const state = readState();
  function saveState() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); }
    catch (_) { footer.textContent = 'Browser storage unavailable · settings last for this session'; }
  }
  function statusText(message) { footer.textContent = message; }
  function formatPrice(value) { return Number.isFinite(Number(value)) ? numberFormat.format(Number(value)) : '—'; }
  function intervalLabel(value) { return intervals.find(item => item.value === value)?.label || FAVORITES.find(item => item[0] === value)?.[1] || value; }
  function sourceLabel(source) {
    if (typeof source === 'string' && source.trim()) return source.trim();
    return 'Provider unspecified';
  }
  function errorText(data, fallback) {
    if (typeof data?.error === 'string') return data.error;
    if (typeof data?.error?.message === 'string') return data.error.message;
    if (typeof data?.message === 'string') return data.message;
    return fallback;
  }
  function makeOption(item) {
    const option = document.createElement('option');
    option.value = item.value;
    option.textContent = item.available === false ? `${item.label} · unavailable` : item.label;
    option.disabled = item.available === false;
    if (item.reason) option.title = item.reason;
    return option;
  }
  function populateIntervals(select, selected) {
    select.replaceChildren(...intervals.map(makeOption));
    if (!intervals.some(item => item.value === selected)) {
      const option = makeOption({ value: selected, label: `${intervalLabel(selected)} · not listed`, available: false });
      select.append(option);
    }
    select.value = selected;
  }
  function syncControls() {
    const active = state.panes[state.active];
    $('#symbol-input').value = active.symbol;
    $('#symbol-input').setCustomValidity('');
    populateIntervals($('#interval-select'), active.interval);
    $('#layout-select').value = state.layout;
    $('#favorite-intervals').querySelectorAll('button').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.interval === active.interval));
      const metadata = intervals.find(item => item.value === button.dataset.interval);
      button.disabled = !metadata || metadata.available === false;
      button.title = metadata?.reason || `Set active chart to ${button.textContent}`;
    });
    document.querySelectorAll('.watchlist-row').forEach(button => {
      const selected = button.dataset.symbol === active.symbol;
      button.classList.toggle('is-selected', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
    $('#watchlist-toggle').setAttribute('aria-expanded', String(state.sidebar));
    $('#workspace').classList.toggle('sidebar-collapsed', !state.sidebar);
  }
  function setActive(id) {
    if (!panes.has(id)) return;
    state.active = id;
    panes.forEach((pane, key) => {
      pane.element.classList.toggle('is-active', key === id);
      pane.element.setAttribute('aria-label', `Chart ${key + 1}: ${state.panes[key].symbol}, ${intervalLabel(state.panes[key].interval)}${key === id ? ', active' : ''}`);
    });
    syncControls();
    statusText(`${LAYOUTS[state.layout]} independent ${LAYOUTS[state.layout] === 1 ? 'chart' : 'charts'} · active chart ${id + 1}${intervalWarning ? ` · ${intervalWarning}` : ' · UTC'}`);
    saveState();
  }
  function updatePaneConfig(id, patch) {
    if (!panes.has(id)) return;
    const next = { ...state.panes[id], ...patch };
    if (!validSymbol(next.symbol) || !validInterval(next.interval)) return;
    state.panes[id] = next;
    setActive(id);
    panes.get(id).load(true);
  }
  function submitSymbol(input, id) {
    const value = normalizeSymbol(input.value);
    if (!validSymbol(value)) {
      input.setCustomValidity('Use a market symbol, e.g. BTCUSDT, BTC/USDT or BYBIT:BTCUSDT.P.');
      input.reportValidity();
      return false;
    }
    input.setCustomValidity('');
    updatePaneConfig(id, { symbol: value });
    return true;
  }
  function emaData(candles, period) {
    if (candles.length < period) return [];
    // Seed with an SMA, not the first close. Do not invent the missing warm-up.
    let value = candles.slice(0, period).reduce((sum, candle) => sum + candle.close, 0) / period;
    const result = [{ time: candles[period - 1].time, value }];
    const alpha = 2 / (period + 1);
    for (let index = period; index < candles.length; index += 1) {
      value = alpha * candles[index].close + (1 - alpha) * value;
      result.push({ time: candles[index].time, value });
    }
    return result;
  }
  function intervalSeconds(interval, anchor) {
    if (interval === 'M') {
      const date = new Date(anchor * 1000);
      return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1) / 1000 - anchor;
    }
    return { D: 86400, '2D': 172800, '3D': 259200, W: 604800 }[interval] || Number(interval) * 60;
  }
  function parseCandles(raw) {
    if (!Array.isArray(raw)) throw new Error('Provider response has no candle array.');
    const seen = new Map();
    raw.forEach(candle => {
      const values = ['ts', 'o', 'h', 'l', 'c', 'v'].map(key => Number(candle?.[key]));
      if (values.some(value => !Number.isFinite(value))) throw new Error('Provider returned an invalid candle.');
      const [timestamp, open, high, low, close, volume] = values;
      if (timestamp <= 0 || Math.min(open, high, low, close) <= 0 || high < Math.max(open, close, low) || low > Math.min(open, close, high) || volume < 0) throw new Error('Provider returned an invalid OHLCV range.');
      const time = Math.floor(timestamp / 1000);
      seen.set(time, { time, open, high, low, close, volume });
    });
    return Array.from(seen.values()).sort((left, right) => left.time - right.time);
  }

  class Pane {
    constructor(id) {
      this.id = id;
      this.disposed = false;
      this.generation = 0;
      this.lifetime = new AbortController();
      this.request = null;
      this.requestTimer = null;
      this.raf = null;
      this.interactionUntil = 0;
      this.dragging = false;
      this.candles = [];
      this.zones = [];
      this.emas = {};
      this.emaValues = {};
      this.element = document.createElement('section');
      this.element.className = 'chart-pane';
      this.element.dataset.paneId = String(id);
      this.element.dataset.state = 'loading';
      this.element.tabIndex = 0;
      this.element.innerHTML = `
        <header class="pane-header">
          <span class="pane-index">${id + 1}</span>
          <details class="pane-symbol-menu">
            <summary aria-label="Change symbol for chart ${id + 1}"><span class="pane-title"></span></summary>
            <form class="pane-symbol-popover">
              <label for="pane-symbol-${id}">Symbol · chart ${id + 1}</label>
              <div class="symbol-row"><input id="pane-symbol-${id}" class="pane-symbol-input" list="symbol-suggestions" maxlength="80" spellcheck="false" autocomplete="off" required><button type="submit">Apply</button></div>
              <p>BTCUSDT · BTC/USDT · BYBIT:BTCUSDT.P<br>Changes this chart only. Provider availability varies.</p>
            </form>
          </details>
          <select class="pane-interval" aria-label="Interval for chart ${id + 1}"></select>
          <span class="pane-source" title="Data provider from chart response">—</span>
          <span class="pane-closed" title="Only completed candles are requested">CLOSED ONLY</span>
          <button type="button" class="pane-reload" aria-label="Reload chart ${id + 1}" title="Reload this chart">↻</button>
        </header>
        <div class="chart-stage">
          <div class="chart-host"></div><canvas class="fvg-overlay" aria-hidden="true"></canvas>
          <div class="chart-legend"><span class="ohlc">Waiting for provider…</span><span class="legend-ema ema-7" data-ema="7"></span><span class="legend-ema ema-14" data-ema="14"></span><span class="legend-ema ema-28" data-ema="28"></span><span class="legend-ema ema-200" data-ema="200"></span></div>
          <div class="chart-message"><span>Loading closed candles…</span></div>
        </div>
        <div class="pane-statusbar"><span class="pane-status" role="status">Connecting to market data…</span><span class="pane-count"></span></div>`;
      grid.append(this.element);
      this.host = $('.chart-host', this.element);
      this.canvas = $('.fvg-overlay', this.element);
      this.context = this.canvas.getContext('2d');
      this.message = $('.chart-message', this.element);
      this.status = $('.pane-status', this.element);
      this.count = $('.pane-count', this.element);
      this.symbolInput = $('.pane-symbol-input', this.element);
      this.chart = LightweightCharts.createChart(this.host, {
        width: Math.max(1, this.host.clientWidth), height: Math.max(1, this.host.clientHeight),
        layout: { background: { type: 'solid', color: '#10141c' }, textColor: '#76849b', fontSize: 10, fontFamily: 'SFMono-Regular, Consolas, monospace' },
        grid: { vertLines: { color: '#1a2130' }, horzLines: { color: '#1a2130' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal, vertLine: { color: '#65748d', width: 1, style: 2, labelBackgroundColor: '#32415c' }, horzLine: { color: '#65748d', width: 1, style: 2, labelBackgroundColor: '#32415c' } },
        rightPriceScale: { borderColor: '#283145', scaleMargins: { top: 0.14, bottom: 0.23 }, minimumWidth: 65 },
        leftPriceScale: { visible: false },
        timeScale: { borderColor: '#283145', timeVisible: true, secondsVisible: false, rightOffset: 6, barSpacing: 6, minBarSpacing: 2 },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
        handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true }
      });
      this.series = this.chart.addCandlestickSeries({ upColor: '#00c087', downColor: '#ef6670', borderVisible: false, wickUpColor: '#00c087', wickDownColor: '#ef6670', priceLineVisible: true, lastValueVisible: true });
      this.volume = this.chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'volume', lastValueVisible: false, priceLineVisible: false });
      this.chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.84, bottom: 0 }, visible: false });
      Object.entries(EMA_COLORS).forEach(([period, color]) => {
        this.emas[period] = this.chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, visible: state.ema[period] });
      });
      this.logicalListener = () => this.scheduleDraw();
      this.crosshairListener = parameter => {
        const candle = parameter.seriesData?.get(this.series);
        this.updateLegend(candle && typeof candle.close === 'number' ? candle : this.candles.at(-1));
        this.scheduleDraw();
      };
      this.chart.timeScale().subscribeVisibleLogicalRangeChange(this.logicalListener);
      this.chart.subscribeCrosshairMove(this.crosshairListener);
      const listen = (target, event, handler, options = {}) => target.addEventListener(event, handler, { ...options, signal: this.lifetime.signal });
      listen(this.element, 'pointerdown', () => { if (state.active !== id) setActive(id); });
      listen(this.element, 'focusin', () => { if (state.active !== id) setActive(id); });
      listen(this.element, 'keydown', event => { if (event.key === 'Escape') $('.pane-symbol-menu', this.element).open = false; });
      listen($('.pane-symbol-popover', this.element), 'submit', event => {
        event.preventDefault();
        if (submitSymbol(this.symbolInput, id)) $('.pane-symbol-menu', this.element).open = false;
      });
      listen(this.symbolInput, 'input', () => this.symbolInput.setCustomValidity(''));
      listen($('.pane-symbol-menu', this.element), 'toggle', () => {
        if ($('.pane-symbol-menu', this.element).open) {
          if (state.active !== id) setActive(id);
          this.symbolInput.focus();
          this.symbolInput.select();
        }
      });
      listen($('.pane-interval', this.element), 'change', event => updatePaneConfig(id, { interval: event.target.value }));
      listen($('.pane-reload', this.element), 'click', () => { setActive(id); this.load(false); });
      // Logical-range subscriptions cover time navigation. Price-axis drags do not
      // emit those events in 4.1, so repaint through gestures and their inertia.
      listen(this.host, 'pointerdown', () => { this.dragging = true; this.animateInteraction(); });
      listen(window, 'pointerup', () => { if (this.dragging) { this.dragging = false; this.animateInteraction(); } });
      listen(window, 'pointercancel', () => { this.dragging = false; this.animateInteraction(); });
      listen(this.host, 'wheel', () => this.animateInteraction(), { passive: true });
      listen(this.host, 'dblclick', () => this.animateInteraction());
      listen(this.host, 'touchmove', () => this.animateInteraction(), { passive: true });
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(this.host);
      this.updateHeader();
      this.load(true);
    }
    updateHeader() {
      const config = state.panes[this.id];
      $('.pane-title', this.element).textContent = config.symbol;
      $('summary', this.element).title = `Change ${config.symbol} · chart ${this.id + 1}`;
      this.symbolInput.value = config.symbol;
      populateIntervals($('.pane-interval', this.element), config.interval);
      this.element.dataset.symbol = config.symbol;
      this.element.dataset.interval = config.interval;
    }
    resize() {
      if (this.disposed) return;
      const width = this.host.clientWidth;
      const height = this.host.clientHeight;
      if (!width || !height) return;
      this.chart.resize(width, height);
      const ratio = window.devicePixelRatio || 1;
      this.canvas.width = Math.round(width * ratio);
      this.canvas.height = Math.round(height * ratio);
      this.canvas.style.width = `${width}px`;
      this.canvas.style.height = `${height}px`;
      this.scheduleDraw();
    }
    animateInteraction() { this.interactionUntil = performance.now() + 850; this.scheduleDraw(); }
    scheduleDraw() {
      if (this.disposed || this.raf !== null) return;
      this.raf = requestAnimationFrame(() => {
        this.raf = null;
        if (this.disposed) return;
        this.drawZones();
        if (this.dragging || performance.now() < this.interactionUntil) this.scheduleDraw();
      });
    }
    coordinateForTime(seconds) {
      const scale = this.chart.timeScale();
      const exact = scale.timeToCoordinate(seconds);
      if (exact !== null) return exact;
      const data = this.candles;
      if (!data.length) return null;
      // timeToCoordinate can return null outside loaded bars. Interpolate logical
      // indices rather than anchoring zones to a viewport edge or infinite ray.
      let low = 0;
      let high = data.length;
      while (low < high) {
        const middle = (low + high) >>> 1;
        if (data[middle].time < seconds) low = middle + 1;
        else high = middle;
      }
      let logical;
      const step = intervalSeconds(state.panes[this.id].interval, low === 0 ? data[0].time : data.at(-1).time);
      if (low === 0) logical = (seconds - data[0].time) / step;
      else if (low === data.length) logical = data.length - 1 + (seconds - data.at(-1).time) / step;
      else logical = low - 1 + (seconds - data[low - 1].time) / (data[low].time - data[low - 1].time);
      return scale.logicalToCoordinate(logical);
    }
    drawZones() {
      const context = this.context;
      if (!context) return;
      const ratio = window.devicePixelRatio || 1;
      const width = this.canvas.width / ratio;
      const height = this.canvas.height / ratio;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      if (!state.fvg || !this.candles.length || !width || !height) return;
      const plotWidth = Math.max(0, width - this.chart.priceScale('right').width());
      const plotHeight = Math.max(0, height - this.chart.timeScale().height());
      context.save();
      context.beginPath(); context.rect(0, 0, plotWidth, plotHeight); context.clip();
      context.font = '9px SFMono-Regular, Consolas, monospace';
      this.zones.forEach(zone => {
        const x1 = this.coordinateForTime(Number(zone.start_ts) / 1000);
        const x2 = this.coordinateForTime(Number(zone.end_ts) / 1000);
        const y1 = this.series.priceToCoordinate(Number(zone.top));
        const y2 = this.series.priceToCoordinate(Number(zone.bottom));
        if ([x1, x2, y1, y2].some(value => value === null || !Number.isFinite(value))) return;
        const left = Math.max(0, Math.min(x1, x2));
        const right = Math.min(plotWidth, Math.max(x1, x2));
        const top = Math.max(0, Math.min(y1, y2));
        const bottom = Math.min(plotHeight, Math.max(y1, y2));
        if (right - left < 1 || bottom - top < 1) return;
        const bull = zone.direction === 'bull';
        context.fillStyle = bull ? 'rgba(0,192,135,0.13)' : 'rgba(245,166,35,0.14)';
        context.strokeStyle = bull ? 'rgba(0,192,135,0.42)' : 'rgba(245,166,35,0.45)';
        context.fillRect(left, top, right - left, bottom - top);
        context.lineWidth = 0.75;
        context.strokeRect(left + 0.5, top + 0.5, Math.max(0, right - left - 1), Math.max(0, bottom - top - 1));
        if (right - left >= 42 && bottom - top >= 15) {
          context.fillStyle = bull ? 'rgba(73,204,167,0.7)' : 'rgba(245,185,79,0.75)';
          context.fillText('FVG', left + 5, top + 11);
        }
      });
      context.restore();
    }
    showMessage(text) {
      this.message.hidden = !text;
      $('span', this.message).textContent = text;
    }
    updateLegend(candle) {
      $('.ohlc', this.element).textContent = candle ? `O ${formatPrice(candle.open)}  H ${formatPrice(candle.high)}  L ${formatPrice(candle.low)}  C ${formatPrice(candle.close)}` : 'No closed candle data';
      Object.keys(EMA_COLORS).forEach(period => {
        const element = $(`[data-ema="${period}"]`, this.element);
        element.hidden = !state.ema[period];
        const value = candle ? this.emaValues[period]?.get(candle.time) : undefined;
        element.textContent = `EMA ${period} ${value === undefined ? '· warming up' : formatPrice(value)}`;
      });
    }
    applyIndicators() {
      Object.keys(EMA_COLORS).forEach(period => this.emas[period].applyOptions({ visible: state.ema[period] }));
      this.updateLegend(this.candles.at(-1));
      this.scheduleDraw();
    }
    clearData() {
      this.candles = [];
      this.zones = [];
      this.emaValues = {};
      this.series.setData([]);
      this.volume.setData([]);
      Object.values(this.emas).forEach(series => series.setData([]));
      this.count.textContent = '';
      $('.pane-source', this.element).textContent = '—';
      this.updateLegend();
      this.scheduleDraw();
    }
    async load(resetView = false) {
      if (this.disposed) return;
      this.updateHeader();
      this.request?.abort();
      clearTimeout(this.requestTimer);
      const generation = ++this.generation;
      const request = new AbortController();
      this.request = request;
      let timedOut = false;
      this.requestTimer = setTimeout(() => { timedOut = true; request.abort(); }, 30000);
      if (resetView) this.clearData();
      this.element.dataset.state = 'loading';
      this.element.setAttribute('aria-busy', 'true');
      this.status.textContent = this.candles.length ? 'Refreshing closed candles…' : 'Loading closed candles…';
      this.showMessage(this.candles.length ? '' : 'Loading closed candles…');
      const config = { ...state.panes[this.id] };
      try {
        const query = new URLSearchParams({ symbol: config.symbol, interval: config.interval, limit: '300' });
        const response = await fetch(`/api/chart?${query}`, { signal: request.signal, headers: { Accept: 'application/json' }, cache: 'no-store' });
        const payload = await response.json();
        if (this.disposed || generation !== this.generation) return;
        if (!response.ok) throw new Error(errorText(payload, `Chart request failed (HTTP ${response.status}).`));
        if (payload.closed_only !== true) throw new Error('Provider did not confirm closed-only candles.');
        const candles = parseCandles(payload.candles);
        this.candles = candles;
        this.zones = (candles.length && Array.isArray(payload.zones) ? payload.zones : []).filter(zone =>
          zone?.type === 'fvg' && zone.mitigated !== true && ['bull', 'bear'].includes(zone.direction) &&
          ['start_ts', 'end_ts', 'top', 'bottom'].every(key => zone[key] != null && Number.isFinite(Number(zone[key]))) &&
          Number(zone.top) > Number(zone.bottom) && Number(zone.end_ts) > Number(zone.start_ts));
        this.series.setData(candles.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
        const minPrice = candles.length ? Math.min(...candles.map(candle => candle.low)) : 1;
        const precision = Math.min(10, Math.max(2, 3 - Math.floor(Math.log10(minPrice))));
        this.series.applyOptions({ priceFormat: { type: 'price', precision, minMove: 10 ** -precision } });
        this.volume.setData(candles.map(candle => ({ time: candle.time, value: candle.volume, color: candle.close >= candle.open ? '#00c08726' : '#ef66702b' })));
        Object.keys(EMA_COLORS).forEach(period => {
          const data = emaData(candles, Number(period));
          this.emaValues[period] = new Map(data.map(point => [point.time, point.value]));
          this.emas[period].setData(data);
        });
        if (resetView && candles.length) {
          // Keep enough density to read structure; all 300 bars remain pannable.
          this.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, candles.length - 125), to: candles.length + 5 });
        }
        const provider = sourceLabel(payload.source);
        $('.pane-source', this.element).textContent = provider.toUpperCase();
        $('.pane-source', this.element).title = `Candle data: ${provider}`;
        const warnings = Array.isArray(payload.warnings) ? payload.warnings.filter(value => typeof value === 'string').join(' · ') : '';
        const timestamp = timeFormat.format(new Date());
        this.element.dataset.state = candles.length ? 'ready' : 'empty';
        this.status.textContent = candles.length ? `${timestamp} UTC · ${provider}${warnings ? ` · ${warnings}` : ''}` : `No closed candles returned · ${provider}${warnings ? ` · ${warnings}` : ''}`;
        this.status.title = this.status.textContent;
        this.count.textContent = `${candles.length} bars · ${this.zones.length} FVG`;
        this.showMessage(candles.length ? '' : 'No closed candles available for this market and interval. Try another symbol or timeframe.');
        this.applyIndicators();
      } catch (error) {
        if (this.disposed || generation !== this.generation || (error.name === 'AbortError' && !timedOut)) return;
        const message = timedOut ? 'Market data request timed out. Use Reload to retry.' : error.message || 'Market data unavailable.';
        this.element.dataset.state = 'error';
        this.status.textContent = `${this.candles.length ? 'STALE · ' : ''}${message}`;
        this.status.title = this.status.textContent;
        this.showMessage(this.candles.length ? '' : message);
      } finally {
        if (generation === this.generation) { clearTimeout(this.requestTimer); this.request = null; this.element.setAttribute('aria-busy', 'false'); }
      }
    }
    destroy() {
      this.disposed = true;
      this.generation += 1;
      this.request?.abort();
      clearTimeout(this.requestTimer);
      this.lifetime.abort();
      this.resizeObserver.disconnect();
      if (this.raf !== null) cancelAnimationFrame(this.raf);
      this.chart.timeScale().unsubscribeVisibleLogicalRangeChange(this.logicalListener);
      this.chart.unsubscribeCrosshairMove(this.crosshairListener);
      this.chart.remove();
      this.element.remove();
    }
  }

  function mountLayout() {
    const count = LAYOUTS[state.layout];
    grid.dataset.layout = state.layout;
    panes.forEach((pane, id) => { if (id >= count) { pane.destroy(); panes.delete(id); } });
    for (let id = 0; id < count; id += 1) if (!panes.has(id)) panes.set(id, new Pane(id));
    if (state.active >= count) state.active = 0;
    setActive(state.active);
    panes.forEach(pane => pane.resize());
    statusText(`${count} independent ${count === 1 ? 'chart' : 'charts'} · active chart ${state.active + 1}${intervalWarning ? ` · ${intervalWarning}` : ' · UTC'}`);
  }
  function createFavorites() {
    const group = $('#favorite-intervals');
    FAVORITES.forEach(([value, label]) => {
      const button = document.createElement('button');
      button.type = 'button'; button.dataset.interval = value; button.textContent = label;
      button.setAttribute('aria-label', `Set active chart interval to ${label}`);
      button.addEventListener('click', () => updatePaneConfig(state.active, { interval: value }));
      group.append(button);
    });
  }
  async function loadIntervals() {
    const request = new AbortController();
    const timeout = setTimeout(() => request.abort(), 15000);
    try {
      const response = await fetch('/api/chart/intervals', { signal: request.signal, headers: { Accept: 'application/json' } });
      const data = await response.json();
      if (!response.ok || !Array.isArray(data.intervals) || !data.intervals.length) throw new Error('Interval list unavailable');
      const seen = new Set();
      const available = data.intervals.filter(item => item && (validInterval(String(item.value)) || (item.value === '30s' && item.available === false)) && typeof item.label === 'string').map(item => ({ ...item, value: String(item.value) })).filter(item => {
        if (seen.has(item.value)) return false;
        seen.add(item.value); return true;
      });
      if (!available.length) throw new Error('Interval list unavailable');
      intervals = available;
      syncControls();
      panes.forEach(pane => pane.updateHeader());
    } catch (_) {
      intervalWarning = 'Interval metadata unavailable; standard shortcuts shown';
      statusText(intervalWarning);
    } finally { clearTimeout(timeout); }
  }
  function createWatchlist() {
    const rows = $('#watchlist-rows');
    WATCHLIST.forEach(symbol => {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'watchlist-row'; button.dataset.symbol = symbol;
      button.setAttribute('aria-label', `Show ${symbol} in active chart`);
      const name = document.createElement('span'); name.className = 'watchlist-symbol'; name.textContent = symbol.replace(/USDT$/, '');
      const quote = document.createElement('small'); quote.textContent = 'USDT'; name.append(quote);
      const price = document.createElement('span'); price.className = 'watchlist-price'; price.textContent = '—';
      const change = document.createElement('span'); change.className = 'watchlist-change'; change.textContent = '—';
      button.append(name, price, change);
      button.addEventListener('click', () => updatePaneConfig(state.active, { symbol }));
      rows.append(button);
    });
  }
  async function refreshPrices() {
    pricesController?.abort();
    clearTimeout(pricesTimeout);
    const generation = ++pricesGeneration;
    pricesController = new AbortController();
    const request = pricesController;
    pricesTimeout = setTimeout(() => request.abort(), 25000);
    const status = $('#prices-status');
    status.textContent = 'Updating price snapshot…';
    try {
      const response = await fetch(`/api/prices?${new URLSearchParams({ symbols: WATCHLIST.join(',') })}`, { signal: request.signal, cache: 'no-store' });
      const data = await response.json();
      if (generation !== pricesGeneration) return;
      if (!response.ok || !Array.isArray(data.prices)) throw new Error('Prices unavailable');
      const prices = new Map(data.prices.filter(item => item && typeof item.symbol === 'string').map(item => [item.symbol, item]));
      let valid = 0;
      document.querySelectorAll('.watchlist-row').forEach(row => {
        const price = prices.get(row.dataset.symbol);
        const priceElement = $('.watchlist-price', row);
        const changeElement = $('.watchlist-change', row);
        const last = price?.price;
        const change = price?.change_24h;
        const validLast = typeof last === 'number' && Number.isFinite(last) && last > 0;
        const validChange = typeof change === 'number' && Number.isFinite(change);
        priceElement.textContent = validLast ? formatPrice(last) : '—';
        changeElement.textContent = validChange ? `${change >= 0 ? '+' : ''}${change.toFixed(2)}%` : '—';
        changeElement.classList.toggle('positive', validChange && change >= 0);
        changeElement.classList.toggle('negative', validChange && change < 0);
        if (validLast) valid += 1;
      });
      status.textContent = valid ? `Snapshot ${timeFormat.format(new Date())} UTC · ${valid}/${WATCHLIST.length} markets` : 'No prices returned by market provider. Symbol selection still works.';
    } catch (error) {
      if (generation !== pricesGeneration) return;
      status.textContent = error.name === 'AbortError' ? 'Price request timed out. Prior snapshot, if any, is stale.' : 'Prices unavailable · prior snapshot, if any, is stale. Select a symbol to load its chart.';
    } finally { if (generation === pricesGeneration) { clearTimeout(pricesTimeout); pricesController = null; } }
  }
  function isVisible(pane) {
    const rect = pane.element.getBoundingClientRect();
    const area = grid.getBoundingClientRect();
    return rect.bottom > area.top && rect.top < area.bottom && rect.right > area.left && rect.left < area.right;
  }
  function autoRefresh() {
    if (!state.auto || document.visibilityState !== 'visible') return;
    panes.forEach(pane => { if (isVisible(pane) && !pane.request) pane.load(false); });
    if (state.sidebar && matchMedia('(min-width:1001px)').matches && !pricesController) refreshPrices();
  }
  function init() {
    if (!window.LightweightCharts) {
      $('#workspace').classList.add('is-unavailable');
      grid.textContent = 'Chart library could not be loaded. Check the locally served Lightweight Charts asset and reload.';
      statusText('Chart library unavailable · no data shown');
      return;
    }
    initialized = true;
    createFavorites(); createWatchlist();
    $('#fvg-toggle').checked = state.fvg;
    $('#auto-refresh').checked = state.auto;
    Object.keys(EMA_COLORS).forEach(period => {
      const control = $(`#ema${period}-toggle`);
      control.checked = state.ema[period];
      control.addEventListener('change', () => { state.ema[period] = control.checked; panes.forEach(pane => pane.applyIndicators()); saveState(); });
    });
    $('#symbol-form').addEventListener('submit', event => { event.preventDefault(); submitSymbol($('#symbol-input'), state.active); });
    $('#symbol-input').addEventListener('input', event => event.target.setCustomValidity(''));
    $('#interval-select').addEventListener('change', event => updatePaneConfig(state.active, { interval: event.target.value }));
    $('#layout-select').addEventListener('change', event => { state.layout = event.target.value; mountLayout(); saveState(); });
    $('#fvg-toggle').addEventListener('change', event => { state.fvg = event.target.checked; panes.forEach(pane => pane.scheduleDraw()); saveState(); });
    $('#auto-refresh').addEventListener('change', event => { state.auto = event.target.checked; saveState(); statusText(state.auto ? 'Auto refresh enabled · visible charts, every 60 seconds · closed candles only' : 'Auto refresh off · reload charts manually'); });
    $('#reload-active').addEventListener('click', () => panes.get(state.active)?.load(false));
    $('#watchlist-toggle').addEventListener('click', () => { state.sidebar = !state.sidebar; syncControls(); saveState(); });
    $('#refresh-prices').addEventListener('click', refreshPrices);
    document.addEventListener('click', event => document.querySelectorAll('.pane-symbol-menu[open]').forEach(menu => { if (!menu.contains(event.target)) menu.open = false; }));
    mountLayout(); loadIntervals(); refreshPrices();
  }
  const refreshTimer = setInterval(autoRefresh, 60000);
  // pagehide also covers browser navigation with bfcache; pageshow reconstructs
  // disposed instances without leaving their requests or observers alive.
  window.addEventListener('pagehide', () => {
    clearInterval(refreshTimer);
    pricesGeneration += 1; pricesController?.abort(); clearTimeout(pricesTimeout);
    panes.forEach(pane => pane.destroy()); panes.clear();
  });
  window.addEventListener('pageshow', event => { if (event.persisted && initialized) location.reload(); });
  init();
})();
