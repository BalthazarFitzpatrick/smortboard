import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const calls = [];
let response = {status: 200, body: {}};
let boardsResponse = null;
installStubDom({fetchImpl: (path, opts) => {
  calls.push({path, opts});
  if (path === '/api/boards' && boardsResponse !== null) {
    return Promise.resolve({ok: true, status: 200, json: async () => boardsResponse});
  }
  if (!opts) return new Promise(() => {});
  return Promise.resolve({ok: response.status < 400, status: response.status,
    json: async () => response.body});
}});
for (const id of ['board-bar', 'bucket-row']) {
  const el = element('div'); el.id = id; document.body.appendChild(el);
}
const menus = [];
class Menu {
  constructor(opts) { this.opts = opts; }
  openAt() { menus.push(this); return this; }
  close() {}
  pick(id) { this.opts.sections[0].onPick({id}); }
}
const src = ['buckets.js', 'expand.js', 'indicate.js', 'shell.js', 'pile.js']
  .map(p => uiBaseAsset(root, p)).concat(
    ['columns.js', 'card_panel.js', 'chat.js', 'shortcuts.js', 'board.js']
      .map(p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8'))).join('\n;\n');
const mod = new Function('Menu', `${src}
;return {renderBoardMergeMode, doAcceptOrRejectCard, loadBoards,
  setBoards: value => { boards = value; },
  setBoard: id => { currentBoardId = id; renderBoardMergeMode(); }};`)(Menu);
const flush = () => new Promise(resolve => setTimeout(resolve, 0));
const row = document.getElementById('bucket-row');
mod.setBoards([{id: 'review'}, {id: 'free', merge_mode: 'free'}]);
mod.setBoard('review');
assert.equal(row.classList.contains('edge-pulse'), false);
assert.equal(document.getElementById('merge-mode-label').textContent, 'review required');
mod.setBoard('free');
assert.equal(row.classList.contains('edge-pulse'), true);
assert.equal(document.getElementById('merge-mode-label').textContent, 'free merge');
mod.setBoard('review');
function shiftA() {
  document._dispatch('keydown', {code: 'KeyA', key: 'A', shiftKey: true,
    target: document.body, preventDefault() {}});
}
shiftA();
assert.equal(calls.filter(c => c.opts?.method === 'PATCH').length, 0);
assert.equal(menus.at(-1).opts.sections[0].items.find(i => i.id === 'cancel').autofocus, true);
menus.at(-1).pick('cancel');
await flush();
assert.equal(calls.filter(c => c.opts?.method === 'PATCH').length, 0);
shiftA();
menus.at(-1).pick('confirm');
await flush();
assert.equal(row.classList.contains('edge-pulse'), true);
const patch = calls.find(c => c.opts?.method === 'PATCH');
assert.equal(patch.path, '/api/boards/review');
assert.deepEqual(JSON.parse(patch.opts.body), {merge_mode: 'free'});
shiftA();
menus.at(-1).pick('confirm');
await flush();
assert.equal(row.classList.contains('edge-pulse'), false);
// a refused toggle leaves the displayed mode intact
response = {status: 400, body: {error: 'refused'}};
shiftA(); menus.at(-1).pick('confirm'); await flush();
assert.equal(row.classList.contains('edge-pulse'), false);
// 202 leaves the card and focus in place until normal polling observes the landing
response = {status: 202, body: {state: 'landing'}};
const before = calls.length;
await mod.doAcceptOrRejectCard('accept', 'card');
assert.deepEqual(calls.slice(before).map(c => c.path), ['/api/cards/card/accept']);
mod.setBoard('free');
assert.equal(row.classList.contains('edge-pulse'), true);
boardsResponse = [];
await mod.loadBoards();
assert.equal(row.classList.contains('edge-pulse'), false);
assert.equal(document.getElementById('merge-mode-label').hidden, true);
console.log('merge mode wiring passed');
