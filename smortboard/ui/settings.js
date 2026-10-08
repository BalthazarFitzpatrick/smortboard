// settings panel (o): mission control preferences, opened from the top-right button or the o key.
//
// built from the same modal-backdrop / panel-floating pair as preflight.js and inbox.js - nothing
// here needs arrow/enter/escape hijacked from the document, so it stays out of Menu.
//
// layout: a preset row, then six independent accordion groups (settings_presets.js generalGroups).
// one GET /api/settings and one GET /api/boards per open feed every control; a save updates that
// snapshot, and the headers, the preset row and the gate notes are redrawn from it.
//
// relies on globals board.js already defines: api, apiOrError, reenterIfFocusLost, loadBoards, and
// on ui_base's segments and disclosure. no new visual primitive here.

const st = {backdrop: null, panel: null, listEl: null};

// the one snapshot every control reads: the settings and the boards, loaded once per open
const snap = {settings: {}, boards: null};
// which groups are open, kept for the session so a reopened panel looks as it was left
let openGroupIds = [];
// per-render registries: disclosures by group id, and one re-read function per control
let groupDisclosures = new Map();
let syncers = [];
const presetUi = {seg: null, lineEl: null, leadEl: null, strongEl: null, statusEl: null};
const gateNotes = new Map();
const offLimitConfirm = {armed: false, lineEl: null, offButton: null};
let renderToken = 0;

function makeNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function clearChildren(el) {
  [...el.children].forEach(child => child.remove());
}

function disarmOffLimit() {
  offLimitConfirm.armed = false;
  if (offLimitConfirm.lineEl) offLimitConfirm.lineEl.hidden = true;
}

// one PATCH /api/settings. a landed save joins the snapshot (the server answers with every setting)
// and redraws the headers; a refused one changes nothing and says why
async function patchSettings(fields) {
  disarmOffLimit();
  const {ok, body} = await apiOrError('/api/settings', {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(fields),
  });
  if (presetUi.statusEl) {
    presetUi.statusEl.textContent = ok ? '' : (body && body.error) || 'could not save';
  }
  if (ok) {
    Object.assign(snap.settings, fields);
    if (body && typeof body === 'object') Object.assign(snap.settings, body);
    refreshHeaders();
  }
  return {ok, body};
}

async function saveSettings(fields) {
  return (await patchSettings(fields)).ok;
}

// ---- controls ---------------------------------------------------------------------------------

// every on/off setting: a two-way on|off segment. polarity lives in settings_presets.js
function buildBooleanToggle(key) {
  const seg = makeSegments({
    options: WORDING.boolean.map(word => ({label: word, value: word === 'on'})),
    value: booleanIsOn(snap.settings, key),
    label: BOOLEAN_SETTINGS[key].label,
    onPick: on => pickBoolean(key, on),
  });
  seg.el.dataset.setting = key;
  syncers.push(() => seg.setLit(booleanIsOn(snap.settings, key)));
  if (key === 'off_limit_branches') offLimitConfirm.offButton = seg.buttons[1];
  return seg;
}

async function pickBoolean(key, on) {
  // lifting every board's lock takes a second click on off
  if (key === 'off_limit_branches' && !on && !offLimitConfirm.armed) {
    offLimitConfirm.armed = true;
    offLimitConfirm.lineEl.hidden = false;
    return false;
  }
  const ok = await saveSettings({[key]: booleanStored(key, on)});
  if (ok && key === 'enable_mouse') setMouseEnabled(on); // live: no reload to start or stop hovering
  return ok;
}

// a choice stored as null / a string: segments cannot hold null as a value, so they carry the word
function buildChoice(key, pairs, label) {
  const wordFor = () => (pairs.find(([, stored]) => stored === (snap.settings[key] ?? null)) || pairs[0])[0];
  const seg = makeSegments({
    options: pairs.map(([word]) => ({label: word, value: word})),
    value: wordFor(),
    label,
    onPick: word => saveSettings({[key]: pairs.find(([w]) => w === word)[1]}),
  });
  seg.el.dataset.setting = key;
  syncers.push(() => seg.setLit(wordFor()));
  return seg;
}

function settingRow(label, control) {
  const row = makeNode('div', 'settings-row');
  row.append(makeNode('span', 'field-label settings-row-name', label), control);
  return row;
}

function bindFieldKeys(input, save) {
  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(evt.target); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    save();
  });
  input.addEventListener('blur', save);
}

function parallelParseInput(raw) {
  const trimmed = raw.trim();
  if (!trimmed) return null; // empty means "no limit" / "use the global default"
  const value = Number(trimmed);
  return Number.isInteger(value) && value > 0 ? value : undefined; // undefined marks it invalid
}

// daily_budget_usd and the spend caps: blank means no cap, same empty-is-unset convention
function budgetParseInput(raw) {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) && value > 0 ? value : undefined; // undefined marks it invalid
}

const mallCamParseInput = parallelParseInput;

// a whole-number field that saves on enter or blur; empty sends null (the default in force)
function buildNumberField({key, parse, placeholder, className}) {
  const input = makeNode('input', `board-name-input text-field ${className}`);
  input.type = 'text';
  input.inputMode = 'numeric';
  input.placeholder = placeholder;
  const status = makeNode('span', 'boards-status');
  const show = () => {
    const value = snap.settings[key];
    input.value = value == null ? '' : String(value);
  };
  show();
  syncers.push(show);

  async function save() {
    const value = parse(input.value);
    if (value === undefined) {
      status.textContent = 'must be a positive whole number';
      status.className = 'boards-status boards-error';
      return;
    }
    if (value === (snap.settings[key] ?? null)) return; // blur fires on every tab past the field
    const {ok, body} = await patchSettings({[key]: value});
    status.textContent = ok ? 'saved' : (body && body.error) || 'could not save';
    status.className = ok ? 'boards-status' : 'boards-status boards-error';
  }
  bindFieldKeys(input, save);

  const row = makeNode('div', 'boards-create-row');
  row.append(input, status);
  return {row, input, status};
}


// ---- fallback picker ------------------------------------------------------------------------

// one fallback as people say it: "openai/gpt-x @ high"
function fallbackText(entry) {
  return entry.effort ? `${entry.ref} @ ${entry.effort}` : entry.ref;
}

function fallbackSummary(entries) {
  if (!entries.length) return 'no fallbacks selected';
  return `${entries.length} selected: ${entries.map(fallbackText).join(' → ')}`;
}

async function openFallbackPicker(role, initialEntries, anchor, status, onSaved) {
  const catalog = await loadModelCatalog();
  const labs = Object.keys(catalog);
  let selectedLab = initialEntries.map(entry => entry.ref.split('/')[0]).find(lab => catalog[lab])
    || (catalog.anthropic ? 'anthropic' : labs[0]);
  let selected = initialEntries.map(entry => ({...entry}));
  // the ref the effort column describes: the model row the arrows sit on, or the last one ticked
  let focusedRef = selected.length ? selected[selected.length - 1].ref : null;
  let saved = false;
  let menu = null;

  const showSummary = () => {
    anchor.textContent = fallbackSummary(selected);
    anchor.title = selected.map(fallbackText).join('\n');
  };
  const focusedEntry = () => selected.find(entry => entry.ref === focusedRef) || null;

  // an effort saved before the model's levels changed, or never saved, restarts at the model's start
  // effort so a re-save never sends a stale one and no entry is left without one
  const clearStaleEfforts = () => {
    for (const entry of selected) {
      const [lab, model] = entry.ref.split('/');
      const row = catalog[lab]?.models.find(row => row.id === model);
      if (row && !modelEffortLevels(row).includes(entry.effort)) entry.effort = startEffort(row);
    }
  };
  clearStaleEfforts();

  const buildSections = () => {
    const entry = focusedEntry();
    const [lab, model] = entry?.ref.split('/') || [];
    const row = catalog[lab]?.models.find(row => row.id === model);
    const effort = effortColumn(entry?.effort, level => {
      entry.effort = level;
      showSummary();
      menu.refresh(buildSections());
    }, entry ? `effort: ${entry.ref}` : 'effort', row);
    if (!entry) {
      effort.items = [];
      effort.empty = 'tick a model';
    }
    return [{
      kind: 'columns',
      columns: [
        {
          label: 'lab',
          multi: false,
          items: labs.map(lab => ({id: lab, label: lab, on: lab === selectedLab})),
          onPick: item => {
            selectedLab = item.id;
            menu.refresh(buildSections());
          },
        },
        {
          label: 'models',
          multi: true,
          items: (catalog[selectedLab]?.models || []).map(model => {
            const ref = `${selectedLab}/${model.id}`;
            return {id: ref, label: model.label, stats: model.tier,
              on: selected.some(entry => entry.ref === ref)};
          }),
          empty: selectedLab ? 'no models' : 'choose a lab',
          onPick: (item, on) => {
            if (on && !selected.some(entry => entry.ref === item.id)) {
              const [pickLab, pickModel] = item.id.split('/');
              const pickRow = catalog[pickLab]?.models.find(row => row.id === pickModel);
              selected.push({ref: item.id, effort: startEffort(pickRow)});
            }
            if (!on) selected = selected.filter(entry => entry.ref !== item.id);
            focusedRef = item.id;
            showSummary();
            menu.refresh(buildSections());
          },
          onFocus: item => {
            focusedRef = item.id;
            menu.refresh(buildSections());
          },
        },
        effort,
      ],
    }, {
      kind: 'buttons',
      buttons: [{id: 'save-fallbacks', label: 'save fallbacks', onClick: async openMenu => {
        const {ok, body} = await patchSettings({[`${role}_cross_lab_fallback`]: selected});
        status.textContent = ok ? 'saved' : body?.error || 'could not save fallbacks';
        if (ok) {
          saved = true;
          onSaved(selected.map(entry => ({...entry})));
          openMenu.close();
        }
      }}],
    }];
  };

  menu = new Menu({
    title: `${role} fallback order`,
    persistent: true,
    sections: buildSections(),
    onDismiss: () => {
      if (saved) return;
      anchor.textContent = fallbackSummary(initialEntries);
      anchor.title = initialEntries.map(fallbackText).join('\n');
    },
  });
  menu.openAt(anchor);
  widenModelPicker(menu);
}

// ---- models by role: the four primary pickers and fallback pickers, launched from buttons -------

const roleModels = {node: null};

function buildRoleModels() {
  const node = makeNode('div', 'settings-stack');
  roleModels.node = node;
  fillRoleModels();
  return node;
}

function fillRoleModels() {
  const settings = snap.settings;
  clearChildren(roleModels.node);
  const roles = ['worker', 'reviewer', 'orchestrator', 'fold'];
  roles.forEach((role, index) => {
    const block = makeNode('div', 'settings-role-block');
    const heading = makeNode('div', 'settings-role-heading');
    const status = makeNode('span', 'boards-status settings-role-status');
    heading.append(makeNode('span', 'field-label settings-role-name', role), status);

    const primaryRow = makeNode('div', 'settings-role-control');
    const picker = makeNode('button', 'toggle role-model');
    picker.type = 'button';
    picker.dataset.role = role;
    const roleEffort = settings[`${role}_effort`];
    picker.textContent = modelLabel(settings[`${role}_model`], settings[`${role}_lab`])
      + (roleEffort ? ` @ ${roleEffort}` : '');
    picker.onclick = async () => {
      try {
        await openModelPicker(async (lab, model, effort) => {
          const {ok, body} = await patchSettings({[`${role}_lab`]: lab, [`${role}_model`]: model,
            [`${role}_effort`]: effort});
          if (ok) fillRoleModels();
          else status.textContent = body?.error || 'could not save model';
        }, picker, null, {lab: settings[`${role}_lab`], model: settings[`${role}_model`],
          effort: roleEffort, default_settings: settings, default_role: role});
      } catch (err) { status.textContent = err.message; }
    };
    primaryRow.append(makeNode('span', 'field-label', 'primary model'), picker);

    const fallbackRow = makeNode('div', 'settings-role-control');
    const fallback = makeNode('button', 'toggle role-fallback settings-role-fallback-trigger');
    fallback.type = 'button';
    fallback.dataset.role = role;
    let fallbackRefs = settings[`${role}_cross_lab_fallback`] || [];
    fallback.textContent = fallbackSummary(fallbackRefs);
    fallback.title = fallbackRefs.map(fallbackText).join('\n');
    fallback.setAttribute('aria-label', `${role} fallback models in order`);
    fallback.onclick = async () => {
      try {
        await openFallbackPicker(role, fallbackRefs, fallback, status, refs => { fallbackRefs = refs; });
      } catch (err) {
        status.textContent = err.message;
      }
    };
    fallbackRow.append(makeNode('span', 'field-label', 'fallback order'), fallback);
    block.append(heading, primaryRow, fallbackRow);
    roleModels.node.appendChild(block);
    if (index < roles.length - 1) roleModels.node.appendChild(makeNode('div', 'h-divider'));
  });
}

function settingsHazardPlaceholder(text) {
  const box = makeNode('div', 'hazard-stripes hazard-placeholder');
  box.appendChild(makeNode('span', 'hazard-label', text));
  return box;
}

// ---- mission control can read: folders mission control's agent may read from, board-wide -------
// mounting them is a separate card - this only maintains the path list, via the same
// whole-list-replace PATCH /api/settings the other board-wide values already use.

const readPaths = {input: null, listEl: null, statusEl: null};

function renderReadPathRow(path) {
  const row = makeNode('div', 'board-row');
  const remove = makeNode('button', 'toggle board-delete', 'remove');
  remove.type = 'button';
  remove.onclick = () => removeReadPath(path);
  row.append(makeNode('span', 'board-name field-label', path), remove);
  return row;
}

function currentReadPaths() {
  return snap.settings.mission_control_read_paths || [];
}

function renderReadPaths() {
  clearChildren(readPaths.listEl);
  const paths = currentReadPaths();
  if (!paths.length) {
    readPaths.listEl.appendChild(settingsHazardPlaceholder('no folders yet'));
    return;
  }
  paths.forEach(path => readPaths.listEl.appendChild(renderReadPathRow(path)));
}

function showReadPathError(text) {
  readPaths.statusEl.textContent = text;
  readPaths.statusEl.className = 'boards-status boards-error';
}

async function addReadPath() {
  const raw = readPaths.input.value.trim();
  if (!raw) return;
  readPaths.statusEl.textContent = 'adding...';
  readPaths.statusEl.className = 'boards-status';
  const {ok, body} = await patchSettings({mission_control_read_paths: [...currentReadPaths(), raw]});
  if (!ok) { showReadPathError((body && body.error) || `could not add ${raw}`); return; }
  readPaths.input.value = '';
  readPaths.statusEl.textContent = '';
  renderReadPaths();
}

async function removeReadPath(path) {
  const {ok, body} = await patchSettings({
    mission_control_read_paths: currentReadPaths().filter(p => p !== path)});
  if (!ok) { showReadPathError((body && body.error) || `could not remove ${path}`); return; }
  renderReadPaths();
}

function buildReadPathsSection() {
  const box = makeNode('div', 'settings-stack');
  const list = makeNode('div', 'boards-list');
  const addRow = makeNode('div', 'boards-create-row');
  const input = makeNode('input', 'board-name-input text-field');
  input.type = 'text';
  input.placeholder = '~/Documents/screenshots';
  const add = makeNode('button', 'toggle', 'add');
  add.type = 'button';
  add.onclick = () => addReadPath();
  // the same folder picker boards use, so a path is chosen rather than typed
  const browse = makeNode('button', 'toggle', 'browse');
  browse.type = 'button';
  browse.onclick = () => openFolderPicker(browse, {
    title: 'a folder mission control can read',
    action: {
      label: 'add this folder',
      when: () => true,
      run: async (path, menu) => {
        menu.close();
        readPaths.input.value = path;
        await addReadPath();
      },
    },
    onError: showReadPathError,
  });
  const status = makeNode('span', 'boards-status');
  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(evt.target); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    addReadPath();
  });
  addRow.append(input, browse, add, status);
  box.append(list, addRow);
  Object.assign(readPaths, {input, listEl: list, statusEl: status});
  renderReadPaths();
  return box;
}

// ---- where new repos go: the folder new board and from online repo open in ----------------------
// blank means the home folder. the server checks it exists and stores it absolute (~ expanded)

const reposHome = {input: null, status: null, saved: ''};

async function saveReposHome() {
  const raw = reposHome.input.value.trim();
  // blur fires on every tab past the field - only a changed value is worth a save
  if (raw === reposHome.saved) return;
  const {ok, body} = await patchSettings({repos_home: raw || null});
  reposHome.status.textContent = ok ? 'saved' : (body && body.error) || 'could not save';
  reposHome.status.className = ok ? 'boards-status' : 'boards-status boards-error';
  if (!ok) return;
  reposHome.saved = body.repos_home || '';
  reposHome.input.value = reposHome.saved;
}

function buildReposHomeSection() {
  const row = makeNode('div', 'boards-create-row');
  const input = makeNode('input', 'board-name-input text-field settings-repos-home-input');
  input.type = 'text';
  input.placeholder = 'home folder';
  const browse = makeNode('button', 'toggle', 'browse');
  browse.type = 'button';
  const status = makeNode('span', 'boards-status');
  Object.assign(reposHome, {input, status, saved: snap.settings.repos_home || ''});
  input.value = reposHome.saved;
  browse.onclick = () => openFolderPicker(browse, {
    title: 'where new repos go',
    start: 'repos',
    action: {
      label: 'use this folder',
      when: () => true,
      run: async (path, menu) => {
        menu.close();
        reposHome.input.value = path;
        await saveReposHome();
      },
    },
    onError: text => {
      reposHome.status.textContent = text;
      reposHome.status.className = 'boards-status boards-error';
    },
  });
  bindFieldKeys(input, saveReposHome);
  row.append(input, browse, status);
  const note = makeNode('div', 'field-label',
    'new board and from online repo start here. any folder is still one step away.');
  const box = makeNode('div', 'settings-stack');
  box.append(row, note);
  return box;
}

// ---- spend caps: the most one run of each role may spend, in usd --------------------------------
// blank means the role's own default (the placeholder); a run reads its cap when it starts

const SPEND_CAPS = [
  {key: 'worker_budget_usd', label: 'card run (worker)', fallback: '5.00'},
  {key: 'reviewer_budget_usd', label: 'review', fallback: '1.50'},
  {key: 'orchestrator_budget_usd', label: 'mission control turn', fallback: '1.00'},
  {key: 'fold_budget_usd', label: 'fold', fallback: '2.00'},
  {key: 'card_total_budget_usd', label: 'card total (all runs)', fallback: 'no limit'},
];
const spendCaps = {inputs: new Map(), statusEl: null};

function renderSpendCapRow(cap) {
  const row = makeNode('div', 'settings-grid-row');
  const input = makeNode('input', 'text-field settings-spend-input');
  input.type = 'text';
  input.inputMode = 'decimal';
  input.placeholder = cap.fallback;
  const show = () => {
    const value = snap.settings[cap.key];
    input.value = value == null ? '' : String(value);
  };
  show();
  syncers.push(show);

  async function save() {
    const value = budgetParseInput(input.value);
    if (value === undefined) {
      spendCaps.statusEl.textContent = `${cap.label}: must be a positive amount, or empty for the default`;
      spendCaps.statusEl.className = 'boards-status boards-error settings-grid-note';
      return;
    }
    if (value === (snap.settings[cap.key] ?? null)) return;
    const {ok, body} = await patchSettings({[cap.key]: value});
    spendCaps.statusEl.textContent = ok ? '' : (body && body.error) || 'could not save';
    spendCaps.statusEl.className = ok ? 'boards-status settings-grid-note' : 'boards-status boards-error settings-grid-note';
  }
  bindFieldKeys(input, save);
  spendCaps.inputs.set(cap.key, input);
  row.append(makeNode('span', 'field-label', cap.label), input);
  return row;
}

function buildSpendCapsSection() {
  spendCaps.inputs = new Map();
  const grid = makeNode('div', 'settings-grid settings-grid-two settings-cost-grid');
  SPEND_CAPS.forEach(cap => grid.appendChild(renderSpendCapRow(cap)));
  const status = makeNode('span', 'boards-status settings-grid-note');
  grid.appendChild(status);
  spendCaps.statusEl = status;
  const note = makeNode('div', 'field-label', 'a board\'s own daily budget is on its panel, shift+o.');
  const box = makeNode('div', 'settings-stack');
  box.append(grid, note);
  return box;
}

// ---- backup: every board as one file, and a file brought back in beside them -------------------
// import only ever adds: the file's boards land next to the ones here under fresh ids, so nothing
// on the board is replaced. a restore under the original ids is `smortboard import`, into an empty db

const backup = {status: null};

function showBackupStatus(text, failed = false) {
  backup.status.textContent = text;
  backup.status.className = failed ? 'boards-status boards-error' : 'boards-status';
}

// a same-origin link with download set: the browser streams the file to disk under the server's
// name, and the page stays where it is
function exportBackup() {
  const link = document.createElement('a');
  link.href = '/api/export';
  link.download = '';
  document.body.appendChild(link);
  link.click();
  link.remove();
  showBackupStatus('export started - your browser saves the file');
}

// the picker stays detached, so up/down in the panel never walks onto a hidden file input
function pickBackupFile() {
  const picker = document.createElement('input');
  picker.type = 'file';
  picker.accept = '.json,application/json';
  picker.addEventListener('change', () => {
    if (picker.files && picker.files.length) importBackup(picker.files[0]);
  });
  picker.click();
}

async function importBackup(file) {
  showBackupStatus(`importing ${file.name}...`);
  const form = new FormData();
  form.append('bundle', file, file.name);
  const {ok, body} = await apiOrError('/api/import', {method: 'POST', body: form});
  if (!ok) {
    showBackupStatus((body && body.error) || `could not import ${file.name}`, true);
    return;
  }
  const names = body.boards.map(board => board.name).join(', ');
  showBackupStatus(`added ${body.boards.length} board(s): ${names}`);
  await loadBoards();
}

function backupButton(text, className, run) {
  const btn = makeNode('button', `toggle ${className}`, text);
  btn.type = 'button';
  btn.onclick = run;
  return btn;
}

function buildBackupSection() {
  const row = makeNode('div', 'run-controls');
  const status = makeNode('span', 'boards-status');
  row.append(
    backupButton('export', 'settings-backup-export', exportBackup),
    backupButton('import as new board(s)', 'settings-backup-import', pickBackupFile),
    status,
  );
  const note = makeNode('div', 'field-label', 'import adds the boards in a file beside yours, under new '
    + 'ids - it never replaces one. a full restore is smortboard import, into an empty database.');
  const wrap = makeNode('div', 'settings-stack');
  wrap.append(row, note);
  backup.status = status;
  return wrap;
}

// ---- the groups ---------------------------------------------------------------------------------

const parallelCaps = {globalInput: null, globalStatus: null};
const mallCam = {input: null, status: null};
const gateTimeout = {input: null, status: null};

// "used by 2 of 3 boards" under a gate; nothing while the boards could not be read
function gateNoteText(key) {
  const usage = snap.boards ? gateUsage(snap.boards, snap.settings)[key] : null;
  return usage ? `used by ${usage.used} of ${usage.total} ${usage.total === 1 ? 'board' : 'boards'}` : '';
}

const GATE_KEYS = ['allow_soft_leases', 'allow_free_merge', 'allow_open_mode'];

function buildGateRow(key) {
  const seg = buildBooleanToggle(key);
  const box = makeNode('div', 'settings-stack settings-gate');
  box.append(settingRow(BOOLEAN_SETTINGS[key].label, seg.el));
  if (GATE_KEYS.includes(key)) {
    const note = makeNode('div', 'field-label settings-gate-note', gateNoteText(key));
    gateNotes.set(key, note);
    box.appendChild(note);
  }
  if (key === 'allow_open_mode') {
    box.appendChild(makeNode('div', 'field-label',
      'open mode is weaker than sealed: cards run on your machine, not in a container.'));
  }
  if (key === 'off_limit_branches') {
    const line = makeNode('div', 'boards-status boards-error settings-confirm-line',
      'this lifts the lock on every board - click off again to confirm');
    line.hidden = true;
    offLimitConfirm.lineEl = line;
    box.appendChild(line);
  }
  return box;
}

function buildSafetyBody() {
  const body = makeNode('div', 'settings-stack');
  ['allow_soft_leases', 'allow_free_merge', 'allow_open_mode', 'off_limit_branches']
    .forEach(key => body.appendChild(buildGateRow(key)));
  return body;
}

function buildCapacityBody() {
  const body = makeNode('div', 'settings-stack');
  const parallel = buildNumberField({key: 'max_parallel', parse: parallelParseInput,
    placeholder: String(SETTINGS_DEFAULTS.max_parallel), className: 'settings-parallel-input'});
  Object.assign(parallelCaps, {globalInput: parallel.input, globalStatus: parallel.status});
  const cam = buildNumberField({key: 'mall_cam_interval_seconds', parse: mallCamParseInput,
    placeholder: String(DEFAULT_MALL_CAM_SECONDS), className: 'settings-parallel-input'});
  Object.assign(mallCam, {input: cam.input, status: cam.status});
  body.append(
    settingRow('cards at once', parallel.row),
    makeNode('div', 'field-label', 'across every board. a board can hold itself lower in shift+o.'),
    settingRow('mall cam interval (seconds per card)', cam.row),
    settingRow('mouse', buildBooleanToggle('enable_mouse').el),
    makeNode('div', 'field-label', 'on: hovering focuses what the arrows would and right-click opens the card menu.'),
  );
  return body;
}

function buildModelsBody() {
  const body = makeNode('div', 'settings-stack');
  // usage_limit_route: wait parks the board until the window resets; ask me asks in the inbox (n)
  // before a fallback model; switch moves to the next credential profile, then the fallback model
  const usage = buildChoice('usage_limit_route', WORDING.usageLimit, 'usage limits');
  usage.buttons.forEach(btn => btn.classList.add('settings-usage-limit-choice'));
  // reviewer_input: null hands the reviewer the diff, "code" the changed files as they stand
  const reviewer = buildChoice('reviewer_input', WORDING.reviewerReads, 'reviewer reads');
  reviewer.buttons.forEach(btn => btn.classList.add('settings-reviewer-input-choice'));
  body.append(
    settingRow('usage limits', usage.el),
    makeNode('div', 'field-label', 'wait: the board parks until the limit resets. ask me: the card asks '
      + 'in the inbox (n) before a fallback model. switch: your next credential profile, then the fallback.'),
    settingRow('reviewer reads', reviewer.el),
    makeNode('div', 'h-divider'),
    buildRoleModels(),
  );
  return body;
}

function buildPathsBody() {
  const body = makeNode('div', 'settings-stack');
  body.append(
    makeNode('div', 'field-label', 'where new repos go'), buildReposHomeSection(),
    makeNode('div', 'field-label', 'mission control can read'), buildReadPathsSection(),
    makeNode('div', 'field-label', 'backup'), buildBackupSection(),
  );
  showBackupStatus('');
  return body;
}

function buildAdvancedBody() {
  const body = makeNode('div', 'settings-stack');
  const route = buildChoice('findings_route', WORDING.findingsRoute, 'findings route');
  const timeout = buildNumberField({key: 'gate_timeout_seconds', parse: parallelParseInput,
    placeholder: String(SETTINGS_DEFAULTS.gate_timeout_seconds), className: 'settings-gate-timeout-input'});
  Object.assign(gateTimeout, {input: timeout.input, status: timeout.status});
  body.append(
    settingRow('findings route', route.el),
    settingRow('resume briefing', buildBooleanToggle('resume_briefing').el),
    settingRow('gate timeout (seconds)', timeout.row),
  );
  return body;
}

const GROUP_BUILDERS = {
  safety: buildSafetyBody,
  capacity: buildCapacityBody,
  cost: buildSpendCapsSection,
  models: buildModelsBody,
  paths: buildPathsBody,
  advanced: buildAdvancedBody,
};

// ---- the preset row -----------------------------------------------------------------------------

function buildPresetRow() {
  const match = matchGeneralPreset(snap.settings);
  const options = GENERAL_PRESETS.map(p => ({label: p.label, value: p.id}));
  options.push({label: 'custom', value: 'custom', auto: true});
  const seg = makeSegments({options, value: match.id, label: 'mode preset', onPick: pickPreset});
  seg.el.classList.add('settings-preset-segments');
  const lead = makeNode('span', 'settings-preset-lead', match.line.lead);
  const strong = makeNode('strong', 'settings-preset-strong', match.line.strong);
  const line = makeNode('div', 'field-label settings-preset-line');
  line.append(lead, strong);
  const status = makeNode('span', 'boards-status boards-error settings-save-status');
  Object.assign(presetUi, {seg, lineEl: line, leadEl: lead, strongEl: strong, statusEl: status});
  const box = makeNode('div', 'settings-stack settings-preset');
  box.append(settingRow('mode preset', seg.el), line, status);
  return box;
}

// one atomic PATCH, then every control re-reads the snapshot and the row relights
async function pickPreset(id) {
  const ok = await saveSettings(generalPresetPatch(id));
  if (ok) syncers.forEach(sync => sync());
  return ok;
}

// summaries, counts, the preset row and the gate notes, all from the current snapshot
function refreshHeaders() {
  generalGroups(snap.settings).forEach(group => {
    const disclosure = groupDisclosures.get(group.id);
    if (!disclosure) return;
    disclosure.setSummary(group.summary);
    disclosure.setCount(group.count);
  });
  if (presetUi.seg) {
    const match = matchGeneralPreset(snap.settings);
    presetUi.seg.setLit(match.id);
    presetUi.leadEl.textContent = match.line.lead;
    presetUi.strongEl.textContent = match.line.strong;
  }
  gateNotes.forEach((note, key) => { note.textContent = gateNoteText(key); });
}

function buildSettingsBody() {
  groupDisclosures = new Map();
  syncers = [];
  gateNotes.clear();
  const items = generalGroups(snap.settings).map(group => {
    const disclosure = makeDisclosure({
      title: group.title, summary: group.summary, count: group.count,
      open: openGroupIds.includes(group.id),
      body: GROUP_BUILDERS[group.id](),
      onToggle: () => { openGroupIds = groupSet.openIds(); },
    });
    groupDisclosures.set(group.id, disclosure);
    return {id: group.id, disclosure};
  });
  const groupSet = makeDisclosureGroup(items);
  const wrap = makeNode('div', 'settings-stack settings-accordion');
  wrap.append(buildPresetRow(), groupSet.el);
  return wrap;
}

async function renderSettings() {
  const token = ++renderToken;
  clearChildren(st.listEl);
  try {
    const [settings, boardList] = await Promise.all([
      api('/api/settings'),
      api('/api/boards').catch(() => null), // the gate notes just stay blank without boards
    ]);
    if (token !== renderToken) return;
    snap.settings = settings;
    snap.boards = Array.isArray(boardList) ? boardList : null;
  } catch (err) {
    if (token === renderToken) st.listEl.appendChild(settingsHazardPlaceholder(`could not load: ${err.message}`));
    return;
  }
  setMouseEnabled(booleanIsOn(snap.settings, 'enable_mouse'));
  st.listEl.appendChild(buildSettingsBody());
}

function buildSettingsDom() {
  const backdrop = makeNode('div', 'modal-backdrop settings-backdrop');
  const panel = makeNode('div', 'panel-floating settings-panel');
  const title = makeNode('div', 'popup-title', 'settings');
  const rule = makeNode('div', 'h-divider');
  const list = makeNode('div', 'settings-list');
  // any click but the second one on off cancels a pending off-limit confirm
  list.addEventListener('click', evt => {
    if (evt.target !== offLimitConfirm.offButton) disarmOffLimit();
  });

  panel.append(title, rule, list);
  backdrop.appendChild(panel);
  backdrop.addEventListener('mousedown', evt => { if (evt.target === backdrop) closeSettingsPanel(); });

  Object.assign(st, {backdrop, panel, listEl: list});
  return backdrop;
}

function onSettingsKey(evt) {
  if (evt.code === 'Escape') closeSettingsPanel();
}

function openSettingsPanel() {
  if (!st.backdrop) buildSettingsDom();
  document.body.appendChild(st.backdrop);
  document.addEventListener('keydown', onSettingsKey);
  disarmOffLimit();
  renderSettings();
  // the panel takes the keyboard; up/down then walk its fields and / types into one
  focusPanel(st.panel);
}

function closeSettingsPanel() {
  if (!st.backdrop || !st.backdrop.parentNode) return;
  // a submenu goes with its panel - left open it hung over the board with nothing under it
  Menu.closeOpen?.();
  document.removeEventListener('keydown', onSettingsKey);
  st.backdrop.remove();
  reenterIfFocusLost();
}

function toggleSettingsPanel() {
  if (st.backdrop && st.backdrop.parentNode) closeSettingsPanel();
  else openSettingsPanel();
}

// ---- top-right button, fixed to the viewport --------------------------------------------------
// lives in board.js's bar corner, between the queue status and the attention count

function buildSettingsButton() {
  const btn = makeNode('button', 'toggle settings-button', 'settings');
  btn.type = 'button';
  btn.title = 'settings (o)';
  btn.onclick = () => toggleSettingsPanel();
  barCorner().appendChild(btn);
  return btn;
}

buildSettingsButton();
