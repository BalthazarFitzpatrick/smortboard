// THE KEYBOARD MODEL, SURFACE BY SURFACE, not once globally: space opens a thing and escape goes
// exactly one level back; / enters the one visible text field and never guesses between several;
// a panel standing over the board owns the keys that would otherwise act on the card behind it;
// and a confirmation opens on yes when the move is reversible, on no when it spends or destroys.
// the expectations here come from the operator's model, not from what the code happened to do.
// run: node tests/js/keyboard_model.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const responses = new Map();
const calls = [];
function stubJson(status, body) {
  return {ok: status >= 200 && status < 300, status, json: async () => body};
}
function fetchStub(path, opts) {
  calls.push({path, opts});
  const resp = responses.get(path);
  return Promise.resolve(resp || stubJson(404, {error: 'no stub for ' + path}));
}

installStubDom({fetchImpl: fetchStub});
responses.set('/api/boards', stubJson(200, []));
responses.set('/api/settings', stubJson(200, {}));
responses.set('/api/prompts', stubJson(200, [{role: 'orchestrator', body: 'a prompt', version: 1, is_default: true}]));
responses.set('/api/boards/b1/fold', stubJson(200, {running: true}));

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

function SpyDrawer() {
  return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false};
}

// THE REAL Menu, not a spy: which button a confirm opens on, and whether a menu counts as a
// surface over the board, are both properties of the panel it actually builds
const src = [uiBase('menu.js'), uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'),
  uiBase('shell.js'), uiBase('pile.js'), smort('columns.js'), smort('card_panel.js'),
  smort('chat.js'), smort('shortcuts.js'), smort('board.js'), smort('settings.js'),
  smort('boards.js')].join('\n;\n');
const mod = new Function('makeDrawer', `${src}
;return {renderCardStrip, st, bp, pe, BINDINGS, surfaceOverBoard, topSurface, surfaceFields,
  setMouseEnabled, mouseAffordances, focusFirstCardSection, wireCommentInput,
  setBoard: id => { currentBoardId = id; },
  setOpenCard: value => { openCard = value; },
  openCardRef: () => openCard};`)(SpyDrawer);

const flush = () => new Promise(r => setTimeout(r, 0));

// the stub has no event propagation: a document key is dispatched to the document listeners, an
// element's own listener is called directly - the two halves a browser does for one press
function press(code, key) {
  document._dispatch('keydown', {code, key: key ?? code, target: document.activeElement || document.body,
    preventDefault() {}, stopPropagation() {}});
}
function fireKeydown(el, evt) {
  const full = {target: el, preventDefault() {}, stopPropagation() {}, ...evt};
  (el._listeners.keydown || []).forEach(fn => fn(full));
}
// a backdrop on its way out is still in the dom for a few hundred ms - it is not an open surface
function liveBackdrops() {
  return document.body.querySelectorAll('.modal-backdrop')
    .filter(el => !el.classList.contains('expand-closing'));
}
function menuPanel() {
  return document.body.querySelector('.menu-panel');
}
function menuTitle() {
  return menuPanel()?.querySelector('.popup-title')?.textContent || null;
}
function focusedMenuId() {
  return document.activeElement?.dataset?.id ?? null;
}

const CARD = {id: 'c1', title: 'a card', description: 'do the thing', status: 'doing',
  tasks: [], criteria: [], comments: [], leases: []};
const strip = mod.renderCardStrip(CARD);
bucketRow.querySelector('.bucket[data-status="doing"] .bucket-rows').appendChild(strip);
strip.tabIndex = 0;
strip.focus();

// ---- what the board binds: f folds, m is the card's menu, and four keys retired into it ---------
assert.ok(mod.BINDINGS.some(b => b.code === 'KeyF' && b.group === 'cards'), 'f folds, from the cards group');
assert.ok(mod.BINDINGS.some(b => b.code === 'KeyM' && b.group === 'cards'), "m opens the focused card's menu");
['KeyE', 'KeyJ', 'Delete', 'KeyL'].forEach(code => {
  assert.ok(!mod.BINDINGS.some(b => b.code === code), `${code} is no longer a binding - it lives in the m menu`);
});

// ---- the board: / has nothing to type into, so it focuses nothing -------------------------------
press('Slash', '/');
assert.equal(document.activeElement, strip, '/ with no text field visible leaves focus where it is');

// space opening a card, and escape closing it, is board_navigation.mjs's ground - the dom stub
// does not parse the panel's innerHTML back into a tree, so a card opened HERE would have no
// comment input to wire. what this file owns is the same two keys while a panel stands over it

// ---- a panel over the board owns space and enter: neither may open the card behind it ------------
strip.focus();
press('KeyO');
await flush();
assert.ok(mod.st.backdrop.parentNode, 'o opens settings');
assert.equal(document.activeElement, mod.st.panel, 'the panel takes the keyboard, not the board behind it');
assert.equal(mod.surfaceOverBoard(), true, 'settings counts as a surface over the board');
fireKeydown(strip, {code: 'Space'});
press('Space');
fireKeydown(strip, {code: 'Enter'});
press('Enter');
assert.equal(mod.openCardRef(), null, 'space and enter must not open the card behind an open panel');
assert.equal(liveBackdrops().length, 1, 'only the panel is open');

// ---- settings has many fields: the cursor picks one, / never guesses ----------------------------
const settingsFields = mod.surfaceFields(mod.st.panel);
assert.ok(settingsFields.length > 1, 'settings is the many-fields case the model describes');
press('Slash', '/');
assert.equal(document.activeElement, mod.st.panel, '/ does not guess between several fields');
press('ArrowDown');
assert.equal(document.activeElement, settingsFields[0], 'the cursor steps into the first field');
press('ArrowDown');
assert.equal(document.activeElement, settingsFields[1], 'and on to the next one');

// ---- escape in settings is one level: field -> panel -> closed, and the field still saves --------
fireKeydown(settingsFields[1], {code: 'Escape'});
assert.ok(mod.st.backdrop.parentNode, 'escape from a field must not close the panel');
assert.equal(document.activeElement, mod.st.panel, 'escape steps out onto the panel');
press('Escape');
assert.equal(mod.st.backdrop.parentNode, null, 'a second escape closes settings');

// ---- / with a panel over an open card never reaches the card's own comment box ------------------
const commentInput = element('input', 'comment-input text-field');
mod.setOpenCard({cardId: 'c1', input: commentInput, expander: {close() {}}});
press('KeyO');
await flush();
press('Slash', '/');
assert.notEqual(document.activeElement, commentInput, '/ must not reach a field under a panel');
press('Escape');
assert.equal(mod.st.backdrop.parentNode, null, 'settings is closed again');
press('Slash', '/');
assert.equal(document.activeElement, commentInput, 'with only the card open, / types its comment');
mod.setOpenCard(null);

// ---- the prompt editor: one field, so / enters it - and it never opens in typing mode -----------
strip.focus();
press('KeyP');
await flush();
assert.equal(document.activeElement, mod.pe.panel, 'the prompt editor opens on the panel, not in the textarea');
press('Slash', '/');
assert.equal(document.activeElement, mod.pe.textarea, '/ enters its one visible field');
fireKeydown(mod.pe.textarea, {code: 'Escape'});
assert.ok(mod.pe.backdrop.parentNode, 'escape from the textarea keeps the panel open');
assert.equal(document.activeElement, mod.pe.panel, 'it steps out onto the panel');
press('Escape');
assert.equal(mod.pe.backdrop.parentNode, null, 'a second escape closes the prompt editor');

// ---- boards and repos opens on the panel too, never straight into the name field ----------------
strip.focus();
press('KeyB');
await flush();
assert.equal(document.activeElement, mod.bp.panel, 'b opens on the panel, not in the name box');
press('Escape');
assert.equal(mod.bp.backdrop.parentNode, null, 'escape closes it');

// ---- a confirmation opens on the safe button: no for what spends, yes for what a menu can undo ---
strip.focus();
press('KeyR');
await flush();
assert.equal(menuTitle(), 'run this card?', 'r asks first');
assert.equal(focusedMenuId(), 'cancel', 'running spends money, so the confirm opens on cancel');
press('Escape');
await flush();

strip.focus();
press('KeyY');
await flush();
assert.equal(menuTitle(), 'accept this card?', 'y asks first');
assert.equal(focusedMenuId(), 'confirm', 'a status a menu can move back opens on the yes');
press('Escape');
await flush();

// ---- e, j and del do nothing now: the menu is the route, and it is two presses deep ------------
strip.focus();
press('KeyE');
press('KeyJ');
press('Delete');
assert.equal(menuPanel(), null, 'e, j and del are retired - no surface opens');
assert.equal(liveBackdrops().length, 0, 'and e does not open the card any more');

// ---- m opens the card's own menu on the focused card, and escape hands the card back -----------
strip.focus();
press('KeyM');
await flush();
assert.equal(menuTitle(), 'card actions', "m opens the focused card's menu");
const items = menuPanel().querySelectorAll('.menu-item').map(el => el.dataset.id);
assert.equal(items[0], 'edit', 'edit reads first');
assert.ok(items.includes('status'), 'and move to is one of its rows');
assert.ok(items.includes('delete'), 'delete is reachable only here now');

// delete from the menu still asks, and still opens on the safe button
menuPanel().querySelector('.menu-item[data-id="delete"]').onclick();
await flush();
assert.equal(menuTitle(), 'delete this card?', 'delete from the menu asks first');
assert.equal(focusedMenuId(), 'keep', 'deleting opens on keep it');
press('Escape');
await flush();

// move to is the menu's own submenu, and escape from it goes back to the menu, not to the board
strip.focus();
press('KeyM');
await flush();
menuPanel().querySelector('.menu-item[data-id="status"]').onclick();
await flush();
assert.equal(menuTitle(), 'move to', 'move to opens as a submenu');
press('Escape');
await flush();
assert.equal(menuTitle(), 'card actions', 'escape from the submenu is one level back, to the menu');
press('Escape');
await flush();
assert.equal(menuPanel(), null, 'a second escape closes the menu');
assert.equal(document.activeElement, strip, 'and focus lands back on the card it acted on');

// ---- f asks before folding, opens on no, and y from the question never accepts the card ---------
mod.setBoard('b1');
strip.focus();
press('KeyF');
await flush();
assert.equal(menuTitle(), "fold this board's cards?", 'f asks first');
assert.equal(focusedMenuId(), 'no', 'a fold deletes cards, so the question opens on no');
const acceptsBefore = calls.filter(c => c.path === '/api/cards/c1/accept').length;
press('KeyY', 'y');
await flush();
assert.ok(calls.some(c => c.path === '/api/boards/b1/fold' && c.opts?.method === 'POST'),
  'y answers the fold question by posting the fold');
assert.equal(calls.filter(c => c.path === '/api/cards/c1/accept').length, acceptsBefore,
  'y answered the question - it never also accepted the focused card');

// ---- the mouse is an option, off by default: with it off the pointer changes nothing -----------
const settle = ms => new Promise(r => setTimeout(r, ms));
function fireMouse(el, type, evt = {}) {
  const full = {target: el, preventDefault() {}, stopPropagation() {}, ...evt};
  (el._listeners[type] || []).forEach(fn => fn(full));
}

const other = mod.renderCardStrip({...CARD, id: 'c2', title: 'another card'});
bucketRow.querySelector('.bucket[data-status="todo"] .bucket-rows').appendChild(other);
strip.focus();
assert.equal(mod.mouseAffordances(), false, 'the mouse is off until the setting turns it on');
fireMouse(other, 'mouseenter');
await settle(90);
assert.equal(document.activeElement, strip, 'with the mouse off, hovering focuses nothing');
let prevented = false;
fireMouse(other, 'contextmenu', {clientX: 20, clientY: 30, preventDefault() { prevented = true; }});
assert.equal(menuPanel(), null, "with the mouse off, right-click stays the browser's own");
assert.equal(prevented, false, 'and the browser menu is not suppressed');

// ---- with it on, hover focuses what the arrows would and right-click opens the m menu ----------
mod.setMouseEnabled('on');
fireMouse(other, 'mouseenter');
await settle(90);
assert.equal(document.activeElement, other, 'hover focuses the card under the pointer');
fireMouse(other, 'mouseenter');
await settle(90);
assert.equal(document.activeElement, other, 'hovering the card already focused changes nothing');

prevented = false;
fireMouse(other, 'contextmenu', {clientX: 20, clientY: 30, preventDefault() { prevented = true; }});
await flush();
assert.equal(menuTitle(), 'card actions', 'right-click opens the same menu m does');
assert.ok(prevented, 'and suppresses the browser menu on a card');
press('Escape');
await flush();

// ---- a hover never steals focus from a panel standing over the board ---------------------------
strip.focus();
press('KeyO');
await flush();
fireMouse(other, 'mouseenter');
await settle(90);
assert.equal(document.activeElement, mod.st.panel, 'an open panel keeps the keyboard through a hover');
press('Escape');
await flush();

// ---- nor from a field someone is typing in -----------------------------------------------------
press('KeyP');
await flush();
press('Slash', '/');
assert.equal(document.activeElement, mod.pe.textarea, 'the prompt editor field has the caret');
fireMouse(other, 'mouseenter');
await settle(90);
assert.equal(document.activeElement, mod.pe.textarea, 'a hover never takes the caret out of a field');
fireKeydown(mod.pe.textarea, {code: 'Escape'});
press('Escape');
mod.setMouseEnabled(false);

// ---- the open card is a surface of its own: sections are what the cursor walks, and / lands in
// its one comment box, in view and ready to type ------------------------------------------------
{
  const backdrop = element('div', 'modal-backdrop expand-backdrop');
  const panel = element('div', 'panel-floating expand-panel card-panel');
  const sections = element('div', 'card-sections');
  const rows = ['title', 'status', 'comments'].map(name => {
    const section = element('div', 'card-section');
    section.dataset.section = name;
    sections.appendChild(section);
    return section;
  });
  const comment = element('input', 'comment-input text-field');
  rows[2].appendChild(comment);
  panel.appendChild(sections);
  backdrop.appendChild(panel);
  document.body.appendChild(backdrop);

  // the card takes the keyboard when it renders, so up and down have somewhere to move from
  mod.focusFirstCardSection(panel);
  assert.equal(document.activeElement, rows[0], 'an open card focuses its first section');

  // one text entry, so / does not guess - it goes there and brings it into view, however far down
  // the panel it sits
  let scrolledTo = null;
  comment.scrollIntoView = opts => { scrolledTo = opts; };
  press('Slash', '/');
  assert.equal(document.activeElement, comment, "/ puts the caret in the card's comment box");
  assert.deepEqual(scrolledTo, {block: 'nearest'}, 'and scrolls it into view, ready to type');

  // arrows inside the box belong to the box; escape steps one level back, onto the card
  mod.wireCommentInput(comment, panel, 'c1');
  let stopped = false;
  fireKeydown(comment, {code: 'ArrowDown', stopPropagation() { stopped = true; }});
  assert.ok(stopped, 'down inside the box never reaches the section nav');
  assert.equal(document.activeElement, comment, 'and the caret stays in the box');
  fireKeydown(comment, {code: 'Escape'});
  assert.equal(document.activeElement, rows[0], 'escape from the box steps back onto the card');

  backdrop.remove();
}

console.log('ok');
