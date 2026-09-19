// one run-state resolver decides the card foot: pollRun (this tab's own run) and the run-all
// scheduler each learn a card's run state on their own clock, and used to write the foot directly -
// which let the two race and flicker between labels for the same card. now both funnel through
// board.js's setLiveRunPhase/setQueueState, and this is what proves the foot never flickers again.
// run: node tests/js/run_state.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, queryAll, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

function fetchStub() { return new Promise(() => {}); } // no poller under test needs a real response

installStubDom({fetchImpl: fetchStub});

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

// a card strip shaped as board.js's renderCardStrip leaves them, so the resolver has a foot to
// write - attention optional, so the attention-guard test can add it on its own strip
function addCardStrip(cardId, {attention = false, actionText = 'Running…'} = {}) {
  const strip = element('div', `row card card-strip${attention ? ' card-attention' : ''}`);
  strip.dataset.cardId = cardId;
  const foot = element('div', 'card-foot');
  const note = element('span', 'card-action');
  note.textContent = actionText;
  foot.appendChild(note);
  const badge = element('span', 'card-run');
  badge.hidden = true;
  foot.appendChild(badge);
  strip.appendChild(foot);
  bucketRow.appendChild(strip);
  return strip;
}
addCardStrip('c1');
addCardStrip('c2');
addCardStrip('c3', {attention: true, actionText: 'Fix leases'});

class SpyMenu { constructor(opts) { this.opts = opts; } openAt() { return this; } close() {} }
function SpyDrawer() {
  return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false};
}

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), uiBase('pile.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js'), smort('scheduler.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {
  setLiveRunPhase, setQueueState, clearRunState, applyScheduleToCards,
};`)(SpyMenu, SpyDrawer);

function badgeText(cardId) {
  return queryAll(bucketRow, `.card-strip[data-card-id="${cardId}"]`)[0]
    .querySelector('.card-run').textContent;
}
function actionText(cardId) {
  return queryAll(bucketRow, `.card-strip[data-card-id="${cardId}"]`)[0]
    .querySelector('.card-action').textContent;
}

// ---- two pollers reporting the same card give one stable label, never alternating -------------

mod.setQueueState('c1', {kind: 'running'});
assert.equal(badgeText('c1'), 'running', 'the scheduler poll alone shows the coarse label');

mod.setLiveRunPhase('c1', 'starting');
assert.equal(badgeText('c1'), 'starting', 'a live phase from pollRun outranks the queue label');

// the scheduler's next tick still calls back in with 'running' - a stale poll must not flip the
// label back once the live phase is known, which is exactly the flicker this card fixes
mod.setQueueState('c1', {kind: 'running'});
assert.equal(badgeText('c1'), 'starting', 'a repeated scheduler poll does not undo the live phase');
mod.setLiveRunPhase('c1', 'starting');
assert.equal(badgeText('c1'), 'starting', 'a repeated pollRun tick reporting the same phase is stable too');

// interleave several more rounds of both pollers - the label never alternates between them
for (let i = 0; i < 3; i++) {
  mod.setQueueState('c1', {kind: 'running'});
  assert.equal(badgeText('c1'), 'starting', 'still the live phase, not the queue label');
  mod.setLiveRunPhase('c1', 'starting');
  assert.equal(badgeText('c1'), 'starting', 'still the live phase after its own poll too');
}

// ---- the live phase moving on is not a flicker - it is the run itself progressing -------------

mod.setLiveRunPhase('c1', 'running');
assert.equal(badgeText('c1'), 'running', 'a genuine phase change from the run itself still shows');

mod.clearRunState('c1');

// ---- queue position only reads "queued N of N" while actually queued --------------------------

mod.setQueueState('c2', {kind: 'queued', index: 2, total: 2});
assert.equal(badgeText('c2'), 'queued, 2 of 2', 'a queued card shows its position');

mod.setLiveRunPhase('c2', 'starting');
assert.equal(badgeText('c2'), 'starting', 'a live phase beats a queue position for the same card');

// the live phase clearing (the run itself, not the queue) falls back to the still-current queue
// state, rather than leaving a stale phase or blanking the corner
mod.setLiveRunPhase('c2', null);
assert.equal(badgeText('c2'), 'queued, 2 of 2', 'once no live phase remains, the queue position shows again');

mod.clearRunState('c2');

// ---- an attention card never shows "running" left and "starting" right ------------------------

assert.equal(actionText('c3'), 'Fix leases', 'c3 starts attention, naming the fix');
mod.setLiveRunPhase('c3', 'starting');
assert.equal(actionText('c3'), 'Fix leases', 'a live phase never overwrites an attention action note');
assert.equal(badgeText('c3'), 'starting', 'the run phase itself still shows on the right');
mod.clearRunState('c3');

// ---- scheduler.js's own entry point (applyScheduleToCards) funnels through the same resolver ---

mod.setLiveRunPhase('c1', 'starting');
mod.applyScheduleToCards({running: ['c1'], queued: [], waiting: {}, paused_until: null});
assert.equal(badgeText('c1'), 'starting', 'the scheduler view agrees the card is running, but the live phase still wins');
mod.clearRunState('c1');

console.log('ok');
