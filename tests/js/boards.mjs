// boards and repos (b): b opens/closes the panel (not a Menu), creating a board switches to it and
// refreshes the bar without a reload, registering a repo refreshes the repo list, a refusal renders
// inline, and typing in any of the panel's inputs never fires a board shortcut. new board picks any
// folder or names a new one, a folder with files asks before its first commit, and main's one push
// is shown to the operator. a new repo's tests come back as one status line, or as a notice held on
// its row when it has none. from online repo lists what gh sees, then clones into a picked folder.
// run: node tests/js/boards.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
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

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), uiBase('pile.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js'), smort('boards.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {toggleBoardsPanel, openBoardsPanel, closeBoardsPanel, bp, BINDINGS, boardsRef: () => boards,
  createBoardFromFolder, renderRepoList, mc};`)(SpyMenu, SpyDrawer);

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
assert.ok(mod.bp.boardListEl.querySelector('.board-row .board-name').classList.contains('on'),
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
assert.ok(!rowA.querySelector('.board-name').classList.contains('on'), 'the opened treatment leaves the old board');
assert.ok(rowB.querySelector('.board-name').classList.contains('on'), 'the opened treatment moves to the newly created board');

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

// ---- a repo row says where it pushes and what each field is; a blank field is not a mystery -----
{
  const row = mod.bp.repoListEl.querySelector('.repo-row');
  const origin = row.querySelector('.repo-origin');
  assert.ok(origin.className.includes('repo-origin-missing'), 'no origin is flagged');
  assert.ok(origin.textContent.includes('no origin'), origin.textContent);
  const labels = row.querySelector('.repo-edit-grid').querySelectorAll('.field-label').map(l => l.textContent);
  assert.deepEqual(labels, ['tests', 'lint', 'image'], 'every field carries its label');
  assert.ok(row.querySelector('.repo-edit-lint'), 'lint is editable on the row');
  assert.equal(row.querySelector('.repo-edit-save').tag, 'button', 'save is a real button');
}
// the manual form waits behind its button: new board and from online repo are the way in
{
  const manual = mod.bp.panel.querySelector('.repo-manual');
  assert.equal(manual.hidden, true, 'adding a repo by hand is folded away');
  const toggle = mod.bp.panel.querySelector('.repo-manual-toggle');
  toggle.onclick();
  assert.equal(manual.hidden, false, 'and opens on its button');
  toggle.onclick();
  assert.equal(manual.hidden, true);
}
// ---- the panel is walkable: up from the name field reaches the boards and new board ----------------
{
  const sources = [...mod.bp.sourceRowEl.children];
  assert.ok(sources.every(s => s.tag === 'button'), 'new board and from online repo are real buttons');
  mod.bp.boardNameInput.focus();
  const seen = [];
  for (let i = 0; i < 8; i++) {
    press('ArrowUp', document.activeElement);
    seen.push(document.activeElement);
  }
  assert.ok(seen.some(el => (el.className || '').includes('board-name')), 'up reaches a board');
  assert.ok(seen.includes(sources[0]), 'and on up to new board');
}

// ---- a refusal renders inline instead of clearing the form -----------------------------------------
stub('/api/boards/b2/repos', 'POST', 400, {error: 'path does not exist: /nope'});
mod.bp.repoFields.path.value = '/nope';
fireKeydown(mod.bp.repoFields.path, {code: 'Enter', key: 'Enter'});
await flush(); await flush();
assert.equal(mod.bp.repoStatusEl.textContent, 'path does not exist: /nope');
assert.ok(mod.bp.repoStatusEl.className.includes('repo-error'));

// ---- escape is one level: out of a repo field onto the panel, then out of the panel ---------------
fireKeydown(mod.bp.repoFields.path, {code: 'Escape', key: 'Escape'});
assert.ok(mod.bp.backdrop.parentNode, 'escape from a field must not close the whole panel');
assert.equal(document.activeElement, mod.bp.panel, 'escape from a field steps out onto the panel');
press('Escape');
assert.equal(mod.bp.backdrop.parentNode, null, 'a second escape closes the panel');
press('KeyB');
await flush(); await flush();
assert.ok(mod.bp.backdrop.parentNode, 'b reopens the panel');
press('KeyB');
assert.equal(mod.bp.backdrop.parentNode, null, 'b again closes it');

// ---- the first row offers new board and online; new board offers every folder, repo or not -------
press('KeyB');
await flush(); await flush();
const sources = [...mod.bp.sourceRowEl.children];
assert.deepEqual(sources.map(s => s.textContent), ['new board', 'from online repo']);
assert.ok(!sources[1].className.includes('disabled'), 'from online repo is a live button');
// new board and the clone both open where new repos go (the repos_home setting in o)
stub('/api/folders?start=repos', 'GET', 200, {here: '/home/me', parent: '/home', repo: false,
  folders: [{name: 'proj', path: '/home/me/proj', repo: true}, {name: 'notes', path: '/home/me/notes', repo: false}]});
sources[0].onclick();
await flush(); await flush();
assert.equal(lastMenu.opts.title, 'new board');
const sectionOf = kind => lastMenu.opts.sections.find(s => s.kind === kind);
assert.deepEqual(sectionOf('list').items.map(i => i.label), ['..', 'proj/  git', 'notes/']);
assert.deepEqual(sectionOf('buttons').buttons.map(b => b.label), ['board from this folder'],
  'a folder that is not a repo offers the action too');
assert.equal(sectionOf('add').button, 'board in new folder');
stub('/api/folders?under=%2Fhome%2Fme%2Fnotes', 'GET', 200,
  {here: '/home/me/notes', parent: '/home/me', repo: false, folders: []});
await sectionOf('list').onPick({id: '/home/me/notes'});
await flush();
assert.ok(sectionOf('buttons'), 'every folder keeps the action as the picker moves');
assert.ok(sectionOf('add'), 'and the new folder field');
const folderPosts = () => calls.filter(c => c.path === '/api/boards/from-folder');
const lastFolderBody = () => JSON.parse(folderPosts().at(-1).opts.body);

// ---- a new folder name posts new_folder; a board the server gave an origin shows main's push ----
const BOARD_C = {id: 'b3', name: 'fresh', position: 2, created_at: 't3'};
const PUSH_MAIN = 'git -C /home/me/notes/fresh push -u origin main && gh repo edit me/fresh --default-branch main';
stub('/api/boards/from-folder', 'POST', 201, {board: BOARD_C,
  repo: {id: 'r2', board_id: 'b3', name: 'fresh', path: '/home/me/notes/fresh', default_branch: 'development',
    test_command: null, image: null, lint_command: null},
  tests: {command: 'npm test', has_tests: true},
  setup: {steps: ['git init', 'pushed development'], push_main: PUSH_MAIN}});
stub('/api/boards', 'GET', 200, [BOARD_A, BOARD_B, BOARD_C]);
stub('/api/boards/b3/cards', 'GET', 200, []);
stub('/api/boards/b3/repos', 'GET', 200, []);
const menuC = lastMenu;
await sectionOf('add').onAdd('fresh', menuC);
await flush(); await flush();
assert.deepEqual(lastFolderBody(), {path: '/home/me/notes', new_folder: 'fresh'});
assert.ok(menuC.closed, 'the folder menu closes once the board exists');
assert.equal(mod.boardsRef().length, 3, 'the new board is in the bar without a reload');
assert.equal(mod.bp.boardStatusEl.textContent,
  "done: git init, pushed development; tests run with `npm test` - change it on the repo's row");
let pushMain = mod.bp.setupEl.querySelector('.boards-push-main');
assert.equal(pushMain.querySelector('.field-label').textContent, 'one step is yours - run this once:');
assert.equal(pushMain.querySelector('code').textContent, PUSH_MAIN);
assert.deepEqual([...pushMain.querySelectorAll('.toggle')].map(t => t.textContent), ['copy', 'done']);
const copied = [];
Object.defineProperty(globalThis, 'navigator', {configurable: true,
  value: {clipboard: {writeText: async text => { copied.push(text); }}}});
await pushMain.querySelector('.boards-push-main-copy').onclick();
assert.deepEqual(copied, [PUSH_MAIN]);
assert.equal(pushMain.querySelector('.boards-push-main-copy').textContent, 'copied');
Object.defineProperty(globalThis, 'navigator', {configurable: true,
  value: {clipboard: {writeText: async () => { throw new Error('denied'); }}}});
await pushMain.querySelector('.boards-push-main-copy').onclick();
assert.equal(pushMain.querySelector('.boards-push-main-copy').textContent, 'could not copy - select it');
press('Escape');
press('KeyB');
await flush(); await flush();
pushMain = mod.bp.setupEl.querySelector('.boards-push-main');
assert.ok(pushMain, "main's push stays through a close until it is done");
pushMain.querySelector('.boards-push-main-done').onclick();
assert.equal(mod.bp.setupEl.querySelector('.boards-push-main'), null, 'done clears it');

// ---- a folder with files asks first; commit these and continue resends with confirm -------------
const FILES = Array.from({length: 14}, (_, i) => `src/f${i}.py`);
stub('/api/boards/from-folder', 'POST', 409, {needs_confirm: true, files: FILES, path: '/home/me/notes'});
sources[0].onclick();
await flush(); await flush();
await sectionOf('list').onPick({id: '/home/me/notes'});
await flush();
const menuD = lastMenu;
await sectionOf('buttons').buttons[0].onClick(menuD);
await flush();
assert.deepEqual(lastFolderBody(), {path: '/home/me/notes'});
assert.ok(menuD.closed, 'the question is asked in the panel, not the folder menu');
const confirmBox = mod.bp.setupEl.querySelector('.boards-folder-confirm');
assert.ok(confirmBox, 'a 409 renders the first commit for the operator to confirm');
assert.equal(confirmBox.querySelector('.field-label').textContent,
  'notes has files and no git - its first commit would hold:');
const listed = [...confirmBox.querySelector('.boards-folder-files').children].map(el => el.textContent);
assert.deepEqual(listed, [...FILES.slice(0, 12), 'and 2 more']);
assert.ok([...confirmBox.children].some(el =>
  el.textContent === 'a .gitignore for secrets is added first if the folder has none'));
assert.deepEqual([...confirmBox.querySelectorAll('.toggle')].map(t => t.textContent),
  ['commit these and continue', 'cancel']);
assert.equal(mod.boardsRef().length, 3, 'no board yet');
const BOARD_NOTES = {id: 'bn', name: 'notes', position: 3, created_at: 'tn'};
stub('/api/boards/from-folder', 'POST', 201, {board: BOARD_NOTES,
  repo: {id: 'r6', board_id: 'bn', name: 'notes', path: '/home/me/notes', default_branch: 'development',
    test_command: 'npm test', image: null, lint_command: null},
  tests: {command: 'npm test', has_tests: true}, setup: {steps: [], push_main: null}});
stub('/api/boards', 'GET', 200, [BOARD_A, BOARD_B, BOARD_C, BOARD_NOTES]);
stub('/api/boards/bn/cards', 'GET', 200, []);
stub('/api/boards/bn/repos', 'GET', 200, []);
await confirmBox.querySelector('.boards-folder-commit').onclick();
await flush(); await flush();
assert.deepEqual(lastFolderBody(), {path: '/home/me/notes', confirm: true});
assert.equal(mod.bp.setupEl.querySelector('.boards-folder-confirm'), null, 'the question is answered');
assert.equal(mod.bp.setupEl.querySelector('.boards-push-main'), null, 'no origin made, no command shown');
assert.equal(mod.boardsRef().length, 4);
assert.equal(mod.bp.boardStatusEl.textContent, "tests run with `npm test` - change it on the repo's row",
  'nothing set up leaves the tests line alone');

// ---- cancel drops the question and posts nothing ---------------------------------------------------
stub('/api/boards/from-folder', 'POST', 409, {needs_confirm: true, files: ['a.py'], path: '/home/me/notes'});
await mod.createBoardFromFolder('/home/me/notes', null);
await flush();
const postsBeforeCancel = folderPosts().length;
mod.bp.setupEl.querySelector('.boards-folder-cancel').onclick();
assert.equal(mod.bp.setupEl.querySelector('.boards-folder-confirm'), null);
assert.equal(folderPosts().length, postsBeforeCancel);
press('KeyB');

// ---- a repo with tests and a command for them is one status line ---------------------------------
press('KeyB');
await flush(); await flush();
const closedMenu = {close() {}};
const BOARD_D = {id: 'b4', name: 'tested', position: 3, created_at: 't4'};
const TESTED_REPO = {id: 'r3', board_id: 'b4', name: 'tested', path: '/home/me/tested',
  default_branch: 'main', test_command: 'npm test', image: null, lint_command: null};
stub('/api/boards/from-folder', 'POST', 201,
  {board: BOARD_D, repo: TESTED_REPO, tests: {command: 'npm test', has_tests: true}});
stub('/api/boards', 'GET', 200, [BOARD_A, BOARD_B, BOARD_C, BOARD_D]);
stub('/api/boards/b4/cards', 'GET', 200, []);
stub('/api/boards/b4/repos', 'GET', 200, [TESTED_REPO]);
await mod.createBoardFromFolder('/home/me/tested', closedMenu);
await flush(); await flush();
assert.equal(mod.bp.boardStatusEl.textContent, "tests run with `npm test` - change it on the repo's row");
assert.equal(mod.bp.repoListEl.querySelector('.repo-tests-notice'), null, 'a tested repo holds no notice');

// ---- a repo with no tests holds a notice on its row, and asking mission control sends nothing -----
const BOARD_E = {id: 'b5', name: 'bare', position: 4, created_at: 't5'};
const BARE_REPO = {id: 'r4', board_id: 'b5', name: 'bare', path: '/home/me/bare', default_branch: 'main',
  test_command: null, image: null, lint_command: null};
stub('/api/boards/from-folder', 'POST', 201,
  {board: BOARD_E, repo: BARE_REPO, tests: {command: null, has_tests: false}});
stub('/api/boards', 'GET', 200, [BOARD_A, BOARD_B, BOARD_C, BOARD_D, BOARD_E]);
stub('/api/boards/b5/cards', 'GET', 200, []);
stub('/api/boards/b5/repos', 'GET', 200, [BARE_REPO]);
await mod.createBoardFromFolder('/home/me/bare', closedMenu);
await flush(); await flush();
let notice = mod.bp.repoListEl.querySelector('.repo-row[data-repo-id="r4"] .repo-tests-notice');
assert.ok(notice, 'a repo with no tests holds a notice on its own row');
assert.equal(notice.querySelector('.field-label').textContent, 'bare has no tests - every card needs them');
assert.deepEqual([...notice.querySelectorAll('.toggle')].map(t => t.textContent),
  ['ask mission control', 'use my own command']);
await mod.renderRepoList();
notice = mod.bp.repoListEl.querySelector('.repo-tests-notice');
assert.ok(notice, 'a re-render keeps the notice until one of its actions is picked');
const orchestratorPosts = () =>
  calls.filter(c => c.path.includes('/orchestrator') && c.opts && c.opts.method === 'POST');
notice.querySelector('.repo-tests-ask').onclick();
await flush();
assert.equal(mod.bp.backdrop.parentNode, null, 'asking mission control closes the panel');
assert.equal(mod.mc.input.value, 'bare has no tests yet. plan a first card that adds a test suite for its '
  + 'current behaviour, sets up its test runner and names the command.');
assert.notEqual(document.activeElement, mod.mc.input, 'the prefill leaves the input unfocused');
assert.equal(orchestratorPosts().length, 0, 'the prefill is never sent');
assert.equal(mod.bp.boardStatusEl.textContent, 'press / then enter to send it');
press('KeyB');
await flush(); await flush();
assert.equal(mod.bp.repoListEl.querySelector('.repo-tests-notice'), null, 'a picked action clears the notice');

// ---- registering a repo with a command but no tests: use my own command focuses its test field ----
Object.keys(mod.bp.repoFields).forEach(k => { mod.bp.repoFields[k].value = ''; });
mod.bp.repoFields.name.value = 'py';
mod.bp.repoFields.path.value = '/py';
const PY_REPO = {id: 'r5', board_id: 'b5', name: 'py', path: '/py', default_branch: 'main',
  test_command: 'uv run --no-sync pytest -q', image: null, lint_command: null};
stub('/api/boards/b5/repos', 'POST', 201,
  {...PY_REPO, tests: {command: 'uv run --no-sync pytest -q', has_tests: false}});
stub('/api/boards/b5/repos', 'GET', 200, [BARE_REPO, PY_REPO]);
fireKeydown(mod.bp.repoFields.path, {code: 'Enter', key: 'Enter'});
await flush(); await flush(); await flush();
notice = mod.bp.repoListEl.querySelector('.repo-row[data-repo-id="r5"] .repo-tests-notice');
assert.ok(notice, 'a registered repo with no tests holds the notice too');
assert.equal(mod.bp.repoListEl.querySelectorAll('.repo-tests-notice').length, 1, 'only on its own row');
notice.querySelector('.repo-tests-own').onclick();
await flush(); await flush();
assert.equal(document.activeElement,
  mod.bp.repoListEl.querySelector('.repo-row[data-repo-id="r5"] .repo-edit-test'),
  "use my own command focuses that repo's test command field");
assert.equal(mod.bp.repoListEl.querySelector('.repo-tests-notice'), null, 'and clears the notice');
press('Escape');

// ---- from online repo: the repos gh can see, then the folder to clone the pick into --------------
press('KeyB');
await flush(); await flush();
const onlineButton = [...mod.bp.sourceRowEl.children][1];
stub('/api/online-repos', 'GET', 200, [
  {name_with_owner: 'me/alpha', private: false, description: ''},
  {name_with_owner: 'me/beta', private: true, description: ''},
]);
await onlineButton.onclick();
await flush(); await flush();
assert.equal(lastMenu.opts.title, 'board from online repo');
const repoMenu = lastMenu;
const repoItems = repoMenu.opts.sections[0].items;
assert.deepEqual(repoItems.map(i => i.label), ['me/alpha', 'me/beta  private']);

stub('/api/folders?start=repos', 'GET', 200, {here: '/home/me', parent: '/home', repo: false, folders: []});
await repoMenu.opts.sections[0].onPick(repoItems[0]);
await flush(); await flush();
assert.ok(repoMenu.closed, 'the repo list closes once a repo is picked');
assert.equal(lastMenu.opts.title, 'clone me/alpha into');
const cloneMenu = lastMenu;
const clone = () => cloneMenu.opts.sections.find(s => s.kind === 'buttons').buttons[0];
assert.equal(clone().label, 'clone into this folder: /home/me/alpha', 'the target path is shown first');
assert.equal(cloneMenu.opts.sections.find(s => s.kind === 'add'), undefined, 'no new folder field');

const BOARD_F = {id: 'b6', name: 'alpha', position: 5, created_at: 't6'};
stub('/api/boards/from-online-repo', 'POST', 201, {board: BOARD_F,
  repo: {id: 'r7', board_id: 'b6', name: 'alpha', path: '/home/me/alpha', default_branch: 'development',
    test_command: 'npm test', image: null, lint_command: null},
  tests: {command: 'npm test', has_tests: true},
  setup: {steps: ['branched development from main', 'pushed development'], push_main: null}});
stub('/api/boards', 'GET', 200, [BOARD_A, BOARD_B, BOARD_C, BOARD_D, BOARD_E, BOARD_F]);
stub('/api/boards/b6/cards', 'GET', 200, []);
stub('/api/boards/b6/repos', 'GET', 200, []);
const cloning = clone().onClick(cloneMenu);
assert.equal(mod.bp.boardStatusEl.textContent, 'cloning...');
await cloning;
await flush(); await flush();
const onlineCalls = () => calls.filter(c => c.path === '/api/boards/from-online-repo');
assert.deepEqual(JSON.parse(onlineCalls().at(-1).opts.body), {repo: 'me/alpha', folder: '/home/me'});
assert.ok(cloneMenu.closed, 'the folder menu closes once the board exists');
assert.equal(mod.boardsRef().length, 6, 'the cloned board is in the bar without a reload');
assert.equal(mod.bp.boardStatusEl.textContent, 'done: branched development from main, pushed development; '
  + "tests run with `npm test` - change it on the repo's row");
assert.equal(mod.bp.setupEl.querySelector('.boards-push-main'), null, 'a clone has its origin already');

// a refused clone keeps the folder menu open and says why
cloneMenu.closed = false;
stub('/api/boards/from-online-repo', 'POST', 400, {error: '/home/me/alpha already exists. Pick a different folder'});
await clone().onClick(cloneMenu);
await flush();
assert.match(mod.bp.boardStatusEl.textContent, /already exists/);
assert.ok(mod.bp.boardStatusEl.className.includes('boards-error'));
assert.ok(!cloneMenu.closed, 'the menu stays to pick another folder');
assert.equal(mod.boardsRef().length, 6, 'no board added');

// a gh that is missing or logged out is refused with the fix, and no menu opens
stub('/api/online-repos', 'GET', 400, {error: 'gh is not logged in. Run `gh auth login`, then try again.'});
const menusBefore = lastMenu;
await onlineButton.onclick();
await flush();
assert.equal(lastMenu, menusBefore, 'a refused listing opens no menu');
assert.match(mod.bp.boardStatusEl.textContent, /gh auth login/);
press('KeyB');

console.log('ok');
