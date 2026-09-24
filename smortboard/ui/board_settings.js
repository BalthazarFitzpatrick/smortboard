// board settings (shift+o): what one board decides for itself - merge mode, file lease, how many
// cards it runs at once, its daily budget. o holds the settings every board shares.
//
// the same modal-backdrop / panel-floating pair as settings.js, whose helpers it reuses:
// choiceToggle, parallelParseInput, budgetParseInput, settingsHazardPlaceholder. soft leases and
// free merge only unlock once o turns the feature on; until then the choice is shown, not offered

const bs = {backdrop: null, panel: null, titleEl: null, listEl: null};

function boardSettingsNote(text) {
  const note = document.createElement('div');
  note.className = 'field-label';
  note.textContent = text;
  return note;
}

function boardSettingsSection(label, ...nodes) {
  const box = document.createElement('div');
  box.className = 'settings-section';
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

// a two-way board choice whose second option waits on a global feature switch in o
function gatedBoardToggle({board, className, field, choices, lit, allowed, offNote, onNote}) {
  const status = boardSettingsStatus();
  const toggle = choiceToggle({
    className,
    choices,
    save: value => patchBoard(board, {[field]: value}, status),
  });
  toggle.show(lit);
  toggle.buttons[1].disabled = !allowed;
  const note = boardSettingsNote(allowed ? onNote : offNote);
  const wrap = document.createElement('div');
  wrap.className = 'settings-stack';
  wrap.append(toggle.row, note, status);
  return wrap;
}

function boardNumberField({board, field, placeholder, className, inputMode, parse, invalid}) {
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
    await patchBoard(board, {[field]: value}, status);
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
  const merge = gatedBoardToggle({
    board,
    className: 'board-settings-merge-choice',
    field: 'merge_mode',
    choices: [['review', 'review'], ['free', 'free']],
    lit: board.merge_mode === 'free' ? 'free' : 'review',
    allowed: settings.allow_free_merge === 'on',
    onNote: 'review: a passing card opens a pull request and waits for you. free: it merges into '
      + 'the base by itself. main is never merged either way.',
    offNote: 'free merge is off for every board - turn it on in settings (o) first.',
  });
  const lease = gatedBoardToggle({
    board,
    className: 'board-settings-lease-choice',
    field: 'lease_mode',
    choices: [['strict', 'strict'], ['soft', 'soft']],
    lit: board.lease_mode === 'soft' ? 'soft' : 'strict',
    allowed: settings.allow_soft_leases === 'on',
    onNote: 'strict: a card writes only inside its lease. soft: it may also write unprotected '
      + 'paths no other card holds, and each one is shown on the card.',
    offNote: 'soft leases are off for every board - turn them on in settings (o) first.',
  });
  const globalCap = settings.max_parallel == null ? 2 : settings.max_parallel;
  const parallel = boardNumberField({
    board,
    field: 'max_parallel',
    placeholder: 'no limit',
    className: 'settings-parallel-input',
    inputMode: 'numeric',
    parse: parallelParseInput,
    invalid: 'must be a positive whole number, or empty for no limit',
  });
  const budget = boardNumberField({
    board,
    field: 'daily_budget_usd',
    placeholder: 'no budget',
    className: 'settings-budget-input',
    inputMode: 'decimal',
    parse: budgetParseInput,
    invalid: 'must be a positive amount, or empty for no cap',
  });
  bs.listEl.append(
    boardSettingsSection('merge mode', merge),
    boardSettingsSection('file lease', lease),
    boardSettingsSection('cards at once', parallel,
      boardSettingsNote(`blank: only the global cap in o (${globalCap}) applies.`)),
    boardSettingsSection('daily budget, usd', budget,
      boardSettingsNote('new cards stop starting once today\'s (utc) spend reaches it.')),
  );
}

async function loadBoardSettings() {
  clearChildren(bs.listEl);
  bs.titleEl.textContent = 'board settings';
  try {
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

async function openBoardSettingsPanel() {
  if (!bs.backdrop) buildBoardSettingsDom();
  document.body.appendChild(bs.backdrop);
  document.addEventListener('keydown', onBoardSettingsKey);
  await loadBoardSettings();
  focusPanel(bs.panel);
}

function closeBoardSettingsPanel() {
  if (!bs.backdrop || !bs.backdrop.parentNode) return;
  document.removeEventListener('keydown', onBoardSettingsKey);
  bs.backdrop.remove();
  reenterIfFocusLost();
}

function toggleBoardSettingsPanel() {
  if (bs.backdrop && bs.backdrop.parentNode) closeBoardSettingsPanel();
  else openBoardSettingsPanel();
}
