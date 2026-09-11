// phase 4 frontend: roster rows, usage rows, mission control send/poll, workforce target
// selection. the backend endpoints do not exist yet in this worktree - every case here stubs
// fetch and proves the panel degrades to a readable line rather than throwing.
// run: node tests/js/mission_control.mjs
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

// a menu spy that actually keeps track of what it was last given, so refresh() (the async
// load-then-fill pattern u/a use) can be asserted on
class SpyMenu {
  constructor(opts) { this.opts = opts; this.sections = opts.sections; }
  openAt() { return this; }
  refresh(sections) { this.sections = sections; }
  close() {}
}
function SpyDrawer(opts) {
  let opened = false;
  return {
    el: element('div'), body: element('div'),
    open() { opened = true; opts.onOpen?.(); }, close() { opened = false; opts.onClose?.(); },
    toggle() { opened = !opened; opened ? opts.onOpen?.() : opts.onClose?.(); },
    isOpen: () => opened,
  };
}

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {
  loadRoster, jumpToCard, usageSections, sendMissionControl, renderMissionControl, mc,
  resolveWorkforceTarget, loadWorkforce, wf, buildDrawers, drawers, onBoardEnter,
  boardIdRef: () => currentBoardId,
};`)(SpyMenu, SpyDrawer);

mod.buildDrawers();

// ---- agent roster: working (on), blocked (disabled, reason in stats), and empty -----------------
responses.set('/api/roster', stubJson(200, [
  {card_id: 'c1', board_id: 'b1', title: 'ship the thing', state: 'working', reason: null, activity: 'writing tests'},
  {card_id: 'c2', board_id: 'b1', title: 'fix the bug', state: 'blocked', reason: 'EXPIRED_CREDENTIAL', activity: ''},
]));
{
  const menu = new SpyMenu({title: 'agent roster', sections: []});
  await mod.loadRoster(menu);
  const items = menu.sections[0].items;
  assert.equal(items.length, 2, 'one row per agent');
  const working = items.find(i => i.id === 'c1');
  const blocked = items.find(i => i.id === 'c2');
  assert.equal(working.on, true, 'a working row is marked on');
  assert.equal(working.disabled, undefined ?? false, 'a working row stays pickable');
  assert.equal(blocked.disabled, true, 'a blocked row is disabled');
  assert.equal(blocked.stats, 'blocked: EXPIRED_CREDENTIAL', 'a blocked row shows the reason in stats');
}

responses.set('/api/roster', stubJson(200, []));
{
  const menu = new SpyMenu({title: 'agent roster', sections: []});
  await mod.loadRoster(menu);
  assert.equal(menu.sections[0].kind, 'node', 'an empty roster renders a node section');
}

// ---- usage: a null utilization shows no percentage, a real one does -----------------------------
{
  const sections = mod.usageSections({
    windows: [
      {type: 'five_hour', status: 'ok', resets_at: null, utilization: null},
      {type: 'seven_day', status: 'near limit', resets_at: 0, utilization: 0.82},
    ],
    models: [
      {model: 'opus', input_tokens: 12300, output_tokens: 4100, cache_read_tokens: 20000, cache_creation_tokens: 6400, cost_usd: 0.19},
    ],
    total_cost_usd: 0.19, runs: 3,
  });
  // one card-like node: ruled sections, ui_base fill bars, a foot with the total
  assert.equal(sections.length, 1, 'usage renders as one card');
  const card = sections[0].node;
  const windows = card.querySelectorAll('.usage-window');
  const statOf = el => el.querySelectorAll('.stat')[0].textContent;
  assert.ok(!statOf(windows[0]).includes('%'), 'a null utilization must not render a percentage');
  assert.equal(windows[0].querySelectorAll('.bar-fill').length, 0, 'no utilisation and no reset draws no bar');
  assert.ok(statOf(windows[1]).includes('82.0%'), 'a real utilization renders as a percentage');
  assert.equal(windows[1].querySelectorAll('.bar-fill')[0].style.width, '82%', 'and fills the bar that far');
  const model = card.querySelectorAll('.usage-model')[0];
  assert.ok(statOf(model).includes('12.3k'), 'token counts render abbreviated');
  assert.ok(model.querySelectorAll('.usage-model-name')[0].textContent.includes('$0.19'), 'model row carries its cost');
  assert.equal(model.querySelectorAll('.bar-fill')[0].style.width, '100%', 'the only model is all of the spend');
  assert.equal(card.querySelectorAll('.usage-foot .stat')[0].textContent, '3 runs - $0.19', 'the foot sums runs and cost');
}

// a window with a reset time but no utilisation shows how far through it we are, and says so
{
  const now = Date.now() / 1000;
  const sections = mod.usageSections({
    windows: [{type: 'five_hour', status: 'allowed', resets_at: now + 3600, utilization: null}],
    models: [], total_cost_usd: 0, runs: 1,
  });
  const win = sections[0].node.querySelectorAll('.usage-window')[0];
  assert.equal(win.querySelectorAll('.bar-fill')[0].style.width, '80%', 'four of five hours gone fills 80%');
  assert.ok(statOf2(win).includes('left in the window'), 'the line names what the bar measures');
  assert.ok(statOf2(win).includes('80% through'), 'and carries the number the bar shows, as time not usage');
  assert.ok(!statOf2(win).includes('used'), 'a time fraction never reads as usage');
  function statOf2(el) { return el.querySelectorAll('.stat')[0].textContent; }
}

// a reset time already in the past is old data: no bar, and the line says the window has reset
{
  const sections = mod.usageSections({
    windows: [{type: 'five_hour', status: 'allowed', resets_at: Date.now() / 1000 - 600, utilization: null}],
    models: [], total_cost_usd: 0, runs: 1,
  });
  const win = sections[0].node.querySelectorAll('.usage-window')[0];
  assert.equal(win.querySelectorAll('.bar-fill').length, 0, 'a window that already reset draws no bar');
  const line = win.querySelectorAll('.stat')[0].textContent;
  assert.ok(line.includes('reset at') && line.includes('reset since the last run'), `the line says so: ${line}`);
}

// no usage at all says so instead of three empty sections
{
  const sections = mod.usageSections({windows: [], models: [], total_cost_usd: 0, runs: 0});
  assert.equal(sections.length, 1, 'no usage renders a single placeholder section');
  assert.equal(sections[0].kind, 'node');
}

// ---- mission control: send posts the board id and message, then renders the polled reply --------
mod.onBoardEnter && (await (async () => {})()); // no-op, keeps this block's shape obvious
// currentBoardId is set by onBoardEnter, which also fetches /api/boards/b1/cards - stub it
responses.set('/api/boards/b1/cards', stubJson(200, []));
responses.set('/api/boards/b1/orchestrator', stubJson(200, {
  messages: [{id: 'm1', author: 'operator', body: 'hi', created_at: '', cards: []}],
  plan: null, thinking: false, error: null, model: 'opus',
}));
await mod.onBoardEnter('b1');
assert.equal(mod.boardIdRef(), 'b1');

mod.drawers.right.open(); // triggers openMissionControl -> loadMissionControl
await new Promise(r => setTimeout(r, 0));
assert.equal(mod.mc.header.textContent, 'mission control - opus', 'the header names the model');

responses.set('/api/boards/b1/orchestrator', stubJson(202, {
  messages: [
    {id: 'm1', author: 'operator', body: 'hi', created_at: '', cards: []},
    {id: 'm2', author: 'operator', body: 'build the login card', created_at: '', cards: []},
  ],
  plan: null, thinking: true, error: null, model: 'opus',
}));
mod.mc.input.value = 'build the login card';
await mod.sendMissionControl('build the login card');
const post = calls.find(c => c.path === '/api/boards/b1/orchestrator' && c.opts?.method === 'POST');
assert.ok(post, 'sending should POST to the board orchestrator route');
assert.deepEqual(JSON.parse(post.opts.body), {message: 'build the login card'}, 'the post body carries the message');
const thinkingLine = mod.mc.log.children.find(c => c.className.includes('author-thinking'));
assert.ok(thinkingLine, 'a thinking reply shows the dim thinking line');
// close before the 1500ms poll fires - closing clears mc.poll so the process can exit
mod.drawers.right.close();

// ---- workforce target: the focused card wins over the roster ------------------------------------
const strip = element('div', 'card-strip');
strip.dataset.cardId = 'c7';
document.body.appendChild(strip);
strip.focus();
{
  const target = await mod.resolveWorkforceTarget();
  assert.equal(target.cardId, 'c7', 'a focused card is the target');
  assert.equal(target.pinned, true, 'a focused card is pinned - no cycling through the roster');
}

// with nothing focused, the roster's first working row is the target
document.activeElement = document.body;
responses.set('/api/roster', stubJson(200, [
  {card_id: 'c9', board_id: 'b1', title: 'do the thing', state: 'working', reason: null, activity: 'coding'},
]));
{
  const target = await mod.resolveWorkforceTarget();
  assert.equal(target.cardId, 'c9', 'falls back to the first working roster row');
  assert.equal(target.pinned, false, 'a roster fallback is not pinned');
}

// with no focus and nothing working, workforce says so rather than guessing
responses.set('/api/roster', stubJson(200, []));
{
  const target = await mod.resolveWorkforceTarget();
  assert.equal(target.cardId, null);
}

// ---- , reads the focused card BEFORE its input takes focus, and does not type itself there -------
// the first real render showed a stray "," in the input and "no card is focused" over a focused card
responses.set('/api/cards/c7/conversation', stubJson(200, {
  card_id: 'c7', title: 'focused card', running: false, phase: null, delivery: 'next_run', messages: [],
}));
strip.focus();
let prevented = false;
document._dispatch('keydown', {code: 'Comma', key: ',', target: strip, preventDefault() { prevented = true; }});
await new Promise(resolve => setTimeout(resolve, 0));
assert.ok(prevented, 'the comma must not type itself into the input it focuses');
assert.equal(mod.wf.cardId, 'c7', 'the focused card is still the target after the input takes focus');
mod.drawers.left.close();

console.log('ok');
