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
  // a function stub is called fresh per request with that request's opts, so a test can fail the
  // first POST and succeed the next one - proving a retry actually happened, without a GET (the
  // drawer's own load) consuming the same one-shot failure
  const resolved = typeof resp === 'function' ? resp(opts) : resp;
  return Promise.resolve(resolved || stubJson(404, {error: 'no stub for ' + path}));
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

// a spy wrapping ui_base's real indicateBadge, so the follow-pill tests below can assert on the
// exact count passed rather than guess at indicate.js's own rendering markup
const badgeSpySrc = 'const __badgeCalls = []; const __rawIndicateBadge = indicateBadge; ' +
  'indicateBadge = (host, n) => { __badgeCalls.push(n); return __rawIndicateBadge(host, n); };';
const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), badgeSpySrc, uiBase('shell.js'),
  smort('messageQueue.js'), smort('columns.js'), smort('card_panel.js'), smort('chat.js'),
  smort('shortcuts.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {
  loadRoster, jumpToCard, usageSections, sendMissionControl, renderMissionControl, mc, mcQueueFor,
  resolveWorkforceTarget, loadWorkforce, wf, buildDrawers, drawers, onBoardEnter, createMessageQueue,
  boardIdRef: () => currentBoardId, __badgeCalls,
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
  messages: [{id: 'm1', author: 'fabian', body: 'hi', created_at: '', cards: []}],
  plan: null, thinking: false, error: null, model: 'opus',
}));
await mod.onBoardEnter('b1');
assert.equal(mod.boardIdRef(), 'b1');

mod.drawers.right.open(); // triggers openMissionControl -> loadMissionControl
await new Promise(r => setTimeout(r, 0));
assert.equal(mod.mc.header.textContent, 'mission control - opus', 'the header names the model');

responses.set('/api/boards/b1/orchestrator', stubJson(202, {
  messages: [
    {id: 'm1', author: 'fabian', body: 'hi', created_at: '', cards: []},
    {id: 'm2', author: 'fabian', body: 'build the login card', created_at: '', cards: []},
  ],
  plan: null, thinking: true, error: null, model: 'opus',
}));
mod.mc.input.value = 'build the login card';
// sendMissionControl hands off to the send queue and returns right away - the queue itself does
// the network call, on the next microtask turn rather than inside this function
mod.sendMissionControl('build the login card');
await new Promise(r => setTimeout(r, 0));
const post = calls.find(c => c.path === '/api/boards/b1/orchestrator' && c.opts?.method === 'POST');
assert.ok(post, 'sending should POST to the board orchestrator route');
assert.deepEqual(JSON.parse(post.opts.body), {message: 'build the login card'}, 'the post body carries the message');
const thinkingLine = mod.mc.log.children.find(c => c.className.includes('author-thinking'));
assert.ok(thinkingLine, 'a thinking reply shows the dim thinking line');
// close before the 1500ms poll fires - closing clears mc.poll so the process can exit
mod.drawers.right.close();

// ---- chat log follow: pinned to the newest line by default, a pill once you scroll up -----------
// mission control replaces its whole log on every redraw, so this also proves the pill survives
// that rebuild instead of just a single appended line
{
  const log = mod.mc.log;
  const jump = mod.mc.jump;
  const render = messages => mod.renderMissionControl({messages, thinking: false, error: null, model: 'opus'});
  const line = (author, body) => ({author, body, cards: []});
  const scrollUp = () => {
    log.scrollHeight = 100; log.clientHeight = 40; log.scrollTop = 20;
    log._listeners.scroll.forEach(fn => fn());
  };
  const pressDown = () => log._listeners.keydown.forEach(fn => fn({code: 'ArrowDown'}));

  // following (the default): a redraw with a new line pins the view to the newest and no pill shows
  log.scrollHeight = 100; log.clientHeight = 40; log.scrollTop = 0;
  render([line('orchestrator', 'line one')]);
  assert.equal(log.scrollTop, log.scrollHeight, 'following keeps the newest line in view');
  assert.equal(jump.hidden, true, 'the pill stays hidden while following');
  assert.equal(mod.__badgeCalls.at(-1), 0, 'the badge reads zero while following');

  // once scrolled up, a line arriving must not move the offset, and must raise the pill's count
  scrollUp();
  const offsetWhileReading = log.scrollTop;
  render([line('orchestrator', 'line one'), line('orchestrator', 'line two')]);
  assert.equal(log.scrollTop, offsetWhileReading, 'a line arriving while scrolled up leaves the offset alone');
  assert.equal(jump.hidden, false, 'the pill shows once something new arrived while scrolled up');
  assert.equal(mod.__badgeCalls.at(-1), 1, 'one new line raises the count to one');

  // a redraw that repeats the same messages (a poll with nothing new) must not inflate the count
  const callsBeforeRepeat = mod.__badgeCalls.length;
  render([line('orchestrator', 'line one'), line('orchestrator', 'line two')]);
  assert.equal(log.scrollTop, offsetWhileReading, 'a redraw with nothing new still leaves the offset alone');
  assert.equal(mod.__badgeCalls.length, callsBeforeRepeat, 'nothing new means no badge update at all');

  // the pill jumps to the newest line and resumes following
  jump._listeners.click.forEach(fn => fn());
  assert.equal(log.scrollTop, log.scrollHeight, 'the pill jumps to the newest line');
  assert.equal(jump.hidden, true, 'the pill hides once it has resumed following');

  // scrolling up again restarts the count from zero, not from wherever it left off
  scrollUp();
  render([line('orchestrator', 'line one'), line('orchestrator', 'line two'), line('orchestrator', 'line three')]);
  assert.equal(jump.hidden, false, 'scrolling up and a new line shows the pill again');
  assert.equal(mod.__badgeCalls.at(-1), 1, 'the count restarts from zero rather than continuing from before');

  // focusing the log and pressing down twice jumps to the newest line and resumes following
  pressDown();
  assert.equal(jump.hidden, false, 'one down does not jump yet');
  pressDown();
  assert.equal(log.scrollTop, log.scrollHeight, 'down-down jumps to the newest line');
  assert.equal(jump.hidden, true, 'down-down resumes following');

  // sending a message jumps to the newest line and resumes following even while scrolled up
  scrollUp();
  render([line('orchestrator', 'line one'), line('orchestrator', 'line two'), line('orchestrator', 'line three'), line('orchestrator', 'line four')]);
  assert.equal(jump.hidden, false, 'scrolled up with something new, ahead of the send below');
  responses.set('/api/boards/b1/orchestrator', stubJson(200, {
    messages: [
      line('orchestrator', 'line one'), line('orchestrator', 'line two'),
      line('orchestrator', 'line three'), line('orchestrator', 'line four'),
      line('fabian', 'jump please'),
    ],
    thinking: false, error: null, model: 'opus',
  }));
  await mod.sendMissionControl('jump please');
  assert.equal(log.scrollTop, log.scrollHeight, 'sending a message jumps to the newest line');
  assert.equal(jump.hidden, true, 'and resumes following');
}

// ---- mission control never loses a message: a failed send stays queued, shows its own state, and
// is delivered once the request succeeds - without the operator resending anything by hand --------
responses.set('/api/boards/b2/cards', stubJson(200, []));
let b2PostAttempts = 0;
responses.set('/api/boards/b2/orchestrator', opts => {
  // the drawer's own GET (on open, and any poll) always succeeds - only the POST this test is
  // exercising fails once, so opening the drawer cannot eat the one-shot failure by accident
  if (opts?.method !== 'POST') {
    return stubJson(200, {messages: [], plan: null, thinking: false, error: null, model: 'opus'});
  }
  b2PostAttempts += 1;
  if (b2PostAttempts === 1) return stubJson(500, {error: 'boom'});
  return stubJson(200, {
    messages: [{id: 'm1', author: 'fabian', body: 'retry me', created_at: '', cards: []}],
    plan: null, thinking: false, error: null, model: 'opus',
  });
});
await mod.onBoardEnter('b2');
mod.drawers.right.open();
await new Promise(r => setTimeout(r, 0));

mod.sendMissionControl('retry me');
await new Promise(r => setTimeout(r, 0));
const queueLine = mod.mc.log.children.find(c => c.dataset.queueId);
assert.ok(queueLine, 'a message not yet confirmed by the server draws its own queued line');
assert.ok(queueLine.className.includes('queue-failed'),
  'a failed attempt is shown as failed, not silently dropped');
const stateBadge = queueLine.children.find(c => c.className === 'terminal-state');
assert.equal(stateBadge.textContent, 'failed', 'the line names its own state');

await new Promise(r => setTimeout(r, 700)); // past the queue's backoff - the retry should have landed
assert.equal(b2PostAttempts, 2, 'the second attempt is the queue retrying on its own, not a resend by hand');
assert.ok(!mod.mc.log.children.some(c => c.dataset.queueId),
  'once delivered the queued line is gone - the confirmed transcript replaced it');
assert.ok(mod.mc.log.children.some(c => c.className.includes('author-fabian') && !c.dataset.queueId),
  'the message now comes from the confirmed transcript, in the order it was sent');
mod.drawers.right.close();

// ---- a reload keeps an unsent message: a fresh queue against the same board id picks the
// message straight back up, without the operator retyping it -------------------------------------
{
  const b2Queue = mod.mcQueueFor('b2');
  assert.deepEqual(b2Queue.items(), [], 'nothing left queued for b2 after the retry test above');
  const neverResolves = () => new Promise(() => {}); // stands in for "still offline"
  const before = mod.createMessageQueue('reload-key', neverResolves, {backoffMs: [10_000]});
  before.enqueue('still here after reload');
  const after = mod.createMessageQueue('reload-key', neverResolves, {backoffMs: [10_000]});
  assert.deepEqual(after.items().map(i => i.body), ['still here after reload'],
    'a fresh queue instance against the same key - standing in for a page reload - keeps the unsent message');
}

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

// ---- , opens the workforce on the focused card and LEAVES FOCUS ON THE BOARD, so , closes it again.
// / is the way into the chat input, and escape hands focus back to the board
responses.set('/api/cards/c7/conversation', stubJson(200, {
  card_id: 'c7', title: 'focused card', running: false, phase: null, delivery: 'next_run', messages: [],
}));
strip.focus();
let prevented = false;
document._dispatch('keydown', {code: 'Comma', key: ',', target: strip, preventDefault() { prevented = true; }});
await new Promise(resolve => setTimeout(resolve, 0));
assert.ok(prevented, 'the comma is swallowed');
assert.equal(mod.wf.cardId, 'c7', 'the focused card is the target');
assert.equal(document.activeElement, strip, 'opening the drawer leaves focus on the card');
assert.ok(!mod.wf.input.focused, 'opening the drawer does not focus its input');

document._dispatch('keydown', {code: 'Slash', key: '/', target: strip, preventDefault() {}});
assert.equal(document.activeElement, mod.wf.input, '/ focuses the open chat input');
mod.wf.input._listeners.keydown.forEach(fn => fn({code: 'Escape', key: 'Escape', stopPropagation() {}, preventDefault() {}}));
assert.notEqual(document.activeElement, mod.wf.input, 'escape leaves the input');

strip.focus();
document._dispatch('keydown', {code: 'Comma', key: ',', target: strip, preventDefault() {}});
assert.ok(!mod.drawers.left.isOpen(), 'the same , closes the drawer it opened');

console.log('ok');
