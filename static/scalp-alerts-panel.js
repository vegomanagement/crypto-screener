/* /scalp notifications drawer: Active (armed signal levels) and Triggered (fired alerts, persisted, ≤ 200)
 * tabs, delete single/all, dock left/right, sound / browser-notification toggles. Clicking a row centres
 * the first column showing that venue+symbol on the price (autocenter paused until C).
 */
(function () {
  'use strict';
  const SA = window.ScalpAlerts, AC = window.ScalpAlertCenter;
  const $ = (sel, el = document) => el.querySelector(sel);

  function mountPanel(page, center) {
    const drawer = $('#scalp-alerts'), toggle = $('#scalp-alerts-toggle'), root = $('#scalp-root');
    const { prefs } = center;
    drawer.innerHTML = `<header class="al-head"><div class="al-tabs" role="tablist">
        <button type="button" role="tab" data-tab="active">Active <span class="n"></span></button>
        <button type="button" role="tab" data-tab="triggered">Triggered <span class="n"></span></button></div>
        <button type="button" data-act="dock" title="Dock on the other side">⇄</button><button type="button" data-act="close" title="Close">✕</button></header>
      <div class="al-opts"><label><input type="checkbox" data-pref="sound"> Sound</label>
        <button type="button" data-act="notify">Browser notifications…</button><span class="al-audio"></span></div>
      <div class="al-msg" role="status"></div>
      <table class="al-table"><thead><tr><th>Time UTC</th><th>Venue</th><th>Symbol</th><th>Source</th><th>Price</th><th>Type</th><th></th></tr></thead><tbody></tbody></table>
      <footer class="al-foot"><button type="button" data-act="clear">Delete all</button><span class="al-note"></span></footer>`;
    const tbody = $('tbody', drawer), msg = $('.al-msg', drawer);
    const say = text => { msg.textContent = text || ''; };
    center.status = text => { say(text); const f = $('#scalp-status'); if (f && text) f.textContent = text; };

    function applyOpen() {
      drawer.hidden = !prefs.open; drawer.dataset.dock = prefs.dock; root.dataset.alertsDock = prefs.open ? prefs.dock : '';
      toggle.setAttribute('aria-expanded', String(prefs.open));
      page.columns.forEach(c => c.resize?.());
    }
    function notifyLabel() {
      const b = $('[data-act="notify"]', drawer);
      if (typeof Notification === 'undefined') { b.textContent = 'Browser notifications unsupported'; b.disabled = true; return; }
      b.textContent = Notification.permission === 'granted' ? `Browser notifications: ${prefs.notify ? 'on' : 'off'}` : Notification.permission === 'denied' ? 'Browser notifications blocked' : 'Enable browser notifications…';
    }
    function cell(text, cls) { const td = document.createElement('td'); td.textContent = text; if (cls) td.className = cls; return td; }

    center.renderPanel = () => {
      const all = center.store.list(), active = SA.activeRows(all), fired = center.log.list();
      const counts = { active: active.length, triggered: fired.length };
      for (const b of drawer.querySelectorAll('[data-tab]')) {
        b.setAttribute('aria-selected', String(b.dataset.tab === prefs.tab)); $('.n', b).textContent = String(counts[b.dataset.tab]);
      }
      $('.badge', toggle).textContent = String(fired.length);
      $('[data-pref="sound"]', drawer).checked = prefs.sound;
      $('.al-audio', drawer).textContent = `audio: ${center.audioState()}`;
      notifyLabel();
      const rows = prefs.tab === 'active' ? active : fired;
      tbody.replaceChildren();
      for (const r of rows) {
        const tr = document.createElement('tr'); tr.dataset.id = r.id; tr.dataset.exchange = r.exchange; tr.dataset.symbol = r.symbol; tr.dataset.price = r.price;
        tr.title = prefs.tab === 'active' ? `${r.status}${r.note ? ` · ${r.note}` : ''}` : r.detail || '';
        tr.append(cell(AC.hms(r.at)), cell(AC.VENUE_LABEL[r.exchange] || r.exchange), cell(r.symbol), cell(r.source), cell(r.price, 'num'),
          cell(prefs.tab === 'active' ? `${r.type} · ${r.status.split(' ')[0]}` : SA.TYPE_LABELS[r.type] || r.type));
        const del = document.createElement('td'), btn = document.createElement('button');
        btn.type = 'button'; btn.dataset.act = 'del'; btn.textContent = '×'; btn.title = prefs.tab === 'active' ? 'Delete this level' : 'Delete this notification';
        del.append(btn); tr.append(del); tbody.append(tr);
      }
      if (!rows.length) { const tr = document.createElement('tr'); tr.append(cell(prefs.tab === 'active' ? 'No armed signal levels. Shift+Alt+click a price to set one.' : 'Nothing triggered yet.', 'empty')); tr.firstChild.colSpan = 7; tbody.append(tr); }
      $('.al-note', drawer).textContent = prefs.tab === 'active' ? 'deletes the levels themselves' : `${fired.length}/${SA.LOG_CAP} kept`;
    };

    function centreOn(exchange, symbol, price) {
      const col = page.columns.find(c => c.cfg.venue === exchange && c.cfg.symbol === symbol);
      if (!col) { say(`No column shows ${AC.VENUE_LABEL[exchange] || exchange} ${symbol}`); return; }
      if (!col.rowN || !col.tick) { say(`${symbol}: waiting for a synced book`); return; }
      col.follow = false; col.view.centerRow = Math.floor(Number(price) / (col.rowN * col.tick) + 1e-6);
      page.setActive(col.index); page.dirty(col);
      say(`Centred ${symbol} on ${price} · press C to resume autocenter`);
    }
    drawer.addEventListener('click', event => {
      const t = event.target, act = t.dataset?.act, tab = t.closest('[data-tab]')?.dataset.tab;
      if (tab) { prefs.tab = tab; center.savePrefs(); center.renderPanel(); return; }
      if (act === 'close') { prefs.open = false; center.savePrefs(); applyOpen(); return; }
      if (act === 'dock') { prefs.dock = prefs.dock === 'right' ? 'left' : 'right'; center.savePrefs(); applyOpen(); return; }
      if (act === 'notify') {
        if (typeof Notification !== 'undefined' && Notification.permission === 'granted') { prefs.notify = !prefs.notify; center.savePrefs(); center.renderPanel(); }
        else center.requestNotifications().then(p => { say(`Notification permission: ${p}`); center.renderPanel(); });
        return;
      }
      if (act === 'clear') {
        if (prefs.tab === 'triggered') center.log.clear();
        else for (const r of SA.activeRows(center.store.list())) center.store.remove(r.id);
        center.renderPanel(); return;
      }
      const tr = t.closest('tr[data-id]');
      if (!tr) return;
      if (act === 'del') { if (prefs.tab === 'triggered') center.log.remove(tr.dataset.id); else center.store.remove(tr.dataset.id); center.renderPanel(); return; }
      centreOn(tr.dataset.exchange, tr.dataset.symbol, tr.dataset.price);
    });
    drawer.addEventListener('change', event => {
      if (event.target.dataset.pref === 'sound') { prefs.sound = event.target.checked; center.savePrefs(); }
    });
    toggle.addEventListener('click', () => { prefs.open = !prefs.open; center.savePrefs(); applyOpen(); center.renderPanel(); });
    window.addEventListener('storage', event => { if (event.key === SA.LOG_KEY) center.renderPanel(); });
    applyOpen(); center.renderPanel();
  }
  window.ScalpAlertsPanel = { mountPanel };
})();
