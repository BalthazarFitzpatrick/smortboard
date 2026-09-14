// pre-flight checklist (h): h opens/closes the panel (not a Menu), the summary line counts ready
// checks, rows group by the api's group field, a fix line only shows for non-ok rows, and the
// re-check button reloads. run: node tests/js/preflight.mjs
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

const CHECKS = [
  {id: 'docker', group: 'machine', label: 'docker', status: 'ok', detail: 'docker answers.', fix: ''},
  {id: 'card-token', group: 'machine', label: 'card token', status: 'fail',
    detail: 'no token file.', fix: 'claude setup-token'},
  {id: 'repo-1-path', group: 'widgets', label: 'repo path', status: 'ok', detail: 'exists.', fix: ''},
  {id: 'repo-1-test-command', group: 'widgets', label: 'test command', status: 'warn',
    detail: 'none set.', fix: 'set one on the repo'},
];
responses.set('/api/preflight', stubJson(200, CHECKS));

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js'), smort('preflight.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {togglePreflightPanel, openPreflightPanel, closePreflightPanel, pf, BINDINGS}`)(SpyMenu, SpyDrawer);

const flush = () => new Promise(r => setTimeout(r, 0));
function press(code, target) {
  document._dispatch('keydown', {code, key: code, target: target || document.body, preventDefault() {}});
}

// h is in the contract, grouped with the other panels
assert.ok(mod.BINDINGS.some(b => b.code === 'KeyH'), 'KeyH must be bound');

// ---- h opens the panel and loads the checklist
press('KeyH');
await flush();
assert.ok(mod.pf.backdrop.parentNode, 'h should open the panel');
assert.ok(calls.some(c => c.path === '/api/preflight'), 'opening should load /api/preflight');

// ---- summary counts ready checks: 2 of 4 here (docker and the repo path)
assert.equal(mod.pf.summaryEl.textContent, '2 of 4 ready');

// ---- rows are grouped, each with a dot, and a fix line only for non-ok rows
const rows = [...mod.pf.listEl.querySelectorAll('.preflight-row')];
assert.equal(rows.length, 4, 'every check gets a row');
const groups = [...mod.pf.listEl.querySelectorAll('.preflight-group')];
assert.equal(groups.length, 2, 'machine and widgets are separate groups');

const dockerRow = rows.find(r => r.dataset.checkId === 'docker');
assert.ok(dockerRow.querySelector('.preflight-dot-ok'), 'an ok row gets the ok dot');
assert.equal(dockerRow.querySelectorAll('.preflight-fix').length, 0, 'an ok row shows no fix line');

const tokenRow = rows.find(r => r.dataset.checkId === 'card-token');
assert.ok(tokenRow.querySelector('.preflight-dot-fail'), 'a fail row gets the fail dot');
assert.equal(tokenRow.querySelector('.preflight-fix').textContent, 'claude setup-token');

const testCmdRow = rows.find(r => r.dataset.checkId === 'repo-1-test-command');
assert.ok(testCmdRow.querySelector('.preflight-dot-warn'), 'a warn row gets the warn dot');

// ---- h again closes it (the key that opens also closes, same as n)
press('KeyH');
assert.ok(!mod.pf.backdrop.parentNode, 'a second h should close the panel');

// ---- escape closes it too, once reopened
press('KeyH');
await flush();
assert.ok(mod.pf.backdrop.parentNode);
document._dispatch('keydown', {code: 'Escape', key: 'Escape', target: document.body, preventDefault() {}});
assert.ok(!mod.pf.backdrop.parentNode, 'escape should close the panel');

// ---- the re-check button reloads
press('KeyH');
await flush();
const before = calls.filter(c => c.path === '/api/preflight').length;
mod.pf.panel.querySelector('.preflight-recheck').onclick();
await flush();
const after = calls.filter(c => c.path === '/api/preflight').length;
assert.equal(after, before + 1, 're-check should call /api/preflight again');

console.log('ok');
