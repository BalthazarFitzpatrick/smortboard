// settings panel (o): the top-right button and the o key open it, esc and an outside click close
// it, and it renders as an empty, extensible section list until a preference card adds to it.
// run: node tests/js/settings.mjs
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
class SpyMenu { constructor(opts) { this.opts = opts; } openAt() { return this; } refresh() {} close() {} }

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'),
  smort('board.js'), smort('settings.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {toggleSettingsPanel, openSettingsPanel, closeSettingsPanel, st, BINDINGS,
  buttonRef: () => document.querySelector('.settings-button')};`)(SpyMenu, SpyDrawer);

function press(code, target) {
  document._dispatch('keydown', {code, key: code, target: target || document.body, preventDefault() {}});
}

// o is in the contract, grouped with the other panels, and the overlay is built from this table
assert.ok(mod.BINDINGS.some(b => b.code === 'KeyO' && b.group === 'panels'), 'KeyO must be a panels binding');

// ---- the button is built at startup, beside the board bar, not inside it (renderBoardBar wipes it)
const button = mod.buttonRef();
assert.ok(button, 'the settings button exists once settings.js loads');
assert.ok(!boardBar.children.includes(button), 'the button must not live inside #board-bar');

// ---- the button opens the panel -------------------------------------------------------------
button.onclick();
assert.ok(mod.st.backdrop.parentNode, 'clicking the button should open the panel');

// ---- the panel renders an empty section list, ready for preferences --------------------------
assert.ok(mod.st.listEl.querySelector('.hazard-placeholder'), 'an empty settings list shows a placeholder, not nothing');
assert.equal(mod.st.listEl.querySelectorAll('.settings-section').length, 0, 'no preference sections exist yet');

// ---- a click on the backdrop itself closes it, a click inside the panel does not ---------------
mod.st.backdrop._listeners.mousedown.forEach(fn => fn({target: mod.st.panel}));
assert.ok(mod.st.backdrop.parentNode, 'a click on the panel must not close it');
mod.st.backdrop._listeners.mousedown.forEach(fn => fn({target: mod.st.backdrop}));
assert.ok(!mod.st.backdrop.parentNode, 'a click on the backdrop itself should close the panel');

// ---- o opens the panel too ------------------------------------------------------------------
press('KeyO');
assert.ok(mod.st.backdrop.parentNode, 'o should open the panel');

// ---- escape closes it ------------------------------------------------------------------------
document._dispatch('keydown', {code: 'Escape', key: 'Escape', target: document.body, preventDefault() {}});
assert.ok(!mod.st.backdrop.parentNode, 'escape should close the panel');

// ---- o toggles: open, then a second press closes it (the key that opens also closes) -----------
press('KeyO');
assert.ok(mod.st.backdrop.parentNode, 'o should reopen the panel');
press('KeyO');
assert.ok(!mod.st.backdrop.parentNode, 'a second o should close the panel');

console.log('ok');
