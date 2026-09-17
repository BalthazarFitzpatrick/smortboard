// boards and repos (b) - the only way in today is curl. this panel creates a board (switching to
// it), and registers/edits repos on the current board, so a fresh tester can get going alone.
// NOT a Menu, same reason the prompt editor and inbox aren't - see board.js's note on menu.js
// before assuming Menu fits a panel with real text inputs in it.
//
// relies on globals board.js already defines: api, apiOrError, escapeHtml, boards, currentBoardId,
// loadBoards, onBoardEnter, activateTab, reenterIfFocusLost.

const bp = {backdrop: null, panel: null, boardListEl: null, boardNameInput: null, boardStatusEl: null,
  boardName: '', repoListEl: null, repoStatusEl: null, repoFields: {}, confirmDeleteId: null};

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

  // three ways to start a board, as the first row: a blank one by name, one made from a repo
  // already on this disk, and one from a repo online (not built yet, see the ledger)
  const sourceRow = document.createElement('div');
  sourceRow.className = 'boards-source-row';
  const fromBlank = document.createElement('div');
  fromBlank.className = 'toggle board-source-new';
  fromBlank.textContent = 'new board';
  fromBlank.onclick = () => bp.boardNameInput.focus();
  const fromLocal = document.createElement('div');
  fromLocal.className = 'toggle board-source-local';
  fromLocal.textContent = 'from local repo';
  fromLocal.onclick = () => openLocalRepoPicker(fromLocal);
  const fromOnline = document.createElement('div');
  fromOnline.className = 'toggle board-source-online disabled';
  fromOnline.textContent = 'from online repo';
  fromOnline.title = 'not built yet';
  sourceRow.append(fromBlank, fromLocal, fromOnline);
  panel.appendChild(sourceRow);

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
    if (evt.code === 'Escape') { evt.stopPropagation(); closeBoardsPanel(); return; }
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
    reposLabelEl: reposLabel, repoListEl: repoList, sourceRowEl: sourceRow,
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
      if (evt.code === 'Escape') { evt.stopPropagation(); closeBoardsPanel(); return; }
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
// repos marked. `action` is the one button it offers, shown only for a folder `action.when` accepts
async function openFolderPicker(anchor, {title, action, onError}) {
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
    ...(action.when(where) ? [{kind: 'buttons', buttons: [
      {label: action.label, tone: 'adds', onClick: m => action.run(where.here, m)},
    ]}] : []),
  ];
  menu = new Menu({title, persistent: true, sections: build()});
  menu.openAt(anchor);
}

function openLocalRepoPicker(anchor) {
  return openFolderPicker(anchor, {
    title: 'board from local repo',
    action: {label: 'create board from this repo', when: where => where.repo, run: createBoardFromRepo},
    onError: text => setBoardStatus(text, true),
  });
}

async function createBoardFromRepo(path, menu) {
  setBoardStatus('creating...');
  const {ok, body} = await apiOrError('/api/boards/from-repo', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path}),
  });
  if (!ok) { setBoardStatus((body && body.error) || 'could not create the board', true); return; }
  menu.close();
  setBoardStatus('');
  await loadBoards();
  activateTab(body.board.id);
  await onBoardEnter(body.board.id);
  renderBoardsPanel();
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
    if (evt.code === 'Escape') { evt.stopPropagation(); closeBoardsPanel(); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    saveRepoEdit(repo.id, testInput.value, imageInput.value, status);
  }));
  editRow.append(testInput, imageInput, save, status);

  row.append(head, path, editRow, renderRememberedLeases(repo));
  return row;
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

async function registerRepoFromPanel() {
  if (!currentBoardId) return;
  const body = {};
  REPO_FORM_FIELDS.forEach(({key}) => { body[key] = bp.repoFields[key].value.trim(); });
  bp.repoStatusEl.textContent = 'registering...';
  bp.repoStatusEl.className = 'repo-status';
  const {ok, body: result} = await apiOrError(`/api/boards/${currentBoardId}/repos`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  });
  if (!ok) {
    bp.repoStatusEl.textContent = (result && result.error) || 'could not register the repo';
    bp.repoStatusEl.className = 'repo-status repo-error';
    return;
  }
  REPO_FORM_FIELDS.forEach(({key, value}) => { bp.repoFields[key].value = value || ''; });
  bp.repoStatusEl.textContent = '';
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
  bp.boardNameInput.focus();
}

function closeBoardsPanel() {
  if (!bp.backdrop || !bp.backdrop.parentNode) return;
  document.removeEventListener('keydown', onBoardsKey);
  bp.backdrop.remove();
  reenterIfFocusLost();
}

function toggleBoardsPanel() {
  if (bp.backdrop && bp.backdrop.parentNode) closeBoardsPanel();
  else openBoardsPanel();
}
