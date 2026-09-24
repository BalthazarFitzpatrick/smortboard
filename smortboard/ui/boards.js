// boards and repos (b) - the only way in today is curl. this panel creates a board (switching to
// it), and registers/edits repos on the current board, so a fresh tester can get going alone.
// NOT a Menu, same reason the prompt editor and inbox aren't - see board.js's note on menu.js
// before assuming Menu fits a panel with real text inputs in it.
//
// relies on globals board.js already defines: api, apiOrError, escapeHtml, boards, currentBoardId,
// loadBoards, onBoardEnter, activateTab, reenterIfFocusLost - and chat.js's prefillMissionControl.

const bp = {backdrop: null, panel: null, boardListEl: null, boardNameInput: null, boardStatusEl: null,
  boardName: '', repoListEl: null, repoStatusEl: null, repoFields: {}, confirmDeleteId: null,
  testsNotice: null, setupEl: null, folderConfirm: null, pushMain: null, settingUp: false};

function buildBoardsDom() {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop boards-backdrop';
  const panel = document.createElement('div');
  panel.className = 'panel-floating boards-panel';

  const title = document.createElement('div');
  title.className = 'popup-title';
  title.textContent = 'boards and repos';
  panel.appendChild(title);
  panel.appendChild(divider());

  // two ways to start a board, as the first row: any folder on this disk, made ready for a board,
  // and a repo online (not built yet, see the ledger). a board with no repo is the name field below
  const sourceRow = document.createElement('div');
  sourceRow.className = 'boards-source-row';
  const fromFolder = document.createElement('div');
  fromFolder.className = 'toggle board-source-new';
  fromFolder.textContent = 'new board';
  fromFolder.onclick = () => openNewBoardPicker(fromFolder);
  const fromOnline = document.createElement('div');
  fromOnline.className = 'toggle board-source-online disabled';
  fromOnline.textContent = 'from online repo';
  fromOnline.title = 'not built yet';
  sourceRow.append(fromFolder, fromOnline);
  panel.appendChild(sourceRow);

  // a new board's first-commit question and the step left to the operator, redrawn from bp
  const setup = document.createElement('div');
  setup.className = 'boards-setup';
  panel.appendChild(setup);

  // ---- boards section ----
  const boardsLabel = document.createElement('div');
  boardsLabel.className = 'field-label';
  boardsLabel.textContent = 'boards';
  panel.appendChild(boardsLabel);

  const boardList = document.createElement('div');
  boardList.className = 'boards-list';
  panel.appendChild(boardList);

  const boardCreateRow = document.createElement('div');
  boardCreateRow.className = 'boards-create-row';
  const boardNameInput = document.createElement('input');
  boardNameInput.type = 'text';
  boardNameInput.className = 'board-name-input text-field';
  boardNameInput.placeholder = 'new board name, enter to create';
  const boardStatus = document.createElement('span');
  boardStatus.className = 'boards-status';
  boardNameInput.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(evt.target); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    createBoardFromPanel();
  });
  boardCreateRow.append(boardNameInput, boardStatus);
  panel.appendChild(boardCreateRow);

  panel.appendChild(divider());

  // ---- repos section ----
  const reposLabel = document.createElement('div');
  reposLabel.className = 'field-label repos-label';
  panel.appendChild(reposLabel);

  const repoList = document.createElement('div');
  repoList.className = 'repos-list';
  panel.appendChild(repoList);

  const hint = document.createElement('div');
  hint.className = 'field-label repos-hint';
  hint.textContent = 'create the GitHub repo and push the default branch first - press h to check';
  panel.appendChild(hint);

  const form = buildRepoForm();
  panel.appendChild(form);

  backdrop.appendChild(panel);
  backdrop.addEventListener('mousedown', evt => { if (evt.target === backdrop) closeBoardsPanel(); });

  Object.assign(bp, {
    backdrop, panel, boardListEl: boardList, boardNameInput, boardStatusEl: boardStatus,
    reposLabelEl: reposLabel, repoListEl: repoList, sourceRowEl: sourceRow, setupEl: setup,
  });
  return backdrop;
}

function divider() {
  const el = document.createElement('div');
  el.className = 'h-divider';
  return el;
}

// name, path, default_branch, test_command, lint_command, image - one row of labelled inputs
const REPO_FORM_FIELDS = [
  {key: 'name', placeholder: 'name'},
  {key: 'path', placeholder: 'path (~ is expanded)'},
  {key: 'default_branch', placeholder: 'default branch', value: 'main'},
  {key: 'test_command', placeholder: 'test command (optional)'},
  {key: 'lint_command', placeholder: 'lint command (optional)'},
  {key: 'image', placeholder: 'image (optional)'},
];

function buildRepoForm() {
  const form = document.createElement('div');
  form.className = 'repo-form';
  REPO_FORM_FIELDS.forEach(({key, placeholder, value}) => {
    const input = document.createElement('input');
    input.type = 'text';
    input.className = `repo-field repo-field-${key} text-field`;
    input.placeholder = placeholder;
    if (value) input.value = value;
    input.addEventListener('keydown', evt => {
      if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(evt.target); return; }
      if (evt.code !== 'Enter') return;
      evt.preventDefault();
      registerRepoFromPanel();
    });
    bp.repoFields[key] = input;
    form.appendChild(input);
  });
  const submitRow = document.createElement('div');
  submitRow.className = 'repo-form-submit-row';
  const submit = document.createElement('div');
  submit.className = 'toggle repo-form-submit';
  submit.textContent = 'register repo';
  submit.onclick = () => registerRepoFromPanel();
  const status = document.createElement('span');
  status.className = 'repo-status';
  submitRow.append(submit, status);
  form.appendChild(submitRow);
  bp.repoStatusEl = status;
  return form;
}

// ---- boards list -----------------------------------------------------------------------------

function boardCardCount(boardId) {
  // best-effort: the board's own card list, if it happens to already be on screen. delete still
  // works without this, it just says "removes its cards" instead of a number
  return api(`/api/boards/${boardId}/cards`).then(cards => cards.length).catch(() => null);
}

function renderBoardRow(board) {
  const row = document.createElement('div');
  row.className = 'board-row';
  row.dataset.boardId = board.id;
  if (board.id === currentBoardId) row.classList.add('on');

  const name = document.createElement('span');
  name.className = 'board-name toggle';
  name.textContent = board.name;
  name.onclick = async () => {
    activateTab(board.id);
    await onBoardEnter(board.id);
    renderBoardsPanel();
  };

  const del = document.createElement('span');
  del.className = 'board-delete toggle';
  del.textContent = 'delete';
  del.onclick = () => armBoardDelete(board.id);

  row.append(name, del);

  if (bp.confirmDeleteId === board.id) {
    const confirmRow = document.createElement('div');
    confirmRow.className = 'board-delete-confirm';
    const label = document.createElement('span');
    label.className = 'field-label';
    label.textContent = 'loading card count...';
    confirmRow.appendChild(label);
    boardCardCount(board.id).then(count => {
      label.textContent = count == null
        ? 'delete this board and its cards?'
        : `delete this board and its ${count} card${count === 1 ? '' : 's'}?`;
    });
    const yes = document.createElement('span');
    yes.className = 'toggle board-delete-yes';
    yes.textContent = 'confirm delete';
    yes.onclick = () => deleteBoardFromPanel(board.id);
    const no = document.createElement('span');
    no.className = 'toggle board-delete-no';
    no.textContent = 'cancel';
    no.onclick = () => { bp.confirmDeleteId = null; renderBoardsPanel(); };
    confirmRow.append(yes, no);
    row.appendChild(confirmRow);
  }
  return row;
}

function armBoardDelete(boardId) {
  bp.confirmDeleteId = boardId;
  renderBoardsPanel();
}

async function deleteBoardFromPanel(boardId) {
  const {ok, body} = await apiOrError(`/api/boards/${boardId}`, {method: 'DELETE'});
  bp.confirmDeleteId = null;
  if (!ok) {
    bp.boardStatusEl.textContent = (body && body.error) || 'could not delete';
    bp.boardStatusEl.className = 'boards-status boards-error';
    return;
  }
  await loadBoards();
  renderBoardsPanel();
}

async function createBoardFromPanel() {
  const name = bp.boardNameInput.value.trim();
  if (!name) return;
  bp.boardStatusEl.textContent = 'creating...';
  bp.boardStatusEl.className = 'boards-status';
  const {ok, body} = await apiOrError('/api/boards', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name}),
  });
  if (!ok) {
    bp.boardStatusEl.textContent = (body && body.error) || 'could not create the board';
    bp.boardStatusEl.className = 'boards-status boards-error';
    return;
  }
  bp.boardNameInput.value = '';
  bp.boardStatusEl.textContent = '';
  await loadBoards();
  // creating switches to it, per the brief
  activateTab(body.id);
  await onBoardEnter(body.id);
  renderBoardsPanel();
}

function setBoardStatus(text, isError = false) {
  bp.boardStatusEl.textContent = text;
  bp.boardStatusEl.className = isError ? 'boards-status boards-error' : 'boards-status';
}

// the folder menu opens in place, like the review tool's save dialog: '..' and each subfolder, git
// repos marked. `action` is the one button it offers, shown only for a folder `action.when` accepts;
// `add`, when given, is a name field whose value goes to add.run with the folder it was typed in
async function openFolderPicker(anchor, {title, action, add, onError}) {
  const first = await apiOrError('/api/folders');
  if (!first.ok) { onError((first.body && first.body.error) || 'could not list folders'); return; }
  let where = first.body;
  let menu = null;
  const open = async under => {
    const next = await apiOrError('/api/folders?under=' + encodeURIComponent(under));
    if (!next.ok) { onError((next.body && next.body.error) || 'could not open that folder'); return; }
    where = next.body;
    menu.refresh(build());
  };
  const build = () => [
    {
      kind: 'list',
      label: `folder  ${where.here}`,
      empty: 'no folders here',
      items: [
        ...(where.parent !== null ? [{id: where.parent, label: '..'}] : []),
        ...where.folders.map(f => ({id: f.path, label: f.repo ? `${f.name}/  git` : `${f.name}/`})),
      ],
      onPick: item => open(item.id),
    },
    ...(add ? [{kind: 'add', placeholder: add.placeholder, button: add.label,
      onAdd: (name, m) => add.run(where.here, name, m)}] : []),
    ...(action.when(where) ? [{kind: 'buttons', buttons: [
      {label: action.label, tone: 'adds', onClick: m => action.run(where.here, m)},
    ]}] : []),
  ];
  menu = new Menu({title, persistent: true, sections: build()});
  menu.openAt(anchor);
}

// any folder can hold a board - the server gives it whatever it lacks (git, development, an origin) -
// so every folder offers the action, and a name typed here makes a new folder inside this one
function openNewBoardPicker(anchor) {
  return openFolderPicker(anchor, {
    title: 'new board',
    action: {label: 'board from this folder', when: () => true,
      run: (path, menu) => createBoardFromFolder(path, menu)},
    add: {placeholder: 'or a new folder in here', label: 'board in new folder',
      run: (path, name, menu) => createBoardFromFolder(path, menu, {new_folder: name})},
    onError: text => setBoardStatus(text, true),
  });
}

// one POST for every starting point. a folder with files and no git answers 409 with what its first
// commit would hold, and waits for the operator; `settingUp` keeps a double click to one board
async function createBoardFromFolder(path, menu, extra = {}) {
  if (bp.settingUp) return;
  bp.settingUp = true;
  setBoardStatus('setting up...');
  let reply;
  try {
    reply = await apiOrError('/api/boards/from-folder', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path, ...extra}),
    });
  } finally {
    bp.settingUp = false;
  }
  const {ok, status, body} = reply;
  if (status === 409 && body?.needs_confirm) {
    menu?.close();
    bp.folderConfirm = {path: body.path, files: body.files};
    setBoardStatus('');
    renderSetupNotices();
    return;
  }
  if (!ok) { setBoardStatus((body && body.error) || 'could not create the board', true); return; }
  menu?.close();
  bp.folderConfirm = null;
  bp.pushMain = body.setup?.push_main || null;
  // what was set up leads the status line, and the tests line follows it
  const done = body.setup?.steps?.length ? `done: ${body.setup.steps.join(', ')}` : '';
  const setStatus = (text, isError = false) =>
    setBoardStatus([done, text].filter(Boolean).join('; '), isError);
  setStatus('');
  reportRepoTests(body.repo, body.tests, setStatus);
  await loadBoards();
  activateTab(body.board.id);
  await onBoardEnter(body.board.id);
  renderBoardsPanel();
}

// ---- a new board's setup notices -------------------------------------------------------------

const FIRST_COMMIT_SHOWN = 12;

function renderSetupNotices() {
  clearChildren(bp.setupEl);
  if (bp.folderConfirm) bp.setupEl.appendChild(renderFolderConfirm(bp.folderConfirm));
  if (bp.pushMain) bp.setupEl.appendChild(renderPushMain(bp.pushMain));
}

function noticeToggle(className, text, onclick) {
  const el = document.createElement('span');
  el.className = `toggle ${className}`;
  el.textContent = text;
  el.onclick = onclick;
  return el;
}

// built like the delete confirm: what the folder's first commit would hold, and two toggles
function renderFolderConfirm({path, files}) {
  const box = document.createElement('div');
  box.className = 'boards-folder-confirm';
  const label = document.createElement('span');
  label.className = 'field-label';
  label.textContent = `${path.split('/').filter(Boolean).pop() || path} has files and no git - `
    + 'its first commit would hold:';
  const list = document.createElement('div');
  list.className = 'boards-folder-files';
  files.slice(0, FIRST_COMMIT_SHOWN).forEach(name => {
    const row = document.createElement('span');
    row.textContent = name;
    list.appendChild(row);
  });
  if (files.length > FIRST_COMMIT_SHOWN) {
    const more = document.createElement('span');
    more.className = 'field-label';
    more.textContent = `and ${files.length - FIRST_COMMIT_SHOWN} more`;
    list.appendChild(more);
  }
  const ignore = document.createElement('span');
  ignore.className = 'field-label';
  ignore.textContent = 'a .gitignore for secrets is added first if the folder has none';
  box.append(label, list, ignore,
    noticeToggle('boards-folder-commit', 'commit these and continue',
      () => createBoardFromFolder(path, null, {confirm: true})),
    noticeToggle('boards-folder-cancel', 'cancel',
      () => { bp.folderConfirm = null; renderSetupNotices(); }));
  return box;
}

// the board never pushes main: when it made the origin, this command is the operator's to run, and
// the notice stays until they say it is done
function renderPushMain(command) {
  const box = document.createElement('div');
  box.className = 'boards-push-main';
  const label = document.createElement('span');
  label.className = 'field-label';
  label.textContent = 'one step is yours - run this once:';
  const code = document.createElement('code');
  code.className = 'boards-push-main-command';
  code.textContent = command;
  const copy = noticeToggle('boards-push-main-copy', 'copy', async () => {
    // written inside the click, which the clipboard asks for; a refusal leaves it to select by hand
    try {
      await navigator.clipboard.writeText(command);
      copy.textContent = 'copied';
    } catch {
      copy.textContent = 'could not copy - select it';
    }
  });
  box.append(label, code, copy,
    noticeToggle('boards-push-main-done', 'done', () => { bp.pushMain = null; renderSetupNotices(); }));
  return box;
}

// ---- repos list --------------------------------------------------------------------------------

function renderRepoRow(repo) {
  const row = document.createElement('div');
  // renderRepoList only ever loads repos for currentBoardId, so every row rendered here belongs
  // to the open board - same "opened" look boards.on gives the board row itself
  row.className = 'repo-row on';
  row.dataset.repoId = repo.id;

  const head = document.createElement('div');
  head.className = 'repo-row-head';
  const name = document.createElement('span');
  name.className = 'repo-name';
  name.textContent = repo.name;
  const branch = document.createElement('span');
  branch.className = 'field-label';
  branch.textContent = repo.default_branch;
  head.append(name, branch);

  const path = document.createElement('div');
  path.className = 'repo-path field-label';
  path.textContent = repo.path;

  const editRow = document.createElement('div');
  editRow.className = 'repo-edit-row';
  const testInput = document.createElement('input');
  testInput.type = 'text';
  testInput.className = 'repo-edit-test text-field';
  testInput.placeholder = 'test command';
  testInput.value = repo.test_command || '';
  const imageInput = document.createElement('input');
  imageInput.type = 'text';
  imageInput.className = 'repo-edit-image text-field';
  imageInput.placeholder = 'image';
  imageInput.value = repo.image || '';
  const save = document.createElement('span');
  save.className = 'toggle repo-edit-save';
  save.textContent = 'save';
  const status = document.createElement('span');
  status.className = 'repo-edit-status';
  save.onclick = () => saveRepoEdit(repo.id, testInput.value, imageInput.value, status);
  [testInput, imageInput].forEach(input => input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(evt.target); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    saveRepoEdit(repo.id, testInput.value, imageInput.value, status);
  }));
  editRow.append(testInput, imageInput, save, status);

  row.append(head, path, editRow);
  if (bp.testsNotice?.repoId === repo.id) row.appendChild(renderTestsNotice(bp.testsNotice));
  row.appendChild(renderRememberedLeases(repo));
  return row;
}

// ---- a new repo's tests ------------------------------------------------------------------------

// every card needs tests, so a repo landing without any says so at once: a command and tests for
// it is one status line, no tests holds a notice on the repo's row until one of its ways out is
// picked or the panel closes
function reportRepoTests(repo, tests, setStatus) {
  if (!tests) return;
  if (!tests.has_tests) {
    bp.testsNotice = {repoId: repo.id, name: repo.name, command: tests.command};
    return;
  }
  if (tests.command) setStatus(`tests run with \`${tests.command}\` - change it on the repo's row`);
  else setStatus("no test command found - set one on the repo's row", true);
}

// built like the delete confirm: a label and two toggles, redrawn from bp on every render
function renderTestsNotice(notice) {
  const box = document.createElement('div');
  box.className = 'repo-tests-notice';
  const label = document.createElement('span');
  label.className = 'field-label';
  label.textContent = `${notice.name} has no tests - every card needs them`;
  const ask = document.createElement('span');
  ask.className = 'toggle repo-tests-ask';
  ask.textContent = 'ask mission control';
  ask.onclick = () => askMissionControlForTests(notice);
  const own = document.createElement('span');
  own.className = 'toggle repo-tests-own';
  own.textContent = 'use my own command';
  own.onclick = () => focusOwnTestCommand(notice.repoId);
  box.append(label, ask, own);
  return box;
}

// written, never sent: the operator reads it in mission control and sends it with / then enter
function askMissionControlForTests({name, command}) {
  const runner = command ? '' : ', sets up its test runner and names the command';
  closeBoardsPanel();
  prefillMissionControl(`${name} has no tests yet. `
    + `Plan a first card that adds a test suite for its current behaviour${runner}.`);
  setBoardStatus('press / then enter to send it');
}

async function focusOwnTestCommand(repoId) {
  bp.testsNotice = null;
  await renderRepoList();
  bp.repoListEl.querySelector(`.repo-row[data-repo-id="${repoId}"] .repo-edit-test`)?.focus();
}

// remembered globs approved once from the inbox (see lease/approve's remember flag) - every
// later card on this repo writes them with no LEASE_CONFLICT, until removed here
function renderRememberedLeases(repo) {
  const box = document.createElement('div');
  box.className = 'repo-remembered-leases';
  const label = document.createElement('div');
  label.className = 'field-label';
  label.textContent = 'remembered lease paths';
  box.appendChild(label);

  const leases = repo.remembered_leases || [];
  if (!leases.length) {
    const empty = document.createElement('div');
    empty.className = 'field-label repo-remembered-leases-empty';
    empty.textContent = 'none remembered yet';
    box.appendChild(empty);
    return box;
  }
  leases.forEach(lease => {
    const item = document.createElement('div');
    item.className = 'repo-remembered-lease-row';
    const glob = document.createElement('span');
    glob.className = 'repo-remembered-lease-glob';
    glob.textContent = lease.path_glob;
    const remove = document.createElement('span');
    remove.className = 'toggle repo-remembered-lease-remove';
    remove.textContent = 'remove';
    remove.onclick = () => forgetRememberedLease(repo.id, lease.id);
    item.append(glob, remove);
    box.appendChild(item);
  });
  return box;
}

async function forgetRememberedLease(repoId, leaseId) {
  await apiOrError(`/api/repos/${repoId}/lease/${leaseId}`, {method: 'DELETE'});
  renderRepoList();
}

async function saveRepoEdit(repoId, testCommand, image, statusEl) {
  statusEl.textContent = 'saving...';
  statusEl.className = 'repo-edit-status';
  const {ok, body} = await apiOrError(`/api/repos/${repoId}`, {
    method: 'PATCH', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({test_command: testCommand || null, image: image || null}),
  });
  if (!ok) {
    statusEl.textContent = (body && body.error) || 'could not save';
    statusEl.className = 'repo-edit-status repo-edit-error';
    return;
  }
  statusEl.textContent = 'saved';
  statusEl.className = 'repo-edit-status repo-edit-ok';
}

function setRepoStatus(text, isError = false) {
  bp.repoStatusEl.textContent = text;
  bp.repoStatusEl.className = isError ? 'repo-status repo-error' : 'repo-status';
}

async function registerRepoFromPanel() {
  if (!currentBoardId) return;
  const body = {};
  REPO_FORM_FIELDS.forEach(({key}) => { body[key] = bp.repoFields[key].value.trim(); });
  setRepoStatus('registering...');
  const {ok, body: result} = await apiOrError(`/api/boards/${currentBoardId}/repos`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  });
  if (!ok) {
    setRepoStatus((result && result.error) || 'could not register the repo', true);
    return;
  }
  REPO_FORM_FIELDS.forEach(({key, value}) => { bp.repoFields[key].value = value || ''; });
  setRepoStatus('');
  reportRepoTests(result, result.tests, setRepoStatus);
  await onBoardEnter(currentBoardId);
  renderRepoList();
}

// ---- rendering the whole panel ------------------------------------------------------------------

function renderBoardsList() {
  clearChildren(bp.boardListEl);
  if (!boards.length) {
    bp.boardListEl.appendChild(hazardPlaceholderBoards('no boards yet'));
    return;
  }
  boards.forEach(board => bp.boardListEl.appendChild(renderBoardRow(board)));
}

function hazardPlaceholderBoards(text) {
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

async function renderRepoList() {
  const current = boards.find(b => b.id === currentBoardId);
  bp.reposLabelEl.textContent = current ? `repos on ${current.name}` : 'repos - no board selected';
  clearChildren(bp.repoListEl);
  if (!currentBoardId) {
    bp.repoListEl.appendChild(hazardPlaceholderBoards('create a board first'));
    return;
  }
  let repos = [];
  try {
    repos = await api(`/api/boards/${currentBoardId}/repos`);
  } catch (err) {
    bp.repoListEl.appendChild(hazardPlaceholderBoards(`could not load repos: ${err.message}`));
    return;
  }
  if (!repos.length) {
    bp.repoListEl.appendChild(hazardPlaceholderBoards('no repos registered on this board yet'));
    return;
  }
  repos.forEach(repo => bp.repoListEl.appendChild(renderRepoRow(repo)));
}

function renderBoardsPanel() {
  renderSetupNotices();
  renderBoardsList();
  renderRepoList();
}

// ---- open / close ---------------------------------------------------------------------------

function onBoardsKey(evt) {
  if (evt.code === 'Escape') closeBoardsPanel();
}

function openBoardsPanel() {
  if (!bp.backdrop) buildBoardsDom();
  document.body.appendChild(bp.backdrop);
  document.addEventListener('keydown', onBoardsKey);
  bp.confirmDeleteId = null;
  renderBoardsPanel();
  // the panel, not a field: this used to open straight into the name box, so every letter typed
  // itself there and escape closed the whole panel from inside it
  focusPanel(bp.panel);
}

function closeBoardsPanel() {
  if (!bp.backdrop || !bp.backdrop.parentNode) return;
  // an unanswered question goes with the panel; the push-main step stays until it is done
  bp.testsNotice = null;
  bp.folderConfirm = null;
  document.removeEventListener('keydown', onBoardsKey);
  bp.backdrop.remove();
  reenterIfFocusLost();
}

function toggleBoardsPanel() {
  if (bp.backdrop && bp.backdrop.parentNode) closeBoardsPanel();
  else openBoardsPanel();
}
