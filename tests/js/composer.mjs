// mission control's composer: starts collapsed at one line, grows upward with typed content up
// to a configurable max (falling back to 6), and leaves the rest to the textarea's own scrolling.
// run: node tests/js/composer.mjs
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {installStubDom, element} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => readFileSync(new URL(`../smortui/ui_base/assets/${p}`, root), 'utf8');
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

function stubJson(status, body) {
  return {ok: status >= 200 && status < 300, status, json: async () => body};
}
function fetchStub() {
  return Promise.resolve(stubJson(200, []));
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
function SpyDrawer() {
  let opened = false;
  return {
    el: element('div'), body: element('div'),
    open() { opened = true; }, close() { opened = false; }, toggle() { opened = !opened; },
    isOpen: () => opened,
  };
}

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), smort('board.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {buildDrawers, mc, growComposer, composerMaxLines};`)(SpyMenu, SpyDrawer);

mod.buildDrawers();
const input = mod.mc.input;

// ---- starts collapsed at one line -----------------------------------------------------------
assert.equal(input.rows, 1, 'the composer starts at one line');

// ---- grows one row per line break, up to the default of 6 -----------------------------------
input.value = 'one\ntwo';
input._listeners.input.forEach(fn => fn());
assert.equal(input.rows, 2, 'one break grows the box by one row');

input.value = 'one\ntwo\nthree\nfour\nfive\nsix\nseven\neight';
input._listeners.input.forEach(fn => fn());
assert.equal(input.rows, 6, 'growth stops at the fallback of 6 even with more line breaks than that');

// ---- shrinks back down as line breaks are removed --------------------------------------------
input.value = 'one line again';
input._listeners.input.forEach(fn => fn());
assert.equal(input.rows, 1, 'clearing the breaks collapses the box back to one line');

// ---- max lines come from global config, not the fallback -------------------------------------
window.smortboardConfig = {composerMaxLines: 3};
input.value = 'one\ntwo\nthree\nfour\nfive';
input._listeners.input.forEach(fn => fn());
assert.equal(input.rows, 3, 'a configured max caps growth below the fallback of 6');
delete window.smortboardConfig;

// ---- an invalid config value still falls back to 6 --------------------------------------------
window.smortboardConfig = {composerMaxLines: 0};
input.value = 'one\ntwo\nthree\nfour\nfive\nsix\nseven';
input._listeners.input.forEach(fn => fn());
assert.equal(input.rows, 6, 'a zero or invalid configured max is treated as unset');
delete window.smortboardConfig;

// ---- sending clears the text and collapses the composer back to one line ----------------------
input.value = 'one\ntwo\nthree';
input._listeners.input.forEach(fn => fn());
assert.equal(input.rows, 3, 'the composer is expanded before sending');
input._listeners.keydown.forEach(fn => fn({code: 'Enter', shiftKey: false, preventDefault() {}}));
assert.equal(input.value, '', 'enter sends and clears the composer');
assert.equal(input.rows, 1, 'sending collapses the composer back to one line');

console.log('ok');
