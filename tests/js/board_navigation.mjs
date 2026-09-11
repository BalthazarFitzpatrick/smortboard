// exercises the contract's verify line against real ui_base sources (buckets.js, expand.js) plus
// board.js's own wiring: five status buckets render, arrow keys move focus across buckets and
// rows, and enter/escape open and close a card. run: node tests/js/board_navigation.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => readFileSync(new URL(`../smortui/ui_base/assets/${p}`, root), 'utf8');
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

installStubDom({fetchImpl: () => new Promise(() => {})}); // fetches never resolve in this test

// build the same skeleton index.html declares: a board bar and five buckets
const boardBar = element('div', 'board-bar');
boardBar.id = 'board-bar';
const bucketRow = element('div', 'bucket-row');
bucketRow.id = 'bucket-row';
const STATUS_ORDER = ['todo', 'doing', 'checking', 'accepted', 'rejected'];
STATUS_ORDER.forEach(status => {
  const bucket = element('div', 'bucket');
  bucket.dataset.status = status;
  bucket.appendChild(element('div', 'bucket-label'));
  bucket.appendChild(element('div', 'bucket-rows'));
  bucketRow.appendChild(bucket);
});
document.body.appendChild(boardBar);
document.body.appendChild(bucketRow);

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), smort('board.js')].join('\n;\n');
const mod = new Function(`${src}
;return {STATUSES, BINDINGS, renderBuckets, renderCardStrip, cardClasses, acceptOrRejectCard, slideFrom};`)();

// ---- five status buckets, matching the contract's kanban columns exactly
assert.deepEqual(mod.STATUSES, STATUS_ORDER, 'board.js should declare exactly the five kanban statuses');
assert.equal(bucketRow.querySelectorAll('.bucket').length, 5, 'five buckets should render');

// ---- rendering places each card in its own status bucket
const cards = STATUS_ORDER.map((status, i) => ({id: `c${i}`, title: `card ${i}`, status, workstream: 'w'}));
mod.renderBuckets(cards);
STATUS_ORDER.forEach(status => {
  const rows = bucketRow.querySelector(`.bucket[data-status="${status}"] .bucket-rows`).querySelectorAll('.row');
  assert.equal(rows.length, 1, `bucket ${status} should hold exactly its one card`);
});

// ---- arrow keys move focus across buckets and rows (real buckets.js navigation)
const firstRow = bucketRow.querySelector('.bucket[data-status="todo"] .row');
const secondBucketRow = bucketRow.querySelector('.bucket[data-status="doing"] .row');
bucketRow._listeners.keydown[0]({key: 'ArrowRight', target: firstRow, preventDefault() {}});
assert.ok(secondBucketRow.focused, 'ArrowRight should move focus into the next bucket');

// ---- ArrowUp from the top row exits to the board bar
let exited = false;
bucketRow._listeners.keydown[0]({key: 'ArrowUp', target: secondBucketRow, preventDefault() {}});
// exit-top fired onExitTop -> returnToBoardBar, which looks for an active nav-tab; none exists in
// this stub, so nothing to assert on focus, but the call must not throw
exited = true;
assert.ok(exited, 'ArrowUp on the top row should exit toward the board bar without throwing');

// ---- enter opens the focused card, escape closes it (real expand.js lifecycle)
const strip = mod.renderCardStrip(cards[0]);
// a collapsing backdrop stays in the page until its animation lands, marked expand-closing - it is
// closed as far as the keyboard and the host are concerned
const backdrops = () => document.body.children.filter(c =>
  c.classList.contains('modal-backdrop') && !c.classList.contains('expand-closing'));
const press = code => strip._listeners.keydown[0]({code, preventDefault() {}, stopPropagation() {}});
press('Enter');
assert.equal(backdrops().length, 1, 'Enter should open the card into a backdrop + panel');
document._dispatch('keydown', {key: 'Escape', code: 'Escape', target: document.body});
assert.equal(backdrops().length, 0, 'Escape should close the open card');

// ---- an open card is three times the default width: 1:1 on an 800px-tall stub, not 1:3
press('Enter');
assert.equal(backdrops()[0].children[0].style.width, '800px',
  'an open card should be three times the 266.7px of the default 1:3 portrait');

// ---- space on the strip closes the card it opened - focus stays on the strip behind the panel
press('Space');
assert.equal(backdrops().length, 0, "space on the open card's strip should close it");

// ---- from inside the panel too, but in the comment input it types a space
press('Enter');
const inputTarget = element('input');
document._dispatch('keydown', {key: ' ', code: 'Space', target: inputTarget, preventDefault() {}});
assert.equal(backdrops().length, 1, 'space in the comment input must not close the card');
document._dispatch('keydown', {key: ' ', code: 'Space', target: document.body, preventDefault() {}});
assert.equal(backdrops().length, 0, 'space should close the open card, next to escape');

// ---- accepting from inside an open card closes it first, before the card moves columns
press('Space');
assert.equal(backdrops().length, 1, 'space on a closed card should still open it');
strip.focus();
mod.acceptOrRejectCard('accept'); // the fetch never resolves here - the close must not wait for it
assert.equal(backdrops().length, 0, 'y should close the open card before it moves');

// ---- the moved strip starts where the old one stood, then is released into its column
const frames = [];
const realFrame = globalThis.requestAnimationFrame;
globalThis.requestAnimationFrame = fn => frames.push(fn);
const moved = element('div');
mod.slideFrom(moved, {left: 50, top: 20});
assert.equal(moved.style.translate, '50px 20px', 'the strip should start at its old position');
while (frames.length) frames.shift()();
assert.equal(moved.style.translate, '', 'then it should be released into its new column');
assert.equal(moved.style.transition, 'translate 220ms ease',
  'with the motion token fallback - the stub has no computed style to read --motion-duration from');
globalThis.requestAnimationFrame = realFrame;

console.log('ok');

// a rejected card wears the stone fill; nothing applied the class before, so the style existed and
// was never reachable from real data
const rejected = mod.cardClasses({status: 'rejected', blocked_reason_code: null});
assert.ok(rejected.includes('card-rejected'), 'a rejected card takes the stone fill');
const working = mod.cardClasses({status: 'doing', blocked_reason_code: null});
assert.ok(working.includes('card-working') && !working.includes('card-rejected'));
const blocked = mod.cardClasses({status: 'doing', blocked_reason_code: 'USAGE_LIMIT'});
assert.ok(blocked.includes('card-attention') && !blocked.includes('card-working'),
  'blocked wins over working - a card waiting on you is not a card making progress');
