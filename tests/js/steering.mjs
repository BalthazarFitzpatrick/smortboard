// live steering, workforce panel side: the subheader and the reply after sending a note both
// reflect how the board says the note was delivered - "live" (agent's next step) vs "next_run".
// run: node tests/js/steering.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => readFileSync(new URL(`../ui_base/ui_base/assets/${p}`, root), 'utf8');
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const responses = new Map();
function stubJson(status, body) {
  return {ok: status >= 200 && status < 300, status, json: async () => body};
}
function fetchStub(path, opts) {
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
  let opened = false;
  return {
    el: element('div'), body: element('div'),
    open() { opened = true; }, close() { opened = false; }, toggle() { opened = !opened; },
    isOpen: () => opened,
  };
}
class SpyMenu { constructor(opts) { this.opts = opts; } openAt() { return this; } close() {} }

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {buildDrawers, drawers, buildWorkforceDom, loadWorkforce, renderWorkforceConversation, sendWorkforce, wf, DELIVERY_LABEL};`)(SpyMenu, SpyDrawer);

mod.buildDrawers();
mod.buildWorkforceDom(mod.drawers.left);

// ---- a live-running card: the subheader says the note reaches the agent at its next step --------
responses.set('/api/cards/c1/conversation', stubJson(200, {
  card_id: 'c1', title: 'ship the thing', running: true, phase: 'running', delivery: 'live',
  messages: [{author: 'agent', body: 'working on it', created_at: '2026-01-01T00:00:00Z'}],
}));
await mod.loadWorkforce({cardId: 'c1', pinned: true});
assert.equal(mod.wf.subheader.hidden, false);
assert.equal(mod.wf.subheader.textContent, mod.DELIVERY_LABEL.live);
assert.equal(mod.wf.subheader.textContent, "delivered at the agent's next step");

// ---- an idle card: the subheader falls back to "next run" -----------------------------------
responses.set('/api/cards/c2/conversation', stubJson(200, {
  card_id: 'c2', title: 'idle card', running: false, phase: null, delivery: 'next_run', messages: [],
}));
await mod.loadWorkforce({cardId: 'c2', pinned: true});
assert.equal(mod.wf.subheader.textContent, 'reaches the agent on its next run');

// ---- a note_delivered board event renders as a plain board line in the timeline -----------------
responses.set('/api/cards/c1/conversation', stubJson(200, {
  card_id: 'c1', title: 'ship the thing', running: true, phase: 'running', delivery: 'live',
  messages: [{author: 'board', body: 'delivered 1 note - agent had just finished its turn', created_at: '2026-01-01T00:00:01Z'}],
}));
await mod.loadWorkforce({cardId: 'c1', pinned: true});
const rendered = Array.from(mod.wf.log.children);
assert.ok(
  rendered.some(c => c.innerHTML.includes('delivered 1 note') && c.className.includes('author-board')),
  `expected a dim board line for note_delivered, got: ${rendered.map(c => c.innerHTML)}`,
);

// ---- sending a note while the run is live updates the subheader from the POST's own delivery ----
responses.set('/api/cards/c1/conversation', stubJson(201, {
  author: 'fabian', body: 'check the edge case', created_at: '2026-01-01T00:00:02Z', delivery: 'live',
}));
await mod.sendWorkforce('check the edge case');
assert.equal(mod.wf.subheader.textContent, "delivered at the agent's next step");

// ---- sending a note to an idle card reports next_run instead --------------------------------
mod.wf.cardId = 'c2';
responses.set('/api/cards/c2/conversation', stubJson(201, {
  author: 'fabian', body: 'later then', created_at: '2026-01-01T00:00:03Z', delivery: 'next_run',
}));
await mod.sendWorkforce('later then');
assert.equal(mod.wf.subheader.textContent, 'reaches the agent on its next run');

console.log('ok');
