// cost telemetry (i): a focused/open card's attempts, or the board's cost table with nothing
// focused. stubs fetch and reads the rendered node tree, same pattern as mission_control.mjs.
// run: node tests/js/telemetry.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => readFileSync(new URL(`../smortui/ui_base/assets/${p}`, root), 'utf8');
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
  smort('board.js'), smort('telemetry.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {
  openTelemetryPanel, telemetryCard, costTable,
  overlayRef: () => openOverlay,
  setCurrentBoardId: id => { currentBoardId = id; },
};`)(SpyMenu, SpyDrawer);

// text content of every leaf-ish node, in document order, joined - enough to assert on without
// caring exactly which element class carries which line
function flatText(node) {
  const own = (node.textContent || '').trim();
  const kids = (node.children || []).flatMap(flatText);
  return own ? [own, ...kids] : kids;
}

// ---- card telemetry: focused card drives it, ruled sections per attempt -------------------------

const strip = element('div', 'row card card-strip');
strip.dataset.cardId = 'c1';
strip.tabIndex = -1;
bucketRow.appendChild(strip);
strip.focus();

responses.set('/api/cards/c1/telemetry', stubJson(200, {
  card_id: 'c1', title: 'a card', model: 'sonnet',
  attempts: [
    {
      started_at: '2026-09-11T10:00:00Z', outcome: 'pull request',
      worker_model: 'claude-sonnet-4', reviewer_model: 'claude-sonnet-4',
      worker_cost_usd: 0.10, reviewer_cost_usd: 0.03, cost_usd: 0.13,
      worker_turns: 4, reviewer_turns: 2, turns: 6, fix_rounds: 0,
      refusal_count: 0, refusals: [],
    },
  ],
  totals: {cost_usd: 0.13, attempts: 1, turns: 6, fix_rounds: 0, refusal_count: 0, refusal_cost_usd: 0},
}));

mod.openTelemetryPanel();
await new Promise(r => setTimeout(r, 0));
const menu = mod.overlayRef().menu;
assert.equal(menu.title, 'telemetry: a card');
const cardText = flatText({children: menu.sections.map(s => s.node)}).join(' | ');
assert.match(cardText, /\$0\.13/);
assert.match(cardText, /pull request/);
assert.match(cardText, /1 attempts? - \$0\.13/);

// ---- refusals list and the waste line -----------------------------------------------------------

const wasteData = {
  card_id: 'c1', title: 'expensive card', model: null,
  attempts: [
    {
      started_at: '2026-09-11T11:00:00Z', outcome: 'blocked: AGENT_QUESTION',
      worker_model: 'claude-opus-4', reviewer_model: null,
      worker_cost_usd: 0.88, reviewer_cost_usd: 0, cost_usd: 0.88,
      worker_turns: 10, reviewer_turns: 0, turns: 10, fix_rounds: 0,
      refusal_count: 2,
      refusals: [{tool: 'Bash', target: 'uv run ruff .'}, {tool: 'Bash', target: 'git push'}],
    },
  ],
  totals: {cost_usd: 0.88, attempts: 1, turns: 10, fix_rounds: 0, refusal_count: 2, refusal_cost_usd: 0.88},
};
const node = mod.telemetryCard(wasteData);
const wasteText = flatText(node).join(' | ');
assert.match(wasteText, /2 refusals/);
assert.match(wasteText, /uv run ruff \./);
assert.match(wasteText, /git push/);
assert.match(wasteText, /\$0\.88 on runs with refusals/);

// ---- no attempts yet reads as a placeholder, not an empty card ----------------------------------

const emptyNode = mod.telemetryCard({attempts: [], totals: {cost_usd: 0, attempts: 0}});
assert.match(emptyNode.children[0].innerHTML, /has not run yet/);

// ---- with nothing focused and no open card, i shows the board's cost table ----------------------

mod.openTelemetryPanel(); // closes the card panel just opened - same key toggles it shut
assert.equal(mod.overlayRef(), null);

strip.blur();
document.activeElement = document.body;
mod.setCurrentBoardId('b1');
responses.set('/api/boards/b1/costs', stubJson(200, [
  {card_id: 'c2', title: 'pricey card', model: 'opus', cost_usd: 0.88, attempts: 1,
    refusal_count: 1, refusal_cost_usd: 0.88, last_outcome: 'blocked: AGENT_QUESTION'},
  {card_id: 'c3', title: 'cheap card', model: 'haiku', cost_usd: 0.02, attempts: 1,
    refusal_count: 0, refusal_cost_usd: 0, last_outcome: 'pull request'},
]));
mod.openTelemetryPanel();
await new Promise(r => setTimeout(r, 0));
const boardMenu = mod.overlayRef().menu;
assert.equal(boardMenu.title, 'costs');
const boardText = flatText({children: boardMenu.sections.map(s => s.node)}).join(' | ');
assert.match(boardText, /pricey card/);
assert.match(boardText, /cheap card/);
assert.match(boardText, /2 cards - \$0\.90/);
assert.match(boardText, /\$0\.88 on runs with refusals/);

console.log('ok');
