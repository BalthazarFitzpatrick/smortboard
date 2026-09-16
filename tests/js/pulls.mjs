// pull requests (v): every open PR across every board, in merge order - v opens/closes the panel
// (not a Menu), rows render board/title/state/link, conflicting and dependency-unmerged markers
// show only when the row carries them, enter opens the active row's PR in a new tab, y accepts the
// active row's card without closing the panel and refuses without dropping the row.
// run: node tests/js/pulls.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const responses = new Map();
const calls = [];
function stubJson(status, body) {
  return {ok: status >= 200 && status < 300, status, json: async () => body};
}
function fetchStub(path, opts) {
  calls.push({path, opts});
  const resp = responses.get(path);
  return Promise.resolve(resp || stubJson(404, {error: 'no stub for ' + path}));
}

installStubDom({fetchImpl: fetchStub});
responses.set('/api/boards', stubJson(200, []));

const opened = [];
window.open = (...args) => { opened.push(args); };

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

const ROWS = [
  {
    card_id: 'c1', board_id: 'b1', board_name: 'alpha', title: 'fix the thing',
    url: 'https://example/pr/1', state: 'open', mergeable: 'mergeable', depends_on: [],
    conflicting: false, dependency_unmerged: false, position: 1,
  },
  {
    card_id: 'c2', board_id: 'b1', board_name: 'alpha', title: 'needs a rebase',
    url: 'https://example/pr/2', state: 'open', mergeable: 'conflicting', depends_on: ['c1'],
    conflicting: true, dependency_unmerged: true, position: 2,
  },
];
responses.set('/api/pulls', stubJson(200, ROWS));

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), uiBase('pile.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js'), smort('pulls.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {togglePullsPanel, openPullsPanel, closePullsPanel, pl, BINDINGS};`)(SpyMenu, SpyDrawer);

const flush = () => new Promise(r => setTimeout(r, 0));
function press(code) {
  document._dispatch('keydown', {code, key: code, target: document.body, preventDefault() {}});
}

// ---- v is a panels binding ------------------------------------------------------------------------
assert.ok(mod.BINDINGS.some(b => b.code === 'KeyV' && b.group === 'panels'), 'v must be a panels binding');

// ---- v opens the panel, not a Menu -----------------------------------------------------------------
press('KeyV');
await flush(); await flush();
assert.ok(mod.pl.backdrop.parentNode, 'the panel is attached once opened');
const pullsCall = calls.find(c => c.path === '/api/pulls');
assert.ok(pullsCall, 'opening the panel loads /api/pulls');

const rows = mod.pl.panel.querySelectorAll('.pulls-row');
assert.equal(rows.length, 2, 'both open pull requests render as rows, in the order the api gave them');
assert.equal(rows[0].querySelector('.pulls-title').textContent, 'fix the thing');
assert.equal(rows[0].querySelector('.pulls-board').textContent, 'alpha');
assert.equal(rows[0].querySelector('.pulls-state').textContent, 'open');
assert.equal(rows[0].querySelector('.pulls-link').textContent, 'https://example/pr/1');

// ---- markers only show for the two states that need a look -----------------------------------------
assert.equal(rows[0].querySelector('.pulls-marker-conflicting'), null, 'a clean pr carries no conflict marker');
assert.equal(rows[0].querySelector('.pulls-marker-dependency'), null, 'a clean pr carries no dependency marker');
assert.ok(rows[1].querySelector('.pulls-marker-conflicting'), 'a conflicting pr is marked');
assert.ok(rows[1].querySelector('.pulls-marker-dependency'), 'a pr with an unmerged dependency is marked');

// ---- the panel opens with the first row active, and arrow keys move it -----------------------------
assert.ok(rows[0].classList.contains('pulls-row-active'), 'the first row starts active');
press('ArrowDown');
assert.ok(!rows[0].classList.contains('pulls-row-active') && rows[1].classList.contains('pulls-row-active'),
  'arrow down moves the active row');

// ---- enter opens the active row's pull request in a new tab, not the one that used to be active ----
press('Enter');
assert.equal(opened.length, 1, 'enter opens exactly one tab');
assert.equal(opened[0][0], 'https://example/pr/2', 'enter opens the active row, not the first one');

// ---- y accepts the active row's card and reloads the list without closing the panel ----------------
responses.set('/api/cards/c2/accept', stubJson(200, {id: 'c2', status: 'accepted'}));
const REFRESHED = [ROWS[0]];
responses.set('/api/pulls', stubJson(200, REFRESHED));
press('KeyY');
await flush(); await flush();
const acceptCall = calls.find(c => c.path === '/api/cards/c2/accept');
assert.ok(acceptCall, 'y should post accept for the active row');
assert.equal(acceptCall.opts.method, 'POST');
assert.ok(mod.pl.backdrop.parentNode, 'the panel stays open once a row is accepted');
const rowsAfterAccept = mod.pl.panel.querySelectorAll('.pulls-row');
assert.equal(rowsAfterAccept.length, 1, 'the list reloaded and now reflects the fresh api response');

// ---- a refusal shows inline and leaves the row on the list ------------------------------------------
responses.set('/api/cards/c1/accept', stubJson(409, {error: 'this card is still running'}));
press('KeyY');
await flush(); await flush();
const status0 = mod.pl.panel.querySelector('.pulls-row .pulls-status');
assert.equal(status0.textContent, 'this card is still running');
assert.ok(status0.className.includes('pulls-error'));
assert.equal(mod.pl.panel.querySelectorAll('.pulls-row').length, 1, 'a refusal does not drop the row');

// ---- escape closes the panel, v reopens it, v again closes it ---------------------------------------
press('Escape');
assert.equal(mod.pl.backdrop.parentNode, null, 'escape closes the panel');
press('KeyV');
await flush(); await flush();
assert.ok(mod.pl.backdrop.parentNode, 'v reopens the panel');
press('KeyV');
assert.equal(mod.pl.backdrop.parentNode, null, 'v again closes it');

// ---- an empty list still opens cleanly ---------------------------------------------------------------
responses.set('/api/pulls', stubJson(200, []));
press('KeyV');
await flush(); await flush();
assert.ok(mod.pl.panel.querySelector('.hazard-placeholder'), 'nothing open renders the empty placeholder');

console.log('ok');
