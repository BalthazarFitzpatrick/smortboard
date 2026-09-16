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

const SETTINGS_SECTIONS = [
  {label: 'credential profiles', node: buildAutoSwitchToggle()},
];

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
    if (evt.code === 'Escape') { evt.stopPropagation(); closeSettingsPanel(); return; }
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
    if (evt.code === 'Escape') { evt.stopPropagation(); closeSettingsPanel(); return; }
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
    if (evt.code === 'Escape') { evt.stopPropagation(); closeSettingsPanel(); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    save();
  });
  input.addEventListener('blur', save);

  const budgetInput = document.createElement('input');
  budgetInput.type = 'text';
  budgetInput.inputMode = 'decimal';
  budgetInput.className = 'text-field settings-budget-input';
  budgetInput.placeholder = 'no budget';
  budgetInput.value = board.daily_budget_usd == null ? '' : String(board.daily_budget_usd);
  const budgetStatus = document.createElement('span');
  budgetStatus.className = 'boards-status';

  async function saveBudget() {
    const value = budgetParseInput(budgetInput.value);
    if (value === undefined) {
      budgetStatus.textContent = 'must be a positive amount, or empty for no cap';
      budgetStatus.className = 'boards-status boards-error';
      return;
    }
    const {ok, body} = await apiOrError(`/api/boards/${board.id}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({daily_budget_usd: value}),
    });
    budgetStatus.textContent = ok ? '' : (body && body.error) || 'could not save';
    budgetStatus.className = ok ? 'boards-status' : 'boards-status boards-error';
  }

  budgetInput.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); closeSettingsPanel(); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    saveBudget();
  });
  budgetInput.addEventListener('blur', saveBudget);

  // statuses span the whole grid row under the fields, and take no room while empty
  status.classList.add('settings-grid-note');
  budgetStatus.classList.add('settings-grid-note');
  row.append(label, input, budgetInput, status, budgetStatus);
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
    parallelCaps.boardsList.appendChild(settingsGridHeader(['board', 'cards at once', 'daily $']));
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
  boardsLabel.textContent = 'per board (blank = no board-specific limit or daily budget)';
  const boardsList = document.createElement('div');
  boardsList.className = 'settings-grid';
  Object.assign(parallelCaps, {boardsList});
  box.append(globalLabel, buildGlobalParallelRow(), boardsLabel, boardsList);
  return box;
}

SETTINGS_SECTIONS.push({
  label: 'how many cards run at once',
  node: buildParallelSection(),
  onOpen: loadParallelSection,
});

// ---- spend caps: the most one run of each role may spend, in usd --------------------------------
// blank means the role's own default (the placeholder); a run reads its cap when it starts

const SPEND_CAPS = [
  {key: 'worker_budget_usd', label: 'card run (worker)', fallback: '5.00'},
  {key: 'reviewer_budget_usd', label: 'review', fallback: '1.50'},
  {key: 'orchestrator_budget_usd', label: 'mission control turn', fallback: '1.00'},
  {key: 'fold_budget_usd', label: 'fold', fallback: '2.00'},
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
    if (evt.code === 'Escape') { evt.stopPropagation(); closeSettingsPanel(); return; }
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
  grid.appendChild(settingsGridHeader(['run', 'max $']));
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

SETTINGS_SECTIONS.push({
  label: 'spend caps per run (the daily budget per board is set above)',
  node: buildSpendCapsSection(),
  onOpen: loadSpendCaps,
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
    if (evt.code === 'Escape') { evt.stopPropagation(); closeSettingsPanel(); return; }
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

function renderSettings() {
  clearChildren(st.listEl);
  if (!SETTINGS_SECTIONS.length) {
    st.listEl.appendChild(settingsHazardPlaceholder('no preferences yet'));
    return;
  }
  SETTINGS_SECTIONS.forEach(section => {
    st.listEl.appendChild(buildSettingsSection(section));
    if (section.onOpen) section.onOpen();
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
