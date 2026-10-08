// the settings panels' logic with no dom: presets, how a set of values is matched to one, the one
// wording for every toggle, and the one-line summaries the accordion headers show. both panels
// (settings.js for o, board_settings.js for shift+o) read it, and tests/js/settings_presets.mjs
// drives it directly
//
// a preset is applied once: it writes its values and nothing is stored about it. the label is
// derived from the values every time, so a later edit never fights a preset, and "custom" is only
// ever a result, never a choice

// defaults that live in the server (scheduler.DEFAULT_MAX_PARALLEL, each role's budget constant).
// mirrored here because the panels show them as the value in force when a setting is unset
const SETTINGS_DEFAULTS = {
  max_parallel: 2,
  mall_cam_interval_seconds: 10,
  gate_timeout_seconds: 600,
  worker_budget_usd: 5,
  reviewer_budget_usd: 1.5,
  orchestrator_budget_usd: 1,
  fold_budget_usd: 2,
};

// every on/off setting: the stored value for on and for off. polarity differs (the off-limit
// switch stores 'off', the gates store 'on'), so the panels never read the raw string themselves
const BOOLEAN_SETTINGS = {
  allow_soft_leases: {on: 'on', off: null, label: 'soft file leases'},
  allow_free_merge: {on: 'on', off: null, label: 'free merge'},
  allow_open_mode: {on: 'on', off: null, label: 'open run mode'},
  off_limit_branches: {on: null, off: 'off', label: 'off-limit branches'},
  enable_mouse: {on: 'on', off: null, label: 'mouse'},
  resume_briefing: {on: null, off: 'off', label: 'resume briefing'},
};

function booleanIsOn(settings, key) {
  const spec = BOOLEAN_SETTINGS[key];
  return (settings[key] ?? null) === spec.on;
}

function booleanStored(key, on) {
  const spec = BOOLEAN_SETTINGS[key];
  return on ? spec.on : spec.off;
}

// the single wording of every choice, so a label never changes between screens
const WORDING = {
  boolean: ['on', 'off'],
  usageLimit: [['wait', null], ['ask me', 'attention'], ['switch', 'switch']],
  reviewerReads: [['diff', null], ['code', 'code']],
  mergeMode: [['review', 'review'], ['free', 'free']],
  fileLease: [['strict', 'strict'], ['soft', 'soft']],
  runMode: [['sealed', 'sealed'], ['open', 'open']],
  findingsRoute: [['per card', null], ['fix', 'fix'], ['attention', 'attention']],
};

const SPEND_CAP_KEYS = [
  ['worker_budget_usd', 'worker'],
  ['reviewer_budget_usd', 'reviewer'],
  ['orchestrator_budget_usd', 'orchestrator'],
  ['fold_budget_usd', 'fold'],
];

const ROLE_NAMES = ['worker', 'reviewer', 'orchestrator', 'fold'];

function moneyText(value) {
  return Number(value).toFixed(2);
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

// the values a preset speaks about, with unset read as the default in force
function effectiveGeneral(settings) {
  const cap = key => numberOrNull(settings[key]) ?? SETTINGS_DEFAULTS[key];
  return {
    allow_soft_leases: booleanIsOn(settings, 'allow_soft_leases'),
    allow_free_merge: booleanIsOn(settings, 'allow_free_merge'),
    allow_open_mode: booleanIsOn(settings, 'allow_open_mode'),
    off_limit_branches: booleanIsOn(settings, 'off_limit_branches'),
    max_parallel: numberOrNull(settings.max_parallel) ?? SETTINGS_DEFAULTS.max_parallel,
    worker_budget_usd: cap('worker_budget_usd'),
    reviewer_budget_usd: cap('reviewer_budget_usd'),
    orchestrator_budget_usd: cap('orchestrator_budget_usd'),
    fold_budget_usd: cap('fold_budget_usd'),
    card_total_budget_usd: numberOrNull(settings.card_total_budget_usd),
  };
}

const GENERAL_PRESETS = [
  {
    id: 'careful',
    label: 'careful',
    sets: 'all gates off, 1 card at once, caps 3.00 / 1.00 / 1.00 / 1.50 usd, card total 10.00 usd',
    values: {
      allow_soft_leases: false, allow_free_merge: false, allow_open_mode: false,
      off_limit_branches: true, max_parallel: 1, worker_budget_usd: 3, reviewer_budget_usd: 1,
      orchestrator_budget_usd: 1, fold_budget_usd: 1.5, card_total_budget_usd: 10,
    },
  },
  {
    id: 'balanced',
    label: 'balanced',
    sets: 'all gates off, 2 cards at once, caps 5.00 / 1.50 / 1.00 / 2.00 usd, no card total',
    values: {
      allow_soft_leases: false, allow_free_merge: false, allow_open_mode: false,
      off_limit_branches: true, max_parallel: 2, worker_budget_usd: 5, reviewer_budget_usd: 1.5,
      orchestrator_budget_usd: 1, fold_budget_usd: 2, card_total_budget_usd: null,
    },
  },
  {
    id: 'fast',
    label: 'fast',
    sets: 'free merge and soft file leases on, open run mode off, 4 cards at once, balanced caps',
    values: {
      allow_soft_leases: true, allow_free_merge: true, allow_open_mode: false,
      off_limit_branches: true, max_parallel: 4, worker_budget_usd: 5, reviewer_budget_usd: 1.5,
      orchestrator_budget_usd: 1, fold_budget_usd: 2, card_total_budget_usd: null,
    },
  },
];

// what one value reads like in a "differs" line
function describeValue(key, value) {
  if (key in BOOLEAN_SETTINGS) return value ? 'on' : 'off';
  if (key === 'max_parallel') return `${value} ${value === 1 ? 'card' : 'cards'} at once`;
  if (key === 'card_total_budget_usd') return value === null ? 'no card total' : `card total ${moneyText(value)}`;
  const role = SPEND_CAP_KEYS.find(([k]) => k === key);
  if (role) return `${role[1]} cap ${moneyText(value)}`;
  return String(value);
}

function differences(have, want, labels) {
  return Object.keys(want).filter(key => have[key] !== want[key]).map(key => ({
    key, have: have[key], want: want[key], text: labels(key, have[key]),
  }));
}

function generalDiffText(key, have) {
  if (key in BOOLEAN_SETTINGS) return `${BOOLEAN_SETTINGS[key].label} is ${have ? 'on' : 'off'}`;
  return describeValue(key, have);
}

// {id: 'careful'|'balanced'|'fast'|'custom', closest, diffs, line: {lead, strong}}. an exact match
// has no diffs; otherwise the closest preset is the one with the fewest, balanced winning a tie
function matchGeneralPreset(settings) {
  const have = effectiveGeneral(settings);
  const scored = GENERAL_PRESETS.map(p => ({p, diffs: differences(have, p.values, generalDiffText)}));
  const exact = scored.find(s => s.diffs.length === 0);
  if (exact) {
    return {id: exact.p.id, closest: exact.p.id, diffs: [],
      line: {lead: `${exact.p.label} sets ${exact.p.sets}. `, strong: '0 values differ.'}};
  }
  const best = scored.reduce((a, b) => (b.diffs.length < a.diffs.length ? b : a),
    scored.find(s => s.p.id === 'balanced'));
  const n = best.diffs.length;
  return {id: 'custom', closest: best.p.id, diffs: best.diffs,
    line: {lead: `no preset matches. closest is ${best.p.label}, with `,
      strong: `${n} ${n === 1 ? 'difference' : 'differences'}: ${best.diffs.map(d => d.text).join(', ')}.`}};
}

// the keys one preset writes in a single PATCH /api/settings. a value equal to its default is sent
// as null, so a balanced preset leaves the rows unset and a later default change still reaches it
function generalPresetPatch(id) {
  const preset = GENERAL_PRESETS.find(p => p.id === id);
  const patch = {};
  Object.entries(preset.values).forEach(([key, value]) => {
    if (key in BOOLEAN_SETTINGS) { patch[key] = booleanStored(key, value); return; }
    patch[key] = value === SETTINGS_DEFAULTS[key] ? null : value;
  });
  return patch;
}

// ---- board presets ------------------------------------------------------------------------------

const BOARD_PRESETS = [
  {id: 'review only', label: 'review only', values: {merge_mode: 'review', lease_mode: 'strict', run_mode: 'sealed'},
    sets: 'merge mode review, file lease strict, run mode sealed', needs: []},
  {id: 'free merge', label: 'free merge', values: {merge_mode: 'free', lease_mode: 'strict', run_mode: 'sealed'},
    sets: 'merge mode free, file lease strict, run mode sealed', needs: ['allow_free_merge']},
  {id: 'open', label: 'open', values: {merge_mode: 'review', lease_mode: 'soft', run_mode: 'open'},
    sets: 'merge mode review, file lease soft, run mode open', needs: ['allow_soft_leases', 'allow_open_mode']},
];

// a board's stored choices with null read as the safe default
function storedBoard(board) {
  return {merge_mode: board.merge_mode || 'review', lease_mode: board.lease_mode || 'strict',
    run_mode: board.run_mode || 'sealed'};
}

function boardDiffText(key, have) {
  const name = {merge_mode: 'merge mode', lease_mode: 'file lease', run_mode: 'run mode'}[key];
  return `${name} is ${have}`;
}

function matchBoardPreset(board) {
  const have = storedBoard(board);
  const scored = BOARD_PRESETS.map(p => ({p, diffs: differences(have, p.values, boardDiffText)}));
  const exact = scored.find(s => s.diffs.length === 0);
  if (exact) {
    return {id: exact.p.id, closest: exact.p.id, diffs: [],
      line: {lead: `${exact.p.label} sets ${exact.p.sets}. `, strong: '0 values differ.'}};
  }
  const best = scored.reduce((a, b) => (b.diffs.length < a.diffs.length ? b : a), scored[0]);
  const n = best.diffs.length;
  return {id: 'custom', closest: best.p.id, diffs: best.diffs,
    line: {lead: `no preset matches. closest is ${best.p.label}, with `,
      strong: `${n} ${n === 1 ? 'difference' : 'differences'}: ${best.diffs.map(d => d.text).join(', ')}.`}};
}

// {ok, missing: [gate keys that are off]}: a board preset whose gate is off is unavailable
function boardPresetAvailability(preset, settings) {
  const missing = preset.needs.filter(key => !booleanIsOn(settings, key));
  return {ok: missing.length === 0, missing};
}

function boardPresetPatch(id) {
  return {...BOARD_PRESETS.find(p => p.id === id).values};
}

// "free merge: off in general" - a gated row's one line, in the toggle wording
function gateLine(settings, key) {
  return `${BOOLEAN_SETTINGS[key].label}: ${booleanIsOn(settings, key) ? 'on' : 'off'} in general`;
}

// the value in force for a board's three gated choices: stored while the gate is on, else the safe
// default. the stored value stays on disk either way
function boardInForce(board, settings) {
  const stored = storedBoard(board);
  return {
    merge_mode: stored.merge_mode === 'free' && !booleanIsOn(settings, 'allow_free_merge') ? 'review' : stored.merge_mode,
    lease_mode: stored.lease_mode === 'soft' && !booleanIsOn(settings, 'allow_soft_leases') ? 'strict' : stored.lease_mode,
    run_mode: stored.run_mode === 'open' && !booleanIsOn(settings, 'allow_open_mode') ? 'sealed' : stored.run_mode,
  };
}

// how many boards use each gated mode right now (in force, so a stored choice behind an off gate
// does not count): {allow_free_merge: {used, total}, ...}
function gateUsage(boards, settings) {
  const total = boards.length;
  const inForce = boards.map(b => boardInForce(b, settings));
  const count = pick => inForce.filter(pick).length;
  return {
    allow_soft_leases: {used: count(b => b.lease_mode === 'soft'), total},
    allow_free_merge: {used: count(b => b.merge_mode === 'free'), total},
    allow_open_mode: {used: count(b => b.run_mode === 'open'), total},
  };
}

// ---- accordion headers (general panel) ---------------------------------------------------------

function onOff(settings, key) {
  return booleanIsOn(settings, key) ? 'on' : 'off';
}

function usageLimitWord(settings) {
  const pair = WORDING.usageLimit.find(([, stored]) => stored === (settings.usage_limit_route ?? null));
  return pair ? pair[0] : 'wait';
}

// {id: {title, count, summary}} for the six groups, in the order the panel shows them
function generalGroups(settings) {
  const eff = effectiveGeneral(settings);
  const rolesSet = ROLE_NAMES.filter(r => settings[`${r}_model`] || settings[`${r}_lab`]).length;
  const paths = (settings.mission_control_read_paths || []).length;
  const timeout = numberOrNull(settings.gate_timeout_seconds) ?? SETTINGS_DEFAULTS.gate_timeout_seconds;
  return [
    {id: 'safety', title: 'safety gates', count: 4,
      summary: `soft leases ${onOff(settings, 'allow_soft_leases')} / free merge ${onOff(settings, 'allow_free_merge')}`
        + ` / open run ${onOff(settings, 'allow_open_mode')} / off-limits ${onOff(settings, 'off_limit_branches')}`},
    {id: 'capacity', title: 'capacity', count: 3,
      summary: `${eff.max_parallel} cards at once / mall cam ${numberOrNull(settings.mall_cam_interval_seconds)
        ?? SETTINGS_DEFAULTS.mall_cam_interval_seconds} s / mouse ${onOff(settings, 'enable_mouse')}`},
    {id: 'cost', title: 'cost', count: 5,
      summary: SPEND_CAP_KEYS.map(([key, name]) => `${name} ${moneyText(eff[key])}`).join(' / ')
        + ` / card total ${eff.card_total_budget_usd === null ? 'none' : moneyText(eff.card_total_budget_usd)}`},
    {id: 'models', title: 'models and limits', count: 6,
      summary: `on limit: ${usageLimitWord(settings)} / reviewer reads ${settings.reviewer_input === 'code' ? 'code' : 'diff'}`
        + ` / ${rolesSet} of 4 roles set`},
    {id: 'paths', title: 'paths and data', count: 3,
      summary: `new repos in ${settings.repos_home || 'your home folder'} / ${paths} readable ${paths === 1 ? 'path' : 'paths'}`},
    {id: 'advanced', title: 'advanced', count: 3,
      summary: `findings ${settings.findings_route || 'per card'} / briefing ${onOff(settings, 'resume_briefing')}`
        + ` / gate timeout ${timeout} s`},
  ];
}

// the board panel's three groups
function boardGroups(board, settings) {
  const stored = storedBoard(board);
  const inForce = boardInForce(board, settings);
  const branches = board.off_limit_branches || [];
  return [
    {id: 'landing', title: 'landing', count: 2,
      summary: `merge ${inForce.merge_mode} / off-limits ${branches.length ? branches.join(', ') : 'none'}`},
    {id: 'running', title: 'running', count: 3,
      summary: `file lease ${inForce.lease_mode} / run ${inForce.run_mode} / cards ${board.max_parallel || 'no limit'}`},
    {id: 'budget', title: 'budget', count: 1,
      summary: `daily ${board.daily_budget_usd ? moneyText(board.daily_budget_usd) : 'none'}`},
  ].map(group => ({...group, stored}));
}
