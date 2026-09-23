// proves the open card asks its expander to hug its content, after the first layout and again on
// every re-layout (a fold opening), with the content's laid-out height
// run: node tests/js/card_panel_fit.mjs
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
;return {renderCardStrip, layoutCardSections};`)(SpyMenu, SpyDrawer);

const tick = () => new Promise(resolve => setTimeout(resolve, 0));

function section(name, height) {
  const el = element('div', 'card-section');
  el.dataset.section = name;
  el.offsetHeight = height;
  return el;
}

// ---- opening the strip hands the panel its own expander, whose fit the layout then calls
const strip = mod.renderCardStrip({id: 'c1', title: 'a card', status: 'todo', workstream: 'w'});
const expander = strip._expander;
const fits = [];
// the spy stands in for ui_base's fit: what it does with the height is ui_base's own test
expander.fit = height => fits.push(height);
expander.open();
const panel = document.body.querySelector('.expand-panel');
assert.ok(panel, 'the strip opened a panel');
assert.equal(panel._expander, expander, 'the panel carries the expander that opened it');
// the stub has no attachment of its own - openCardPanel bails on a closed panel
panel.isConnected = true;

// the stub does not parse cardPanelHtml back into a tree, so these hand-built sections are what
// the fetch-then-render in onOpen lays out. one column (500px), so every section stacks
const container = element('div', 'card-sections');
container.offsetWidth = 500;
// the stub does no layout: the container's height follows what layout wrote, as a browser's would
container.offsetTop = 0;
Object.defineProperty(container, 'offsetHeight', {get: () => parseFloat(container.style.height) || 0});
const title = section('title', 50);
const about = section('about', 100);
const details = section('details', 30);
[title, about, details].forEach(s => container.appendChild(s));
panel.appendChild(container);
await tick();

// 50 + 14 + 100 + 14 + 30, the container's own height once layout has stacked them
assert.equal(container.style.height, '208px', 'fixture: the layout stacked all three sections');
assert.deepEqual(fits, [208], 'the first layout fits the panel to the content it laid out');

// ---- a fold opening grows a section, the observer re-lays out, and the panel follows
details.offsetHeight = 130;
mod.layoutCardSections(panel);
assert.equal(container.style.height, '308px', 'fixture: the re-layout took the taller section');
assert.deepEqual(fits, [208, 308], 'every re-layout fits again, to the new height');

// ---- anything laid out above or below the sections counts too
const foot = element('div', 'panel-foot');
foot.offsetTop = 320;
foot.offsetHeight = 40;
panel.appendChild(foot);
mod.layoutCardSections(panel);
assert.equal(fits.at(-1), 360, 'from the first child\'s top to the last one\'s bottom');
foot.remove();

// ---- a panel no expander opened (the tests' hand-built ones) lays out without fitting
const bare = element('div', 'card-panel');
const bareSections = element('div', 'card-sections');
bareSections.offsetWidth = 500;
bareSections.appendChild(section('title', 50));
bare.appendChild(bareSections);
const before = fits.length;
mod.layoutCardSections(bare);
assert.equal(bareSections.style.height, '50px', 'fixture: the bare panel was laid out');
assert.equal(fits.length, before, 'and nothing was fitted');

// ---- close is untouched: the panel still collapses the way it always did
const backdrop = panel.parentNode;
expander.close();
assert.ok(backdrop.className.includes('expand-closing'), 'closing still collapses the panel');

console.log('ok');
