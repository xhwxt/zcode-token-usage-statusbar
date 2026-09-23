// Exercise the real context reader and refresh loop with a small DOM fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../overlay.js'), 'utf8');
const reader = source.slice(source.indexOf('    var nativeCtx ='), source.indexOf('    /* ---------- 定位'));
const refresh = source.slice(source.indexOf('    function heavy()'), source.indexOf('    function hideBar()'));
let now = 10000, renders = [];
const card = (label) => ({ isConnected: true, querySelectorAll: () => [
  { getAttribute: () => label, textContent: label }
] });
const context = vm.createContext({
  Date: {now: () => now}, stale: () => false, pid: 'pane',
  nativeCtxVal: 0, pendingSid: 'a', instSid: 'a', slot: 1,
  composer: {isConnected: true}, state: {data: {}},
  ensureCardPad() {}, render() { renders.push(context.nativeCtxVal); },
});
context.wantedEl = context.composer;
context.cardCache = card('总量 1,000,000');
vm.runInContext(reader + refresh, context);
context.heavy();
assert.deepEqual(renders, [1000000]);
context.heavy();
assert.equal(renders.length, 1, 'unchanged state should not render repeatedly');
context.cardCache = card('总量 175，616');
context.pendingSid = 'b';
context.heavy();
assert.deepEqual(renders, [1000000, 175616], 'session switch must refresh before cache expiry');
context.cardCache.querySelectorAll = card('总量 32,000').querySelectorAll;
now += 5001;
context.heavy();
assert.equal(renders.at(-1), 32000, 'model change must render without new usage data');
context.cardCache = card('no native context label');
context.heavy();
assert.equal(renders.at(-1), 0, 'missing native limit must clear stale value for fallback');
console.log('Overlay context regression checks passed');
