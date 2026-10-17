// Regression: the drawing toolbar chrome must not leave listeners on `document` after dispose().
// A capture 'pointerdown' listener on document kept every destroyed pane (chart, candles, DOM)
// reachable after a layout change (live: 15 of 15 destroyed panes retained after 10 switches).
const test = require('node:test');
const assert = require('node:assert/strict');

function fakeNode(tag) {
  return {
    tagName: String(tag).toUpperCase(), children: [], dataset: {}, style: {}, attrs: {}, hidden: false, textContent: '',
    className: '', title: '', value: '', disabled: false,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k] ?? null; },
    removeAttribute(k) { delete this.attrs[k]; },
    append(...c) { this.children.push(...c); }, appendChild(c) { this.children.push(c); return c; },
    insertBefore(c) { this.children.push(c); return c; }, replaceChildren(...c) { this.children = c; },
    remove() {}, querySelector() { return null; }, querySelectorAll() { return []; }, closest() { return null; },
    addEventListener() {}, removeEventListener() {},
    getBoundingClientRect() { return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; },
  };
}

function fakeDocument() {
  const added = [], removed = [];
  return {
    added, removed,
    createElement: fakeNode,
    addEventListener(type, fn, opts) { added.push({ type, fn, opts }); },
    removeEventListener(type, fn) { removed.push({ type, fn }); },
    live() {
      return added.filter(a => !(a.opts && typeof a.opts === 'object' && a.opts.signal && a.opts.signal.aborted)
        && !removed.some(r => r.type === a.type && r.fn === a.fn));
    },
  };
}

test('drawing chrome dispose() removes its document listeners', () => {
  const doc = fakeDocument();
  const previous = globalThis.document;
  globalThis.document = doc;
  try {
    const UI = require('../static/drawings/ui.js');
    const controller = {
      state: { prefs: {}, selected: null, tool: 'cursor', items: [] },
      actions: { find: () => null }, setPrefs() {}, setTool() {}, subscribe() { return () => {}; },
    };
    const chrome = UI.createChrome(controller, { header: fakeNode('header'), stage: fakeNode('div'), panels: { openSettings() {} } });
    assert.ok(doc.added.length >= 1, 'chrome listens on document for outside clicks');
    chrome.dispose();
    assert.deepEqual(doc.live().map(a => a.type), [], 'no document listener survives dispose()');
  } finally {
    globalThis.document = previous;
  }
});
