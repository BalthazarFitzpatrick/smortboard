// proves the settings panels' pure logic: how values are matched to a preset (and when that is
// "custom"), what a preset writes, the one toggle wording, board gate lines and counts, and the
// accordion header summaries. no dom - the module is plain script globals, loaded as the page does
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', '..', 'smortboard', 'ui', 'settings_presets.js'), 'utf8');
const mod = new Function(`${src}
;return {BOOLEAN_SETTINGS, WORDING, booleanIsOn, booleanStored, effectiveGeneral, matchGeneralPreset,
  generalPresetPatch, matchBoardPreset, boardPresetAvailability, boardPresetPatch, BOARD_PRESETS,
  gateLine, boardInForce, gateUsage, generalGroups, boardGroups};`)();

// ---- one polarity, mapped in one place ----------------------------------------------------------
assert.equal(mod.booleanIsOn({}, 'allow_free_merge'), false, 'a gate is off when unset');
assert.equal(mod.booleanIsOn({allow_free_merge: 'on'}, 'allow_free_merge'), true);
assert.equal(mod.booleanIsOn({}, 'off_limit_branches'), true, 'off-limits is on when unset');
assert.equal(mod.booleanIsOn({off_limit_branches: 'off'}, 'off_limit_branches'), false);
assert.equal(mod.booleanStored('off_limit_branches', false), 'off', 'off stores the string off');
assert.equal(mod.booleanStored('off_limit_branches', true), null);
assert.equal(mod.booleanStored('allow_soft_leases', true), 'on');
assert.equal(mod.booleanStored('allow_soft_leases', false), null);
assert.equal(mod.booleanStored('resume_briefing', false), 'off');
assert.deepEqual(mod.WORDING.boolean, ['on', 'off'], 'booleans only ever read on and off');

// ---- general presets -----------------------------------------------------------------------------
let match = mod.matchGeneralPreset({});
assert.equal(match.id, 'balanced', 'an untouched board of settings is balanced');
assert.deepEqual(match.diffs, []);
assert.match(match.line.lead, /^balanced sets all gates off, 2 cards at once/);
assert.equal(match.line.strong, '0 values differ.');

match = mod.matchGeneralPreset({allow_free_merge: 'on'});
assert.equal(match.id, 'custom', 'a value no preset has is custom');
assert.equal(match.closest, 'balanced');
assert.equal(match.diffs.length, 1);
assert.equal(match.line.lead, 'no preset matches. closest is balanced, with ');
assert.equal(match.line.strong, '1 difference: free merge is on.');

match = mod.matchGeneralPreset({max_parallel: 3});
assert.equal(match.line.strong, '1 difference: 3 cards at once.');

assert.equal(mod.matchGeneralPreset({allow_soft_leases: 'on', allow_free_merge: 'on', max_parallel: 4}).id, 'fast');
assert.equal(mod.matchGeneralPreset({max_parallel: '1', worker_budget_usd: '3', reviewer_budget_usd: 1,
  fold_budget_usd: '1.5', card_total_budget_usd: 10}).id, 'careful', 'numeric strings from the api match');
assert.equal(mod.matchGeneralPreset({max_parallel: 2, worker_budget_usd: 5}).id, 'balanced',
  'a value equal to its default matches the unset one');

// ---- what a preset writes ------------------------------------------------------------------------
assert.deepEqual(mod.generalPresetPatch('balanced'), {
  allow_soft_leases: null, allow_free_merge: null, allow_open_mode: null, off_limit_branches: null,
  max_parallel: null, worker_budget_usd: null, reviewer_budget_usd: null, orchestrator_budget_usd: null,
  fold_budget_usd: null, card_total_budget_usd: null,
}, 'balanced clears every row, so default changes still reach it');
const fast = mod.generalPresetPatch('fast');
assert.equal(fast.allow_soft_leases, 'on');
assert.equal(fast.allow_free_merge, 'on');
assert.equal(fast.allow_open_mode, null, 'fast keeps open run mode off');
assert.equal(fast.max_parallel, 4);
assert.equal(fast.worker_budget_usd, null);
const careful = mod.generalPresetPatch('careful');
assert.deepEqual([careful.max_parallel, careful.worker_budget_usd, careful.reviewer_budget_usd,
  careful.fold_budget_usd, careful.card_total_budget_usd], [1, 3, 1, 1.5, 10]);
['careful', 'balanced', 'fast'].forEach(id => {
  const applied = Object.fromEntries(Object.entries(mod.generalPresetPatch(id)).filter(([, v]) => v !== null));
  assert.equal(mod.matchGeneralPreset(applied).id, id, `applying ${id} reads back as ${id}`);
});

// ---- board presets -------------------------------------------------------------------------------
assert.equal(mod.matchBoardPreset({}).id, 'review only', 'a new board is review only');
assert.equal(mod.matchBoardPreset({merge_mode: 'free'}).id, 'free merge');
assert.equal(mod.matchBoardPreset({lease_mode: 'soft', run_mode: 'open'}).id, 'open');
match = mod.matchBoardPreset({merge_mode: 'free', run_mode: 'open'});
assert.equal(match.id, 'custom');
assert.equal(match.closest, 'free merge');
assert.equal(match.line.strong, '1 difference: run mode is open.');
assert.deepEqual(mod.boardPresetPatch('open'), {merge_mode: 'review', lease_mode: 'soft', run_mode: 'open'});
const open = mod.BOARD_PRESETS.find(p => p.id === 'open');
assert.deepEqual(mod.boardPresetAvailability(open, {}), {ok: false, missing: ['allow_soft_leases', 'allow_open_mode']});
assert.deepEqual(mod.boardPresetAvailability(open, {allow_soft_leases: 'on', allow_open_mode: 'on'}), {ok: true, missing: []});
assert.equal(mod.boardPresetAvailability(mod.BOARD_PRESETS[0], {}).ok, true, 'review only never needs a gate');

// ---- gates: the line, the value in force, the counts -------------------------------------------------
assert.equal(mod.gateLine({}, 'allow_free_merge'), 'free merge: off in general');
assert.equal(mod.gateLine({allow_open_mode: 'on'}, 'allow_open_mode'), 'open run mode: on in general');
const freeBoard = {id: 'a', merge_mode: 'free', lease_mode: 'soft', run_mode: 'open'};
assert.deepEqual(mod.boardInForce(freeBoard, {}), {merge_mode: 'review', lease_mode: 'strict', run_mode: 'sealed'},
  'stored choices behind an off gate resolve to the safe value');
assert.deepEqual(mod.boardInForce(freeBoard, {allow_free_merge: 'on', allow_soft_leases: 'on', allow_open_mode: 'on'}),
  {merge_mode: 'free', lease_mode: 'soft', run_mode: 'open'}, 'and come back with the gate');
const boards = [freeBoard, {id: 'b'}, {id: 'c', lease_mode: 'soft'}, {id: 'd'}];
assert.deepEqual(mod.gateUsage(boards, {allow_free_merge: 'on', allow_soft_leases: 'on'}), {
  allow_soft_leases: {used: 2, total: 4}, allow_free_merge: {used: 1, total: 4}, allow_open_mode: {used: 0, total: 4},
});
assert.equal(mod.gateUsage(boards, {}).allow_soft_leases.used, 0, 'a gate that is off has no users');

// ---- accordion header summaries --------------------------------------------------------------------
const groups = mod.generalGroups({free_merge: undefined, allow_free_merge: 'on', mission_control_read_paths: ['c:/a'],
  repos_home: 'c:/dev', usage_limit_route: 'switch', worker_model: 'm'});
assert.deepEqual(groups.map(g => g.id), ['safety', 'capacity', 'cost', 'models', 'paths', 'advanced']);
assert.equal(groups[0].summary, 'soft leases off / free merge on / open run off / off-limits on');
assert.equal(groups[1].summary, '2 cards at once / mall cam 10 s / mouse off');
assert.equal(groups[2].summary, 'worker 5.00 / reviewer 1.50 / orchestrator 1.00 / fold 2.00 / card total none');
assert.equal(groups[3].summary, 'on limit: switch / reviewer reads diff / 1 of 4 roles set');
assert.equal(groups[4].summary, 'new repos in c:/dev / 1 readable path');
assert.equal(groups[5].summary, 'findings per card / briefing on / gate timeout 600 s');
const bgroups = mod.boardGroups({id: 'a', off_limit_branches: ['main', 'trunk'], max_parallel: 3, daily_budget_usd: 8,
  merge_mode: 'free'}, {});
assert.deepEqual(bgroups.map(g => g.id), ['landing', 'running', 'budget']);
assert.equal(bgroups[0].summary, 'merge review / off-limits main, trunk', 'the summary shows the value in force');
assert.equal(bgroups[1].summary, 'file lease strict / run sealed / cards 3');
assert.equal(bgroups[2].summary, 'daily 8.00');

console.log('ok');
