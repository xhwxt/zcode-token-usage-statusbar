const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '..', 'overlay.js'), 'utf8');

class Target {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, fn, capture = false) {
    const list = this.listeners.get(type) || [];
    if (!list.some(item => item.fn === fn && item.capture === capture)) list.push({ fn, capture });
    this.listeners.set(type, list);
  }
  removeEventListener(type, fn, capture = false) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item.fn !== fn || item.capture !== capture));
  }
  dispatch(type) {
    for (const { fn } of [...(this.listeners.get(type) || [])]) fn.call(this, { target: this });
  }
  count(type) { return (this.listeners.get(type) || []).length; }
}

class Element extends Target {
  constructor(tag) {
    super();
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {};
    this.style = { cursor: '', display: 'none' }; this.className = ''; this.textContent = '';
    this.innerHTML = ''; this.offsetWidth = 420; this.offsetHeight = 240;
  }
  appendChild(el) { this.children.push(el); el.parentElement = this; return el; }
  remove() {
    if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(el => el !== this);
    this.parentElement = null;
  }
  contains(el) { return this === el || this.children.some(child => child.contains(el)); }
  querySelector(selector) { return selector === 'p' ? this.children.find(el => el.tagName === 'P') || null : null; }
  getBoundingClientRect() { return { left: 20, top: 400, bottom: 430, width: 500, height: 30 }; }
}

function harness(lang = 'zh', rateLine = true) {
  const window = new Target();
  Object.assign(window, { innerWidth: 1000, innerHeight: 800, __zusagePluginState: { rateLine } });
  const body = new Element('body'), quote = body.appendChild(new Element('blockquote'));
  const line = quote.appendChild(new Element('p'));
  line.textContent = '⚡ 153.8 tok/s(本轮) · 首字 3.0s · 输出 2,000 tok';
  line.style.cursor = 'text';
  const document = {
    body, createElement: tag => new Element(tag),
    querySelectorAll(selector) {
      if (selector === 'blockquote p, blockquote, p') return [line, quote];
      if (selector.startsWith('.zusage-root')) return body.children.filter(el => /zusage-/.test(el.className));
      return [];
    },
  };
  const intervals = new Map(); let next = 0;
  const sandbox = vm.createContext({ window, document, console,
    localStorage: { getItem: key => key === 'zusage3.lang' ? lang : null, setItem() {} },
    setInterval: (fn, ms) => { const id = ++next; intervals.set(id, { fn, ms }); return id; },
    clearInterval: id => intervals.delete(id), setTimeout: () => ++next,
  });
  // Execute the actual entire IIFE. Only expose its real shared-state/render path;
  // delayed composer discovery is outside the document-level rate tooltip tests.
  const executable = source.replace(/\}\)\(\);\s*$/, 'window.rateTest = { language: function(lang) { state.lang = lang; renderAll(true); } };})();');
  function inject() { window.__zusageOverlay = false; vm.runInContext(executable, sandbox); }
  inject();
  return { window, body, line, quote, intervals, inject,
    tip: () => body.children.find(el => el.className.includes('zusage-rate-tip')),
    scan: () => { for (const timer of [...intervals.values()]) if (timer.ms === 2000) timer.fn(); },
  };
}

test('R9: actual bilingual rate tooltip HTML has seven populated rows and correct thinking basis', () => {
  for (const lang of ['zh', 'en']) {
    const h = harness(lang); h.line.dispatch('mouseenter');
    const html = h.tip().innerHTML;
    assert.equal(h.tip().style.display, 'block');
    assert.doesNotMatch(html, /undefined/);
    assert.equal((html.match(/class="rt-row"/g) || []).length, 7);
    const texts = [...html.matchAll(/<span>(.*?)<\/span>/g)].map(match => match[1]);
    assert.equal(texts.length, 7); assert.ok(texts.every(Boolean));
    assert.match(html, lang === 'en' ? /thinking tokens already included/ : /思考 token 已含其中/);
    assert.match(html, lang === 'en' ? /thinking.*started_at/ : /思考.*started_at/);
  }
});

test('R10: reinjection rebinds existing nodes and removes old listeners, scan interval and scroll handler', () => {
  const h = harness();
  const oldTips = [], oldScans = [];
  for (let generation = 0; generation < 4; generation++) {
    h.line.dispatch('mouseenter');
    assert.equal(h.tip().style.display, 'block');
    assert.equal(h.line.count('mouseenter'), 1); assert.equal(h.line.count('mouseleave'), 1);
    assert.equal(h.quote.count('mouseenter'), 0);
    assert.equal(h.window.count('scroll'), 1); assert.equal(h.intervals.size, 1);
    oldTips.push(h.tip()); oldScans.push([...h.intervals.values()][0].fn);
    h.inject();
    assert.equal(oldTips.at(-1).parentElement, null);
    assert.equal(oldTips.at(-1).style.display, 'none');
    for (const scan of oldScans) scan();
    for (const tip of oldTips) assert.equal(tip.style.display, 'none');
  }
  h.line.dispatch('mouseenter'); assert.equal(h.tip().style.display, 'block');
  h.window.dispatch('scroll'); assert.equal(h.tip().style.display, 'none');
});

test('R10: language refreshes open tooltip and toggle off cleans bindings; re-enable rescans old lines', () => {
  const h = harness(); h.line.dispatch('mouseenter');
  h.window.rateTest.language('en');
  assert.match(h.tip().innerHTML, /Rate line · what each figure means/);
  assert.doesNotMatch(h.tip().innerHTML, /undefined/);
  h.window.__zusagePluginUpdate({ rateLine: false });
  assert.equal(h.tip().style.display, 'none'); assert.equal(h.line.count('mouseenter'), 0);
  assert.equal(h.line.style.cursor, 'text');
  h.scan(); h.line.dispatch('mouseenter'); assert.equal(h.tip().style.display, 'none');
  h.window.__zusagePluginUpdate({ rateLine: true });
  h.line.dispatch('mouseenter'); assert.equal(h.tip().style.display, 'block');
  assert.match(h.tip().innerHTML, /Rate line/);
});

test('R10: disabled startup and virtual-list recycling do not retain hover listeners', () => {
  const disabled = harness('zh', false);
  disabled.line.dispatch('mouseenter'); assert.equal(disabled.tip().style.display, 'none');
  assert.equal(disabled.line.count('mouseenter'), 0);
  const h = harness(); h.line.dispatch('mouseenter');
  h.line.textContent = 'ordinary reply'; h.scan();
  assert.equal(h.tip().style.display, 'none'); assert.equal(h.line.count('mouseenter'), 0);
  h.line.textContent = '⚡ 200 tok/s(本轮)'; h.scan();
  h.line.dispatch('mouseenter'); assert.equal(h.tip().style.display, 'block');
  h.line.remove(); h.scan();
  assert.equal(h.tip().style.display, 'none'); assert.equal(h.line.count('mouseenter'), 0);
  assert.equal(h.line.style.cursor, 'text');
});
