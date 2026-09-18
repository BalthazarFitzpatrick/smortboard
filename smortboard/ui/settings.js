// settings panel (o): mission control preferences, opened from the top-right button or the o key.
//
// built from the same modal-backdrop / panel-floating pair as preflight.js and inbox.js - nothing
// here needs arrow/enter/escape hijacked from the document, so it stays out of Menu.
//
// scope toggles, snapshot options and the rest arrive as later cards, each pushing a
// {label, node, onOpen} entry here - onOpen (optional) runs every time the panel opens, so a
// section backed by its own fetch can refresh instead of showing stale data from the last open.
//
// relies on globals board.js already defines: api, apiOrError, reenterIfFocusLost. no new visual
// primitive here - rows and text fields reuse boards.js's own classes (board-row, boards-create-row,
// text-field, toggle), already loaded via boards.css.

const st = {backdrop: null, panel: null, listEl: null};

// auto_switch_profiles is opt-in: "on" rotates credentials on USAGE_LIMIT (smortboard/profiles.py);
// unset (the default) or anything else parks the board until the reset instead, spending nothing
// on another account without being asked. the toggle reads its state from GET /api/settings on
// open (buildAutoSwitchToggle -> loadAutoSwitchToggle) - unset renders as unchecked.
function buildAutoSwitchToggle() {
  const wrap = document.createElement('label');
  wrap.className = 'settings-toggle-row';
  const box = document.createElement('input');
  box.type = 'checkbox';
  box.className = 'settings-auto-switch-checkbox';
  box.checked = false;
  const text = document.createElement('span');
  text.textContent = 'switch credential profiles automatically on a usage limit';
  wrap.append(box, text);

  box.addEventListener('change', async () => {
    box.disabled = true;
    const {ok} = await apiOrError('/api/settings', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({auto_switch_profiles: box.checked ? 'on' : null}),
    });
    box.disabled = false;
    if (!ok) box.checked = !box.checked; // revert on a failed save
  });

  loadAutoSwitchToggle(box);
  return wrap;
}

async function loadAutoSwitchToggle(box) {
  try {
    const settings = await api('/api/settings');
    box.checked = settings.auto_switch_profiles === 'on';
  } catch {
    // leave the default (unchecked) - a failed load is not worth blocking the panel on
  }
}

// enable_mouse: the board is driven from the keyboard, and this turns on the pointer half of it -
// hovering focuses what the arrow keys would (fanning a piled column with it), and right-click
// opens the card menu m opens. off by default; the clicks that always worked are never gated by it
const mouseSetting = {box: null};

function buildMouseToggle() {
  const box = document.createElement('input');
  box.type = 'checkbox';
  box.className = 'settings-enable-mouse-checkbox';
  box.checked = false;

  const row = document.createElement('label');
  row.className = 'settings-toggle-row';
  const text = document.createElement('span');
  text.textContent = 'enable mouse';
  row.append(box, text);

  const note = document.createElement('div');
  note.className = 'field-label';
  note.textContent = 'the keyboard is how the board is driven. with this on, hovering focuses '
    + 'what the arrows would and right-click opens the card menu.';

  box.addEventListener('change', async () => {
    box.disabled = true;
    const {ok} = await apiOrError('/api/settings', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({enable_mouse: box.checked ? 'on' : null}),
    });
    box.disabled = false;
    if (!ok) { box.checked = !box.checked; return; } // revert on a failed save
    setMouseEnabled(box.checked); // live: no reload to start or stop hovering
  });

  const wrap = document.createElement('div');
  wrap.className = 'settings-stack';
  wrap.append(row, note);
  mouseSetting.box = box;
  return wrap;
}

async function loadMouseToggle() {
  try {
    const settings = await api('/api/settings');
    mouseSetting.box.checked = settings.enable_mouse === 'on';
    setMouseEnabled(mouseSetting.box.checked);
  } catch {
    // leave it unchecked - keyboard only is the safe default to fail to
  }
}

const SETTINGS_SECTIONS = [
  {group: 'labs', label: 'credential profiles', node: buildAutoSwitchToggle()},
  {group: 'general', label: 'mouse', node: buildMouseToggle(), onOpen: loadMouseToggle},
];

const roleModels = {node: document.createElement('div')};
roleModels.node.className = 'settings-stack';

function fallbackSummary(refs) {
  if (!refs.length) return 'no fallbacks selected';
  return `${refs.length} selected: ${refs.join(' → ')}`;
}

async function openFallbackPicker(role, initialRefs, anchor, status, onSaved) {
  const catalog = await loadModelCatalog();
  const labs = Object.keys(catalog);
  let selectedLab = initialRefs.map(ref => ref.split('/')[0]).find(lab => catalog[lab])
    || (catalog.anthropic ? 'anthropic' : labs[0]);
  let selectedRefs = [...initialRefs];
  let saved = false;
  let menu = null;

  const buildSections = () => [{
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
        empty: selectedLab ? 'no models' : 'choose a lab',
        items: (catalog[selectedLab]?.models || []).map(model => {
          const ref = `${selectedLab}/${model.id}`;
          return {id: ref, label: model.label, stats: model.tier, on: selectedRefs.includes(ref)};
        }),
        onPick: (item, on) => {
          if (on && !selectedRefs.includes(item.id)) selectedRefs.push(item.id);
          if (!on) selectedRefs = selectedRefs.filter(ref => ref !== item.id);
          anchor.textContent = fallbackSummary(selectedRefs);
          anchor.title = selectedRefs.join('\n');
        },
      },
    ],
  }, {
    kind: 'buttons',
    buttons: [{id: 'save-fallbacks', label: 'save fallbacks', onClick: async openMenu => {
      const {ok, body} = await apiOrError('/api/settings', {method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({[`${role}_cross_lab_fallback`]: selectedRefs})});
      status.textContent = ok ? 'saved' : body?.error || 'could not save fallbacks';
      if (ok) {
        saved = true;
        onSaved([...selectedRefs]);
        openMenu.close();
      }
    }}],
  }];

  menu = new Menu({
    title: `${role} fallback order`,
    persistent: true,
    sections: buildSections(),
    onDismiss: () => {
      if (saved) return;
      anchor.textContent = fallbackSummary(initialRefs);
      anchor.title = initialRefs.join('\n');
    },
  });
  menu.openAt(anchor);
}

async function loadRoleModels() {
  try {
    const settings = await api('/api/settings');
    clearChildren(roleModels.node);
    const roles = ['worker', 'reviewer', 'orchestrator', 'fold'];
    roles.forEach((role, index) => {
      const block = document.createElement('div');
      block.className = 'settings-role-block';
      const heading = document.createElement('div');
      heading.className = 'settings-role-heading';
      const label = document.createElement('span');
      label.className = 'field-label settings-role-name';
      label.textContent = role;
      const status = document.createElement('span');
      status.className = 'boards-status settings-role-status';
      heading.append(label, status);

      const primaryRow = document.createElement('div');
      primaryRow.className = 'settings-role-control';
      const primaryLabel = document.createElement('span');
      primaryLabel.className = 'field-label';
      primaryLabel.textContent = 'primary model';
      const picker = document.createElement('button');
      picker.className = 'toggle role-model';
      picker.dataset.role = role;
      picker.textContent = modelLabel(settings[`${role}_model`], settings[`${role}_lab`]);
      picker.onclick = async () => {
        try {
          await openModelPicker(async (lab, model) => {
            const {ok, body} = await apiOrError('/api/settings', {method: 'PATCH',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({[`${role}_lab`]: lab, [`${role}_model`]: model})});
            if (ok) loadRoleModels();
            else status.textContent = body?.error || 'could not save model';
          }, picker);
        } catch (err) { status.textContent = err.message; }
      };
      primaryRow.append(primaryLabel, picker);

      const fallbackRow = document.createElement('div');
      fallbackRow.className = 'settings-role-control';
      const fallbackLabel = document.createElement('span');
      fallbackLabel.className = 'field-label';
      fallbackLabel.textContent = 'fallback order';
      const fallback = document.createElement('button');
      fallback.className = 'toggle role-fallback settings-role-fallback-trigger';
      fallback.dataset.role = role;
      let fallbackRefs = settings[`${role}_cross_lab_fallback`] || [];
      fallback.textContent = fallbackSummary(fallbackRefs);
      fallback.title = fallbackRefs.join('\n');
      fallback.setAttribute('aria-label', `${role} fallback models in order`);
      fallback.onclick = async () => {
        try {
          await openFallbackPicker(role, fallbackRefs, fallback, status, refs => { fallbackRefs = refs; });
        } catch (err) {
          status.textContent = err.message;
        }
      };
      fallbackRow.append(fallbackLabel, fallback);
      block.append(heading, primaryRow, fallbackRow);
      roleModels.node.appendChild(block);
      if (index < roles.length - 1) {
        const divider = document.createElement('div');
        divider.className = 'h-divider';
        roleModels.node.appendChild(divider);
      }
    });
  } catch (err) {
    roleModels.node.textContent = `could not load models: ${err.message}`;
  }
}
SETTINGS_SECTIONS.push({group: 'labs', label: 'models by role', node: roleModels.node, onOpen: loadRoleModels});

function settingsHazardPlaceholder(text) {
  const box = document.createElement('div');
  box.className = 'hazard-stripes hazard-placeholder';
  const label = document.createElement('span');
  label.className = 'hazard-label';
  label.textContent = text;
  box.appendChild(label);
  return box;
}

// a grid's column captions, as a row whose cells sit in the grid itself
function settingsGridHeader(captions) {
  const row = document.createElement('div');
  row.className = 'settings-grid-row settings-grid-head';
  captions.forEach(text => {
    const cell = document.createElement('span');
    cell.className = 'field-label';
    cell.textContent = text;
    row.appendChild(cell);
  });
  return row;
}

function clearChildren(el) {
  [...el.children].forEach(child => child.remove());
}

// ---- mission control can read: folders mission control's agent may read from, board-wide -------
// mounting them is a separate card (out of scope here) - this only maintains the path list, via
// the same whole-list-replace PATCH /api/settings the other board-wide values already use.

const readPaths = {input: null, listEl: null, statusEl: null};

function renderReadPathRow(path) {
  const row = document.createElement('div');
  row.className = 'board-row';
  const label = document.createElement('span');
  label.className = 'board-name field-label';
  label.textContent = path;
  const remove = document.createElement('span');
  remove.className = 'toggle board-delete';
  remove.textContent = 'remove';
  remove.onclick = () => removeReadPath(path);
  row.append(label, remove);
  return row;
}

async function currentReadPaths() {
  const settings = await api('/api/settings');
  return settings.mission_control_read_paths || [];
}

async function loadReadPaths() {
  let paths;
  try {
    paths = await currentReadPaths();
  } catch (err) {
    clearChildren(readPaths.listEl);
    readPaths.listEl.appendChild(settingsHazardPlaceholder(`could not load: ${err.message}`));
    return;
  }
  clearChildren(readPaths.listEl);
  if (!paths.length) {
    readPaths.listEl.appendChild(settingsHazardPlaceholder('no folders yet'));
    return;
  }
  paths.forEach(path => readPaths.listEl.appendChild(renderReadPathRow(path)));
}

async function patchReadPaths(paths) {
  return apiOrError('/api/settings', {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mission_control_read_paths: paths}),
  });
}

async function addReadPath() {
  const raw = readPaths.input.value.trim();
  if (!raw) return;
  readPaths.statusEl.textContent = 'adding...';
  readPaths.statusEl.className = 'boards-status';
  const existing = await currentReadPaths();
  const {ok, body} = await patchReadPaths([...existing, raw]);
  if (!ok) {
    readPaths.statusEl.textContent = (body && body.error) || `could not add ${raw}`;
    readPaths.statusEl.className = 'boards-status boards-error';
    return;
  }
  readPaths.input.value = '';
  readPaths.statusEl.textContent = '';
  await loadReadPaths();
}

async function removeReadPath(path) {
  const existing = await currentReadPaths();
  const {ok, body} = await patchReadPaths(existing.filter(p => p !== path));
  if (!ok) {
    readPaths.statusEl.textContent = (body && body.error) || `could not remove ${path}`;
    readPaths.statusEl.className = 'boards-status boards-error';
    return;
  }
  await loadReadPaths();
}

function buildReadPathsSection() {
  const box = document.createElement('div');
  box.className = 'settings-stack';

  const list = document.createElement('div');
  list.className = 'boards-list';

  const addRow = document.createElement('div');
  addRow.className = 'boards-create-row';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'board-name-input text-field';
  input.placeholder = '~/Documents/screenshots';
  const add = document.createElement('span');
  add.className = 'toggle';
  add.textContent = 'add';
  add.onclick = () => addReadPath();
  // the same folder picker boards use, so a path is chosen rather than typed
  const browse = document.createElement('span');
  browse.className = 'toggle';
  browse.textContent = 'browse';
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
    onError: text => {
      readPaths.statusEl.textContent = text;
      readPaths.statusEl.className = 'boards-status boards-error';
    },
  });
  const status = document.createElement('span');
  status.className = 'boards-status';
  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(evt.target); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    addReadPath();
  });
  addRow.append(input, browse, add, status);

  box.append(list, addRow);
  Object.assign(readPaths, {input, listEl: list, statusEl: status});
  return box;
}

SETTINGS_SECTIONS.push({
  group: 'general',
  label: 'mission control can read',
  node: buildReadPathsSection(),
  onOpen: loadReadPaths,
});

// ---- how many cards run at once: one global cap, plus an optional cap per board ----------------
// the global number is the one seat count shared across every board (scheduler.py's runs.active());
// a board's own number only ever holds it back further, never past the global cap - an unset board
// row behaves exactly like today, no board-specific limit at all.

const parallelCaps = {globalInput: null, globalStatus: null, boardsList: null};

function parallelParseInput(raw) {
  const trimmed = raw.trim();
  if (!trimmed) return null; // empty means "no limit" / "use the global default"
  const value = Number(trimmed);
  return Number.isInteger(value) && value > 0 ? value : undefined; // undefined marks it invalid
}

function buildGlobalParallelRow() {
  const row = document.createElement('div');
  row.className = 'boards-create-row';
  const input = document.createElement('input');
  input.type = 'text';
  input.inputMode = 'numeric';
  input.className = 'board-name-input text-field settings-parallel-input';
  input.placeholder = String(2); // scheduler.DEFAULT_MAX_PARALLEL, kept in sync by eye
  const status = document.createElement('span');
  status.className = 'boards-status';

  async function save() {
    const value = parallelParseInput(input.value);
    if (value === undefined) {
      status.textContent = 'must be a positive whole number';
      status.className = 'boards-status boards-error';
      return;
    }
    const {ok, body} = await apiOrError('/api/settings', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({max_parallel: value}),
    });
    status.textContent = ok ? 'saved' : (body && body.error) || 'could not save';
    status.className = ok ? 'boards-status' : 'boards-status boards-error';
  }

  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(evt.target); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    save();
  });
  input.addEventListener('blur', save);

  row.append(input, status);
  Object.assign(parallelCaps, {globalInput: input, globalStatus: status});
  return row;
}

// daily_budget_usd: a board's own spend cap, in usd, checked against today's (UTC) run cost -
// blank means no cap, same empty-is-unset convention as the parallel cap beside it
function budgetParseInput(raw) {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) && value > 0 ? value : undefined; // undefined marks it invalid
}

function renderBoardParallelRow(board) {
  const row = document.createElement('div');
  row.className = 'board-row settings-grid-row';
  const label = document.createElement('span');
  label.className = 'board-name field-label';
  label.textContent = board.name;
  label.title = board.name;
  const input = document.createElement('input');
  input.type = 'text';
  input.inputMode = 'numeric';
  input.className = 'text-field settings-parallel-input';
  input.placeholder = 'no limit';
  input.value = board.max_parallel == null ? '' : String(board.max_parallel);
  const status = document.createElement('span');
  status.className = 'boards-status';

  async function save() {
    const value = parallelParseInput(input.value);
    if (value === undefined) {
      status.textContent = 'must be a positive whole number, or empty for no limit';
      status.className = 'boards-status boards-error';
      return;
    }
    const {ok, body} = await apiOrError(`/api/boards/${board.id}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({max_parallel: value}),
    });
    status.textContent = ok ? '' : (body && body.error) || 'could not save';
    status.className = ok ? 'boards-status' : 'boards-status boards-error';
  }

  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(evt.target); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    save();
  });
  input.addEventListener('blur', save);

  // the status spans the whole grid row and takes no room while empty
  status.classList.add('settings-grid-note');
  row.append(label, input, status);
  return row;
}

async function loadParallelSection() {
  try {
    const settings = await api('/api/settings');
    parallelCaps.globalInput.value = settings.max_parallel == null ? '' : String(settings.max_parallel);
    parallelCaps.globalStatus.textContent = '';
  } catch (err) {
    parallelCaps.globalStatus.textContent = `could not load: ${err.message}`;
    parallelCaps.globalStatus.className = 'boards-status boards-error';
  }
  try {
    const boards = await api('/api/boards');
    clearChildren(parallelCaps.boardsList);
    parallelCaps.boardsList.appendChild(settingsGridHeader(['board', 'cards at once']));
    boards.forEach(b => parallelCaps.boardsList.appendChild(renderBoardParallelRow(b)));
  } catch (err) {
    clearChildren(parallelCaps.boardsList);
    parallelCaps.boardsList.appendChild(settingsHazardPlaceholder(`could not load boards: ${err.message}`));
  }
}

function buildParallelSection() {
  const box = document.createElement('div');
  box.className = 'settings-stack';
  const globalLabel = document.createElement('div');
  globalLabel.className = 'field-label';
  globalLabel.textContent = 'global (shared across every board)';
  const boardsLabel = document.createElement('div');
  boardsLabel.className = 'field-label';
  boardsLabel.textContent = 'per board (blank = no board-specific limit)';
  const boardsList = document.createElement('div');
  boardsList.className = 'settings-grid settings-grid-two';
  Object.assign(parallelCaps, {boardsList});
  box.append(globalLabel, buildGlobalParallelRow(), boardsLabel, boardsList);
  return box;
}

SETTINGS_SECTIONS.push({
  group: 'general',
  label: 'how many cards run at once',
  node: buildParallelSection(),
  onOpen: loadParallelSection,
});

// ---- daily budgets: each board's total spend cap for the current utc day -----------------------

const dailyBudgets = {boardsList: null};

function renderBoardBudgetRow(board) {
  const row = document.createElement('div');
  row.className = 'board-row settings-grid-row';
  const label = document.createElement('span');
  label.className = 'board-name field-label';
  label.textContent = board.name;
  label.title = board.name;
  const input = document.createElement('input');
  input.type = 'text';
  input.inputMode = 'decimal';
  input.className = 'text-field settings-budget-input';
  input.placeholder = 'no budget';
  input.value = board.daily_budget_usd == null ? '' : String(board.daily_budget_usd);
  const status = document.createElement('span');
  status.className = 'boards-status settings-grid-note';

  async function save() {
    const value = budgetParseInput(input.value);
    if (value === undefined) {
      status.textContent = 'must be a positive amount, or empty for no cap';
      status.className = 'boards-status boards-error settings-grid-note';
      return;
    }
    const {ok, body} = await apiOrError(`/api/boards/${board.id}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({daily_budget_usd: value}),
    });
    status.textContent = ok ? '' : (body && body.error) || 'could not save';
    status.className = ok ? 'boards-status settings-grid-note' : 'boards-status boards-error settings-grid-note';
  }

  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(evt.target); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    save();
  });
  input.addEventListener('blur', save);
  row.append(label, input, status);
  return row;
}

async function loadDailyBudgets() {
  try {
    const boards = await api('/api/boards');
    clearChildren(dailyBudgets.boardsList);
    dailyBudgets.boardsList.appendChild(settingsGridHeader(['board', 'daily $']));
    boards.forEach(board => dailyBudgets.boardsList.appendChild(renderBoardBudgetRow(board)));
  } catch (err) {
    clearChildren(dailyBudgets.boardsList);
    dailyBudgets.boardsList.appendChild(settingsHazardPlaceholder(`could not load boards: ${err.message}`));
  }
}

function buildDailyBudgetsSection() {
  const grid = document.createElement('div');
  grid.className = 'settings-grid settings-grid-two';
  dailyBudgets.boardsList = grid;
  return grid;
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
  const row = document.createElement('div');
  row.className = 'settings-grid-row';
  const label = document.createElement('span');
  label.className = 'field-label';
  label.textContent = cap.label;
  const input = document.createElement('input');
  input.type = 'text';
  input.inputMode = 'decimal';
  input.className = 'text-field settings-spend-input';
  input.placeholder = cap.fallback;

  async function save() {
    const value = budgetParseInput(input.value);
    if (value === undefined) {
      spendCaps.statusEl.textContent = `${cap.label}: must be a positive amount, or empty for the default`;
      spendCaps.statusEl.className = 'boards-status boards-error settings-grid-note';
      return;
    }
    const {ok, body} = await apiOrError('/api/settings', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({[cap.key]: value}),
    });
    spendCaps.statusEl.textContent = ok ? '' : (body && body.error) || 'could not save';
    spendCaps.statusEl.className = ok ? 'boards-status settings-grid-note' : 'boards-status boards-error settings-grid-note';
  }

  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(evt.target); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    save();
  });
  input.addEventListener('blur', save);
  spendCaps.inputs.set(cap.key, input);
  row.append(label, input);
  return row;
}

function buildSpendCapsSection() {
  const grid = document.createElement('div');
  grid.className = 'settings-grid settings-grid-two';
  SPEND_CAPS.forEach(cap => grid.appendChild(renderSpendCapRow(cap)));
  const status = document.createElement('span');
  status.className = 'boards-status settings-grid-note';
  grid.appendChild(status);
  spendCaps.statusEl = status;
  return grid;
}

async function loadSpendCaps() {
  try {
    const settings = await api('/api/settings');
    SPEND_CAPS.forEach(cap => {
      const value = settings[cap.key];
      spendCaps.inputs.get(cap.key).value = value == null ? '' : String(value);
    });
    spendCaps.statusEl.textContent = '';
  } catch (err) {
    spendCaps.statusEl.textContent = `could not load: ${err.message}`;
    spendCaps.statusEl.className = 'boards-status boards-error settings-grid-note';
  }
}

function buildCostControlsSection() {
  const dailyNode = buildDailyBudgetsSection();
  dailyNode.classList.add('settings-cost-grid');
  const spendNode = buildSpendCapsSection();
  spendNode.classList.add('settings-cost-grid');

  const dailyTrigger = document.createElement('button');
  dailyTrigger.className = 'toggle settings-cost-trigger settings-daily-budgets-trigger';
  dailyTrigger.textContent = 'daily budgets per board';
  dailyTrigger.onclick = () => {
    const menu = new Menu({
      title: 'daily budgets per board',
      persistent: true,
      sections: [{kind: 'node', node: dailyNode}],
    });
    menu.openAt(dailyTrigger);
    loadDailyBudgets();
  };

  const spendTrigger = document.createElement('button');
  spendTrigger.className = 'toggle settings-cost-trigger settings-spend-caps-trigger';
  spendTrigger.textContent = 'spend caps per run';
  spendTrigger.onclick = () => {
    const menu = new Menu({
      title: 'spend caps per run',
      persistent: true,
      sections: [{kind: 'node', node: spendNode}],
    });
    menu.openAt(spendTrigger);
    loadSpendCaps();
  };

  const triggers = document.createElement('div');
  triggers.className = 'settings-cost-triggers';
  triggers.append(dailyTrigger, spendTrigger);
  return triggers;
}

SETTINGS_SECTIONS.push({
  group: 'cost',
  label: 'budgets and spend caps',
  node: buildCostControlsSection(),
});

// ---- mall cam interval (cf90bacc): how long the workforce drawer holds each active card before -
// advancing to the next one, when it is cycling rather than pinned to one card. empty means chat.js's
// own default (10s) - same empty-is-default convention buildGlobalParallelRow already uses

const mallCam = {input: null, status: null};

function mallCamParseInput(raw) {
  const trimmed = raw.trim();
  if (!trimmed) return null; // empty means "use the default"
  const value = Number(trimmed);
  return Number.isInteger(value) && value > 0 ? value : undefined; // undefined marks it invalid
}

function buildMallCamSection() {
  const row = document.createElement('div');
  row.className = 'boards-create-row';
  const input = document.createElement('input');
  input.type = 'text';
  input.inputMode = 'numeric';
  input.className = 'board-name-input text-field settings-parallel-input';
  input.placeholder = String(DEFAULT_MALL_CAM_SECONDS);
  const status = document.createElement('span');
  status.className = 'boards-status';

  async function save() {
    const value = mallCamParseInput(input.value);
    if (value === undefined) {
      status.textContent = 'must be a positive whole number';
      status.className = 'boards-status boards-error';
      return;
    }
    const {ok, body} = await apiOrError('/api/settings', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mall_cam_interval_seconds: value}),
    });
    status.textContent = ok ? 'saved' : (body && body.error) || 'could not save';
    status.className = ok ? 'boards-status' : 'boards-status boards-error';
  }

  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(evt.target); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    save();
  });
  input.addEventListener('blur', save);

  row.append(input, status);
  Object.assign(mallCam, {input, status});
  return row;
}

async function loadMallCamSection() {
  try {
    const settings = await api('/api/settings');
    mallCam.input.value = settings.mall_cam_interval_seconds == null ? '' : String(settings.mall_cam_interval_seconds);
    mallCam.status.textContent = '';
  } catch (err) {
    mallCam.status.textContent = `could not load: ${err.message}`;
    mallCam.status.className = 'boards-status boards-error';
  }
}

SETTINGS_SECTIONS.push({
  group: 'general',
  label: 'mall cam: seconds per card while auto-cycling the workforce drawer',
  node: buildMallCamSection(),
  onOpen: loadMallCamSection,
});

function buildSettingsSection(section) {
  const box = document.createElement('div');
  box.className = 'settings-section';
  const label = document.createElement('div');
  label.className = 'field-label';
  label.textContent = section.label;
  box.append(label, section.node);
  return box;
}

const SETTINGS_GROUPS = [
  {id: 'general', label: 'general board settings'},
  {id: 'labs', label: 'labs and models'},
  {id: 'cost', label: 'cost control'},
];

function buildSettingsGroup(group, sections) {
  const box = document.createElement('div');
  box.className = 'settings-group';
  box.dataset.group = group.id;
  const label = document.createElement('div');
  label.className = 'field-label settings-group-title';
  label.textContent = group.label;
  const rule = document.createElement('div');
  rule.className = 'h-divider';
  box.append(label, rule);
  sections.forEach(section => box.appendChild(buildSettingsSection(section)));
  return box;
}

function renderSettings() {
  clearChildren(st.listEl);
  if (!SETTINGS_SECTIONS.length) {
    st.listEl.appendChild(settingsHazardPlaceholder('no preferences yet'));
    return;
  }
  SETTINGS_GROUPS.forEach(group => {
    const sections = SETTINGS_SECTIONS.filter(section => section.group === group.id);
    if (!sections.length) return;
    st.listEl.appendChild(buildSettingsGroup(group, sections));
    sections.forEach(section => { if (section.onOpen) section.onOpen(); });
  });
}

function buildSettingsDom() {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop settings-backdrop';
  const panel = document.createElement('div');
  panel.className = 'panel-floating settings-panel';

  const title = document.createElement('div');
  title.className = 'popup-title';
  title.textContent = 'settings';
  const rule = document.createElement('div');
  rule.className = 'h-divider';

  const list = document.createElement('div');
  list.className = 'settings-list';

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
  renderSettings();
  // the panel takes the keyboard; up/down then walk its fields and / types into one
  focusPanel(st.panel);
}

function closeSettingsPanel() {
  if (!st.backdrop || !st.backdrop.parentNode) return;
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
  const btn = document.createElement('div');
  btn.className = 'toggle settings-button';
  btn.textContent = 'settings';
  btn.title = 'settings (o)';
  btn.onclick = () => toggleSettingsPanel();
  barCorner().appendChild(btn);
  return btn;
}

buildSettingsButton();
