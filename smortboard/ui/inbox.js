// attention inbox (n) - every card across every board waiting on fabian, in one panel.
// NOT a Menu, same reason the prompt editor (board.js) is not one: Menu hijacks arrow/enter/escape
// on the whole document while open, which would eat every one of those typed into the answer
// input. built from the same modal-backdrop / panel-floating pair.
//
// relies on globals board.js already defines: api, escapeHtml, showRun, onBoardEnter,
// currentBoardId, indicateFocus, activateTab, jumpToCard.

const INDICATOR_POLL_MS = 10000;

const ib = {backdrop: null, panel: null, listEl: null, rows: []};
let indicatorEl = null;

// ---- top-right indicator, polled independently of the panel being open ------------------------

function buildIndicator() {
  // NOT appended inside #board-bar: board.js's renderBoardBar() does `bar.innerHTML = ''` on
  // every board list load, which would wipe out a child living there. Fixed to the viewport
  // instead, at the board bar's top-right corner, so it survives every re-render untouched.
  const el = document.createElement('div');
  el.className = 'attention-indicator dim';
  el.title = 'attention inbox (n)';
  el.onclick = () => openInboxPanel();
  document.body.appendChild(el);
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

// ---- the panel itself -------------------------------------------------------------------------

function buildInboxDom() {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop inbox-backdrop';
  const panel = document.createElement('div');
  panel.className = 'panel-floating inbox-panel';

  const title = document.createElement('div');
  title.className = 'popup-title';
  title.textContent = 'attention inbox';
  const rule = document.createElement('div');
  rule.className = 'h-divider';

  const list = document.createElement('div');
  list.className = 'inbox-list';

  panel.append(title, rule, list);
  backdrop.appendChild(panel);
  backdrop.addEventListener('mousedown', evt => { if (evt.target === backdrop) closeInboxPanel(); });

  Object.assign(ib, {backdrop, panel, listEl: list});
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

// built with createElement/appendChild, not innerHTML - the test dom stub does not parse innerHTML
// strings back into a tree, same reason the prompt editor's role row is built this way (board.js)
function buildInboxRow(row) {
  const rowEl = document.createElement('div');
  rowEl.className = 'inbox-row';
  rowEl.dataset.cardId = row.card_id;

  rowEl.appendChild(document.createElement('div')).className = 'h-divider';

  const head = document.createElement('div');
  head.className = 'inbox-row-head';
  const title = document.createElement('span');
  title.className = 'inbox-title toggle';
  title.textContent = row.title;
  title.onclick = () => {
    closeInboxPanel();
    jumpToCard(row.card_id, ib.rows.map(r => ({card_id: r.card_id, board_id: r.board_id})));
  };
  const board = document.createElement('span');
  board.className = 'field-label';
  board.textContent = row.board_name;
  const reason = document.createElement('span');
  reason.className = 'field-label inbox-reason';
  reason.textContent = row.reason;
  const since = document.createElement('span');
  since.className = 'field-label inbox-since';
  since.textContent = row.since;
  head.append(title, board, reason, since);

  const question = document.createElement('div');
  question.className = 'inbox-question';
  question.textContent = row.question || '(no question text)';

  const answerRow = document.createElement('div');
  answerRow.className = 'inbox-answer-row';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'inbox-answer text-field';
  input.placeholder = 'answer, then enter';
  const status = document.createElement('span');
  status.className = 'inbox-status';
  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); closeInboxPanel(); return; }
    if (evt.code !== 'Enter') return;
    evt.preventDefault();
    sendAnswer(row.card_id, input, status);
  });
  answerRow.append(input, status);

  rowEl.append(head, question, answerRow);
  return rowEl;
}

// works on both the real DOM (where .children is read-only) and the test stub - child.remove()
// is defined on both, unlike reassigning .innerHTML or .children
function clearChildren(el) {
  [...el.children].forEach(child => child.remove());
}

function renderInbox() {
  clearChildren(ib.listEl);
  if (!ib.rows.length) {
    ib.listEl.appendChild(hazardPlaceholder('nothing is waiting on you'));
    return;
  }
  ib.rows.forEach(row => ib.listEl.appendChild(buildInboxRow(row)));
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
  renderInbox();
}

function onInboxKey(evt) {
  if (evt.code === 'Escape') closeInboxPanel();
}

function openInboxPanel() {
  if (!ib.backdrop) buildInboxDom();
  document.body.appendChild(ib.backdrop);
  document.addEventListener('keydown', onInboxKey);
  loadInbox();
}

function closeInboxPanel() {
  if (!ib.backdrop || !ib.backdrop.parentNode) return;
  document.removeEventListener('keydown', onInboxKey);
  ib.backdrop.remove();
  reenterIfFocusLost();
}

function toggleInboxPanel() {
  if (ib.backdrop && ib.backdrop.parentNode) closeInboxPanel();
  else openInboxPanel();
}

startAttentionIndicator();
