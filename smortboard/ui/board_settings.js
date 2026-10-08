// board settings (shift+o): what one board decides for itself - merge mode, file lease, run mode,
// the branches it never lands on, how many cards it runs at once, its daily budget. o holds the
// settings every board shares.
//
// a preset row (review only, free merge, open) writes a board's three modes together in one request;
// the groups below it are independent accordions, closed on first open and remembered for the
// session. every two-way choice is a ui_base segments control. a mode behind a global switch in o
// shows that switch's state on its row and the segment is unavailable until o turns it on
//
// the same modal-backdrop / panel-floating pair as settings.js, whose helpers it reuses:
// parallelParseInput, budgetParseInput, settingsHazardPlaceholder, clearChildren. the matching and
// wording logic is in settings_presets.js

const bs = {backdrop: null, panel: null, titleEl: null, listEl: null};

// the groups left open, kept while the page lives so reopening the panel finds them as they were
const boardGroupsOpen = new Set();

function boardSettingsNote(text) {
  const note = document.createElement('div');
  note.className = 'field-label';
  note.textContent = text;
  return note;
}

// the row's entry in settings_help.js, by its label
const BOARD_HELP_IDS = {
  'board preset': 'board_preset', 'merge mode': 'merge_mode', 'off-limit branches': 'board_off_limit_branches',
  'file lease': 'lease_mode', 'run mode': 'run_mode', 'cards at once': 'board_max_parallel',
  'daily budget, usd': 'daily_budget_usd',
};

function boardSettingsSection(label, ...nodes) {
  const box = document.createElement('div');
  box.className = 'settings-section';
  if (BOARD_HELP_IDS[label]) box.dataset.help = BOARD_HELP_IDS[label];
  const title = document.createElement('div');
  title.className = 'field-label';
  title.textContent = label;
  box.append(title, ...nodes);
  return box;
}

function boardSettingsStatus() {
  const status = document.createElement('span');
  status.className = 'boards-status';
  return status;
}

async function patchBoard(board, fields, status) {
  const {ok, body} = await apiOrError(`/api/boards/${board.id}`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(fields),
  });
  status.textContent = ok ? '' : (body && body.error) || 'could not save';
  status.className = ok ? 'boards-status' : 'boards-status boards-error';
  if (!ok) return false;
  Object.assign(board, fields);
  // board.js keeps its own copy of each board: the merge-mode label in the bar reads it
  const cached = boards.find(b => b.id === board.id);
  if (cached) Object.assign(cached, fields);
  renderBoardMergeMode();
  return true;
}

// the preset row: a segments control with the matching preset lit, `custom` lit by the app when no
// preset matches, and a diff line saying what the lit preset sets or how far the board is from it
function boardPresetRow(board, settings, onSaved) {
  const status = boardSettingsStatus();
  const diff = document.createElement('div');
  diff.className = 'field-label board-preset-diff';
  const lead = document.createElement('span');
  const strong = document.createElement('b');
  diff.append(lead, strong);

  const options = BOARD_PRESETS.map(preset => {
    const {ok, missing} = boardPresetAvailability(preset, settings);
    return {label: preset.label, value: preset.id, disabled: !ok,
      hint: ok ? '' : missing.map(key => gateLine(settings, key)).join(', ')};
  });
  options.push({label: 'custom', value: 'custom', auto: true});

  const segments = makeSegments({
    options, value: matchBoardPreset(board).id, label: 'board preset',
    onPick: async id => {
      const saved = await patchBoard(board, boardPresetPatch(id), status);
      if (saved) onSaved();
      return saved;
    },
  });
  function refresh() {
    const match = matchBoardPreset(board);
    segments.setLit(match.id);
    lead.textContent = match.line.lead;
    strong.textContent = match.line.strong;
  }
  refresh();
  return {node: boardSettingsSection('board preset', segments.el, diff, status), refresh};
}

// one two-way choice saved to this board. `gate` names the global switch behind the second option:
// its state shows under the row, the option is unavailable while it is off, and a stored choice
// that the switch is holding back says so
function boardChoiceRow({board, settings, label, field, options, gate, onSaved}) {
  const status = boardSettingsStatus();
  const inForce = boardInForce(board, settings)[field];
  const gateOn = !gate || booleanIsOn(settings, gate);
  const gateValue = options[1].value;
  const segments = makeSegments({
    label,
    value: inForce,
    options: options.map(opt => ({...opt, disabled: !gateOn && opt.value === gateValue,
      hint: !gateOn && opt.value === gateValue ? gateLine(settings, gate) : ''})),
    onPick: async value => {
      const saved = await patchBoard(board, {[field]: value}, status);
      if (saved) onSaved();
      return saved;
    },
  });
  const nodes = [segments.el];
  if (gate) {
    const line = document.createElement('div');
    line.className = 'board-gate-line';
    const state = document.createElement('span');
    state.className = gateOn ? 'board-gate-on' : 'board-gate-off';
    state.textContent = gateLine(settings, gate);
    line.appendChild(state);
    if (!gateOn) {
      const link = document.createElement('button');
      link.type = 'button';
      link.className = 'toggle board-gate-link';
      link.textContent = 'turn on in general';
      link.onclick = () => {
        closeBoardSettingsPanel();
        if (typeof openSettingsPanel === 'function') openSettingsPanel();
      };
      line.appendChild(link);
    }
    nodes.push(line);
  }
  // a stored choice the global switch is holding back is kept, and says so
  const held = boardSettingsNote('');
  nodes.push(held, status);
  const section = boardSettingsSection(label, ...nodes);
  // relights the row after a preset or another save wrote this board's modes
  section.refreshRow = () => {
    const current = boardInForce(board, settings)[field];
    const stored = board[field] || options[0].value;
    segments.setLit(current);
    held.hidden = stored === current;
    held.textContent = stored === current ? ''
      : `saved for this board: ${stored}. it applies again when ${BOOLEAN_SETTINGS[gate].label} is on in general.`;
  };
  section.refreshRow();
  return section;
}

// the branches this board never lands on: one comma-separated field. an empty list is allowed and
// means every branch; while the global switch in o is off, none of it applies
function offLimitEditor(board, settings, onSaved) {
  const box = document.createElement('div');
  box.className = 'settings-stack';
  const status = boardSettingsStatus();
  const lifted = !booleanIsOn(settings, 'off_limit_branches');
  const none = boardSettingsNote('none - this board may land on any branch.');
  const joined = () => (board.off_limit_branches || []).join(', ');

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'board-name-input text-field off-limit-input';
  input.placeholder = 'main, master, trunk';
  input.value = joined();
  input.disabled = lifted;
  const showNone = () => { none.hidden = !!(board.off_limit_branches || []).length; };

  // split on commas and whitespace, drop empties and repeats
  const parseNames = raw => [...new Set(raw.split(/[\s,]+/).filter(Boolean))];
  const save = async () => {
    const names = parseNames(input.value);
    if (names.join(',') === (board.off_limit_branches || []).join(',')) return;
    if (!(await patchBoard(board, {off_limit_branches: names}, status))) return;
    input.value = joined();
    showNone();
    onSaved();
  };
  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(evt.target); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    save();
  });
  input.addEventListener('blur', save);
  showNone();
  box.append(input, none, status);
  // while the global switch is off the list stays visible but cannot be edited, and says why
  if (lifted) {
    box.appendChild(boardSettingsNote('off-limit branches are off for every board - turn them on in settings (o) to use this list.'));
  }
  return boardSettingsSection('off-limit branches', box);
}

function boardNumberField({board, field, placeholder, className, inputMode, parse, invalid, onSaved}) {
  const row = document.createElement('div');
  row.className = 'boards-create-row';
  const input = document.createElement('input');
  input.type = 'text';
  input.inputMode = inputMode;
  input.className = `board-name-input text-field ${className}`;
  input.placeholder = placeholder;
  input.value = board[field] == null ? '' : String(board[field]);
  const status = boardSettingsStatus();

  async function save() {
    const value = parse(input.value);
    if (value === undefined) {
      status.textContent = invalid;
      status.className = 'boards-status boards-error';
      return;
    }
    if (value === board[field]) return;
    if (await patchBoard(board, {[field]: value}, status)) onSaved();
  }

  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(evt.target); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    save();
  });
  input.addEventListener('blur', save);
  row.append(input, status);
  return row;
}

function renderBoardSettings(board, settings) {
  const groups = {};
  const choiceRows = [];
  let preset = null;
  // after any save: relight the preset row and the choice rows, refresh every header's summary
  function onSaved() {
    if (preset) preset.refresh();
    choiceRows.forEach(row => row.refreshRow());
    boardGroups(board, settings).forEach(g => groups[g.id]?.setSummary(g.summary));
  }
  const choiceRow = config => {
    const row = boardChoiceRow({...config, board, settings, onSaved});
    choiceRows.push(row);
    return row;
  };

  preset = boardPresetRow(board, settings, onSaved);
  const globalCap = numberOrNull(settings.max_parallel) ?? SETTINGS_DEFAULTS.max_parallel;
  const bodies = {
    landing: [
      choiceRow({
        label: 'merge mode', field: 'merge_mode', gate: 'allow_free_merge',
        options: [{label: 'review', value: 'review'}, {label: 'free', value: 'free'}],
      }),
      offLimitEditor(board, settings, onSaved),
    ],
    running: [
      choiceRow({
        label: 'file lease', field: 'lease_mode', gate: 'allow_soft_leases',
        options: [{label: 'strict', value: 'strict'}, {label: 'soft', value: 'soft'}],
      }),
      choiceRow({
        label: 'run mode', field: 'run_mode', gate: 'allow_open_mode',
        options: [{label: 'sealed', value: 'sealed'}, {label: 'open', value: 'open'}],
      }),
      boardSettingsSection('cards at once',
        boardNumberField({
          board, onSaved, field: 'max_parallel', placeholder: 'no limit', className: 'settings-parallel-input',
          inputMode: 'numeric', parse: parallelParseInput,
          invalid: 'must be a positive whole number, or empty for no limit',
        }),
        boardSettingsNote(`global limit: ${globalCap}`)),
    ],
    budget: [
      boardSettingsSection('daily budget, usd',
        boardNumberField({
          board, onSaved, field: 'daily_budget_usd', placeholder: 'no budget', className: 'settings-budget-input',
          inputMode: 'decimal', parse: budgetParseInput, invalid: 'must be a positive amount, or empty for no cap',
        })),
    ],
  };

  const groupEls = boardGroups(board, settings).map(group => {
    const body = document.createElement('div');
    body.className = 'settings-stack board-settings-group-body';
    body.append(...bodies[group.id]);
    const disclosure = makeDisclosure({
      title: group.title, summary: group.summary, body,
      open: boardGroupsOpen.has(group.id),
      onToggle: open => { if (open) boardGroupsOpen.add(group.id); else boardGroupsOpen.delete(group.id); },
    });
    groups[group.id] = disclosure;
    return disclosure.el;
  });
  bs.listEl.append(preset.node, ...groupEls);
}

async function loadBoardSettings() {
  clearChildren(bs.listEl);
  bs.titleEl.textContent = 'board settings';
  try {
    // one read of each per open, shared by every row
    const [allBoards, settings] = await Promise.all([api('/api/boards'), api('/api/settings')]);
    const board = allBoards.find(b => b.id === currentBoardId);
    if (!board) {
      bs.listEl.appendChild(settingsHazardPlaceholder('no board open'));
      return;
    }
    bs.titleEl.textContent = `board settings: ${board.name}`;
    renderBoardSettings(board, settings);
  } catch (err) {
    bs.listEl.appendChild(settingsHazardPlaceholder(`could not load: ${err.message}`));
  }
}

function buildBoardSettingsDom() {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop settings-backdrop';
  const panel = document.createElement('div');
  panel.className = 'panel-floating settings-panel board-settings-panel';
  const title = document.createElement('div');
  title.className = 'popup-title';
  const rule = document.createElement('div');
  rule.className = 'h-divider';
  const list = document.createElement('div');
  list.className = 'settings-list';
  panel.append(title, rule, list);
  backdrop.appendChild(panel);
  backdrop.addEventListener('mousedown', evt => { if (evt.target === backdrop) closeBoardSettingsPanel(); });
  Object.assign(bs, {backdrop, panel, titleEl: title, listEl: list});
}

function onBoardSettingsKey(evt) {
  if (evt.code === 'Escape') closeBoardSettingsPanel();
}

// the floating help box of the open panel, made on open and destroyed on close
let boardHelp = null;

async function openBoardSettingsPanel() {
  if (!bs.backdrop) buildBoardSettingsDom();
  document.body.appendChild(bs.backdrop);
  document.addEventListener('keydown', onBoardSettingsKey);
  await loadBoardSettings();
  boardHelp = bindSettingsHelp(bs.panel);
  focusPanel(bs.panel);
}

function closeBoardSettingsPanel() {
  if (!bs.backdrop || !bs.backdrop.parentNode) return;
  // a submenu goes with its panel - left open it hung over the board with nothing under it
  Menu.closeOpen?.();
  document.removeEventListener('keydown', onBoardSettingsKey);
  if (boardHelp) { boardHelp.destroy(); boardHelp = null; }
  bs.backdrop.remove();
  reenterIfFocusLost();
}

function toggleBoardSettingsPanel() {
  if (bs.backdrop && bs.backdrop.parentNode) closeBoardSettingsPanel();
  else openBoardSettingsPanel();
}
