// the top-bar usage cells: codex 7d, claude 5h and 7d, each the mean of its lab's real account
// windows, toned kingfisher / vanilla / stone at 70 and 90 percent used. a lab with no real reading
// draws an empty frame, never 0%, and a failed poll keeps the last reading.
// run: UI_BASE_ASSETS_DIR=<ui_base assets dir> node tests/js/usage_cells.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset, stubLayout} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');

const responses = new Map();
const stubJson = (status, body) => ({ok: status >= 200 && status < 300, status, json: async () => body});
const fetchStub = path => Promise.resolve(responses.get(path) || stubJson(404, {error: 'no stub for ' + path}));

installStubDom({fetchImpl: fetchStub});
stubLayout.rect = () => ({left: 0, top: 0, right: 460, bottom: 900, width: 460, height: 900});
responses.set('/api/boards', stubJson(200, []));

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

const real = (lab, type, utilization, extra = {}) =>
  ({lab, type, profile: 'default', status: 'ok', utilization, source: 'oauth', ...extra});
responses.set('/api/usage', stubJson(200, {windows: [
  real('anthropic', 'five_hour', 0.42),
  real('anthropic', 'seven_day', 0.91),
  real('openai', 'seven_day', 0.70, {source: 'app_server'}),
]}));

function SpyDrawer() {
  return {el: element('div'), body: element('div'), open() {}, close() {}, toggle() {}, isOpen: () => false};
}
class SpyMenu { constructor(opts) { this.opts = opts; } openAt() { return this; } refresh() {} close() {} }

const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'),
  uiBase('pile.js'), smort('columns.js'), smort('card_panel.js'), smort('chat.js'),
  smort('shortcuts.js'), smort('board.js'), smort('usage_cells.js')].join('\n;\n');
const mod = new Function('Menu', 'makeDrawer', `${src}
;return {labUsage, usageTone, renderUsageCells, pollUsageCells, cells: () => usageCells};`)(SpyMenu, SpyDrawer);

const flush = () => new Promise(r => setTimeout(r, 0));
const cell = label => mod.cells().find(c => c.spec.label === label).el;
const readout = el => ({
  value: el.querySelector('.fill-cell-value').textContent,
  width: el.querySelector('.fill-cell-bar').style.width,
  tone: el.classList.contains('warn') ? 'warn' : el.classList.contains('attention') ? 'attention' : 'good',
});

// ---- (a) the three cells read their lab's window, with the level tones -------------------------------
await flush(); await flush();
assert.deepEqual(mod.cells().map(c => c.spec.label), ['codex 7d', 'claude 5h', 'claude 7d']);
assert.deepEqual(readout(cell('claude 5h')), {value: '42%', width: '42%', tone: 'good'});
assert.deepEqual(readout(cell('claude 7d')), {value: '91%', width: '91%', tone: 'warn'});
assert.deepEqual(readout(cell('codex 7d')), {value: '70%', width: '70%', tone: 'attention'},
  '70 percent is the first vanilla value, not the last kingfisher one');
assert.equal(mod.usageTone(69), '');
assert.equal(mod.usageTone(89), 'attention');
assert.equal(mod.usageTone(90), 'warn');

// ---- the cells sit in the bar corner, a ui_base divider between groups and between claude's two ----
const rootEl = document.getElementById('usage-cells');
assert.ok(rootEl && rootEl.parentNode === document.getElementById('bar-corner'), 'in the bar corner');
assert.equal(rootEl.querySelectorAll('.divider').length, 2, 'codex | claude 5h | claude 7d');
assert.equal(rootEl.querySelectorAll('.fill-cell').length, 3);

// ---- (b) several tokens in one lab: the mean ----------------------------------------------------------
assert.ok(Math.abs(mod.labUsage([real('anthropic', 'seven_day', 0.2), real('anthropic', 'seven_day', 0.6)],
  'anthropic', 'seven_day') - 0.4) < 1e-9, 'mean of 0.2 and 0.6');

// ---- (c) a window without a source is a run estimate, not an account reading: ignored ------------------
assert.equal(mod.labUsage([real('openai', 'seven_day', 0.9, {source: undefined})], 'openai', 'seven_day'), null);
assert.ok(Math.abs(mod.labUsage([real('openai', 'seven_day', 0.2),
  real('openai', 'seven_day', 0.9, {source: undefined})], 'openai', 'seven_day') - 0.2) < 1e-9);

// ---- (d) a rolled-over or null window draws an empty frame, not 0% -------------------------------------
mod.renderUsageCells([real('anthropic', 'five_hour', null), real('anthropic', 'seven_day', 0.5, {rolled_over: true})]);
assert.deepEqual(readout(cell('claude 5h')), {value: '--', width: '0', tone: 'good'});
assert.deepEqual(readout(cell('claude 7d')), {value: '--', width: '0', tone: 'good'});

// ---- (e) an empty window list empties every cell, and a window of the wrong lab does not leak -----------
mod.renderUsageCells([]);
mod.cells().forEach(c => assert.equal(readout(c.el).value, '--', c.spec.label));
mod.renderUsageCells([real('anthropic', 'seven_day', 0.5)]);
assert.equal(readout(cell('codex 7d')).value, '--', 'codex does not read claude\'s window');
assert.equal(readout(cell('claude 7d')).value, '50%');

// ---- zero usage is a real reading: 0%, kingfisher, no bar ----------------------------------------------
mod.renderUsageCells([real('openai', 'seven_day', 0)]);
assert.deepEqual(readout(cell('codex 7d')), {value: '0%', width: '0%', tone: 'good'});

// ---- (f) a failed poll keeps the last reading ----------------------------------------------------------
mod.renderUsageCells([real('anthropic', 'five_hour', 0.33)]);
responses.set('/api/usage', stubJson(500, {error: 'boom'}));
await mod.pollUsageCells();
assert.equal(readout(cell('claude 5h')).value, '33%', 'a 500 leaves the last reading showing');

console.log('ok');
