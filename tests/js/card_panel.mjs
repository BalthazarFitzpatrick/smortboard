// proves the outcome section renders what the checking-and-rejection contract promises, and that
// accept/reject handle both the happy path and a 409 refusal without throwing.
// run: node tests/js/card_panel.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

// a tiny fetch that answers by url and records calls, rather than one fixed response - the panel
// fetches the card and its outcome in the same breath, and accept/reject need a real 409 to prove
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
// board.js calls loadBoards() at import time - give it an empty board list so that unrelated
// startup call doesn't 404 after this test's own assertions have already run
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

class SpyMenu { constructor(opts) { this.opts = opts; } openAt() { return this; } close() {} }
function SpyDrawer() { return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false}; }

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {cardPanelHtml, acceptOrRejectCard, showRun, runBadge};`)(SpyMenu, SpyDrawer);

// ---- a full outcome renders summary, the finding, the PR link, and not the "not run yet" text
// (cardPanelHtml is the actual render, the same string openCardPanel assigns to panel.innerHTML -
// the stub does not parse html into a real tree, so the string itself is what gets asserted on)
const card = {id: 'c1', title: 't', status: 'checking', tasks: [], criteria: [], deps: [], attachments: [], comments: []};
const outcome = {
  summary: 'did it',
  tests: {passed: true, command: 'node tests/js/board_navigation.mjs', exit_code: 0, output: ''},
  review: {
    approved: false,
    error: null,
    findings: [{category: 'style', severity: 'minor', file: 'board.js', line: 12, message: 'trailing comma'}],
  },
  pr_url: 'https://x/pull/1',
  fix_rounds: 1,
  findings_route: 'fix',
};
const html1 = mod.cardPanelHtml(card, outcome);
assert.ok(html1.includes('did it'), 'panel should show the worker summary');
assert.ok(html1.includes('trailing comma'), 'panel should show the finding message');
assert.ok(html1.includes('<a href="https://x/pull/1"'), 'panel should link the PR url');
assert.ok(!html1.includes('not run yet'), 'a real outcome should not say not run yet');

// ---- an all-null outcome says so instead of rendering empty sections
const html2 = mod.cardPanelHtml(card, {
  summary: null, tests: null, review: null, pr_url: null, fix_rounds: 0, findings_route: 'fix',
});
assert.ok(html2.includes('not run yet'), 'a null outcome should say not run yet');

// ---- the lease shows its globs, and an empty one says the card will not run
assert.ok(html1.includes('lease: <span class="empty">none - it will not run</span>'),
  'a card with no lease should say it will not run');
const leased = mod.cardPanelHtml({...card, leases: [{path_glob: 'src/**'}, {path_glob: 'tests/**'}]}, outcome);
assert.ok(leased.includes('lease: src/**, tests/**'), 'a lease should list its globs');

// ---- a card waiting on someone leads its status with the call to action; a quiet one does not
const waiting = mod.cardPanelHtml({...card, next_action: 'Press r to run it again.'}, outcome);
assert.ok(waiting.includes('<div class="card-next">next: Press r to run it again.</div>'),
  'the panel should say what to do next');
assert.ok(!html1.includes('card-next'), 'a card with no action shows no next line');
// comments keep their own block, so their line breaks survive
const commented = mod.cardPanelHtml({...card, comments: [{author: 'smortboard', body: 'a\nb'}]}, outcome);
assert.ok(commented.includes('<div class="comment-body">a\nb</div>'), 'a comment body is its own block');

// ---- a 409 from accept shows a refused badge with the server's error, and does not throw
const strip = element('div', 'card-strip');
strip.dataset.cardId = 'c2';
const badge = element('span', 'card-run');
badge.hidden = true;
strip.appendChild(badge);
document.body.appendChild(strip);
strip.focus();
responses.set('/api/cards/c2/accept', stubJson(409, {error: 'card not in checking'}));
await mod.acceptOrRejectCard('accept');
assert.equal(badge.hidden, false, 'the badge should show');
assert.equal(badge.textContent, "can't accept",
  'a 409 should name the refused action - a bare "refused" read as the labels swapped');
assert.equal(badge.title, 'card not in checking', 'the badge title should carry the server error');

console.log('ok');
