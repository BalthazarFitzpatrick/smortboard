// settings panel (o): the top-right button and the o key open it, esc and an outside click close
// it, and it renders an extensible section list - "mission control can read" is the first real
// section, added, refused and removed through the whole-list-replace PATCH /api/settings.
// run: node tests/js/settings.mjs
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
  fold_cross_lab_fallback: ['anthropic/opus', 'openai/gpt-6-astra'],
};
const boardsState = [
  {id: 'b1', name: 'alpha', max_parallel: null, daily_budget_usd: null},
  {id: 'b2', name: 'beta', max_parallel: 1, daily_budget_usd: null},
];
const calls = [];
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
      {id: 'fable', label: 'Fable', tier: 'deep'},
      {id: 'opus', label: 'Opus', tier: 'deep'},
      {id: 'sonnet', label: 'Sonnet', tier: 'standard'},
      {id: 'haiku', label: 'Haiku', tier: 'light'},
    ]},
    openai: {available: true, models: [
      {id: 'gpt-6-astra', label: 'Astra', tier: 'deep'},
      {id: 'gpt-5.6-sol', label: 'Sol', tier: 'standard'},
      {id: 'gpt-5.6-terra', label: 'Terra', tier: 'standard'},
      {id: 'gpt-5.6-luna', label: 'Luna', tier: 'light'},
    ]},
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
  // anything else never answers, as on main: board.js's own startup fetches (loadBoards) would
  // otherwise reject unhandled and end the run before a single assertion
  if (path !== '/api/settings') return new Promise(() => {});
  if (!opts || !opts.method || opts.method === 'GET') return Promise.resolve(stubJson(200, {...settingsState}));
  if (opts.method === 'PATCH') {
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
  constructor(opts) { this.opts = opts; this.closed = false; modelMenus.push(this); }
  openAt(anchor) { this.anchor = anchor; return this; }
  refresh(sections) { this.opts.sections = sections; }
  close() { this.closed = true; }
}

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), uiBase('pile.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js'), smort('settings.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {toggleSettingsPanel, openSettingsPanel, closeSettingsPanel, st, readPaths, parallelCaps, dailyBudgets, BINDINGS, mc, mallCam, spendCaps,
  buttonRef: () => document.querySelector('.settings-button'),
  addButtonRef: () => document.querySelectorAll('.boards-create-row .toggle').find(t => t.textContent === 'add'),
  browseButtonRef: () => document.querySelectorAll('.boards-create-row .toggle').find(t => t.textContent === 'browse')};`)(SpyMenu, SpyDrawer);

// settings.js once wrote its mall cam field into chat.js's `mc`, replacing mission control's own input
assert.notEqual(mod.mc.input, mod.mallCam.input, "the mall cam field must not become mission control's input");
assert.ok(mod.mallCam.input, 'the mall cam field is kept in its own state');

async function flush() {
  await new Promise(r => setTimeout(r, 0));
  await new Promise(r => setTimeout(r, 0));
}

function press(code, target) {
  document._dispatch('keydown', {code, key: code, target: target || document.body, preventDefault() {}});
}

// o is in the contract, grouped with the other panels, and the overlay is built from this table
assert.ok(mod.BINDINGS.some(b => b.code === 'KeyO' && b.group === 'panels'), 'KeyO must be a panels binding');

// ---- the button is built at startup, beside the board bar, not inside it (renderBoardBar wipes it)
const button = mod.buttonRef();
assert.ok(button, 'the settings button exists once settings.js loads');
assert.ok(!boardBar.children.includes(button), 'the button must not live inside #board-bar');

// ---- the button opens the panel -------------------------------------------------------------
button.onclick();
assert.ok(mod.st.backdrop.parentNode, 'clicking the button should open the panel');

// ---- the panel groups related settings while keeping each setting as its own section ------------
await flush();
const settingsGroups = mod.st.listEl.querySelectorAll('.settings-group');
assert.deepEqual(settingsGroups.map(group => group.querySelector('.settings-group-title').textContent),
  ['general board settings', 'labs and models', 'cost control']);
assert.deepEqual(settingsGroups.map(group => group.querySelectorAll('.settings-section')
  .map(section => section.children[0].textContent)), [
  ['mouse', 'mission control can read', 'how many cards run at once',
    'mall cam: seconds per card while auto-cycling the workforce drawer'],
  ['credential profiles', 'usage limits', 'models by role'],
  ['budgets and spend caps'],
]);
assert.equal(mod.st.listEl.querySelectorAll('.settings-section').length, 8);
const costTriggers = mod.st.listEl.querySelectorAll('.settings-cost-trigger');
assert.deepEqual(costTriggers.map(trigger => trigger.textContent),
  ['daily budgets per board', 'spend caps per run'], 'cost controls have separate compact triggers');
const dailyTrigger = costTriggers[0];
dailyTrigger.onclick();
const dailyMenu = modelMenus.at(-1);
assert.equal(dailyMenu.opts.title, 'daily budgets per board');
assert.equal(dailyMenu.opts.persistent, true);
assert.equal(dailyMenu.anchor, dailyTrigger, 'daily budgets open below their own trigger');
assert.equal(dailyMenu.opts.sections.filter(section => section.kind === 'node').length, 1,
  'the daily budget menu contains only the board budget grid');
const spendTrigger = costTriggers[1];
spendTrigger.onclick();
const spendMenu = modelMenus.at(-1);
assert.equal(spendMenu.opts.title, 'spend caps per run');
assert.equal(spendMenu.opts.persistent, true);
assert.equal(spendMenu.anchor, spendTrigger, 'spend caps open below their own trigger');
assert.equal(spendMenu.opts.sections.filter(section => section.kind === 'node').length, 1,
  'the spend cap menu contains only the per-run cap grid');
await flush();

const rolePickers = mod.st.listEl.querySelectorAll('.role-model');
assert.equal(rolePickers.length, 4);
const roleBlocks = mod.st.listEl.querySelectorAll('.settings-role-block');
assert.equal(roleBlocks.length, 4, 'each model role has its own settings block');
assert.deepEqual(roleBlocks.map(block => block.querySelector('.settings-role-name').textContent),
  ['worker', 'reviewer', 'orchestrator', 'fold']);
assert.ok(roleBlocks.every(block => block.querySelectorAll('.settings-role-control').length === 2),
  'each role separates the primary model from the fallback order');
assert.ok(roleBlocks.every(block => block.querySelector('.settings-role-status')),
  'each role keeps save status beside its heading');
const reviewerPicker = rolePickers.find(row => row.dataset.role === 'reviewer');
await reviewerPicker.onclick();
const primaryMenu = modelMenus.at(-1);
assert.equal(primaryMenu.anchor, reviewerPicker,
  'the model menu opens at the role picker that launched it');
assert.equal(primaryMenu.opts.title, 'choose model');
assert.equal(primaryMenu.opts.persistent, true, 'one menu stays open while choosing a lab and model');
let primaryColumns = primaryMenu.opts.sections.find(section => section.kind === 'columns').columns;
assert.equal(primaryColumns[0].multi, false);
assert.equal(primaryColumns[1].multi, false, 'primary model selection is single-select');
primaryColumns[0].onPick({id: 'openai'});
primaryColumns = primaryMenu.opts.sections.find(section => section.kind === 'columns').columns;
primaryColumns[1].onPick({id: 'gpt-6-astra'});
assert.equal(primaryMenu.closed, false, 'selecting a primary model keeps the menu open');
const primarySave = primaryMenu.opts.sections.find(section => section.kind === 'buttons')
  .buttons.find(button => button.id === 'save-model');
assert.equal(primarySave.enabled, true);
await primarySave.onClick(primaryMenu);
await flush();
assert.deepEqual(JSON.parse(calls.filter(c => c.opts?.method === 'PATCH').at(-1).opts.body),
  {reviewer_lab: 'openai', reviewer_model: 'gpt-6-astra'});

const fallbackTrigger = mod.st.listEl.querySelectorAll('.role-fallback')
  .find(row => row.dataset.role === 'fold');
assert.equal(fallbackTrigger.textContent,
  '2 selected: anthropic/opus → openai/gpt-6-astra', 'the trigger summarizes the saved order');
await fallbackTrigger.onclick();
const fallbackMenu = modelMenus.at(-1);
assert.equal(fallbackMenu.opts.persistent, true, 'fallback selection stays open for multiple picks');
assert.equal(fallbackMenu.anchor, fallbackTrigger, 'the fallback menu opens at its trigger');
let columns = fallbackMenu.opts.sections.find(section => section.kind === 'columns').columns;
assert.deepEqual(columns[0].items.map(item => item.label), ['anthropic', 'openai']);
assert.deepEqual(columns[1].items.map(item => item.label), ['Fable', 'Opus', 'Sonnet', 'Haiku']);
assert.equal(columns[1].items.find(item => item.label === 'Opus').on, true,
  'saved fallbacks start selected');

columns[0].onPick({id: 'openai'});
columns = fallbackMenu.opts.sections.find(section => section.kind === 'columns').columns;
assert.deepEqual(columns[1].items.map(item => item.label), ['Astra', 'Sol', 'Terra', 'Luna']);
columns[1].onPick({id: 'openai/gpt-5.6-sol'}, true);
columns[0].onPick({id: 'anthropic'});
columns = fallbackMenu.opts.sections.find(section => section.kind === 'columns').columns;
columns[1].onPick({id: 'anthropic/fable'}, true);
columns[1].onPick({id: 'anthropic/opus'}, false);
assert.equal(fallbackTrigger.textContent,
  '3 selected: openai/gpt-6-astra → openai/gpt-5.6-sol → anthropic/fable',
  'new picks append while deselection removes without reordering the rest');
const saveFallbacks = fallbackMenu.opts.sections.find(section => section.kind === 'buttons').buttons[0];
await saveFallbacks.onClick(fallbackMenu);
assert.deepEqual(JSON.parse(calls.filter(c => c.opts?.method === 'PATCH').at(-1).opts.body),
  {fold_cross_lab_fallback: ['openai/gpt-6-astra', 'openai/gpt-5.6-sol', 'anthropic/fable']});
assert.equal(fallbackMenu.closed, true, 'a successful save closes the picker');

// ---- the mouse is opt-in: unset renders unchecked, and ticking it PATCHes "on" ------------------
{
  const mouseBox = mod.st.listEl.querySelector('.settings-enable-mouse-checkbox');
  assert.ok(mouseBox, 'the panel carries the enable-mouse toggle');
  assert.equal(mouseBox.checked, false, 'the mouse is off by default');
  mouseBox.checked = true;
  mouseBox._listeners.change.forEach(fn => fn());
  await flush();
  const patch = calls.filter(c => c.path === '/api/settings' && c.opts?.method === 'PATCH').at(-1);
  assert.deepEqual(JSON.parse(patch.opts.body), {enable_mouse: 'on'});
  mouseBox.checked = false;
  mouseBox._listeners.change.forEach(fn => fn());
  await flush();
  const off = calls.filter(c => c.path === '/api/settings' && c.opts?.method === 'PATCH').at(-1);
  assert.deepEqual(JSON.parse(off.opts.body), {enable_mouse: null}, 'unticking clears it');
}

// ---- spend caps: blank is the default, a value is PATCHed under its own key ----------------------
{
  const spendNode = spendMenu.opts.sections.find(section => section.kind === 'node').node;
  const inputs = spendNode.querySelectorAll('.settings-spend-input');
  assert.equal(inputs.length, 5, 'worker, review, mission control, fold and the card total each have a cap');
  assert.equal(inputs[2].placeholder, '1.00', 'the mission control default shows as the placeholder');
  assert.equal(inputs[4].placeholder, 'no limit', 'the card total cap has no default - unset means unlimited');
  inputs[2].value = '2.5';
  inputs[2]._listeners.blur.forEach(fn => fn());
  await flush();
  const capPatch = calls.filter(c => c.path === '/api/settings' && c.opts?.method === 'PATCH').at(-1);
  assert.deepEqual(JSON.parse(capPatch.opts.body), {orchestrator_budget_usd: 2.5});
  inputs[0].value = 'lots';
  inputs[0]._listeners.blur.forEach(fn => fn());
  await flush();
  assert.ok(mod.spendCaps.statusEl.textContent.includes('positive amount'), 'a bad amount is refused in place');
}
const autoSwitchBox = mod.st.listEl.querySelector('.settings-auto-switch-checkbox');
assert.ok(autoSwitchBox, 'the credential section carries the auto-switch toggle');
assert.equal(autoSwitchBox.checked, false, 'rotation is opt-in - unset renders unchecked');
assert.ok(mod.readPaths.listEl.querySelector('.hazard-placeholder'), 'no folders yet shows a placeholder, not nothing');

// ---- checking the auto-switch box sends "on", unchecking sends null (opt-in, not opt-out) -------
autoSwitchBox.checked = true;
autoSwitchBox._listeners.change.forEach(fn => fn());
await flush();
let switchPatch = calls.filter(c => c.path === '/api/settings' && c.opts?.method === 'PATCH').at(-1);
assert.deepEqual(JSON.parse(switchPatch.opts.body), {auto_switch_profiles: 'on'});
autoSwitchBox.checked = false;
autoSwitchBox._listeners.change.forEach(fn => fn());
await flush();
switchPatch = calls.filter(c => c.path === '/api/settings' && c.opts?.method === 'PATCH').at(-1);
assert.deepEqual(JSON.parse(switchPatch.opts.body), {auto_switch_profiles: null});

// ---- the usage-limit route: unchecked switches by itself (unset), checked asks first -----------
const routeBox = mod.st.listEl.querySelector('.settings-usage-limit-route-checkbox');
assert.ok(routeBox, 'the labs group carries the usage-limit route toggle');
assert.equal(routeBox.checked, false, 'unset renders unchecked - the fallback switch stays automatic');
routeBox.checked = true;
routeBox._listeners.change.forEach(fn => fn());
await flush();
let routePatch = calls.filter(c => c.path === '/api/settings' && c.opts?.method === 'PATCH').at(-1);
assert.deepEqual(JSON.parse(routePatch.opts.body), {usage_limit_route: 'attention'});
routeBox.checked = false;
routeBox._listeners.change.forEach(fn => fn());
await flush();
routePatch = calls.filter(c => c.path === '/api/settings' && c.opts?.method === 'PATCH').at(-1);
assert.deepEqual(JSON.parse(routePatch.opts.body), {usage_limit_route: null});

// ---- parallelism stays in general settings; daily budgets have their own cost-control grid -----
assert.equal(mod.parallelCaps.globalInput.value, '', 'an unset global cap renders as an empty field, not 0');
const parallelRows = mod.parallelCaps.boardsList.querySelectorAll('.board-row');
assert.equal(parallelRows.length, 2, 'one parallel-limit row per board');
assert.equal(parallelRows[0].querySelector('.board-name').textContent, 'alpha');
assert.equal(parallelRows[0].querySelectorAll('input').length, 1,
  'the general grid only contains the parallel limit');
assert.equal(parallelRows[0].querySelector('input').value, '', 'alpha has no board-specific limit');
assert.equal(parallelRows[1].querySelector('.board-name').textContent, 'beta');
assert.equal(parallelRows[1].querySelector('input').value, '1', 'beta already carries its own limit');
const budgetRows = mod.dailyBudgets.boardsList.querySelectorAll('.board-row');
assert.equal(budgetRows.length, 2, 'one daily-budget row per board');
assert.equal(budgetRows[0].querySelectorAll('input').length, 1,
  'the cost-control grid only contains the daily budget');
assert.equal(budgetRows[0].querySelector('input').value, '', 'alpha has no daily budget');

// ---- saving a board's daily budget PATCHes /api/boards/<id> ------------------------------------
budgetRows[0].querySelector('input').value = '5.5';
budgetRows[0].querySelector('input')._listeners.blur.forEach(fn => fn());
await flush();
assert.equal(boardsState[0].daily_budget_usd, 5.5, "alpha now carries its own daily budget");
let boardPatch = calls.filter(call => call.path === '/api/boards/b1' && call.opts?.method === 'PATCH').at(-1);
assert.deepEqual(JSON.parse(boardPatch.opts.body), {daily_budget_usd: 5.5});

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

// ---- a board's own row PATCHes /api/boards/<id>, and an empty value clears the limit -------------
parallelRows[0].querySelector('input').value = '2';
parallelRows[0].querySelector('input')._listeners.blur.forEach(fn => fn());
await flush();
assert.equal(boardsState[0].max_parallel, 2, 'alpha now carries its own limit');
boardPatch = calls.filter(call => call.path === '/api/boards/b1' && call.opts?.method === 'PATCH').at(-1);
assert.deepEqual(JSON.parse(boardPatch.opts.body), {max_parallel: 2});
parallelRows[1].querySelector('input').value = '';
parallelRows[1].querySelector('input')._listeners.blur.forEach(fn => fn());
await flush();
assert.equal(boardsState[1].max_parallel, null, "clearing the field removes beta's limit");

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

console.log('ok');
