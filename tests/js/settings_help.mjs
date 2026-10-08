// proves the settings help text: one entry per row id, each with a title and lines, lowercase,
// plain words (the floating box renders them with textContent), and a lookup that is null for a
// row without one. the panels tag rows with these ids, so the ids below are the contract
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', '..', 'smortboard', 'ui', 'settings_help.js'), 'utf8');
const {SETTINGS_HELP, helpFor} = new Function(`${src}\n;return {SETTINGS_HELP, helpFor};`)();

const general = ['preset', 'allow_soft_leases', 'allow_free_merge', 'allow_open_mode', 'off_limit_branches',
  'max_parallel', 'mall_cam_interval_seconds', 'enable_mouse', 'worker_budget_usd', 'reviewer_budget_usd',
  'orchestrator_budget_usd', 'fold_budget_usd', 'card_total_budget_usd', 'usage_limit_route', 'reviewer_input',
  'models', 'repos_home', 'mission_control_read_paths', 'backup', 'findings_route', 'resume_briefing',
  'gate_timeout_seconds'];
const board = ['board_preset', 'merge_mode', 'board_off_limit_branches', 'lease_mode', 'run_mode',
  'board_max_parallel', 'daily_budget_usd'];

[...general, ...board].forEach(id => assert.ok(SETTINGS_HELP[id], `help for ${id}`));
assert.deepEqual(Object.keys(SETTINGS_HELP).sort(), [...general, ...board].sort(), 'no entry without a row, no row without an entry');

Object.entries(SETTINGS_HELP).forEach(([id, entry]) => {
  assert.ok(entry.title && entry.title === entry.title.toLowerCase(), `${id}: lowercase title`);
  assert.ok(Array.isArray(entry.lines) && entry.lines.length >= 1, `${id}: has lines`);
  entry.lines.forEach(line => {
    assert.equal(typeof line, 'string', `${id}: lines are plain strings`);
    assert.ok(line.length >= 5 && line.length < 260, `${id}: a short line`);
    assert.equal(line, line.toLowerCase(), `${id}: lowercase text`);
    assert.ok(!/[<>]/.test(line), `${id}: no markup`);
  });
});

assert.equal(helpFor('merge_mode'), SETTINGS_HELP.merge_mode);
assert.equal(helpFor('nothing here'), null);
// the gated modes name the switch that unlocks them
['merge_mode', 'lease_mode', 'run_mode'].forEach(id => {
  assert.ok(SETTINGS_HELP[id].lines.join(' ').includes('in general'), `${id} names its switch`);
});
console.log('ok');
