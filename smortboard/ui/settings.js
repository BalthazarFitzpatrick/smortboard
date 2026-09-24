// settings panel (o): mission control preferences, opened from the top-right button or the o key.
//
// built from the same modal-backdrop / panel-floating pair as preflight.js and inbox.js - nothing
// here needs arrow/enter/escape hijacked from the document, so it stays out of Menu.
//
// scope toggles, snapshot options and the rest arrive as later cards, each pushing a
// {label, node, onOpen} entry here - onOpen (optional) runs every time the panel opens, so a
// section backed by its own fetch can refresh instead of showing stale data from the last open.
//
// relies on globals board.js already defines: api, apiOrError, reenterIfFocusLost, loadBoards. no
// new visual primitive here - rows and text fields reuse boards.js's own classes (board-row,
// boards-create-row, text-field, toggle), already loaded via boards.css, and ui_base's run-controls.

const st = {backdrop: null, panel: null, listEl: null};

// two buttons for one setting, the mouse toggle's look: the lit one only moves once the save
// lands, so a failed save leaves it where it was. real buttons, so tab, enter and space just work
function choiceToggle({className, choices, save}) {
  const row = document.createElement('div');
  row.className = 'run-controls';
  const state = {value: undefined, saving: false};
  const buttons = choices.map(([text, value]) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `toggle ${className}`;
    btn.dataset.choice = text;
    btn.textContent = text;
    btn.onclick = async () => {
      if (state.saving || state.value === value) return;
      state.saving = true;
      const ok = await save(value);
      state.saving = false;
      if (ok) show(value);
    };
    return btn;
  });
  function show(value) {
    state.value = value;
    buttons.forEach((btn, i) => {
      const on = choices[i][1] === value;
      btn.classList.toggle('on', on);
      btn.setAttribute('aria-pressed', String(on));
    });
  }
  row.append(...buttons);
  return {row, buttons, show};
}

async function saveSettings(fields) {
  const {ok} = await apiOrError('/api/settings', {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(fields),
  });
  return ok;
}

function toggleWithNote(toggle, noteText) {
  const note = document.createElement('div');
  note.className = 'field-label';
  note.textContent = noteText;
  const wrap = document.createElement('div');
  wrap.className = 'settings-stack';
  wrap.append(toggle.row, note);
  return wrap;
}

// usage_limit_route: one ladder for a usage limit. unset (the default) parks the board until the
// window resets; "attention" asks in the inbox (n) before a fallback model; "switch" moves to the
// next credential profile, then the fallback model, without asking
const usageRoute = choiceToggle({
  className: 'settings-usage-limit-choice',
  choices: [['wait for the reset', null], ['ask me', 'attention'], ['switch by itself', 'switch']],
  save: value => saveSettings({usage_limit_route: value}),
});

function buildUsageLimitToggle() {
  usageRoute.show(null);
  return toggleWithNote(usageRoute, 'wait: the board parks until the limit resets. ask me: the card '
    + 'asks in the inbox (n) before moving to a fallback model. switch: your next credential '
    + 'profile, then the fallback model.');
}

async function loadUsageLimitToggle() {
  try {
    const settings = await api('/api/settings');
    const route = settings.usage_limit_route;
    usageRoute.show(route === 'attention' || route === 'switch' ? route : null);
  } catch {
    // leave "wait for the reset" lit - it spends nothing, the safe default to fail to
  }
}

// allow_soft_leases and allow_free_merge switch a feature on for the whole install; each board
// then opts in on its own panel (shift+o). off puts every board back on strict or review
const softLeases = choiceToggle({
  className: 'settings-soft-leases-choice',
  choices: [['enabled', 'on'], ['disabled', null]],
  save: value => saveSettings({allow_soft_leases: value}),
});

const freeMerge = choiceToggle({
  className: 'settings-free-merge-choice',
  choices: [['enabled', 'on'], ['disabled', null]],
  save: value => saveSettings({allow_free_merge: value}),
});

function buildSoftLeasesToggle() {
  softLeases.show(null);
  return toggleWithNote(softLeases, 'enabled: a board may let its cards write unprotected paths no other '
    + 'card holds - pick it per board in shift+o. disabled: every board is strict.');
}

function buildFreeMergeToggle() {
  freeMerge.show(null);
  return toggleWithNote(freeMerge, 'enabled: a board may merge passing cards into its base without '
    + 'asking - pick it per board in shift+o. disabled: every board waits for your review. main is '
    + 'never merged either way.');
}

async function loadFeatureToggles() {
  try {
    const settings = await api('/api/settings');
    softLeases.show(settings.allow_soft_leases === 'on' ? 'on' : null);
    freeMerge.show(settings.allow_free_merge === 'on' ? 'on' : null);
  } catch {
    // leave both disabled lit - disabled is what the server assumes for an unset key
  }
}

// enable_mouse: the board is driven from the keyboard, and this turns on the pointer half of it -
// hovering focuses what the arrow keys would (fanning a piled column with it), and right-click
// opens the card menu m opens. off by default; the clicks that always worked are never gated by it
const mouseSetting = {enabled: null, disabled: null, saving: false};

function showMouseChoice(on) {
  mouseSetting.enabled.classList.toggle('on', on);
  mouseSetting.disabled.classList.toggle('on', !on);
  mouseSetting.enabled.setAttribute('aria-pressed', String(on));
  mouseSetting.disabled.setAttribute('aria-pressed', String(!on));
}

// the lit button only moves once the save lands, so a failed save leaves it where it was
async function pickMouse(on) {
  if (mouseSetting.saving) return;
  mouseSetting.saving = true;
  const {ok} = await apiOrError('/api/settings', {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({enable_mouse: on ? 'on' : null}),
  });
  mouseSetting.saving = false;
  if (!ok) return;
  showMouseChoice(on);
  setMouseEnabled(on); // live: no reload to start or stop hovering
}

// real buttons, so tab reaches them and enter or space picks without a key handler of our own
function mouseChoiceButton(text, on) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'toggle settings-mouse-choice';
  btn.dataset.choice = text;
  btn.textContent = text;
  btn.onclick = () => pickMouse(on);
  return btn;
}

// the section heading is the "mouse" label; the two buttons sit in ui_base's row of controls
function buildMouseToggle() {
  const row = document.createElement('div');
  row.className = 'run-controls';
  const enabled = mouseChoiceButton('enabled', true);
  const disabled = mouseChoiceButton('disabled', false);
  row.append(enabled, disabled);

  const note = document.createElement('div');
  note.className = 'field-label';
  note.textContent = 'the keyboard is how the board is driven. with this on, hovering focuses '
    + 'what the arrows would and right-click opens the card menu.';

  const wrap = document.createElement('div');
  wrap.className = 'settings-stack';
  wrap.append(row, note);
  Object.assign(mouseSetting, {enabled, disabled});
  showMouseChoice(false);
  return wrap;
}

async function loadMouseToggle() {
  try {
    const settings = await api('/api/settings');
    const on = settings.enable_mouse === 'on';
    showMouseChoice(on);
    setMouseEnabled(on);
  } catch {
    // leave disabled lit - keyboard only is the safe default to fail to
  }
}

const SETTINGS_SECTIONS = [
  {group: 'labs', label: 'usage limits', node: buildUsageLimitToggle(), onOpen: loadUsageLimitToggle},
  {group: 'general', label: 'soft file leases', node: buildSoftLeasesToggle(), onOpen: loadFeatureToggles},
  {group: 'general', label: 'free merge', node: buildFreeMergeToggle()},
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

// unset is the cli's own default and passes no flag; a level becomes --effort (claude) or
// model_reasoning_effort (codex) on that role's runs - store/schema.py EFFORT_LEVELS
const ROLE_EFFORT_LEVELS = ['low', 'medium', 'high'];

function openEffortPicker(role, current, anchor, status) {
  const items = ['default', ...ROLE_EFFORT_LEVELS].map(level => ({
    id: level, label: level, on: level === (current || 'default'),
  }));
  new Menu({
    title: `${role} effort`,
    sections: [{kind: 'list', items, onPick: async item => {
      const value = item.id === 'default' ? null : item.id;
      const {ok, body} = await apiOrError('/api/settings', {method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({[`${role}_effort`]: value})});
      if (ok) loadRoleModels();
      else status.textContent = body?.error || 'could not save effort';
    }}],
  }).openAt(anchor);
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

      const effortRow = document.createElement('div');
      effortRow.className = 'settings-role-control';
      const effortLabel = document.createElement('span');
      effortLabel.className = 'field-label';
      effortLabel.textContent = 'effort';
      const effort = document.createElement('button');
      effort.className = 'toggle role-effort';
      effort.dataset.role = role;
      effort.textContent = settings[`${role}_effort`] || 'default';
      effort.onclick = () => openEffortPicker(role, settings[`${role}_effort`], effort, status);
      effortRow.append(effortLabel, effort);
      block.append(heading, primaryRow, fallbackRow, effortRow);
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
  const remove = document.createElement('button');
  remove.type = 'button';
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
  const add = document.createElement('button');
  add.type = 'button';
  add.className = 'toggle';
  add.textContent = 'add';
  add.onclick = () => addReadPath();
  // the same folder picker boards use, so a path is chosen rather than typed
  const browse = document.createElement('button');
  browse.type = 'button';
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

// ---- where new repos go: the folder new board and from online repo open in ----------------------
// blank means the home folder. the server checks it exists and stores it absolute (~ expanded)

const reposHome = {input: null, status: null, saved: ''};

async function saveReposHome() {
  const raw = reposHome.input.value.trim();
  // blur fires on every tab past the field - only a changed value is worth a save
  if (raw === reposHome.saved) return;
  const {ok, body} = await apiOrError('/api/settings', {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({repos_home: raw || null}),
  });
  reposHome.status.textContent = ok ? 'saved' : (body && body.error) || 'could not save';
  reposHome.status.className = ok ? 'boards-status' : 'boards-status boards-error';
  if (!ok) return;
  reposHome.saved = body.repos_home || '';
  reposHome.input.value = reposHome.saved;
}

function buildReposHomeSection() {
  const row = document.createElement('div');
  row.className = 'boards-create-row';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'board-name-input text-field settings-repos-home-input';
  input.placeholder = 'home folder';
  const browse = document.createElement('button');
  browse.type = 'button';
  browse.className = 'toggle';
  browse.textContent = 'browse';
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
  const status = document.createElement('span');
  status.className = 'boards-status';
  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(evt.target); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    saveReposHome();
  });
  input.addEventListener('blur', saveReposHome);
  row.append(input, browse, status);
  Object.assign(reposHome, {input, status});

  const note = document.createElement('div');
  note.className = 'field-label';
  note.textContent = 'new board and from online repo start here. any folder is still one step away.';
  const box = document.createElement('div');
  box.className = 'settings-stack';
  box.append(row, note);
  return box;
}

async function loadReposHome() {
  try {
    const settings = await api('/api/settings');
    reposHome.saved = settings.repos_home || '';
    reposHome.input.value = reposHome.saved;
    reposHome.status.textContent = '';
  } catch (err) {
    reposHome.status.textContent = `could not load: ${err.message}`;
    reposHome.status.className = 'boards-status boards-error';
  }
}

SETTINGS_SECTIONS.push({
  group: 'general',
  label: 'where new repos go',
  node: buildReposHomeSection(),
  onOpen: loadReposHome,
});

// ---- how many cards run at once: one global cap, plus an optional cap per board ----------------
// the global number is the one seat count shared across every board (scheduler.py's runs.active());
// a board's own number only ever holds it back further, never past the global cap - an unset board
// row behaves exactly like today, no board-specific limit at all.

const parallelCaps = {globalInput: null, globalStatus: null};

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

async function loadParallelSection() {
  try {
    const settings = await api('/api/settings');
    parallelCaps.globalInput.value = settings.max_parallel == null ? '' : String(settings.max_parallel);
    parallelCaps.globalStatus.textContent = '';
  } catch (err) {
    parallelCaps.globalStatus.textContent = `could not load: ${err.message}`;
    parallelCaps.globalStatus.className = 'boards-status boards-error';
  }
}

function buildParallelSection() {
  const box = document.createElement('div');
  box.className = 'settings-stack';
  const note = document.createElement('div');
  note.className = 'field-label';
  note.textContent = 'across every board. a board can hold itself lower in shift+o.';
  box.append(buildGlobalParallelRow(), note);
  return box;
}

SETTINGS_SECTIONS.push({
  group: 'general',
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
  const spendNode = buildSpendCapsSection();
  spendNode.classList.add('settings-cost-grid');

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

  const note = document.createElement('div');
  note.className = 'field-label';
  note.textContent = 'a board\'s own daily budget is on its panel, shift+o.';
  const triggers = document.createElement('div');
  triggers.className = 'settings-cost-triggers';
  triggers.append(spendTrigger);
  const wrap = document.createElement('div');
  wrap.className = 'settings-stack';
  wrap.append(triggers, note);
  return wrap;
}

SETTINGS_SECTIONS.push({
  group: 'cost',
  label: 'spend caps',
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
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = `toggle ${className}`;
  btn.textContent = text;
  btn.onclick = run;
  return btn;
}

function buildBackupSection() {
  const row = document.createElement('div');
  row.className = 'run-controls';
  const status = document.createElement('span');
  status.className = 'boards-status';
  row.append(
    backupButton('export', 'settings-backup-export', exportBackup),
    backupButton('import as new board(s)', 'settings-backup-import', pickBackupFile),
    status,
  );

  const note = document.createElement('div');
  note.className = 'field-label';
  note.textContent = 'import adds the boards in a file beside yours, under new ids - it never '
    + 'replaces one. a full restore is smortboard import, into an empty database.';

  const wrap = document.createElement('div');
  wrap.className = 'settings-stack';
  wrap.append(row, note);
  backup.status = status;
  return wrap;
}

SETTINGS_SECTIONS.push({
  group: 'general',
  label: 'backup',
  node: buildBackupSection(),
  onOpen: () => showBackupStatus(''),
});

// last in general: the board is driven from the keyboard, so the mouse is the least of these
SETTINGS_SECTIONS.push({group: 'general', label: 'mouse', node: buildMouseToggle(), onOpen: loadMouseToggle});

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
  {id: 'general', label: 'general'},
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
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'toggle settings-button';
  btn.textContent = 'settings';
  btn.title = 'settings (o)';
  btn.onclick = () => toggleSettingsPanel();
  barCorner().appendChild(btn);
  return btn;
}

buildSettingsButton();
