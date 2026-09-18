// attention inbox (n) - every card across every board waiting on the operator, in one panel.
// NOT a Menu, same reason the prompt editor (board.js) is not one: Menu hijacks arrow/enter/escape
// on the whole document while open, which would eat every one of those typed into the answer
// input. built from the same modal-backdrop / panel-floating pair.
//
// relies on globals board.js already defines: api, escapeHtml, showRun, onBoardEnter,
// currentBoardId, indicateFocus, activateTab, jumpToCard, boards.
//
// a plain vertical list of board-style attention cards - no pile, no fan, no fixed card height.
// the list scrolls when there are many; a single card never does.

const INDICATOR_POLL_MS = 10000;

// header + list; scope remembered across opens for the life of the page, reset on reload
const ib = {backdrop: null, panel: null, headerLabel: null, listEl: null, rows: [], scope: null, scopes: []};
let indicatorEl = null;

// a short human label for the reason code, the first thing a card says - falls back to the raw
// code for anything attention.py adds later that this map hasn't caught up with yet
const REASON_LABELS = {
  AGENT_QUESTION: 'question',
  LEASE_CONFLICT: 'needs a file outside its lease',
  TESTS_FAILED: 'tests failed',
  REVIEW_REJECTED: 'review rejected',
  USAGE_LIMIT: 'usage limit',
  CRASH: 'crashed',
  DEPENDENCY_REJECTED: 'dependency rejected',
  MERGE_CONFLICT: 'merge conflict',
  API_UNREACHABLE: 'api unreachable',
};

function reasonLabel(reason) {
  return REASON_LABELS[reason] || reason;
}

// "3h ago" rather than an iso stamp - how long it has waited is the part worth reading
function sinceLabel(iso, now = Date.now()) {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return iso || '';
  const minutes = Math.max(0, Math.round((now - then) / 60000));
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)}h ago`;
  return `${Math.round(minutes / 1440)}d ago`;
}

// ---- top-right indicator, polled independently of the panel being open ------------------------

function buildIndicator() {
  // lives in board.js's bar corner, beside the queue status and settings, never inside #board-bar
  const el = document.createElement('div');
  el.className = 'attention-indicator dim';
  el.title = 'attention inbox (n)';
  el.onclick = () => openInboxPanel();
  barCorner().appendChild(el);
  return el;
}

function renderIndicator(count) {
  if (!indicatorEl) return;
  indicatorEl.textContent = count > 0 ? String(count) : '0';
  indicatorEl.classList.toggle('dim', count === 0);
}

async function pollAttentionCount() {
  try {
    const rows = await api('/api/attention');
    renderIndicator(rows.length);
  } catch {
    // a failed poll leaves the last known count showing rather than flashing to zero
  }
}

function startAttentionIndicator() {
  indicatorEl = buildIndicator();
  if (!indicatorEl) return;
  pollAttentionCount();
  const timer = setInterval(pollAttentionCount, INDICATOR_POLL_MS);
  // node keeps a bare setInterval alive forever - real browsers ignore unref, the js test suite
  // (which runs this file directly with node, not a browser) needs it to let the process exit
  timer.unref?.();
}

// ---- scopes: all boards, then every board that actually has a waiting card, in board order -----

// `boards` (board.js) carries the real board order; a test bundle or an empty install may leave it
// unpopulated, so fall back to the order boards first appear in the rows themselves
function boardOrder(rows) {
  if (typeof boards !== 'undefined' && boards && boards.length) return boards.map(b => b.id);
  const seen = [];
  rows.forEach(row => { if (!seen.includes(row.board_id)) seen.push(row.board_id); });
  return seen;
}

// pure: rows -> the ordered list of scopes, each an id ('all' or a board id), a label and the
// rows it covers. a board with nothing waiting gets no scope at all - there is nothing to switch to
function computeScopes(rows) {
  const scopes = [{key: 'all', label: 'all boards', rows}];
  const names = new Map(rows.map(r => [r.board_id, r.board_name]));
  boardOrder(rows).forEach(boardId => {
    const boardRows = rows.filter(r => r.board_id === boardId);
    if (!boardRows.length) return;
    scopes.push({key: boardId, label: names.get(boardId) || boardId, rows: boardRows});
  });
  return scopes;
}

// the scope to open on: the current board if it has waiting cards, otherwise all boards
function pickInitialScope(scopes) {
  if (typeof currentBoardId !== 'undefined' && currentBoardId && scopes.some(s => s.key === currentBoardId)) {
    return currentBoardId;
  }
  return 'all';
}

// a previously chosen scope survives redraws (poll, answering) as long as it still holds cards -
// once it empties this falls back to all boards, same as a fresh open with nothing current
function resolveScope(scopes, wanted) {
  if (wanted && scopes.some(s => s.key === wanted)) return wanted;
  return pickInitialScope(scopes);
}

function scopeByKey(key) {
  return ib.scopes.find(s => s.key === key) || ib.scopes[0];
}

// ---- the panel itself -------------------------------------------------------------------------

function buildInboxDom() {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop inbox-backdrop';
  const panel = document.createElement('div');
  panel.className = 'panel-floating inbox-panel';

  const header = document.createElement('div');
  header.className = 'inbox-header';
  const prev = document.createElement('span');
  prev.className = 'inbox-nav toggle inbox-prev';
  prev.textContent = '←';
  prev.onclick = () => cycleScope(-1);
  const label = document.createElement('span');
  label.className = 'inbox-scope-label';
  const next = document.createElement('span');
  next.className = 'inbox-nav toggle inbox-next';
  next.textContent = '→';
  next.onclick = () => cycleScope(1);
  header.append(prev, label, next);

  const list = document.createElement('div');
  list.className = 'inbox-list';
  list.tabIndex = -1;

  panel.append(header, list);
  backdrop.appendChild(panel);
  backdrop.addEventListener('mousedown', evt => { if (evt.target === backdrop) closeInboxPanel(); });

  Object.assign(ib, {backdrop, panel, headerLabel: label, listEl: list});
  wireInboxListFocus(list);
  return backdrop;
}

function hazardPlaceholder(text) {
  const box = document.createElement('div');
  box.className = 'hazard-stripes hazard-placeholder';
  const label = document.createElement('span');
  label.className = 'hazard-label';
  label.textContent = text;
  box.appendChild(label);
  return box;
}

function renderScopeHeader() {
  const scope = scopeByKey(ib.scope);
  ib.headerLabel.textContent = `${scope.label} (${scope.rows.length})`;
}

function cycleScope(dir) {
  if (!ib.scopes.length) return;
  const idx = ib.scopes.findIndex(s => s.key === ib.scope);
  const nextScope = ib.scopes[(idx + dir + ib.scopes.length) % ib.scopes.length];
  ib.scope = nextScope.key;
  ib.focusIndex = null;
  renderScopeHeader();
  renderInboxList();
}

// a short 1-2 line preview of the question/note, full text kept in the title attribute so nothing
// is actually lost - the card is an overview, not the whole novel
function buildSummary(row) {
  const el = document.createElement('div');
  el.className = 'inbox-summary';
  el.textContent = row.question || '(no question text)';
  el.title = row.question || '';
  return el;
}

// built with createElement/appendChild, not innerHTML - the test dom stub does not parse innerHTML
// strings back into a tree, same reason the prompt editor's role row is built this way (board.js).
// the card is a board-style overview: reason first and prominent, then title, board, a short
// summary of what it needs - the lease/answer controls only show up once this card has focus
function buildInboxCard(row, idx, focused) {
  const card = document.createElement('div');
  card.className = 'row card card-strip card-attention inbox-card focus-glow';
  card.tabIndex = -1;
  card.dataset.cardId = row.card_id;
  card.dataset.idx = String(idx);

  const reasonRow = document.createElement('div');
  reasonRow.className = 'inbox-reason-row';
  const reason = document.createElement('span');
  reason.className = 'inbox-reason';
  reason.textContent = reasonLabel(row.reason);
  const since = document.createElement('span');
  since.className = 'inbox-since';
  since.textContent = sinceLabel(row.since);
  reasonRow.append(reason, since);

  const title = document.createElement('div');
  title.className = focused ? 'inbox-title inbox-control' : 'inbox-title';
  title.tabIndex = -1;
  title.textContent = row.title;
  title.onclick = () => {
    // A GLANCE, NOT A DEPARTURE. the inbox is a queue you work through, so opening a card from it
    // is a look at one row - closing that card comes back here, at the row you were on, rather
    // than leaving you on the board with the queue gone
    glanceAtCard(row.card_id);
  };

  const board = document.createElement('div');
  board.className = 'field-label inbox-board';
  board.textContent = row.board_name;

  const action = document.createElement('div');
  action.className = 'inbox-action';
  action.textContent = `next: ${row.action || row.hint || 'open the card'}`;

  card.append(reasonRow, title, board, action, buildSummary(row));

  card.addEventListener('keydown', evt => onCardKey(evt, card));
  // with the mouse enabled (settings, o), hovering focuses the row the arrows would - the same
  // focusin path, so the panel's own focus index never disagrees with what is lit
  card.addEventListener('mouseenter', () => hoverFocus(card, () => card.focus()));

  if (!focused) return card;

  const extras = [];
  if (row.reason === 'LEASE_CONFLICT' && row.wants && row.wants.length) {
    extras.push(buildLeaseApproveRow(row));
  }

  // an answer cannot move a decision or a rate limit - the action line already says what will
  if (row.answerable === false) {
    card.append(...extras);
    return card;
  }

  const answerRow = document.createElement('div');
  answerRow.className = 'inbox-answer-row';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'inbox-answer text-field';
  input.placeholder = '/ to answer, then enter';
  const status = document.createElement('span');
  status.className = 'inbox-status';
  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); card.focus(); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    sendAnswer(row.card_id, input, status);
  });
  answerRow.append(input, status);

  // the answer field stays too - the operator may prefer to tell the agent to leave the file be
  // instead of widening the lease for it
  card.append(...extras, answerRow);
  card.dataset.answerable = 'true';
  return card;
}

// the board's card keys: space (or enter) steps into the focused card onto its first control,
// arrows move between its controls, space presses one, / puts the cursor in its answer field.
// stopped here so the board's own space and / never act on the card behind the panel
function onCardKey(evt, card) {
  const onControl = evt.target.classList?.contains('inbox-control');
  if (evt.target !== card && !onControl) return;
  if (evt.code === 'Escape' && evt.target === card) { closeInboxPanel(); return; }
  if (evt.code === 'Slash' || evt.key === '/') {
    const input = card.querySelector('.inbox-answer');
    evt.preventDefault();
    evt.stopPropagation();
    if (input) input.focus();
    return;
  }
  if (evt.code !== 'Space' && evt.code !== 'Enter') return;
  evt.preventDefault();
  evt.stopPropagation();
  if (onControl) { evt.target.onclick?.(); return; }
  // ONE PRESS WHERE THERE IS ONLY ONE THING TO DO. stepping in and then pressing again made sense
  // when a card had several controls, and reads as "it selected the title" on the common row, whose
  // only control IS the title. a row with more than one still steps in, where the choice is real
  const controls = Array.from(card.querySelectorAll('.inbox-control'));
  if (controls.length === 1) { controls[0].onclick?.(); return; }
  if (controls.length) controls[0].focus();
}

// up/down between the controls of the card focus is inside; at either end focus stays put
function moveInsideCard(control, dir) {
  const card = control.closest('.inbox-card');
  const controls = Array.from(card.querySelectorAll('.inbox-control'));
  const next = controls[controls.indexOf(control) + dir];
  if (next) next.focus();
}

// the "wants: <paths>" line and its approve control - one click widens exactly those paths and
// resumes the card, reusing the `toggle` class other inbox controls already use as a button
function buildLeaseApproveRow(row) {
  const wrap = document.createElement('div');
  wrap.className = 'inbox-lease-approve';

  const wants = document.createElement('div');
  wants.className = 'inbox-wants';
  wants.textContent = `wants: ${row.wants.join(', ')}`;

  const button = document.createElement('span');
  button.className = 'inbox-approve toggle inbox-control';
  button.tabIndex = -1;
  button.textContent = 'approve';
  const status = document.createElement('span');
  status.className = 'inbox-status';
  button.onclick = () => approveLease(row.card_id, row.wants, button, status);

  wrap.append(wants, button, status);
  return wrap;
}

async function approveLease(cardId, paths, button, status) {
  button.classList.add('dim');
  status.textContent = 'approving...';
  status.className = 'inbox-status';
  const {ok, body} = await apiOrError(`/api/cards/${cardId}/lease/approve`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({paths}),
  });
  if (!ok) {
    status.textContent = (body && body.error) || 'could not approve';
    status.className = 'inbox-status inbox-error';
    button.classList.remove('dim');
    return;
  }
  status.textContent = 'resumed';
  status.className = 'inbox-status inbox-ok';
  pollAttentionCount();
  if (currentBoardId) onBoardEnter(currentBoardId);
  loadInbox();
}

// works on both the real DOM (where .children is read-only) and the test stub - child.remove()
// is defined on both, unlike reassigning .innerHTML or .children
function clearChildren(el) {
  [...el.children].forEach(child => child.remove());
}

// redraws the list from ib.scope alone: a plain vertical list of cards, no pile/fan regime to pick
// - the only thing that changes per card is whether it's the focused one (answer/lease controls)
function renderInboxList() {
  clearChildren(ib.listEl);
  const scope = scopeByKey(ib.scope);
  const rows = scope ? scope.rows : [];
  if (!rows.length) {
    ib.focusIndex = null;
    const label = scope && scope.key !== 'all' ? ` on ${scope.label}` : '';
    ib.listEl.appendChild(hazardPlaceholder(`nothing is waiting on you${label}`));
    return;
  }
  // coming back from a glance: land on the row you went into, wherever it sits now. the card may
  // have left the inbox entirely while you were in it (you answered it), in which case this finds
  // nothing and the usual first-row rule takes over
  if (ib.returnToCardId != null) {
    const back = rows.findIndex(row => row.card_id === ib.returnToCardId);
    ib.returnToCardId = null;
    if (back >= 0) ib.focusIndex = back;
  }
  // a card is always the one with keyboard focus, from the moment the panel opens - otherwise
  // arrowdown/arrowup have nothing to move from, and no card would ever wear the focus glow
  if (ib.focusIndex == null || ib.focusIndex >= rows.length) ib.focusIndex = 0;
  rows.forEach((row, idx) => {
    const card = buildInboxCard(row, idx, idx === (ib.focusIndex ?? -1));
    ib.listEl.appendChild(card);
  });
  focusInboxIndex(ib.focusIndex);
}

// opens one row's card over the board and comes back to this panel when it closes. the card panel
// is ui_base's expander (card_panel.js), and its close is the only signal we need - no timer, no
// polling the dom. `once`: a second glance registers its own return
function glanceAtCard(cardId) {
  const roster = ib.rows.map(r => ({card_id: r.card_id, board_id: r.board_id}));
  const returning = {scope: ib.scope, cardId};
  closeInboxPanel();
  Promise.resolve(jumpToCard(cardId, roster)).then(() => {
    // only arm the return once a card really opened: a row whose strip is not on the board (a
    // deleted card, a board that failed to load) must not leave a watcher to fire on the next
    // unrelated card you open
    const strip = document.querySelector(`.card-strip[data-card-id="${cardId}"]`);
    if (strip) afterCardCloses(() => reopenInboxAt(returning));
  // jumpToCard loads the card's board when it is not the open one, so it can fail on the network.
  // nothing to arm then, and the panel is already closed - come straight back instead of stranding
  }).catch(() => reopenInboxAt(returning));
}

// back into the panel on the same scope, with the glanced row focused again
function reopenInboxAt({scope, cardId}) {
  if (ib.backdrop?.parentNode) return; // already back, nothing to restore
  if (scope) ib.scope = scope;
  ib.returnToCardId = cardId;
  openInboxPanel();
}

function focusInboxIndex(idx) {
  const cards = Array.from(ib.listEl.children);
  cards.forEach(el => { el.tabIndex = -1; });
  if (idx == null) return;
  const target = cards.find(el => el.dataset.idx === String(idx));
  if (!target) return;
  target.tabIndex = 0;
  target.focus();
  target.scrollIntoView?.({block: 'nearest'});
  indicateFocus?.(target);
}

// ArrowUp/ArrowDown move focus card by card - the list itself scrolls the newly focused card into
// view, no card is ever asked to scroll its own content
function onInboxListKey(evt) {
  if (evt.code !== 'ArrowDown' && evt.code !== 'ArrowUp') return;
  if (evt.target.closest?.('.inbox-answer')) return;
  if (evt.target.classList?.contains('inbox-control')) {
    evt.preventDefault();
    evt.stopPropagation();
    moveInsideCard(evt.target, evt.code === 'ArrowDown' ? 1 : -1);
    return;
  }
  const scope = scopeByKey(ib.scope);
  const rows = scope ? scope.rows : [];
  const row = evt.target.closest?.('[data-idx]');
  const idx = row ? Number(row.dataset.idx) : (ib.focusIndex ?? -1);
  const dir = evt.code === 'ArrowDown' ? 1 : -1;
  const nextIdx = idx + dir;
  if (nextIdx < 0 || nextIdx >= rows.length) return;
  evt.preventDefault();
  evt.stopPropagation();
  ib.focusIndex = nextIdx;
  renderInboxList();
}

function wireInboxListFocus(listEl) {
  listEl.addEventListener('keydown', onInboxListKey, {capture: true});
  listEl.addEventListener('focusin', evt => {
    const row = evt.target.closest?.('[data-idx]');
    if (!row) return;
    if (ib.focusIndex !== Number(row.dataset.idx)) {
      ib.focusIndex = Number(row.dataset.idx);
      renderInboxList();
    }
  });
  listEl.addEventListener('click', evt => {
    const row = evt.target.closest?.('[data-idx]');
    if (row) row.focus();
  });
}

async function sendAnswer(cardId, input, status) {
  const message = input.value.trim();
  if (!message) return;
  input.disabled = true;
  status.textContent = 'sending...';
  status.className = 'inbox-status';
  const {ok, body} = await apiOrError(`/api/cards/${cardId}/answer`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message}),
  });
  if (!ok) {
    status.textContent = (body && body.error) || 'could not answer';
    status.className = 'inbox-status inbox-error';
    input.disabled = false;
    return;
  }
  // the row stays put and says "resumed" - it drops out on the panel's next load, same moment the
  // board re-renders and no longer shows the card as blocked
  status.textContent = 'resumed';
  status.className = 'inbox-status inbox-ok';
  // A DISABLED INPUT DROPS THE KEYBOARD ON THE FLOOR. it was disabled while the answer posted, and
  // leaving it that way put focus on <body>: the list's arrow keys are bound to the list and never
  // fired again, and escape found no card above body so it closed the whole panel instead of
  // stepping back one level. hand the keyboard back to the row, which is where the queue continues
  input.disabled = false;
  input.value = '';
  input.closest?.('.inbox-card')?.focus();
  pollAttentionCount();
  if (currentBoardId) onBoardEnter(currentBoardId);
}

async function loadInbox() {
  clearChildren(ib.listEl);
  ib.listEl.appendChild(hazardPlaceholder('loading...'));
  try {
    ib.rows = await api('/api/attention');
  } catch (err) {
    clearChildren(ib.listEl);
    ib.listEl.appendChild(hazardPlaceholder(`could not load: ${err.message}`));
    return;
  }
  ib.scopes = computeScopes(ib.rows);
  ib.scope = resolveScope(ib.scopes, ib.scope);
  renderScopeHeader();
  renderInboxList();
}

// capture, not bubble: the board tab bar wires its own ArrowLeft/ArrowRight (shell.js) straight
// on whichever tab element still holds dom focus from before the panel opened, and a bubble-phase
// listener here would only run after that has already switched boards. capture runs first, and
// stopPropagation keeps every key the panel owns from ever reaching the board underneath it
function onInboxKey(evt) {
  if (evt.code === 'Escape') {
    evt.stopPropagation();
    // one level back: out of a field or a control onto its card, and only from a card to closed
    const card = evt.target.closest?.('.inbox-card');
    if (card && evt.target !== card) { evt.preventDefault?.(); card.focus(); return; }
    closeInboxPanel();
    return;
  }
  const targetTag = (evt.target?.tagName || evt.target?.tag || '').toUpperCase();
  const inField = targetTag === 'INPUT' || targetTag === 'TEXTAREA';
  if (inField) return;
  if (evt.code === 'ArrowLeft') { evt.stopPropagation(); evt.preventDefault?.(); cycleScope(-1); return; }
  if (evt.code === 'ArrowRight') { evt.stopPropagation(); evt.preventDefault?.(); cycleScope(1); return; }
}

function openInboxPanel() {
  if (!ib.backdrop) buildInboxDom();
  ib.focusIndex = null;
  document.body.appendChild(ib.backdrop);
  document.addEventListener('keydown', onInboxKey, {capture: true});
  loadInbox();
}

function closeInboxPanel() {
  if (!ib.backdrop || !ib.backdrop.parentNode) return;
  document.removeEventListener('keydown', onInboxKey, {capture: true});
  ib.backdrop.remove();
  reenterIfFocusLost();
}

function toggleInboxPanel() {
  if (ib.backdrop && ib.backdrop.parentNode) closeInboxPanel();
  else openInboxPanel();
}

startAttentionIndicator();
