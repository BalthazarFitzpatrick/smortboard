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
;return {sortColumnCards, computePileLayout, computeStackCounts, letterCounts, renderBucketColumn,
  handlePileKey, MIN_PILED_CARDS, computePileFit, fitPiledColumn, squareCard, PILE, PEEK, MIN_CARD,
  PORTRAIT_BELOW, pileLayerJitter, cardEdgeVar, MAX_PILE_LAYERS};`)(SpyMenu, SpyDrawer);

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

// ---- within doing: a card a live agent actually holds ranks above one the board only retries
// on a timer (9bd5a207) ----------------------------------------------------------------------

{
  const held = card('h1', 'doing', {blocked_reason_code: 'API_UNREACHABLE', handled_by_board: true});
  const live1 = card('l1', 'doing');
  const live2 = card('l2', 'doing');
  const sorted = mod.sortColumnCards([held, live1, live2]);
  assert.deepEqual(sorted.map(c => c.id), ['l1', 'l2', 'h1'],
    'a card a live agent holds sorts above one the board is only retrying automatically');
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
  const {bucketRows} = buildColumn(836, cards);
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
  const {bucketRows} = buildColumn(836, cards);
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
  const {bucketRows} = buildColumn(836, cards);
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
  const {bucketRows} = buildColumn(836, cards);
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
  // the board retrying a card itself is not a card waiting on operator - it counts under its own
  // status, same as cardClasses already draws it (card-working, not card-attention)
  const cards = [card(1, 'doing'), card(2, 'doing', {blocked_reason_code: 'API_UNREACHABLE', handled_by_board: true})];
  const counts = mod.letterCounts(cards, 'doing');
  assert.deepEqual(counts, {d: 2}, 'a self-handled card does not inflate the attention count');
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
  const {bucketEl, bucketRows} = buildColumn(836, cards);
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
  const {bucketRows} = buildColumn(836, cards);
  const pile = piles(bucketRows)[0];
  pile._listeners.click[0]();
  assert.equal(piles(bucketRows).length, 0, 'clicking the pile also opens the column');
  assert.equal(fullCards(bucketRows).length, 10);
}

// ---- square cards: a card is as tall as the column is wide, portrait below PORTRAIT_BELOW -------

{
  assert.equal(mod.squareCard(500), 500, 'wide enough - a plain square');
  assert.equal(mod.squareCard(230), 230, 'exactly at the portrait line - still square');
  assert.equal(mod.squareCard(180), mod.PORTRAIT_BELOW, 'narrower than 230 - keeps 230px of height, turns portrait');
}

// ---- computePileFit: the pile is fixed at 96px and only ever grows with the leftover -------------

{
  // roomy: a wide column leaves plenty over past the full-size cards - the pile takes all of it
  const rows = [{type: 'card'}, {type: 'card'}, {type: 'pile'}, {type: 'card'}, {type: 'card'}];
  const fit = mod.computePileFit(rows, 2000, 10, 300);
  assert.equal(fit.card, 300, 'cards stay full square size - plenty of room');
  assert.ok(fit.pileHeight >= mod.PILE, 'the pile never shrinks below its own floor');
  assert.equal(fit.scrolls, false);
}

{
  // tight but each full-height card (plus its peek neighbours) still fits - pile sits at its floor
  const rows = [{type: 'card'}, {type: 'card'}, {type: 'pile'}, {type: 'card'}, {type: 'card'}];
  const fit = mod.computePileFit(rows, 836, 10, 300);
  assert.equal(fit.card, 300, 'still full square size');
  assert.equal(fit.pileHeight, mod.PILE, 'nothing left over past the cards - the pile sits at its 96px floor');
}

{
  // shrink-then-scroll: not even one card per stack fits at square size - cards shrink to the
  // 112px floor (PILE + 16), and the column is still too short even then, so it scrolls
  const rows = [{type: 'card'}, {type: 'pile'}, {type: 'card'}];
  const fit = mod.computePileFit(rows, 200, 10, 300);
  assert.equal(fit.card, mod.MIN_CARD, 'cards shrink to the pile-plus-16 floor');
  assert.equal(fit.scrolls, true, 'still too short even at the floor - the column scrolls');
  assert.equal(fit.pileHeight, mod.PILE, 'a scrolling pile keeps its plain floor - no leftover to share');
}

{
  // just enough room for the shrunk floor - cards shrink but the column does not need to scroll
  const rows = [{type: 'card'}, {type: 'pile'}, {type: 'card'}];
  const fit = mod.computePileFit(rows, 2 * mod.MIN_CARD + mod.PILE + 20, 10, 300);
  assert.equal(fit.card, mod.MIN_CARD);
  assert.equal(fit.scrolls, false, 'the shrunk floor is enough - no scroll needed');
}

// ---- computeStackCounts: 1 card per stack minimum, round-robin growth, front stack first ---------

{
  assert.deepEqual(mod.computeStackCounts(10, 200, 300, 10), [1, 1], 'not even 1 card per stack fits - both stay at their floor of 1');
  assert.deepEqual(mod.computeStackCounts(10, 836, 300, 18), [2, 2], 'room for 2 apiece, not 3 - round-robin lands even');
  const [front, back] = mod.computeStackCounts(10, 836, 300, 18);
  assert.ok(Math.abs(front - back) <= 1, 'the two stacks never differ by more than one card');
}

// ---- in the dom: a narrow column turns portrait, and no stray scrollbar appears unless it must ---

{
  // exactly MIN_PILED_CARDS, plenty of room, but a 180px-wide column - the card stays portrait
  // (230px tall) rather than shrinking down to the column's own narrow width
  const cards = Array.from({length: 5}, (_, i) => card(`f${i}`, 'todo'));
  const bucketEl = element('div', 'bucket');
  bucketEl.dataset.status = 'todo';
  const label = element('div', 'bucket-label');
  const bucketRows = element('div', 'bucket-rows');
  bucketRows.getBoundingClientRect = () => ({top: 800 - 24 - 1000, left: 0, right: 0, bottom: 0, width: 180, height: 0});
  bucketEl.appendChild(label);
  bucketEl.appendChild(bucketRows);
  mod.renderBucketColumn(bucketEl, cards, 'todo');
  const pile = piles(bucketRows)[0];
  assert.ok(parseFloat(pile.style.height) >= mod.PILE, 'the pile keeps its floor');
  const full = fullCards(bucketRows);
  assert.ok(full.length, 'at least one card shows full');
  full.forEach(s => assert.equal(parseFloat(s.style.height), mod.PORTRAIT_BELOW, 'a 180px-wide column keeps a portrait 230px card, not a squashed square'));
  assert.ok(fullCards(bucketRows).every(s => !s.className.includes('fan-item')), 'piled full cards drop the fan overlap');
  // the covered card in a stack shows only its title-strip peek, overlapped by the one after it
  const covered = full.filter(s => s.className.includes('card-covered'));
  assert.ok(covered.length, 'a stack of more than one card covers its earlier member');
  covered.forEach(s => {
    const cardHeight = parseFloat(s.style.height);
    const next = bucketRows.children[bucketRows.children.indexOf(s) + 1];
    assert.ok(next.className.includes('card-strip'), 'the covered card is always followed by the one covering it');
    assert.equal(parseFloat(next.style.marginTop), -(cardHeight - mod.PEEK), 'the later card slides up, leaving only PEEK px of the earlier one showing');
  });
  assert.ok(!bucketRows.className.includes('bucket-rows-scrolls'), 'room enough at the portrait floor - no scrollbar needed');
}

{
  // a column with real cards, real width, but no vertical room at all - the only case that scrolls
  const cards = Array.from({length: 10}, (_, i) => card(`g${i}`, 'todo'));
  const bucketEl = element('div', 'bucket');
  bucketEl.dataset.status = 'todo';
  const label = element('div', 'bucket-label');
  const bucketRows = element('div', 'bucket-rows');
  bucketRows.getBoundingClientRect = () => ({top: 800 - 24 - 150, left: 0, right: 0, bottom: 0, width: 300, height: 0});
  bucketEl.appendChild(label);
  bucketEl.appendChild(bucketRows);
  mod.renderBucketColumn(bucketEl, cards, 'todo');
  assert.ok(bucketRows.className.includes('bucket-rows-scrolls'), 'too short even at the card floor - the column scrolls');
  const pile = piles(bucketRows)[0];
  assert.equal(parseFloat(pile.style.height), mod.PILE, 'a scrolling pile still keeps its plain 96px floor, no more');
}

// ---- pile style: a desk pile, jittered per card id, each layer wearing its own state edge -------

{
  const cards = Array.from({length: 10}, (_, i) => card(`s${i}`, 'doing'));
  const {bucketRows} = buildColumn(836, cards, 'doing');
  const pile = piles(bucketRows)[0];
  assert.ok(pile.className.includes('card-pile-doing'), 'a doing pile wears the doing edge');
  const layers = pile.children.filter(c => c.className.includes('card-pile-layer'));
  assert.ok(layers.length && layers.every(l => /rotate\(-?\d/.test(l.style.transform)),
    'every layer carries its own jittered rotation - the desk-pile look');
}
{
  // attention sorts first, so three of them put one past the top pair and into the pile
  const cards = Array.from({length: 10}, (_, i) => card(`p${i}`, 'todo', i < 3 ? {blocked_reason_code: 'CRASH'} : {}));
  const {bucketRows} = buildColumn(836, cards);
  assert.ok(piles(bucketRows)[0].className.includes('card-pile-attention'), 'a pile holding an attention card wears its edge');
}
{
  const cards = Array.from({length: 10}, (_, i) => card(`q${i}`, 'todo'));
  const {bucketRows} = buildColumn(836, cards);
  const cls = piles(bucketRows)[0].className;
  assert.ok(!cls.includes('card-pile-doing') && !cls.includes('card-pile-attention'), 'a quiet pile keeps the grey edge');
}

// ---- pile jitter: seeded by card id alone, so the same ids always draw the same pile ------------

{
  const a = mod.pileLayerJitter('card-42');
  const b = mod.pileLayerJitter('card-42');
  assert.deepEqual(a, b, 'the same card id gives the same offset and rotation every time');
  const c = mod.pileLayerJitter('card-43');
  assert.notDeepEqual(a, c, 'a different id gives a different jitter (sanity - not a hash collision)');
  assert.ok(a.dx >= -8 && a.dx <= 8, 'dx stays within +-8px');
  assert.ok(a.dy >= -10 && a.dy <= 10, 'dy stays within +-10px, clamped to the row gap it may protrude into');
  assert.ok(Math.abs(a.rot) >= 0.6 && Math.abs(a.rot) <= 2.2, 'rotation magnitude stays within 0.6-2.2deg');
}

{
  // every card's edge colour matches cardClasses' own precedence for a full card
  assert.equal(mod.cardEdgeVar(card('w', 'doing')), 'var(--fill-good)');
  assert.equal(mod.cardEdgeVar(card('h', 'doing', {handled_by_board: true})), 'var(--fill-good)');
  assert.equal(mod.cardEdgeVar(card('a', 'todo', {blocked_reason_code: 'CRASH'})), 'var(--fill-attention)');
  assert.equal(mod.cardEdgeVar(card('r', 'rejected')), 'var(--fill-warn)');
  assert.equal(mod.cardEdgeVar(card('v', 'accepted')), 'var(--status-good)');
  assert.equal(mod.cardEdgeVar(card('t', 'todo')), 'var(--grey-border)');
}

{
  // up to MAX_PILE_LAYERS drawn, bottom first: the first (lowest-index) card ends up painted last
  // (closest to the face), the way a real desk pile is built up one card at a time
  const cards = Array.from({length: 20}, (_, i) => card(`z${i}`, 'todo'));
  const {bucketRows} = buildColumn(836, cards);
  const pile = piles(bucketRows)[0];
  const layers = pile.children.filter(c => c.className.includes('card-pile-layer'));
  assert.equal(layers.length, mod.MAX_PILE_LAYERS, 'never more than MAX_PILE_LAYERS drawn edges');
  const expectedTop = mod.pileLayerJitter(pile._cards[0].id);
  const topTransform = layers.at(-1).style.transform;
  assert.ok(topTransform.includes(expectedTop.dx.toFixed(1)), 'the first (bottom) card is the last layer appended - painted on top, under the face');
}

console.log('ok');
