// settings panel (o): the top-right button and the o key open it, esc and an outside click close
// it. a preset row and six accordion groups, fed by one GET /api/settings and one GET /api/boards.
// run: UI_BASE_ASSETS_DIR=<ui_base assets dir> node tests/js/settings.mjs
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
const settingsState = {
  mission_control_read_paths: [],
  max_parallel: null,
  worker_lab: 'openai',
  fold_cross_lab_fallback: [
    {ref: 'anthropic/opus', effort: null}, {ref: 'openai/gpt-6-astra', effort: 'high'},
  ],
  // saved before the model's effort levels narrowed: sol has no max
  worker_cross_lab_fallback: [{ref: 'openai/gpt-5.6-sol', effort: 'max'}],
};
const boardsState = [
  {id: 'b1', name: 'alpha', max_parallel: null, daily_budget_usd: null, merge_mode: 'free', lease_mode: 'soft'},
  {id: 'b2', name: 'beta', max_parallel: 1, daily_budget_usd: null, run_mode: 'open'},
];
const calls = [];
// knobs a test flips to make the next call fail, the way the real server would
const stubControl = {refuseNextSettingsPatch: false, importRefusal: null};
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
  if (path === '/api/catalog') return Promise.resolve(stubJson(200, {
    anthropic: {available: true, models: [
      {id: 'fable', label: 'fable 5', tier: 'deep', effort_levels: ['low', 'high', 'max'], default_effort: 'high'},
      {id: 'opus', label: 'Opus', tier: 'deep'},
      {id: 'sonnet', label: 'Sonnet', tier: 'standard'},
      {id: 'haiku', label: 'Haiku', tier: 'light', effort_levels: []},
    ]},
    openai: {available: true, models: [
      {id: 'gpt-6-astra', label: 'gpt-6-astra', tier: 'deep', effort_levels: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'], default_effort: 'medium'},
      {id: 'gpt-5.6-sol', label: 'gpt-5.6-sol', tier: 'standard', effort_levels: ['low', 'medium', 'high', 'xhigh'], default_effort: 'low'},
      {id: 'gpt-5.6-terra', label: 'Terra', tier: 'standard'},
      {id: 'gpt-5.6-luna', label: 'Luna', tier: 'light'},
    ]},
    gemini: {available: false, unavailable_reason: 'no credential', models: [{id: 'gem', label: 'gem', tier: 'standard'}]},
  }));
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
    if ('daily_budget_usd' in body) {
      if (body.daily_budget_usd !== null && (typeof body.daily_budget_usd !== 'number' || body.daily_budget_usd <= 0)) {
        return Promise.resolve(stubJson(400, {error: 'daily_budget_usd must be a positive number or null'}));
      }
      board.daily_budget_usd = body.daily_budget_usd;
    }
    return Promise.resolve(stubJson(200, {...board}));
  }
  // import only ever adds: the file's boards come back under new ids beside the existing ones
  if (path === '/api/import' && opts && opts.method === 'POST') {
    if (stubControl.importRefusal) return Promise.resolve(stubJson(400, {error: stubControl.importRefusal}));
    return Promise.resolve(stubJson(201, {boards: [{id: 'b9', name: 'restored', max_parallel: null}]}));
  }
  // anything else never answers, as on main: board.js's own startup fetches (loadBoards) would
  // otherwise reject unhandled and end the run before a single assertion
  if (path !== '/api/settings') return new Promise(() => {});
  if (!opts || !opts.method || opts.method === 'GET') return Promise.resolve(stubJson(200, {...settingsState}));
  if (opts.method === 'PATCH') {
    if (stubControl.refuseNextSettingsPatch) {
      stubControl.refuseNextSettingsPatch = false;
      return Promise.resolve(stubJson(500, {error: 'store error: disk full'}));
    }
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
    if ('repos_home' in body) {
      if (body.repos_home === '~/nope') return Promise.resolve(stubJson(400, {error: '~/nope does not exist or is not a folder'}));
      settingsState.repos_home = body.repos_home === '~/dev' ? '/home/op/dev' : body.repos_home;
    }
    // every other key is stored as sent, so a save reads back like the real store
    const special = ['mission_control_read_paths', 'max_parallel', 'repos_home'];
    for (const [key, value] of Object.entries(body)) if (!special.includes(key)) settingsState[key] = value;
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
const modelMenus = [];
class SpyMenu {
  constructor(opts) {
    this.opts = opts; this.closed = false; modelMenus.push(this);
    const classes = new Set();
    this.el = {classList: {add: name => classes.add(name), contains: name => classes.has(name)}};
  }
  openAt(anchor) { this.anchor = anchor; return this; }
  refresh(sections) { this.opts.sections = sections; }
  close() { this.closed = true; }
  static closeOpen() { SpyMenu.closedOpen += 1; }
}
SpyMenu.closedOpen = 0;

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('segments.js'), uiBase('disclosure.js'), uiBase('aside.js'), uiBase('shell.js'), uiBase('pile.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js'), smort('settings_presets.js'), smort('settings_help.js'), smort('settings.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {generalPresetPatch, panelStops, openIds: () => openGroupIds, toggleSettingsPanel, openSettingsPanel, closeSettingsPanel, st, readPaths, parallelCaps, BINDINGS, mc, mallCam, spendCaps,
  openModelPicker, resolvePickerRole,
  mouseAffordances,
  buttonRef: () => document.querySelector('.settings-button'),
  addButtonRef: () => document.querySelectorAll('.boards-create-row .toggle').find(t => t.textContent === 'add'),
  browseButtonRef: () => document.querySelectorAll('.boards-create-row .toggle').find(t => t.textContent === 'browse')};`)(SpyMenu, SpyDrawer);

assert.equal(mod.st.listEl, null, 'nothing is built before the panel first opens');

async function flush() {
  await new Promise(r => setTimeout(r, 0));
  await new Promise(r => setTimeout(r, 0));
}

function press(code, target) {
  document._dispatch('keydown', {code, key: code, target: target || document.body, preventDefault() {}});
}

const list = () => mod.st.listEl;
const seg = key => list().querySelector(`[data-setting="${key}"]`);
const segButtons = key => seg(key).querySelectorAll('.segment');
const segLabels = key => segButtons(key).map(btn => btn.textContent);
const segLit = key => segButtons(key).filter(btn => btn.classList.contains('on')).map(btn => btn.textContent);
async function click(btn) {
  await Promise.all(btn._listeners.click.map(fn => fn({target: btn})));
  await flush();
}
const pickSeg = (key, text) => click(segButtons(key).find(btn => btn.textContent === text));
const presetButtons = () => list().querySelector('.settings-preset-segments').querySelectorAll('.segment');
const presetLit = () => presetButtons().filter(btn => btn.classList.contains('on')).map(btn => btn.textContent);
const pickPreset = text => click(presetButtons().find(btn => btn.textContent === text));
const disclosures = () => list().querySelectorAll('.disclosure');
const heads = () => list().querySelectorAll('.disclosure-head');
const bodiesHidden = () => list().querySelectorAll('.disclosure-body').map(body => Boolean(body.hidden));
const patches = () => calls.filter(c => c.path === '/api/settings' && c.opts?.method === 'PATCH');
const lastSettingsPatch = () => JSON.parse(patches().at(-1).opts.body);
const allText = el => [el.textContent, ...(el.children || []).map(allText)].join(' ');
const gateNotes = () => list().querySelectorAll('.settings-gate-note').map(note => note.textContent);

// o is in the contract, grouped with the other panels, and the overlay is built from this table
assert.ok(mod.BINDINGS.some(b => b.code === 'KeyO' && b.group === 'panels'), 'KeyO must be a panels binding');

// ---- the button is built at startup, beside the board bar, not inside it (renderBoardBar wipes it)
const button = mod.buttonRef();
assert.ok(button, 'the settings button exists once settings.js loads');
assert.ok(!boardBar.children.includes(button), 'the button must not live inside #board-bar');

// ---- the button opens the panel, which reads settings and boards once each ----------------------
const before = calls.length;
button.onclick();
assert.ok(mod.st.backdrop.parentNode, 'clicking the button should open the panel');
await flush();
const opened = calls.slice(before);
assert.equal(opened.filter(c => c.path === '/api/settings').length, 1, 'one GET /api/settings per open');
assert.equal(opened.filter(c => c.path === '/api/boards').length, 1, 'one GET /api/boards per open');
assert.ok(opened.every(c => !c.opts?.method || c.opts.method === 'GET'), 'opening only reads');

// settings.js once wrote its mall cam field into chat.js's `mc`, replacing mission control's own input
assert.notEqual(mod.mc.input, mod.mallCam.input, "the mall cam field must not become mission control's input");
assert.ok(mod.mallCam.input, 'the mall cam field is kept in its own state');

// ---- the preset row, then six accordion groups in order, all closed ----------------------------
assert.equal(list().querySelector('.settings-preset .settings-row-name').textContent, 'mode preset');
assert.deepEqual(presetButtons().map(btn => btn.textContent), ['careful', 'balanced', 'fast', 'custom']);
assert.deepEqual(disclosures().map(d => d.querySelector('.disclosure-name').textContent),
  ['safety gates', 'capacity', 'cost', 'models and limits', 'paths and data', 'advanced']);
assert.ok(disclosures().every(d => !d.classList.contains('open')), 'every group starts closed');
assert.deepEqual(bodiesHidden(), [true, true, true, true, true, true], 'closed bodies are hidden');
assert.ok(disclosures().every(d => !d.querySelector('.count-badge')), 'no count badges on the headers');
assert.deepEqual(disclosures().map(d => d.querySelector('.disclosure-summary').textContent), [
  'soft leases off / free merge off / open run off / off-limits on',
  '2 cards at once / mall cam 10 s / mouse off',
  'worker 5.00 / reviewer 1.50 / orchestrator 1.00 / fold 2.00 / card total none',
  'on limit: wait / reviewer reads diff / 1 of 4 roles set',
  'new repos in your home folder / 0 readable paths',
  'findings per card / briefing on / gate timeout 600 s',
]);
// closed bodies are hidden, so the keyboard walker only meets the preset row and the headers
const firstStops = mod.panelStops(mod.st.panel);
assert.ok(firstStops.every(stop => stop.tag === 'button'), 'no field is a stop while every group is closed');
assert.ok(heads().every(head => firstStops.includes(head)), 'every header is a stop');
assert.ok(!firstStops.some(stop => (stop.className || '').includes('role-model')));
assert.equal(list().querySelectorAll('.board-row').length, 0, 'o holds only what every board shares - no per-board rows');

// ---- groups are independent, and the open set is kept for the session -------------------------
await click(heads()[0]);
assert.deepEqual(bodiesHidden(), [false, true, true, true, true, true]);
await click(heads()[3]);
assert.deepEqual(mod.openIds(), ['safety', 'models'], 'opening one never closes another');
assert.equal(disclosures()[0].querySelector('.disclosure-summary').hidden, false, 'the summary stays while open: the header keeps its height');
await click(heads()[3]);
assert.deepEqual(mod.openIds(), ['safety']);
mod.closeSettingsPanel();
const beforeReopen = calls.length;
mod.openSettingsPanel();
await flush();
assert.deepEqual(bodiesHidden(), [false, true, true, true, true, true], 'a reopened panel keeps its open groups');
assert.equal(calls.slice(beforeReopen).filter(c => c.path === '/api/settings').length, 1);
assert.equal(calls.slice(beforeReopen).filter(c => c.path === '/api/boards').length, 1);
for (const head of heads().slice(1)) await click(head);
assert.deepEqual(bodiesHidden(), [false, false, false, false, false, false]);

// ---- every boolean is a two-way on|off toggle ---------------------------------------------------
for (const key of ['allow_soft_leases', 'allow_free_merge', 'allow_open_mode', 'off_limit_branches',
  'enable_mouse', 'resume_briefing']) {
  assert.deepEqual(segLabels(key), ['on', 'off'], `${key} reads on|off`);
  assert.ok(segButtons(key).every(btn => btn.tag === 'button'), 'real buttons');
}
assert.deepEqual(segLit('allow_free_merge'), ['off']);
assert.deepEqual(segLit('off_limit_branches'), ['on'], 'unset keeps every board list, which reads on');
assert.deepEqual(segLit('resume_briefing'), ['on'], 'unset resumes with a briefing');

// ---- the preset row lights what matches, and custom when nothing does ---------------------------
assert.deepEqual(presetLit(), ['balanced']);
assert.equal(list().querySelector('.settings-preset-strong').textContent, '0 values differ.');
assert.ok(list().querySelector('.settings-preset-lead').textContent.startsWith('balanced sets '));
const customButton = presetButtons().find(btn => btn.textContent === 'custom');
assert.equal(customButton['aria-disabled'], 'true', 'custom is lit by the app, never clicked');
assert.deepEqual(gateNotes(), ['used by 0 of 2 boards', 'used by 0 of 2 boards', 'used by 0 of 2 boards'],
  'a stored mode behind an off gate does not count as used');

await pickSeg('allow_free_merge', 'on');
assert.deepEqual(lastSettingsPatch(), {allow_free_merge: 'on'});
assert.deepEqual(segLit('allow_free_merge'), ['on']);
assert.deepEqual(presetLit(), ['custom'], 'a value no preset has relights as custom');
assert.ok(list().querySelector('.settings-preset-lead').textContent.startsWith('no preset matches. closest is balanced'));
assert.ok(list().querySelector('.settings-preset-strong').textContent.includes('free merge is on'));
assert.ok(disclosures()[0].querySelector('.disclosure-summary').textContent.includes('free merge on'),
  'the header summary follows a save');
assert.deepEqual(gateNotes(), ['used by 0 of 2 boards', 'used by 1 of 2 boards', 'used by 0 of 2 boards'],
  'the gate note follows the gate');

// a refused save leaves the lit segment where it was
stubControl.refuseNextSettingsPatch = true;
await pickSeg('allow_free_merge', 'off');
assert.deepEqual(segLit('allow_free_merge'), ['on'], 'a refused save does not move the lit segment');
assert.equal(list().querySelector('.settings-save-status').textContent, 'store error: disk full');
await pickSeg('allow_free_merge', 'off');
assert.deepEqual(lastSettingsPatch(), {allow_free_merge: null});
assert.deepEqual(presetLit(), ['balanced']);
assert.equal(list().querySelector('.settings-save-status').textContent, '', 'a landed save clears the message');

// a preset is one PATCH and nothing else
{
  const mark = calls.length;
  await pickPreset('fast');
  const sent = calls.slice(mark);
  assert.equal(sent.length, 1, 'a preset sends exactly one request');
  assert.equal(sent[0].path, '/api/settings');
  assert.equal(sent[0].opts.method, 'PATCH');
  assert.deepEqual(JSON.parse(sent[0].opts.body), mod.generalPresetPatch('fast'));
  assert.deepEqual(presetLit(), ['fast']);
  assert.ok(list().querySelector('.settings-preset-lead').textContent.startsWith('fast sets '));
  assert.deepEqual(segLit('allow_free_merge'), ['on'], 'the controls re-read the new values');
  assert.deepEqual(segLit('allow_soft_leases'), ['on']);
  assert.equal(mod.parallelCaps.globalInput.value, '4');
  assert.equal(mod.spendCaps.inputs.get('worker_budget_usd').value, '', 'a default value is stored unset');
  stubControl.refuseNextSettingsPatch = true;
  await pickPreset('careful');
  assert.deepEqual(presetLit(), ['fast'], 'a refused preset does not move the lit segment');
  await pickPreset('careful');
  assert.equal(mod.parallelCaps.globalInput.value, '1');
  assert.equal(mod.spendCaps.inputs.get('card_total_budget_usd').value, '10');
  assert.deepEqual(segLit('allow_free_merge'), ['off']);
  await pickPreset('balanced');
  assert.deepEqual(presetLit(), ['balanced']);
  assert.equal(mod.parallelCaps.globalInput.value, '');
  assert.equal(mod.spendCaps.inputs.get('card_total_budget_usd').value, '');
}

// ---- off-limit branches: off is stored 'off', on is null, and off asks for a second click -------
{
  const line = list().querySelector('.settings-confirm-line');
  assert.equal(line.hidden, true);
  assert.equal(line.textContent, 'this lifts the lock on every board - click off again to confirm');
  const sent = patches().length;
  await pickSeg('off_limit_branches', 'off');
  assert.equal(patches().length, sent, 'the first click only asks');
  assert.equal(line.hidden, false, 'the confirm line shows');
  assert.deepEqual(segLit('off_limit_branches'), ['on']);
  await pickSeg('allow_soft_leases', 'on'); // any other action cancels
  assert.equal(line.hidden, true, 'another click cancels the confirm');
  await pickSeg('allow_soft_leases', 'off');
  await pickSeg('off_limit_branches', 'off');
  assert.equal(patches().length, sent + 2, 'after a cancel the next click asks again');
  await pickSeg('off_limit_branches', 'off');
  assert.deepEqual(lastSettingsPatch(), {off_limit_branches: 'off'}, 'the second click saves off as the string off');
  assert.deepEqual(segLit('off_limit_branches'), ['off']);
  assert.equal(line.hidden, true);
  await pickSeg('off_limit_branches', 'on');
  assert.deepEqual(lastSettingsPatch(), {off_limit_branches: null}, 'on needs no confirm and stores null');
  assert.deepEqual(segLit('off_limit_branches'), ['on']);
}

// ---- the help box: the descriptions moved out of the rows into a box that follows the focused row ---
const helpRow = id => {
  const walk = el => (el.dataset && el.dataset.help === id ? el : (el.children || []).map(walk).find(Boolean));
  return walk(list());
};
assert.ok(!allText(list()).includes('weaker than sealed'), 'no inline description under the row');
assert.ok(helpRow('allow_open_mode'), 'the open run mode row carries its help id');
['preset', 'allow_soft_leases', 'allow_free_merge', 'off_limit_branches', 'max_parallel', 'enable_mouse',
  'usage_limit_route', 'reviewer_input', 'worker_budget_usd', 'card_total_budget_usd', 'models', 'repos_home',
  'mission_control_read_paths', 'backup', 'findings_route', 'resume_briefing', 'gate_timeout_seconds']
  .forEach(id => assert.ok(helpRow(id), `${id} row carries its help id`));
const asideEls = () => document.body.children.filter(c => String(c.className).includes('aside'));
assert.equal(asideEls().length, 1, 'one help box while the panel is open');
const focusRow = id => mod.st.panel._listeners.focusin.forEach(fn => fn({target: helpRow(id)}));
focusRow('allow_open_mode');
assert.ok(allText(asideEls()[0]).includes('open run mode'), 'the box names the focused setting');
assert.ok(allText(asideEls()[0]).includes('weaker than a container'), 'and says what its choices mean');
focusRow('max_parallel');
assert.ok(allText(asideEls()[0]).includes('cards at once'), 'it follows the focus to the next row');
mod.st.panel._listeners.focusout.forEach(fn => fn({relatedTarget: null}));
await pickSeg('allow_open_mode', 'on');
assert.deepEqual(lastSettingsPatch(), {allow_open_mode: 'on'});
await pickSeg('allow_open_mode', 'off');
assert.deepEqual(lastSettingsPatch(), {allow_open_mode: null});
await pickSeg('allow_soft_leases', 'on');
assert.deepEqual(lastSettingsPatch(), {allow_soft_leases: 'on'});
assert.equal(gateNotes()[0], 'used by 1 of 2 boards', 'b1 holds a soft lease once the gate opens');
await pickSeg('allow_soft_leases', 'off');

// ---- the cost group holds the five spend cap rows inline, no popup ------------------------------
assert.equal(list().querySelectorAll('.settings-cost-trigger').length, 0);

const roleBlocks = mod.st.listEl.querySelectorAll('.settings-role-block');
assert.equal(roleBlocks.length, 4, 'each model role has its own settings block');
assert.deepEqual(roleBlocks.map(block => block.querySelector('.settings-role-name').textContent),
  ['worker', 'reviewer', 'orchestrator', 'fold']);
assert.ok(roleBlocks.every(block => block.dataset.help === 'models'), 'the floating help still finds each block');
assert.ok(roleBlocks.every(block => block.querySelector('.settings-role-status')),
  'each role keeps save status beside its heading');
assert.equal(mod.st.listEl.querySelectorAll('.role-model').length, 0, 'no primary picker button is left');
assert.equal(mod.st.listEl.querySelectorAll('.role-fallback').length, 0, 'no fallback picker button is left');
assert.equal(mod.st.listEl.querySelectorAll('.role-effort').length, 0, 'no separate effort row');
assert.equal(opened.filter(c => c.path === '/api/catalog').length, 1, 'one GET /api/catalog per open');

const block = role => roleBlocks.find(node => node.querySelector('.settings-role-name').textContent === role);
const chainRows = role => block(role).querySelectorAll('.settings-chain-row');
const rowTexts = role => chainRows(role).map(row =>
  row.querySelectorAll('.toggle').filter(btn => !btn.dataset.step).map(btn => btn.textContent));
const ctl = (role, key) => block(role).querySelector(`[data-key="${key}"]`);
const lastMenu = () => modelMenus.at(-1);
const menuItems = () => lastMenu().opts.sections[0].items;
// open a control's list and pick one option by id; the list is a single-select Menu under the button
async function choose(role, key, id) {
  const btn = ctl(role, key);
  await btn.onclick();
  assert.equal(lastMenu().anchor, btn, 'the list opens under the control that launched it');
  assert.equal(lastMenu().opts.sections[0].kind, 'list');
  const section = lastMenu().opts.sections[0];
  await section.onPick(section.items.find(item => item.id === id));
  await flush();
}
const tap = async (role, key) => { await ctl(role, key).onclick(); await flush(); };
const patchCount = () => patches().length;

// the chain is read from the settings: row 1 the primary (the role's catalog default while unset)
assert.deepEqual(rowTexts('worker'), [['openai', 'gpt-5.6-sol', 'medium'], ['openai', 'gpt-5.6-sol', 'medium']],
  'worker: primary default from the lab, then its one fallback with the stale max effort restarted');
assert.deepEqual(rowTexts('reviewer'), [['anthropic', 'Sonnet', 'medium']]);
assert.deepEqual(rowTexts('fold'), [['anthropic', 'fable 5', 'low'],
  ['anthropic', 'Opus', 'medium'], ['openai', 'gpt-6-astra', 'high']], 'fallbacks keep their saved order');
assert.equal(ctl('worker', '0:up'), null, 'the primary row has no step buttons');
assert.deepEqual(chainRows('fold')[1].querySelectorAll('.toggle').slice(3).map(btn => btn.textContent),
  ['up', 'down', 'del']);
assert.ok(mod.panelStops(mod.st.panel).includes(ctl('fold', '1:del')), 'the arrow walker reaches the chain buttons');
assert.ok(mod.panelStops(mod.st.panel).includes(ctl('fold', 'add')));

// a lab list shows an unavailable lab dashed with its reason, and cannot pick it
await ctl('reviewer', '0:lab').onclick();
const gemini = menuItems().find(item => item.id === 'gemini');
assert.equal(gemini.disabled, true);
assert.equal(gemini.state.unavailable, true);
assert.equal(gemini.stats, 'no credential');
assert.deepEqual(menuItems().filter(item => item.id !== 'gemini').map(item => item.stats), ['', '']);
lastMenu().close();

// effort: one patch with the primary's three keys; never a default choice
await ctl('reviewer', '0:effort').onclick();
assert.deepEqual(menuItems().map(item => item.id), ['low', 'medium', 'high'], 'no default row');
lastMenu().close();
let count = patchCount();
await choose('reviewer', '0:effort', 'high');
assert.equal(patchCount(), count + 1, 'one patch per change');
assert.deepEqual(lastSettingsPatch(), {reviewer_lab: 'anthropic', reviewer_model: 'sonnet', reviewer_effort: 'high'});
assert.deepEqual(rowTexts('reviewer'), [['anthropic', 'Sonnet', 'high']], 'only that role redraws');

// lab: the model resets to the lab's preferred model and the effort to its start effort
count = patchCount();
await choose('reviewer', '0:lab', 'openai');
assert.equal(patchCount(), count + 1, 'one patch per change');
assert.deepEqual(lastSettingsPatch(), {reviewer_lab: 'openai', reviewer_model: 'gpt-5.6-sol', reviewer_effort: 'medium'});
// model: the effort is kept when the new model offers it, else the start effort
await choose('reviewer', '0:effort', 'xhigh');
await choose('reviewer', '0:model', 'gpt-6-astra');
assert.deepEqual(lastSettingsPatch(), {reviewer_lab: 'openai', reviewer_model: 'gpt-6-astra', reviewer_effort: 'xhigh'},
  'xhigh survives a model that offers it');
await choose('reviewer', '0:effort', 'ultra');
await choose('reviewer', '0:model', 'gpt-5.6-sol');
assert.deepEqual(lastSettingsPatch(), {reviewer_lab: 'openai', reviewer_model: 'gpt-5.6-sol', reviewer_effort: 'medium'},
  'ultra is not offered by sol, so the start effort is used');
// a model with no effort levels has no effort control and saves a null effort
await choose('reviewer', '0:lab', 'anthropic');
await choose('reviewer', '0:model', 'haiku');
assert.deepEqual(lastSettingsPatch(), {reviewer_lab: 'anthropic', reviewer_model: 'haiku', reviewer_effort: null});
assert.equal(ctl('reviewer', '0:effort'), null, 'haiku shows no effort control');
assert.deepEqual(rowTexts('reviewer'), [['anthropic', 'Haiku']]);
assert.ok(!patches().some(c => JSON.parse(c.opts.body).reviewer_effort === 'default'), 'default is never an effort');
await choose('reviewer', '0:model', 'sonnet');
assert.deepEqual(lastSettingsPatch(), {reviewer_lab: 'anthropic', reviewer_model: 'sonnet', reviewer_effort: 'medium'});

// add fallback: the first available lab, its preferred model, the start effort
assert.equal(ctl('reviewer', 'add').textContent, 'add fallback');
assert.ok(!ctl('reviewer', 'add').className.includes('unavailable'), 'a regular solid button');
await tap('reviewer', 'add');
assert.deepEqual(lastSettingsPatch(), {reviewer_cross_lab_fallback: [{ref: 'anthropic/sonnet', effort: 'medium'}]});
assert.deepEqual(rowTexts('reviewer'), [['anthropic', 'Sonnet', 'medium'], ['anthropic', 'Sonnet', 'medium']]);
const stepClass = (role, key) => ctl(role, key).className;
assert.ok(stepClass('reviewer', '1:up').includes('unavailable'), 'up is dashed on the first fallback');
assert.ok(stepClass('reviewer', '1:down').includes('unavailable'), 'down is dashed on the last');
assert.ok(!stepClass('reviewer', '1:del').includes('unavailable'));
count = patchCount();
await tap('reviewer', '1:up');
await tap('reviewer', '1:down');
assert.equal(patchCount(), count, 'an unavailable button does nothing');
// a fallback's own lab change writes the whole list
await choose('reviewer', '1:lab', 'openai');
assert.deepEqual(lastSettingsPatch(), {reviewer_cross_lab_fallback: [{ref: 'openai/gpt-5.6-sol', effort: 'medium'}]});
await tap('reviewer', 'add');
assert.deepEqual(lastSettingsPatch(), {reviewer_cross_lab_fallback: [
  {ref: 'openai/gpt-5.6-sol', effort: 'medium'}, {ref: 'anthropic/sonnet', effort: 'medium'}]});
assert.ok(!stepClass('reviewer', '1:down').includes('unavailable') && !stepClass('reviewer', '2:up').includes('unavailable'));
await tap('reviewer', '2:up');
assert.deepEqual(lastSettingsPatch(), {reviewer_cross_lab_fallback: [
  {ref: 'anthropic/sonnet', effort: 'medium'}, {ref: 'openai/gpt-5.6-sol', effort: 'medium'}]}, 'up sends the whole new list');
assert.equal(document.activeElement, ctl('reviewer', '1:up'), 'focus follows the moved row');
await tap('reviewer', '1:down');
assert.deepEqual(lastSettingsPatch(), {reviewer_cross_lab_fallback: [
  {ref: 'openai/gpt-5.6-sol', effort: 'medium'}, {ref: 'anthropic/sonnet', effort: 'medium'}]});
await choose('reviewer', '1:model', 'gpt-6-astra');
assert.deepEqual(lastSettingsPatch().reviewer_cross_lab_fallback[0], {ref: 'openai/gpt-6-astra', effort: 'medium'});
await tap('reviewer', '1:del');
assert.deepEqual(lastSettingsPatch(), {reviewer_cross_lab_fallback: [{ref: 'anthropic/sonnet', effort: 'medium'}]});
await tap('reviewer', '1:del');
assert.deepEqual(lastSettingsPatch(), {reviewer_cross_lab_fallback: []});
assert.deepEqual(rowTexts('reviewer'), [['anthropic', 'Sonnet', 'medium']]);

// the note follows the usage limits toggle
const chainNote = () => list().querySelector('.settings-chain-note').textContent;
assert.equal(chainNote(), 'fallbacks are only used when usage limits is switch, now wait');
await pickSeg('usage_limit_route', 'switch');
assert.equal(chainNote(), 'fallbacks are only used when usage limits is switch, now switch');
await pickSeg('usage_limit_route', 'ask me');
assert.equal(chainNote(), 'fallbacks are only used when usage limits is switch, now ask me');
await pickSeg('usage_limit_route', 'wait');
assert.equal(chainNote(), 'fallbacks are only used when usage limits is switch, now wait');

await mod.openModelPicker(() => {}, undefined, null, {default_settings: settingsState});
const defaultCardMenu = modelMenus.at(-1);
assert.ok(defaultCardMenu.el.classList.contains('menu-centered'), 'a picker with no anchor is centred');
const defaultCardColumns = defaultCardMenu.opts.sections.find(section => section.kind === 'columns').columns;
assert.deepEqual(defaultCardColumns[2].items.map(item => item.id), ['low', 'medium', 'high', 'xhigh'],
  'card default resolves the worker model from its configured lab');
assert.equal(defaultCardColumns[2].items.find(item => item.on).id, 'medium', 'a model never chosen for starts at medium');
assert.deepEqual(mod.resolvePickerRole({openai: {models: [{id: 'worker', tier: 'standard'},
  {id: 'planner', tier: 'deep'}]}}, {orchestrator_lab: 'openai', orchestrator_model: 'planner'}, 'fold'),
  {lab: 'openai', model: 'planner'}, 'unset fold inherits the orchestrator lab and model');
assert.deepEqual(mod.resolvePickerRole({openai: {models: [{id: 'first', tier: 'standard'},
  {id: 'preferred', tier: 'standard', prefer_for: ['worker']}]}}, {worker_lab: 'openai'}),
  {lab: 'openai', model: 'preferred'}, 'role preference wins over the first matching tier');

let savedFold;
await mod.openModelPicker((...selection) => { savedFold = selection; }, undefined, null,
  {default_role: 'fold', default_settings: {orchestrator_lab: 'openai', orchestrator_model: 'gpt-5.6-sol'}});
const inheritedFoldMenu = modelMenus.at(-1);
let inheritedFoldColumns = inheritedFoldMenu.opts.sections.find(section => section.kind === 'columns').columns;
assert.deepEqual(inheritedFoldColumns[2].items.map(item => item.id), ['low', 'medium', 'high', 'xhigh']);
inheritedFoldColumns[2].onPick({id: 'xhigh'});
inheritedFoldMenu.opts.sections.find(section => section.kind === 'buttons').buttons
  .find(button => button.id === 'save-model').onClick(inheritedFoldMenu);
assert.deepEqual(savedFold, [null, null, 'xhigh'], 'effort-only fold save keeps model inheritance');
inheritedFoldMenu.opts.sections.find(section => section.kind === 'buttons').buttons
  .find(button => button.id === 'model-default').onClick(inheritedFoldMenu);
assert.deepEqual(savedFold, [null, null, null], 'board default restores inheritance');

// ---- the mouse is opt-in: off lit when unset, and on PATCHes "on" -------------------------------
{
  assert.deepEqual(segLit('enable_mouse'), ['off'], 'the mouse is off by default');
  await pickSeg('enable_mouse', 'on');
  assert.deepEqual(lastSettingsPatch(), {enable_mouse: 'on'});
  assert.deepEqual(segLit('enable_mouse'), ['on'], 'picking on lights it and only it');
  assert.equal(mod.mouseAffordances(), true, 'the pointer affordances switch on with no reload');
  await pickSeg('enable_mouse', 'off');
  assert.deepEqual(lastSettingsPatch(), {enable_mouse: null}, 'off clears it');
  assert.deepEqual(segLit('enable_mouse'), ['off']);
  assert.equal(mod.mouseAffordances(), false);
  // a refused save leaves the lit segment, and the mouse, where they were
  stubControl.refuseNextSettingsPatch = true;
  await pickSeg('enable_mouse', 'on');
  assert.deepEqual(segLit('enable_mouse'), ['off'], 'a failed save does not move the lit segment');
  assert.equal(mod.mouseAffordances(), false);
}

// ---- spend caps: blank is the default, a value is PATCHed under its own key ----------------------
{
  const inputs = list().querySelectorAll('.settings-spend-input');
  assert.equal(inputs.length, 5, 'worker, review, mission control, fold and the card total each have a cap');
  assert.equal(inputs[2].placeholder, '1.00', 'the mission control default shows as the placeholder');
  assert.equal(inputs[4].placeholder, 'no limit', 'the card total cap has no default - unset means unlimited');
  inputs[2].value = '2.5';
  inputs[2]._listeners.blur.forEach(fn => fn());
  await flush();
  assert.deepEqual(lastSettingsPatch(), {orchestrator_budget_usd: 2.5});
  assert.ok(disclosures()[2].querySelector('.disclosure-summary').textContent.includes('orchestrator 2.50'));
  const sent = patches().length;
  inputs[0].value = 'lots';
  inputs[0]._listeners.blur.forEach(fn => fn());
  await flush();
  assert.equal(patches().length, sent, 'a bad amount never reaches the server');
  assert.ok(mod.spendCaps.statusEl.textContent.includes('positive amount'), 'a bad amount is refused in place');
  inputs[0].value = '';
  inputs[2].value = '';
  inputs[2]._listeners.blur.forEach(fn => fn());
  await flush();
  assert.deepEqual(lastSettingsPatch(), {orchestrator_budget_usd: null});
}
assert.ok(mod.readPaths.listEl.querySelector('.hazard-placeholder'), 'no folders yet shows a placeholder, not nothing');

// ---- the usage limit is one three-way choice, and the lit segment follows only a landed save ----
assert.deepEqual(segLabels('usage_limit_route'), ['wait', 'ask me', 'switch']);
assert.deepEqual(segLit('usage_limit_route'), ['wait'], 'unset waits - it spends nothing');
await pickSeg('usage_limit_route', 'switch');
assert.deepEqual(lastSettingsPatch(), {usage_limit_route: 'switch'});
assert.deepEqual(segLit('usage_limit_route'), ['switch']);
assert.ok(disclosures()[3].querySelector('.disclosure-summary').textContent.includes('on limit: switch'));
await pickSeg('usage_limit_route', 'ask me');
assert.deepEqual(lastSettingsPatch(), {usage_limit_route: 'attention'});
stubControl.refuseNextSettingsPatch = true;
await pickSeg('usage_limit_route', 'wait');
assert.deepEqual(segLit('usage_limit_route'), ['ask me'], 'a refused save leaves the lit segment where it was');
await pickSeg('usage_limit_route', 'wait');
assert.deepEqual(lastSettingsPatch(), {usage_limit_route: null});

// ---- reviewer reads: the diff or the code --------------------------------------------------------
assert.deepEqual(segLabels('reviewer_input'), ['diff', 'code']);
assert.deepEqual(segLit('reviewer_input'), ['diff']);
await pickSeg('reviewer_input', 'code');
assert.deepEqual(lastSettingsPatch(), {reviewer_input: 'code'});
assert.deepEqual(segLit('reviewer_input'), ['code']);
await pickSeg('reviewer_input', 'diff');
assert.deepEqual(lastSettingsPatch(), {reviewer_input: null});

// ---- advanced: findings route, resume briefing, gate timeout -----------------------------------
assert.deepEqual(segLabels('findings_route'), ['per card', 'fix', 'attention']);
assert.deepEqual(segLit('findings_route'), ['per card']);
await pickSeg('findings_route', 'fix');
assert.deepEqual(lastSettingsPatch(), {findings_route: 'fix'});
await pickSeg('findings_route', 'attention');
assert.deepEqual(lastSettingsPatch(), {findings_route: 'attention'});
await pickSeg('findings_route', 'per card');
assert.deepEqual(lastSettingsPatch(), {findings_route: null});
await pickSeg('resume_briefing', 'off');
assert.deepEqual(lastSettingsPatch(), {resume_briefing: 'off'}, 'off is the stored value for the briefing');
assert.deepEqual(segLit('resume_briefing'), ['off']);
await pickSeg('resume_briefing', 'on');
assert.deepEqual(lastSettingsPatch(), {resume_briefing: null}, 'null is on');
{
  const field = list().querySelector('.settings-gate-timeout-input');
  assert.equal(field.placeholder, '600');
  field.value = '120';
  field._listeners.blur.forEach(fn => fn());
  await flush();
  assert.deepEqual(lastSettingsPatch(), {gate_timeout_seconds: 120});
  assert.ok(disclosures()[5].querySelector('.disclosure-summary').textContent.includes('gate timeout 120 s'));
  const sent = patches().length;
  field.value = 'soon';
  field._listeners.blur.forEach(fn => fn());
  await flush();
  assert.equal(patches().length, sent, 'a bad timeout never reaches the server');
  field.value = '';
  field._listeners.blur.forEach(fn => fn());
  await flush();
  assert.deepEqual(lastSettingsPatch(), {gate_timeout_seconds: null}, 'empty sends null - the default');
}

// ---- mall cam: empty is the default, a whole number is saved ------------------------------------
{
  assert.equal(mod.mallCam.input.placeholder, '10');
  mod.mallCam.input.value = '15';
  mod.mallCam.input._listeners.blur.forEach(fn => fn());
  await flush();
  assert.deepEqual(lastSettingsPatch(), {mall_cam_interval_seconds: 15});
  mod.mallCam.input.value = '';
  mod.mallCam.input._listeners.blur.forEach(fn => fn());
  await flush();
  assert.deepEqual(lastSettingsPatch(), {mall_cam_interval_seconds: null});
}

// ---- where new repos go: saved on enter or blur, expanded by the server, refused when missing -----
{
  const field = mod.st.listEl.querySelector('.settings-repos-home-input');
  assert.equal(field.value, '', 'unset reads as blank - the home folder');
  field.value = '~/dev';
  field._listeners.blur.forEach(fn => fn());
  await flush();
  assert.deepEqual(lastSettingsPatch(), {repos_home: '~/dev'});
  assert.equal(field.value, '/home/op/dev', 'the field shows the absolute path the server stored');
  const saves = calls.length;
  field._listeners.blur.forEach(fn => fn());
  await flush();
  assert.equal(calls.length, saves, 'tabbing past an unchanged field saves nothing');
  field.value = '~/nope';
  field._listeners.blur.forEach(fn => fn());
  await flush();
  assert.ok(field.parentNode.querySelectorAll('.boards-error')[0].textContent.includes('does not exist'));
  assert.equal(settingsState.repos_home, '/home/op/dev', 'a refused folder changes nothing');
}

// ---- the global parallel cap stays here; each board's own cap is in shift+o ---------------------
assert.equal(mod.parallelCaps.globalInput.value, '', 'an unset global cap renders as an empty field, not 0');

// ---- browse opens the shared folder picker, and its one action adds the folder it is on --------
{
  const opened = [];
  globalThis.openFolderPicker = (anchor, opts) => opened.push(opts);
  mod.browseButtonRef().onclick();
  assert.equal(opened.length, 1, 'browse opens the folder picker');
  assert.equal(opened[0].action.when({here: '/home/op/anything', repo: false}), true, 'any folder can be added, not only repos');
  delete globalThis.openFolderPicker;
}

// ---- adding a path PATCHes the whole list, and stores the server's expanded absolute path -------
mod.readPaths.input.value = '~/Documents/screenshots';
mod.addButtonRef().onclick();
await flush();
const patch = calls.filter(c => c.path === '/api/settings' && c.opts?.method === 'PATCH').at(-1);
assert.deepEqual(JSON.parse(patch.opts.body), {mission_control_read_paths: ['~/Documents/screenshots']}, 'the client sends the raw text typed - the ~ expands server-side');
assert.deepEqual(settingsState.mission_control_read_paths, ['/home/op/Documents/screenshots'], 'the stored value is the absolute, expanded path');
assert.equal(mod.readPaths.input.value, '', 'the input clears after a successful add');
assert.equal(mod.readPaths.listEl.querySelectorAll('.board-row').length, 1, 'one row for the added path');
assert.equal(mod.readPaths.listEl.querySelector('.board-name').textContent, '/home/op/Documents/screenshots', 'the row shows the absolute path, not what was typed');

// ---- a path that does not exist is refused with a message naming it, and nothing is added -------
mod.readPaths.input.value = '/nope/not-there';
mod.addButtonRef().onclick();
await flush();
assert.ok(mod.readPaths.statusEl.textContent.includes('/nope/not-there'), 'the refusal names the bad path');
assert.equal(mod.readPaths.listEl.querySelectorAll('.board-row').length, 1, 'the refused path was never added');
assert.deepEqual(settingsState.mission_control_read_paths, ['/home/op/Documents/screenshots'], 'a refused patch leaves the stored list untouched');

// ---- removing a row PATCHes the remaining list and drops it from the panel ----------------------
mod.readPaths.listEl.querySelector('.board-delete').onclick();
await flush();
assert.deepEqual(settingsState.mission_control_read_paths, [], 'removing the only path clears the setting');
assert.ok(mod.readPaths.listEl.querySelector('.hazard-placeholder'), 'the list falls back to the empty placeholder');

// ---- saving the global cap PATCHes /api/settings ------------------------------------------------
mod.parallelCaps.globalInput.value = '3';
mod.parallelCaps.globalInput._listeners.blur.forEach(fn => fn());
await flush();
assert.equal(settingsState.max_parallel, 3, 'the global cap is saved');

// ---- zero, negative and non-numeric values are refused without a PATCH landing ------------------
mod.parallelCaps.globalInput.value = '0';
mod.parallelCaps.globalInput._listeners.blur.forEach(fn => fn());
await flush();
assert.equal(settingsState.max_parallel, 3, 'a zero value never reaches the server');
assert.ok(mod.parallelCaps.globalStatus.textContent.includes('positive'), 'the field explains why it refused');

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

// ---- backup: export downloads, import adds beside the boards here and never replaces one --------
{
  // what the section creates on the fly - the download link and the file picker - is caught here
  const made = [];
  const createElement = document.createElement;
  document.createElement = tag => {
    const el = createElement(tag);
    el.click = () => { el.clicked = true; };
    made.push(el);
    return el;
  };
  mod.openSettingsPanel();
  await flush();
  const buttons = mod.st.listEl.querySelectorAll('.run-controls .toggle');
  assert.deepEqual(buttons.map(btn => btn.textContent), ['export', 'import as new board(s)'],
    'no replace: import only ever adds boards');
  assert.ok(buttons.every(btn => btn.tag === 'button' && btn.type === 'button'),
    'real buttons: tab reaches them and enter or space picks');
  const status = buttons[0].parentNode.querySelector('.boards-status');
  const [exportButton, importButton] = buttons;

  made.length = 0; // opening the panel built its own rows; only what the buttons make counts
  exportButton.onclick();
  const link = made.find(el => el.tag === 'a');
  assert.equal(link.href, '/api/export');
  assert.equal(link.download, '', 'download set, so the page stays and the server names the file');
  assert.ok(link.clicked && link.removed, 'the link is clicked once and not left in the page');

  made.length = 0;
  importButton.onclick();
  const picker = made.find(el => el.tag === 'input');
  assert.equal(picker.type, 'file');
  assert.ok(picker.clicked, 'import opens the file picker');
  assert.equal(picker.parentNode, null, 'the picker never joins the panel, so up/down never land on it');

  const boardsFetchesBefore = calls.filter(c => c.path === '/api/boards').length;
  picker.files = [new File(['{"boards": []}'], 'backup.json', {type: 'application/json'})];
  picker._listeners.change.forEach(fn => fn());
  await flush();
  const upload = calls.filter(c => c.path === '/api/import').at(-1);
  assert.equal(upload.opts.method, 'POST');
  assert.ok(upload.opts.body instanceof FormData, 'the file goes up as multipart, like an attachment');
  assert.equal(upload.opts.body.get('bundle').name, 'backup.json');
  assert.equal(status.textContent, 'added 1 board(s): restored');
  assert.ok(calls.filter(c => c.path === '/api/boards').length > boardsFetchesBefore,
    'the board bar reloads so the added board shows');

  // a refused file says why, in place
  stubControl.importRefusal = 'the bundle holds no boards';
  picker._listeners.change.forEach(fn => fn());
  await flush();
  assert.equal(status.textContent, 'the bundle holds no boards');
  assert.ok(status.classList.contains('boards-error'));
  document.createElement = createElement;
  mod.closeSettingsPanel();
}

// o on an open settings panel closes it, and a fallback picker opened from it goes too - it used
// to stay on screen with nothing under it
{
  mod.openSettingsPanel();
  await flush();
  const before = SpyMenu.closedOpen;
  mod.toggleSettingsPanel();
  assert.equal(SpyMenu.closedOpen, before + 1, 'closing settings should close its open submenu');
}

console.log('ok');
