// board settings (shift+o): the open board's own merge mode, file lease, parallel cap and daily
// budget. soft and free only unlock once o turns the feature on; o itself keeps no per-board rows.
// run: node tests/js/board_settings.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const settingsState = {max_parallel: 3, allow_soft_leases: null, allow_free_merge: null};
const boardsState = [
  {id: 'b1', name: 'alpha', merge_mode: null, lease_mode: null, max_parallel: null, daily_budget_usd: null},
  {id: 'b2', name: 'beta', merge_mode: null, lease_mode: null, max_parallel: 1, daily_budget_usd: null},
];
const calls = [];
function stubJson(status, body) {
  return {ok: status >= 200 && status < 300, status, json: async () => body};
}
function fetchStub(path, opts) {
  calls.push({path, opts});
  const method = (opts && opts.method) || 'GET';
  if (path === '/api/boards' && method === 'GET') {
    return Promise.resolve(stubJson(200, boardsState.map(b => ({...b}))));
  }
  if (path === '/api/settings' && method === 'GET') return Promise.resolve(stubJson(200, {...settingsState}));
  const boardMatch = /^\/api\/boards\/([^/]+)$/.exec(path);
  if (boardMatch && method === 'PATCH') {
    const board = boardsState.find(b => b.id === boardMatch[1]);
    const body = JSON.parse(opts.body);
    // the server's own gate: soft and free need the global switch
    if (body.merge_mode === 'free' && settingsState.allow_free_merge !== 'on') {
      return Promise.resolve(stubJson(400, {error: 'free merge is off - turn it on in settings (o) first'}));
    }
    Object.assign(board, body);
    return Promise.resolve(stubJson(200, {...board}));
  }
  // board.js's own startup fetches never answer, as in settings.mjs
  return new Promise(() => {});
}

installStubDom({fetchImpl: fetchStub});
const boardBar = element('div', 'board-bar');
boardBar.id = 'board-bar';
const bucketRow = element('div', 'bucket-row');
bucketRow.id = 'bucket-row';
document.body.appendChild(boardBar);
document.body.appendChild(bucketRow);

function SpyDrawer() {
  return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false};
}
class SpyMenu {
  constructor(opts) { this.opts = opts; }
  openAt() { return this; }
  refresh() {}
  close() {}
}

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), uiBase('pile.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js'), smort('settings.js'), smort('board_settings.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {bs, st, BINDINGS, openBoardSettingsPanel,
  setBoards: list => { boards = list; }, setCurrent: id => { currentBoardId = id; }};`)(SpyMenu, SpyDrawer);

async function flush() {
  for (let i = 0; i < 4; i++) await new Promise(r => setTimeout(r, 0));
}
function press(code, shiftKey = false) {
  document._dispatch('keydown', {code, key: code, shiftKey, target: document.body, preventDefault() {}});
}
const panel = () => mod.bs.listEl;
function section(label) {
  return panel().querySelectorAll('.settings-section').find(s => s.children[0].textContent === label);
}
function buttons(label) {
  return section(label).querySelectorAll('.toggle');
}
function lit(label) {
  return buttons(label).filter(b => b.classList.contains('on')).map(b => b.textContent);
}
// the stub's textContent is the element's own, so gather the subtree's
function text(el) {
  return [el.textContent, ...(el.children || []).map(text)].join(' ');
}
function lastBoardPatch() {
  const call = calls.filter(c => /^\/api\/boards\/b/.test(c.path) && c.opts?.method === 'PATCH').at(-1);
  return call && {path: call.path, body: JSON.parse(call.opts.body)};
}

mod.setBoards(boardsState.map(b => ({...b})));
mod.setCurrent('b1');

// ---- shift+o is on o's row: the overlay keeps one row per code --------------------------------
assert.ok(mod.BINDINGS.some(b => b.code === 'KeyO' && b.label === 'o / shift+o'));

// ---- shift+o opens this board's panel, plain o still opens the shared one -----------------------
press('KeyO', true);
await flush();
assert.ok(mod.bs.backdrop.parentNode, 'shift+o opens the board panel');
assert.ok(!mod.st.backdrop || !mod.st.backdrop.parentNode, 'shift+o does not open o');
assert.equal(mod.bs.titleEl.textContent, 'board settings: alpha');
assert.deepEqual(panel().querySelectorAll('.settings-section').map(s => s.children[0].textContent),
  ['merge mode', 'file lease', 'cards at once', 'daily budget, usd']);

// ---- with the global switches off, review and strict are shown and free and soft are locked ------
assert.deepEqual(lit('merge mode'), ['review']);
assert.deepEqual(lit('file lease'), ['strict']);
assert.equal(buttons('merge mode')[1].disabled, true, 'free waits for the switch in o');
assert.equal(buttons('file lease')[1].disabled, true, 'soft waits for the switch in o');
assert.ok(text(section('merge mode')).includes('turn it on in settings (o)'));
assert.ok(text(section('file lease')).includes('turn them on in settings (o)'));
assert.ok(text(section('cards at once')).includes('(3)'), 'the note names the global cap');

// ---- escape closes it; the key that opens also closes -------------------------------------------
document._dispatch('keydown', {code: 'Escape', key: 'Escape', target: document.body, preventDefault() {}});
assert.ok(!mod.bs.backdrop.parentNode, 'escape closes the board panel');

// ---- switches on: free and soft unlock and save to this board only ------------------------------
settingsState.allow_free_merge = 'on';
settingsState.allow_soft_leases = 'on';
press('KeyO', true);
await flush();
assert.equal(buttons('merge mode')[1].disabled, false);
await buttons('merge mode')[1].onclick();
assert.deepEqual(lastBoardPatch(), {path: '/api/boards/b1', body: {merge_mode: 'free'}});
assert.deepEqual(lit('merge mode'), ['free']);
assert.equal(document.getElementById('merge-mode-label').textContent, 'free merge',
  'the bar label follows without a reload');
await buttons('file lease')[1].onclick();
assert.deepEqual(lastBoardPatch(), {path: '/api/boards/b1', body: {lease_mode: 'soft'}});
assert.deepEqual(lit('file lease'), ['soft']);
assert.equal(boardsState[1].lease_mode, null, 'beta is untouched');

// ---- a refused save leaves the lit button where it was and says why -----------------------------
await buttons('merge mode')[0].onclick();
assert.deepEqual(lit('merge mode'), ['review']);
settingsState.allow_free_merge = null; // turned off elsewhere while the panel was open
await buttons('merge mode')[1].onclick();
assert.deepEqual(lit('merge mode'), ['review'], 'the server refused, so review stays lit');
assert.ok(section('merge mode').querySelector('.boards-error').textContent.includes('free merge is off'));

// ---- the numbers: blank clears, a bad value never reaches the server ----------------------------
const cap = section('cards at once').querySelector('input');
assert.equal(cap.value, '', 'alpha has no own cap');
cap.value = '2';
cap._listeners.blur.forEach(fn => fn());
await flush();
assert.deepEqual(lastBoardPatch(), {path: '/api/boards/b1', body: {max_parallel: 2}});
const patches = calls.length;
cap.value = '0';
cap._listeners.blur.forEach(fn => fn());
await flush();
assert.equal(calls.length, patches, 'zero is refused in place');
assert.ok(section('cards at once').querySelector('.boards-error').textContent.includes('positive'));
cap.value = '';
cap._listeners.blur.forEach(fn => fn());
await flush();
assert.deepEqual(lastBoardPatch(), {path: '/api/boards/b1', body: {max_parallel: null}});

const budget = section('daily budget, usd').querySelector('input');
budget.value = '5.5';
budget._listeners.blur.forEach(fn => fn());
await flush();
assert.deepEqual(lastBoardPatch(), {path: '/api/boards/b1', body: {daily_budget_usd: 5.5}});
assert.equal(boardsState[0].daily_budget_usd, 5.5);

press('KeyO', true);
assert.ok(!mod.bs.backdrop.parentNode, 'a second shift+o closes it');

// ---- no board open: the panel says so instead of editing nothing -------------------------------
mod.setCurrent(null);
press('KeyO', true);
await flush();
assert.ok(text(panel()).includes('no board open'));

console.log('ok');
