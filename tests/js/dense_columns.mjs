// progressive column density: tier selection against measured height, the doing/attention
// aggregate order, the accordion (one expanded chip at a time) and an aggregate drawing its cards
// out one at a time. run: node tests/js/dense_columns.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => readFileSync(new URL(`../smortui/ui_base/assets/${p}`, root), 'utf8');
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

installStubDom({fetchImpl: () => new Promise(() => {})});
const boardBar = element('div', 'board-bar');
boardBar.id = 'board-bar';
const bucketRow = element('div', 'bucket-row');
bucketRow.id = 'bucket-row';
document.body.appendChild(boardBar);
document.body.appendChild(bucketRow);

class SpyMenu { constructor(opts) { this.opts = opts; } openAt() { return this; } close() {} }
function SpyDrawer() { return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false}; }

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {pickColumnPlan, buildAggregateItems, renderBucketColumn, itemsHeight};`)(SpyMenu, SpyDrawer);

function card(id, status, extra = {}) {
  return {id, title: `card ${id}`, status, workstream: '', ...extra};
}

// ---- pickColumnPlan is pure: heights in, tier + items out -------------------------------------

{
  const cards = [card(1, 'todo'), card(2, 'todo')];
  const plan = mod.pickColumnPlan(cards, 1000);
  assert.equal(plan.tier, 1, 'plenty of room stays at tier 1');
  assert.deepEqual(plan.items.map(i => i.type), ['card', 'card']);
}

{
  // ten cards: too tall for full fan cards (10*210+9*18=2262) but chips fit (10*58+9*18=742)
  const cards = Array.from({length: 10}, (_, i) => card(i, 'todo'));
  const plan = mod.pickColumnPlan(cards, 800);
  assert.equal(plan.tier, 2, 'overflowing tier 1 but fitting as chips lands on tier 2');
  assert.ok(plan.items.every(i => i.type === 'chip'));
}

{
  // thirty cards: even chips overflow (30*58+29*18=2262) against a small viewport
  const cards = Array.from({length: 30}, (_, i) => card(i, 'todo'));
  const plan = mod.pickColumnPlan(cards, 400);
  assert.equal(plan.tier, 3, 'overflowing chips too falls through to tier 3');
}

{
  // a column of plain cards has nothing for the doing or attention aggregate to hold: the chips
  // that do not fit go into one last "more" aggregate, so the whole column stays on screen
  const plain = Array.from({length: 20}, (_, i) => card(`t${i}`, 'todo'));
  const waiting = Array.from({length: 3}, (_, i) => card(`a${i}`, 'todo', {review_flag: true}));
  const plan = mod.pickColumnPlan([...plain, ...waiting], 400);
  assert.equal(plan.tier, 3);
  assert.ok(mod.itemsHeight(plan.items) <= 400, 'the column fits the height it was given');
  assert.deepEqual(plan.items.map(i => i.status || i.type), ['chip', 'chip', 'attention', 'more']);
  assert.deepEqual(plan.items.slice(0, 2).map(i => i.card.id), ['t0', 't1'], 'the first chips keep their order');
  const more = plan.items[3];
  assert.equal(more.cards.length, 18, 'every card that did not fit is in the "more" aggregate');
  assert.equal(more.cards[0].id, 't2', 'and drawn out in board order');
}

// ---- buildAggregateItems: doing first, then attention/vanilla, chips otherwise ----------------

{
  const c1 = card(1, 'todo');
  const d1 = card(2, 'doing');
  const d2 = card(3, 'doing');
  const a1 = card(4, 'checking', {review_flag: true});
  const c2 = card(5, 'todo');
  const items = mod.buildAggregateItems([c1, d1, d2, a1, c2]);
  assert.deepEqual(items.map(i => i.type), ['chip', 'aggregate', 'aggregate', 'chip'],
    'leading chip, then the doing group, then the attention group, then the trailing chip');
  assert.equal(items[1].status, 'doing');
  assert.equal(items[1].cards.length, 2, 'both doing cards fold into the one doing aggregate');
  assert.equal(items[2].status, 'attention');
  assert.equal(items[2].cards.length, 1);
  assert.equal(items[0].card, c1);
  assert.equal(items[3].card, c2);
}

{
  // no doing or attention cards at all: every card stays its own chip, no aggregate appears
  const cards = [card(1, 'todo'), card(2, 'todo')];
  const items = mod.buildAggregateItems(cards);
  assert.ok(items.every(i => i.type === 'chip'), 'an aggregate never appears with nothing to group');
}

// ---- renderBucketColumn wires tier 3 into the dom, plus the accordion and the draw-out --------

function buildColumn(availableHeight, cards) {
  const bucketRows = element('div', 'bucket-rows');
  // availableColumnHeight reads window.innerHeight (800 in the stub) minus this element's own top
  bucketRows.getBoundingClientRect = () => ({top: 800 - 24 - availableHeight, left: 0, right: 0, bottom: 0, width: 300, height: 0});
  mod.renderBucketColumn(bucketRows, cards);
  return bucketRows;
}

{
  // room for chips only (10 cards), not full cards
  const cards = Array.from({length: 10}, (_, i) => card(i, 'todo'));
  const bucketRows = buildColumn(800, cards);
  const strips = bucketRows.children.filter(c => c.className.includes('card-strip'));
  assert.equal(strips.length, 10);
  assert.ok(strips.every(s => s.className.includes('card-chip')), 'tier 2 renders every strip collapsed');

  // focusing one strip expands it (drops card-chip) and re-collapses the previous one - the
  // stub's focus() does not bubble a focusin event, so the listener is invoked directly, same as
  // board_navigation.mjs does for keydown
  const focusIn = target => bucketRows._listeners.focusin[0]({target});
  focusIn(strips[0]);
  assert.ok(!strips[0].className.includes('card-chip'), 'the focused chip fans out');
  focusIn(strips[3]);
  assert.ok(strips[3].className.includes('card-chip') === false, 'the newly focused chip fans out');
  assert.ok(strips[0].className.includes('card-chip'), 'the previously expanded chip collapses back - accordion, one at a time');
}

{
  // force tier 3: many doing cards crowd out even the chip tier
  const doing = Array.from({length: 20}, (_, i) => card(`d${i}`, 'doing'));
  const cards = [card('lead', 'todo'), ...doing];
  const bucketRows = buildColumn(200, cards);
  const rows = bucketRows.children;
  const aggregate = rows.find(r => r.className.includes('card-aggregate'));
  assert.ok(aggregate, 'tier 3 renders the doing aggregate placeholder');
  assert.equal(aggregate.querySelector('.aggregate-count').textContent, '20');

  const before = bucketRows.children.length;
  bucketRows._listeners.focusin[0]({target: aggregate}); // drawFromAggregate fires on focusin
  const after = bucketRows.children;
  assert.equal(after.length, before + 1, 'one more real card enters the column');
  assert.equal(aggregate.querySelector('.aggregate-count').textContent, '19', 'the count decrements by one');
  assert.ok(after.some(r => r.className.includes('card-chip') && !r.className.includes('card-aggregate')),
    'the drawn card is a real, focusable chip');
}

{
  // a bucket-rows node survives across renders (board switch, poll) - re-rendering it must not
  // stack a second focusin listener, or one focus event draws two cards instead of one
  const doing = Array.from({length: 5}, (_, i) => card(`d${i}`, 'doing'));
  const bucketRows = element('div', 'bucket-rows');
  bucketRows.getBoundingClientRect = () => ({top: 800 - 24 - 50, left: 0, right: 0, bottom: 0, width: 300, height: 0});
  mod.renderBucketColumn(bucketRows, doing);
  mod.renderBucketColumn(bucketRows, doing); // a second render onto the same persistent node
  assert.equal(bucketRows._listeners.focusin.length, 1, 'wiring the same node twice must not stack listeners');
  const aggregate = bucketRows.children.find(r => r.className.includes('card-aggregate'));
  bucketRows._listeners.focusin[0]({target: aggregate});
  assert.equal(aggregate.querySelector('.aggregate-count').textContent, '4', 'one focus draws exactly one card, not one per stacked listener');
}

{
  // drawing the last card out of an aggregate removes the placeholder entirely
  const doing = [card('only', 'doing')];
  const cards = [...doing];
  const bucketRows = buildColumn(0, cards); // 0 available height forces tier 3 even for one card
  const aggregate = bucketRows.children.find(r => r.className.includes('card-aggregate'));
  assert.ok(aggregate, 'a single doing card still gets its own aggregate once nothing fits');
  bucketRows._listeners.focusin[0]({target: aggregate});
  assert.ok(!bucketRows.children.some(r => r.className.includes('card-aggregate')),
    'the placeholder disappears once its last card has been drawn out');
}

console.log('ok');
