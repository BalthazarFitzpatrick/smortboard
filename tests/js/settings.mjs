// settings panel (o): the top-right button and the o key open it, esc and an outside click close
// it, and it renders an extensible section list - "mission control can read" is the first real
// section, added, refused and removed through the whole-list-replace PATCH /api/settings.
// run: node tests/js/settings.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

// a tiny fake server: GET returns the current settings, PATCH replaces
// mission_control_read_paths wholesale - '~' expands server-side, an unknown path is refused by
// name, exactly like the real store's _check_read_paths.
const EXPANDS = {'~/Documents/screenshots': '/home/op/Documents/screenshots'};
const settingsState = {mission_control_read_paths: [], max_parallel: null};
const boardsState = [
  {id: 'b1', name: 'alpha', max_parallel: null},
  {id: 'b2', name: 'beta', max_parallel: 1},
];
const calls = [];
function stubJson(status, body) {
  return {ok: status >= 200 && status < 300, status, json: async () => body};
}
function expandReadPath(raw) {
  if (raw in EXPANDS) return EXPANDS[raw];
  if (Object.values(EXPANDS).includes(raw)) return raw;
  return null;
}
function fetchStub(path, opts) {
  calls.push({path, opts});
  if (path === '/api/boards' && (!opts || !opts.method || opts.method === 'GET')) {
    return Promise.resolve(stubJson(200, boardsState.map(b => ({...b}))));
  }
  const boardMatch = /^\/api\/boards\/([^/]+)$/.exec(path);
  if (boardMatch && opts && opts.method === 'PATCH') {
    const board = boardsState.find(b => b.id === boardMatch[1]);
    if (!board) return Promise.resolve(stubJson(404, {error: 'no board'}));
    const body = JSON.parse(opts.body);
    if ('max_parallel' in body) {
      if (body.max_parallel !== null && (!Number.isInteger(body.max_parallel) || body.max_parallel <= 0)) {
        return Promise.resolve(stubJson(400, {error: 'max_parallel must be a positive integer or null'}));
      }
      board.max_parallel = body.max_parallel;
    }
    return Promise.resolve(stubJson(200, {...board}));
  }
  // anything else never answers, as on main: board.js's own startup fetches (loadBoards) would
  // otherwise reject unhandled and end the run before a single assertion
  if (path !== '/api/settings') return new Promise(() => {});
  if (!opts || !opts.method || opts.method === 'GET') return Promise.resolve(stubJson(200, {...settingsState}));
  if (opts.method === 'PATCH') {
    const body = JSON.parse(opts.body);
    if ('mission_control_read_paths' in body) {
      const resolved = [];
      for (const raw of body.mission_control_read_paths) {
        const expanded = expandReadPath(raw);
        if (expanded === null) return Promise.resolve(stubJson(400, {error: `${raw} does not exist or is not a folder`}));
        resolved.push(expanded);
      }
      settingsState.mission_control_read_paths = resolved;
    }
    if ('max_parallel' in body) {
      if (body.max_parallel !== null && (!Number.isInteger(body.max_parallel) || body.max_parallel <= 0)) {
        return Promise.resolve(stubJson(400, {error: 'max_parallel must be a positive integer or null'}));
      }
      settingsState.max_parallel = body.max_parallel;
    }
    return Promise.resolve(stubJson(200, {...settingsState}));
  }
  return Promise.resolve(stubJson(404, {error: 'no stub for ' + path}));
}

installStubDom({fetchImpl: fetchStub});
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
class SpyMenu { constructor(opts) { this.opts = opts; } openAt() { return this; } refresh() {} close() {} }

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js'), smort('settings.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {toggleSettingsPanel, openSettingsPanel, closeSettingsPanel, st, rp, pl, BINDINGS,
  buttonRef: () => document.querySelector('.settings-button'),
  addButtonRef: () => document.querySelector('.boards-create-row .toggle')};`)(SpyMenu, SpyDrawer);

async function flush() {
  await new Promise(r => setTimeout(r, 0));
  await new Promise(r => setTimeout(r, 0));
}

function press(code, target) {
  document._dispatch('keydown', {code, key: code, target: target || document.body, preventDefault() {}});
}

// o is in the contract, grouped with the other panels, and the overlay is built from this table
assert.ok(mod.BINDINGS.some(b => b.code === 'KeyO' && b.group === 'panels'), 'KeyO must be a panels binding');

// ---- the button is built at startup, beside the board bar, not inside it (renderBoardBar wipes it)
const button = mod.buttonRef();
assert.ok(button, 'the settings button exists once settings.js loads');
assert.ok(!boardBar.children.includes(button), 'the button must not live inside #board-bar');

// ---- the button opens the panel -------------------------------------------------------------
button.onclick();
assert.ok(mod.st.backdrop.parentNode, 'clicking the button should open the panel');

// ---- the panel renders all four sections: credential profiles, mission control can read,
// parallelism, mall cam interval (cf90bacc)
await flush();
assert.equal(mod.st.listEl.querySelectorAll('.settings-section').length, 4,
  'credential profiles, mission control can read, how many cards run at once, mall cam interval');
assert.ok(mod.st.listEl.querySelector('.settings-auto-switch-checkbox'), 'the credential section carries the auto-switch toggle');
assert.ok(mod.rp.listEl.querySelector('.hazard-placeholder'), 'no folders yet shows a placeholder, not nothing');

// ---- the parallelism section loads the global cap and one row per board -------------------------
assert.equal(mod.pl.globalInput.value, '', 'an unset global cap renders as an empty field, not 0');
const boardRows = mod.pl.boardsList.querySelectorAll('.board-row');
assert.equal(boardRows.length, 2, 'one row per board');
assert.equal(boardRows[0].querySelector('.board-name').textContent, 'alpha');
assert.equal(boardRows[0].querySelector('input').value, '', 'alpha has no board-specific limit');
assert.equal(boardRows[1].querySelector('.board-name').textContent, 'beta');
assert.equal(boardRows[1].querySelector('input').value, '1', 'beta already carries its own limit');

// ---- adding a path PATCHes the whole list, and stores the server's expanded absolute path -------
mod.rp.input.value = '~/Documents/screenshots';
mod.addButtonRef().onclick();
await flush();
const patch = calls.find(c => c.path === '/api/settings' && c.opts?.method === 'PATCH');
assert.deepEqual(JSON.parse(patch.opts.body), {mission_control_read_paths: ['~/Documents/screenshots']}, 'the client sends the raw text typed - the ~ expands server-side');
assert.deepEqual(settingsState.mission_control_read_paths, ['/home/op/Documents/screenshots'], 'the stored value is the absolute, expanded path');
assert.equal(mod.rp.input.value, '', 'the input clears after a successful add');
assert.equal(mod.rp.listEl.querySelectorAll('.board-row').length, 1, 'one row for the added path');
assert.equal(mod.rp.listEl.querySelector('.board-name').textContent, '/home/op/Documents/screenshots', 'the row shows the absolute path, not what was typed');

// ---- a path that does not exist is refused with a message naming it, and nothing is added -------
mod.rp.input.value = '/nope/not-there';
mod.addButtonRef().onclick();
await flush();
assert.ok(mod.rp.statusEl.textContent.includes('/nope/not-there'), 'the refusal names the bad path');
assert.equal(mod.rp.listEl.querySelectorAll('.board-row').length, 1, 'the refused path was never added');
assert.deepEqual(settingsState.mission_control_read_paths, ['/home/op/Documents/screenshots'], 'a refused patch leaves the stored list untouched');

// ---- removing a row PATCHes the remaining list and drops it from the panel ----------------------
mod.rp.listEl.querySelector('.board-delete').onclick();
await flush();
assert.deepEqual(settingsState.mission_control_read_paths, [], 'removing the only path clears the setting');
assert.ok(mod.rp.listEl.querySelector('.hazard-placeholder'), 'the list falls back to the empty placeholder');

// ---- saving the global cap PATCHes /api/settings ------------------------------------------------
mod.pl.globalInput.value = '3';
mod.pl.globalInput._listeners.blur.forEach(fn => fn());
await flush();
assert.equal(settingsState.max_parallel, 3, 'the global cap is saved');

// ---- zero, negative and non-numeric values are refused without a PATCH landing ------------------
mod.pl.globalInput.value = '0';
mod.pl.globalInput._listeners.blur.forEach(fn => fn());
await flush();
assert.equal(settingsState.max_parallel, 3, 'a zero value never reaches the server');
assert.ok(mod.pl.globalStatus.textContent.includes('positive'), 'the field explains why it refused');

// ---- a board's own row PATCHes /api/boards/<id>, and an empty value clears the limit -------------
boardRows[0].querySelector('input').value = '2';
boardRows[0].querySelector('input')._listeners.blur.forEach(fn => fn());
await flush();
assert.equal(boardsState[0].max_parallel, 2, 'alpha now carries its own limit');
boardRows[1].querySelector('input').value = '';
boardRows[1].querySelector('input')._listeners.blur.forEach(fn => fn());
await flush();
assert.equal(boardsState[1].max_parallel, null, "clearing the field removes beta's limit");

// ---- a click on the backdrop itself closes it, a click inside the panel does not ---------------
mod.st.backdrop._listeners.mousedown.forEach(fn => fn({target: mod.st.panel}));
assert.ok(mod.st.backdrop.parentNode, 'a click on the panel must not close it');
mod.st.backdrop._listeners.mousedown.forEach(fn => fn({target: mod.st.backdrop}));
assert.ok(!mod.st.backdrop.parentNode, 'a click on the backdrop itself should close the panel');

// ---- o opens the panel too ------------------------------------------------------------------
press('KeyO');
assert.ok(mod.st.backdrop.parentNode, 'o should open the panel');

// ---- escape closes it ------------------------------------------------------------------------
document._dispatch('keydown', {code: 'Escape', key: 'Escape', target: document.body, preventDefault() {}});
assert.ok(!mod.st.backdrop.parentNode, 'escape should close the panel');

// ---- o toggles: open, then a second press closes it (the key that opens also closes) -----------
press('KeyO');
assert.ok(mod.st.backdrop.parentNode, 'o should reopen the panel');
press('KeyO');
assert.ok(!mod.st.backdrop.parentNode, 'a second o should close the panel');

console.log('ok');
