// dense column presentation: sort order, the rest layout (2 full + pile + 2 full), drawing a card
// off the pile in each direction, reversing an excursion, header counts/letters, and expand via
// the button or a pile click. run: node tests/js/dense_columns.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => readFileSync(new URL(`../smortui/ui_base/assets/${p}`, root), 'utf8');
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

installStubDom({fetchImpl: () => new Promise(() => {})});
const boardBar = element('div', 'board-bar');
boardBar.id = 'board-bar';
const bucketRow = element('div', 'bucket-row');
bucketRow.id = 'bucket-row';
document.body.appendChild(boardBar);
document.body.appendChild(bucketRow);

class SpyMenu { constructor(opts) { this.opts = opts; } openAt() { return this; } close() {} }
function SpyDrawer() { return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false}; }

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {sortColumnCards, computePileLayout, letterCounts, renderBucketColumn, handlePileKey, MIN_PILED_CARDS};`)(SpyMenu, SpyDrawer);

function card(id, status, extra = {}) {
  return {id, title: `card ${id}`, status, workstream: '', ...extra};
}

// ---- sortColumnCards: doing first, then attention, then the rest - stable within each group -----

{
  const t1 = card('t1', 'todo');
  const d1 = card('d1', 'doing');
  const a1 = card('a1', 'todo', {review_flag: true});
  const d2 = card('d2', 'doing');
  const t2 = card('t2', 'todo');
  const a2 = card('a2', 'todo', {blocked_reason_code: 'CRASH'});
  const sorted = mod.sortColumnCards([t1, d1, a1, d2, t2, a2]);
  assert.deepEqual(sorted.map(c => c.id), ['d1', 'd2', 'a1', 'a2', 't1', 't2'],
    'doing group first, then attention, then the rest, each group in its original order');
}

// ---- buildColumn: a bucket element (label + rows), the height buildColumn's caller wants --------

function buildColumn(availableHeight, cards, status = 'todo') {
  const bucketEl = element('div', 'bucket');
  bucketEl.dataset.status = status;
  const label = element('div', 'bucket-label');
  const bucketRows = element('div', 'bucket-rows');
  bucketRows.getBoundingClientRect = () => ({top: 800 - 24 - availableHeight, left: 0, right: 0, bottom: 0, width: 300, height: 0});
  bucketEl.appendChild(label);
  bucketEl.appendChild(bucketRows);
  mod.renderBucketColumn(bucketEl, cards, status);
  return {bucketEl, bucketRows: bucketEl.querySelector('.bucket-rows')};
}

function fullCards(bucketRows) {
  return bucketRows.children.filter(c => c.className.includes('card-strip'));
}
function piles(bucketRows) {
  return bucketRows.children.filter(c => c.className.includes('card-pile'));
}

// ---- a column that fits renders every card full, no pile at all ---------------------------------

{
  const cards = [card(1, 'todo'), card(2, 'todo')];
  const {bucketRows} = buildColumn(2000, cards);
  assert.equal(fullCards(bucketRows).length, 2);
  assert.equal(piles(bucketRows).length, 0);
}

// ---- an overflowing column at rest: 2 full, one pile holding the rest, 2 full -------------------

{
  const cards = Array.from({length: 10}, (_, i) => card(`c${i}`, 'todo'));
  const {bucketRows} = buildColumn(200, cards);
  const rows = bucketRows.children;
  assert.equal(rows.length, 5, '2 full + 1 pile + 2 full');
  assert.equal(fullCards(bucketRows).length, 4, 'at most 4 full cards visible at rest');
  const pile = piles(bucketRows)[0];
  assert.equal(pile._cards.length, 6, 'the pile holds everything not in the fixed first/last two');
  assert.deepEqual(fullCards(bucketRows).slice(0, 2).map(r => r.dataset.cardId), ['c0', 'c1']);
  assert.deepEqual(fullCards(bucketRows).slice(2).map(r => r.dataset.cardId), ['c8', 'c9']);
}

// ---- drawing down: the pile shrinks, a top pile grows, 4 full cards throughout -------------------

{
  const cards = Array.from({length: 12}, (_, i) => card(`c${i}`, 'todo'));
  const {bucketRows} = buildColumn(200, cards);
  const dispatch = (target, key) => {
    const evt = {key, target, preventDefault() {}, stopPropagation() {}};
    bucketRows._listeners.keydown[0](evt);
    return evt;
  };
  let focused = fullCards(bucketRows)[0]; // c0
  for (let i = 0; i < 5; i++) {
    dispatch(focused, 'ArrowDown');
    focused = bucketRows.children.find(r => r.focused);
  }
  // after 5 presses from c0, focus sits on c5 (idx 5)
  assert.equal(focused.dataset.cardId, 'c5');
  assert.equal(fullCards(bucketRows).length, 4, 'never more than 4 full cards');
  assert.deepEqual(fullCards(bucketRows).map(r => r.dataset.cardId), ['c4', 'c5', 'c10', 'c11'],
    'card above focus, the focused card, and the fixed last two');
  const pileCards = piles(bucketRows).map(p => p._cards.map(c => c.id));
  assert.deepEqual(pileCards[0], ['c0', 'c1', 'c2', 'c3'], 'passed cards collect into a pile at the top');
  assert.deepEqual(pileCards[1], ['c6', 'c7', 'c8', 'c9'], 'the middle pile shrinks as focus moves down');
}

// ---- reverse travel: moving back up returns cards from the top pile to the middle pile -----------

{
  const cards = Array.from({length: 12}, (_, i) => card(`c${i}`, 'todo'));
  const {bucketRows} = buildColumn(200, cards);
  const dispatch = (target, key) => bucketRows._listeners.keydown[0]({key, target, preventDefault() {}, stopPropagation() {}});
  let focused = fullCards(bucketRows)[0];
  for (let i = 0; i < 5; i++) { dispatch(focused, 'ArrowDown'); focused = bucketRows.children.find(r => r.focused); }
  for (let i = 0; i < 3; i++) { dispatch(focused, 'ArrowUp'); focused = bucketRows.children.find(r => r.focused); }
  assert.equal(focused.dataset.cardId, 'c2', 'three presses back up from c5 lands on c2');
  assert.deepEqual(fullCards(bucketRows).map(r => r.dataset.cardId), ['c1', 'c2', 'c10', 'c11']);
  const pileCards = piles(bucketRows).map(p => p._cards.map(c => c.id));
  assert.deepEqual(pileCards[0], ['c0'], 'the top pile shrank back down as focus returned toward it');
}

// ---- travelling up from the bottom mirrors down: bottom pile grows, top two stay fixed -----------

{
  const cards = Array.from({length: 12}, (_, i) => card(`c${i}`, 'todo'));
  const {bucketRows} = buildColumn(200, cards);
  const dispatch = (target, key) => bucketRows._listeners.keydown[0]({key, target, preventDefault() {}, stopPropagation() {}});
  let focused = fullCards(bucketRows)[3]; // c11, the last fixed card
  for (let i = 0; i < 5; i++) { dispatch(focused, 'ArrowUp'); focused = bucketRows.children.find(r => r.focused); }
  assert.equal(focused.dataset.cardId, 'c6');
  assert.deepEqual(fullCards(bucketRows).map(r => r.dataset.cardId), ['c0', 'c1', 'c6', 'c7'],
    'fixed first two, focused card and the card below it');
  const pileCards = piles(bucketRows).map(p => p._cards.map(c => c.id));
  assert.deepEqual(pileCards[0], ['c2', 'c3', 'c4', 'c5'], 'the middle pile shrinks from the bottom side');
  assert.deepEqual(pileCards[1], ['c8', 'c9', 'c10', 'c11'], 'passed cards collect into a pile at the bottom');
}

// ---- header counts: one state present is a bare number, several is letter:count in state colors -

{
  const cards = [card(1, 'todo'), card(2, 'todo'), card(3, 'todo')];
  const counts = mod.letterCounts(cards, 'todo');
  assert.deepEqual(counts, {t: 3});
}
{
  const cards = [card(1, 'doing'), card(2, 'doing'), card(3, 'doing', {review_flag: true})];
  const counts = mod.letterCounts(cards, 'doing');
  assert.deepEqual(counts, {d: 2, a: 1}, 'a blocked/flagged doing card counts as attention, not doing');
}

{
  const cards = [card(1, 'todo'), card(2, 'todo', {blocked_reason_code: 'CRASH'})];
  const {bucketEl} = buildColumn(2000, cards);
  const counts = bucketEl.querySelector('.bucket-counts');
  assert.ok(counts.querySelector('.count-t'), 'todo count present');
  assert.ok(counts.querySelector('.count-a'), 'attention count present');
}

// ---- expand: the button and a pile click both open the whole column, same button collapses it ---

{
  const cards = Array.from({length: 10}, (_, i) => card(`c${i}`, 'todo'));
  const {bucketEl, bucketRows} = buildColumn(200, cards);
  assert.equal(piles(bucketRows).length, 1, 'starts piled');
  const expandBtn = bucketEl.querySelector('.bucket-expand');
  expandBtn._listeners.click[0]();
  assert.equal(piles(bucketRows).length, 0, 'expand button opens the column');
  assert.equal(fullCards(bucketRows).length, 10, 'every card renders full');
  assert.equal(expandBtn.textContent, 'collapse');
  expandBtn._listeners.click[0]();
  assert.equal(piles(bucketRows).length, 1, 'the same button collapses back');
}

{
  const cards = Array.from({length: 10}, (_, i) => card(`c${i}`, 'todo'));
  const {bucketRows} = buildColumn(200, cards);
  const pile = piles(bucketRows)[0];
  pile._listeners.click[0]();
  assert.equal(piles(bucketRows).length, 0, 'clicking the pile also opens the column');
  assert.equal(fullCards(bucketRows).length, 10);
}

console.log('ok');
