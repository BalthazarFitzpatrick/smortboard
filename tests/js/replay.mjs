// RUN REPLAY (t): opens for the focused/open card, renders the rail with the right verdict markers,
// j/k/arrows/home/end scrub without leaking to the board's global handler, an edit step renders a
// removed/added diff, and t again closes the panel.
// run: node tests/js/replay.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => readFileSync(new URL(`../smortui/ui_base/assets/${p}`, root), 'utf8');
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const responses = new Map();
const calls = [];
function stubJson(status, body) {
  return {ok: status >= 200 && status < 300, status, json: async () => body};
}
function fetchStub(path, opts) {
  calls.push({path, opts});
  const resp = responses.get(path);
  return Promise.resolve(resp || stubJson(404, {error: 'no stub for ' + path}));
}

installStubDom({fetchImpl: fetchStub});
responses.set('/api/boards', stubJson(200, []));

const boardBar = element('div', 'board-bar');
boardBar.id = 'board-bar';
const bucketRow = element('div', 'bucket-row');
bucketRow.id = 'bucket-row';
['todo', 'doing', 'checking', 'accepted', 'rejected'].forEach(status => {
  const bucket = element('div', 'bucket');
  bucket.dataset.status = status;
  bucket.appendChild(element('div', 'bucket-label'));
  bucket.appendChild(element('div', 'bucket-rows'));
  bucketRow.appendChild(bucket);
});
document.body.appendChild(boardBar);
document.body.appendChild(bucketRow);

class SpyMenu {
  constructor(opts) { this.opts = opts; }
  openAt() { return this; }
  refresh() {}
  close() {}
}
function SpyDrawer() {
  return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false};
}

const src = [
  uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'),
  smort('board.js'), smort('replay.js'),
].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {toggleReplay, rp, BINDINGS};`)(SpyMenu, SpyDrawer);

function press(code, target) {
  document._dispatch('keydown', {code, key: code, target: target || document.body, preventDefault() {}});
}

// the panel's own keydown listener sits BETWEEN the pressed target and document in real bubbling -
// dom_stub's document._dispatch does not simulate bubbling, so this calls the panel's listener
// directly (as the browser would, before the event ever reaches document) and proves it calls
// stopPropagation - the mechanism that actually keeps the board's global handler from seeing it
function pressOnPanel(code) {
  let stopped = false;
  const evt = {code, key: code, target: mod.rp.panel, preventDefault() {}, stopPropagation() { stopped = true; }};
  (mod.rp.panel._listeners.keydown || []).forEach(fn => fn(evt));
  return stopped;
}

// a focused card strip is what actionableCardId() reads
const strip = element('div', 'row card card-strip');
strip.dataset.cardId = 'c9';
strip.tabIndex = -1;
bucketRow.appendChild(strip);
strip.focus();

const STEPS = [
  {seq: 1, created_at: 't1', kind: 'narration', label: 'looking around', detail: {text: 'looking around'}, ok: null},
  {
    seq: 2, created_at: 't2', kind: 'edit', label: 'edited a.py',
    detail: {file_path: '/a.py', old_text: 'one\ntwo\nthree', new_text: 'one\ntwo-fixed\nthree'}, ok: true,
  },
  {seq: 3, created_at: 't3', kind: 'refused', label: 'refused: Bash', detail: {reason: 'permission denied'}, ok: false},
];
responses.set('/api/cards/c9/timeline', stubJson(200, {
  attempt: 1,
  attempts: [{attempt: 1, started_at: 't0', step_count: 3, reached_worker: true}],
  steps: STEPS,
}));

// ---- t opens the panel for the focused card and renders the rail with the right verdicts ----------
mod.toggleReplay();
await new Promise(r => setTimeout(r, 0));
assert.ok(mod.rp.backdrop.parentNode, 'the panel is attached once opened');
assert.ok(calls.some(c => c.path === '/api/cards/c9/timeline'), 't loads the focused card\'s timeline');
assert.equal(mod.rp.rail.children.length, 3, 'one rail row per step');
const markerClasses = [...mod.rp.rail.children].map(row => row.querySelector('.replay-marker').className);
assert.ok(markerClasses[0].includes('replay-marker-none'), 'no verdict is a hollow marker');
assert.ok(markerClasses[1].includes('replay-marker-ok'), 'a passed step is lichen');
assert.ok(markerClasses[2].includes('replay-marker-fail'), 'a refused step is stone red');
assert.equal(mod.rp.index, 0, 'the panel opens on the first step');

// ---- an edit step's detail renders removed and added lines, not colour alone -----------------------
mod.rp.rail.children[1].onclick();
assert.equal(mod.rp.index, 1);
assert.ok(mod.rp.detail.innerHTML.includes('replay-diff-removed'), 'removed lines get their own class');
assert.ok(mod.rp.detail.innerHTML.includes('replay-diff-added'), 'added lines get their own class');
assert.ok(mod.rp.detail.innerHTML.includes('- two'), 'the removed line keeps its - gutter');
assert.ok(mod.rp.detail.innerHTML.includes('+ two-fixed'), 'the added line keeps its + gutter');

// ---- j/k and arrows scrub steps, and every one of them stops propagation before it could reach
// the board's own document-level handler --------------------------------------------------------
assert.ok(pressOnPanel('KeyJ'), 'j stops propagation');
assert.equal(mod.rp.index, 2, 'j moves to the next step');
assert.ok(pressOnPanel('KeyK'), 'k stops propagation');
assert.equal(mod.rp.index, 1, 'k moves back a step');
assert.ok(pressOnPanel('ArrowDown'), 'arrowdown stops propagation');
assert.equal(mod.rp.index, 2, 'arrowdown also steps forward');
assert.ok(pressOnPanel('Home'), 'home stops propagation');
assert.equal(mod.rp.index, 0, 'home jumps to the first step');
assert.ok(pressOnPanel('End'), 'end stops propagation');
assert.equal(mod.rp.index, 2, 'end jumps to the last step');

// ---- escape closes the panel -------------------------------------------------------------------------
assert.ok(pressOnPanel('Escape'), 'escape stops propagation');
assert.equal(mod.rp.backdrop.parentNode, null, 'escape closes the panel');

// ---- t again re-opens, and t while open closes it ------------------------------------------------------
strip.focus();
mod.toggleReplay();
await new Promise(r => setTimeout(r, 0));
assert.ok(mod.rp.backdrop.parentNode, 't re-opens the panel');
mod.toggleReplay();
assert.equal(mod.rp.backdrop.parentNode, null, 't again closes the panel');

console.log('ok');
