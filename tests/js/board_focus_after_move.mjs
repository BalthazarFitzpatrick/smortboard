// exercises board.js's post-move focus: accepting or rejecting a card (y/x) must never leave focus
// riding into the card's new column. it lands on the card directly above the mover in its old
// column, else hops left across empty columns to the nearest non-empty one's topmost card, else
// parks on nothing - which the board must still recover from on the next keypress, not by falling
// through to the board bar. run: node tests/js/board_focus_after_move.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');
const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'), smort('board.js')].join('\n;\n');
const STATUS_ORDER = ['todo', 'doing', 'checking', 'accepted', 'rejected'];
const BOARD_ID = 'b1';

class SpyMenu { constructor(opts) { this.opts = opts; } openAt() { return this; } close() {} }
function SpyDrawer() { return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false}; }

// a fresh stub document, response map and module per scenario - nothing carries over, same
// reasoning board_column_skip.mjs gives for why each case gets its own setup()
function setup(cardsByStatus) {
  const responses = new Map();
  const key = (path, method) => `${method || 'GET'} ${path}`;
  const stubJson = (status, body) => ({ok: status >= 200 && status < 300, status, json: async () => body});
  const stub = (path, method, status, body) => responses.set(key(path, method), stubJson(status, body));
  const fetchStub = (path, opts) => {
    const resp = responses.get(key(path, opts && opts.method));
    return Promise.resolve(resp || stubJson(404, {error: 'no stub for ' + key(path, opts && opts.method)}));
  };
  installStubDom({fetchImpl: fetchStub});

  const boardBar = element('div', 'board-bar');
  boardBar.id = 'board-bar';
  const bucketRow = element('div', 'bucket-row');
  bucketRow.id = 'bucket-row';
  STATUS_ORDER.forEach(status => {
    const bucket = element('div', 'bucket');
    bucket.dataset.status = status;
    bucket.appendChild(element('div', 'bucket-label'));
    bucket.appendChild(element('div', 'bucket-rows'));
    bucketRow.appendChild(bucket);
  });
  document.body.appendChild(boardBar);
  document.body.appendChild(bucketRow);

  // board.js calls loadBoards() at import time. an empty list here would set renderEmptyState(true),
  // which hides every .bucket - and nothing later unhides them, since loadBoards only runs once -
  // so focusAfterColumnExit's hop-left would filter out every column. one real board keeps them shown
  stub('/api/boards', 'GET', 200, [{id: BOARD_ID, name: 'b'}]);

  const mod = new Function('Menu', 'makeDrawer', `${src}
;return {onBoardEnter, acceptOrRejectCard, reenterIfFocusLost};`)(SpyMenu, SpyDrawer);

  const flatten = () => STATUS_ORDER.flatMap(status =>
    (cardsByStatus[status] || []).map(id => ({id, title: id, status, workstream: 'w'})));
  stub(`/api/boards/${BOARD_ID}/cards`, 'GET', 200, flatten());

  const strip = id => bucketRow.querySelector(`[data-card-id="${id}"]`);
  return {mod, stub, strip, bucketRow};
}

const flush = () => new Promise(r => setTimeout(r, 0));

// after a move, the reload's GET reflects the card in its new column - moved out of `from`,
// appended under `to` - everything else in cardsByStatus stays put
function moved(cardsByStatus, id, from, to) {
  const next = {};
  STATUS_ORDER.forEach(s => { next[s] = (cardsByStatus[s] || []).filter(x => x !== id); });
  next[to] = [...(next[to] || []), id];
  return next;
}

// ---- focus lands on the card directly above the mover, not into its new column --------------------
{
  const layout = {doing: ['d1', 'd2']};
  const {mod, stub, strip} = setup(layout);
  await flush(); await flush();
  await mod.onBoardEnter(BOARD_ID); // renders the pre-move layout; sets currentBoardId too
  await flush();
  strip('d2').focus();
  // now stub the post-move state - stubbing it before onBoardEnter would have rendered d2 already
  // moved, so the strip this test focuses would never have sat in the doing column at all
  stub('/api/cards/d2/accept', 'POST', 200, {});
  stub(`/api/boards/${BOARD_ID}/cards`, 'GET', 200,
    Object.entries(moved(layout, 'd2', 'doing', 'accepted')).flatMap(([status, ids]) =>
      ids.map(id => ({id, title: id, status, workstream: 'w'}))));
  await mod.acceptOrRejectCard('accept');
  await flush(); await flush();
  assert.equal(document.activeElement.dataset.cardId, 'd1',
    'focus should land on the card that stood directly above the mover');
}

// ---- topmost mover with nothing above it hops left, skipping empty columns, to the nearest -------
// non-empty column's topmost card - doing and checking both sit empty between accepted and todo
{
  const layout = {todo: ['t1'], accepted: ['a1', 'a2']};
  const {mod, stub, strip} = setup(layout);
  await flush(); await flush();
  await mod.onBoardEnter(BOARD_ID);
  await flush();
  strip('a1').focus();
  stub('/api/cards/a1/reject', 'POST', 200, {});
  stub(`/api/boards/${BOARD_ID}/cards`, 'GET', 200,
    Object.entries(moved(layout, 'a1', 'accepted', 'rejected')).flatMap(([status, ids]) =>
      ids.map(id => ({id, title: id, status, workstream: 'w'}))));
  await mod.acceptOrRejectCard('reject');
  await flush(); await flush();
  assert.equal(document.activeElement.dataset.cardId, 't1',
    'focus should hop left over the two empty columns (doing, checking) to todo\'s topmost card');
}

// ---- no card above and nothing non-empty to the left: focus parks on nothing, and the next -------
// bound keypress re-enters the board via its own row, never the board bar
{
  const layout = {todo: ['t1']};
  const {mod, stub, strip} = setup(layout);
  await flush(); await flush();
  await mod.onBoardEnter(BOARD_ID);
  await flush();
  strip('t1').focus();
  stub('/api/cards/t1/accept', 'POST', 200, {});
  stub(`/api/boards/${BOARD_ID}/cards`, 'GET', 200,
    Object.entries(moved(layout, 't1', 'todo', 'accepted')).flatMap(([status, ids]) =>
      ids.map(id => ({id, title: id, status, workstream: 'w'}))));
  await mod.acceptOrRejectCard('accept');
  await flush(); await flush();
  assert.equal(document.activeElement, document.body,
    'with no card above and no non-empty column to the left, nothing should be focused');

  // no board bar tab exists in this stub, so a wrong fall-through to returnToBoardBar() would
  // leave activeElement on body too - the real discriminator is that reenterIfFocusLost finds and
  // focuses the card row first, landing on a card rather than staying parked
  const recovered = mod.reenterIfFocusLost();
  assert.ok(recovered, 'a lost-focus reentry should report it recovered focus');
  assert.equal(document.activeElement.dataset.cardId, 't1',
    'the next keypress should re-enter onto a card row, not fall through to the board bar');
}

console.log('ok');
