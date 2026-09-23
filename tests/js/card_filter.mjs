// proves the cmd/ctrl+f card filter: it focuses its own input instead of the browser's find, matches
// case-insensitively on a card's title, short id, description, comments and column name, draws only
// the matching cards without reordering them or asking the server, clears on escape or an empty
// value, survives every redraw, and never lets the arrow keys land on a card it left out.
// run: node tests/js/card_filter.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const fetched = [];
installStubDom({fetchImpl: path => { fetched.push(path); return new Promise(() => {}); }});

const boardBar = element('div', 'board-bar');
boardBar.id = 'board-bar';
const tab = element('div', 'nav-tab active');
boardBar.appendChild(tab);
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

class SpyMenu { constructor(opts) { this.opts = opts; } openAt() { return this; } close() {} }
function SpyDrawer() { return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false}; }

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'),
  uiBase('pile.js'), uiBase('entrytext.js'), smort('columns.js'), smort('card_panel.js'),
  smort('chat.js'), smort('shortcuts.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {renderBuckets, redrawColumns, cardMatchesFilter, applyCardFilter, focusCardFilter, jumpToCard,
  setCurrentBoardId: id => { currentBoardId = id; }, setOpenCard: value => { openCard = value; },
  openCardRef: () => openCard};`)(SpyMenu, SpyDrawer);

function pressDoc(opts) {
  document._dispatch('keydown', {target: document.activeElement || document.body,
    preventDefault() {}, stopPropagation() {}, ...opts});
}
function fireKeydownOn(el, opts) {
  (el._listeners.keydown || []).forEach(fn => fn({target: el, preventDefault() {}, stopPropagation() {}, ...opts}));
}
function fireInput(input) {
  (input._listeners.input || []).forEach(fn => fn({target: input}));
}
const rowsOf = status => bucketRow.querySelector(`.bucket[data-status="${status}"] .bucket-rows`);
const drawnIds = status => rowsOf(status).children
  .filter(el => el.className.includes('card-strip')).map(el => el.dataset.cardId);
const stripFor = id => bucketRow.querySelector(`.card-strip[data-card-id="${id}"]`);

const cards = [
  {id: 'aaaaaaaa-0000', title: 'fix the login form', status: 'todo',
    description: 'GOAL:\n- nothing to do with search', comments: []},
  {id: 'bbbbbbbb-1111', title: 'unrelated card', status: 'doing',
    description: 'GOAL:\n- also unrelated', comments: [{author: 'operator', body: 'mentions filterword here'}]},
  {id: 'cccccccc-2222', title: 'third card', status: 'checking',
    description: 'GOAL:\n- plain', comments: []},
];
mod.renderBuckets(cards);
const ALL = cards.map(c => c.id);

// ---- what a card matches on: title, short id, description, comments and its column's name -------
assert.ok(mod.cardMatchesFilter(cards[0], 'login'), 'a title substring matches');
assert.ok(mod.cardMatchesFilter(cards[0], 'LOGIN'), 'the match is case-insensitive');
assert.ok(mod.cardMatchesFilter(cards[0], 'aaaaaaaa'), 'the short id matches');
assert.ok(!mod.cardMatchesFilter(cards[0], 'aaaaaaaa-0000'), 'the full id is not what the card shows');
assert.ok(mod.cardMatchesFilter(cards[0], 'nothing to do'), 'the description matches');
assert.ok(mod.cardMatchesFilter(cards[1], 'filterword'), 'a comment body (the timeline) matches');
assert.ok(mod.cardMatchesFilter(cards[1], 'doing'), "the card's own column name matches");
assert.ok(!mod.cardMatchesFilter(cards[2], 'doing'), "another column's name does not");
assert.ok(mod.cardMatchesFilter(cards[0], '   '), 'an empty query matches every card');

// ---- applying it: only the matches are drawn, the rest stay out, and nothing is asked of the server
const fetchesBefore = fetched.length;
mod.applyCardFilter('login');
assert.ok(stripFor('aaaaaaaa-0000'), 'a matching card stays on the board');
assert.equal(stripFor('bbbbbbbb-1111'), null, 'a card that does not match is not drawn');
assert.equal(stripFor('cccccccc-2222'), null, 'nor is the other one');
assert.equal(fetched.length, fetchesBefore, 'filtering is view-only: no request of its own');

mod.applyCardFilter('');
assert.ok(ALL.every(id => stripFor(id)), 'an empty query brings every card back');

// ---- a full redraw (a board switch, a reload) keeps the filter the operator typed ---------------
mod.applyCardFilter('login');
mod.renderBuckets(cards);
assert.equal(stripFor('bbbbbbbb-1111'), null, 'the filter survives a full redraw');
assert.ok(stripFor('aaaaaaaa-0000'), 'and its match is still drawn');
mod.applyCardFilter('');

// ---- cmd/ctrl+f focuses the filter instead of the browser's find --------------------------------
pressDoc({code: 'KeyF', metaKey: true});
assert.equal(document.getElementById('card-filter'), null, 'with no board on screen it leaves the browser its find');

mod.setCurrentBoardId('b1');
let prevented = false;
pressDoc({code: 'KeyF', metaKey: true, preventDefault() { prevented = true; }});
const filterInput = document.getElementById('card-filter');
assert.ok(filterInput, 'cmd+f builds the filter input');
assert.ok(filterInput.className.split(' ').includes('text-field'), "it is ui_base's own text field");
assert.equal(document.activeElement, filterInput, 'cmd+f focuses it');
assert.ok(prevented, "and keeps the browser's own find from opening");

// plain f is the fold question, never the filter
filterInput.blur();
pressDoc({code: 'KeyF'});
assert.notEqual(document.activeElement, filterInput, 'a bare f does not reach the filter');

pressDoc({code: 'KeyF', ctrlKey: true});
assert.equal(document.activeElement, filterInput, 'ctrl+f reaches the same input');

// an open card sits over the board: the browser keeps its find there
filterInput.blur();
mod.setOpenCard({cardId: 'aaaaaaaa-0000', expander: {close() {}}});
pressDoc({code: 'KeyF', metaKey: true});
assert.notEqual(document.activeElement, filterInput, 'cmd+f over an open card is left to the browser');
mod.setOpenCard(null);

// ---- typing filters live; escape clears it and hands the keyboard back to the board -------------
filterInput.focus();
filterInput.value = 'THIRD';
fireInput(filterInput);
assert.deepEqual(drawnIds('checking'), ['cccccccc-2222'], 'typing narrows the board as it goes');
assert.deepEqual(drawnIds('todo'), [], 'and takes out what no longer matches');

fireKeydownOn(filterInput, {code: 'Escape'});
assert.equal(filterInput.value, '', 'escape empties the filter');
assert.ok(ALL.every(id => stripFor(id)), 'and every card comes back');
assert.equal(document.activeElement, tab, 'focus goes back to the board bar');

// ---- a column keeps its order: the matches are drawn in the order they already stood ------------
const spread = ['x1', 'y2', 'x3'].map(id => ({id, title: `${id[0] === 'x' ? 'alpha' : 'beta'} ${id}`,
  status: 'todo', description: '', comments: []}));
mod.renderBuckets(spread);
mod.applyCardFilter('alpha');
assert.deepEqual(drawnIds('todo'), ['x1', 'x3'], 'the matches keep their order, the others are gone');

// ---- the arrow keys never land on a card the filter left out ------------------------------------
const x1 = stripFor('x1');
x1.focus();
bucketRow._listeners.keydown[0]({key: 'ArrowDown', target: x1, preventDefault() {}});
assert.equal(document.activeElement, stripFor('x3'), 'down from the first match lands on the next match');

// the poll's redraw (redrawColumns) goes through the same filter
mod.redrawColumns([{...spread[1], title: 'beta y2, still'}]);
assert.deepEqual(drawnIds('todo'), ['x1', 'x3'], 'a poll redraw keeps the filter');

// a card the filter left out has no strip, but a poll moving it still takes it out of its old column
mod.redrawColumns([{...spread[1], status: 'doing'}]);
const listed = status => rowsOf(status)._pile.sorted.map(c => c.id);
assert.deepEqual(listed('todo'), ['x1', 'x3'], 'the moved card leaves no copy behind in todo');
assert.deepEqual(listed('doing'), ['y2'], 'it is listed in doing, where it went');
assert.deepEqual(drawnIds('doing'), [], 'and stays undrawn there while it does not match');
mod.applyCardFilter('');
assert.deepEqual(drawnIds('doing'), ['y2'], 'clearing the filter draws it in its new column');

// ---- a dense column: a match that would sit in a pile is drawn where it can be seen -------------
const dense = Array.from({length: 30}, (_, i) => ({id: `d${i}`, title: i === 25 || i === 28 ? `needle ${i}` : `hay ${i}`,
  status: 'todo', description: '', comments: []}));
mod.renderBuckets(dense);
const piles = () => rowsOf('todo').children.filter(el => el.className.includes('card-pile'));
assert.ok(piles().length > 0, 'fixture: thirty cards pile up');
assert.ok(!drawnIds('todo').includes('d25'), 'fixture: card 25 starts inside a pile');
mod.applyCardFilter('needle');
assert.deepEqual(drawnIds('todo'), ['d25', 'd28'], 'both matches are drawn as cards');
assert.equal(piles().length, 0, 'with no pile left to hide them in');
mod.applyCardFilter('');

// ---- a fanned column's own arrow keys (handlePileKey) step over the cards left out ---------------
const fanned = Array.from({length: 10}, (_, i) => ({id: `f${i}`, title: i % 2 && i < 8 ? `skip ${i}` : `keep ${i}`,
  status: 'todo', description: '', comments: []}));
mod.renderBuckets(fanned);
mod.applyCardFilter('keep');
const todoRows = rowsOf('todo');
assert.ok(todoRows._pile.regime > 1, 'fixture: six matches still overlap, so the column drives its own arrows');
const first = stripFor('f0');
first.focus();
todoRows._listeners.keydown[0]({key: 'ArrowDown', target: first, preventDefault() {}, stopPropagation() {}});
assert.equal(document.activeElement?.dataset.cardId, 'f2', 'down from f0 skips f1, which the filter left out');
// the last match is the column's end, whatever the unfiltered list still holds past it
const last = stripFor('f9');
last.focus();
let stopped = false;
todoRows._listeners.keydown[0]({key: 'ArrowDown', target: last, preventDefault() {}, stopPropagation() { stopped = true; }});
assert.equal(stopped, false, 'down from the last match falls through to the board nav, like any column end');
mod.applyCardFilter('');

// ---- a jump to a card (the roster, the inbox's glance) is a request to see it: the filter gives way
mod.renderBuckets(spread);
filterInput.value = 'alpha';
fireInput(filterInput);
assert.equal(stripFor('y2'), null, 'fixture: the filter leaves y2 out');
await mod.jumpToCard('y2', [{card_id: 'y2', board_id: 'b1'}]);
assert.ok(stripFor('y2'), 'jumping to it clears the filter so its card is drawn');
assert.equal(filterInput.value, '', 'and the field says so');
assert.equal(mod.openCardRef()?.cardId, 'y2', 'the card it jumped to is the one opened');
mod.setOpenCard(null);

console.log('ok');
