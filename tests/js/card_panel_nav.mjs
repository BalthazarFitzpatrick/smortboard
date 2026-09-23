// proves the open card's arrow keys follow its masonry columns as drawn: up/down stay in a column,
// left/right cross to the section beside, a full-width band belongs to every column, and a panel
// re-rendered after a comment never answers one press twice. run: node tests/js/card_panel_nav.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const responses = new Map();
function stubJson(status, body) {
  return {ok: status >= 200 && status < 300, status, json: async () => body};
}
installStubDom({fetchImpl: path => Promise.resolve(responses.get(path) || stubJson(404, {error: path}))});
// board.js calls loadBoards() at import time - an empty board list keeps that startup call quiet
responses.set('/api/boards', stubJson(200, []));
responses.set('/api/cards/c1', stubJson(200, {id: 'c1', title: 'a card', status: 'todo',
  tasks: [], criteria: [], comments: [], leases: []}));
responses.set('/api/cards/c1/outcome', stubJson(200, null));

const boardBar = element('div', 'board-bar');
boardBar.id = 'board-bar';
const bucketRow = element('div', 'bucket-row');
bucketRow.id = 'bucket-row';
document.body.appendChild(boardBar);
document.body.appendChild(bucketRow);

class SpyMenu { constructor(opts) { this.opts = opts; } openAt() { return this; } close() {} }
function SpyDrawer() { return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false}; }

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'),
  uiBase('pile.js'), uiBase('entrytext.js'), smort('columns.js'), smort('card_panel.js'),
  smort('chat.js'), smort('shortcuts.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {computeMasonryLayout, computeSectionMove, readSectionPlaces, layoutCardSections,
  openCardPanel};`)(SpyMenu, SpyDrawer);

// ---- computeSectionMove is pure: placements in, the landing index out ---------------------------

// title spans both columns; A and C stack in the left column, B sits beside them on the right
const GAP = 30;
const placed = mod.computeMasonryLayout(
  [{full: true, height: 50}, {full: false, height: 40}, {full: false, height: 100}, {full: false, height: 60}],
  2, GAP,
);
assert.deepEqual(placed.map(p => p.column), [null, 0, 1, 0], 'fixture: title full, A left, B right, C left');
const [TITLE, A, B, C] = [0, 1, 2, 3];
const move = (from, dir) => mod.computeSectionMove(placed, from, dir);

assert.equal(move(TITLE, 'down'), A, 'down from the full-width title lands on the first column item');
assert.equal(move(A, 'right'), B, 'right from A crosses to B beside it');
assert.equal(move(A, 'down'), C, 'down from A stays in its own column, skipping B');
assert.equal(move(B, 'left'), A, 'left from B takes A, which overlaps it more than C does');
assert.equal(move(A, 'up'), TITLE, 'up from A reaches the band, which belongs to every column');
assert.equal(move(B, 'up'), TITLE, 'and so does up from the right column');
assert.equal(move(C, 'up'), A, 'up from C walks back up the left column');
assert.equal(move(C, 'right'), B, 'right from C lands on B, the only section beside it');

// edges: nothing that way is no move at all
assert.equal(move(TITLE, 'up'), TITLE, 'up from the top stays');
assert.equal(move(C, 'down'), C, 'down from the last section in a column stays');
assert.equal(move(B, 'down'), B, 'down from the last section on the right stays');
assert.equal(move(A, 'left'), A, 'left from the first column stays');
assert.equal(move(B, 'right'), B, 'right from the last column stays');
assert.equal(move(TITLE, 'left'), TITLE, 'left from a full-width band is a no-op');
assert.equal(move(TITLE, 'right'), TITLE, 'right from a full-width band is a no-op');

// a tie on overlap goes to the upper section
const tied = [{column: 0, top: 0, bottom: 100}, {column: 1, top: 50, bottom: 150}, {column: 1, top: -50, bottom: 50}];
assert.equal(mod.computeSectionMove(tied, 0, 'right'), 2, 'equal overlap resolves to the upper section');

// a section in the band below never counts as beside one above it
const banded = mod.computeMasonryLayout(
  [{full: false, height: 40}, {full: true, height: 20}, {full: false, height: 40}, {full: false, height: 40}], 2, GAP);
assert.equal(mod.computeSectionMove(banded, 0, 'right'), 0, 'nothing beside the lone section above the band');
assert.equal(mod.computeSectionMove(banded, 2, 'right'), 3, 'the band below has its own neighbour');

// one column: every section is full width, so left/right never move and up/down walk the list
const single = mod.computeMasonryLayout([{full: true, height: 50}, {full: true, height: 40}, {full: true, height: 60}], 1, GAP);
assert.equal(mod.computeSectionMove(single, 1, 'left'), 1, 'one column: left is a no-op');
assert.equal(mod.computeSectionMove(single, 1, 'right'), 1, 'one column: right is a no-op');
assert.equal(mod.computeSectionMove(single, 1, 'down'), 2, 'one column: down is the next section');
assert.equal(mod.computeSectionMove(single, 1, 'up'), 0, 'one column: up is the previous section');

// ---- the panel: layoutCardSections writes the placement down, the arrow keys read it back -------

function section(name, height) {
  const el = element('div', 'card-section focus-glow focus-glow-soft');
  el.dataset.section = name;
  el.offsetHeight = height;
  el.scrolled = [];
  el.scrollIntoView = opts => el.scrolled.push(opts);
  return el;
}

const panel = element('div', 'card-panel');
panel.isConnected = true; // the stub has no attachment of its own - openCardPanel bails on a closed panel
const container = element('div', 'card-sections');
container.offsetWidth = 800; // two columns (>= the 760px fold point)
const title = section('title', 50);
// tall enough that B's overlap with A beats its overlap with C at the 14px gap
const a = section('workstream', 60);
const b = section('status', 100);
const c = section('description', 60);
const d = section('comments', 30);
const comment = element('input', 'comment-input text-field');
d.appendChild(comment);
[title, a, b, c, d].forEach(s => container.appendChild(s));
panel.appendChild(container);
document.body.appendChild(panel);

// the stub does not parse cardPanelHtml back into a tree, so these hand-built sections are what
// openCardPanel lays out and wires
await mod.openCardPanel(panel, 'c1');

assert.equal(title.dataset.column, 'all', 'a full-width section is recorded as spanning every column');
assert.equal(a.dataset.column, '0', 'a column section records its column');
assert.equal(a.dataset.top, '64', 'and its top (title 50 + the 14 gap)');
assert.equal(b.dataset.column, '1', 'the second section lands in the right column');
assert.equal(c.dataset.column, '0', 'the third one under the shorter left column');
assert.equal(document.activeElement, title, 'the open card focuses its first section');

function press(code, extra = {}) {
  let prevented = false;
  const evt = {code, target: document.activeElement, preventDefault() { prevented = true; },
    stopPropagation() {}, ...extra};
  (panel._listeners.keydown || []).forEach(fn => fn(evt));
  return prevented;
}

assert.equal(press('ArrowDown'), true, 'a handled arrow is prevented');
assert.equal(document.activeElement, a, 'down from the title lands on A');
assert.equal(a.tabIndex, 0, 'the focused section is the one in the tab order');
assert.equal(title.tabIndex, -1, 'and the one it left is not');
assert.deepEqual(a.scrolled, [{block: 'nearest'}], 'the landing section is scrolled into view');
press('ArrowRight');
assert.equal(document.activeElement, b, 'right from A lands on B');
press('ArrowLeft');
assert.equal(document.activeElement, a, 'left from B lands back on A');
press('ArrowDown');
assert.equal(document.activeElement, c, 'down from A stays in the left column');
press('ArrowUp');
press('ArrowUp');
assert.equal(document.activeElement, title, 'up twice from C walks back to the title');
assert.equal(press('ArrowUp'), false, 'an arrow with nowhere to go is left to the browser');
assert.equal(document.activeElement, title, 'and moves nothing');
assert.equal(press('ArrowLeft'), false, 'left from the full-width title is not handled');

// the comment box keeps its own arrows, and a held modifier is the browser's
comment.focus();
assert.equal(press('ArrowLeft'), false, 'left inside the comment box moves the caret, not the section');
assert.equal(document.activeElement, comment, 'the caret stays in the box');
title.focus();
assert.equal(press('ArrowDown', {metaKey: true}), false, 'cmd+down is left to the browser');
assert.equal(document.activeElement, title, 'and moves nothing');
assert.equal(press('KeyJ'), false, 'every other key passes straight through');

// ---- a narrow panel folds to one column: left/right go nowhere, up/down walk document order ---
container.offsetWidth = 500;
mod.layoutCardSections(panel);
title.focus();
press('ArrowDown');
press('ArrowDown');
assert.equal(document.activeElement, b, 'one column: down walks the sections in document order');
assert.equal(press('ArrowRight'), false, 'one column: right is not handled');
assert.equal(press('ArrowLeft'), false, 'one column: left is not handled');
assert.equal(document.activeElement, b, 'and focus stays put');

// ---- a panel the masonry never placed (the design archive) walks in document order ------------
const unplaced = [element('div', 'card-section'), element('div', 'card-section')];
assert.deepEqual(mod.readSectionPlaces(unplaced).map(p => [p.column, p.top]), [[null, 0], [null, 1]],
  'no recorded placement reads as one column in document order');

// ---- a posted comment re-renders into the same panel: one press must still move exactly once ----
container.offsetWidth = 800;
const listenersBefore = panel._listeners.keydown.length;
await mod.openCardPanel(panel, 'c1');
assert.equal(panel._listeners.keydown.length, listenersBefore, 're-rendering the panel adds no second listener');
let focusCalls = 0;
const focusA = a.focus;
a.focus = () => { focusCalls += 1; focusA(); };
assert.equal(document.activeElement, title, 'the re-render focuses the first section again');
press('ArrowDown');
assert.equal(document.activeElement, a, 'down still lands on A after the re-render');
assert.equal(focusCalls, 1, 'and A was focused once, not once per render');

console.log('ok');
