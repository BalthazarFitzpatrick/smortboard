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
;return {sortColumnCards, computePileLayout, letterCounts, renderBucketColumn, handlePileKey, MIN_PILED_CARDS,
  computePileFit, fitPiledColumn, MIN_PILE_HEIGHT, MIN_COVERED_VISIBLE};`)(SpyMenu, SpyDrawer);

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
  // the board retrying a card itself is not a card waiting on fabian - it counts under its own
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

// ---- pile fit: piles take the measured leftover, full cards overlap only when they must ---------

function fitTotal(rows, fit, gap) {
  return rows.reduce((sum, r, i) => sum + (r.type === 'pile' ? fit.pileHeight : r.height - fit.cuts[i]), 0)
    + (rows.length - 1) * gap;
}

{
  // roomy: short cards leave plenty over - the pile takes it, no card is cut
  const c = {type: 'card', height: 30};
  const rows = [c, c, {type: 'pile', height: 0}, c, c];
  const fit = mod.computePileFit(rows, 400, 10);
  assert.ok(fit.pileHeight >= mod.MIN_PILE_HEIGHT);
  assert.ok(fit.cuts.every(cut => cut === 0));
  assert.ok(fitTotal(rows, fit, 10) <= 400);
}

{
  // the measured browser case: 307px cards against 886px - at rest, drawn down, drawn up. a
  // focused card sandwiched between two piles (mid-excursion) is never a stacking victim at all,
  // so it is never cut; a focused card that IS the earlier half of a full-full pair still is -
  // focus only changes size/saturation (css), never which card the stack shows on top
  const c = (focused = false) => ({type: 'card', height: 307, focused});
  const p = {type: 'pile', height: 0};
  const layouts = [[c(true), c(), p, c(), c()], [p, c(), c(true), p, c(), c()], [c(), c(), p, c(true), c(), p]];
  for (const rows of layouts) {
    const fit = mod.computePileFit(rows, 886, 10);
    assert.ok(fit.pileHeight >= mod.MIN_PILE_HEIGHT, 'every pile keeps room for its count');
    assert.ok(fitTotal(rows, fit, 10) <= 886, 'the column fits the measured height');
    rows.forEach((r, i) => {
      const isPairEarlier = r.type === 'card' && rows[i + 1]?.type === 'card';
      if (r.focused && !isPairEarlier) assert.equal(fit.cuts[i], 0, 'a focused card with no later sibling to lose to stays whole');
      if (fit.cuts[i]) assert.ok(r.height - fit.cuts[i] >= mod.MIN_COVERED_VISIBLE, 'a cut card keeps its title band');
    });
  }
  // the earlier half of a pair is always the one cut, whether or not it holds focus
  assert.ok(mod.computePileFit(layouts[0], 886, 10).cuts[0] > 0, 'a focused top card is still the one that gives way to the card after it');
  assert.ok(mod.computePileFit(layouts[2], 886, 10).cuts[3] > 0, 'a focused bottom-pair upper card is still the one that gives way');
}

{
  // in the dom: stub cards measure 30px, gap falls back to 18 - pile height comes from the leftover
  const cards = Array.from({length: 10}, (_, i) => card(`f${i}`, 'todo'));
  const {bucketRows} = buildColumn(400, cards);
  const pile = piles(bucketRows)[0];
  const pileHeight = parseFloat(pile.style.height);
  assert.ok(pileHeight > 0, 'the pile gets a positive height from the leftover');
  assert.ok(fullCards(bucketRows).every(s => !s.className.includes('fan-item')), 'piled full cards drop the fan overlap');
  assert.ok(fullCards(bucketRows).length * 30 + pileHeight + 4 * 18 <= 400, 'rows fit the available height');

  // re-measured tall: the pile holds its minimum and cards away from focus are cut short instead
  fullCards(bucketRows).forEach(s => { s.getBoundingClientRect = () => ({top: 0, left: 0, right: 0, bottom: 0, width: 100, height: 307}); });
  mod.fitPiledColumn(bucketRows);
  assert.equal(parseFloat(pile.style.height), mod.MIN_PILE_HEIGHT);
  // real overlap: every card keeps its full 307px box (a shorter one reflows its foot and button
  // up into view); the row after it slides up over it, and whatever that row cannot cover - a pile
  // is shorter than the part it covers - is clipped away rather than left poking out beneath
  const gap = 18; // the stub's computed rowGap is empty, so fitPiledColumn falls back to 18
  const clipOf = s => Number((/inset\(0 0 (\d+)px 0\)/.exec(s.style.clipPath || '') || [0, 0])[1]);
  const covered = fullCards(bucketRows).filter(s => s.className.includes('card-covered'));
  assert.ok(covered.length, 'a card per full pair is tucked under the row after it');
  covered.forEach(s => {
    assert.ok(!s.style.height, 'a covered card keeps its full box - it is clipped, never shrunk');
    const next = bucketRows.children[bucketRows.children.indexOf(s) + 1];
    const nextTop = 307 + gap + parseFloat(next.style.marginTop);
    const visibleBottom = 307 - clipOf(s);
    const opaque = next.className.includes('card-pile') ? parseFloat(next.style.height) - 9 : 307;
    assert.ok(nextTop < visibleBottom, 'the row after a covered card really overlaps it');
    assert.ok(visibleBottom <= nextTop + opaque, 'and covers all of what shows - nothing pokes out beneath');
  });
  // a victim is always the earlier half of a card/card pair, so its "next" is always the later
  // card of that same pair, never the pile beside it - the pile never covers a full card directly
  assert.ok(covered.every(s => !bucketRows.children[bucketRows.children.indexOf(s) + 1].className.includes('card-pile')),
    'the pile itself never ends up covering a full card - only a later full card does');
  // the first card is the earlier half of the top pair, so it gives way too, focus or not - the
  // card after it (later, never the earlier) is the one that stays on top of the stack
  assert.ok(fullCards(bucketRows)[0].className.includes('card-covered'), 'with no excursion yet, the first card is still the one covered');
  assert.ok(!fullCards(bucketRows).at(-1).className.includes('card-covered') && !fullCards(bucketRows).at(-1).className.includes('card-clipped'),
    'the last card - always the later half of its pair - never gives way');

  // focus on the bottom pair's upper card changes nothing about who gives way: the earlier card of
  // a pair is always the victim, so the layout here is identical to the unfocused case above
  bucketRows._pile.focusIndex = 8;
  mod.fitPiledColumn(bucketRows);
  const last = fullCards(bucketRows).at(-1);
  assert.ok(!last.className.includes('card-clipped'), 'the later card of a pair is never the one that gives way, focused or not');
  assert.equal(last.style.marginTop, '-247px', 'the later card still slides up over the earlier (focused) one');
  const secondToLast = fullCards(bucketRows).at(-2);
  assert.ok(secondToLast.className.includes('card-covered'),
    'the earlier (now-focused) card of the bottom pair still gives way to the one after it');
  assert.equal(pile.style.marginTop, '', 'a refit clears the overlap a previous fit left behind');
}

// ---- pile style: straight layers, and the edge of the state the pile holds ----------------------

{
  const cards = Array.from({length: 10}, (_, i) => card(`s${i}`, 'doing'));
  const {bucketRows} = buildColumn(200, cards, 'doing');
  const pile = piles(bucketRows)[0];
  assert.ok(pile.className.includes('card-pile-doing'), 'a doing pile wears the doing edge');
  const layers = pile.children.filter(c => c.className.includes('card-pile-layer'));
  assert.ok(layers.length && layers.every(l => !l.style.transform.includes('rotate')),
    'layers stack straight - a rotated corner rose over the face');
}
{
  // attention sorts first, so three of them put one past the top pair and into the pile
  const cards = Array.from({length: 10}, (_, i) => card(`p${i}`, 'todo', i < 3 ? {blocked_reason_code: 'CRASH'} : {}));
  const {bucketRows} = buildColumn(200, cards);
  assert.ok(piles(bucketRows)[0].className.includes('card-pile-attention'), 'a pile holding an attention card wears its edge');
}
{
  const cards = Array.from({length: 10}, (_, i) => card(`q${i}`, 'todo'));
  const {bucketRows} = buildColumn(200, cards);
  const cls = piles(bucketRows)[0].className;
  assert.ok(!cls.includes('card-pile-doing') && !cls.includes('card-pile-attention'), 'a quiet pile keeps the grey edge');
}

console.log('ok');
