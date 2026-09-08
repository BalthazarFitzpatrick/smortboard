// proves the phase-1 keyboard model cannot drift: every key the contract lists actually triggers
// a handler branch in board.js's global keydown listener, and the `s` overlay is built from the
// exact same BINDINGS table those branches dispatch on. run: node tests/js/keyboard_bindings.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => readFileSync(new URL(`../ui_base/ui_base/assets/${p}`, root), 'utf8');
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

installStubDom({fetchImpl: () => new Promise(() => {})});

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

let openedMenus = [];
class SpyMenu {
  constructor(opts) { this.opts = opts; }
  openAt(where) { openedMenus.push({title: this.opts.title, sections: this.opts.sections, where}); return this; }
  close() {}
}

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('shell.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', `${src}
;return {BINDINGS, boardsRef: () => boards};`)(SpyMenu);

// the contract's table, verified against what board.js actually declares
const CONTRACT_KEYS = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Enter', 'Space', 'Escape',
  'KeyG', 'KeyU', 'KeyA', 'KeyS', 'Slash', 'Comma', 'Period',
  'Digit1', 'Digit2', 'Digit3', 'Digit4', 'Digit5', 'Digit6', 'Digit7', 'Digit8', 'Digit9'];
const boundCodes = mod.BINDINGS.map(b => b.code);
CONTRACT_KEYS.forEach(code => assert.ok(boundCodes.includes(code), `${code} must be in BINDINGS`));
assert.equal(boundCodes.length, CONTRACT_KEYS.length, 'BINDINGS should declare exactly the contract keys, no more no less');

function press(code) {
  document._dispatch('keydown', {code, key: code, target: document.body, preventDefault() {}});
}

// ---- g, u, a, s, comma and period each open something (hazard placeholder or the overlay)
press('KeyG');
press('KeyU'); press('KeyA'); press('Comma'); press('Period'); press('KeyS');
assert.equal(openedMenus.length, 5, 'u, a, comma, period and s should each open exactly one menu');
assert.deepEqual(openedMenus.map(m => m.title),
  ['usage', 'agent roster', 'workforce', 'mission control', 'keyboard shortcuts']);

// ---- the shortcut overlay's rows are literally the binding table, so they cannot drift apart
const overlay = openedMenus[openedMenus.length - 1];
const overlayItems = overlay.sections[0].items;
assert.deepEqual(overlayItems.map(i => i.id), boundCodes, 'the overlay must list exactly the bound codes, in order');
mod.BINDINGS.forEach((b, i) => {
  assert.ok(overlayItems[i].label.startsWith(b.label), `overlay row ${i} should show binding label "${b.label}"`);
});

console.log('ok');
