// a card the scheduler has queued shows in DOING as pending - a grey strip under the cards an agent
// is actually running - rather than sitting in ATTENTION until the queue reaches it. presentation
// only: the queue emptying puts every card back in its own column with its own colour, and nothing
// about the card itself is written. run: node tests/js/card_pending.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const responses = new Map();
function stub(path, body) { responses.set(path, {ok: true, status: 200, json: async () => body}); }
function fetchStub(path) {
  return Promise.resolve(responses.get(path) || {ok: false, status: 404, json: async () => ({})});
}

installStubDom({fetchImpl: fetchStub});
globalThis.getComputedStyle = () => ({transform: 'none', rowGap: '10px', getPropertyValue: () => ''});

function card(id, status, extra = {}) {
  return {id, title: id, status, blocked_reason_code: null, review_flag: false,
    criteria: [], tasks: [], updated_at: `t-${id}`, ...extra};
}

// c1 an agent is holding, c2 a blocked card the queue re-queued (and holds back on a lease clash),
// c3 a plain todo card the queue took, c4 held back on a limit, c5 one the queue skipped entirely,
// c6 one the board retries itself
const cards = [
  card('c1', 'doing'),
  card('c2', 'doing', {blocked_reason_code: 'AGENT_QUESTION'}),
  card('c3', 'todo'),
  card('c4', 'todo', {blocked_reason_code: 'USAGE_LIMIT'}),
  card('c5', 'todo'),
  card('c6', 'doing', {blocked_reason_code: 'API_UNREACHABLE', handled_by_board: true}),
];
stub('/api/boards', [{id: 'b1', name: 'one', position: 0, created_at: 't'}]);
stub('/api/boards/b1/cards', cards);

const boardBar = element('div', 'board-bar');
boardBar.id = 'board-bar';
const bucketRow = element('div', 'bucket-row');
bucketRow.id = 'bucket-row';
['todo', 'doing', 'attention', 'checking', 'accepted', 'rejected'].forEach(status => {
  const bucket = element('div', 'bucket');
  bucket.dataset.status = status;
  bucket.appendChild(element('div', 'bucket-label'));
  const rows = element('div', 'bucket-rows');
  // room enough for every card to draw full, so the assertions read strips rather than piles
  rows.getBoundingClientRect = () => ({top: 0, left: 0, right: 300, bottom: 0, width: 300, height: 0});
  bucket.appendChild(rows);
  bucketRow.appendChild(bucket);
});
document.body.appendChild(boardBar);
document.body.appendChild(bucketRow);
globalThis.window.innerHeight = 4000;

function SpyDrawer() {
  return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false};
}
class SpyMenu { constructor(opts) { this.opts = opts; } openAt() { return this; } refresh() {} close() {} }

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), uiBase('pile.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js'), smort('scheduler.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {onBoardEnter, applyScheduleToCards, columnFor, cardClasses, cardEdgeVar, sortColumnCards,
  letterCounts, isPendingCard, setOpenCard: v => { openCard = v; }};`)(SpyMenu, SpyDrawer);

const flush = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setTimeout(r, 0)); };
const byId = id => cards.find(c => c.id === id);
const columnOf = id => ['todo', 'doing', 'attention', 'checking', 'accepted', 'rejected'].find(status =>
  bucketRow.querySelector(`.bucket[data-status="${status}"] .bucket-rows`)
    .querySelectorAll('.card-strip').some(s => s.dataset.cardId === id));
const drawnIn = status => bucketRow.querySelector(`.bucket[data-status="${status}"] .bucket-rows`)
  .querySelectorAll('.card-strip').map(s => s.dataset.cardId);
const strip = id => bucketRow.querySelectorAll('.card-strip').find(s => s.dataset.cardId === id);
const headerCount = status => bucketRow
  .querySelector(`.bucket[data-status="${status}"] .bucket-label`).querySelector('.bucket-counts').textContent;

await mod.onBoardEnter('b1');
await flush();

// ---- before the queue: a blocked card sits in attention, exactly as it did ----------------------

assert.equal(columnOf('c2'), 'attention', 'a blocked card starts in attention');
assert.equal(columnOf('c3'), 'todo', 'and a plain todo card starts in todo');
assert.ok(strip('c2').className.includes('card-attention'), 'wearing the attention edge');

// ---- w starts the queue: a queued card stays in its own column and breathes there ---------------
// doing is only what an agent holds; a queued todo card waits in todo, in queue order (c3 first)

const running = {
  running: ['c1'],
  queued: ['c3', 'c2', 'c4'],
  waiting: {c2: 'lease conflict with card c1', c4: 'paused until the five-hour window resets'},
  paused_until: null,
};
mod.applyScheduleToCards(running);
await flush();

assert.deepEqual(drawnIn('doing'), ['c1', 'c2', 'c6'],
  'running first, then the re-queued card that was already doing, then the card the board only retries');
assert.deepEqual(drawnIn('todo').slice(0, 2), ['c3', 'c4'], 'queued todo cards stay in todo, in queue order');
assert.equal(drawnIn('attention').length, 0, 'nothing queued is left in attention');
assert.equal(columnOf('c2'), 'doing', 'a blocked card held back on a lease clash is pending, not in attention');
assert.equal(columnOf('c4'), 'todo', 'one held back on a limit waits in todo');
assert.equal(columnOf('c5'), 'todo', 'a card the queue skipped entirely stays put too');

// ---- the colours: blue is the agent, grey is the queue -------------------------------------------

assert.ok(strip('c1').className.includes('card-working'), 'the card an agent holds keeps the blue edge');
['c2', 'c3', 'c4'].forEach(id => {
  const cls = strip(id).className;
  assert.ok(cls.includes('card-pending'), `${id} is drawn as pending`);
  assert.ok(cls.includes('edge-pulse'), `${id} breathes`);
  assert.ok(!cls.includes('card-working'), `${id} is not drawn as running`);
  assert.ok(!cls.includes('card-attention'), `${id} is not drawn as attention`);
});
assert.equal(mod.cardEdgeVar(byId('c2')), 'var(--grey-border)', 'a pending pile layer wears the grey edge');
assert.equal(mod.cardEdgeVar(byId('c1')), 'var(--fill-good)', 'a running one still wears the blue');

// ---- the counts agree with the strips ------------------------------------------------------------

assert.equal(headerCount('doing'), '3', 'the header matches the column');
assert.ok(!strip('c1').className.includes('edge-pulse'), 'a running card does not breathe');
assert.equal(headerCount('attention'), '0', 'and the attention count drops to nothing');
assert.deepEqual(mod.letterCounts([byId('c1'), byId('c2')], 'doing'), {d: 2},
  'a pending blocked card counts as doing, not as attention');

// ---- nothing about the cards themselves was touched ----------------------------------------------

assert.equal(byId('c2').status, 'doing');
assert.equal(byId('c2').blocked_reason_code, 'AGENT_QUESTION', 'the reason code is still there to read');
assert.equal(byId('c2').review_flag, false);
assert.equal(byId('c3').status, 'todo', 'a queued todo card keeps its stored status');

// the foot itself is not readable here - renderCardStrip builds it with innerHTML, which the stub
// dom does not parse into nodes - so what the queue writes on it is covered in scheduler.mjs

// ---- stopping the queue puts every card back where it belongs ------------------------------------

mod.applyScheduleToCards({running: [], queued: [], waiting: {}, paused_until: null});
await flush();

assert.equal(columnOf('c2'), 'attention', 'the blocked card returns to attention');
assert.ok(strip('c2').className.includes('card-attention'), 'wearing its own colour again');
assert.equal(columnOf('c3'), 'todo', 'and the todo card returns to todo');
assert.equal(columnOf('c1'), 'doing', 'a card that was running is still in doing');
assert.equal(headerCount('attention'), '2', 'the attention count picks the blocked card back up');
assert.equal(byId('c2').status, 'doing', 'still nothing stored changed');

// a waiting entry the queue does not list has no place in it, so it is not pending
mod.applyScheduleToCards({running: [], queued: [], waiting: {c5: 'odd'}, paused_until: null});
await flush();
assert.ok(!mod.isPendingCard('c5'), 'waiting without a place in the queue is not pending');
assert.equal(columnOf('c5'), 'todo');

// ---- the pure helpers, straight ------------------------------------------------------------------

mod.applyScheduleToCards({running: [], queued: ['c2'], waiting: {}, paused_until: null});
assert.ok(mod.isPendingCard(byId('c2')) && mod.isPendingCard('c2'), 'a card or a bare id both answer');
assert.equal(mod.columnFor(byId('c2')), 'doing', 'a queued blocked card renders in its own column, not attention');
assert.equal(mod.columnFor(byId('c4')), 'attention', 'one the queue has not taken still renders in attention');
assert.deepEqual(mod.sortColumnCards([byId('c6'), byId('c2'), byId('c1')]).map(c => c.id), ['c1', 'c2', 'c6'],
  'running, then pending, then the card the board is retrying');
mod.applyScheduleToCards({running: [], queued: [], waiting: {}, paused_until: null});
assert.equal(mod.columnFor(byId('c2')), 'attention', 'and the queue emptying undoes all of it');
await flush();

console.log('ok');
