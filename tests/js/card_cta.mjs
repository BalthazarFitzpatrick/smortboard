// proves ctaFor's label/action logic, and the compact action note 3919ce56 replaced the oversized
// full-width CTA button with: one per card, coloured by state, left-aligned ahead of the
// workstream, no click handler of its own.
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

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), uiBase('pile.js'), smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {renderCardStrip, ctaFor};`)(SpyMenu, SpyDrawer);

// ---- the label tracks status, one state at a time
assert.equal(mod.ctaFor({status: 'todo'}).label, 'run');
assert.equal(mod.ctaFor({status: 'doing'}).label, 'running…');
assert.equal(mod.ctaFor({status: 'checking'}).label, 'review pr');
assert.equal(mod.ctaFor({status: 'accepted'}).label, 'view');
assert.equal(mod.ctaFor({status: 'rejected'}).label, 'rerun');

// ---- a blocked card's label names the fix, not the bare reason code
assert.equal(mod.ctaFor({status: 'doing', blocked_reason_code: 'LEASE_CONFLICT'}).label, 'fix leases');
assert.equal(mod.ctaFor({status: 'doing', blocked_reason_code: 'AGENT_QUESTION'}).label, 'answer question');
assert.equal(mod.ctaFor({status: 'doing', blocked_reason_code: 'USAGE_LIMIT'}).label, 'resume run');
assert.equal(mod.ctaFor({status: 'doing', blocked_reason_code: 'CRASH'}).label, 'investigate crash');
assert.equal(mod.ctaFor({status: 'checking', blocked_reason_code: 'TESTS_FAILED'}).label, 'review failure');
assert.equal(mod.ctaFor({status: 'checking', blocked_reason_code: 'REVIEW_REJECTED'}).label, 'review findings');
assert.equal(mod.ctaFor({status: 'doing', blocked_reason_code: 'DEPENDENCY_REJECTED'}).label, 'review dependency');
// an unlisted code still says something, rather than leaking the raw code onto the button
assert.equal(mod.ctaFor({status: 'doing', blocked_reason_code: 'SOMETHING_NEW'}).label, 'needs attention');
// blocked wins over status, the same precedence cardClasses already gives the card's own border
assert.equal(mod.ctaFor({status: 'checking', blocked_reason_code: 'LEASE_CONFLICT'}).label, 'fix leases');

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

// ---- the strip renders exactly one compact action note, left of the workstream, no separate
// full-width button (3919ce56 reverted d66ceee3's CTA). the note is templated into innerHTML, which
// the stub keeps as a string only (see its own note further down), so these check the markup
// directly rather than a live node
const strip = mod.renderCardStrip({id: 'c1', title: 't', status: 'todo', workstream: 'w'});
assert.equal(strip.querySelectorAll('.card-cta').length, 0, 'the oversized CTA button is gone');
const footIdx = strip.innerHTML.indexOf('card-foot');
const actionIdx = strip.innerHTML.indexOf('card-action', footIdx);
const workstreamIdx = strip.innerHTML.indexOf('card-workstream', footIdx);
assert.ok(actionIdx > 0 && actionIdx < workstreamIdx, 'the action note sits left of the workstream');
assert.match(strip.innerHTML, /<span class="card-action card-action-quiet"[^>]*>run<\/span>/);

// ---- a blocked card's note is visibly distinct from a quiet one, in the attention colour
const blockedStrip = mod.renderCardStrip({id: 'c2', title: 't', status: 'doing', blocked_reason_code: 'LEASE_CONFLICT'});
assert.match(blockedStrip.innerHTML, /<span class="card-action card-action-attention"[^>]*>fix leases<\/span>/);

// ---- a running card's note wears the working colour, same as the card's own border
const runningStrip = mod.renderCardStrip({id: 'c3', title: 't', status: 'doing'});
assert.match(runningStrip.innerHTML, /<span class="card-action card-action-working"[^>]*>running…<\/span>/);

// ---- checking and accepted borrow their own state colours
const checkingStrip = mod.renderCardStrip({id: 'c4', title: 't', status: 'checking'});
assert.match(checkingStrip.innerHTML, /card-action-review"[^>]*>review pr</);
const acceptedStrip = mod.renderCardStrip({id: 'c5', title: 't', status: 'accepted'});
assert.match(acceptedStrip.innerHTML, /card-action-accepted"[^>]*>view</);

// ---- handled_by_board wins over card-attention on the strip's own border class, and its retry
// text appears exactly once (on the note, not repeated anywhere else on the footer)
const handledStrip = mod.renderCardStrip({
  id: 'c6', title: 't', status: 'doing', blocked_reason_code: 'MERGE_CONFLICT',
  handled_by_board: true, next: 'resuming automatically',
});
assert.ok(!handledStrip.className.includes('card-attention'), 'a self-handled card does not glow');
assert.ok(handledStrip.className.includes('card-working'), 'it reads as working instead');
assert.match(handledStrip.innerHTML, /card-action-working"[^>]*>resuming automatically</);
assert.equal(
  (handledStrip.innerHTML.match(/resuming automatically/g) || []).length, 1,
  'the retry text is drawn once, not repeated on the footer',
);

console.log('ok');
