// the prompt editor (p): opens and loads the three roles, switching role shows its own body,
// saving PATCHes the edited body and shows the returned version, typing p inside the textarea
// never fires a board shortcut, and a failed save renders an error line rather than throwing.
// run: node tests/js/prompt_editor.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => readFileSync(new URL(`../ui_base/ui_base/assets/${p}`, root), 'utf8');
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

class SpyMenu {
  constructor(opts) { this.opts = opts; }
  openAt() { return this; }
  refresh() {}
  close() {}
}
function SpyDrawer(opts) {
  return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false};
}

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {togglePromptEditor, pe, BINDINGS};`)(SpyMenu, SpyDrawer);

function press(code, target) {
  document._dispatch('keydown', {code, key: code, target: target || document.body, preventDefault() {}});
}

const PROMPTS = [
  {role: 'orchestrator', version: 0, body: 'you are the orchestrator', is_default: true},
  {role: 'worker', version: 3, body: 'you are the worker', is_default: false},
  {role: 'reviewer', version: 0, body: 'you are the reviewer', is_default: true},
];
responses.set('/api/prompts', stubJson(200, PROMPTS));

// ---- p opens and loads the three roles, orchestrator's body shown first --------------------------
mod.togglePromptEditor();
await new Promise(r => setTimeout(r, 0));
await new Promise(r => setTimeout(r, 0));
assert.ok(mod.pe.backdrop.parentNode, 'the panel is attached once opened');
assert.equal(mod.pe.textarea.value, 'you are the orchestrator', 'orchestrator loads first');
const roleLabels = [...mod.pe.roleRow.children].map(b => b.querySelector('.prompt-role-version').textContent);
assert.deepEqual(roleLabels, ['default', 'version 3', 'default'], 'each role shows default or its version');

// ---- switching role shows its own body -------------------------------------------------------------
mod.pe.roleRow.children[1].onclick();
assert.equal(mod.pe.textarea.value, 'you are the worker', 'switching role loads that role\'s body');

// ---- typing p inside the textarea does not close the panel or fire a board shortcut ---------------
mod.pe.textarea.value = 'you are the worker, edited';
press('KeyP', mod.pe.textarea);
assert.ok(mod.pe.backdrop.parentNode, 'p typed into the textarea must not close the panel');

// ---- saving sends PATCH with the edited body and shows the returned version -----------------------
responses.set('/api/prompts/worker', stubJson(200, {role: 'worker', version: 4, body: 'you are the worker, edited', is_default: false}));
const saveBtn = mod.pe.panel.querySelector('.prompt-save');
saveBtn.onclick();
await new Promise(r => setTimeout(r, 0));
const patch = calls.find(c => c.path === '/api/prompts/worker' && c.opts?.method === 'PATCH');
assert.ok(patch, 'save should PATCH the role route');
assert.deepEqual(JSON.parse(patch.opts.body), {body: 'you are the worker, edited'}, 'the patch body carries the edited text');
assert.ok(mod.pe.statusEl.textContent.includes('version 4'), 'the status line names the new version');

// ---- a failed save shows an error line ---------------------------------------------------------------
mod.pe.roleRow.children[2].onclick(); // reviewer, untouched
responses.set('/api/prompts/reviewer', stubJson(404, {error: "no such role 'reviewer'"}));
mod.pe.panel.querySelector('.prompt-save').onclick();
await new Promise(r => setTimeout(r, 0));
assert.ok(mod.pe.statusEl.className.includes('prompt-error'), 'a failed save renders in the warn tone');
assert.ok(mod.pe.statusEl.textContent.length > 0, 'a failed save renders a readable line');

// ---- p closes the open panel -------------------------------------------------------------------------
mod.togglePromptEditor();
assert.equal(mod.pe.backdrop.parentNode, null, 'p again closes the panel');

console.log('ok');
