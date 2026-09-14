// the attention inbox (n): the board-bar indicator polls and dims at zero, n opens/closes the
// panel (not a Menu), rows render title/reason/question, answering posts and shows "resumed"
// without eating board shortcuts, and a refusal renders inline instead of clearing the row.
// run: node tests/js/inbox.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => readFileSync(new URL(`../smortui/ui_base/assets/${p}`, root), 'utf8');
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

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'), smort('board.js'), smort('inbox.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {toggleInboxPanel, openInboxPanel, closeInboxPanel, ib, BINDINGS, indicatorRef: () => indicatorEl};`)(SpyMenu, SpyDrawer);

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
const rows = mod.ib.panel.querySelectorAll('.inbox-row');
assert.equal(rows.length, 3, 'all three blocked cards render as rows');
assert.equal(rows[0].querySelector('.inbox-title').textContent, 'widen the lease');
assert.equal(rows[0].querySelector('.inbox-reason').textContent, 'AGENT_QUESTION');
assert.equal(rows[0].querySelector('.inbox-question').textContent, 'should I widen the lease?');
// the call to action leads each row, above the note it came from
assert.equal(rows[0].querySelector('.inbox-action').textContent,
  'next: Answer the agent\'s question in the inbox (n) - the answer resumes it.');
assert.equal(rows[1].querySelector('.inbox-action').textContent, 'next: open the card',
  'a row with no action text still gets a line to act on');

// ---- typing in the answer input never fires a board shortcut (n does not close the panel) --------
const input0 = rows[0].querySelector('.inbox-answer');
input0.value = 'go ahead';
press('KeyN', input0);
assert.ok(mod.ib.backdrop.parentNode, 'n typed into the answer input must not toggle the panel');

// ---- enter sends the answer, and a success shows "resumed" ---------------------------------------
responses.set('/api/cards/c1/answer', stubJson(202, {card_id: 'c1', running: true, phase: 'running'}));
fireKeydown(input0, {code: 'Enter', key: 'Enter'});
await flush(); await flush();
const answerCall = calls.find(c => c.path === '/api/cards/c1/answer');
assert.ok(answerCall, 'enter should post the answer route');
assert.equal(answerCall.opts.method, 'POST');
assert.deepEqual(JSON.parse(answerCall.opts.body), {message: 'go ahead'});
const status0 = rows[0].querySelector('.inbox-status');
assert.equal(status0.textContent, 'resumed');
assert.ok(status0.className.includes('inbox-ok'));
assert.ok(input0.disabled, 'the input is disabled once answered');

// ---- a refusal renders inline and leaves the input usable to try again ---------------------------
const input1 = rows[1].querySelector('.inbox-answer');
input1.value = 'try again';
responses.set('/api/cards/c2/answer', stubJson(409, {error: 'this card is still running'}));
fireKeydown(input1, {code: 'Enter', key: 'Enter'});
await flush(); await flush();
const status1 = rows[1].querySelector('.inbox-status');
assert.equal(status1.textContent, 'this card is still running');
assert.ok(status1.className.includes('inbox-error'));
assert.ok(!input1.disabled, 'a refusal leaves the input open to retry');

// ---- a LEASE_CONFLICT row shows the wanted paths and an approve control -------------------------
assert.equal(rows[2].querySelector('.inbox-wants').textContent, 'wants: ui/board.js, docs/notes.md');
assert.ok(rows[2].querySelector('.inbox-answer'), 'the answer field still shows too');
const approveButton = rows[2].querySelector('.inbox-approve');
assert.ok(approveButton.classList.contains('toggle'), 'reuses the existing toggle button class');
responses.set('/api/cards/c3/lease/approve', stubJson(202, {card_id: 'c3', running: true, phase: 'running'}));
approveButton.onclick();
await flush(); await flush();
const approveCall = calls.find(c => c.path === '/api/cards/c3/lease/approve');
assert.ok(approveCall, 'the approve control posts to the lease/approve route');
assert.equal(approveCall.opts.method, 'POST');
assert.deepEqual(JSON.parse(approveCall.opts.body), {paths: ['ui/board.js', 'docs/notes.md']});

// ---- a lease/approve refusal renders inline on that row too ---------------------------------------
responses.set('/api/attention', stubJson(200, ROWS));
const rows3 = mod.ib.panel.querySelectorAll('.inbox-row');
const approveButton2 = rows3[2].querySelector('.inbox-approve');
responses.set('/api/cards/c3/lease/approve', stubJson(409, {error: 'this card is not blocked on a lease conflict'}));
approveButton2.onclick();
await flush(); await flush();
const approveStatus = rows3[2].querySelector('.inbox-lease-approve .inbox-status');
assert.equal(approveStatus.textContent, 'this card is not blocked on a lease conflict');
assert.ok(approveStatus.className.includes('inbox-error'));

// ---- escape, from inside the answer input, closes the panel --------------------------------------
fireKeydown(input1, {code: 'Escape', key: 'Escape'});
assert.equal(mod.ib.backdrop.parentNode, null, 'escape from the answer input closes the panel');

// ---- n again reopens it -----------------------------------------------------------------------------
press('KeyN');
await flush(); await flush();
assert.ok(mod.ib.backdrop.parentNode, 'n reopens the panel');
press('KeyN');
assert.equal(mod.ib.backdrop.parentNode, null, 'n again closes it');

console.log('ok');
