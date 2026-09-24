// board cost overview (c): every board's spend, costliest first, plus totals - a centred panel
// like usage and telemetry. stubs fetch and reads the rendered node tree. run: node tests/js/costs.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const responses = new Map();
function stubJson(status, body) {
  return {ok: status >= 200 && status < 300, status, json: async () => body};
}
function fetchStub(path) {
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

class SpyMenu {
  constructor(opts) { this.opts = opts; this.title = opts.title; this.sections = opts.sections; }
  openAt() { return this; }
  refresh(sections) { this.sections = sections; }
  close() {}
}
function SpyDrawer() {
  return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false};
}

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), uiBase('pile.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js'), smort('telemetry.js'), smort('costs.js')].join('\n;\n');
const jumps = [];
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {
  openCostsOverviewPanel, costsOverviewSections,
  overlayRef: () => openOverlay,
  setCurrentBoardId: id => { currentBoardId = id; },
  currentBoardIdRef: () => currentBoardId,
};`)(SpyMenu, SpyDrawer);

function flatText(node) {
  const own = (node.textContent || '').trim();
  const kids = (node.children || []).flatMap(flatText);
  return own ? [own, ...kids] : kids;
}

// ---- c opens the panel and renders every board, costliest first, with a totals foot -------------

responses.set('/api/costs', stubJson(200, {
  boards: [
    {
      board_id: 'b1', board_name: 'pricey board', cost_usd: 0.88, runs: 1, cards: 1,
      cards_accepted: 0, pull_requests_opened: 0, refusal_cost_usd: 0.88,
      worker_cost_usd: 0.88, reviewer_cost_usd: 0, refusal_count: 1,
      spend_by_model: [{model: 'opus', cost_usd: 0.88}], cost_per_pr_usd: null,
    },
    {
      board_id: 'b2', board_name: 'cheap board', cost_usd: 0.13, runs: 1, cards: 1,
      cards_accepted: 1, pull_requests_opened: 1, refusal_cost_usd: 0,
      worker_cost_usd: 0.10, reviewer_cost_usd: 0.03, refusal_count: 0,
      spend_by_model: [{model: 'claude-sonnet-4', cost_usd: 0.13}], cost_per_pr_usd: 0.13,
    },
  ],
  totals: {
    board_id: null, board_name: 'all boards', cost_usd: 1.01, runs: 2, cards: 2,
    cards_accepted: 1, pull_requests_opened: 1, refusal_cost_usd: 0.88,
    worker_cost_usd: 0.98, reviewer_cost_usd: 0.03,
    spend_by_model: [{model: 'opus', cost_usd: 0.88}, {model: 'claude-sonnet-4', cost_usd: 0.13}],
    cost_per_pr_usd: 1.01,
    turn_cost_usd: 0,
  },
  orchestrator_turns_counted: false,
  cost_groups: {
    total: {cards: 2, cost_usd: 1.01, prs: 1, cost_per_card_usd: 0.505, cost_per_pr_usd: 1.01},
    accepted: {cards: 1, cost_usd: 0.13, prs: 1, cost_per_card_usd: 0.13, cost_per_pr_usd: 0.13},
    refused: {cards: 1, cost_usd: 0.88, prs: 0, cost_per_card_usd: 0.88, cost_per_pr_usd: null},
  },
}));

mod.openCostsOverviewPanel();
await new Promise(r => setTimeout(r, 0));
const menu = mod.overlayRef().menu;
assert.equal(menu.title, 'cost overview');
const text = flatText({children: menu.sections.map(s => s.node || {children: []})}).join(' | ');
// the 3x3 grid leads, total then accepted then refused, before the per-board table
assert.match(text, /total \| \$1\.01 spend \| \$0\.51 \/ card \| \$1\.01 \/ pr \| 2 cards · 1 pr/);
assert.match(text, /accepted \| \$0\.13 spend \| \$0\.13 \/ card \| \$0\.13 \/ pr \| 1 card · 1 pr/);
assert.match(text, /refused \| \$0\.88 spend \| \$0\.88 \/ card \| — \/ pr \| 1 card · 0 prs/);
assert.ok(text.indexOf('total |') < text.indexOf('board | share'), 'the grid sits above the board table');
assert.ok(text.indexOf('accepted |') < text.indexOf('refused |'), 'accepted before refused, left to right');
// one row per board, costliest first, the figures in column order under a header row
assert.equal(menu.sections.length, 1, 'a single card, no separate jump list');
assert.match(text, /board \| share \| spend \| runs \| accepted \| prs \| per pr \| spend w\/ denial/);
assert.match(text, /pricey board \| \$0\.88 \| 1 \| 0\/1 \| 0 \| - \| \$0\.88/);
assert.match(text, /cheap board \| \$0\.13 \| 1 \| 1\/1 \| 1 \| \$0\.13 \| -/);
assert.ok(text.indexOf('pricey board') < text.indexOf('cheap board'), 'costliest board leads');
assert.match(text, /mission control and fold turns \$/);
assert.match(text, /2 boards - 2 cards - 2 runs - \$1\.01/);
assert.match(text, /\$0\.88 on runs with a permission denial/);
assert.match(text, /worker \$0\.98 - reviewer \$0\.03/);
assert.match(text, /anthropic - opus \$0\.88, claude-sonnet-4 \$0\.13/);

// ---- clicking a board's name jumps to it: same switch-and-render a tab click does ---------------

const walk = (node, out = []) => { out.push(node); (node.children || []).forEach(c => walk(c, out)); return out; };
const nameCell = walk(menu.sections[0].node).find(n => n.classList?.contains('cost-name') && n.textContent === 'pricey board');
mod.setCurrentBoardId('b2');
responses.set('/api/boards/b1/cards', stubJson(200, []));
nameCell.onclick();
await new Promise(r => setTimeout(r, 0));
assert.equal(mod.currentBoardIdRef(), 'b1', 'picking a row switches to that board');

// ---- c again closes the panel, same toggle as every other overlay key ---------------------------

mod.openCostsOverviewPanel();
assert.equal(mod.overlayRef(), null);

// ---- no boards reads as a placeholder ------------------------------------------------------------

const emptySections = mod.costsOverviewSections({boards: [], totals: {}});
assert.equal(emptySections.length, 1);
assert.match(emptySections[0].node.innerHTML, /no boards yet/);

// ---- runs with no recorded price: the known sum stays, the unpriced runs are counted beside it,
// share bars fill from the known sums, and a board with nothing priced reads plain "unknown" ------

const mixed = mod.costsOverviewSections({
  boards: [
    {
      board_id: 'b1', board_name: 'comet', cost_usd: null, known_cost_usd: 4.99, unknown_costs: 3,
      runs: 5, cards: 2, cards_accepted: 1, pull_requests_opened: 1,
      refusal_cost_usd: 0, refusal_known_cost_usd: 0, refusal_unknown_costs: 0,
      cost_per_pr_usd: null, known_cost_per_pr_usd: 4.99, spend_by_model: [],
    },
    {
      board_id: 'b2', board_name: 'old board', cost_usd: null, known_cost_usd: 0, unknown_costs: 2,
      runs: 2, cards: 1, cards_accepted: 0, pull_requests_opened: 0,
      refusal_cost_usd: 0, refusal_known_cost_usd: 0, refusal_unknown_costs: 0,
      cost_per_pr_usd: null, known_cost_per_pr_usd: null, spend_by_model: [],
    },
  ],
  totals: {
    cost_usd: null, known_cost_usd: 4.99, unknown_costs: 5, runs: 7, cards: 3,
    cards_accepted: 1, pull_requests_opened: 1,
    refusal_cost_usd: 0, refusal_known_cost_usd: 0, refusal_unknown_costs: 0,
    worker_cost_usd: 4.5, worker_known_cost_usd: 4.5, worker_unknown_costs: 0,
    reviewer_cost_usd: null, reviewer_known_cost_usd: 0.49, reviewer_unknown_costs: 5,
    cost_per_pr_usd: null, known_cost_per_pr_usd: 4.99,
    spend_by_model: [
      {model: 'anthropic/sonnet', cost_usd: null, known_cost_usd: 4.5, unknown_costs: 2},
      {model: 'anthropic/opus', cost_usd: null, known_cost_usd: 0, unknown_costs: 3},
    ],
    turn_cost_usd: 0, turn_known_cost_usd: 0, turn_unknown_costs: 0,
  },
  cost_groups: {
    total: {cards: 3, cost_usd: null, known_cost_usd: 4.99, unknown_costs: 5, prs: 1,
      cost_per_card_usd: null, cost_per_pr_usd: null, known_cost_per_card_usd: 1.6633, known_cost_per_pr_usd: 4.99},
    accepted: {cards: 1, cost_usd: 4.99, known_cost_usd: 4.99, unknown_costs: 0, prs: 1,
      cost_per_card_usd: 4.99, cost_per_pr_usd: 4.99, known_cost_per_card_usd: 4.99, known_cost_per_pr_usd: 4.99},
    refused: {cards: 1, cost_usd: null, known_cost_usd: 0, unknown_costs: 2, prs: 0,
      cost_per_card_usd: null, cost_per_pr_usd: null, known_cost_per_card_usd: null, known_cost_per_pr_usd: null},
  },
});
const mixedText = flatText({children: [mixed[0].node]}).join(' | ');
assert.match(mixedText, /total \| \$4\.99 \+ 5 unknown spend \| \$1\.66 \/ card \| \$4\.99 \/ pr/);
assert.match(mixedText, /accepted \| \$4\.99 spend/);
assert.match(mixedText, /refused \| unknown spend \| — \/ card \| — \/ pr/);
assert.match(mixedText, /comet \| \$4\.99 \+ 3 unknown \| 5 \| 1\/2 \| 1 \| \$4\.99 \| -/);
assert.match(mixedText, /old board \| unknown \| 2 \| 0\/1 \| 0 \| - \| -/);
assert.match(mixedText, /2 boards - 3 cards - 7 runs - \$4\.99 \+ 5 unknown - \$4\.99 \/ pr/);
assert.match(mixedText, /worker \$4\.50 - reviewer \$0\.49 \+ 5 unknown/);
assert.match(mixedText, /anthropic - anthropic\/sonnet \$4\.50 \+ 2 unknown, anthropic\/opus unknown/);
assert.match(mixedText, /mission control and fold turns \$0\.00/);
const fills = walk(mixed[0].node).filter(n => (n.className || '').startsWith('bar-fill'));
assert.deepEqual(fills.map(f => f.style.width), ['100%', '0%'], 'share bars fill from the known sums');

// ---- the header switches between "cost" and "cost optimisation" via ArrowLeft/ArrowRight and
// via the two arrow buttons - the optimisation view lazily loads and renders its four sections ---

function press(code) {
  document._dispatch('keydown', {code, key: code, target: document.body, preventDefault() {}, stopPropagation() {}});
}

responses.set('/api/costs/optimisation', stubJson(200, {
  board_id: null,
  cap_fit: {
    worker_cap_usd: 5,
    rows: [
      {complexity: 1, source: 'rated', runs: 3, within_cap: 2, hit_cap: 1, median_cost_usd: 0.4, max_cost_usd: 5.2},
    ],
  },
  suggested_caps: {
    worker: {current_cap_usd: 5, runs: 6, p90_cost_usd: 1.8, suggested_cap_usd: 2.0, runs_that_would_be_cut: 1, spend_difference_usd: 0.3},
    reviewer: {current_cap_usd: 1.5, note: 'not enough runs'},
    orchestrator: {current_cap_usd: 1.0, note: 'not enough runs'},
    fold: {current_cap_usd: 2.0, note: 'not enough runs'},
  },
  waste: {rows: [{reason: 'hit cap', count: 1, cost_usd: 5.2}], total_cost_usd: 5.2},
  model_fit: [{model: 'opus', complexity: 3, cards: 2, accepted_cards: 1, cost_per_accepted_card_usd: 0.9, fix_round_share: 0.5}],
}));

mod.openCostsOverviewPanel();
await new Promise(r => setTimeout(r, 0));
const menu2 = mod.overlayRef().menu;
const findHeaderLabel = () => walk(menu2.sections[0].node).find(n => n.className === 'pager-label');
assert.equal(findHeaderLabel().textContent, 'cost', 'the panel opens on the cost view');

// right arrow key switches to optimisation and triggers the lazy load
press('ArrowRight');
await new Promise(r => setTimeout(r, 0));
assert.equal(findHeaderLabel().textContent, 'cost optimisation', 'ArrowRight switches views');
const optText = flatText({children: [menu2.sections[0].node]}).join(' | ');
assert.match(optText, /cap fit - worker cap \$5\.00/);
assert.match(optText, /low \| rated \| 3 \| 2 \| 1 \| \$0\.40 \| \$5\.20/);
assert.match(optText, /suggested caps/);
assert.match(optText, /worker \| \$5\.00 \| \$1\.80 \| \$2\.00 \| 1 \| \$0\.30/);
assert.match(optText, /reviewer \| \$1\.50 \| not enough runs/);
assert.match(optText, /waste - \$5\.20 total/);
assert.match(optText, /hit cap \| 1 \| \$5\.20/);
assert.match(optText, /model fit/);
assert.match(optText, /opus \| high \| 2 \| 1 \| \$0\.90 \| 50%/);

// left arrow switches back
press('ArrowLeft');
assert.equal(findHeaderLabel().textContent, 'cost', 'ArrowLeft switches back to cost');

// the two arrow buttons do the same thing as the keys
const headerNav = walk(menu2.sections[0].node).filter(n => n.className === 'pager-nav toggle');
headerNav[1].onclick();
assert.equal(findHeaderLabel().textContent, 'cost optimisation', 'the -> button switches views');
headerNav[0].onclick();
assert.equal(findHeaderLabel().textContent, 'cost', 'the <- button switches back');

// closing the panel and reopening it starts back on the cost view - no stale optimisation state
mod.openCostsOverviewPanel();
assert.equal(mod.overlayRef(), null, 'c again closes the panel');

console.log('ok');
