/* /scalp alert centre (browser side): price/signal levels on the DOM columns, signal triggering with
 * WebAudio beep + header blink + optional browser Notification, per-column volume alerts and the
 * notifications drawer. Logic lives in levels.js / scalp-alerts.js (unit-tested); this file is wiring.
 * View only: a level is a marker and an alert is a message; nothing here can place or cancel an order.
 */
(function () {
  'use strict';
  const LV = window.ScreenerLevels, SA = window.ScalpAlerts, OB = window.ScalpOrderBook, R = window.ScalpRender;
  const $ = (sel, el = document) => el.querySelector(sel);
  const PREFS_KEY = 'screener.scalp.alertprefs.v1';
  const VENUE_LABEL = { binance: 'Binance', bybit: 'Bybit', hyperliquid: 'Hyperliquid' };
  const pad = n => String(n).padStart(2, '0');
  const hms = ms => { const d = new Date(ms); return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`; };

  function loadPrefs() {
    let raw = null;
    try { raw = JSON.parse(localStorage.getItem(PREFS_KEY) || 'null'); } catch (_) { raw = null; }
    const r = raw && typeof raw === 'object' ? raw : {};
    return { sound: typeof r.sound === 'boolean' ? r.sound : true, notify: r.notify === true, dock: r.dock === 'left' ? 'left' : 'right',
      open: r.open === true, tab: r.tab === 'active' ? 'active' : 'triggered' };
  }

  function createAlertCenter(page) {
    const store = LV.store, log = new SA.AlertLog(safeStorage());
    const prefs = loadPrefs();
    const stats = { signals: 0, volume: 0, beeps: 0, beepsBlocked: 0, notifications: 0, notificationsSkipped: 0, rearmed: 0, lastSignal: null };
    const audio = { ctx: null };
    const center = { store, log, prefs, stats, renderPanel: () => {}, status: () => {} };

    function safeStorage() { try { localStorage.getItem(PREFS_KEY); return localStorage; } catch (_) { return null; } }
    function savePrefs() { try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (_) { /* session only */ } }
    center.savePrefs = savePrefs;

    // ── sound: browsers only start audio after a user gesture, so it is unlocked on the first one ──
    function unlockAudio() {
      try {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return;
        audio.ctx ??= new Ctx();
        if (audio.ctx.state === 'suspended') audio.ctx.resume();
      } catch (_) { /* no audio on this device */ }
    }
    for (const type of ['pointerdown', 'keydown']) window.addEventListener(type, unlockAudio, { capture: true });
    /** Returns 'played' | 'blocked' (no gesture yet / no WebAudio) | 'off'. */
    function beep(freqs) {
      if (!prefs.sound) return 'off';
      const ctx = audio.ctx;
      if (!ctx || ctx.state !== 'running') { stats.beepsBlocked += 1; return 'blocked'; }
      let t = ctx.currentTime;
      for (const f of freqs) {
        const osc = ctx.createOscillator(), gain = ctx.createGain();
        osc.type = 'sine'; osc.frequency.value = f;
        gain.gain.setValueAtTime(0.0001, t); gain.gain.exponentialRampToValueAtTime(0.25, t + 0.01); gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.14);
        osc.connect(gain).connect(ctx.destination); osc.start(t); osc.stop(t + 0.15); t += 0.17;
      }
      stats.beeps += 1;
      return 'played';
    }
    center.audioState = () => (audio.ctx ? audio.ctx.state : 'not started (needs a click or key press)');

    function notify(title, body) {
      if (!prefs.notify || typeof Notification === 'undefined' || Notification.permission !== 'granted') { stats.notificationsSkipped += 1; return false; }
      try { new Notification(title, { body, tag: `scalp-${title}-${body}`.slice(0, 120) }); stats.notifications += 1; return true; }
      catch (_) { stats.notificationsSkipped += 1; return false; }
    }
    /** Permission is requested only from this (button click) path. */
    center.requestNotifications = async () => {
      if (typeof Notification === 'undefined') return 'unsupported';
      if (Notification.permission === 'default') { try { await Notification.requestPermission(); } catch (_) { /* denied */ } }
      prefs.notify = Notification.permission === 'granted'; savePrefs();
      return Notification.permission;
    };

    // ── header blink: 5 s blinking, then highlighted until clicked ──
    function blink(venue, symbol) {
      for (const col of page.columns) {
        if (col.cfg.venue !== venue || col.cfg.symbol !== symbol) continue;
        const head = $('.dom-head', col.element);
        head.classList.remove('alert-hold'); head.classList.add('alert-blink');
        clearTimeout(col.blinkTimer);
        col.blinkTimer = setTimeout(() => { head.classList.remove('alert-blink'); head.classList.add('alert-hold'); }, 5000);
      }
    }

    // ── firing ──
    function fire(col, alert, source) {
      const { venue, symbol } = col.cfg, fmtUsd = alert.usd > 0 ? ` · $${OB.abbreviate(alert.usd)}` : '';
      const detail = alert.type === 'signal'
        ? `trade ${alert.tradePrice}${alert.prevPrice ? ` after ${alert.prevPrice}` : ' (first trade seen)'}${alert.gap ? ' · across a stream gap' : ''}${alert.note ? ` · ${alert.note}` : ''}`
        : `${alert.detail}${fmtUsd}`;
      const sound = beep(SA.SOUNDS[alert.type] || [700]);
      let entry = null;
      try {
        entry = log.add({ exchange: venue, symbol, source, price: alert.price, type: alert.type, detail: `${detail}${sound === 'blocked' ? ' · sound blocked (no click yet)' : ''}`,
          levelId: alert.levelId, tradePrice: alert.tradePrice, prevPrice: alert.prevPrice, tradeTime: alert.tradeTime, tradeId: alert.tradeId, usd: alert.usd });
      } catch (_) { /* invalid entry: never shown rather than shown wrong */ }
      if (alert.type === 'signal') { stats.signals += 1; stats.lastSignal = { ...alert, at: Date.now(), sound }; blink(venue, symbol); }
      else stats.volume += 1;
      notify(`${VENUE_LABEL[venue]} ${symbol} · ${SA.TYPE_LABELS[alert.type]}`, `${alert.price} · ${detail}`);
      center.renderPanel();
      return entry;
    }

    /** Hub trade events for a column. Signal levels are evaluated once per (venue, symbol) even if several columns show it. */
    center.onTrades = (col, event) => {
      if (event?.type === 'status') { if (event.status === 'disconnected') col.tradeGap = true; return; }
      if (event?.type !== 'trades' || !Array.isArray(event.rows) || !col.volAlerts) return;
      const { venue, symbol } = col.cfg;
      const owner = page.columns.find(c => c.cfg.venue === venue && c.cfg.symbol === symbol && c.volAlerts);
      for (const row of event.rows) {
        const price = SA.tradePriceString(venue, row), print = OB.normalizePrint(venue, row);
        if (!price || !print) continue;
        const now = Date.now();
        if (owner === col) signalTick(col, price, print, now);
        for (const a of col.volAlerts.onTrade(print, now)) fire(col, a, 'trades');
        col.lastTradePrice = price;
      }
    };
    function signalTick(col, price, print, now) {
      const { venue, symbol } = col.cfg, cached = (col.levels || []).filter(l => l.kind === 'signal');
      if (!cached.length) { col.tradeGap = false; return; }
      const { trigger, rearm } = SA.evaluateSignals(cached, col.lastTradePrice, price, col.tickStr);
      for (const l of rearm) { if (store.update(l.id, { armed: true })) stats.rearmed += 1; }
      for (const l of trigger) {
        const fresh = store.list({ exchange: venue, symbol }).find(x => x.id === l.id);
        if (!fresh || !fresh.armed) continue; // removed, or already fired by another tab
        store.update(l.id, SA.triggerPatch(fresh, now));
        fire(col, { type: 'signal', price: fresh.price, levelId: fresh.id, tradePrice: price, prevPrice: col.lastTradePrice, tradeTime: print.time,
          tradeId: print.id ?? null, gap: col.tradeGap, note: fresh.note }, fresh.source);
      }
      col.tradeGap = false;
    }
    /** Book updates for a column's volume alerts. Anything but a synced book resets the trackers (no alerts from a resync). */
    center.onBook = (col, sync) => {
      if (!col.volAlerts) return;
      if (sync.state !== 'synced') { if (col.volAlerts.seeded) col.volAlerts.reset(); return; }
      for (const a of col.volAlerts.onBook(sync.book, Date.now())) fire(col, a, 'book');
    };

    // ── levels cache per column ──
    center.refreshLevels = () => {
      for (const col of page.columns) { col.levels = store.list({ exchange: col.cfg.venue, symbol: col.cfg.symbol }); page.dirty(col); }
      center.renderPanel();
    };
    store.subscribe(() => center.refreshLevels());
    center.fire = fire;
    return center;
  }
  window.ScalpAlertCenter = { createAlertCenter, hms, VENUE_LABEL };
})();
