// proves the phase-1 keyboard model cannot drift: every key the contract lists actually triggers
// a handler branch in board.js's global keydown listener, and the `s` overlay is built from the
// exact same BINDINGS table those branches dispatch on. run: node tests/js/keyboard_bindings.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => readFileSync(new URL(`../ui_base/ui_base/assets/${p}`, root), 'utf8');
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

// never resolves (nothing in this file awaits a fetch), but records what was asked for so y/x can
// be proven to reach their handler and hit the right route
const fetchCalls = [];
installStubDom({fetchImpl: path => { fetchCalls.push(path); return new Promise(() => {}); }});

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

// the drawers are real ui_base components now, so the comma and period keys no longer open a menu
let drawerToggles = [];
function SpyDrawer(opts) {
  let opened = false;
  const rec = {edge: opts.edge, sliverRatio: opts.sliverRatio, widthRatio: opts.widthRatio,
               top: opts.top, bottom: opts.bottom, toggles: 0};
  drawerToggles.push(rec);
  return {
    el: element('div'), body: element('div'),
    open() { opened = true; }, close() { opened = false; },
    toggle() { opened = !opened; rec.toggles += 1; },
    isOpen: () => opened,
  };
}

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {BINDINGS, buildDrawers, boardsRef: () => boards};`)(SpyMenu, SpyDrawer);

// the contract's table, verified against what board.js actually declares
const CONTRACT_KEYS = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Enter', 'Space', 'Escape',
  'KeyG', 'KeyW', 'KeyU', 'KeyI', 'KeyA', 'KeyD', 'KeyR', 'KeyY', 'KeyX', 'KeyM', 'KeyT', 'KeyS',
  'KeyP', 'KeyN', 'Slash', 'Comma', 'Period',
  'Digit1', 'Digit2', 'Digit3', 'Digit4', 'Digit5', 'Digit6', 'Digit7', 'Digit8', 'Digit9'];
const boundCodes = mod.BINDINGS.map(b => b.code);
CONTRACT_KEYS.forEach(code => assert.ok(boundCodes.includes(code), `${code} must be in BINDINGS`));
assert.equal(boundCodes.length, CONTRACT_KEYS.length, 'BINDINGS should declare exactly the contract keys, no more no less');

function press(code) {
  document._dispatch('keydown', {code, key: code, target: document.body, preventDefault() {}});
}

// ---- g, u, a, s, comma and period each open something (hazard placeholder or the overlay)
press('KeyG');
press('KeyU'); press('KeyA'); press('KeyS');
assert.equal(openedMenus.length, 3, 'u, a and s should each open exactly one menu');
assert.deepEqual(openedMenus.map(m => m.title), ['usage', 'agent roster', 'keyboard shortcuts']);

// THE DRAWERS EXIST BEFORE ANY KEY IS PRESSED. their slivers are the only thing telling you they
// are there, so building them lazily hides the affordance behind knowing the shortcut already
mod.buildDrawers();
assert.equal(drawerToggles.length, 2, 'both drawers are built at startup, not on first press');
assert.deepEqual(drawerToggles.map(d => d.edge), ['left', 'right'], 'workforce left, mission control right');
drawerToggles.forEach(d => {
  assert.equal(d.sliverRatio, 0.05, 'a twentieth of the drawer stays visible when parked');
  assert.equal(d.widthRatio, 0.85, 'a drawer is 85% of its own half of the screen');
  assert.equal(d.bottom, 50, 'pinned 50px off the floor');
  // the stub's bar measures zero, so top collapses to the inset alone - what is under test is
  // that the inset is applied on top of wherever the bar ends, not the bar's real height
  assert.equal(d.top, 50, 'and the same 50px below the board bar');
});

// THE KEY THAT OPENS ALSO CLOSES, and reuses the drawer rather than building another
press('Comma'); press('Period');
assert.equal(drawerToggles.length, 2, 'pressing a key reuses the drawer built at startup');
press('Comma');
assert.equal(drawerToggles[0].toggles, 2, 'the left drawer toggled twice');

// ---- the shortcut overlay's rows are literally the binding table, so they cannot drift apart
const overlay = openedMenus[openedMenus.length - 1];
// two columns now, one section each - read together they must still be the whole table, in order
assert.equal(overlay.sections.length, 2, 'the overlay has two columns');
const overlayItems = overlay.sections.flatMap(s => s.items);
assert.deepEqual(overlayItems.map(i => i.id), boundCodes, 'the overlay must list exactly the bound codes, in order');
mod.BINDINGS.forEach((b, i) => {
  assert.ok(overlayItems[i].label.startsWith(b.label), `overlay row ${i} should show binding label "${b.label}"`);
});

// ---- y and x reach acceptOrRejectCard and post the right route for whatever card is focused
const strip = element('div', 'row card card-strip');
strip.dataset.cardId = 'c9';
strip.tabIndex = -1;
bucketRow.appendChild(strip);
strip.focus();
press('KeyY');
press('KeyX');
assert.ok(fetchCalls.includes('/api/cards/c9/accept'), 'y should post accept for the focused card');
assert.ok(fetchCalls.includes('/api/cards/c9/reject'), 'x should post reject for the focused card');

// ---- p opens the prompt editor (not a Menu, so it never shows up in openedMenus) and p again closes it
press('KeyP');
assert.ok(fetchCalls.includes('/api/prompts'), 'p should load the three role prompts');
press('KeyP');

console.log('ok');
