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
  summarizeDescription, renderCardStrip, appendLine, parseWorkerBlock, complexityLabel};`)(SpyMenu, SpyDrawer);

// one section's own markup out of the panel string: from its data-section to the next one. the stub
// does not parse html into a tree, so "under the needs section" means inside this slice
function sectionOf(html, name) {
  const start = html.indexOf(`data-section="${name}"`);
  if (start === -1) return '';
  const next = html.indexOf('data-section="', start + 1);
  return html.slice(start, next === -1 ? html.length : next);
}
const sectionOrder = html => [...html.matchAll(/data-section="([a-z]+)"/g)].map(m => m[1]);
const count = (text, needle) => text.split(needle).length - 1;

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
const done1 = sectionOf(html1, 'done');
assert.ok(done1.includes('did it'), 'done should carry the worker summary');
assert.ok(done1.includes('trailing comma'), 'done should show the finding message, one line under review');
assert.ok(done1.includes('<a class="pr-link" href="https://x/pull/1" target="_blank" rel="noreferrer">#1</a>'),
  'done should link the PR url, opening in a new tab');
assert.ok(done1.includes('passed, exit 0'), 'the rail says the tests passed and their exit');
assert.ok(done1.includes('not approved'), 'the rail says the review did not approve');
assert.ok(done1.includes('<span class="rail-key">fix rounds</span><span class="verdict">1</span>'),
  'fix rounds show once there has been one');
assert.ok(!html1.includes('not run yet'), 'a real outcome should not say not run yet');
assert.ok(!html1.includes('findings route'), 'the findings route footer is gone');
assert.ok(!mod.cardPanelHtml(card, {...outcome, fix_rounds: 0}).includes('fix rounds'),
  'no fix rounds line while there have been none');

// ---- an all-null outcome says so instead of rendering empty sections
const html2 = mod.cardPanelHtml(card, {
  summary: null, tests: null, review: null, pr_url: null, fix_rounds: 0, findings_route: 'fix',
});
assert.ok(sectionOf(html2, 'done').includes('not run yet'), 'a null outcome should say not run yet');
assert.ok(!html2.includes('run-rail'), 'a card never run has no rail of empty steps');

// ---- bare bones, one column: head, about, done, needs, and the card's facts folded under details
assert.deepEqual(sectionOrder(html1), ['title', 'about', 'done', 'needs', 'details'], 'five sections, in reading order');
assert.ok(html1.includes('data-columns="1"'), 'one column at any width');
assert.ok(!html1.includes('data-section="workstream"'), 'the workstream section is gone');

// dependencies and attachments fold under details only when there are any
const linked = mod.cardPanelHtml({...card, depends_on: ['abcdef0123'], attachments: [{filename: 'shot.png'}]}, outcome);
const linkedDetails = sectionOf(linked, 'details');
assert.ok(linkedDetails.includes('<summary>dependencies - 1</summary>') && linkedDetails.includes('card abcdef01'),
  'a dependency off this board shows by short id, folded');
assert.ok(linkedDetails.includes('<summary>attachments - 1</summary>') && linkedDetails.includes('shot.png'));
assert.ok(!sectionOf(html1, 'details').includes('dependencies'), 'no dependency fold without dependencies');

// ---- the head: one meta line over the title - id, status, block reason in words, model, spend
const headHtml = sectionOf(mod.cardPanelHtml(
  {...card, id: 'abcdef01-2222', status: 'doing', blocked_reason_code: 'TESTS_FAILED', workstream: 'ui', model: 'sonnet', title: 'the title'},
  {...outcome, runs: 3, turns: 141, cost_usd: 6.771, cost_estimated: false},
), 'title');
assert.ok(headHtml.includes('<span class="card-id">abcdef01</span>'), 'the short id leads the meta line');
assert.ok(headHtml.includes('<span>doing</span>'));
assert.ok(headHtml.includes('<span class="card-meta-flag">tests failed</span>'), 'the reason code in plain words');
assert.ok(headHtml.includes('<span>ui</span>'), 'the workstream folds into the meta line');
assert.ok(headHtml.includes('<span class="card-model">sonnet</span>'));
assert.ok(headHtml.includes('<span>$6.77</span>') && !headHtml.includes('turns'), 'the cost only; runs and turns live under i');
const opusHead = sectionOf(mod.cardPanelHtml({...card, model: 'anthropic/claude-opus-5'}, outcome), 'title');
assert.ok(opusHead.includes('<span class="card-model">opus 5</span>'), 'a model is named the way people say it');
const haikuHead = sectionOf(mod.cardPanelHtml({...card, model: 'anthropic/claude-haiku-4-5-20251001'}, outcome), 'title');
assert.ok(haikuHead.includes('<span class="card-model">haiku 4.5</span>'), 'a version keeps its dot, a date suffix goes');
assert.ok(!headHtml.includes('complexity'), 'complexity stays in the card menu, not the head line');
assert.ok(headHtml.includes('<div class="section-value">the title</div>'), 'the title sits under the meta line');
assert.ok(!sectionOf(mod.cardPanelHtml(card, outcome), 'title').includes('runs'), 'no spend part for a card with no runs');

// ---- the lease shows its globs, and an empty one says the card will not run
assert.ok(sectionOf(html1, 'details').includes('<div class="empty">no lease - it will not run</div>'),
  'a card with no lease says so out loud, not behind a fold');
const leased = sectionOf(mod.cardPanelHtml({...card, leases: [{path_glob: 'src/**'}, {path_glob: 'tests/**'}]}, outcome), 'details');
assert.ok(leased.includes('<summary>lease - 2</summary>'), 'the lease folds behind its count');
assert.ok(leased.includes('<span class="card-path">src/**</span>, <span class="card-path">tests/**</span>'), 'a lease is one line of globs');

// ---- the worker's closing block: DONE lines under done, ACTION, WHY and NOT DONE under needs
const blockSummary = 'ACTION: review the PR\nWHY: criteria met\nDONE:\n- a\nNOT DONE:\n- b';
const reviewCard = {...card, review_flag: 1, next_action: 'Review the pull request, then press y to accept or x to reject.'};
const blockHtml = mod.cardPanelHtml(reviewCard, {...outcome, summary: blockSummary});
const blockDone = sectionOf(blockHtml, 'done');
const blockNeeds = sectionOf(blockHtml, 'needs');
assert.ok(blockDone.includes('<li>a</li>'), 'a DONE line is listed under done');
assert.ok(!blockDone.includes('review the PR') && !blockDone.includes('<li>b</li>'), 'done holds no ask and no leftover');
// one next step, one voice: the board's step wins, the agent's own ask is not drawn beside it
assert.ok(blockNeeds.includes('Review the pull request, then press y'), "the board's next action sits under needs");
assert.ok(!blockNeeds.includes('review the PR') && !blockNeeds.includes('criteria met'),
  "with a board step, the agent's ACTION and WHY are not a second voice");
assert.ok(blockNeeds.includes('left: b'), 'a NOT DONE line sits under needs');
// with no board step, the agent's own ask is the one next step, with its WHY
const askNeeds = sectionOf(mod.cardPanelHtml({...reviewCard, next_action: null}, {...outcome, summary: blockSummary}), 'needs');
assert.ok(askNeeds.includes('<li class="card-next">review the PR</li>'), "the agent's ACTION is the step when the board has none");
assert.ok(askNeeds.includes('why: criteria met'), 'with its WHY');
assert.ok(blockNeeds.includes('data-accent="attention"'), 'a card waiting on the operator wears the accent');
assert.ok(!blockHtml.includes('agent summary'), 'with a block to read, the summary is not folded in whole as well');

const parsed = mod.parseWorkerBlock('narration first\n\nACTION: none\nWHY: done\nDONE:\n- x\n- y\nNOT DONE:\n- none\nSCREENSHOT: the board');
assert.equal(parsed.action, 'none');
assert.deepEqual(parsed.done, ['x', 'y']);
assert.deepEqual(parsed.notDone, ['none'], 'the SCREENSHOT line is not a leftover');
assert.equal(parsed.before, 'narration first', 'narration above the block is kept apart');
assert.equal(mod.parseWorkerBlock('no block here'), null, 'no ACTION line, no block');
const noneHtml = sectionOf(mod.cardPanelHtml(reviewCard, {...outcome, summary: 'ACTION: none\nDONE:\n- x\nNOT DONE:\n- none'}), 'needs');
assert.ok(!noneHtml.includes('agent: none') && !noneHtml.includes('left: none'), '"none" says nothing is left, so it is not drawn');

// ---- the summary's first sentence is shown once, not as a header and again as its body
const sentence = 'Keyed the endpoint on the header.';
const once = mod.cardPanelHtml(card, {...outcome, summary: `${sentence} Covered the replay case.`});
assert.equal(count(once, 'Keyed the endpoint'), 1, 'the first sentence is not duplicated without a block');
const onceBlock = mod.cardPanelHtml(card, {...outcome, summary: `ACTION: none\nWHY: done\nDONE:\n- ${sentence}\nNOT DONE:\n- none`});
assert.equal(count(onceBlock, 'Keyed the endpoint'), 1, 'nor with one');

// ---- a summary with no block falls back: the text folded under done, reason and next under needs
const crashed = {...card, status: 'doing', blocked_reason_code: 'CRASH', next_action: 'Check the note for what broke.'};
const fallback = mod.cardPanelHtml(crashed, {...outcome, summary: 'older run, plain prose'});
assert.ok(sectionOf(fallback, 'done').includes('<details class="card-fold"><summary>agent summary - 22 chars</summary>'),
  'the whole summary folds under done');
assert.ok(sectionOf(fallback, 'done').includes('older run, plain prose'));
assert.ok(!sectionOf(fallback, 'needs').includes('blocked: crash'), 'the reason lives in the head line, not again under needs');
assert.ok(sectionOf(fallback, 'needs').includes('<li class="card-next">Check the note for what broke.</li>'),
  'needs carries the one next step');
const bare = sectionOf(mod.cardPanelHtml({...crashed, next_action: null}, {...outcome, summary: 'older run, plain prose'}), 'needs');
assert.ok(bare.includes('<li>blocked: crash</li>'), 'with no step to give, needs says what went wrong');

// ---- a card waiting on someone leads needs with the call to action; a quiet one does not
const waiting = mod.cardPanelHtml({...card, blocked_reason_code: 'TESTS_FAILED', next_action: 'Press r to run it again.'}, outcome);
assert.ok(sectionOf(waiting, 'needs').includes('<li class="card-next">Press r to run it again.</li>'),
  'the panel should say what to do next');
assert.ok(!html1.includes('card-next'), 'a card with no action shows no next line');

// ---- the paths the run wanted in its lease, or was widened to, sit under needs
const leaseAsk = sectionOf(mod.cardPanelHtml(crashed, {...outcome, lease_wanted: ['src/y.py'], lease_expanded: ['src/x.py', 'src/z.py']}), 'needs');
assert.ok(leaseAsk.includes('lease widened: <span class="card-path">src/x.py</span>, <span class="card-path">src/z.py</span>'),
  'lease_expanded paths render under needs');
assert.ok(leaseAsk.includes('wants lease: <span class="card-path">src/y.py</span>'), 'and lease_wanted ones');

// ---- a working card needs nothing, and says so without the accent
const workingNeeds = sectionOf(mod.cardPanelHtml({...card, status: 'doing'}, outcome), 'needs');
assert.ok(workingNeeds.includes('nothing, agent on it'), "a working card's needs reads as nothing needed");
assert.ok(!workingNeeds.includes('data-accent'), 'and wears no accent');
const retrying = sectionOf(mod.cardPanelHtml(
  {...card, status: 'doing', blocked_reason_code: 'API_UNREACHABLE', handled_by_board: true, next: 'retry at 21:40 UTC'}, outcome,
), 'needs');
assert.ok(retrying.includes('nothing, board on it - retry at 21:40 UTC'), 'a card the board is retrying needs nothing either');
assert.ok(!retrying.includes('data-accent'));
assert.ok(sectionOf(html1, 'needs').includes('<input class="comment-input text-field"'), "the card's one text entry sits in needs");

// ---- history: one line per note, time and headline, the operator's own marked "you"
const commented = mod.cardPanelHtml({...card, comments: [{author: 'operator', body: 'a\nb', created_at: '2026-09-14T12:00:00+00:00'}]}, outcome);
const history = sectionOf(commented, 'details');
assert.ok(history.includes('<summary>history - 1</summary>'), 'history folds behind its count');
assert.ok(/<time>09-14 \d\d:\d\d<\/time>/.test(history), 'a short time leads the line');
assert.ok(history.includes('<span class="history-head history-you">you: a</span>'), 'an operator line reads as you');
assert.ok(history.includes('<div class="comment-body">b</div>'), 'the body is folded behind the headline');

// a board note leads with its first line and closes the rest behind details
const boardNoted = sectionOf(mod.cardPanelHtml(
  {...card, comments: [{author: 'smortboard', body: 'tests failed: test_x\n\nfull output here'}]},
  outcome,
), 'details');
assert.ok(boardNoted.includes('<span class="history-head">tests failed: test_x</span>'), 'a board note leads with its one-liner');
assert.ok(boardNoted.includes('<details class="history-note"><summary class="history-line">'),
  'the rest of a board note is closed behind details by default');
assert.ok(boardNoted.includes('full output here'), 'the full body is still there, just folded');

// only the latest board note's action is offered, once, in needs - an older one is history
const rejectedCard = {...card, status: 'rejected', comments: [
  {author: 'smortboard', body: 'first note\n\ndetail'},
  {author: 'smortboard', body: 'review findings\n\nfull findings'},
]};
const rejectedHtml = mod.cardPanelHtml(rejectedCard, outcome);
assert.equal(count(rejectedHtml, 'comment-cta'), 1, 'exactly one primary action button');
assert.ok(sectionOf(rejectedHtml, 'needs').includes('data-cta-action="run">Rerun</button>'),
  "a rejected card offers Rerun under needs, the same action the strip's CTA offers");
assert.ok(!mod.cardPanelHtml({...card, status: 'rejected'}, outcome).includes('comment-cta'),
  'no board note, no button');

// ---- clickable PR links (8082e7af): a full url anywhere renders as a real link that opens in a
// new tab; a bare PR number only links when the card carries its own repo_url

// a PR url mentioned inside the worker summary, not just the dedicated outcome.pr_url field
const summaryWithUrl = mod.cardPanelHtml(card, {...outcome, pr_url: null, summary: 'opened https://x/pull/9 for review'});
assert.ok(
  summaryWithUrl.includes('opened <a class="pr-link" href="https://x/pull/9" target="_blank" rel="noreferrer">https://x/pull/9</a> for review'),
  'a PR url inside the summary text should become a link, not stay as plain text');
assert.ok(sectionOf(summaryWithUrl, 'done').includes('<span class="verdict">none yet</span>'), 'no pr yet says so');

// a PR url inside a comment
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
assert.ok(numberNoRepo.includes('<span class="rail-key">pr</span><span class="verdict">#7</span>'),
  'a bare PR number with no known repo should render as text, not a broken link');
assert.ok(!numberNoRepo.includes('<a'), 'no anchor should be drawn when there is nowhere for it to point');

// ---- the same linkifying rule drives chat lines (mission control / workforce), where a card's
// worker or the operator might paste a PR url mid-conversation
const chatLog = element('div', 'terminal-log');
const chatLine = mod.appendLine(chatLog, 'worker', 'done, see https://x/pull/5', null);
assert.ok(
  chatLine.innerHTML.includes('<a class="pr-link" href="https://x/pull/5" target="_blank" rel="noreferrer">https://x/pull/5</a>'),
  'a PR url in a chat line should render as a clickable link');

// ---- the commit the summary names rides the done label
const committed = sectionOf(mod.cardPanelHtml(card, {...outcome, summary: 'ACTION: none\nDONE:\n- committed as ce051d14aa on this branch'}), 'done');
assert.ok(committed.includes('done<span class="field-count">commit ce051d14</span>'), 'the done label names the commit');

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
const longLine = Array.from({length: 30}, (_, i) => `word${i}`).join(' ');
assert.equal(mod.summarizeDescription(longLine).split(' ').length, 14, 'cut to at most 14 words');
assert.equal(mod.summarizeDescription(longLine, 20).split(' ').length, 20, 'or to the limit it is given');
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

// ---- about: a ~20-word lead, the whole brief folded under it, and the short id in the head ------

const detailCard = {
  id: 'abcdef01-2222-3333-4444-555555555555', title: 't', status: 'todo',
  description: 'GOAL:\n- keep it short\n\nSCOPE:\n- and the rest of it too',
  tasks: [{text: 'first step', done: true}], criteria: [], deps: [], attachments: [], comments: [],
};
const detailHtml = mod.cardPanelHtml(detailCard, {});
const about = sectionOf(detailHtml, 'about');
assert.ok(sectionOf(detailHtml, 'title').includes('abcdef01'), 'the head shows the short-id');
assert.ok(about.includes('<div class="card-lead">keep it short and the rest of it too</div>'), 'about leads with the summary');
assert.ok(about.includes(`<summary>full brief - ${detailCard.description.length} chars</summary>`), 'the full brief is folded, sized');
assert.ok(about.includes('SCOPE:'), 'the folded brief is the whole description');
assert.ok(about.includes('<li>[x] first step</li>'), 'tasks sit under about');
const longAbout = sectionOf(mod.cardPanelHtml({...detailCard, description: longLine}, {}), 'about');
assert.ok(longAbout.includes('word19…</div>'), 'a lead cut short says so');
const shortAbout = sectionOf(mod.cardPanelHtml({...detailCard, description: 'one line'}, {}), 'about');
assert.ok(!shortAbout.includes('full brief'), 'a brief the lead already shows whole is not folded again');
assert.ok(sectionOf(mod.cardPanelHtml({...detailCard, description: ''}, {}), 'about').includes('no brief'));
assert.ok(sectionOf(mod.cardPanelHtml({...detailCard, status: 'todo'}, {}), 'needs').includes('nothing yet, r runs it'));

// ---- complexity is read in the card menu (c after m): rated shows the level, unrated the estimate
// with an "(estimated)" note ------------------------------------------------------------------

assert.equal(mod.complexityLabel({...card, complexity: 2}), 'medium', 'a rated card shows its level');
assert.equal(mod.complexityLabel({...card, complexity: null, criteria: [], tasks: [], leases: []}), 'low (estimated)',
  'an unrated card with nothing else shows a low estimate');


console.log('ok');
