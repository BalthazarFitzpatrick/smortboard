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

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), uiBase('pile.js'), uiBase('entrytext.js'), smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {cardPanelHtml, doAcceptOrRejectCard, showRun, runBadge, splitCommentHeadline,
  summarizeDescription, renderCardStrip, appendLine};`)(SpyMenu, SpyDrawer);

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
assert.ok(html1.includes('<a class="pr-link" href="https://x/pull/1" target="_blank" rel="noreferrer">https://x/pull/1</a>'),
  'panel should link the PR url, opening in a new tab');
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
// an operator comment keeps its own block, so its line breaks survive
const commented = mod.cardPanelHtml({...card, comments: [{author: 'operator', body: 'a\nb'}]}, outcome);
assert.ok(commented.includes('<div class="comment-body">a\nb</div>'), 'a comment body is its own block');

// a board comment leads with its first line and closes the rest behind details
const boardNoted = mod.cardPanelHtml(
  {...card, comments: [{author: 'smortboard', body: 'tests failed: test_x\n\nfull output here'}]},
  outcome,
);
assert.ok(boardNoted.includes('<div class="comment-headline">tests failed: test_x</div>'),
  'a board note leads with its one-liner');
assert.ok(boardNoted.includes('<details class="comment-details"><summary>details</summary>'),
  'the rest of a board note is closed behind details by default');
assert.ok(boardNoted.includes('full output here'), 'the full body is still there, just folded');

// only the latest board note gets the primary action button - an older one is history, not a CTA
const rejectedCard = {...card, status: 'rejected', comments: [
  {author: 'smortboard', body: 'first note\n\ndetail'},
  {author: 'smortboard', body: 'review findings\n\nfull findings'},
]};
const rejectedHtml = mod.cardPanelHtml(rejectedCard, outcome);
const ctaButtons = (rejectedHtml.match(/class="comment-cta"/g) || []).length;
assert.equal(ctaButtons, 1, 'exactly one comment carries the primary action button');

// ---- clickable PR links (8082e7af): a full url anywhere renders as a real link that opens in a
// new tab; a bare PR number only links when the card carries its own repo_url

// a PR url mentioned inside the worker summary, not just the dedicated outcome.pr_url field
const summaryWithUrl = mod.cardPanelHtml(card, {...outcome, pr_url: null, summary: 'opened https://x/pull/9 for review'});
assert.ok(
  summaryWithUrl.includes('opened <a class="pr-link" href="https://x/pull/9" target="_blank" rel="noreferrer">https://x/pull/9</a> for review'),
  'a PR url inside the summary text should become a link, not stay as plain text');

// a PR url inside a comment body
const commentWithUrl = mod.cardPanelHtml({...card, comments: [{author: 'operator', body: 'see https://x/pull/3'}]}, outcome);
assert.ok(
  commentWithUrl.includes('<a class="pr-link" href="https://x/pull/3" target="_blank" rel="noreferrer">https://x/pull/3</a>'),
  'a PR url inside a comment should become a link');

// only a PR number is stored (no url) - with a repo_url on the card, the link is built from it
const numberOnly = mod.cardPanelHtml({...card, repo_url: 'https://x/'}, {...outcome, pr_url: 42});
assert.ok(
  numberOnly.includes('<a class="pr-link" href="https://x/pull/42" target="_blank" rel="noreferrer">#42</a>'),
  'a bare PR number should link out using the card\'s own repo_url (trailing slash trimmed)');

// only a PR number, and no repo_url anywhere on the card - shown plainly rather than a dead link
const numberNoRepo = mod.cardPanelHtml(card, {...outcome, pr_url: 7});
assert.ok(numberNoRepo.includes('outcome-pr" data-state="ok">#7</div>'),
  'a bare PR number with no known repo should render as text, not a broken link');
assert.ok(!numberNoRepo.includes('<a'), 'no anchor should be drawn when there is nowhere for it to point');

// ---- the same linkifying rule drives chat lines (mission control / workforce), where a card's
// worker or the operator might paste a PR url mid-conversation
const chatLog = element('div', 'terminal-log');
const chatLine = mod.appendLine(chatLog, 'worker', 'done, see https://x/pull/5', null);
assert.ok(
  chatLine.innerHTML.includes('<a class="pr-link" href="https://x/pull/5" target="_blank" rel="noreferrer">https://x/pull/5</a>'),
  'a PR url in a chat line should render as a clickable link');
assert.ok(rejectedHtml.includes('data-cta-action="run">Rerun</button>'),
  "a rejected card's latest note offers Rerun, the same action the strip's CTA offers");

// ---- splitCommentHeadline: the first line leads, everything after is the detail
assert.deepEqual(mod.splitCommentHeadline('one line only'), {headline: 'one line only', rest: ''});
assert.deepEqual(mod.splitCommentHeadline('head\n\nbody\nmore'), {headline: 'head', rest: 'body\nmore'});

// ---- a 409 from accept shows a refused badge with the server's error, and does not throw
const strip = element('div', 'card-strip');
strip.dataset.cardId = 'c2';
const badge = element('span', 'card-run');
badge.hidden = true;
strip.appendChild(badge);
document.body.appendChild(strip);
strip.focus();
responses.set('/api/cards/c2/accept', stubJson(409, {error: 'card not in checking'}));
await mod.doAcceptOrRejectCard('accept', 'c2');
assert.equal(badge.hidden, false, 'the badge should show');
assert.equal(badge.textContent, "can't accept",
  'a 409 should name the refused action - a bare "refused" read as the labels swapped');
assert.equal(badge.title, 'card not in checking', 'the badge title should carry the server error');

// ---- summarizeDescription: strips section headers and bullets, cuts to 14 words (9bd5a207) ----

assert.equal(
  mod.summarizeDescription('GOAL:\n- Reverse the oversized CTA and restore a compact status note.'),
  'Reverse the oversized CTA and restore a compact status note.',
  'the GOAL: header is dropped, its bullet content becomes the plain summary',
);
// section headers past the first are dropped too, but their own content still fills the word
// budget if the first section left room - the summary is a word budget, not a "first section only"
assert.equal(
  mod.summarizeDescription('GOAL:\n- short goal\n\nSCOPE:\n- more detail here'),
  'short goal more detail here',
);
const longLine = Array.from({length: 20}, (_, i) => `word${i}`).join(' ');
assert.equal(mod.summarizeDescription(longLine).split(' ').length, 14, 'cut to at most 14 words');
assert.equal(mod.summarizeDescription(''), '', 'an empty description summarizes to nothing');
assert.equal(mod.summarizeDescription('RULES:\nCSS/view-side only.'), 'CSS/view-side only.',
  'any all-caps header line is dropped, not only GOAL:');
assert.ok(mod.summarizeDescription('GOAL: make x work').startsWith('make x'),
  'a header sharing its line with content is dropped, the content kept');
assert.equal(
  mod.summarizeDescription('GOAL: make x work\nOUT OF SCOPE: y'),
  'make x work y',
  'every line loses its leading label, multi-word ones included',
);

// ---- the overview strip shows the summary, not the raw description, and carries no short-id ----

const overviewStrip = mod.renderCardStrip({
  id: 'abcdef01-2222-3333-4444-555555555555', title: 't', status: 'todo',
  description: 'GOAL:\n- keep it short',
});
assert.ok(overviewStrip.innerHTML.includes('keep it short'), 'the overview shows the summary');
assert.ok(!overviewStrip.innerHTML.includes('GOAL:'), 'the overview never shows the GOAL: prefix');
assert.ok(!overviewStrip.innerHTML.includes('card-id'), 'the overview carries no short-id span');

// ---- the opened detail panel shows the full description and the short-id (9bd5a207) ------------

const detailCard = {
  id: 'abcdef01-2222-3333-4444-555555555555', title: 't', status: 'todo',
  description: 'GOAL:\n- keep it short\n\nSCOPE:\n- and the rest of it too',
  tasks: [], criteria: [], deps: [], attachments: [], comments: [],
};
const detailHtml = mod.cardPanelHtml(detailCard, {});
assert.ok(detailHtml.includes('abcdef01'), 'the opened detail shows the short-id');
assert.ok(detailHtml.includes('and the rest of it too'), 'the opened detail shows the full description');

// ---- complexity sits next to model in the status section: rated shows the level, unrated the
// estimate with an "(estimated)" note -------------------------------------------------------

const ratedCard = {...card, complexity: 2};
assert.ok(mod.cardPanelHtml(ratedCard, {}).includes('complexity: medium'), 'a rated card shows its level');

const unratedCard = {...card, complexity: null, criteria: [], tasks: [], leases: []};
assert.ok(mod.cardPanelHtml(unratedCard, {}).includes('complexity: low (estimated)'),
  'an unrated card with nothing else shows a low estimate');

console.log('ok');
