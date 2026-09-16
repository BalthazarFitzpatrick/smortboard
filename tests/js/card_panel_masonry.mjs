// proves the card panel's sections stack by their own measured height, per column, instead of a
// css-grid row snapping every column to the tallest cell in it - see layoutCardSections in board.js
// run: node tests/js/card_panel_masonry.mjs
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
function fetchStub(path) {
  const resp = responses.get(path);
  return Promise.resolve(resp || stubJson(404, {error: 'no stub for ' + path}));
}

installStubDom({fetchImpl: fetchStub});
// board.js calls loadBoards() at import time - an empty board list keeps that startup call quiet
responses.set('/api/boards', stubJson(200, []));

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

class SpyMenu { constructor(opts) { this.opts = opts; } openAt() { return this; } close() {} }
function SpyDrawer() { return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false}; }

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), uiBase('pile.js'), smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {computeMasonryLayout, layoutCardSections, watchCardSections};`)(SpyMenu, SpyDrawer);

// ---- computeMasonryLayout is pure: no dom, just heights in, positions out --------------------

// three column items after one full-width band: the third one must land right under the shorter
// of the first two, not wait for the taller one - that wait is the blank gap this replaces
const afterBand = mod.computeMasonryLayout(
  [{full: false, height: 100}, {full: false, height: 20}, {full: false, height: 15}],
  2, 10,
);
assert.deepEqual([afterBand[0].column, afterBand[1].column], [0, 1], 'the first two items fill each column once');
assert.equal(afterBand[0].top, 0, 'the first column item starts at the top of its column');
assert.equal(afterBand[1].top, 0, 'the second column item starts level with the first - both columns began even');
assert.equal(afterBand[2].column, 1, 'the third item goes to the shorter column');
assert.equal(afterBand[2].top, 30, 'it lands right under that column\'s own item (20 + 10 gap), not under the taller one');

// a full-width item syncs every column back to the same reach before the next item is placed
const withBand = mod.computeMasonryLayout(
  [{full: false, height: 100}, {full: false, height: 20}, {full: true, height: 5}, {full: false, height: 1}],
  2, 10,
);
assert.equal(withBand[2].top, 110, 'the band starts at the taller column\'s reach (100 + 10), not the shorter one\'s');
assert.equal(withBand[3].top, 125, 'a column item after the band starts level with the band\'s own bottom (110 + 5 + 10)');

// ---- layoutCardSections wires that into the dom: width lands before height is measured, and the
// container is sized to what was actually used, not a fixed row grid ---------------------------

function makeSection(name, height) {
  const el = element('div', 'card-section');
  el.dataset.section = name;
  el.getBoundingClientRect = () => ({left: 0, top: 0, right: 0, bottom: 0, width: 0, height});
  return el;
}

function buildPanel(containerWidth) {
  const panel = element('div', 'card-panel');
  const sections = element('div', 'card-sections');
  sections.getBoundingClientRect = () => ({left: 0, top: 0, right: 0, bottom: 0, width: containerWidth, height: 0});
  panel.appendChild(sections);
  document.body.appendChild(panel);
  return {panel, sections};
}

// wide enough for two columns (>= the 760px fold point)
const {panel, sections} = buildPanel(800);
const title = makeSection('title', 50);
const status = makeSection('status', 30);
const workstream = makeSection('workstream', 80);
const outcome = makeSection('outcome', 20);
const tasks = makeSection('tasks', 100);
const deps = makeSection('deps', 20);
const comments = makeSection('comments', 15);
[title, status, workstream, outcome, tasks, deps, comments].forEach(s => sections.appendChild(s));

mod.layoutCardSections(panel);

assert.equal(title.style.width, '100%', 'title spans the full width');
assert.equal(status.style.width, '380px', 'a column section is sized to its own column, not the full width');
assert.equal(status.style.top, '80px', 'status starts right under title (50 + 30 gap)');
assert.equal(status.style.left, '0px', 'status sits in the first column');
assert.equal(workstream.style.top, '80px', 'workstream starts level with status - both columns began even after title');
assert.equal(workstream.style.left, '420px', 'workstream sits in the second column (380 + 40 gap)');
assert.equal(outcome.style.top, '190px', 'outcome starts at the taller of the two columns\' reach (workstream: 80 + 80 + 30)');
assert.equal(tasks.style.top, '240px', 'tasks starts right under outcome (190 + 20 + 30)');
assert.equal(deps.style.top, '240px', 'deps starts level with tasks - both columns synced by outcome');
assert.equal(comments.style.top, '290px',
  'comments (the third item after the band) stacks right under deps (240 + 20 + 30), not under tasks - no shared-row wait');
assert.equal(comments.style.left, '420px', 'comments stays in the shorter column, not wherever the row model would have put it');
assert.equal(sections.style.height, '340px', 'the container is sized to the tallest column\'s actual reach, minus the trailing gap');

// ---- reflow on resize: a narrower width folds to one column and every section restacks in order
sections.getBoundingClientRect = () => ({left: 0, top: 0, right: 0, bottom: 0, width: 500, height: 0});
mod.layoutCardSections(panel);
assert.equal(status.style.width, '100%', 'a narrow panel makes every section full width');
assert.equal(status.style.top, '80px', 'status still follows title directly');
assert.equal(workstream.style.top, '140px', 'workstream now queues after status instead of sitting beside it (80 + 30 + 30)');
assert.equal(workstream.style.left, '0px', 'a single column has nothing to sit beside');
assert.equal(comments.style.top, '480px', 'the single column keeps stacking every section in document order');

// ---- reorder and remove: layoutCardSections reads the dom fresh every call, not a cached order
sections.getBoundingClientRect = () => ({left: 0, top: 0, right: 0, bottom: 0, width: 800, height: 0});
deps.remove();
sections.appendChild(deps); // move deps to the end, after comments
mod.layoutCardSections(panel);
assert.equal(deps.style.top, '285px', 'a reordered section is measured in its new document position');
assert.equal(deps.style.left, '420px', 'reordering can change which column a section lands in');

// ---- a container measured before it has a real box (width 0) is left alone rather than handed a
// negative column width - (0 - gap) / columns went negative here before the guard, squeezing every
// section to nothing instead of waiting for a later call with a real measurement
sections.getBoundingClientRect = () => ({left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0});
const beforeZeroWidth = status.style.width;
mod.layoutCardSections(panel);
assert.equal(status.style.width, beforeZeroWidth, 'a zero-width read should not touch section styles');

// ---- a panel laid out before it had a box is laid out again once it gets one: a resize observer
// re-runs layout when the container's width lands or a section's height changes, and a closed
// panel stops being watched
const observed = [];
let fire = null;
globalThis.ResizeObserver = class {
  constructor(callback) { fire = callback; }
  observe(el) { observed.push(el); }
  disconnect() { observed.length = 0; }
};
panel.isConnected = true;
status.style.top = '';
mod.watchCardSections(panel);
assert.ok(observed.includes(sections), 'the container is watched for its width');
assert.ok(observed.includes(status), 'each section is watched for its height');
sections.getBoundingClientRect = () => ({left: 0, top: 0, right: 0, bottom: 0, width: 800, height: 0});
fire([]);
assert.equal(status.style.top, '80px', 'the observer lays the panel out once it has a real width');
panel.isConnected = false;
fire([]);
assert.equal(observed.length, 0, 'a closed panel stops being watched');
delete globalThis.ResizeObserver;

// ---- the design archive's fifteen panel-layout-N variants restyle .card-sections back to a normal
// flow list (panel-layouts.css) - masonry's position: absolute must stay scoped away from them, or
// every archived section collapses to the same top-left point regardless of that flow. layout.css
// is asserted on as text, the same way this suite already asserts on cardPanelHtml's raw markup:
// there is no css engine here to resolve the cascade against
const css = readFileSync(new URL('smortboard/ui/layout.css', root), 'utf8');
assert.ok(/\.card-panel:not\(\[class\*="panel-layout-"\]\)\s*\.card-section\s*\{[^}]*position:\s*absolute/.test(css),
  'masonry\'s absolute positioning should be scoped off the design archive panels');
assert.ok(!/(^|\s)\.card-panel \.card-section\s*\{[^}]*position:\s*absolute/m.test(css),
  'an unscoped .card-panel .card-section rule should not itself set position: absolute');

console.log('ok');
