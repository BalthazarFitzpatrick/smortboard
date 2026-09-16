// dense column presentation: sort order, column motion option C (a group of at most n cards between
// two piles, one card per step, top/bottom anchoring), header counts/letters, and expand via the
// button or a pile click. run: node tests/js/dense_columns.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, stubMotion, stubLayout} from './dom_stub.mjs';

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
;return {sortColumnCards, computePileLayout, computeGroupFit, letterCounts, renderBucketColumn,
  handlePileKey, MIN_PILED_CARDS, fitPiledColumn, squareCard, PILE, PEEK, MIN_CARD,
  PORTRAIT_BELOW, pileLayerJitter, cardEdgeVar, MAX_PILE_LAYERS, shadowAlpha, shadowImage,
  shadowImageCache, refitColumn, PILE_REFIT_DEBOUNCE_MS, indicateCardFocus, applyCardShadows,
  PILE_GAP_ABOVE, PILE_GAP_BELOW, BUCKET_ROW_GAP, flipDelta, planPileMotion, PILE_MOTION_MS,
  PILE_SETTLE_MS, PILE_EASING, ROW_MOTION_MS, ROW_EASING, CLIP_REACH, PILE_FACE_INSET, pileMouth,
  mouthFrames, pileFace, foldFrames, PILE_HANDOVER_IN, PILE_HANDOVER_OUT,
  GROW_FRAME, REST_FRAME};`)(SpyMenu, SpyDrawer);

// fixture heights below are derived from the module's own tuning constants, not typed pixel
// counts, so a future gap-tuning pass moves the fixtures with it instead of breaking them
const GAP = mod.BUCKET_ROW_GAP; // buildColumn's stub leaves rowGap unset, so this fallback applies
const WIDE = 300; // buildColumn's stub width - square cards, WIDE px tall

// mirrors computeGroupFit: the tallest a group of n gets (the focused card open at its start, the
// last card still full below it) and what both piles cost around it
const groupMax = (n, cardPx) => (n <= 1 ? cardPx : 2 * cardPx + (n - 2) * mod.PEEK);
const pilesCost = gap => 2 * (mod.PILE + mod.PILE_GAP_ABOVE + mod.PILE_GAP_BELOW + gap);

// a short screen: exactly the room n=2 needs, one PEEK short of n=3
const SHORT_N2 = groupMax(2, WIDE) + pilesCost(GAP);
// a tall screen: exactly the room n=3 needs
const TALL_N3 = groupMax(3, WIDE) + pilesCost(GAP);
// room for 12 cards as one group, the open card included - no pile at all
const ALL_12_HEIGHT = groupMax(12, WIDE);

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

// the drawn rows only - never a ghost still sliding into (or shrinking off) a pile
const drawnRows = bucketRows => bucketRows.children.filter(c => !c.className.includes('row-ghost'));
function fullCards(bucketRows) {
  return drawnRows(bucketRows).filter(c => c.className.includes('card-strip'));
}
function piles(bucketRows) {
  return drawnRows(bucketRows).filter(c => c.className.includes('card-pile'));
}

// ---- a column that fits renders every card full, no pile at all ---------------------------------

{
  const cards = [card(1, 'todo'), card(2, 'todo')];
  const {bucketRows} = buildColumn(2000, cards);
  assert.equal(fullCards(bucketRows).length, 2);
  assert.equal(piles(bucketRows).length, 0);
}

// ---- column motion option C (derived/column_motion): helpers read the drawn rows back ------------

// presses a key on the focused row (or the first card, before anything has focus) and returns the
// row that has focus afterwards
function keyDriver(bucketRows) {
  return key => {
    const target = bucketRows.children.find(r => r.focused) || fullCards(bucketRows)[0];
    bucketRows._listeners.keydown[0]({key, target, preventDefault() {}, stopPropagation() {}});
    return bucketRows.children.find(r => r.focused);
  };
}

// card id -> 'above' / 'below' (in that pile), 'group' (the card run holding focus, or the group's
// start at rest) or 'edge' (the column's first or last cards past the far pile)
function roles(bucketRows) {
  const state = bucketRows._pile;
  const anchorIdx = String(state.focusIndex ?? state.start);
  const runs = [];
  bucketRows.children.forEach(el => {
    if (el.className.includes('card-pile')) { runs.push({pile: el}); return; }
    const last = runs.at(-1);
    if (last?.cards && el.dataset.join !== 'none') last.cards.push(el);
    else runs.push({cards: [el]});
  });
  const groupAt = runs.findIndex(r => r.cards?.some(el => el.dataset.idx === anchorIdx));
  const out = new Map();
  runs.forEach((r, i) => {
    if (r.pile) r.pile._cards.forEach(c => out.set(c.id, i < groupAt ? 'above' : 'below'));
    else r.cards.forEach(el => out.set(el.dataset.cardId, i === groupAt ? 'group' : 'edge'));
  });
  return out;
}

// mirrors the css model: normal flex flow (each row's top is the previous row's bottom plus the gap
// and the pile margins), a card's own margin-top on top of that - bucket-rows' flex gap sits UNDER
// a negative margin, never cancelled by it - and flex-end pinning the last row to the column's
// bottom edge when bottom-anchored
function replayRows(bucketRows, available, gap) {
  let bottom = 0, marginBelow = 0;
  const rows = drawnRows(bucketRows).map((el, i) => {
    const pile = el.className.includes('card-pile');
    const marginTop = pile ? mod.PILE_GAP_ABOVE : parseFloat(el.style.marginTop || '0');
    const top = (i ? bottom + marginBelow + gap : 0) + marginTop;
    bottom = top + parseFloat(el.style.height);
    marginBelow = pile ? mod.PILE_GAP_BELOW : 0;
    return {el, pile, top, bottom};
  });
  if (bucketRows.className.includes('bucket-rows-bottom')) {
    const shift = available - (bottom + marginBelow);
    rows.forEach(r => { r.top += shift; r.bottom += shift; });
  }
  return rows;
}

// both piles and the focused card always sit inside the column - only other cards get cut
function assertPilesInside(bucketRows, available, gap, where) {
  assert.equal(bucketRows.style.height, `${available}px`, 'the column is exactly the room it has');
  replayRows(bucketRows, available, gap).forEach(r => {
    if (!r.pile && !r.el.focused) return;
    assert.ok(r.top >= -0.5 && r.bottom <= available + 0.5,
      `${where}: ${r.pile ? 'a pile' : 'the focused card'} at ${r.top}..${r.bottom} must sit inside 0..${available}`);
  });
}

// ---- at rest: the group of n, then the lower pile, then the column's last two cards --------------

for (const [room, n] of [[SHORT_N2, 2], [TALL_N3, 3]]) {
  const cards = Array.from({length: 20}, (_, i) => card(`r${n}-${i}`, 'todo'));
  const {bucketRows} = buildColumn(room, cards);
  const r = roles(bucketRows);
  const ids = part => cards.filter(c => r.get(c.id) === part).map(c => c.id);
  assert.deepEqual(ids('group'), cards.slice(0, n).map(c => c.id), `room for n=${n}: the first ${n} cards form the group`);
  assert.deepEqual(ids('below'), cards.slice(n, 18).map(c => c.id), 'the lower pile holds everything up to the last two');
  assert.deepEqual(ids('edge'), cards.slice(18).map(c => c.id), 'the column\'s last two cards sit below the pile');
  assert.equal(ids('above').length, 0, 'no upper pile at rest');
  assert.ok(!bucketRows.className.includes('bucket-rows-bottom'), 'anchored to the top at rest');
}

// ---- n by column height: the largest n in {3, 2} whose tallest group plus both piles fits -------

{
  const fit = (total, room) => mod.computeGroupFit(total, room, GAP, WIDE);
  assert.equal(fit(20, TALL_N3).n, 3, 'a tall column holds a group of 3 between the piles');
  assert.equal(fit(20, TALL_N3 - 1).n, 2, 'one px short of that and the group drops to 2');
  assert.equal(fit(20, SHORT_N2).n, 2);
  assert.equal(fit(20, SHORT_N2 - 1).n, 1, 'only when even 2 cannot fit does it fall back to fewer');
  assert.equal(fit(100, 3 * TALL_N3).n, 3, 'n never grows past 3, however tall the column');
  const one = groupMax(1, WIDE) + pilesCost(GAP);
  assert.deepEqual(fit(20, one), {n: 1, card: WIDE, piles: true, scrolls: false}, 'n=1 keeps square cards');
  const shrunk = fit(20, one - 1);
  assert.ok(shrunk.card < WIDE && shrunk.card >= mod.MIN_CARD && !shrunk.scrolls,
    'past n=1, cards shrink toward MIN_CARD before anything scrolls');
  assert.equal(fit(20, 200).card, mod.MIN_CARD);
  assert.equal(fit(20, 200).scrolls, true, 'too short even at the card floor - the only case that scrolls');
}

// ---- one card per step, both directions: the step past the group's end draws ONE card off the
// lower pile and puts the group's oldest onto the upper pile (the old draw-out model moved three
// at its hardcoded idx-2 threshold). piles and the focused card stay inside the column throughout --

for (const room of [SHORT_N2, TALL_N3]) {
  const total = 14;
  const cards = Array.from({length: total}, (_, i) => card(`s${room}-${i}`, 'todo'));
  const {bucketRows} = buildColumn(room, cards);
  const n = mod.computeGroupFit(total, room, GAP, WIDE).n;
  const press = keyDriver(bucketRows);
  let before = roles(bucketRows);
  const step = (key, expectIdx) => {
    const focused = press(key);
    assert.equal(focused.dataset.idx, String(expectIdx));
    const after = roles(bucketRows);
    const moved = (from, to) => cards.filter(c => from.includes(before.get(c.id)) && to.includes(after.get(c.id))).length;
    const counts = {intoGroup: moved(['above', 'below'], ['group']), outOfGroup: moved(['group'], ['above', 'below'])};
    assert.ok(counts.intoGroup <= 1 && counts.outOfGroup <= 1, `n=${n}, focus ${expectIdx}: at most one card each way, got ${JSON.stringify(counts)}`);
    assert.equal(cards.filter(c => after.get(c.id) === 'group').length, n, 'the group never grows past n');
    assert.equal(after.get(focused.dataset.cardId), 'group', 'the focused card is always in the group');
    assert.ok(!focused.className.includes('card-covered'), 'and open, never a covered strip');
    assertPilesInside(bucketRows, room, GAP, `n=${n}, focus ${expectIdx}`);
    before = after;
    return counts;
  };
  for (let i = 1; i < n; i++) {
    assert.deepEqual(step('ArrowDown', i), {intoGroup: 0, outOfGroup: 0}, 'stepping inside the group moves nothing');
  }
  assert.deepEqual(step('ArrowDown', n), {intoGroup: 1, outOfGroup: 1},
    'the step past the group\'s end: one card off the lower pile, the oldest onto the upper pile');
  for (let i = n + 1; i < total; i++) step('ArrowDown', i);
  for (let i = total - 2; i > total - 1 - n; i--) {
    assert.deepEqual(step('ArrowUp', i), {intoGroup: 0, outOfGroup: 0}, 'back up inside the group moves nothing');
  }
  assert.deepEqual(step('ArrowUp', total - 1 - n), {intoGroup: 1, outOfGroup: 1},
    'the step past the group\'s start: one card off the upper pile, the newest onto the lower pile');
  for (let i = total - 2 - n; i >= 0; i--) step('ArrowUp', i);
}

// ---- the anchor: top until the last card, then bottom until the first card, then top again -------

{
  const total = 14;
  const cards = Array.from({length: total}, (_, i) => card(`a${i}`, 'todo'));
  const {bucketRows} = buildColumn(SHORT_N2, cards);
  const press = keyDriver(bucketRows);
  const bottomAnchored = () => bucketRows.className.includes('bucket-rows-bottom');
  for (let i = 1; i < total - 1; i++) {
    press('ArrowDown');
    assert.ok(!bottomAnchored(), `focus ${i}: still anchored to the top`);
  }
  press('ArrowDown');
  assert.ok(bottomAnchored(), 'reaching the last card anchors the column to the bottom edge');
  const rows = replayRows(bucketRows, SHORT_N2, GAP);
  assert.equal(rows.at(-1).bottom, SHORT_N2, 'the group sits on the bottom edge');
  rows.filter(r => r.top < 0).forEach(r => assert.ok(!r.pile, 'only the column\'s first cards run off the top, never a pile'));
  const n = mod.computeGroupFit(total, SHORT_N2, GAP, WIDE).n;
  for (let i = total - 2; i > 3; i--) {
    press('ArrowUp');
    assert.ok(bottomAnchored(), `focus ${i}: stays bottom-anchored on the way back up`);
    const below = [...roles(bucketRows).values()].filter(v => v === 'below').length;
    assert.equal(below, Math.max(0, total - i - n), 'the pile below the group grows one card per step up');
    assertPilesInside(bucketRows, SHORT_N2, GAP, `bottom-anchored, focus ${i}`);
  }
  assert.equal(press('ArrowDown').dataset.idx, '5');
  assert.ok(bottomAnchored(), 'turning back down mid-column keeps the bottom anchor');
  for (let i = 4; i >= 1; i--) {
    assert.equal(press('ArrowUp').dataset.idx, String(i));
    assert.ok(bottomAnchored(), `focus ${i}: bottom-anchored until the first card itself`);
  }
  const first = press('ArrowUp');
  assert.equal(first.dataset.idx, '0');
  assert.ok(!bottomAnchored(), 'back at the first card, the column anchors to the top again');
}

// ---- real geometry: a 35-card column on a live 1600px-wide board (playwright, the todo column:
// rows 89.8125px below the viewport top, 222.390625px wide, a 10px gap) - piles stay on screen at
// every step, at a tall window and a short one --------------------------------------------------

for (const innerHeight of [1300, 700]) {
  const rowsTop = 89.8125, width = 222.390625, gap = 10;
  const cards = Array.from({length: 35}, (_, i) => card(`d${innerHeight}-${i}`, 'todo'));
  const bucketEl = element('div', 'bucket');
  bucketEl.dataset.status = 'todo';
  const bucketRows = element('div', 'bucket-rows');
  bucketRows.getBoundingClientRect = () => ({top: rowsTop, left: 0, right: 0, bottom: 0, width, height: 0});
  const realGetComputedStyle = globalThis.getComputedStyle;
  globalThis.getComputedStyle = el => (el === bucketRows ? {rowGap: `${gap}px`} : realGetComputedStyle(el));
  globalThis.window.innerHeight = innerHeight;
  bucketEl.appendChild(element('div', 'bucket-label'));
  bucketEl.appendChild(bucketRows);
  mod.renderBucketColumn(bucketEl, cards, 'todo');
  const available = innerHeight - rowsTop - 24;
  const press = keyDriver(bucketRows);
  assertPilesInside(bucketRows, available, gap, `window ${innerHeight}, at rest`);
  for (let i = 1; i < 35; i++) { press('ArrowDown'); assertPilesInside(bucketRows, available, gap, `window ${innerHeight}, down to ${i}`); }
  for (let i = 33; i >= 0; i--) { press('ArrowUp'); assertPilesInside(bucketRows, available, gap, `window ${innerHeight}, up to ${i}`); }
  globalThis.getComputedStyle = realGetComputedStyle;
}
globalThis.window.innerHeight = 800;

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

// ---- no pile when it isn't needed: every card as one group, the open card included ------------

{
  const fit = mod.computeGroupFit(12, ALL_12_HEIGHT, GAP, WIDE);
  assert.deepEqual([fit.n, fit.piles], [12, false], 'every card fits as one group - no piles at all');
  assert.equal(mod.computeGroupFit(13, ALL_12_HEIGHT, GAP, WIDE).piles, true, 'one card more and a pile has to appear');
  const cards = Array.from({length: 12}, (_, i) => card(`n${i}`, 'todo'));
  const {bucketRows} = buildColumn(ALL_12_HEIGHT, cards);
  assert.equal(piles(bucketRows).length, 0, 'no pile row at all');
  assert.equal(fullCards(bucketRows).length, 12, 'every card renders, peeked into one running stack');
  keyDriver(bucketRows)('ArrowDown');
  keyDriver(bucketRows)('ArrowDown');
  assertPilesInside(bucketRows, ALL_12_HEIGHT, GAP, 'no piles, focus open mid-run');
  assert.ok(replayRows(bucketRows, ALL_12_HEIGHT, GAP).at(-1).bottom <= ALL_12_HEIGHT, 'the whole run still fits with a card open');
}

// ---- in the dom: a narrow column turns portrait, and no stray scrollbar appears unless it must ---

{
  // enough cards, and enough room to need a pile (not so much room the no-pile rule kicks in), but
  // a 180px-wide column - the card stays portrait (230px tall) rather than shrinking to fit the
  // column's own narrow width. room for n=2 at that portrait height, so the group has a covered card
  const room = groupMax(2, mod.PORTRAIT_BELOW) + pilesCost(GAP);
  const cards = Array.from({length: 12}, (_, i) => card(`f${i}`, 'todo'));
  const bucketEl = element('div', 'bucket');
  bucketEl.dataset.status = 'todo';
  const label = element('div', 'bucket-label');
  const bucketRows = element('div', 'bucket-rows');
  bucketRows.getBoundingClientRect = () => ({top: 800 - 24 - room, left: 0, right: 0, bottom: 0, width: 180, height: 0});
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
  const {bucketRows} = buildColumn(SHORT_N2, cards);
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
  const {bucketEl, bucketRows} = buildColumn(SHORT_N2, cards); // starts piled: 2 full + pile + 2 full
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

// ---- the ends match the mockup's pushGroup (derived/column_motion, layoutC): the focused card is
// always the open one - at rest, when focus first lands in the column, and at the last card - with
// the cards passed as covered strips above it and the next card full below it ---------------------

// the column top to bottom: 'P' a pile, else the card's 1-based position plus 'o' (open, nothing
// slides over it) or 's' (a covered strip)
function drawn(bucketRows) {
  return bucketRows.children.map(el => (el.className.includes('card-pile') ? 'P'
    : `${Number(el.dataset.idx) + 1}${el.className.includes('card-covered') ? 's' : 'o'}`));
}
// focus arriving from outside the column - buckets.js's left/right nav, tab, a panel closing -
// rather than through a pile key
function focusFromOutside(bucketRows, row) {
  row.focus();
  bucketRows._listeners.focusin[0]({target: row, stopPropagation() {}});
  return bucketRows.children.find(r => r.focused);
}

for (const [room, n] of [[SHORT_N2, 2], [TALL_N3, 3]]) {
  const total = 14;
  const cards = Array.from({length: total}, (_, i) => card(`e${n}-${i}`, 'todo'));
  const {bucketRows} = buildColumn(room, cards);
  const rest = n === 3 ? ['1s', '2s', '3o'] : ['1s', '2o'];
  assert.deepEqual(drawn(bucketRows).slice(0, n), rest, `n=${n}, at rest: every card covered by the next, as the fan rests`);
  const first = focusFromOutside(bucketRows, fullCards(bucketRows)[0]);
  assert.equal(first.dataset.idx, '0');
  assert.deepEqual(drawn(bucketRows).slice(0, n), n === 3 ? ['1o', '2s', '3o'] : ['1o', '2o'],
    `n=${n}: focus landing on card 1 opens it`);
  const second = focusFromOutside(bucketRows, fullCards(bucketRows).find(s => s.dataset.idx === '1'));
  assert.equal(second.dataset.idx, '1', 'the column redraws around a card focus lands on from outside');
  assert.deepEqual(drawn(bucketRows).slice(0, n), n === 3 ? ['1s', '2o', '3o'] : ['1s', '2o'],
    `n=${n}: card 2 focused - card 1 passed as a strip, card 2 open, card 3 full below it`);
  const press = keyDriver(bucketRows);
  for (let i = 2; i < total; i++) press('ArrowDown');
  assert.equal(bucketRows.children.find(r => r.focused).dataset.idx, String(total - 1));
  assert.deepEqual(drawn(bucketRows).slice(-n), n === 3 ? ['12s', '13s', '14o'] : ['13s', '14o'],
    `n=${n}, the last card: the cards passed are strips above it, the last card open at the bottom`);
  press('ArrowUp');
  assert.deepEqual(drawn(bucketRows).slice(-n), n === 3 ? ['12s', '13o', '14o'] : ['13o', '14o'],
    `n=${n}, one back up: that card opens and the last card stays full below it`);
}

// ---- card shadows ride their card: each is a child canvas of its own strip, placed against the
// strip itself, so a card that moves takes its shadow with it and none is ever left behind -------

{
  const cards = Array.from({length: 12}, (_, i) => card(`y${i}`, 'todo'));
  const {bucketEl, bucketRows} = buildColumn(SHORT_N2, cards);
  const pad = mod.shadowImage(WIDE, WIDE).pad; // the stub's border width is 0
  const shadeOf = strip => strip.children.filter(c => c.className === 'card-shade');
  const assertRiding = where => {
    const strips = fullCards(bucketRows);
    strips.forEach(s => assert.equal(shadeOf(s).length, 1, `${where}: exactly one shadow per card`));
    const all = bucketEl.querySelectorAll('.card-shade');
    assert.equal(all.length, strips.length, `${where}: no shadow outside a drawn card`);
    all.forEach(c => {
      assert.ok(strips.includes(c.parentNode), `${where}: every shadow is a child of a card still drawn`);
      assert.deepEqual([c.style.top, c.style.left], [`-${pad}px`, `-${pad}px`], `${where}: placed against its own card`);
    });
  };
  assertRiding('at rest');
  assert.equal(bucketEl.querySelectorAll('.card-shadow-layer').length, 0, 'no shared shadow layer left');
  const strip = fullCards(bucketRows)[0];
  strip.getBoundingClientRect = () => ({top: 100, left: 0, right: WIDE, bottom: 100 + WIDE, width: WIDE, height: WIDE});
  mod.applyCardShadows(bucketRows);
  const shade = shadeOf(strip)[0];
  strip.getBoundingClientRect = () => ({top: 400, left: 40, right: 40 + WIDE, bottom: 400 + WIDE, width: WIDE, height: WIDE});
  mod.applyCardShadows(bucketRows);
  assert.equal(shadeOf(strip)[0], shade, 'a card that moved keeps the very same shadow node');
  assert.deepEqual([shade.style.top, shade.style.left], [`-${pad}px`, `-${pad}px`], 'still placed against the card, wherever it went');
  strip.getBoundingClientRect = () => ({top: 400, left: 40, right: 340, bottom: 630, width: WIDE, height: mod.PORTRAIT_BELOW});
  mod.applyCardShadows(bucketRows);
  assert.equal(shadeOf(strip).length, 1, 'a card that changed size swaps its shadow, never stacks a second');
  assert.equal(shadeOf(strip)[0].dataset.size, `${WIDE}x${mod.PORTRAIT_BELOW}`, 'sized to the card\'s new box');
  const press = keyDriver(bucketRows);
  for (let i = 1; i < cards.length; i++) { press('ArrowDown'); assertRiding(`down to ${i}`); }
  for (let i = cards.length - 2; i >= 0; i--) { press('ArrowUp'); assertRiding(`up to ${i}`); }
}

// ---- flip: the translate/scale that replays a row from its old box, about the element's centre --

{
  const d = mod.flipDelta({left: 0, top: 0, width: WIDE, height: mod.PILE}, {left: 0, top: mod.PILE, width: WIDE, height: WIDE});
  assert.deepEqual(d, {x: 0, y: mod.PILE / 2 - (mod.PILE + WIDE / 2), sx: 1, sy: mod.PILE / WIDE},
    'a card drawn off a pile starts at the pile\'s own box - its centre, its height');
}

// ---- attention is the standard frame in vanilla: no ring, glow or outline past it, in css or on
// the card itself ---------------------------------------------------------------------------------

const layoutCss = smort('layout.css').replace(/\/\*[\s\S]*?\*\//g, '');
// every rule whose selector mentions `needle`, as [selector, body] pairs
function cssRules(needle) {
  return [...layoutCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .map(m => [m[1].trim(), m[2]]).filter(([sel]) => sel.includes(needle));
}

{
  const rules = cssRules('.card-attention');
  assert.ok(rules.length, 'the attention rule is still there - it sets the frame colour');
  rules.forEach(([sel, body]) => assert.ok(!/box-shadow|outline/.test(body), `${sel}: no ring or glow past the frame`));
  const cards = [card('x1', 'todo', {review_flag: true}), card('x2', 'todo', {blocked_reason_code: 'CRASH'})];
  const {bucketRows} = buildColumn(2000, cards, 'attention');
  fullCards(bucketRows).forEach(s => {
    assert.ok(s.className.includes('card-attention'), 'still marked attention, for the frame colour');
    assert.deepEqual(s.className.split(' ').filter(c => /glow|ring/.test(c)), [], 'no glow or ring class');
    assert.ok(!s.style.boxShadow && !s.style.outline, 'no inline ring either');
  });
}

// ---- focus: one look for every card in every column - the lift, the colours and its own ring -
// read off the real cascade (ui_base's base.css, then layout.css) for real rendered strips, so a
// rule scoped to one column type shows up as a difference rather than slipping past a grep --------

const stripComments = css => css.replace(/\/\*[\s\S]*?\*\//g, '');
const sheet = [uiBase('base.css'), smort('layout.css')].flatMap(css =>
  [...stripComments(css).matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(m => [m[1].trim(), m[2]]));

// one compound selector (no combinator) against el - :focus holds only for `focusedEl`
function compoundMatches(el, compound, focusedEl) {
  if (compound.includes('::') || /:has\(/.test(compound)) return false;
  const nots = [];
  let rest = compound.replace(/:not\(([^()]*)\)/g, (_, inner) => { nots.push(inner); return ''; });
  for (const pseudo of rest.match(/:[\w-]+/g) || []) {
    if (pseudo === ':focus' || pseudo === ':focus-visible') { if (el !== focusedEl) return false; }
    else if (pseudo === ':first-child') { if (el.parentNode?.children[0] !== el) return false; }
    else if (pseudo === ':last-child') { if (el.parentNode?.children.at(-1) !== el) return false; }
    else return false; // :hover, :root and the rest are not in play here
  }
  rest = rest.replace(/:[\w-]+/g, '');
  if (/[[*%]/.test(rest)) return false;
  const tag = rest.replace(/[.#][\w-]+/g, '');
  if (tag && tag !== el.tag) return false;
  if ((rest.match(/#[\w-]+/g) || []).some(id => el.id !== id.slice(1))) return false;
  if (!(rest.match(/\.[\w-]+/g) || []).every(c => el.classList?.contains(c.slice(1)))) return false;
  return !nots.some(inner => compoundMatches(el, inner, focusedEl));
}

// a full selector, right to left through descendant, child and sibling combinators
function selectorMatches(el, selector, focusedEl) {
  const tokens = selector.split(/(\s*[>~+]\s*|\s+)/);
  const from = (node, i) => {
    if (!node || !compoundMatches(node, tokens[i], focusedEl)) return false;
    if (i === 0) return true;
    const comb = tokens[i - 1].trim();
    const siblings = node.parentNode?.children || [];
    const before = siblings.slice(0, siblings.indexOf(node));
    if (comb === '>') return from(node.parentNode, i - 2);
    if (comb === '~') return before.some(s => from(s, i - 2));
    if (comb === '+') return from(before.at(-1), i - 2);
    for (let up = node.parentNode; up; up = up.parentNode) if (from(up, i - 2)) return true;
    return false;
  };
  return from(el, tokens.length - 1);
}

function specificity(selector) {
  const s = selector.replace(/::[\w-]+/g, '');
  const ids = (s.match(/#[\w-]+/g) || []).length;
  const classes = (s.match(/\.[\w-]+|\[[^\]]*\]|:(?!not\(|has\()[\w-]+/g) || []).length;
  const tags = (s.replace(/:[\w-]+|\.[\w-]+|#[\w-]+|\[[^\]]*\]/g, ' ').match(/[a-z][\w-]*/gi) || []).length;
  return ids * 10000 + classes * 100 + tags;
}

// the winning value of each focus property for `strip`, with `focusedEl` holding focus
const FOCUS_PROPS = ['transform', 'filter', 'box-shadow', 'outline'];
function focusLook(strip, focusedEl = strip) {
  const won = {};
  sheet.forEach(([list, body]) => list.split(',').map(s => s.trim()).forEach(sel => {
    if (!sel || sel.startsWith('@') || !selectorMatches(strip, sel, focusedEl)) return;
    const spec = specificity(sel);
    body.split(';').forEach(decl => {
      const at = decl.indexOf(':');
      const prop = decl.slice(0, at).trim();
      if (at < 0 || !FOCUS_PROPS.includes(prop)) return;
      if (!won[prop] || spec >= won[prop].spec) won[prop] = {spec, value: decl.slice(at + 1).trim()};
    });
  }));
  return Object.fromEntries(FOCUS_PROPS.map(p => [p, won[p]?.value ?? null]));
}

// the :has() rules that hide indicate.js's marker, and whether one of them covers `strip` focused
function markerHiddenFor(strip) {
  return sheet.some(([list, body]) => /opacity:\s*0\b/.test(body) && list.split(',').some(sel => {
    const has = sel.match(/:has\(([^()]*)\)\s+\.focus-marker$/);
    return has && selectorMatches(strip, has[1], strip);
  }));
}

{
  const focusedIn = (bucketRows, pick) => {
    const strip = pick(fullCards(bucketRows));
    strip.focus();
    return strip;
  };
  // a plain short column (the fan), an expanded one (the fan again), and a stacked one: its open
  // card, a covered strip, an edge card past the lower pile, and the last card bottom-anchored
  const plain = buildColumn(2000, [card('pl0', 'todo'), card('pl1', 'todo'), card('pl2', 'todo')]).bucketRows;
  const expanded = buildColumn(600, Array.from({length: 10}, (_, i) => card(`ex${i}`, 'todo')));
  expanded.bucketEl.querySelector('.bucket-expand')._listeners.click[0]();
  const stacked = buildColumn(SHORT_N2, Array.from({length: 14}, (_, i) => card(`st${i}`, 'todo'))).bucketRows;
  const bottom = buildColumn(SHORT_N2, Array.from({length: 14}, (_, i) => card(`bt${i}`, 'todo'))).bucketRows;
  const pressBottom = keyDriver(bottom);
  for (let i = 1; i < 14; i++) pressBottom('ArrowDown');
  const cases = {
    'plain, first card': focusedIn(plain, s => s[0]),
    'plain, middle card': focusedIn(plain, s => s[1]),
    'expanded, middle card': focusedIn(expanded.bucketRows, s => s[4]),
    'stacked, open card': focusFromOutside(stacked, fullCards(stacked)[0]),
    'stacked, covered strip': focusedIn(stacked, s => s.find(x => x.className.includes('card-covered'))),
    'stacked, edge card': focusedIn(stacked, s => s.at(-1)),
    'stacked, bottom-anchored last card': fullCards(bottom).at(-1),
  };
  cases['stacked, bottom-anchored last card'].focus();
  assert.ok(cases['plain, middle card'].className.includes('fan-item') && !cases['stacked, open card'].className.includes('fan-item'),
    'both column types are really under test: the plain column fans, the stacked one does not');
  const reference = focusLook(cases['stacked, open card']);
  assert.match(reference.transform, /^scale\(1\.03\)$/, 'a focused card lifts 3% toward the viewer');
  assert.match(reference.filter, /saturate\(1\.7\) brightness\(1\.4\)/, 'and its own colours step up');
  assert.match(reference['box-shadow'], /^inset 0 0 0 3px var\(--cream\), inset 0 0 8px 1\.5px /,
    'its own ring: the inset frame, then the inner glow at half its old 16px blur and 3px spread');
  Object.entries(cases).forEach(([where, strip]) => {
    assert.deepEqual(focusLook(strip), reference, `${where}: the same focus look as a stacked card`);
    assert.ok(markerHiddenFor(strip), `${where}: the shared marker hides, so it never draws over the card's own ring`);
    assert.equal(focusLook(strip, null)['box-shadow'], 'none', `${where}: no ring without focus`);
  });
}

// ---- pile motion: a step that moves a card onto or off a pile runs at the pile's own slower
// timing, and a pile's count only changes when the card lands on it ------------------------------

{
  assert.ok(mod.PILE_MOTION_MS >= 260 && mod.PILE_MOTION_MS <= 320, 'the pile timing sits in the 260-320 ms band');
  assert.ok(mod.PILE_MOTION_MS > mod.ROW_MOTION_MS, 'and is slower than a plain step');
  const prior = new Map([['a', 'card'], ['b', 'card'], ['c', 'pile-below'], ['d', 'pile-below']]);
  const next = new Map([['a', 'pile-above'], ['b', 'card'], ['c', 'card'], ['d', 'pile-below']]);
  const plan = mod.planPileMotion(prior, next);
  assert.deepEqual([plan.duration, plan.easing], [mod.PILE_MOTION_MS, mod.PILE_EASING], 'any pile change: every row at the pile timing');
  assert.deepEqual(plan.piles.get('pile-above'), {landing: ['a'], lifting: [], countAt: mod.PILE_MOTION_MS},
    'a card landing: its pile\'s count changes when it lands, not before');
  assert.deepEqual(plan.piles.get('pile-below'), {landing: [], lifting: ['c'], countAt: 0},
    'a card lifting off: that pile\'s count changes at once, the card has left it');
  const still = mod.planPileMotion(prior, new Map(prior));
  assert.deepEqual([still.duration, still.easing, still.piles.size], [mod.ROW_MOTION_MS, mod.ROW_EASING, 0],
    'a step inside the group keeps the plain timing');
}

// a keyframe's clip-path inset as [top, right, bottom, left] px, and its translate's y
const insets = frame => frame.clipPath.match(/^inset\((.+)\)$/)[1].split(' ').map(parseFloat);
const shiftY = frame => parseFloat(frame.translate.split(' ')[1]);
// nothing of a box `height` tall (or its shadow) left between the top and bottom cuts
const fullyClipped = (frame, height) => insets(frame)[0] + insets(frame)[2] >= height;
// the card's own box uncut on every side
const uncut = frame => insets(frame).every(v => v <= 0);
// the band of screen a frame actually paints a box drawn at `box` into - a negative inset reaches
// out past the box for the shadow rather than cutting into it, so it never moves the visible edge
const visible = (frame, box) => {
  const [top, , bottom] = insets(frame);
  const y = box.top + shiftY(frame);
  return [y + Math.max(top, 0), y + box.height - Math.max(bottom, 0)];
};

// ---- the pile's mouth: a card goes in and out through the painted edge that faces it, cut at that
// line the whole way - the cut never moves while the card slides, so the pile eats it from its edge

{
  const r = mod.CLIP_REACH;
  const pile = {left: 0, top: 0, width: WIDE, height: mod.PILE};
  const line = mod.PILE - mod.PILE_FACE_INSET;
  const below = {left: 0, top: line + r + 30, width: WIDE, height: WIDE};
  const mouth = mod.pileMouth(pile, below);
  assert.deepEqual(mouth, {above: true, left: 0, line}, 'a card below the pile meets its bottom face edge');

  // the face is the count's box, PILE_FACE_INSET in from the row - but a jittered layer protrudes
  // past it, and that outermost drawn edge is the one that eats the card, never the row/gap edge
  const jut = 6;
  const layers = [{top: mod.PILE_FACE_INSET - jut, bottom: mod.PILE - mod.PILE_FACE_INSET + jut}];
  const painted = mod.pileFace(pile, layers);
  assert.deepEqual([painted.top, painted.top + painted.height], [mod.PILE_FACE_INSET - jut, line + jut],
    'the painted face reaches out to the layers that stick out past the count');
  assert.equal(mod.pileMouth(pile, below, layers).line, line + jut, 'and the mouth sits on that edge');
  assert.ok(line + jut < pile.top + pile.height, 'still inside the pile row, never out at the gap');
  const aboveCard = {left: 0, top: -WIDE - 60, width: WIDE, height: WIDE};
  assert.equal(mod.pileMouth(pile, aboveCard, layers).line, mod.PILE_FACE_INSET - jut,
    'mirrored at the top edge for a card above the pile');
  const {shown, inside} = mod.mouthFrames(below, mouth);
  assert.equal(shown.translate, '0px 0px', 'shown: the card at its own box');
  assert.ok(uncut(shown), 'and uncut there');
  assert.equal(below.top + insets(shown)[0], line, 'the cut sits on the mouth line');
  assert.equal(below.top + shiftY(inside) + insets(inside)[0], line, 'and is still on it once the card is all the way in');
  assert.ok(fullyClipped(inside, WIDE) && insets(inside)[0] >= WIDE + r, 'all the way in: nothing of the card or its shadow is left');
  assert.ok(insets(inside)[0] > insets(shown)[0], 'the cut grows from the pile\'s side');

  const above = {left: 0, top: -WIDE - 60, width: WIDE, height: WIDE};
  const low = mod.pileMouth(pile, above);
  assert.deepEqual(low, {above: false, left: 0, line: mod.PILE_FACE_INSET}, 'a card above the pile meets its top face edge');
  const lowFrames = mod.mouthFrames(above, low);
  assert.equal(above.top + above.height - insets(lowFrames.shown)[2], low.line);
  assert.equal(above.top + above.height + shiftY(lowFrames.inside) - insets(lowFrames.inside)[2], low.line);
  assert.ok(fullyClipped(lowFrames.inside, WIDE) && insets(lowFrames.inside)[2] > insets(lowFrames.shown)[2],
    'mirrored: the cut grows from the bottom, toward the pile below');

  // the invariant is the line, not the clamp: a card whose own box already reaches past the mouth
  // is cut on the same line from the first frame, which is what keeps the cut still while it slides
  const reaching = {left: 0, top: 20, width: WIDE, height: WIDE};
  const reach = mod.mouthFrames(reaching, mod.pileMouth(pile, reaching));
  assert.equal(reaching.top + insets(reach.shown)[0], line, 'reaching in: the cut is on the line at once');
  assert.equal(reaching.top + shiftY(reach.inside) + insets(reach.inside)[0], line, 'and stays there');

  // the fold: a pile forming in the card's own place, the card collapsing onto its face. the edge
  // on the pile's side holds, the other travels, and it lands exactly on the face
  const face = mod.pileFace(pile);
  const folder = {left: 0, top: 40, width: WIDE, height: WIDE};
  const up = mod.foldFrames(folder, face, true);
  assert.ok(uncut(up.open) && up.open.translate === '0px 0px', 'open: the card whole, where it is');
  assert.deepEqual(visible(up.folded, folder), [face.top, face.top + face.height], 'folded: exactly the pile\'s face');
  assert.ok(insets(up.folded)[2] > insets(up.open)[2] && insets(up.folded)[0] <= 0,
    'a pile above holds the card\'s top edge and climbs its bottom');

  const belowFace = {left: 0, top: 400, width: WIDE, height: mod.PILE - 2 * mod.PILE_FACE_INSET};
  const down = mod.foldFrames(folder, belowFace, false);
  assert.deepEqual(visible(down.folded, folder), [belowFace.top, belowFace.top + belowFace.height],
    'mirrored: it lands on the lower pile\'s face too');
  assert.ok(insets(down.folded)[0] > insets(down.open)[0] && insets(down.folded)[2] <= 0,
    'a pile below holds the card\'s bottom edge and pushes its top down');
}

// drives a stacked column with the stub's animation recorder on and timers held, never real time
function withMotion(run, {reduced = false} = {}) {
  const timers = [];
  const realSetTimeout = globalThis.setTimeout;
  stubMotion.on = true;
  stubMotion.log.length = 0;
  globalThis.setTimeout = (fn, ms) => { timers.push({fn, ms}); return 0; };
  if (reduced) globalThis.matchMedia = query => ({matches: query.includes('reduce')});
  try { run(timers); } finally {
    stubMotion.on = false;
    stubLayout.rect = null;
    globalThis.setTimeout = realSetTimeout;
    delete globalThis.matchMedia;
  }
}
const pileOn = (bucketRows, side) => piles(bucketRows).find(p => p.dataset.side === side);
const pileCount = pile => pile.querySelector('.bucket-counts')?.textContent ?? null;
const ghostsOf = bucketRows => bucketRows.children.filter(c => c.className.includes('row-ghost'));

// how far a drawn pile layer sticks out past the count's face here - .card-pile-layer's jitter does
// this in the browser, and it is what the mouth must sit on rather than the face inset
const LAYER_JUT = 4;

// the stub has no layout: give each drawn row the box the css model puts it at (replayRows), and a
// pile's own layers the face box they are inset to, the outermost one jutting past it
function rowGeometry(bucketRows, available) {
  const box = (top, bottom) => ({left: 0, top, width: WIDE, height: bottom - top, right: WIDE, bottom});
  return el => {
    const pile = el.className.includes('card-pile-layer') && el.parentNode;
    if (pile) {
      const r = pile.getBoundingClientRect();
      const inset = mod.PILE_FACE_INSET - (pile.children.indexOf(el) === 0 ? LAYER_JUT : 0);
      return box(r.top + inset, r.bottom - inset);
    }
    if (el.parentNode !== bucketRows || el.className.includes('row-ghost')) return null;
    const row = replayRows(bucketRows, available, GAP).find(r => r.el === el);
    return row && box(row.top, row.bottom);
  };
}

// paint order the way the browser settles it here: every row, ghost and pile is positioned at
// z-index 0 or auto in one stacking context, so a higher z wins and equal z paints in dom order
const zOf = el => Number(el.style.zIndex || 0);
function paintsUnder(a, b) {
  if (zOf(a) !== zOf(b)) return zOf(a) < zOf(b);
  const rows = a.parentNode.children;
  return a.parentNode === b.parentNode && rows.indexOf(a) < rows.indexOf(b);
}

withMotion(timers => {
  const total = 14;
  const cards = Array.from({length: total}, (_, i) => card(`mo${i}`, 'todo'));
  const {bucketEl, bucketRows} = buildColumn(SHORT_N2, cards);
  stubLayout.rect = rowGeometry(bucketRows, SHORT_N2);
  const n = mod.computeGroupFit(total, SHORT_N2, GAP, WIDE).n;
  const press = keyDriver(bucketRows);
  focusFromOutside(bucketRows, fullCards(bucketRows)[0]);
  for (let i = 1; i < n; i++) press('ArrowDown');
  const landings = () => timers.filter(t => t.ms === mod.PILE_MOTION_MS);
  assert.equal(landings().length, 0, 'steps inside the group schedule no pile landing');
  const belowBefore = pileOn(bucketRows, 'below')._cards.length;
  const ghostOf = id => stubMotion.log.find(a => a.el.className.includes('row-ghost') && a.el.dataset.cardId === id);

  // the step past the group's end FORMS the upper pile, in the very room card 1 is leaving: the
  // card shrinks onto the pile's first layer rather than sliding in from outside it
  stubMotion.log.length = 0;
  press('ArrowDown');
  const ghost = ghostOf(cards[0].id);
  assert.ok(ghost, 'the card folded onto the pile moves there as itself');
  const upper = pileOn(bucketRows, 'above');
  const upperFace = mod.pileFace(upper.getBoundingClientRect(), [...upper.querySelectorAll('.card-pile-layer')].map(l => l.getBoundingClientRect()));
  assert.equal(ghost.el.parentNode, bucketRows, 'seated among the rows, in their paint order');
  assert.ok(paintsUnder(ghost.el, upper), 'under the pile the whole way, not hovering over it');
  assert.ok(paintsUnder(fullCards(bucketRows)[0], ghost.el),
    'but over the rows sliding up into the room it still holds, or the fold is invisible');
  assert.ok(!ghost.el.className.split(' ').includes('row') && ghost.el.dataset.idx === undefined, 'never counted as a row');
  assert.deepEqual([ghost.options.duration, ghost.options.easing], [mod.PILE_MOTION_MS, mod.PILE_EASING], 'at the pile timing');
  assert.ok(ghost.keyframes.every(k => !('opacity' in k)), 'no fade: it shrinks, it does not dissolve');
  const [open, folded] = ghost.keyframes;
  const ghostBox = {top: parseFloat(ghost.el.style.top), height: parseFloat(ghost.el.style.height)};
  assert.ok(uncut(open) && open.translate === '0px 0px', 'from where it was drawn, whole');
  assert.deepEqual(visible(folded, ghostBox), [upperFace.top, upperFace.top + upperFace.height],
    'ending exactly as the forming pile\'s top layer');
  assert.ok(insets(folded)[2] > insets(open)[2] && insets(folded)[0] <= 0,
    'the top pile holds the card\'s top edge and climbs its bottom, content cut not squashed');
  const formed = upper._animations[0];
  assert.deepEqual(formed.keyframes, mod.PILE_HANDOVER_IN, 'the pile only takes over once the card has landed');
  assert.equal(formed.options.duration, ghost.options.duration);
  assert.equal(pileCount(upper), null, 'with no count until its first card is in');
  assert.equal(landings().length, 1, 'the count is scheduled for when the card is in');
  assert.ok(landings()[0].ms >= ghost.options.duration, 'not before the card is fully eaten');
  landings()[0].fn();
  assert.equal(pileCount(upper), '1');
  // the other end of the same step: one card slides out from under the lower pile, whose count
  // drops at once
  const lowerPile = pileOn(bucketRows, 'below');
  const lifted = fullCards(bucketRows).find(s => s.dataset.cardId === cards[n].id);
  const lift = lifted._animations[0];
  assert.deepEqual([lift.options.duration, lift.options.easing], [mod.PILE_MOTION_MS, mod.PILE_EASING], 'at the pile timing');
  assert.ok(paintsUnder(lifted, lowerPile), 'the card drawn off the lower pile comes out from under it');
  assert.ok(fullyClipped(lift.keyframes[0], parseFloat(lifted.style.height)), 'starting all the way in, nothing showing');
  assert.ok(shiftY(lift.keyframes[0]) > 0, 'down inside the pile below it');
  assert.ok(insets(lift.keyframes[1])[2] < insets(lift.keyframes[0])[2], 'the cut falls back toward the pile as it slides out');
  assert.equal(lift.keyframes[1].translate, '0px 0px', 'and ends in its own place');
  const liftBox = lifted.getBoundingClientRect();
  assert.equal(visible(lift.keyframes[0], liftBox)[1], visible(lift.keyframes[1], liftBox)[1],
    'the cut sits on the same screen line at both ends, so it never moves while the card comes out');
  assert.ok(lift.keyframes.every(k => !('opacity' in k)) && lifted._animations.length === 1, 'no fade on the way out either');
  lift.onfinish();
  assert.equal(lowerPile.style.zIndex, '', 'the pile drops back to its own paint order once the card is out');
  assert.equal(pileCount(lowerPile), String(belowBefore - 1), 'the lower pile shows its new count straight away');

  // the next step lands a second card on the pile that is already there: 1 until it is in, then 2.
  // the first card's ghost is still sliding (no real time passes) and keeps its seat under the pile
  stubMotion.log.length = 0;
  press('ArrowDown');
  const again = pileOn(bucketRows, 'above');
  const second = ghostOf(cards[1].id);
  assert.ok(paintsUnder(second.el, again) && paintsUnder(ghost.el, again), 'both cards under the redrawn pile');
  assert.ok(paintsUnder(ghost.el, fullCards(bucketRows)[0]),
    'the folding card drops back under the rows once its pile is drawn for real');
  const [slideShown, slideInside] = second.keyframes;
  assert.ok(shiftY(slideInside) < 0 && insets(slideInside)[0] > insets(slideShown)[0],
    'a pile that is already there is slid into through its mouth, not folded onto');
  assert.ok(fullyClipped(slideInside, parseFloat(second.el.style.height)), 'until nothing of the card is left');
  assert.deepEqual(ghostsOf(bucketRows), [ghost.el, second.el], 'the first still in flight, re-seated, not dropped');
  assert.equal(pileCount(again), '1', 'the pile keeps its old count while the card is still sliding in');
  assert.equal(landings().length, 2, 'one more landing, scheduled PILE_MOTION_MS in - when the card is in');
  landings()[1].fn();
  assert.equal(pileCount(again), '2', 'the count ticks up once the card is eaten');
  assert.ok(again.querySelector('.bucket-counts')._animations?.length, 'with a small pop on the number');
  ghost.onfinish();
  second.onfinish();
  assert.deepEqual(ghostsOf(bucketRows), [], 'each ghost goes once its slide ends');

  // the reverse, off the upper pile: back to the group's start, then one more draws a card off it
  press('ArrowUp');
  stubMotion.log.length = 0;
  press('ArrowUp');
  const top = pileOn(bucketRows, 'above');
  const drawnOff = fullCards(bucketRows).find(s => s.dataset.cardId === cards[1].id);
  const out = drawnOff._animations[0];
  assert.ok(drawnOff && out, 'card 2 is drawn off the upper pile');
  assert.ok(bucketRows.children.indexOf(drawnOff) > bucketRows.children.indexOf(top), 'after the pile in the dom');
  assert.ok(paintsUnder(drawnOff, top), 'yet under it until it is out');
  assert.ok(fullyClipped(out.keyframes[0], parseFloat(drawnOff.style.height)) && shiftY(out.keyframes[0]) < 0,
    'starting all the way up inside the pile');
  assert.ok(insets(out.keyframes[1])[0] < insets(out.keyframes[0])[0] && uncut(out.keyframes[1]), 'ending out of it, uncut');
  // and the same step folds the group's last card into the lower pile, from below the group
  const intoLower = stubMotion.log.find(a => a.el.className.includes('row-ghost'));
  assert.ok(paintsUnder(intoLower.el, pileOn(bucketRows, 'below')), 'under the lower pile');
  assert.ok(shiftY(intoLower.keyframes[1]) > 0 && insets(intoLower.keyframes[1])[2] > insets(intoLower.keyframes[0])[2],
    'down into it, cut from the bottom');
  out.onfinish();
  intoLower.onfinish();

  // the last card off the upper pile EMPTIES it: the pile leaves the room rather than staying to be
  // slid out of, so the card grows back out of its face from the same edge and the pile hands over
  stubMotion.log.length = 0;
  const emptyingFace = mod.pileFace(top.getBoundingClientRect(),
    [...top.querySelectorAll('.card-pile-layer')].map(l => l.getBoundingClientRect()));
  press('ArrowUp');
  const first = fullCards(bucketRows).find(s => s.dataset.cardId === cards[0].id);
  const gone = stubMotion.log.find(a => a.el.className.includes('row-ghost') && a.el.className.includes('card-pile'));
  assert.ok(gone && !pileOn(bucketRows, 'above'), 'the upper pile is gone');
  assert.ok(paintsUnder(gone.el, first), 'the card takes the pile\'s room over it, the pile does not shrink on top');
  const grow = first._animations[0];
  const firstBox = first.getBoundingClientRect();
  assert.deepEqual(visible(grow.keyframes[0], firstBox), [emptyingFace.top, emptyingFace.top + emptyingFace.height],
    'starting exactly as the face of the pile it is emptying');
  assert.ok(uncut(grow.keyframes[1]) && grow.keyframes[1].translate === '0px 0px', 'and ending whole, in its own place');
  assert.ok(insets(grow.keyframes[0])[2] > insets(grow.keyframes[1])[2] && insets(grow.keyframes[0])[0] <= 0,
    'the top pile\'s card grows down from its top edge, mirroring the fold');
  assert.deepEqual(gone.keyframes, mod.PILE_HANDOVER_OUT, 'the pile hands over at once instead of shrinking on top of it');
  bucketEl.remove();
});

withMotion(timers => {
  const cards = Array.from({length: 14}, (_, i) => card(`rm${i}`, 'todo'));
  const {bucketRows} = buildColumn(SHORT_N2, cards);
  const n = mod.computeGroupFit(14, SHORT_N2, GAP, WIDE).n;
  const press = keyDriver(bucketRows);
  for (let i = 0; i <= n; i++) press('ArrowDown');
  assert.equal(stubMotion.log.length, 0, 'reduced motion: nothing animates');
  assert.equal(timers.filter(t => t.ms === mod.PILE_MOTION_MS).length, 0, 'and no landing is scheduled');
  assert.equal(pileCount(pileOn(bucketRows, 'above')), '2', 'every count is final at once');
}, {reduced: true});

// ---- leaving a stacked column puts it back at rest: the open card is covered by the next again, as
// the fan does, whether focus left from card 1 or from further down ------------------------------

{
  const neighbour = element('div', 'bucket');
  const neighbourRow = element('div', 'row card card-strip');
  neighbour.appendChild(neighbourRow);
  bucketRow.appendChild(neighbour);
  const panel = element('div', 'expand-panel');
  document.body.appendChild(panel);
  const leaveTo = (bucketRows, to) => {
    const from = bucketRows.children.find(r => r.focused);
    from.blur();
    to.focus();
    bucketRows._listeners.focusout[0]({target: from, relatedTarget: to});
  };
  // what the rest rule draws for the group where it now stands
  const restDrawing = bucketRows => {
    const state = bucketRows._pile;
    const n = mod.computeGroupFit(state.sorted.length, SHORT_N2, GAP, WIDE).n;
    return mod.computePileLayout(state.sorted, null, state.start, state.anchor, n).rows
      .map(r => (r.type === 'pile' ? 'P' : `${r.idx + 1}${r.covered ? 's' : 'o'}`));
  };
  const cards = Array.from({length: 14}, (_, i) => card(`lv${i}`, 'todo'));
  const {bucketEl, bucketRows} = buildColumn(SHORT_N2, cards);
  bucketRow.appendChild(bucketEl);
  const atRest = drawn(bucketRows);
  assert.deepEqual(atRest, restDrawing(bucketRows));

  focusFromOutside(bucketRows, fullCards(bucketRows)[0]);
  assert.equal(drawn(bucketRows)[0], '1o', 'card 1 opens under focus');
  leaveTo(bucketRows, neighbourRow);
  assert.deepEqual(drawn(bucketRows), atRest, 'leaving from card 1: the next card slides back over it - the column at rest');
  assert.equal(bucketRows._pile.focusIndex, null);

  const press = keyDriver(bucketRows);
  focusFromOutside(bucketRows, fullCards(bucketRows)[0]);
  for (let i = 1; i <= 5; i++) press('ArrowDown');
  // one back up: focus on the group's first card, which the group's next card covers at rest
  assert.equal(press('ArrowUp').dataset.idx, '4');
  assert.ok(drawn(bucketRows).includes('5o'), 'card 5 is open under focus');
  const pilesBefore = piles(bucketRows).map(p => p._cards.map(c => c.id));
  leaveTo(bucketRows, neighbourRow);
  assert.deepEqual(drawn(bucketRows), restDrawing(bucketRows), 'leaving from a middle card: the same rest rule, no card left open');
  assert.ok(drawn(bucketRows).includes('5s') && !drawn(bucketRows).includes('5o'), 'the card that had focus is covered again');
  assert.deepEqual(piles(bucketRows).map(p => p._cards.map(c => c.id)), pilesBefore, 'the piles stay as they were');

  focusFromOutside(bucketRows, fullCards(bucketRows).find(s => s.dataset.idx === '5'));
  const open = drawn(bucketRows);
  leaveTo(bucketRows, panel);
  assert.deepEqual(drawn(bucketRows), open, 'a panel opened from the card is not leaving - nothing is redrawn under it');
  bucketEl.remove();
  neighbour.remove();
  panel.remove();
}

console.log('ok');
