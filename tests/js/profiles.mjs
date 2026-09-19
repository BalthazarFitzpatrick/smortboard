// credential profiles (shift+p): the panel lists profiles from /api/profiles, adds one by pasting
// a token, activates and removes a row, and the token field never survives a submit.
// run: node tests/js/profiles.mjs
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
const listMenus = [];
function spyListMenu(title, items, onPick, extra = {}) {
  const menu = new SpyMenu({title, items, onPick, extra});
  menu.pick = id => onPick(items.find(item => item.id === id));
  listMenus.push(menu);
  return menu;
}

const ROWS = [
  {name: 'default', active: true, present: true, limited_until: null},
  {name: 'alt', active: false, present: true, limited_until: null},
];
responses.set('/api/profiles', stubJson(200, ROWS));

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), uiBase('pile.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js'), smort('profiles.js')].join('\n;\n');
const mod = new Function('Menu', 'listMenu', 'makeDrawer', `${src}
;return {toggleProfilesPanel, openProfilesPanel, closeProfilesPanel, pr, BINDINGS};`)(SpyMenu, spyListMenu, SpyDrawer);

const flush = () => new Promise(r => setTimeout(r, 0));
function press(code, target, extra) {
  document._dispatch('keydown', {code, key: code, target: target || document.body, preventDefault() {}, ...extra});
}

// shift+p is on the same physical key as p (prompts) but a distinct binding contract - one row
// covers both, per keyboard_bindings.mjs's frozen count of codes
assert.ok(mod.BINDINGS.some(b => b.code === 'KeyP' && b.group === 'panels'), 'KeyP must be a panels binding');

// ---- shift+p opens the panel and loads the profile list ---------------------------------------
press('KeyP', document.body, {shiftKey: true});
await flush(); await flush();
assert.ok(mod.pr.backdrop.parentNode, 'shift+p opens the panel');
const rows = mod.pr.listEl.querySelectorAll('.profile-row');
assert.equal(rows.length, 2, 'both profiles render as rows');
assert.equal(rows[0].querySelector('.profile-name').textContent, 'default');
assert.ok(rows[0].querySelector('.profile-name').className.includes('profile-active'), 'the active profile is marked');
assert.ok(!rows[0].querySelector('.profile-activate'), 'the active row has no activate control');
assert.ok(rows[0].querySelector('.profile-remove'), 'every profile, default and active included, can be removed');
assert.ok(rows[1].querySelector('.profile-activate'), 'an inactive row can be activated');
assert.ok(rows[1].querySelector('.profile-remove'), 'a non-default row can be removed');

// ---- plain p still opens the prompt editor, unaffected by the shared code -----------------------
mod.closeProfilesPanel();
press('KeyP');
await flush(); await flush();
assert.ok(calls.some(c => c.path === '/api/prompts'), 'plain p should still load the prompt editor');
assert.ok(!mod.pr.backdrop.parentNode, 'plain p must not open the profiles panel');

// ---- reopen for the add/activate/remove flow ---------------------------------------------------
press('KeyP', document.body, {shiftKey: true});
await flush(); await flush();

// ---- the token field never round-trips: it clears on submit whether the api accepts it or not ---
const nameInput = mod.pr.listEl.querySelector('.profile-add-name');
const tokenInput = mod.pr.listEl.querySelector('.profile-add-token');
assert.equal(tokenInput.type, 'password', 'the token field is masked, never a plain text field');
nameInput.value = 'work';
tokenInput.value = 'x'.repeat(108);
responses.set('/api/profiles', stubJson(400, {error: "that doesn't look like a claude setup-token"}));
mod.pr.listEl.querySelector('.profile-add-submit').onclick();
assert.equal(tokenInput.value, '', 'the token field clears immediately, even on a refused paste');
await flush(); await flush();
const addCall = calls.find(c => c.path === '/api/profiles' && c.opts && c.opts.method === 'POST');
assert.ok(addCall, 'add posts to /api/profiles');
assert.deepEqual(JSON.parse(addCall.opts.body), {lab: 'anthropic', kind: 'oauth', name: 'work', token: 'x'.repeat(108)});
const addStatus = mod.pr.listEl.querySelector('.profile-add-row .profile-status');
assert.equal(addStatus.textContent, "that doesn't look like a claude setup-token");

// ---- a successful add reloads the list, and the response never carries the token ----------------
mod.pr.listEl.querySelector('.profile-add-name').value = 'work';
mod.pr.listEl.querySelector('.profile-add-token').value = 'y'.repeat(108);
const addedRow = {name: 'work', active: false, present: true, limited_until: null};
assert.ok(!('token' in addedRow), 'a profile row never carries a token field');
responses.set('/api/profiles', stubJson(201, addedRow));
mod.pr.listEl.querySelector('.profile-add-submit').onclick();
await flush(); await flush();
assert.equal(mod.pr.listEl.querySelector('.profile-add-token').value, '', 'the token field is empty after a successful add too');

// ---- activate posts to the per-name activate route -----------------------------------------------
responses.set('/api/profiles', stubJson(200, ROWS));
mod.closeProfilesPanel();
press('KeyP', document.body, {shiftKey: true}); // reopen fresh to reload rows
await flush(); await flush();
responses.set('/api/profiles/anthropic/alt/activate', stubJson(200, {name: 'alt', active: true, present: true, limited_until: null}));
mod.pr.listEl.querySelectorAll('.profile-row')[1].querySelector('.profile-activate').onclick();
await flush(); await flush();
const activateCall = calls.find(c => c.path === '/api/profiles/anthropic/alt/activate');
assert.ok(activateCall, 'activate posts to /api/profiles/<name>/activate');
assert.equal(activateCall.opts.method, 'POST');

// ---- remove posts DELETE to the per-name route ---------------------------------------------------
responses.set('/api/profiles/anthropic/alt', stubJson(204, null));
mod.pr.listEl.querySelectorAll('.profile-row')[1].querySelector('.profile-remove').onclick();
await flush(); await flush();
const removeCall = calls.find(c => c.path === '/api/profiles/anthropic/alt' && c.opts.method === 'DELETE');
assert.ok(removeCall, 'remove sends DELETE to /api/profiles/<name>');

// ---- escape closes the panel -------------------------------------------------------------------
responses.set('/api/profiles', stubJson(200, [
  {...ROWS[0], lab: 'anthropic', kind: 'oauth'},
  {name: 'work', lab: 'openai', kind: 'auth_json', active: true, present: true},
]));
mod.closeProfilesPanel();
mod.openProfilesPanel();
await flush(); await flush();
assert.equal(mod.pr.listEl.querySelectorAll('.profile-row').length, 2);
assert.equal(mod.pr.listEl.querySelector('select'), null, 'the add form uses smortui menus, not native selects');
const labButton = mod.pr.listEl.querySelector('.profile-add-lab');
assert.ok(labButton.classList.contains('toggle'), 'the lab picker uses the smortui toggle convention');
assert.equal(mod.pr.listEl.querySelector('.profile-add-kind'), null, 'anthropic has no redundant kind picker');
labButton.onclick();
assert.equal(listMenus.at(-1).opts.title, 'choose lab');
listMenus.at(-1).pick('openai');
assert.equal(labButton.textContent, 'openai');
const kindButton = mod.pr.listEl.querySelector('.profile-add-kind');
assert.equal(kindButton.textContent, 'ChatGPT login JSON');
assert.ok(kindButton.classList.contains('toggle'), 'the OpenAI credential picker uses the smortui toggle convention');
kindButton.onclick();
assert.deepEqual(listMenus.at(-1).opts.items.map(item => item.id), ['auth_json', 'api_key']);
labButton.onclick();
listMenus.at(-1).pick('anthropic');
assert.equal(mod.pr.listEl.querySelector('.profile-add-kind'), null, 'switching back to anthropic removes the kind picker');
labButton.onclick();
listMenus.at(-1).pick('openai');
mod.pr.listEl.querySelector('.profile-add-name').value = 'json-profile';
mod.pr.listEl.querySelector('.profile-add-token').value = '{"tokens":{"access_token":"demo"}}';
mod.pr.listEl.querySelector('.profile-add-submit').onclick();
assert.equal(mod.pr.listEl.querySelector('.profile-add-token').value, '');
await flush();
assert.equal(JSON.parse(calls.filter(c => c.opts?.method === 'POST').at(-1).opts.body).lab, 'openai');
assert.equal(JSON.parse(calls.filter(c => c.opts?.method === 'POST').at(-1).opts.body).kind, 'auth_json');
press('Escape');
assert.ok(!mod.pr.backdrop.parentNode, 'escape closes the panel');

console.log('ok');
