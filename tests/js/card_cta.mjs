// proves the primary call-to-action button on each card strip: exactly one per card, a label that
// tracks status and blocked reason code, a distinct highlight when the card is waiting on the
// operator, and clicks that act on that card rather than whatever happens to be focused.
// run: node tests/js/card_cta.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const calls = [];
function fetchStub(path, opts) {
  calls.push({path, opts});
  return new Promise(() => {}); // the click handlers under test never need a real response
}

installStubDom({fetchImpl: fetchStub});

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

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {renderCardStrip, ctaFor};`)(SpyMenu, SpyDrawer);

// ---- the label tracks status, one state at a time
assert.equal(mod.ctaFor({status: 'todo'}).label, 'Run');
assert.equal(mod.ctaFor({status: 'doing'}).label, 'Running…');
assert.equal(mod.ctaFor({status: 'checking'}).label, 'Review PR');
assert.equal(mod.ctaFor({status: 'accepted'}).label, 'View');
assert.equal(mod.ctaFor({status: 'rejected'}).label, 'Rerun');

// ---- a blocked card's label names the fix, not the bare reason code
assert.equal(mod.ctaFor({status: 'doing', blocked_reason_code: 'LEASE_CONFLICT'}).label, 'Fix leases');
assert.equal(mod.ctaFor({status: 'doing', blocked_reason_code: 'AGENT_QUESTION'}).label, 'Answer question');
assert.equal(mod.ctaFor({status: 'doing', blocked_reason_code: 'USAGE_LIMIT'}).label, 'Resume run');
assert.equal(mod.ctaFor({status: 'doing', blocked_reason_code: 'CRASH'}).label, 'Investigate crash');
assert.equal(mod.ctaFor({status: 'checking', blocked_reason_code: 'TESTS_FAILED'}).label, 'Review failure');
assert.equal(mod.ctaFor({status: 'checking', blocked_reason_code: 'REVIEW_REJECTED'}).label, 'Review findings');
assert.equal(mod.ctaFor({status: 'doing', blocked_reason_code: 'DEPENDENCY_REJECTED'}).label, 'Review dependency');
// an unlisted code still says something, rather than leaking the raw code onto the button
assert.equal(mod.ctaFor({status: 'doing', blocked_reason_code: 'SOMETHING_NEW'}).label, 'Needs attention');
// blocked wins over status, the same precedence cardClasses already gives the card's own border
assert.equal(mod.ctaFor({status: 'checking', blocked_reason_code: 'LEASE_CONFLICT'}).label, 'Fix leases');

// ---- blocked and flagged cards carry the attention flag; a quiet card does not
assert.ok(mod.ctaFor({status: 'doing', blocked_reason_code: 'LEASE_CONFLICT'}).attention);
assert.ok(mod.ctaFor({status: 'todo', review_flag: 1}).attention);
assert.ok(!mod.ctaFor({status: 'todo'}).attention);
assert.ok(!mod.ctaFor({status: 'todo', review_flag: 0}).attention);

// ---- a card the board is retrying itself shows the retry time, not a reason code or a glow
assert.equal(
  mod.ctaFor({status: 'doing', blocked_reason_code: 'API_UNREACHABLE', handled_by_board: true, next: 'retry at 21:40 UTC'}).label,
  'retry at 21:40 UTC',
);
assert.ok(!mod.ctaFor({status: 'doing', blocked_reason_code: 'API_UNREACHABLE', handled_by_board: true, next: 'retry at 21:40 UTC'}).attention);

// ---- the strip renders exactly one CTA, distinct from the status/workstream footer
const strip = mod.renderCardStrip({id: 'c1', title: 't', status: 'todo', workstream: 'w'});
const ctas = strip.querySelectorAll('.card-cta');
assert.equal(ctas.length, 1, 'a card shows exactly one primary action');
assert.equal(ctas[0].textContent, 'Run');
assert.ok(!ctas[0].classList.contains('card-cta-attention'), 'a quiet card CTA carries no attention styling');

// ---- a blocked card's CTA is visibly distinct from a quiet one
const blockedStrip = mod.renderCardStrip({id: 'c2', title: 't', status: 'doing', blocked_reason_code: 'LEASE_CONFLICT'});
const blockedCta = blockedStrip.querySelector('.card-cta');
assert.equal(blockedCta.textContent, 'Fix leases');
assert.ok(blockedCta.classList.contains('card-cta-attention'), 'a blocked card highlights its CTA');

// ---- clicking a todo card's CTA runs that card, regardless of what is focused, and stops the
// click from also reaching the strip's own click handling (which would otherwise pop it open too)
let stopped = false;
ctas[0]._listeners.click[0]({stopPropagation() { stopped = true; }});
assert.ok(calls.some(c => c.path === '/api/runtime'), "a todo card's CTA checks runtime readiness before running");
assert.ok(stopped, "the CTA's click must stop before it reaches the strip");

// ---- clicking a checking card's CTA opens the card so its outcome and PR can be read
const checkingStrip = mod.renderCardStrip({id: 'c3', title: 't', status: 'checking'});
let opened = false;
checkingStrip._expander = {open: () => { opened = true; }, close() {}};
checkingStrip.querySelector('.card-cta')._listeners.click[0]({stopPropagation() {}});
assert.ok(opened, "a checking card's CTA opens the panel rather than deciding for the operator");

// ---- handled_by_board wins over card-attention on the strip's own border class
const handledStrip = mod.renderCardStrip({
  id: 'c4', title: 't', status: 'doing', blocked_reason_code: 'MERGE_CONFLICT',
  handled_by_board: true, next: 'resuming automatically',
});
assert.ok(!handledStrip.className.includes('card-attention'), 'a self-handled card does not glow');
assert.ok(handledStrip.className.includes('card-working'), 'it reads as working instead');
assert.equal(handledStrip.querySelector('.card-cta').textContent, 'resuming automatically');
// the retry text lives on the CTA alone - the footer falls back to the bare status rather than
// repeating it. the stat span is templated into innerHTML, which the stub keeps as a string only
// (see its own note further down), so this checks the markup directly rather than a live node
assert.match(handledStrip.innerHTML, /<span class="stat"[^>]*>doing<\/span>/);
assert.doesNotMatch(handledStrip.innerHTML, /resuming automatically/);

console.log('ok');
