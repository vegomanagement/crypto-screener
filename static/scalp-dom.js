/* /scalp page: 1–4 view-only DOM columns (live L2 ladder + prints feed + cluster columns + density age).
 * Public venue data only, streamed straight from the venues; no orders, keys or accounts anywhere.
 */
(function () {
  'use strict';
  const OB = window.ScalpOrderBook, R = window.ScalpRender, S = window.ScalpSettings, CD = window.ScalpColumnData, SA = window.ScalpAlerts;
  const $ = (sel, el = document) => el.querySelector(sel);
  const evidence = new URLSearchParams(location.search).get('evidence') === '1';
  const hub = window.WorkspaceMarketStream.createHub();
  const pad = n => String(n).padStart(2, '0');
  const countdown = ms => (ms > 0 ? `${pad(Math.floor(ms / 3600000))}:${pad(Math.floor(ms / 60000) % 60)}:${pad(Math.floor(ms / 1000) % 60)}` : '—');

  async function fetchJson(url, init) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(url, { cache: 'no-store', ...init, signal: controller.signal });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(data?.error || `HTTP ${response.status}`);
      return data;
    } finally { clearTimeout(timer); }
  }
  /** Venue tick: server metadata (Binance/Bybit) or Hyperliquid's price-dependent rule from a live l2Book price. */
  async function resolveTick(venue, symbol) {
    const meta = await fetchJson(`/api/scalp/instrument?${new URLSearchParams({ exchange: venue, symbol })}`);
    if (meta.tick_size) return Number(meta.tick_size);
    const book = await fetchJson('https://api.hyperliquid.xyz/info', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'l2Book', coin: symbol }) });
    const px = Number(book?.levels?.[0]?.[0]?.px || book?.levels?.[1]?.[0]?.px);
    if (!(px > 0)) throw new Error('hyperliquid: no book price to derive tick');
    return OB.hyperliquidTick(px, meta.sz_decimals);
  }
  /** Mark price + funding where the venue publishes it publicly. Returns null fields when unknown. */
  async function fetchMarket(venue, symbol) {
    if (venue === 'binance') {
      const d = await fetchJson(`https://fapi.binance.com/fapi/v1/premiumIndex?symbol=${encodeURIComponent(symbol)}`);
      return { mark: Number(d.markPrice), funding: Number(d.lastFundingRate), nextFunding: Number(d.nextFundingTime), basis: 'premiumIndex' };
    }
    if (venue === 'bybit') {
      const d = await fetchJson(`https://api.bybit.com/v5/market/tickers?category=linear&symbol=${encodeURIComponent(symbol)}`);
      const t = d?.result?.list?.[0];
      if (!t) throw new Error('bybit: no ticker');
      return { mark: Number(t.markPrice), funding: Number(t.fundingRate), nextFunding: Number(t.nextFundingTime), basis: 'tickers' };
    }
    const [meta, ctxs] = await fetchJson('https://api.hyperliquid.xyz/info', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: 'metaAndAssetCtxs' }) });
    const k = meta?.universe?.findIndex(u => u.name === symbol);
    if (!(k >= 0)) throw new Error('hyperliquid: coin not in perp universe');
    const hour = 3600000, next = Math.ceil(Date.now() / hour) * hour; // HL settles funding every hour on the hour
    return { mark: Number(ctxs[k].markPx), funding: Number(ctxs[k].funding), nextFunding: next, basis: 'hourly' };
  }

  class Column {
    constructor(index, page) {
      this.index = index; this.page = page; this.cfg = page.settings.cols[index];
      this.view = { centerRow: NaN, rowPx: this.cfg.rowPx }; this.follow = true; this.ruler = { anchorRow: null, hoverRow: null };
      this.rowN = null; this.tick = null; this.stream = null; this.data = null; this.density = new OB.DensityTracker();
      this.market = null; this.marketError = null; this.status = 'loading'; this.statusDetail = ''; this.error = null;
      this.lastDraw = null; this.records = []; this.generation = 0; this.unsubTrades = null; this.marketTimer = null;
      this.levels = []; this.volAlerts = null; this.lastTradePrice = null; this.tradeGap = false; this.tickStr = null; this.suppressClick = false;
      this.element = this.build(); this.start();
    }
    build() {
      const el = document.createElement('section'); el.className = 'dom-col'; el.dataset.index = String(this.index);
      el.innerHTML = `<div class="dom-head"><form class="dom-symbol"><select name="venue" aria-label="Venue">${Object.entries(S.VENUES)
        .map(([k, v]) => `<option value="${k}">${v}</option>`).join('')}</select><input name="symbol" spellcheck="false" autocomplete="off" maxlength="40" aria-label="Symbol"><button type="submit" title="Apply">↵</button></form>
        <span class="dom-scale"></span><a class="chart-link" title="Open this symbol in the chart workspace">chart ↗</a><button type="button" class="settings-btn" title="Column settings" aria-expanded="false">⚙</button></div>
        <div class="dom-status"></div><div class="dom-market"></div><div class="dom-body"><canvas></canvas></div>`;
      const form = $('form', el);
      form.venue.value = this.cfg.venue; form.symbol.value = this.cfg.symbol;
      form.addEventListener('submit', event => {
        event.preventDefault();
        const venue = form.venue.value, symbol = form.symbol.value.trim();
        const fixed = venue === 'hyperliquid' ? symbol : symbol.toUpperCase();
        if (!S.validSymbol(venue, fixed)) { this.setError(`Invalid ${S.VENUES[venue]} symbol: ${symbol}`); return; }
        this.page.updateColumn(this.index, { venue, symbol: fixed }, 'restart');
      });
      $('.settings-btn', el).addEventListener('click', () => this.page.toggleSettings(this));
      const canvas = this.canvas = $('canvas', el);
      el.addEventListener('pointerdown', () => this.page.setActive(this.index));
      canvas.addEventListener('wheel', event => this.onWheel(event), { passive: false });
      canvas.addEventListener('mousemove', event => { this.ruler.hoverRow = this.rowAt(event); this.page.dirty(this); });
      canvas.addEventListener('mouseleave', () => { this.ruler.hoverRow = null; this.page.dirty(this); });
      canvas.addEventListener('click', event => {
        if (this.suppressClick) { this.suppressClick = false; return; } // end of a level drag, not a ruler click
        this.ruler.anchorRow = this.rowAt(event); this.page.dirty(this);
      });
      $('.dom-head', el).addEventListener('click', () => { const h = $('.dom-head', el); h.classList.remove('alert-blink', 'alert-hold'); clearTimeout(this.blinkTimer); });
      if (this.page.alerts) window.ScalpLevelsUI.bind(this, this.page.alerts);
      new ResizeObserver(() => this.resize()).observe($('.dom-body', el));
      return el;
    }
    rowAt(event) {
      const rect = this.canvas.getBoundingClientRect();
      return R.rowAtY(this.view, event.clientY - rect.top, rect.height);
    }
    onWheel(event) {
      event.preventDefault();
      if (event.ctrlKey || event.metaKey) {
        this.cfg.rowPx = Math.max(8, Math.min(32, this.cfg.rowPx + (event.deltaY < 0 ? 2 : -2)));
        this.view.rowPx = this.cfg.rowPx; this.page.persist();
      } else if (Number.isFinite(this.view.centerRow)) {
        this.follow = false; this.view.centerRow += Math.sign(event.deltaY) * -3;
      }
      this.page.dirty(this);
    }
    resize() {
      const body = $('.dom-body', this.element), dpr = window.devicePixelRatio || 1;
      const w = Math.max(1, Math.floor(body.clientWidth)), h = Math.max(1, Math.floor(body.clientHeight));
      this.size = { width: w, height: h, dpr };
      this.canvas.width = Math.round(w * dpr); this.canvas.height = Math.round(h * dpr);
      this.ctx = this.canvas.getContext('2d'); this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.page.dirty(this);
    }
    setError(text) { this.error = text; this.renderText(); }
    stop() {
      this.generation += 1; this.stream?.close(); this.stream = null; this.unsubTrades?.(); this.unsubTrades = null;
      clearTimeout(this.marketTimer); this.marketTimer = null;
    }
    dispose() { this.stop(); this.element.remove(); }
    async start() {
      this.stop();
      const g = this.generation, { venue, symbol } = this.cfg;
      this.volAlerts = null; this.levels = [];
      this.status = 'loading'; this.error = null; this.rowN = null; this.view.centerRow = NaN; this.follow = true;
      this.density = new OB.DensityTracker(); this.market = null; this.records = [];
      const link = $('.chart-link', this.element); link.href = S.chartLink(this.cfg);
      this.renderText();
      try { this.tick = await resolveTick(venue, symbol); } catch (error) {
        if (g === this.generation) { this.status = 'error'; this.setError(`Instrument unavailable: ${error.message}`); }
        return;
      }
      if (g !== this.generation) return;
      this.data = new CD.ColumnData({ venue, symbol, tick: this.tick, feed: this.cfg.feed, clusters: this.cfg.clusters });
      this.tickStr = CD.tickString(this.tick); this.lastTradePrice = null; this.tradeGap = false;
      this.volAlerts = new SA.VolumeAlerts({ tick: this.tick, tickStr: this.tickStr }); this.volAlerts.configure(this.cfg.alerts);
      this.levels = this.page.alerts ? this.page.alerts.store.list({ exchange: venue, symbol }) : [];
      this.unsubTrades = hub.subscribeTrades({ venue, symbol }, event => {
        if (g !== this.generation) return;
        if (this.data.onEvent(event)) this.page.dirty(this);
        this.page.alerts?.onTrades(this, event);
      });
      this.stream = OB.createBookStream({ venue, symbol, tick: this.tick,
        onStatus: ({ status, detail }) => {
          if (g !== this.generation) return;
          this.status = status; this.statusDetail = detail || ''; this.renderText();
          if (status !== 'synced') this.volAlerts?.reset(); // resync: re-seed, never alert on a rebuilt book
        },
        onApplied: evidence ? (msg, sync) => this.record(msg, sync) : null,
        onUpdate: sync => {
          if (g !== this.generation) return;
          if (sync.state === 'synced') this.density.update(sync.book, this.cfg.densityUsd, Date.now());
          this.page.alerts?.onBook(this, sync);
          this.page.dirty(this);
        } });
      this.pollMarket(g);
    }
    async pollMarket(g) {
      if (g !== this.generation) return;
      if (document.visibilityState === 'visible') {
        try { this.market = await fetchMarket(this.cfg.venue, this.cfg.symbol); this.marketError = null; }
        catch (error) { this.marketError = error.message; }
      }
      if (g === this.generation) this.marketTimer = setTimeout(() => this.pollMarket(g), this.cfg.venue === 'hyperliquid' ? 30000 : 10000);
    }
    /** Evidence mode only: keep the last books with their venue update ids for the REST comparison. */
    record(msg, sync) {
      const entry = { at: Date.now(), top: sync.book.top(50) };
      if (this.cfg.venue === 'binance') Object.assign(entry, { U: msg.U, u: msg.u, pu: msg.pu, E: msg.E, touched: [...msg.b, ...msg.a].map(l => Number(l[0])) });
      else if (this.cfg.venue === 'bybit') Object.assign(entry, { u: msg.data.u, seq: msg.data.seq, ts: msg.ts, type: msg.type });
      else Object.assign(entry, { time: msg.data.time });
      this.records.push(entry);
      if (this.records.length > 400) this.records.splice(0, this.records.length - 400);
    }
    renderText() {
      const st = $('.dom-status', this.element), mk = $('.dom-market', this.element), sync = this.stream?.sync;
      $('.dom-scale', this.element).textContent = `${S.scaleLabel(this.cfg)} · ${this.cfg.units === 'usd' ? '$' : 'coin'}`;
      st.replaceChildren();
      const chip = (cls, text) => { const s = document.createElement('span'); s.className = `chip ${cls}`; s.textContent = text; st.append(s); };
      chip(this.error ? 'error' : this.status, this.error ? 'error' : this.status);
      if (this.cfg.venue === 'bybit') chip('capped', '200/side cap');
      if (this.cfg.venue === 'hyperliquid') chip('capped', '≤20/side cap');
      const parts = [];
      if (this.error) parts.push(this.error);
      if (this.statusDetail) parts.push(this.statusDetail);
      if (sync) parts.push(this.cfg.venue === 'binance' ? `REST 1000 + diff · resyncs ${sync.stats.resyncs} · gaps ${sync.stats.gaps}` : `updates ${sync.stats.applied} · resyncs ${sync.stats.resyncs}`);
      if (this.data) parts.push(`trades ${this.data.streamStatus} · ${this.data.stats.received} prints${this.data.stats.idGaps ? ` · ${this.data.stats.idGaps} id gaps` : ''}`);
      parts.push(`density ≥$${OB.abbreviate(this.cfg.densityUsd)}: ${this.density.size}`);
      st.append(document.createTextNode(parts.join(' · ')));
      const book = this.stream?.book, best = book?.best(), d = OB.decimalsOf(this.tick || 1);
      if (!book || sync?.state !== 'synced' || best.bid === null || best.ask === null) { mk.textContent = this.marketError ? `Market: ${this.marketError}` : 'Waiting for a synced book…'; return; }
      const bid = best.bid * this.tick, ask = best.ask * this.tick, mid = (bid + ask) / 2, m = this.market;
      const fmt = v => R.formatPrice(v, d);
      let text = `Bid <b>${fmt(bid)}</b> · Ask <b>${fmt(ask)}</b> · Spread ${fmt(ask - bid)} (${((ask - bid) / mid * 100).toFixed(4)}%)`;
      if (m && m.mark > 0) text += ` · Mark <b>${fmt(m.mark)}</b> (Δ ${m.mark - mid >= 0 ? '+' : ''}${(m.mark - mid).toFixed(d)})`;
      if (m && Number.isFinite(m.funding)) text += ` · Funding <b>${(m.funding * 100).toFixed(4)}%</b>${m.basis === 'hourly' ? '/h' : ''} · in ${window.ScalpPageUtil.countdown(m.nextFunding - Date.now())}`;
      else if (this.marketError) text += ' · funding n/a';
      mk.innerHTML = text; // numbers only (formatted locally); no venue strings are interpolated
    }
    /** One paint of this column. Returns the renderer stats (kept for the page's render accounting). */
    draw(now) {
      if (!this.ctx || !this.size || !this.tick) return null;
      const sync = this.stream?.sync, book = sync?.state === 'synced' ? sync.book : null, best = book?.best();
      if (book && best.bid !== null && best.ask !== null) {
        const mid = (best.bid + best.ask) / 2 * this.tick;
        if (this.rowN === null) this.rowN = OB.rowTicks(S.scaleSpec(this.cfg), mid, this.tick);
        const midRow = Math.floor(mid / (this.rowN * this.tick));
        if (!Number.isFinite(this.view.centerRow)) this.view.centerRow = midRow;
        else if (this.follow && this.cfg.autocenter) this.view.centerRow = R.autocenter(this.view.centerRow, midRow, this.size.height, this.view.rowPx, this.cfg.autocenterPct);
        this.midRow = midRow;
      }
      if (this.rowN === null) { this.ctx.clearRect(0, 0, this.size.width, this.size.height); return null; }
      const model = { book, tick: this.tick, rowN: this.rowN, decimals: OB.decimalsOf(this.tick), density: this.density, now,
        mark: this.market?.mark, ruler: this.ruler, feed: this.data?.feed, clusters: this.data?.bars, levels: this.levels };
      this.lastDraw = R.drawColumn(this.ctx, this.size, this.view, model, this.cfg);
      return this.lastDraw;
    }
    center() { this.follow = true; if (Number.isFinite(this.midRow)) this.view.centerRow = this.midRow; }
    /** Row size changed: keep the same centre price, reset the ruler (rows no longer mean the same price). */
    regroup() {
      const oldN = this.rowN;
      this.rowN = null; this.ruler = { anchorRow: null, hoverRow: null };
      if (oldN && Number.isFinite(this.view.centerRow) && this.tick) {
        const price = (this.view.centerRow + 0.5) * oldN * this.tick, book = this.stream?.book, best = book?.best();
        const ref = best && best.bid !== null && best.ask !== null ? (best.bid + best.ask) / 2 * this.tick : price;
        this.rowN = OB.rowTicks(S.scaleSpec(this.cfg), ref, this.tick);
        this.view.centerRow = Math.floor(price / (this.rowN * this.tick));
      }
    }
  }
  window.ScalpColumn = Column;
  window.ScalpPageUtil = { fetchJson, fetchMarket, resolveTick, countdown, hub, evidence };
})();
