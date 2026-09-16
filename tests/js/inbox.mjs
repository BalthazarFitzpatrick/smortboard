// the attention inbox (n): the board-bar indicator polls and dims at zero, n opens/closes the
// panel (not a Menu), cards render title/reason/question, answering posts and shows "resumed"
// without eating board shortcuts, a refusal renders inline instead of clearing the card, the
// header scope cycles all boards <-> each board with waiting cards and falls back when one
// empties, and the list itself picks spread/fan/pile the same way a board column does.
// run: node tests/js/inbox.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset, stubLayout} from './dom_stub.mjs';

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
// plenty of room - a wide, tall box so the default 3-row fixtures below always spread (regime 1)
// unless a test deliberately shrinks it to force a fan or a pile
stubLayout.rect = () => ({left: 0, top: 0, right: 800, bottom: 2000, width: 800, height: 2000});
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

function SpyDrawer() {
  return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false};
}
class SpyMenu { constructor(opts) { this.opts = opts; } openAt() { return this; } refresh() {} close() {} }

const ROWS = [
  {card_id: 'c1', board_id: 'b1', board_name: 'alpha', title: 'widen the lease', reason: 'AGENT_QUESTION', question: 'should I widen the lease?', since: '2026-01-01T00:00:00', action: 'Answer the agent\'s question in the inbox (n) - the answer resumes it.'},
  {card_id: 'c2', board_id: 'b1', board_name: 'alpha', title: 'fix the flaky test', reason: 'TESTS_FAILED', question: '`pytest` exited 1', since: '2026-01-02T00:00:00'},
  {card_id: 'c3', board_id: 'b1', board_name: 'alpha', title: 'edited outside its lease', reason: 'LEASE_CONFLICT', question: 'blocked: lease conflict', since: '2026-01-03T00:00:00', wants: ['ui/board.js', 'docs/notes.md']},
];
responses.set('/api/attention', stubJson(200, ROWS));

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), uiBase('pile.js'), smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'), smort('board.js'), smort('inbox.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {toggleInboxPanel, openInboxPanel, closeInboxPanel, loadInbox, cycleScope, computeInboxFit,
  computeScopes, resolveScope, ib, BINDINGS, indicatorRef: () => indicatorEl};`)(SpyMenu, SpyDrawer);

const flush = () => new Promise(r => setTimeout(r, 0));
function press(code, target) {
  document._dispatch('keydown', {code, key: code, target: target || document.body, preventDefault() {}});
}
// the stub has no real event propagation - an input's own keydown listener (added via
// addEventListener, not the global handler) has to be invoked directly, same as a real browser
// would dispatch to the element before bubbling to document
function fireKeydown(el, evt) {
  const full = {target: el, preventDefault() {}, stopPropagation() {}, ...evt};
  (el._listeners.keydown || []).forEach(fn => fn(full));
}

// ---- KeyN is in the panels group, and the indicator polls at startup ------------------------------
assert.ok(mod.BINDINGS.some(b => b.code === 'KeyN' && b.group === 'panels'), 'n must be a panels binding');
await flush(); await flush();
const indicator = mod.indicatorRef();
assert.ok(indicator, 'the indicator is built onto the board bar at startup');
assert.equal(indicator.textContent, '3', 'the indicator shows the attention count');
assert.ok(!indicator.classList.contains('dim'), 'a nonzero count is not dim');

// ---- n opens the panel, not a Menu (SpyMenu.openAt would have been recorded, it was not for n) ----
press('KeyN');
await flush(); await flush();
assert.ok(mod.ib.backdrop.parentNode, 'the panel is attached once opened');
assert.equal(mod.ib.headerLabel.textContent, 'all boards (3)', 'a single board opens scoped to all boards');
let cards = mod.ib.panel.querySelectorAll('.inbox-card');
assert.equal(cards.length, 3, 'all three blocked cards render as cards');
assert.equal(cards[0].querySelector('.inbox-title').textContent, 'widen the lease');
assert.equal(cards[0].querySelector('.inbox-reason').textContent, 'AGENT_QUESTION');
assert.equal(cards[0].querySelector('.inbox-question').textContent, 'should I widen the lease?');
// the call to action leads each card, above the note it came from
assert.equal(cards[0].querySelector('.inbox-action').textContent,
  'next: Answer the agent\'s question in the inbox (n) - the answer resumes it.');
assert.equal(cards[1].querySelector('.inbox-action').textContent, 'next: open the card',
  'a card with no action text still gets a line to act on');

// ---- header scope: one board only -> the header still lets you flip to that board's own scope ----
mod.cycleScope(1);
assert.equal(mod.ib.headerLabel.textContent, 'alpha (3)', 'cycling right lands on the one board scope');
assert.equal(mod.ib.panel.querySelectorAll('.inbox-card').length, 3);
mod.cycleScope(1);
assert.equal(mod.ib.headerLabel.textContent, 'all boards (3)', 'cycling wraps back to all boards');
mod.cycleScope(-1);
assert.equal(mod.ib.headerLabel.textContent, 'alpha (3)', 'left cycles the other way too');
mod.cycleScope(-1);
assert.equal(mod.ib.headerLabel.textContent, 'all boards (3)');

// ---- typing in the answer input never fires a board shortcut (n does not close the panel) --------
cards = mod.ib.panel.querySelectorAll('.inbox-card');
const input0 = cards[0].querySelector('.inbox-answer');
input0.value = 'go ahead';
press('KeyN', input0);
assert.ok(mod.ib.backdrop.parentNode, 'n typed into the answer input must not toggle the panel');

// ---- arrowleft/right in the answer field must not cycle the scope, only real navigation does ------
const before = mod.ib.headerLabel.textContent;
press('ArrowLeft', input0);
assert.equal(mod.ib.headerLabel.textContent, before, 'arrows typed into a text field never cycle the scope');

// ---- enter sends the answer, and a success shows "resumed" ---------------------------------------
responses.set('/api/cards/c1/answer', stubJson(202, {card_id: 'c1', running: true, phase: 'running'}));
fireKeydown(input0, {code: 'Enter', key: 'Enter'});
await flush(); await flush();
const answerCall = calls.find(c => c.path === '/api/cards/c1/answer');
assert.ok(answerCall, 'enter should post the answer route');
assert.equal(answerCall.opts.method, 'POST');
assert.deepEqual(JSON.parse(answerCall.opts.body), {message: 'go ahead'});
const status0 = cards[0].querySelector('.inbox-status');
assert.equal(status0.textContent, 'resumed');
assert.ok(status0.className.includes('inbox-ok'));
assert.ok(input0.disabled, 'the input is disabled once answered');

// ---- a refusal renders inline and leaves the input usable to try again ---------------------------
const input1 = cards[1].querySelector('.inbox-answer');
input1.value = 'try again';
responses.set('/api/cards/c2/answer', stubJson(409, {error: 'this card is still running'}));
fireKeydown(input1, {code: 'Enter', key: 'Enter'});
await flush(); await flush();
const status1 = cards[1].querySelector('.inbox-status');
assert.equal(status1.textContent, 'this card is still running');
assert.ok(status1.className.includes('inbox-error'));
assert.ok(!input1.disabled, 'a refusal leaves the input open to retry');

// ---- a LEASE_CONFLICT card shows the wanted paths and an approve control -------------------------
assert.equal(cards[2].querySelector('.inbox-wants').textContent, 'wants: ui/board.js, docs/notes.md');
assert.ok(cards[2].querySelector('.inbox-answer'), 'the answer field still shows too');
const approveButton = cards[2].querySelector('.inbox-approve');
assert.ok(approveButton.classList.contains('toggle'), 'reuses the existing toggle button class');
responses.set('/api/cards/c3/lease/approve', stubJson(202, {card_id: 'c3', running: true, phase: 'running'}));
approveButton.onclick();
await flush(); await flush();
const approveCall = calls.find(c => c.path === '/api/cards/c3/lease/approve');
assert.ok(approveCall, 'the approve control posts to the lease/approve route');
assert.equal(approveCall.opts.method, 'POST');
assert.deepEqual(JSON.parse(approveCall.opts.body), {paths: ['ui/board.js', 'docs/notes.md']});

// ---- a lease/approve refusal renders inline on that card too ---------------------------------------
responses.set('/api/attention', stubJson(200, ROWS));
const cards3 = mod.ib.panel.querySelectorAll('.inbox-card');
const approveButton2 = cards3[2].querySelector('.inbox-approve');
responses.set('/api/cards/c3/lease/approve', stubJson(409, {error: 'this card is not blocked on a lease conflict'}));
approveButton2.onclick();
await flush(); await flush();
const approveStatus = cards3[2].querySelector('.inbox-lease-approve .inbox-status');
assert.equal(approveStatus.textContent, 'this card is not blocked on a lease conflict');
assert.ok(approveStatus.className.includes('inbox-error'));

// ---- arrowdown moves focus card by card, and the focused card is the one wearing tabIndex 0 -------
cards3[0].focus();
mod.ib.focusIndex = 0;
fireKeydown(mod.ib.listEl, {code: 'ArrowDown', key: 'ArrowDown'});
await flush();
assert.equal(mod.ib.focusIndex, 1, 'arrowdown moves focus to the next card');
const focused = mod.ib.panel.querySelector('.inbox-card[data-idx="1"]');
assert.equal(focused.tabIndex, 0, 'the focused card is the one left tabbable');
assert.ok(focused.focused, 'the focused card actually took dom focus (wears .focus-glow via :focus)');
assert.ok(focused.className.includes('focus-glow'), 'every inbox card wears the focus-glow treatment');

// ---- escape in the answer input returns focus to its card, not to the panel ------------------------
const card1 = cards[1];
fireKeydown(input1, {code: 'Escape', key: 'Escape'});
assert.ok(card1.focused, 'escape from the answer input returns focus to its card');
assert.ok(mod.ib.backdrop.parentNode, 'the panel itself stays open');

// ---- escape on a focused card (not its field) closes the panel -------------------------------------
fireKeydown(card1, {code: 'Escape', key: 'Escape', target: card1});
assert.equal(mod.ib.backdrop.parentNode, null, 'escape on the card itself closes the panel');

// ---- n again reopens it -----------------------------------------------------------------------------
press('KeyN');
await flush(); await flush();
assert.ok(mod.ib.backdrop.parentNode, 'n reopens the panel');
press('KeyN');
assert.equal(mod.ib.backdrop.parentNode, null, 'n again closes it');

// ---- scopes: all boards first, then each board with waiting cards, in order ------------------------
const twoBoardRows = [
  ...ROWS,
  {card_id: 'c4', board_id: 'b2', board_name: 'beta', title: 'crashed on startup', reason: 'CRASH', question: 'traceback here', since: '2026-01-04T00:00:00'},
];
const scopes = mod.computeScopes(twoBoardRows);
assert.deepEqual(scopes.map(s => s.key), ['all', 'b1', 'b2']);
assert.deepEqual(scopes.map(s => s.rows.length), [4, 3, 1]);
assert.equal(mod.resolveScope(scopes, 'b2'), 'b2', 'a scope that still has cards is kept');
assert.equal(mod.resolveScope(scopes, 'gone'), 'all', 'an unknown/emptied scope falls back to all boards');
const noB2 = mod.computeScopes(ROWS); // b2 has nothing waiting any more
assert.equal(mod.resolveScope(noB2, 'b2'), 'all', 'a scope that emptied out falls back to all boards');

// ---- inbox fit: the same three regimes a board column picks, for a fixed landscape card height ----
// plenty of room: every card fits whole, spread apart
assert.deepEqual(mod.computeInboxFit(3, 1000, 10, 170), {regime: 1, n: 3, card: 170, piles: false, scrolls: false});
// not enough to spread, enough to fan (each covered card still shows PEEK of itself)
const fanFit = mod.computeInboxFit(3, 300, 10, 170);
assert.equal(fanFit.regime, 2);
assert.equal(fanFit.n, 3);
assert.equal(fanFit.piles, false);
// too few cards to bother piling (< MIN_PILED_CARDS) even with no room - still fans, and scrolls
const tinyFit = mod.computeInboxFit(3, 50, 10, 170);
assert.equal(tinyFit.regime, 2);
assert.equal(tinyFit.scrolls, true);
// enough cards and little enough room that even the fan does not fit - piled between two piles
const piledFit = mod.computeInboxFit(8, 300, 10, 170);
assert.equal(piledFit.regime, 3);
assert.equal(piledFit.piles, true);
assert.ok(piledFit.n < 8, 'the piled group holds fewer than the full count');

console.log('ok');
