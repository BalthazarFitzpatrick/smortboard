// proves the phase-1 keyboard model cannot drift: every key the contract lists actually triggers
// a handler branch in board.js's global keydown listener, and the `s` overlay is built from the
// exact same BINDINGS table those branches dispatch on. run: node tests/js/keyboard_bindings.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
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
  constructor(opts) { this.opts = opts; this.sections = opts.sections; }
  openAt(where) { openedMenus.push({title: this.opts.title, where, menu: this}); return this; }
  // the real Menu swaps a section's rows in place - the overlay's page turns rely on this
  refresh(sections) { this.sections = sections; }
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

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), uiBase('pile.js'),
  smort('columns.js'), smort('card_panel.js'), smort('chat.js'), smort('shortcuts.js'),
  smort('board.js'), smort('telemetry.js'), smort('costs.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {BINDINGS, BINDING_GROUPS, buildDrawers, boardsRef: () => boards, overlayRows,
  setBoard: id => { currentBoardId = id; }};`)(SpyMenu, SpyDrawer);

// the contract's table, verified against what board.js actually declares
const CONTRACT_KEYS = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Enter', 'Space', 'Escape',
  'KeyG', 'KeyW', 'KeyU', 'KeyI', 'KeyC', 'KeyA', 'KeyD', 'KeyR', 'KeyK', 'KeyY', 'KeyX', 'KeyM',
  'KeyE', 'KeyJ', 'Delete',
  'KeyT', 'KeyS',
  'KeyP', 'KeyN', 'KeyV', 'KeyQ', 'KeyH', 'KeyO', 'KeyB', 'Slash', 'Comma', 'Period',
  'Digit1', 'Digit2', 'Digit3', 'Digit4', 'Digit5', 'Digit6', 'Digit7', 'Digit8', 'Digit9'];
const boundCodes = mod.BINDINGS.map(b => b.code);
CONTRACT_KEYS.forEach(code => assert.ok(boundCodes.includes(code), `${code} must be in BINDINGS`));
assert.equal(boundCodes.length, CONTRACT_KEYS.length, 'BINDINGS should declare exactly the contract keys, no more no less');

// the overlay shows the nine board keys as one row, so the panels column fits on screen
{
  const rows = mod.overlayRows(mod.BINDINGS.filter(b => b.group === 'panels'));
  const boardRows = rows.filter(r => /jump to board/.test(r.label));
  assert.equal(boardRows.length, 1, 'one overlay row for all nine board keys');
  assert.equal(boardRows[0].label, '1 .. 9 - jump to board 1 .. 9');
  assert.equal(rows.length, mod.BINDINGS.filter(b => b.group === 'panels').length - 8, 'the other rows are untouched');
}

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

// pages, not columns: one section visible at a time, one page per BINDINGS group in order
{
  const folded = code => /^Digit[2-9]$/.test(code);
  const pageIds = group => mod.BINDINGS
    .filter(b => b.group === group)
    .filter(b => !folded(b.code))
    .map(b => (b.code === 'Digit1' ? 'boards' : b.code));
  const [firstGroup, secondGroup] = mod.BINDING_GROUPS.map(([group]) => group);

  assert.equal(overlay.menu.sections.length, 1, 'the overlay shows one page at a time, not two columns');
  assert.deepEqual(overlay.menu.sections[0].items.map(i => i.id), pageIds(firstGroup), 's opens on the first BINDINGS group');

  press('ArrowRight');
  assert.deepEqual(overlay.menu.sections[0].items.map(i => i.id), pageIds(secondGroup), 'right turns to the next page');
  press('ArrowRight');
  assert.deepEqual(overlay.menu.sections[0].items.map(i => i.id), pageIds(firstGroup), 'right wraps back to the first page');

  press('ArrowLeft');
  assert.deepEqual(overlay.menu.sections[0].items.map(i => i.id), pageIds(secondGroup), 'left wraps the other way, to the last page');
  press('ArrowLeft');
  assert.deepEqual(overlay.menu.sections[0].items.map(i => i.id), pageIds(firstGroup), 'left steps back to the first page');

  // every binding lands on exactly one page - the groups partition the whole table between them
  const allPageIds = mod.BINDING_GROUPS.flatMap(([group]) => mod.BINDINGS.filter(b => b.group === group).map(b => b.code));
  assert.deepEqual(allPageIds.sort(), mod.BINDINGS.map(b => b.code).sort(),
    'every binding in BINDINGS appears on exactly one page');

  mod.BINDINGS.filter(b => b.group === firstGroup && !folded(b.code)).forEach((b, i) => {
    assert.ok(overlay.menu.sections[0].items[i].label.startsWith(b.label), `page 1 row ${i} should show binding label "${b.label}"`);
  });
}

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

// ---- cmd, ctrl and alt belong to the browser: copy, paste and reload must never fire a board key
{
  const fetchesBefore = fetchCalls.length;
  const menusBefore = openedMenus.length;
  const withMod = (code, mod) =>
    document._dispatch('keydown', {code, key: code, [mod]: true, target: strip, preventDefault() {}});
  withMod('KeyR', 'metaKey');  // reload on mac - used to run the focused card
  withMod('KeyC', 'ctrlKey');  // copy on windows - used to open the cost overview
  withMod('KeyV', 'metaKey');
  withMod('KeyY', 'metaKey');
  withMod('KeyU', 'metaKey');
  withMod('KeyS', 'altKey');
  assert.equal(fetchCalls.length, fetchesBefore, 'no modified key may reach the api');
  assert.equal(openedMenus.length, menusBefore, 'no modified key may open a panel');
  // u opens its panel whatever has focus, so it proves the bare key still acts
  press('KeyU');
  assert.equal(openedMenus.length, menusBefore + 1, 'the same key without a modifier still acts');
}

console.log('ok');
