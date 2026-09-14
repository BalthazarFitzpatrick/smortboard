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

// auto_switch_profiles: unset/"on" rotates credentials on USAGE_LIMIT (smortboard/profiles.py);
// "off" parks the board until the reset instead, the pre-profiles behaviour. the toggle reads its
// state from GET /api/settings on open (buildAutoSwitchToggle -> loadAutoSwitchToggle), same as
// every board setting - there is no client-side default, an unset key just renders as checked.
function buildAutoSwitchToggle() {
  const wrap = document.createElement('label');
  wrap.className = 'settings-toggle-row';
  const box = document.createElement('input');
  box.type = 'checkbox';
  box.className = 'settings-auto-switch-checkbox';
  box.checked = true;
  const text = document.createElement('span');
  text.textContent = 'switch credential profiles automatically on a usage limit';
  wrap.append(box, text);

  box.addEventListener('change', async () => {
    box.disabled = true;
    const {ok} = await apiOrError('/api/settings', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({auto_switch_profiles: box.checked ? null : 'off'}),
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
    box.checked = settings.auto_switch_profiles !== 'off';
  } catch {
    // leave the default (checked) - a failed load is not worth blocking the panel on
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

function clearChildren(el) {
  [...el.children].forEach(child => child.remove());
}

// ---- mission control can read: folders mission control's agent may read from, board-wide -------
// mounting them is a separate card (out of scope here) - this only maintains the path list, via
// the same whole-list-replace PATCH /api/settings the other board-wide values already use.

const rp = {input: null, listEl: null, statusEl: null};

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
    clearChildren(rp.listEl);
    rp.listEl.appendChild(settingsHazardPlaceholder(`could not load: ${err.message}`));
    return;
  }
  clearChildren(rp.listEl);
  if (!paths.length) {
    rp.listEl.appendChild(settingsHazardPlaceholder('no folders yet'));
    return;
  }
  paths.forEach(path => rp.listEl.appendChild(renderReadPathRow(path)));
}

async function patchReadPaths(paths) {
  return apiOrError('/api/settings', {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mission_control_read_paths: paths}),
  });
}

async function addReadPath() {
  const raw = rp.input.value.trim();
  if (!raw) return;
  rp.statusEl.textContent = 'adding...';
  rp.statusEl.className = 'boards-status';
  const existing = await currentReadPaths();
  const {ok, body} = await patchReadPaths([...existing, raw]);
  if (!ok) {
    rp.statusEl.textContent = (body && body.error) || `could not add ${raw}`;
    rp.statusEl.className = 'boards-status boards-error';
    return;
  }
  rp.input.value = '';
  rp.statusEl.textContent = '';
  await loadReadPaths();
}

async function removeReadPath(path) {
  const existing = await currentReadPaths();
  const {ok, body} = await patchReadPaths(existing.filter(p => p !== path));
  if (!ok) {
    rp.statusEl.textContent = (body && body.error) || `could not remove ${path}`;
    rp.statusEl.className = 'boards-status boards-error';
    return;
  }
  await loadReadPaths();
}

function buildReadPathsSection() {
  const box = document.createElement('div');

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
  const status = document.createElement('span');
  status.className = 'boards-status';
  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); closeSettingsPanel(); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    addReadPath();
  });
  addRow.append(input, add, status);

  box.append(list, addRow);
  Object.assign(rp, {input, listEl: list, statusEl: status});
  return box;
}

SETTINGS_SECTIONS.push({
  label: 'mission control can read',
  node: buildReadPathsSection(),
  onOpen: loadReadPaths,
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
// NOT appended inside #board-bar: board.js's renderBoardBar() does `bar.innerHTML = ''` on every
// board list load, which would wipe out a child living there - the same gotcha the attention
// indicator (inbox.js) already ran into. fixed to the viewport instead, at the board bar's corner.

function buildSettingsButton() {
  const btn = document.createElement('div');
  btn.className = 'toggle settings-button';
  btn.textContent = 'settings';
  btn.title = 'settings (o)';
  btn.onclick = () => toggleSettingsPanel();
  document.body.appendChild(btn);
  return btn;
}

buildSettingsButton();
