// boards and repos (b): b opens/closes the panel (not a Menu), creating a board switches to it and
// refreshes the bar without a reload, registering a repo refreshes the repo list, a refusal renders
// inline, and typing in any of the panel's inputs never fires a board shortcut.
// run: node tests/js/boards.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => readFileSync(new URL(`../smortui/ui_base/assets/${p}`, root), 'utf8');
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

// method-aware stub map, since GET and POST both hit "/api/boards" - a single path->response map
// (as inbox.mjs uses) cannot tell them apart
const responses = new Map();
const calls = [];
function stubJson(status, body) {
  return {ok: status >= 200 && status < 300, status, json: async () => body};
}
function key(path, method) { return `${method || 'GET'} ${path}`; }
function stub(path, method, status, body) { responses.set(key(path, method), stubJson(status, body)); }
function fetchStub(path, opts) {
  calls.push({path, opts});
  const resp = responses.get(key(path, opts && opts.method));
  return Promise.resolve(resp || stubJson(404, {error: 'no stub for ' + key(path, opts && opts.method)}));
}

installStubDom({fetchImpl: fetchStub});
// indicateFocus (ui_base/indicate.js) reads the motion tokens off computed style when it moves the
// focus ring onto the active board tab - real only in a browser, so stub the bit board.js needs
globalThis.getComputedStyle = () => ({transform: 'none', getPropertyValue: () => ''});

const BOARD_A = {id: 'b1', name: 'alpha', position: 0, created_at: 't'};
const BOARD_B = {id: 'b2', name: 'beta', position: 1, created_at: 't2'};
stub('/api/boards', 'GET', 200, [BOARD_A]);
stub('/api/boards/b1/cards', 'GET', 200, []);
stub('/api/boards/b1/repos', 'GET', 200, []);

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

function SpyDrawer() {
  return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false};
}
let lastMenu = null;
class SpyMenu {
  constructor(opts) { this.opts = opts; lastMenu = this; }
  openAt() { return this; }
  refresh(sections) { this.opts.sections = sections; }
  close() { this.closed = true; }
}

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js'), smort('boards.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {toggleBoardsPanel, openBoardsPanel, closeBoardsPanel, bp, BINDINGS, boardsRef: () => boards};`)(SpyMenu, SpyDrawer);

const flush = () => new Promise(r => setTimeout(r, 0));
function press(code, target) {
  document._dispatch('keydown', {code, key: code, target: target || document.body, preventDefault() {}});
}
function fireKeydown(el, evt) {
  const full = {target: el, preventDefault() {}, stopPropagation() {}, ...evt};
  (el._listeners.keydown || []).forEach(fn => fn(full));
}

await flush(); await flush(); // loadBoards().then(...) at the bottom of board.js

// ---- b is in the panels group, and b opens/closes the panel (not a Menu) --------------------------
assert.ok(mod.BINDINGS.some(b => b.code === 'KeyB' && b.group === 'panels'), 'b must be a panels binding');
press('KeyB');
await flush(); await flush();
assert.ok(mod.bp.backdrop.parentNode, 'the panel is attached once opened');
assert.equal(mod.bp.boardListEl.querySelectorAll('.board-row').length, 1, 'the one existing board renders');
assert.equal(mod.bp.boardListEl.querySelector('.board-name').textContent, 'alpha');
assert.ok(mod.bp.boardListEl.querySelector('.board-row').classList.contains('on'),
  'the open board carries the opened treatment');
assert.equal(mod.bp.repoListEl.querySelectorAll('.repo-row').length, 0, 'no repos yet on this board');

// ---- typing in the board-name input never fires a board shortcut ----------------------------------
mod.bp.boardNameInput.value = 'beta';
press('KeyB', mod.bp.boardNameInput);
assert.ok(mod.bp.backdrop.parentNode, 'b typed into the board-name input must not toggle the panel');

// ---- creating a board switches to it and refreshes the board bar, without a page reload -----------
stub('/api/boards', 'POST', 201, BOARD_B);
stub('/api/boards', 'GET', 200, [BOARD_A, BOARD_B]); // what loadBoards() re-fetches after create
stub('/api/boards/b2/cards', 'GET', 200, []);
stub('/api/boards/b2/repos', 'GET', 200, []);
fireKeydown(mod.bp.boardNameInput, {code: 'Enter', key: 'Enter'});
await flush(); await flush(); await flush();
const postCall = calls.find(c => c.path === '/api/boards' && c.opts && c.opts.method === 'POST');
assert.ok(postCall, 'enter in the board-name input should POST /api/boards');
assert.deepEqual(JSON.parse(postCall.opts.body), {name: 'beta'});
assert.equal(mod.boardsRef().length, 2, 'the board bar list now has both boards');
assert.equal(mod.bp.boardListEl.querySelectorAll('.board-row').length, 2, 'the panel list also refreshed');
assert.equal(mod.bp.boardNameInput.value, '', 'the name input clears after a successful create');
const [rowA, rowB] = [...mod.bp.boardListEl.querySelectorAll('.board-row')];
assert.ok(!rowA.classList.contains('on'), 'the opened treatment leaves the old board');
assert.ok(rowB.classList.contains('on'), 'the opened treatment moves to the newly created board');

// ---- registering a repo refreshes the repo list under the current board ---------------------------
Object.keys(mod.bp.repoFields).forEach(k => { mod.bp.repoFields[k].value = ''; });
mod.bp.repoFields.name.value = 'smortboard';
mod.bp.repoFields.path.value = '/repo';
mod.bp.repoFields.default_branch.value = 'main';
const NEW_REPO = {id: 'r1', board_id: 'b2', name: 'smortboard', path: '/repo', default_branch: 'main',
  test_command: null, image: null, lint_command: null};
stub('/api/boards/b2/repos', 'POST', 201, NEW_REPO);
stub('/api/boards/b2/repos', 'GET', 200, [NEW_REPO]); // re-fetched by onBoardEnter after register
fireKeydown(mod.bp.repoFields.path, {code: 'Enter', key: 'Enter'});
await flush(); await flush(); await flush();
const repoPostCall = calls.find(c => c.path === '/api/boards/b2/repos' && c.opts && c.opts.method === 'POST');
assert.ok(repoPostCall, 'enter in a repo field should POST the repo');
assert.equal(mod.bp.repoListEl.querySelectorAll('.repo-row').length, 1, 'the new repo renders without a reload');
assert.equal(mod.bp.repoListEl.querySelector('.repo-name').textContent, 'smortboard');
assert.ok(mod.bp.repoListEl.querySelector('.repo-row').classList.contains('on'),
  'a repo on the open board carries the opened treatment too');

// ---- a refusal renders inline instead of clearing the form -----------------------------------------
stub('/api/boards/b2/repos', 'POST', 400, {error: 'path does not exist: /nope'});
mod.bp.repoFields.path.value = '/nope';
fireKeydown(mod.bp.repoFields.path, {code: 'Enter', key: 'Enter'});
await flush(); await flush();
assert.equal(mod.bp.repoStatusEl.textContent, 'path does not exist: /nope');
assert.ok(mod.bp.repoStatusEl.className.includes('repo-error'));

// ---- escape from a repo field closes the panel, b again reopens it --------------------------------
fireKeydown(mod.bp.repoFields.path, {code: 'Escape', key: 'Escape'});
assert.equal(mod.bp.backdrop.parentNode, null, 'escape from a repo field closes the panel');
press('KeyB');
await flush(); await flush();
assert.ok(mod.bp.backdrop.parentNode, 'b reopens the panel');
press('KeyB');
assert.equal(mod.bp.backdrop.parentNode, null, 'b again closes it');

// ---- the first row offers three sources; from local repo browses folders and creates from a repo --
press('KeyB');
await flush(); await flush();
const sources = [...mod.bp.sourceRowEl.children];
assert.deepEqual(sources.map(s => s.textContent), ['new board', 'from local repo', 'from online repo']);
assert.ok(sources[2].className.includes('disabled'), 'from online repo is not built yet');
stub('/api/folders', 'GET', 200, {here: '/home/me', parent: '/home', repo: false,
  folders: [{name: 'proj', path: '/home/me/proj', repo: true}]});
sources[1].onclick();
await flush(); await flush();
assert.equal(lastMenu.opts.title, 'board from local repo');
const listOf = () => lastMenu.opts.sections.find(s => s.kind === 'list');
assert.deepEqual(listOf().items.map(i => i.label), ['..', 'proj/  git']);
assert.ok(!lastMenu.opts.sections.some(s => s.kind === 'buttons'), 'no create button outside a repo');
stub('/api/folders?under=%2Fhome%2Fme%2Fproj', 'GET', 200,
  {here: '/home/me/proj', parent: '/home/me', repo: true, folders: []});
await listOf().onPick({id: '/home/me/proj'});
await flush();
const create = lastMenu.opts.sections.find(s => s.kind === 'buttons');
assert.ok(create, 'inside a repo the create button appears');
const BOARD_C = {id: 'b3', name: 'proj', position: 2, created_at: 't3'};
stub('/api/boards/from-repo', 'POST', 201, {board: BOARD_C, repo: {id: 'r2', board_id: 'b3', name: 'proj',
  path: '/home/me/proj', default_branch: 'main', test_command: null, image: null, lint_command: null}});
stub('/api/boards', 'GET', 200, [BOARD_A, BOARD_B, BOARD_C]);
stub('/api/boards/b3/cards', 'GET', 200, []);
stub('/api/boards/b3/repos', 'GET', 200, []);
await create.buttons[0].onClick(lastMenu);
await flush(); await flush();
const fromRepoCall = calls.find(c => c.path === '/api/boards/from-repo');
assert.deepEqual(JSON.parse(fromRepoCall.opts.body), {path: '/home/me/proj'});
assert.ok(lastMenu.closed, 'the folder menu closes once the board exists');
assert.equal(mod.boardsRef().length, 3, 'the new board is in the bar without a reload');
press('KeyB');

console.log('ok');
