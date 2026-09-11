// a run this page did not start still moves its card: the board watches /api/runs and redraws when
// the set of running cards changes, keeping focus on the card you were on, and never rebuilds the
// strips under an open card. before this, a card run from the api (or running across a page
// reload) stayed drawn as doing, blue edge, after it had finished.
// run: node tests/js/follow_runs.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => readFileSync(new URL(`../smortui/ui_base/assets/${p}`, root), 'utf8');
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const responses = new Map();
const calls = [];
function stub(path, body) { responses.set(path, {ok: true, status: 200, json: async () => body}); }
function fetchStub(path) {
  calls.push(path);
  return Promise.resolve(responses.get(path) || {ok: false, status: 404, json: async () => ({})});
}

installStubDom({fetchImpl: fetchStub});
globalThis.getComputedStyle = () => ({transform: 'none', getPropertyValue: () => ''});

const card = (id, status) => ({id, title: id, status, blocked_reason_code: null, criteria: [], tasks: []});
stub('/api/boards', [{id: 'b1', name: 'one', position: 0, created_at: 't'}]);
stub('/api/boards/b1/cards', [card('c1', 'doing'), card('c2', 'todo')]);
stub('/api/runs', [{card_id: 'c1', phase: 'running', running: true}]);

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
  smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {followRunsOnce, onBoardEnter, setOpenCard: v => { openCard = v; }};`)(SpyMenu, SpyDrawer);

const flush = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setTimeout(r, 0)); };
await flush();
const inColumn = (status, id) => bucketRow
  .querySelector(`.bucket[data-status="${status}"] .bucket-rows`)
  .querySelectorAll('.card-strip').some(s => s.dataset.cardId === id);
const cardFetches = () => calls.filter(p => p === '/api/boards/b1/cards').length;

// ---- the first look only learns what is running; nothing has changed yet, so no redraw
assert.ok(inColumn('doing', 'c1'), 'c1 starts in doing');
let fetched = cardFetches();
assert.equal(await mod.followRunsOnce(), false, 'the first poll only records the running set');
assert.equal(await mod.followRunsOnce(), false, 'an unchanged set redraws nothing');
assert.equal(cardFetches(), fetched);

// ---- the run finishes elsewhere: the next poll redraws, and c1 moves to checking
stub('/api/runs', []);
stub('/api/boards/b1/cards', [card('c1', 'checking'), card('c2', 'todo')]);
const c2 = bucketRow.querySelectorAll('.card-strip').find(s => s.dataset.cardId === 'c2');
c2.focus();
assert.equal(await mod.followRunsOnce(), true, 'a finished run redraws the board');
assert.ok(inColumn('checking', 'c1') && !inColumn('doing', 'c1'), 'c1 is drawn in checking now');
assert.equal(document.activeElement?.dataset?.cardId, 'c2', 'focus stays on the card you were on');

// ---- a run starting elsewhere redraws too, but never under an open card - it waits a tick
stub('/api/runs', [{card_id: 'c2', phase: 'preparing', running: true}]);
mod.setOpenCard({cardId: 'c1'});
fetched = cardFetches();
assert.equal(await mod.followRunsOnce(), false, 'an open card holds the redraw back');
assert.equal(cardFetches(), fetched);
mod.setOpenCard(null);
assert.equal(await mod.followRunsOnce(), true, 'and it happens on the next tick once the card is closed');

console.log('ok');
