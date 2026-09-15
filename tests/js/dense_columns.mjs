// dense column presentation: sort order, the rest layout (2 full + pile + 2 full), drawing a card
// off the pile in each direction, reversing an excursion, header counts/letters, and expand via
// the button or a pile click. run: node tests/js/dense_columns.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => readFileSync(new URL(`../smortui/ui_base/assets/${p}`, root), 'utf8');
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const {dispatchWindow} = installStubDom({fetchImpl: () => new Promise(() => {})});
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
  PORTRAIT_BELOW, pileLayerJitter, cardEdgeVar, MAX_PILE_LAYERS, shadowAlpha, shadowImage,
  shadowImageCache, refitColumn, PILE_REFIT_DEBOUNCE_MS, indicateCardFocus, applyCardShadows,
  PILE_GAP_ABOVE, PILE_GAP_BELOW, BUCKET_ROW_GAP};`)(SpyMenu, SpyDrawer);

// fixture heights below are derived from the module's own tuning constants, not typed pixel
// counts, so a future gap-tuning pass moves the fixtures with it instead of breaking them
const GAP = mod.BUCKET_ROW_GAP; // buildColumn's stub leaves rowGap unset, so this fallback applies

// mirrors computeStackCounts' own "used" formula for a [front, back] split
function stacksUsed(front, back, card, gap) {
  const fixed = mod.PILE + 2 * gap + mod.PILE_GAP_ABOVE + mod.PILE_GAP_BELOW;
  return (front - 1) * mod.PEEK + card + (back - 1) * mod.PEEK + card + fixed;
}

// mirrors computePileFit's own "used" formula for one pile flanked by two 2-card stacks
function pileFitUsed(card, gap) {
  const fixedGaps = 2 * gap + mod.PILE_GAP_ABOVE + mod.PILE_GAP_BELOW; // 2 groups + 1 pile - 1 = 2 gaps
  return 2 * card + 2 * mod.PEEK + mod.PILE + fixedGaps;
}

// the room a [2, 2] round-robin rest needs, plus one gap of headroom so it doesn't tip to [3, 2]
const REST_2X2_HEIGHT = stacksUsed(2, 2, 300, GAP) + GAP;
// exactly the room two 2-card stacks plus one pile need at full square size - zero left over
const PILE_FIT_NO_LEFTOVER = pileFitUsed(300, 10);
// room for every one of 12 cards peeked into a single continuous run, no pile at all
const ALL_PEEKED_12_HEIGHT = 11 * mod.PEEK + 300;
// the room a [3, 2] round-robin rest needs, plus one gap of headroom so it doesn't tip to [3, 3]/[4, 2]
const REST_3X2_HEIGHT = stacksUsed(3, 2, 300, GAP) + GAP;

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
// (12 cards, not 10: at 300px-wide cards, 10 fit entirely as one continuous peeked run cheaper
// than any pile - see the "no pile when it isn't needed" tests below - so this needs a column
// that genuinely can't show every card even that way)

{
  const cards = Array.from({length: 12}, (_, i) => card(`c${i}`, 'todo'));
  const {bucketRows} = buildColumn(REST_2X2_HEIGHT, cards);
  const rows = bucketRows.children;
  assert.equal(rows.length, 5, '2 full + 1 pile + 2 full');
  assert.equal(fullCards(bucketRows).length, 4, 'at most 4 full cards visible at rest');
  const pile = piles(bucketRows)[0];
  assert.equal(pile._cards.length, 8, 'the pile holds everything not in the fixed first/last two');
  assert.deepEqual(fullCards(bucketRows).slice(0, 2).map(r => r.dataset.cardId), ['c0', 'c1']);
  assert.deepEqual(fullCards(bucketRows).slice(2).map(r => r.dataset.cardId), ['c10', 'c11']);
}

// ---- drawing down: the pile shrinks, a top pile grows, 4 full cards throughout -------------------

{
  const cards = Array.from({length: 12}, (_, i) => card(`c${i}`, 'todo'));
  const {bucketRows} = buildColumn(REST_2X2_HEIGHT, cards);
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
  const {bucketRows} = buildColumn(REST_2X2_HEIGHT, cards);
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
  const {bucketRows} = buildColumn(REST_2X2_HEIGHT, cards);
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

// ---- a key press keeps the round-robin sizes, unless the focused card would drop out of them ----

{
  // 20 cards, roomy enough that computeStackCounts lands on [3, 2] at rest (not [1,1] or a fixed
  // [2,2]) - moving focus one step from idx 0 to idx 1 is still covered by front=3, so the layout
  // does not reset to a fixed [2, 2]
  const cards = Array.from({length: 20}, (_, i) => card(`r${i}`, 'todo'));
  const {bucketRows} = buildColumn(REST_3X2_HEIGHT, cards);
  assert.deepEqual(fullCards(bucketRows).map(r => r.dataset.cardId), ['r0', 'r1', 'r2', 'r18', 'r19'],
    'rests at the round-robin [3, 2] split, not a fixed [2, 2]');
  const dispatch = (target, key) => bucketRows._listeners.keydown[0]({key, target, preventDefault() {}, stopPropagation() {}});
  dispatch(fullCards(bucketRows)[0], 'ArrowDown');
  assert.equal(bucketRows.children.find(r => r.focused).dataset.cardId, 'r1');
  assert.deepEqual(fullCards(bucketRows).map(r => r.dataset.cardId), ['r0', 'r1', 'r2', 'r18', 'r19'],
    'idx 1 is still inside the 3-card front stack, so the round-robin sizes are kept, focused or not');
}

{
  // a genuinely tight column: computeStackCounts stalls at its own [1, 1] floor. idx 0 stays
  // renderable either way, but idx 1 would fall into the pile under [1, 1] - THIS is the one case
  // that still falls back to a fixed [2, 2], so the focused card is never left unrendered
  const cards = Array.from({length: 12}, (_, i) => card(`t${i}`, 'todo'));
  const {bucketRows} = buildColumn(600, cards);
  assert.deepEqual(fullCards(bucketRows).map(r => r.dataset.cardId), ['t0', 't11'],
    'rests at [1, 1] - too tight for anything more');
  const dispatch = (target, key) => bucketRows._listeners.keydown[0]({key, target, preventDefault() {}, stopPropagation() {}});
  dispatch(fullCards(bucketRows)[0], 'ArrowDown');
  const focused = bucketRows.children.find(r => r.focused);
  assert.equal(focused.dataset.cardId, 't1', 'the focused card is still rendered');
  assert.deepEqual(fullCards(bucketRows).map(r => r.dataset.cardId), ['t0', 't1', 't10', 't11'],
    'falls back to a fixed [2, 2] only because [1, 1] would have hidden the focused card');
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
  const {bucketEl, bucketRows} = buildColumn(600, cards);
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
  const {bucketRows} = buildColumn(600, cards);
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

// ---- card shadow: a pixel field ported from derived/shadow_tuner/index.html - darkest at a ------
// corner, generated once per size and cached, never per frame or per redraw

{
  // a corner (both dx and dy past the edge) reaches further and stays darker longer than a side -
  // that is the whole reason a plain css box-shadow (one radius, one spread) can't make this
  const corner = mod.shadowAlpha(-5, -5, 300, 300);
  const topSide = mod.shadowAlpha(150, -5, 300, 300); // same distance out, but mid-side, not a corner
  const bottomSide = mod.shadowAlpha(150, 305, 300, 300); // the bottom edge, on by default
  const inside = mod.shadowAlpha(150, 150, 300, 300); // inside the card - no shadow at all
  assert.ok(corner > topSide, 'a corner point is darker than a side point the same distance out');
  assert.ok(topSide > 0 && bottomSide > 0, 'both the top and the (default-on) bottom edge cast a shadow');
  assert.equal(inside, 0, 'nothing inside the card itself');
  // far along an edge, past the corner's creep, the side settles to its own (lower) strength
  const nearCorner = mod.shadowAlpha(5, -1, 300, 300);
  const farAlongTop = mod.shadowAlpha(150, -1, 300, 300);
  assert.ok(nearCorner >= farAlongTop, 'close to a corner is at least as dark as the middle of an edge');
}

{
  // generated once per size, cached by it - a second call for the same size returns the very same
  // object, not a freshly-drawn one, and shadowImageCache holds exactly one entry either way
  mod.shadowImageCache.clear();
  const a = mod.shadowImage(300, 300);
  const b = mod.shadowImage(300, 300);
  assert.equal(a, b, 'the same size returns the cached image, not a redraw');
  assert.equal(mod.shadowImageCache.size, 1);
  const c = mod.shadowImage(180, 230); // a portrait card's own size - a different cache entry
  assert.notEqual(a, c);
  assert.equal(mod.shadowImageCache.size, 2);
  assert.ok(a.imageData && c.imageData, 'each cached entry carries real pixel data');
}

// ---- computePileFit: the pile is fixed at PILE and only ever grows with the leftover -----------

{
  // roomy: a wide column leaves plenty over past the full-size cards - the pile takes all of it
  const rows = [{type: 'card'}, {type: 'card'}, {type: 'pile'}, {type: 'card'}, {type: 'card'}];
  const fit = mod.computePileFit(rows, 2000, 10, 300);
  assert.equal(fit.card, 300, 'cards stay full square size - plenty of room');
  assert.ok(fit.pileHeight >= mod.PILE, 'the pile never shrinks below its own floor');
  assert.equal(fit.scrolls, false);
}

{
  // tight but each full-height card (plus its peek neighbours) still fits, with nothing left over -
  // a real gap only sits between groups (2 of them here, around the pile), never inside a stack,
  // whose own cards overlap via negative margin-top instead
  const rows = [{type: 'card'}, {type: 'card'}, {type: 'pile'}, {type: 'card'}, {type: 'card'}];
  const fit = mod.computePileFit(rows, PILE_FIT_NO_LEFTOVER, 10, 300);
  assert.equal(fit.card, 300, 'still full square size');
  assert.equal(fit.pileHeight, mod.PILE, 'nothing left over past the cards - the pile sits at its own floor');
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
  // two single-card groups + one pile: fixedGaps is 2 * gap + the pile's own above/below knobs -
  // exactly enough room for two MIN_CARD cards plus the pile plus those fixed gaps, no more
  const fixedGaps = 2 * 10 + mod.PILE_GAP_ABOVE + mod.PILE_GAP_BELOW;
  const fit = mod.computePileFit(rows, 2 * mod.MIN_CARD + mod.PILE + fixedGaps, 10, 300);
  assert.equal(fit.card, mod.MIN_CARD);
  assert.equal(fit.scrolls, false, 'the shrunk floor is enough - no scroll needed');
}

// ---- a pile never grows past PILE + one PEEK - the rest stays empty, not in the pile ------------

{
  // wildly roomy - the old rule would have handed all of it to the pile; the new rule caps it
  const rows = [{type: 'card'}, {type: 'card'}, {type: 'pile'}, {type: 'card'}, {type: 'card'}];
  const fit = mod.computePileFit(rows, 3000, 10, 300);
  assert.equal(fit.pileHeight, mod.PILE + mod.PEEK, `the pile stops growing at ${mod.PILE} + ${mod.PEEK} = ${mod.PILE + mod.PEEK}px`);
}
{
  // several piles share the leftover, and each is capped the same way, independently
  const rows = [{type: 'pile'}, {type: 'card'}, {type: 'card'}, {type: 'pile'}, {type: 'card'}, {type: 'card'}];
  const fit = mod.computePileFit(rows, 3000, 10, 300);
  assert.equal(fit.pileHeight, mod.PILE + mod.PEEK);
}
{
  // a little leftover, less than one PEEK - the pile takes exactly that, same as before the cap
  const rows = [{type: 'card'}, {type: 'card'}, {type: 'pile'}, {type: 'card'}, {type: 'card'}];
  const fit = mod.computePileFit(rows, PILE_FIT_NO_LEFTOVER + 30, 10, 300);
  assert.equal(fit.pileHeight, mod.PILE + 30, 'under the PEEK cap, the pile still takes the whole leftover');
}

// ---- no pile when it isn't needed: if every card fits as one continuously peeked run, no pile ----

{
  // 12 cards, a 300px-wide column, and enough room for all 12 peeked (11 * PEEK + card) - not
  // a single card is hidden behind a pile
  assert.deepEqual(mod.computeStackCounts(12, ALL_PEEKED_12_HEIGHT, 300, 18), [12, 0], 'front takes every card, back and the pile are empty');
  const cards = Array.from({length: 12}, (_, i) => card(`n${i}`, 'todo'));
  const bucketEl = element('div', 'bucket');
  bucketEl.dataset.status = 'todo';
  const label = element('div', 'bucket-label');
  const bucketRows = element('div', 'bucket-rows');
  bucketRows.getBoundingClientRect = () => ({top: 800 - 24 - ALL_PEEKED_12_HEIGHT, left: 0, right: 0, bottom: 0, width: 300, height: 0});
  bucketEl.appendChild(label);
  bucketEl.appendChild(bucketRows);
  mod.renderBucketColumn(bucketEl, cards, 'todo');
  assert.equal(piles(bucketRows).length, 0, 'no pile row at all');
  assert.equal(fullCards(bucketRows).length, 12, 'every card renders, peeked into one running stack');
}
{
  // one card too many for the no-pile route - now a pile has to appear
  assert.notDeepEqual(mod.computeStackCounts(13, ALL_PEEKED_12_HEIGHT, 300, 18), [13, 0], 'one more card and the continuous run no longer fits');
  const [front, back] = mod.computeStackCounts(13, ALL_PEEKED_12_HEIGHT, 300, 18);
  assert.ok(front + back < 13, 'so a pile holds whatever is left over');
}

// ---- computeStackCounts: 1 card per stack minimum, round-robin growth, front stack first ---------

{
  assert.deepEqual(mod.computeStackCounts(12, 200, 300, 10), [1, 1], 'not even 1 card per stack fits - both stay at their floor of 1');
  assert.deepEqual(mod.computeStackCounts(12, REST_2X2_HEIGHT, 300, 18), [2, 2], 'room for 2 apiece, not 3 - round-robin lands even');
  const [front, back] = mod.computeStackCounts(12, REST_2X2_HEIGHT, 300, 18);
  assert.ok(Math.abs(front - back) <= 1, 'the two stacks never differ by more than one card');
}

// ---- in the dom: a narrow column turns portrait, and no stray scrollbar appears unless it must ---

{
  // enough cards, and enough room to need a pile (not so much room the no-pile rule kicks in), but
  // a 180px-wide column - the card stays portrait (230px tall) rather than shrinking to fit the
  // column's own narrow width
  const cards = Array.from({length: 12}, (_, i) => card(`f${i}`, 'todo'));
  const bucketEl = element('div', 'bucket');
  bucketEl.dataset.status = 'todo';
  const label = element('div', 'bucket-label');
  const bucketRows = element('div', 'bucket-rows');
  bucketRows.getBoundingClientRect = () => ({top: 800 - 24 - 700, left: 0, right: 0, bottom: 0, width: 180, height: 0});
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
    // the stub's computed rowGap is empty, so fitPiledColumn falls back to BUCKET_ROW_GAP (18) -
    // the margin has to cancel that real flex gap too, or the peek band comes out gap px too tall
    assert.equal(parseFloat(next.style.marginTop), -(cardHeight - mod.PEEK + 18),
      'the later card slides up over the gap AND the peek, leaving exactly PEEK px of the earlier one showing');
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
  assert.equal(parseFloat(pile.style.height), mod.PILE, 'a scrolling pile still keeps its plain floor, no more');
}

// ---- regression: a 35-card column really ends inside the window, not past its bottom -----------
// bucket-rows' own flex `gap` sits UNDER a covered card's negative margin-top, adding to it rather
// than being cancelled by it - so a stack of n peeked cards came out (n-1) real gaps too tall,
// however correctly computePileFit had sized the column. real offsets, measured from a live
// 1600px-wide six-column board (playwright, .bucket[data-status="todo"] .bucket-rows): rowsTop
// 89.8125px below the viewport top (board bar + column header), width 222.390625px, gap 10px -
// checked at both a tall window (1300, where a 35-card column piled and had room for two full
// stacks) and a short one (700, where it piles down to the fixed edges)

function replayPiledColumnBottom(rowKinds, card, pileHeight, gap, peek) {
  // mirrors the real css model exactly: normal flex flow (each row's top is the previous row's
  // bottom plus gap), then a covered card's own negative margin-top on top of that - never a
  // model that assumes the margin already accounts for the gap, which is the bug this guards
  let top = 0, bottom = 0, prevWasCard = false;
  rowKinds.forEach((kind, i) => {
    const height = kind === 'pile' ? pileHeight : card;
    if (i > 0) top = bottom + gap;
    if (i > 0 && kind === 'card' && prevWasCard) top += -(card - peek + gap);
    bottom = top + height;
    prevWasCard = kind === 'card';
  });
  return bottom;
}

for (const [innerHeight, rowsTop] of [[1300, 89.8125], [700, 89.8125]]) {
  const width = 222.390625, gap = 10;
  const cards = Array.from({length: 35}, (_, i) => card(`d${i}`, 'todo'));
  const bucketEl = element('div', 'bucket');
  bucketEl.dataset.status = 'todo';
  const label = element('div', 'bucket-label');
  const bucketRows = element('div', 'bucket-rows');
  bucketRows.getBoundingClientRect = () => ({top: rowsTop, left: 0, right: 0, bottom: 0, width, height: 0});
  bucketRows._realGap = gap;
  const realGetComputedStyle = globalThis.getComputedStyle;
  globalThis.getComputedStyle = el => (el === bucketRows ? {rowGap: `${gap}px`} : realGetComputedStyle(el));
  globalThis.window.innerHeight = innerHeight;
  bucketEl.appendChild(label);
  bucketEl.appendChild(bucketRows);
  mod.renderBucketColumn(bucketEl, cards, 'todo');
  globalThis.getComputedStyle = realGetComputedStyle;

  const rowKinds = Array.from(bucketRows.children).map(el => el.classList.contains('card-pile') ? 'pile' : 'card');
  const cardHeight = parseFloat(fullCards(bucketRows)[0].style.height);
  const pileHeight = parseFloat((piles(bucketRows)[0] || {style: {height: '0px'}}).style.height);
  const bottom = replayPiledColumnBottom(rowKinds, cardHeight, pileHeight, gap, mod.PEEK);
  const available = innerHeight - rowsTop - 24;
  assert.ok(bottom <= available + 0.5,
    `window ${innerHeight}: the column's real bottom (${bottom}px) must stay inside the ${available}px available, not past it`);
}
globalThis.window.innerHeight = 800;

// ---- pile style: a desk pile, jittered per card id, each layer wearing its own state edge -------

{
  const cards = Array.from({length: 10}, (_, i) => card(`s${i}`, 'doing'));
  const {bucketRows} = buildColumn(600, cards, 'doing');
  const pile = piles(bucketRows)[0];
  assert.ok(pile.className.includes('card-pile-doing'), 'a doing pile wears the doing edge');
  const layers = pile.children.filter(c => c.className.includes('card-pile-layer'));
  assert.ok(layers.length && layers.every(l => /rotate\(-?\d/.test(l.style.transform)),
    'every layer carries its own jittered rotation - the desk-pile look');
}
{
  // attention sorts first, so three of them put one past the top pair and into the pile
  const cards = Array.from({length: 10}, (_, i) => card(`p${i}`, 'todo', i < 3 ? {blocked_reason_code: 'CRASH'} : {}));
  const {bucketRows} = buildColumn(600, cards);
  assert.ok(piles(bucketRows)[0].className.includes('card-pile-attention'), 'a pile holding an attention card wears its edge');
}
{
  const cards = Array.from({length: 10}, (_, i) => card(`q${i}`, 'todo'));
  const {bucketRows} = buildColumn(600, cards);
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
  const {bucketRows} = buildColumn(REST_2X2_HEIGHT, cards);
  const pile = piles(bucketRows)[0];
  const layers = pile.children.filter(c => c.className.includes('card-pile-layer'));
  assert.equal(layers.length, mod.MAX_PILE_LAYERS, 'never more than MAX_PILE_LAYERS drawn edges');
  const expectedTop = mod.pileLayerJitter(pile._cards[0].id);
  const topTransform = layers.at(-1).style.transform;
  assert.ok(topTransform.includes(expectedTop.dx.toFixed(1)), 'the first (bottom) card is the last layer appended - painted on top, under the face');
}

// ---- resize refit: a window resize event redraws every piled column from its own state, picking
// up its new measured size, debounced - no full renderBuckets, no cards refetched -----------------

{
  const cards = Array.from({length: 12}, (_, i) => card(`w${i}`, 'todo'));
  const {bucketEl, bucketRows} = buildColumn(REST_2X2_HEIGHT, cards); // starts piled: 2 full + pile + 2 full
  bucketRow.appendChild(bucketEl); // refitPiledColumns finds columns under #bucket-row
  assert.equal(piles(bucketRows).length, 1, 'starts piled at the narrow/short size');
  const focused = fullCards(bucketRows)[0];
  focused.tabIndex = 0;
  focused.focus();

  // the window grows: the same column now has room for every card peeked, no pile needed
  bucketRows.getBoundingClientRect = () => ({top: 800 - 24 - 2000, left: 0, right: 0, bottom: 0, width: 300, height: 0});
  dispatchWindow('resize', {});
  assert.equal(piles(bucketRows).length, 1, 'debounced - nothing changes on the raw event itself');
  await new Promise(resolve => setTimeout(resolve, mod.PILE_REFIT_DEBOUNCE_MS + 60));

  assert.equal(piles(bucketRows).length, 0, 'the debounced refit drops the pile once the column has room for every card');
  assert.equal(fullCards(bucketRows).length, 12, 'every card renders full after the resize');
  const stillFocused = bucketRows.children.find(r => r.focused);
  assert.ok(stillFocused && stillFocused.dataset.cardId === 'w0', 'focus stays on the same card through the refit');
}

// ---- indicateCardFocus: a covered card's marker clips to its own visible peek band, never the
// full box that would otherwise run across the card covering it. a covering (or plain) card keeps
// its full frame -------------------------------------------------------------------------------

{
  const cards = Array.from({length: 12}, (_, i) => card(`m${i}`, 'todo'));
  const {bucketRows} = buildColumn(REST_2X2_HEIGHT, cards); // 2 full + pile + 2 full - the front pair overlaps
  const covered = fullCards(bucketRows).find(s => s.className.includes('card-covered'));
  const covering = bucketRows.children[bucketRows.children.indexOf(covered) + 1];
  assert.ok(covered && covering, 'a covered card and the one covering it both exist');

  // real geometry: the covering card's own top sits PEEK px below the covered card's top - only
  // that top band is what fitPiledColumn actually leaves visible
  covered.getBoundingClientRect = () => ({top: 100, left: 0, right: 0, bottom: 330, width: 230, height: 230});
  covering.getBoundingClientRect = () => ({top: 100 + mod.PEEK, left: 0, right: 0, bottom: 380, width: 230, height: 230});

  mod.indicateCardFocus(covered);
  const marker = document.querySelector('.focus-marker');
  assert.equal(marker.style.clipPath, `inset(0 0 calc(100% - ${mod.PEEK}px) 0)`,
    'a covered card clips the marker to its own peek band, not its full (unclipped) box');

  mod.indicateCardFocus(covering);
  assert.equal(marker.style.clipPath, '', 'the covering card keeps its full, unclipped frame');
}

// ---- applyCardShadows: an attention card's ring glow clears the shadow the next card casts over
// it - it must out-rank that one shadow, but never the next card itself, or the ring would draw
// over a neighbour / uncover a covered card, exactly what it must not do -------------------------

{
  const cards = Array.from({length: 12}, (_, i) => card(`z${i}`, 'todo'));
  const {bucketRows} = buildColumn(REST_2X2_HEIGHT, cards); // 2 full + pile + 2 full
  const strips = fullCards(bucketRows);
  const covered = strips.find(s => s.className.includes('card-covered'));
  const covering = bucketRows.children[bucketRows.children.indexOf(covered) + 1];
  covered.getBoundingClientRect = () => ({top: 100, left: 0, right: 0, bottom: 330, width: 230, height: 230});
  covering.getBoundingClientRect = () => ({top: 100 + mod.PEEK, left: 0, right: 0, bottom: 380, width: 230, height: 230});

  mod.applyCardShadows(bucketRows);
  const plainZ = Number(covered.style.zIndex);
  const coveringZ = Number(covering.style.zIndex);
  // the shade canvases are appended in row order, one per full card - covering's own is at its
  // same position among them, since piles never get one (applyCardShadows skips card-pile rows)
  const coveringShadeZ = Number(document.querySelectorAll('.card-shade')[strips.indexOf(covering)].style.zIndex);
  assert.ok(coveringShadeZ > plainZ, 'at rest, the covering card\'s shadow paints over the covered card');

  covered.className += ' card-attention';
  mod.applyCardShadows(bucketRows);
  const attentionZ = Number(covered.style.zIndex);
  assert.ok(attentionZ > coveringShadeZ, 'an attention ring clears the covering shadow');
  assert.ok(attentionZ < coveringZ, 'and never climbs above the card covering it');
}

console.log('ok');
