// the beta's "submit issue" control: first in the bar corner, ahead of the board's own numbers,
// opening the repo's bug form in a new tab with the running version prefilled into the one field
// id that form actually declares (version, .github/ISSUE_TEMPLATE/bug.yml).
// run: node tests/js/issue_button.mjs
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
function fetchStub(path) {
  const resp = responses.get(path);
  return resp ? Promise.resolve(resp) : new Promise(() => {}); // anything else never answers
}

installStubDom({fetchImpl: fetchStub});
responses.set('/api/boards', stubJson(200, []));
responses.set('/health', stubJson(200, {ok: true, version: '0.1.0', operator: 'Balthazar'}));

const boardBar = element('div', 'board-bar');
boardBar.id = 'board-bar';
const bucketRow = element('div', 'bucket-row');
bucketRow.id = 'bucket-row';
['todo', 'doing'].forEach(status => {
  const bucket = element('div', 'bucket');
  bucket.dataset.status = status;
  bucket.appendChild(element('div', 'bucket-label'));
  bucket.appendChild(element('div', 'bucket-rows'));
  bucketRow.appendChild(bucket);
});
document.body.appendChild(boardBar);
document.body.appendChild(bucketRow);

class SpyMenu { constructor(opts) { this.opts = opts; } openAt() { return this; } refresh() {} close() {} }
function SpyDrawer() {
  return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false};
}

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'),
  uiBase('pile.js'), smort('columns.js'), smort('card_panel.js'), smort('chat.js'),
  smort('shortcuts.js'), smort('board.js'), smort('scheduler.js'), smort('settings.js')]
  .join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {ISSUE_FORM_URL, cornerRef: () => barCorner(), buttonRef: () => document.getElementById('issue-button')};`)(SpyMenu, SpyDrawer);

const flush = () => new Promise(r => setTimeout(r, 0));

// ---- it exists, and it is the FIRST thing in the corner group -----------------------------------
const button = mod.buttonRef();
assert.ok(button, 'the corner carries a submit-issue control');
assert.equal(mod.cornerRef().children[0], button,
  'it sits left of the board\'s own numbers - first in the corner, not appended after them');
assert.ok(!boardBar.children.includes(button), 'and never inside #board-bar, which renderBoardBar wipes');

// ---- it says what the operator asked it to say, and looks like the rest of the chrome -----------
assert.equal(button.textContent, 'beta: submit issue');
assert.ok(button.className.includes('toggle'), 'it is a toggle like the settings button, not a new shape');

// ---- an <a>, so tab reaches it and enter opens it - no new single-key binding taken -------------
assert.equal(button.tag, 'a', 'a real link: keyboard-operable without inventing a shortcut');
assert.equal(button.target, '_blank', 'the form opens in its own tab, never over the board');
assert.ok(button.rel.includes('noopener'), 'and cannot reach back into the board');

// ---- it points at the repo's bug form ------------------------------------------------------------
assert.ok(button.href.startsWith(mod.ISSUE_FORM_URL), 'it opens the bug form the beta labels');
assert.ok(mod.ISSUE_FORM_URL.includes('template=bug.yml'), 'by template, so the form fields show');

// ---- once /health answers, the version the form asks for first is filled in ----------------------
await flush(); await flush();
assert.ok(mod.buttonRef().href.includes('&version=0.1.0'),
  'the running version prefills bug.yml\'s first field');
assert.ok(!mod.buttonRef().href.includes('&os='), 'no other field id is guessed at');

console.log('ok');
