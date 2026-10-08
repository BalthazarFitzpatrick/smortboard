// proves the card overflow menu (...) wires delete/change-model/move-status without forking
// any of their logic, that delete always confirms first, and that each action also has its own
// keyboard shortcut scoped to whichever card is focused - or, for a menu pick, the card whose menu
// was actually opened, even if focus sits elsewhere. run: UI_BASE_ASSETS_DIR=<ui_base assets dir> node tests/js/card_overflow.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

// method-aware stub: cycleCardModel GETs the card then PATCHes it on the same path, so a
// path-only map (as card_panel.mjs uses) can't tell the two calls apart
const responses = new Map();
const calls = [];
function stubJson(status, body) {
  return {ok: status >= 200 && status < 300, status, json: async () => body};
}
function setResponse(method, path, status, body) {
  responses.set(`${method} ${path}`, stubJson(status, body));
}
function fetchStub(path, opts) {
  const method = (opts && opts.method) || 'GET';
  calls.push({path, opts: opts || {}});
  const resp = responses.get(`${method} ${path}`);
  return Promise.resolve(resp || stubJson(404, {error: `no stub for ${method} ${path}`}));
}
const flush = () => new Promise(r => setTimeout(r, 0));

installStubDom({fetchImpl: fetchStub});
// board.js calls loadBoards() at import time - see card_panel.mjs for why this stub exists
setResponse('GET', '/api/boards', 200, []);

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

// records every menu built, in order, and lets a test drive its onPick the way a click would
let menus = [];
class SpyMenu {
  constructor(opts) { this.opts = opts; this.el = element('div'); menus.push(this); }
  openAt() { return this; }
  refresh(sections) { this.opts.sections = sections; }
  close() {}
  pick(itemId) {
    const section = this.opts.sections.find(s => (s.items || []).some(i => i.id === itemId));
    section.onPick(section.items.find(i => i.id === itemId));
  }
}
function SpyDrawer() {
  return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false};
}

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), uiBase('pile.js'), smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {renderCardStrip, openMoveStatusMenu};`)(SpyMenu, SpyDrawer);

function press(code, target) {
  document._dispatch('keydown', {code, key: code, target: target || document.body, preventDefault() {}, stopPropagation() {}});
}
const backdrops = () => document.body.children.filter(c =>
  c.classList.contains('modal-backdrop') && !c.classList.contains('expand-closing'));

const todoRows = bucketRow.querySelector('.bucket[data-status="todo"] .bucket-rows');
const strip = mod.renderCardStrip({id: 'c1', title: 'card one', status: 'todo', workstream: 'w'});
todoRows.appendChild(strip);
setResponse('GET', '/api/cards/c1', 200, {id: 'c1', title: 'card one', status: 'todo', model: null,
  tasks: [], criteria: [], depends_on: [], attachments: [], comments: []});
setResponse('GET', '/api/cards/c1/outcome', 200, {});

// ---- every card carries its own ... trigger
const overflow = strip.querySelector('.card-overflow');
assert.ok(overflow, 'the strip should render a ... trigger');

let stopped = false;
overflow.onclick({stopPropagation: () => { stopped = true; }});
assert.equal(menus.length, 1, 'clicking ... should open exactly one menu');
assert.ok(stopped, 'the click must not bubble to the strip and also open the card behind the menu');
const overflowItems = menus[0].opts.sections[0].items.map(i => i.id);
assert.deepEqual(overflowItems, ['model', 'status', 'delete'],
  'the menu offers change model, move status and delete - no edit (the panel has no edit mode) and no complexity');

// ---- picking delete opens a confirm; nothing is deleted before that confirm is answered
menus = [];
overflow.onclick({stopPropagation(){}});
menus[0].pick('delete');
assert.equal(menus.length, 2, 'delete should open a second, confirming menu');
assert.equal(menus[1].opts.title, 'delete this card?');
// what must not have happened yet is a write
const writes = () => calls.filter(c => c.opts?.method && c.opts.method !== 'GET');
assert.equal(writes().length, 0, 'no request should fire before the confirm is answered');

// ---- "keep it" leaves the card alone
menus[1].pick('keep');
await flush();
assert.equal(writes().length, 0, "keep it should not call the api");

// ---- confirming actually deletes it
menus = [];
overflow.onclick({stopPropagation(){}});
menus[0].pick('delete');
menus[1].pick('delete');
await flush();
assert.ok(calls.some(c => c.path === '/api/cards/c1' && c.opts.method === 'DELETE'),
  'confirming delete should DELETE the card');

// ---- move status lists every column and greys out the one the card already sits in
menus = [];
mod.openMoveStatusMenu('c1');
const statusItems = menus[0].opts.sections[0].items;
assert.deepEqual(statusItems.map(i => i.id), ['todo', 'doing', 'checking', 'accepted', 'rejected']);
assert.equal(statusItems.find(i => i.id === 'todo').disabled, true,
  'the card\'s current column should be disabled, not offered as a destination');
assert.ok(statusItems.filter(i => i.id !== 'todo').every(i => !i.disabled));

// ---- picking a status writes it directly - a manual override, not the accept/reject gate
calls.length = 0;
setResponse('PATCH', '/api/cards/c1', 200, {});
menus[0].pick('checking');
await flush();
const moved = calls.find(c => c.path === '/api/cards/c1' && c.opts.method === 'PATCH');
assert.ok(moved, 'move status should PATCH the card');
assert.equal(JSON.parse(moved.opts.body).status, 'checking');

// ---- change model reuses cycleCardModel exactly, and acts on the card whose ... was clicked -
// even while a different card holds keyboard focus
const strip2 = mod.renderCardStrip({id: 'c2', title: 'card two', status: 'todo', workstream: 'w'});
todoRows.appendChild(strip2);
strip2.focus();
calls.length = 0;
setResponse('GET', '/api/cards/c1', 200, {id: 'c1', model: null, model_efforts: {'openai/x': 'low'}});
setResponse('GET', '/api/settings', 200, {});
setResponse('GET', '/api/catalog', 200, {
  anthropic: {available: false, unavailable_reason: 'no usable profile', models: [{id: 'haiku', label: 'Haiku', tier: 'light'}]},
  openai: {available: true, models: [{id: 'x', label: 'X', tier: 'standard'},
    {id: 'y', label: 'Y', tier: 'standard', effort_levels: ['low', 'high']},
    {id: 'z', label: 'Z', tier: 'light', effort_levels: []}]},
});
setResponse('PATCH', '/api/cards/c1', 200, {id: 'c1', lab: 'openai', model: 'x'});
menus = [];
overflow.onclick({stopPropagation(){}});
menus[0].pick('model');
await flush();
assert.equal(menus.length, 2, 'change model opens one model picker');
assert.equal(calls.filter(c => c.opts.method === 'PATCH').length, 0, 'no write before a model is selected');
assert.equal(menus[1].opts.title, 'choose model');
assert.ok(menus[1].el.classList.contains('menu-centered'), 'the card route centres the picker like every dialog');
let modelColumns = menus[1].opts.sections.find(section => section.kind === 'columns').columns;
assert.equal(modelColumns[0].items.find(item => item.id === 'anthropic').disabled, true,
  'a lab without a usable profile cannot be selected');
modelColumns[0].onPick({id: 'openai'});
assert.equal(menus.length, 2, 'picking a lab updates the same menu');
modelColumns = menus[1].opts.sections.find(section => section.kind === 'columns').columns;
const effortOf = () => {
  modelColumns = menus[1].opts.sections.find(section => section.kind === 'columns').columns;
  return {levels: modelColumns[2].items.map(item => item.id), on: modelColumns[2].items.find(item => item.on)?.id};
};
modelColumns[1].onPick({id: 'x'});
assert.deepEqual(effortOf(), {levels: ['low', 'medium', 'high'], on: 'low'},
  'there is no default row, and a model this card chose an effort for opens on that choice');
assert.equal(modelColumns[2].label, 'effort', 'effort is the third column beside lab and model');
modelColumns[1].onPick({id: 'y'});
assert.deepEqual(effortOf(), {levels: ['low', 'high'], on: 'low'},
  "a model never chosen for starts at its first level when it has no medium, not on x's choice");
modelColumns[2].onPick({id: 'high'});
modelColumns[1].onPick({id: 'z'});
assert.deepEqual(effortOf(), {levels: [], on: undefined}, 'a model with no levels has no effort to set');
modelColumns[1].onPick({id: 'y'});
assert.equal(effortOf().on, 'high', 'an effort picked for y is still there after visiting another model');
modelColumns[1].onPick({id: 'x'});
assert.equal(effortOf().on, 'low', 'going back to x finds the effort the card chose for x');
// the cursor on a model shows that model's efforts without picking it; right only moves now
modelColumns[1].onFocus({id: 'y'});
assert.deepEqual(effortOf(), {levels: ['low', 'high'], on: undefined},
  'efforts of the model under the cursor, none lit until one is chosen');
// an effort chosen there picks that model too
modelColumns[2].onPick({id: 'high'});
assert.deepEqual(effortOf(), {levels: ['low', 'high'], on: 'high'}, 'the effort picks its model');
assert.ok(modelColumns[1].items.find(item => item.id === 'y').on, 'and the model is now the picked one');
assert.equal(menus[1].opts.sections.find(section => section.kind === 'buttons').buttons
  .find(button => button.id === 'save-model').primary, true, 'enter confirms through the save button');
modelColumns[2].onPick({id: 'high'});
assert.equal(calls.filter(c => c.opts.method === 'PATCH').length, 0,
  'selecting a model waits for the save action');
const saveModel = menus[1].opts.sections.find(section => section.kind === 'buttons')
  .buttons.find(button => button.id === 'save-model');
saveModel.onClick(menus[1]);
await flush();
const modelPatch = calls.find(c => c.path === '/api/cards/c1' && c.opts.method === 'PATCH');
assert.deepEqual(JSON.parse(modelPatch.opts.body), {lab: 'openai', model: 'y', effort: 'high'},
  "the picker patches the card's model ref and its effort");
assert.ok(!calls.some(c => c.path.startsWith('/api/cards/c2')), 'and never touch the focused card instead');

// ---- e, j and del retired into the menu: m is the only key, and every action is still two
// presses away through it
strip.focus();
menus = [];
press('KeyE');
press('KeyJ');
press('Delete');
assert.equal(menus.length, 0, 'e, j and del are not bindings any more');
assert.equal(backdrops().length, 0, 'and e no longer opens the focused card');

menus = [];
press('KeyM');
assert.equal(menus.length, 1, 'm opens the focused card\'s menu');
assert.equal(menus[0].opts.title, 'card actions');
menus[0].pick('delete');
assert.equal(menus.length, 2, 'delete from the menu still asks first');
assert.equal(menus[1].opts.title, 'delete this card?');
menus[1].pick('keep'); // leave the card in place for anything else that runs after this file

menus = [];
press('KeyM');
menus[0].pick('status');
assert.equal(menus[1].opts.title, 'move to', 'move to opens as the menu\'s own submenu');

console.log('ok');
