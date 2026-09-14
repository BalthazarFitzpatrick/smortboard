// scheduler.js: run-all/stop over w, the board-bar status line, and the digest popup over d.
// run: node tests/js/scheduler.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, queryAll} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => readFileSync(new URL(`../smortui/ui_base/assets/${p}`, root), 'utf8');
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const fetchCalls = [];
const fetchQueue = [];
function fetchImpl(path, opts) {
  fetchCalls.push({path, method: (opts && opts.method) || 'GET'});
  const next = fetchQueue.shift();
  // no response queued (board.js's own startup fetch for /api/boards, which this suite never
  // exercises) - hang forever rather than answer with a shape nothing here declared
  if (!next) return new Promise(() => {});
  return Promise.resolve({
    ok: next.status < 400,
    status: next.status,
    json: async () => next.body,
  });
}
function queueResponse(body, status = 200) { fetchQueue.push({status, body}); }

installStubDom({fetchImpl});

const boardBar = element('div', 'board-bar');
boardBar.id = 'board-bar';
document.body.appendChild(boardBar);

const bucketRow = element('div', 'bucket-row');
bucketRow.id = 'bucket-row';
['todo', 'doing', 'checking', 'accepted', 'rejected'].forEach(status => {
  const bucket = element('div', 'bucket');
  bucket.dataset.status = status;
  bucket.appendChild(element('div', 'bucket-label'));
  bucket.appendChild(element('div', 'bucket-rows'));
  bucketRow.appendChild(bucket);
});
document.body.appendChild(bucketRow);

// two card strips, shaped as board.js's renderCardStrip leaves them, so showRun has a foot to write
function addCardStrip(cardId) {
  const strip = element('div', 'row card card-strip');
  strip.dataset.cardId = cardId;
  const foot = element('div', 'card-foot');
  const badge = element('span', 'card-run');
  badge.hidden = true;
  foot.appendChild(badge);
  strip.appendChild(foot);
  bucketRow.appendChild(strip);
  return strip;
}
addCardStrip('c1');
addCardStrip('c2');

class SpyMenu {
  constructor(opts) { this.opts = opts; this.sections = opts.sections; SpyMenu.instances.push(this); }
  openAt(where) { this.where = where; return this; }
  refresh(sections) { this.sections = sections; }
  close() {}
}
SpyMenu.instances = [];

function SpyDrawer() {
  return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false};
}

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js'), smort('scheduler.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {
  toggleRunAll, openDigestPanel, renderScheduleStatus, applyScheduleToCards,
  digestSinceDefault, rememberDigestOpened,
  setCurrentBoardId: id => { currentBoardId = id; },
};`)(SpyMenu, SpyDrawer);

mod.setCurrentBoardId('b1');

function badgeText(cardId) {
  return queryAll(bucketRow, `.card-strip[data-card-id="${cardId}"]`)[0]
    .querySelector('.card-run').textContent;
}

// ---- w: run-all starts, and the board bar shows a compact status ------------------------------

queueResponse({running: ['c1'], queued: ['c2'], waiting: {}, paused_until: null});
await mod.toggleRunAll();

const runAllCall = fetchCalls.find(c => c.path === '/api/boards/b1/run-all');
assert.ok(runAllCall, 'w should POST run-all for the current board');
assert.equal(runAllCall.method, 'POST');

const statusEl = document.getElementById('schedule-status');
assert.ok(statusEl && !statusEl.hidden, 'the board bar shows a status while a schedule runs');
assert.equal(statusEl.textContent, '1 running - 1 queued');
assert.equal(badgeText('c1'), 'running', 'a running card carries it on its foot');
assert.equal(badgeText('c2'), 'queued', 'a queued card carries it on its foot too');

// ---- w again: stop clears the queue, running cards are left alone ------------------------------

queueResponse({running: ['c1'], queued: [], waiting: {}, paused_until: null});
await mod.toggleRunAll();
const stopCall = fetchCalls.find(c => c.path === '/api/boards/b1/run-all/stop');
assert.ok(stopCall, 'pressing w again should stop the queue');
assert.equal(stopCall.method, 'POST');
assert.equal(badgeText('c1'), 'running', 'a card already running keeps going after stop');

// ---- a waiting card shows its reason, and the status line reports a pause ----------------------

queueResponse({
  running: [], queued: ['c2'], waiting: {c2: 'lease conflict with card c1'},
  paused_until: Math.floor(Date.now() / 1000) + 3600,
});
await mod.toggleRunAll(); // scheduleRunning was left true above (still running c1) - this stops it
// drive one more explicit render call, independent of the toggle's own start/stop branch, over the
// exact server shape /api/boards/{id}/schedule returns
mod.renderScheduleStatus({
  running: [], queued: ['c2'], waiting: {c2: 'lease conflict with card c1'},
  paused_until: Math.floor(Date.now() / 1000) + 3600,
});
mod.applyScheduleToCards({
  running: [], queued: ['c2'], waiting: {c2: 'lease conflict with card c1'},
  paused_until: Math.floor(Date.now() / 1000) + 3600,
});
assert.equal(badgeText('c2'), 'waiting', 'a card the scheduler is holding back shows it on its foot');
assert.match(statusEl.textContent, /paused til \d{2}:\d{2}/);

// ---- an idle schedule hides the status line entirely --------------------------------------------

mod.renderScheduleStatus({running: [], queued: [], waiting: {}, paused_until: null});
assert.ok(statusEl.hidden, 'nothing running or queued and no pause - the status line disappears');

// ---- d: the digest remembers "since" across opens, defaulting to 0 the first time ---------------

assert.equal(mod.digestSinceDefault(), 0, 'never opened before - the digest shows everything');
mod.rememberDigestOpened();
assert.ok(mod.digestSinceDefault() > 0, 'opening the digest stamps a since value for next time');

queueResponse({
  pull_requests: [
    {card_id: 'c1', title: 'base card', url: 'https://example/pr/1', merge_after: [], dependency_not_in_batch: false},
    {card_id: 'c3', title: 'dependent card', url: 'https://example/pr/2', merge_after: ['c1'], dependency_not_in_batch: true},
  ],
  waiting_on_operator: [{card_id: 'c9', title: 'stuck card', reason: 'AGENT_QUESTION', question: 'which flow?'}],
  runs_and_spend: {runs: 4, total_cost_usd: 1.23},
});
mod.openDigestPanel();
await new Promise(r => setTimeout(r, 0)); // let the digest fetch's .then chain settle

const digestCall = fetchCalls.find(c => c.path.startsWith('/api/boards/b1/digest'));
assert.ok(digestCall, 'd should GET the board digest');

const menu = SpyMenu.instances[SpyMenu.instances.length - 1];
assert.equal(menu.opts.title, 'morning digest');
const card = menu.sections[0].node;
const labels = queryAll(card, '.field-label').map(el => el.textContent);
assert.ok(labels.includes('pull requests, in merge order'));
assert.ok(labels.includes('waiting on you'));
assert.ok(labels.some(l => l === 'base card'), 'each pull request is labelled by its card title');

const links = queryAll(card, '.digest-pr-link');
assert.deepEqual(links.map(l => l.textContent), ['https://example/pr/1', 'https://example/pr/2']);
assert.equal(links[0].href, 'https://example/pr/1', 'each pull request is a link');

const warnings = queryAll(card, '.digest-warn');
assert.equal(warnings.length, 1, 'only the dependent whose dependency was not in this batch is flagged');

const waitingRows = queryAll(card, '.digest-waiting');
assert.equal(waitingRows.length, 1);
assert.ok(waitingRows[0].children.some(c => c.textContent === 'which flow?'),
  'a blocked card carries its one question');

console.log('ok');
