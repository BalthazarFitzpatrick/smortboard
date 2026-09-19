// a card's strip moves to its new column the moment its status changes, whatever changed it: a
// key, the card's own run, or mission control. todo -> doing and doing -> checking happen mid-run
// (lifecycle.py), so watching only which cards are "running" missed both - the board now polls
// every card's status and updated_at and redraws just the strips that moved. an open card's own
// strip is left alone until it closes; every other strip still redraws while a panel is open.
// run: node tests/js/follow_runs.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const responses = new Map();
const calls = [];
function stub(path, body) { responses.set(path, {ok: true, status: 200, json: async () => body}); }
function fetchStub(path) {
  calls.push(path);
  return Promise.resolve(responses.get(path) || {ok: false, status: 404, json: async () => ({})});
}

installStubDom({fetchImpl: fetchStub});
globalThis.getComputedStyle = () => ({transform: 'none', getPropertyValue: () => ''});

const card = (id, status) =>
  ({id, title: id, status, blocked_reason_code: null, criteria: [], tasks: [], updated_at: `t-${status}`});

stub('/api/boards', [{id: 'b1', name: 'one', position: 0, created_at: 't'}]);
stub('/api/boards/b1/cards', [card('c1', 'todo'), card('c2', 'todo')]);

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

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), uiBase('pile.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {followRunsOnce, onBoardEnter, doAcceptOrRejectCard, setOpenCard: v => { openCard = v; }};`)(SpyMenu, SpyDrawer);

const flush = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setTimeout(r, 0)); };
await flush();
const inColumn = (status, id) => bucketRow
  .querySelector(`.bucket[data-status="${status}"] .bucket-rows`)
  .querySelectorAll('.card-strip').some(s => s.dataset.cardId === id);
const strip = id => bucketRow.querySelectorAll('.card-strip').find(s => s.dataset.cardId === id);
const cardFetches = () => calls.filter(p => p === '/api/boards/b1/cards').length;

// ---- nothing has changed yet, so this poll redraws nothing - whether it is the very first look
// or board.js's own startup (followRuns() fires once on load) already took that first look for us
assert.ok(inColumn('todo', 'c1'), 'c1 starts in todo');
let fetched = cardFetches();
assert.equal(await mod.followRunsOnce(), false, 'nothing has changed, so nothing redraws');
assert.equal(cardFetches(), fetched + 1, 'the poll still asked the server once');

// ---- a status write that lands mid-run - not only when the run ends - moves the card within one poll
stub('/api/boards/b1/cards', [card('c1', 'doing'), card('c2', 'todo')]);
assert.equal(await mod.followRunsOnce(), true, 'todo -> doing redraws on the very next poll');
assert.ok(inColumn('doing', 'c1') && !inColumn('todo', 'c1'), 'c1 is drawn in doing now');
// the moved card is laid out by its new column like every other card, not left at its natural height
assert.match(String(strip('c1').style.height || ''), /^\d+(\.\d+)?px$/, 'a moved card gets the column card height');
assert.equal(strip('c1').style.height, strip('c2').style.height, 'the same height as the card that stayed');

stub('/api/boards/b1/cards', [card('c1', 'checking'), card('c2', 'todo')]);
assert.equal(await mod.followRunsOnce(), true, 'doing -> checking redraws on the very next poll too');
assert.ok(inColumn('checking', 'c1') && !inColumn('doing', 'c1'), 'c1 is drawn in checking now');

// ---- an unchanged board still asks, but redraws nothing
fetched = cardFetches();
assert.equal(await mod.followRunsOnce(), false, 'an unchanged board redraws nothing');
assert.equal(cardFetches(), fetched + 1);

// ---- keyboard focus follows a redrawn strip that moved, same as any other rebuild
strip('c2').focus();
stub('/api/boards/b1/cards', [card('c1', 'checking'), card('c2', 'accepted')]);
assert.equal(await mod.followRunsOnce(), true, 'an unrelated card moving is still a redraw');
assert.ok(inColumn('accepted', 'c2'), 'c2 is drawn in accepted now');
assert.equal(document.activeElement?.dataset?.cardId, 'c2', 'focus stays on the card that moved');

// ---- an open card holds its own redraw back - the strip underneath its panel is never rebuilt
strip('c1')._probe = 'do not touch';
mod.setOpenCard({cardId: 'c1'});
stub('/api/boards/b1/cards', [card('c1', 'accepted'), card('c2', 'accepted')]);
assert.equal(await mod.followRunsOnce(), false, 'the only change is the open card, so nothing redraws yet');
assert.equal(strip('c1')._probe, 'do not touch', "the open card's own strip is never rebuilt under the user");
assert.ok(inColumn('checking', 'c1'), 'the open card visually stays put until it closes');

// ---- with that same panel still open, an unrelated card's status change still moves its strip
stub('/api/boards/b1/cards', [card('c1', 'accepted'), card('c2', 'rejected')]);
assert.equal(await mod.followRunsOnce(), true, "another card's change redraws even while a panel is open");
assert.ok(inColumn('rejected', 'c2'), 'c2 moved to rejected while c1 stayed open');
assert.equal(strip('c1')._probe, 'do not touch', "the open card's strip is still the same untouched node");

// ---- closing the card lets its own deferred change land on the next poll
mod.setOpenCard(null);
assert.equal(await mod.followRunsOnce(), true, 'the open card gets its own redraw once it closes');
assert.ok(inColumn('accepted', 'c1') && !inColumn('checking', 'c1'), 'c1 is finally drawn in accepted');

// ---- a full reload always lands every card in the column of its stored status
await mod.onBoardEnter('b1');
assert.ok(inColumn('accepted', 'c1') && inColumn('rejected', 'c2'), 'a full reload matches stored status too');

// a landing can finish before the first poll after a board render
stub('/api/boards/b1/cards', [card('c1', 'checking'), card('c2', 'rejected')]);
await mod.onBoardEnter('b1');
responses.set('/api/cards/c1/accept', {ok: true, status: 202, json: async () => ({state: 'landing'})});
await mod.doAcceptOrRejectCard('accept', 'c1');
assert.ok(inColumn('checking', 'c1'));
stub('/api/boards/b1/cards', [card('c1', 'accepted'), card('c2', 'rejected')]);
assert.equal(await mod.followRunsOnce(), true);
assert.ok(inColumn('accepted', 'c1'));

console.log('ok');
