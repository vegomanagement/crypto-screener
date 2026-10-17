/* /scalp: price levels on a DOM column — Alt+click simple level, Shift+Alt+click signal level (each toggles
 * off a level already in that row), drag to move, double-click for the inline editor. Prices are exact
 * decimal strings built from the row's tick index (levels.js fromTicks), never from float pixels.
 */
(function () {
  'use strict';
  const LV = window.ScreenerLevels, R = window.ScalpRender;
  const $ = (sel, el = document) => el.querySelector(sel);

  function bind(col, center) {
    const canvas = col.canvas, store = center.store;
    const yOf = event => { const rect = canvas.getBoundingClientRect(); return { y: event.clientY - rect.top, h: rect.height }; };
    const rowSize = () => (col.rowN && col.tick ? col.rowN * col.tick : null);
    const priceAt = event => {
      if (!rowSize()) return null;
      const { y, h } = yOf(event), row = R.rowAtY(col.view, y, h);
      return LV.fromTicks(row * col.rowN, col.tickStr);
    };
    const hit = event => {
      if (!rowSize()) return null;
      const { y, h } = yOf(event);
      return R.levelAtY(col.levels, col.view, y, h, rowSize(), 5);
    };
    const sameRow = (level, price) => rowSize() && Math.floor(Number(level.price) / rowSize() + 1e-6) === Math.floor(Number(price) / rowSize() + 1e-6);
    const say = text => center.status?.(text);

    canvas.addEventListener('click', event => {
      if (!event.altKey) return;
      event.preventDefault(); event.stopImmediatePropagation();
      const price = priceAt(event);
      if (!price) { say('Levels need a synced book (tick and row size unknown yet)'); return; }
      const kind = event.shiftKey ? 'signal' : 'simple';
      const existing = (col.levels || []).find(l => l.kind === kind && sameRow(l, price));
      try {
        if (existing) { store.remove(existing.id); say(`Removed ${kind} level ${existing.price}`); }
        else { const l = store.add({ exchange: col.cfg.venue, symbol: col.cfg.symbol, price, kind, source: 'dom' }); say(`${kind === 'signal' ? 'Signal' : 'Level'} ${l.price} set`); }
      } catch (error) { say(error.message); }
    }, { capture: true });

    let drag = null;
    canvas.addEventListener('pointerdown', event => {
      if (event.button !== 0 || event.altKey || event.shiftKey || event.ctrlKey || event.metaKey) return;
      const level = hit(event);
      if (!level) return;
      drag = { id: level.id, from: level.price, price: level.price, moved: false };
      canvas.setPointerCapture?.(event.pointerId);
    });
    canvas.addEventListener('pointermove', event => {
      if (!drag) { canvas.style.cursor = hit(event) ? 'ns-resize' : ''; return; }
      const price = priceAt(event);
      if (!price || price === drag.price) return;
      drag.price = price; drag.moved = drag.moved || price !== drag.from;
      const level = (col.levels || []).find(l => l.id === drag.id);
      if (level) level.dragPrice = price; // preview only; the store is written on release
      col.page.dirty(col);
    });
    const endDrag = commit => {
      if (!drag) return;
      const d = drag; drag = null;
      const level = (col.levels || []).find(l => l.id === d.id);
      if (level) delete level.dragPrice;
      if (commit && d.moved && LV.compareDecimal(d.price, d.from) !== 0) {
        col.suppressClick = true;
        const cur = store.list({ exchange: col.cfg.venue, symbol: col.cfg.symbol }).find(l => l.id === d.id);
        if (cur) {
          // A moved signal level is a new alert: re-armed, and its "fired" grey dropped.
          const patch = cur.kind === 'signal' ? { price: d.price, armed: true, triggeredAt: null, ...(cur.color === LV.TRIGGERED_COLOR ? { color: LV.DEFAULT_COLORS.signal } : {}) } : { price: d.price };
          try { store.update(d.id, patch); say(`Level moved ${d.from} → ${d.price}`); } catch (error) { say(error.message); }
        }
      }
      col.page.dirty(col);
    };
    canvas.addEventListener('pointerup', () => endDrag(true));
    canvas.addEventListener('pointercancel', () => endDrag(false));
    canvas.addEventListener('dblclick', event => {
      const level = hit(event);
      if (!level) return;
      event.preventDefault();
      openEditor(col, center, level, yOf(event).y);
    });
  }

  function openEditor(col, center, level, y) {
    const body = $('.dom-body', col.element);
    $('.level-editor', body)?.remove();
    const form = document.createElement('form');
    form.className = 'level-editor'; form.setAttribute('aria-label', 'Level editor');
    form.style.top = `${Math.max(4, Math.min(body.clientHeight - 150, y + 10))}px`;
    form.innerHTML = `<div class="le-title"></div>
      <label>Type <select name="kind"><option value="simple">simple</option><option value="signal">signal</option></select></label>
      <label>Colour <input type="color" name="color"></label>
      <label>Width <select name="width"><option>1</option><option>2</option><option>3</option><option>4</option></select></label>
      <label><input type="checkbox" name="repeat"> Repeat (re-arm after the price leaves by ≥ 1 tick)</label>
      <label>Note <input name="note" maxlength="80" spellcheck="false" autocomplete="off"></label>
      <div class="le-error" role="alert"></div>
      <footer><button type="button" data-act="rearm">Re-arm</button><button type="button" data-act="delete">Delete</button><button type="button" data-act="close">Close</button></footer>`;
    const fill = l => {
      $('.le-title', form).textContent = `${l.kind === 'signal' ? 'Signal' : 'Level'} ${l.price} · ${l.source}${l.kind === 'signal' ? (l.armed ? ' · armed' : ' · fired') : ''}`;
      form.kind.value = l.kind; form.color.value = l.color; form.width.value = String(l.width); form.repeat.checked = l.repeat; form.note.value = l.note;
      $('[data-act="rearm"]', form).hidden = !(l.kind === 'signal' && !l.armed);
    };
    fill(level);
    const apply = patch => {
      try { const l = center.store.update(level.id, patch); if (!l) { form.remove(); return; } $('.le-error', form).textContent = ''; fill(l); }
      catch (error) { $('.le-error', form).textContent = error.message; }
    };
    form.addEventListener('change', event => {
      const t = event.target;
      if (t.name === 'kind') apply({ kind: t.value, armed: t.value === 'signal', triggeredAt: null });
      else if (t.name === 'color') apply({ color: t.value });
      else if (t.name === 'width') apply({ width: Number(t.value) });
      else if (t.name === 'repeat') apply({ repeat: t.checked });
      else if (t.name === 'note') apply({ note: t.value.slice(0, 80) });
    });
    form.addEventListener('submit', event => { event.preventDefault(); apply({ note: form.note.value.slice(0, 80) }); });
    form.addEventListener('click', event => {
      const act = event.target.dataset?.act;
      if (act === 'close') form.remove();
      if (act === 'delete') { center.store.remove(level.id); form.remove(); }
      if (act === 'rearm') apply({ armed: true, triggeredAt: null, ...(form.color.value === LV.TRIGGERED_COLOR ? { color: LV.DEFAULT_COLORS.signal } : {}) });
    });
    form.addEventListener('keydown', event => { if (event.key === 'Escape') form.remove(); event.stopPropagation(); });
    body.append(form);
    form.note.focus();
  }
  window.ScalpLevelsUI = { bind, openEditor };
})();
