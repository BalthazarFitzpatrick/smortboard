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

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'),
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
  },
  orchestrator_turns_counted: false,
}));

mod.openCostsOverviewPanel();
await new Promise(r => setTimeout(r, 0));
const menu = mod.overlayRef().menu;
assert.equal(menu.title, 'cost overview');
const text = flatText({children: menu.sections.map(s => s.node || {children: []})}).join(' | ');
// one row per board, costliest first, the figures in column order under a header row
assert.equal(menu.sections.length, 1, 'a single card, no separate jump list');
assert.match(text, /board \| share \| spend \| runs \| accepted \| prs \| per pr \| on refusals/);
assert.match(text, /pricey board \| \$0\.88 \| 1 \| 0\/1 \| 0 \| - \| \$0\.88/);
assert.match(text, /cheap board \| \$0\.13 \| 1 \| 1\/1 \| 1 \| \$0\.13 \| -/);
assert.ok(text.indexOf('pricey board') < text.indexOf('cheap board'), 'costliest board leads');
assert.match(text, /mission-control turns are not counted/);
assert.match(text, /2 boards - 2 cards - 2 runs - \$1\.01/);
assert.match(text, /\$0\.88 on runs with refusals/);
assert.match(text, /worker \$0\.98 - reviewer \$0\.03 - opus \$0\.88, claude-sonnet-4 \$0\.13/);

// ---- clicking a board's name jumps to it: same switch-and-render a tab click does ---------------

const walk = (node, out = []) => { out.push(node); (node.children || []).forEach(c => walk(c, out)); return out; };
const nameCell = walk(menu.sections[0].node).find(n => n.className === 'cost-name' && n.textContent === 'pricey board');
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

console.log('ok');
