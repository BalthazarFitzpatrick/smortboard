// exercises board.js's column-skip wiring: left/right should jump over any run of empty status
// buckets to the nearest column that actually has a card, stay put with no wrap when nothing
// non-empty lies further that way, and leave a plain adjacent move to the real ui_base nav
// untouched. run: node tests/js/board_column_skip.mjs
import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {installStubDom, element, uiBaseAsset} from './dom_stub.mjs';

const root = new URL('../../', import.meta.url);
const uiBase = p => uiBaseAsset(root, p);
const smort = p => readFileSync(new URL(`smortboard/ui/${p}`, root), 'utf8');
const src = [uiBase('buckets.js'), uiBase('expand.js'), uiBase('indicate.js'), uiBase('shell.js'), smort('board.js')].join('\n;\n');
const STATUS_ORDER = ['todo', 'doing', 'checking', 'accepted', 'rejected'];

// a fresh stub document plus a fresh board.js module per scenario - nothing accumulates across
// cases, so the one keydown listener ui_base's makeBuckets attaches is always index 0
function setup(statusesWithCards) {
  installStubDom({fetchImpl: () => new Promise(() => {})}); // fetches never resolve in this test
  const boardBar = element('div', 'board-bar');
  boardBar.id = 'board-bar';
  const bucketRow = element('div', 'bucket-row');
  bucketRow.id = 'bucket-row';
  STATUS_ORDER.forEach(status => {
    const bucket = element('div', 'bucket');
    bucket.dataset.status = status;
    bucket.appendChild(element('div', 'bucket-label'));
    bucket.appendChild(element('div', 'bucket-rows'));
    bucketRow.appendChild(bucket);
  });
  document.body.appendChild(boardBar);
  document.body.appendChild(bucketRow);
  const mod = new Function(`${src}\n;return {renderBuckets};`)();
  const cards = statusesWithCards.map(status => ({id: status, title: status, status, workstream: 'w'}));
  mod.renderBuckets(cards);
  const rowFor = status => bucketRow.querySelector(`.bucket[data-status="${status}"] .row`);
  // dispatches through document, same path a real keypress takes to the capture-phase listener
  const press = (code, target) => {
    let prevented = false;
    let stopped = false;
    document._dispatch('keydown', {
      code, target, preventDefault() { prevented = true; }, stopPropagation() { stopped = true; },
    });
    return {prevented, stopped};
  };
  return {bucketRow, rowFor, press};
}

// ---- single empty column skipped: todo has a card, doing is empty, checking has one
{
  const {rowFor, press} = setup(['todo', 'checking', 'accepted', 'rejected']);
  rowFor('todo').focus();
  const result = press('ArrowRight', rowFor('todo'));
  assert.ok(result.prevented && result.stopped, 'a skip should swallow the key itself');
  assert.ok(rowFor('checking').focused, 'ArrowRight should skip the single empty doing column');
}

// ---- multiple consecutive empty columns skipped: doing and checking both empty
{
  const {rowFor, press} = setup(['todo', 'accepted']);
  rowFor('todo').focus();
  press('ArrowRight', rowFor('todo'));
  assert.ok(rowFor('accepted').focused, 'ArrowRight should skip two consecutive empty columns');
}

// ---- no non-empty column further right: nothing past checking, and todo (to the left) must
// not be reached either - no wrap
{
  const {rowFor, press} = setup(['todo', 'checking']);
  rowFor('checking').focus();
  const result = press('ArrowRight', rowFor('checking'));
  assert.ok(result.prevented && result.stopped, 'running out of columns should still swallow the key');
  assert.ok(rowFor('checking').focused, 'focus should stay on checking, not move at all');
  assert.equal(rowFor('todo').focused, undefined, 'focus must not wrap around to todo');
}

// ---- same, pressing left from the leftmost card with only empty columns behind it
{
  const {rowFor, press} = setup(['checking', 'rejected']);
  rowFor('checking').focus();
  const result = press('ArrowLeft', rowFor('checking'));
  assert.ok(result.prevented && result.stopped, 'running out of columns to the left should also swallow the key');
  assert.ok(rowFor('checking').focused, 'focus should stay on checking');
}

// ---- a normal adjacent move (no empty column in between) is left to the real default nav: this
// listener steps aside, and the row-level listener ui_base attaches still does its job
{
  const {bucketRow, rowFor, press} = setup(['todo', 'doing', 'checking', 'accepted', 'rejected']);
  rowFor('todo').focus();
  const result = press('ArrowRight', rowFor('todo'));
  assert.ok(!result.prevented && !result.stopped, 'a plain adjacent move should not be intercepted');
  assert.ok(rowFor('todo').focused, 'this listener alone must not have moved focus');
  bucketRow._listeners.keydown[0]({key: 'ArrowRight', target: rowFor('todo'), preventDefault() {}});
  assert.ok(rowFor('doing').focused, 'the real default nav should still move focus to the next column');
}

console.log('ok');
