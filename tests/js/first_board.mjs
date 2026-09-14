// the first board on a fresh install: the empty state hides the columns rather than removing them,
// so the board created from b renders into them and the panel refreshes to show it. before this, the
// empty state wiped the columns and the first create threw before the panel re-rendered.
// run: node tests/js/first_board.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => readFileSync(new URL(`../smortui/ui_base/assets/${p}`, root), 'utf8');
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const responses = new Map();
function stub(path, method, status, body) {
  responses.set(`${method} ${path}`, {ok: status >= 200 && status < 300, status, json: async () => body});
}
function fetchStub(path, opts) {
  const resp = responses.get(`${(opts && opts.method) || 'GET'} ${path}`);
  return Promise.resolve(resp || {ok: false, status: 404, json: async () => ({error: 'no stub'})});
}

installStubDom({fetchImpl: fetchStub});
globalThis.getComputedStyle = () => ({transform: 'none', getPropertyValue: () => ''});

stub('/api/boards', 'GET', 200, []);

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

function SpyDrawer() {
  return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false};
}
class SpyMenu { constructor(opts) { this.opts = opts; } openAt() { return this; } refresh() {} close() {} }

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js'), smort('boards.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {bp, currentBoard: () => currentBoardId};`)(SpyMenu, SpyDrawer);

const flush = () => new Promise(r => setTimeout(r, 0));
const buckets = () => bucketRow.querySelectorAll('.bucket');
await flush(); await flush();

// ---- empty: the placeholder shows, the columns are hidden but still there ------------------------
assert.equal(bucketRow.querySelectorAll('.empty-state').length, 1, 'the empty state renders');
assert.equal(buckets().length, 5, 'the columns survive the empty state');
assert.ok(buckets().every(b => b.hidden), 'and are hidden behind it');

// ---- creating the first board renders into the columns and refreshes the panel -------------------
document._dispatch('keydown', {code: 'KeyB', key: 'b', target: document.body, preventDefault() {}});
await flush();
const BOARD = {id: 'b1', name: 'alpha test', position: 0, created_at: 't'};
stub('/api/boards', 'POST', 201, BOARD);
stub('/api/boards', 'GET', 200, [BOARD]);
stub('/api/boards/b1/cards', 'GET', 200, []);
stub('/api/boards/b1/repos', 'GET', 200, []);
mod.bp.boardNameInput.value = 'alpha test';
(mod.bp.boardNameInput._listeners.keydown || []).forEach(fn =>
  fn({code: 'Enter', key: 'Enter', target: mod.bp.boardNameInput, preventDefault() {}, stopPropagation() {}}));
for (let i = 0; i < 6; i++) await flush();

assert.equal(mod.currentBoard(), 'b1', 'the new board is current');
assert.equal(bucketRow.querySelectorAll('.empty-state').length, 0, 'the empty state is gone');
assert.ok(buckets().every(b => !b.hidden), 'the columns are back');
assert.equal(mod.bp.boardListEl.querySelectorAll('.board-row').length, 1, 'the panel lists the new board');
assert.equal(mod.bp.reposLabelEl.textContent, 'repos on alpha test', 'and selects it for repos');

console.log('ok');
