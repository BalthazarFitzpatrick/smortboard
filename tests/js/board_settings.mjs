// board settings (shift+o): the open board's own presets, merge mode, file lease, run mode, off-limit
// branches, parallel cap and daily budget, as independent accordions. free, soft and open stay
// unavailable until o turns the matching switch on; o itself keeps no per-board rows.
// run: UI_BASE_ASSETS_DIR=<ui_base assets dir> node tests/js/board_settings.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const settingsState = {max_parallel: 3, allow_soft_leases: null, allow_free_merge: null, allow_open_mode: null};
const boardsState = [
  {id: 'b1', name: 'alpha', merge_mode: null, lease_mode: null, run_mode: null, max_parallel: null, daily_budget_usd: null,
    off_limit_branches: ['main', 'master', 'trunk']},
  {id: 'b2', name: 'beta', merge_mode: null, lease_mode: null, run_mode: null, max_parallel: 1, daily_budget_usd: null,
    off_limit_branches: ['main', 'master', 'trunk']},
];
const calls = [];
let fetchFail = false;
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
    if (fetchFail) return Promise.resolve(stubJson(400, {error: "not a branch name: 'bad..name'"}));
    // the server's own gates: free, soft and open need the global switch, and nothing is written
    // when any field of the request is refused
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

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('segments.js'), uiBase('disclosure.js'), uiBase('shell.js'), uiBase('pile.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js'), smort('settings_presets.js'), smort('settings.js'), smort('board_settings.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {bs, st, BINDINGS, openBoardSettingsPanel,
  setBoards: list => { boards = list; }, setCurrent: id => { currentBoardId = id; }};`)(SpyMenu, SpyDrawer);

async function flush() {
  for (let i = 0; i < 6; i++) await new Promise(r => setTimeout(r, 0));
}
function press(code, shiftKey = false) {
  document._dispatch('keydown', {code, key: code, shiftKey, target: document.body, preventDefault() {}});
}
// a segment answers a click through its listener, and the pick resolves a tick later
async function click(button) {
  (button._listeners.click || []).forEach(fn => fn());
  await flush();
}
const panel = () => mod.bs.listEl;
function section(label) {
  return panel().querySelectorAll('.settings-section').find(s => s.children[0].textContent === label);
}
function buttons(label) {
  return section(label).querySelectorAll('.segment');
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
const groupEls = () => panel().querySelectorAll('.disclosure');
const groupHead = title => groupEls().find(g => g.querySelector('.disclosure-name').textContent === title);

mod.setBoards(boardsState.map(b => ({...b})));
mod.setCurrent('b1');

// ---- shift+o is on o's row: the overlay keeps one row per code --------------------------------
assert.ok(mod.BINDINGS.some(b => b.code === 'KeyO' && b.label === 'o / shift+o'));

// ---- shift+o opens this board's panel, plain o still opens the shared one -----------------------
const callsBeforeOpen = calls.length;
press('KeyO', true);
await flush();
assert.ok(mod.bs.backdrop.parentNode, 'shift+o opens the board panel');
assert.ok(!mod.st.backdrop || !mod.st.backdrop.parentNode, 'shift+o does not open o');
assert.equal(mod.bs.titleEl.textContent, 'board settings: alpha');
const opened = calls.slice(callsBeforeOpen);
assert.equal(opened.filter(c => c.path === '/api/settings').length, 1, 'one read of the settings per open');
assert.equal(opened.filter(c => c.path === '/api/boards').length, 1, 'and one of the boards');
assert.deepEqual(panel().querySelectorAll('.settings-section').map(s => s.children[0].textContent),
  ['board preset', 'merge mode', 'off-limit branches', 'file lease', 'run mode', 'cards at once', 'daily budget, usd']);

// ---- three accordions, closed on first open, each with its summary and a count --------------------
assert.deepEqual(groupEls().map(g => g.querySelector('.disclosure-name').textContent), ['landing', 'running', 'budget']);
assert.ok(groupEls().every(g => !g.classList.contains('open')), 'all closed on first open');
assert.ok(panel().querySelectorAll('.disclosure-body').every(b => b.hidden === true), 'closed bodies are hidden');
assert.deepEqual(groupEls().map(g => g.querySelector('.disclosure-summary').textContent), [
  'merge review / off-limits main, master, trunk',
  'file lease strict / run sealed / cards no limit',
  'daily none',
]);
assert.deepEqual(groupEls().map(g => g.querySelector('.count-badge').textContent), ['2', '3', '1']);

// ---- the preset row: review only is lit, the others wait for their switches, custom is the app's ---
assert.deepEqual(lit('board preset'), ['review only']);
const presetButtons = () => buttons('board preset');
assert.deepEqual(presetButtons().map(b => b.textContent), ['review only', 'free merge', 'open', 'custom']);
assert.ok(presetButtons()[1].classList.contains('unavailable'), 'free merge waits for its switch');
assert.ok(presetButtons()[2].classList.contains('unavailable'), 'open waits for its switches');
assert.ok(presetButtons()[1].title.includes('free merge: off in general'), 'the hint names the switch');
assert.ok(presetButtons()[2].title.includes('open run mode: off in general'));
assert.ok(text(section('board preset')).includes('review only sets merge mode review, file lease strict, run mode sealed.'));
assert.ok(text(section('board preset')).includes('0 values differ.'));
const before = calls.length;
await click(presetButtons()[1]);
await click(presetButtons()[3]);
assert.equal(calls.length, before, 'an unavailable preset and the custom state never save');

// ---- with the global switches off the modes show their value in force and the switch's state ------
assert.deepEqual(lit('merge mode'), ['review']);
assert.deepEqual(lit('file lease'), ['strict']);
assert.deepEqual(lit('run mode'), ['sealed']);
assert.deepEqual(buttons('merge mode').map(b => b.textContent), ['review', 'free'], 'wording: review / free');
assert.deepEqual(buttons('file lease').map(b => b.textContent), ['strict', 'soft'], 'wording: strict / soft');
assert.deepEqual(buttons('run mode').map(b => b.textContent), ['sealed', 'open'], 'wording: sealed / open');
assert.ok(buttons('merge mode')[1].classList.contains('unavailable'), 'free is dashed, not just grey');
assert.ok(buttons('file lease')[1].classList.contains('unavailable'));
assert.ok(buttons('run mode')[1].classList.contains('unavailable'));
assert.ok(text(section('merge mode')).includes('free merge: off in general'));
assert.ok(text(section('merge mode')).includes('turn on in general'));
assert.ok(text(section('file lease')).includes('soft file leases: off in general'));
assert.ok(text(section('run mode')).includes('open run mode: off in general'));
assert.ok(text(section('cards at once')).includes('(3)'), 'the note names the global limit');
assert.ok(text(section('cards at once')).includes('can only lower'));

// ---- groups are independent: opening one leaves the others as they were, and the set is kept -----
await click(groupHead('landing').querySelector('.disclosure-head'));
await click(groupHead('budget').querySelector('.disclosure-head'));
assert.deepEqual(groupEls().map(g => g.classList.contains('open')), [true, false, true]);

// ---- escape closes it; the key that opens also closes -------------------------------------------
document._dispatch('keydown', {code: 'Escape', key: 'Escape', target: document.body, preventDefault() {}});
assert.ok(!mod.bs.backdrop.parentNode, 'escape closes the board panel');
press('KeyO', true);
await flush();
assert.deepEqual(groupEls().map(g => g.classList.contains('open')), [true, false, true],
  'the open groups are remembered for the session');
document._dispatch('keydown', {code: 'Escape', key: 'Escape', target: document.body, preventDefault() {}});

// ---- switches on: free and soft unlock and save to this board only ------------------------------
settingsState.allow_free_merge = 'on';
settingsState.allow_soft_leases = 'on';
settingsState.allow_open_mode = 'on';
press('KeyO', true);
await flush();
assert.ok(!buttons('merge mode')[1].classList.contains('unavailable'));
assert.ok(text(section('merge mode')).includes('free merge: on in general'));
assert.ok(!text(section('merge mode')).includes('turn on in general'), 'no link once the switch is on');
await click(buttons('merge mode')[1]);
assert.deepEqual(lastBoardPatch(), {path: '/api/boards/b1', body: {merge_mode: 'free'}});
assert.deepEqual(lit('merge mode'), ['free']);
assert.deepEqual(lit('board preset'), ['free merge'], 'the preset row follows the modes');
assert.equal(document.getElementById('merge-mode-label').textContent, 'free merge',
  'the bar label follows without a reload');
assert.equal(groupHead('landing').querySelector('.disclosure-summary').textContent.startsWith('merge free'), true,
  'the header summary follows too');
await click(buttons('file lease')[1]);
assert.deepEqual(lastBoardPatch(), {path: '/api/boards/b1', body: {lease_mode: 'soft'}});
assert.deepEqual(lit('file lease'), ['soft']);
assert.equal(boardsState[1].lease_mode, null, 'beta is untouched');
assert.deepEqual(lit('board preset'), ['custom'], 'free merge plus soft matches no preset');
assert.ok(text(section('board preset')).includes('no preset matches. closest is free merge, with'));
assert.ok(text(section('board preset')).includes('1 difference: file lease is soft.'));
assert.ok(text(section('run mode')).includes('weaker than a container'));
await click(buttons('run mode')[1]);
assert.deepEqual(lastBoardPatch(), {path: '/api/boards/b1', body: {run_mode: 'open'}});
assert.deepEqual(lit('run mode'), ['open']);
assert.equal(boardsState[1].run_mode, null, 'beta is untouched');

// ---- a preset writes the three modes in one request and relights the rows -------------------------
const requests = calls.length;
await click(presetButtons()[0]);
assert.equal(calls.length, requests + 1, 'one request for the whole preset');
assert.deepEqual(lastBoardPatch(), {path: '/api/boards/b1', body: {merge_mode: 'review', lease_mode: 'strict', run_mode: 'sealed'}});
assert.deepEqual(lit('board preset'), ['review only']);
assert.deepEqual([lit('merge mode'), lit('file lease'), lit('run mode')], [['review'], ['strict'], ['sealed']]);
await click(presetButtons()[2]);
assert.deepEqual(lastBoardPatch().body, {merge_mode: 'review', lease_mode: 'soft', run_mode: 'open'});
assert.deepEqual(lit('board preset'), ['open']);
assert.deepEqual([lit('merge mode'), lit('file lease'), lit('run mode')], [['review'], ['soft'], ['open']]);
await click(presetButtons()[0]);

// ---- a refused save leaves the lit segment where it was and says why ------------------------------
await click(buttons('merge mode')[1]);
await click(buttons('merge mode')[0]);
assert.deepEqual(lit('merge mode'), ['review']);
settingsState.allow_free_merge = null; // turned off elsewhere while the panel was open
await click(buttons('merge mode')[1]);
assert.deepEqual(lit('merge mode'), ['review'], 'the server refused, so review stays lit');
assert.ok(section('merge mode').querySelector('.boards-error').textContent.includes('free merge is off'));
settingsState.allow_free_merge = 'on';

// ---- the numbers: blank clears, a bad value never reaches the server ----------------------------
const cap = section('cards at once').querySelector('input');
assert.equal(cap.value, '', 'alpha has no own cap');
cap.value = '2';
cap._listeners.blur.forEach(fn => fn());
await flush();
assert.deepEqual(lastBoardPatch(), {path: '/api/boards/b1', body: {max_parallel: 2}});
assert.ok(groupHead('running').querySelector('.disclosure-summary').textContent.endsWith('cards 2'));
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
assert.equal(groupHead('budget').querySelector('.disclosure-summary').textContent, 'daily 5.50');

// ---- off-limit branches: one field holds the names, blur and enter save the whole list ---------
const offSection = () => section('off-limit branches');
const offField = () => offSection().querySelector('.off-limit-input');
const blurField = field => field._listeners.blur.forEach(fn => fn());
assert.equal(offSection().querySelectorAll('input').length, 1, 'one field, no per-branch rows');
assert.equal(offSection().querySelectorAll('button').length, 0, 'no add or remove buttons');
assert.equal(offField().value, 'main, master, trunk', 'the default list is shown joined');
offField().value = 'release, main';
blurField(offField());
await flush();
assert.deepEqual(lastBoardPatch(), {path: '/api/boards/b1', body: {off_limit_branches: ['release', 'main']}});
assert.equal(offField().value, 'release, main');
assert.ok(groupHead('landing').querySelector('.disclosure-summary').textContent.endsWith('off-limits release, main'));
offField().value = 'a  b,,c';
offField()._listeners.keydown.forEach(fn => fn({code: 'Enter', target: offField(), preventDefault() {}}));
await flush();
assert.deepEqual(lastBoardPatch().body, {off_limit_branches: ['a', 'b', 'c']}, 'enter saves, spaces split too');
offField().value = ' ';
blurField(offField());
await flush();
assert.deepEqual(lastBoardPatch().body, {off_limit_branches: []}, 'empty sends none');
assert.ok(text(offSection()).includes('none - this board may land on any branch'));
assert.deepEqual(boardsState[1].off_limit_branches, ['main', 'master', 'trunk'], 'beta is untouched');
// an api error shows in the status span and the typed text stays
fetchFail = true;
offField().value = 'bad..name';
blurField(offField());
await flush();
fetchFail = false;
assert.ok(offSection().querySelector('.boards-error').textContent.includes('not a branch name'));
assert.equal(offField().value, 'bad..name', 'the typed text is kept');
offField().value = 'main, master, trunk';
blurField(offField());
await flush();

press('KeyO', true);
assert.ok(!mod.bs.backdrop.parentNode, 'a second shift+o closes it');

// ---- a stored free choice behind an off switch is kept, shown as saved and not in force ----------
boardsState[0].merge_mode = 'free';
settingsState.allow_free_merge = null;
press('KeyO', true);
await flush();
assert.deepEqual(lit('merge mode'), ['review'], 'review is in force while the switch is off');
assert.ok(text(section('merge mode')).includes('saved for this board: free. it applies again when free merge is on in general.'));
assert.ok(groupHead('landing').querySelector('.disclosure-summary').textContent.startsWith('merge review'));
assert.equal(boardsState[0].merge_mode, 'free', 'the stored choice is untouched');
press('KeyO', true);
boardsState[0].merge_mode = null;

// ---- with the global switch off, the list stays visible but cannot be edited ---------------------
settingsState.off_limit_branches = 'off';
press('KeyO', true);
await flush();
assert.equal(section('off-limit branches').querySelector('.off-limit-input').disabled, true);
assert.ok(text(section('off-limit branches')).includes('turn them on in settings (o)'));
settingsState.off_limit_branches = null;
press('KeyO', true);
assert.ok(!mod.bs.backdrop.parentNode);

// ---- no board open: the panel says so instead of editing nothing -------------------------------
mod.setCurrent(null);
press('KeyO', true);
await flush();
assert.ok(text(panel()).includes('no board open'));

console.log('ok');
