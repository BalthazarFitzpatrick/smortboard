// attention inbox (n) - every card across every board waiting on the operator, in one panel.
// NOT a Menu, same reason the prompt editor (board.js) is not one: Menu hijacks arrow/enter/escape
// on the whole document while open, which would eat every one of those typed into the answer
// input. built from the same modal-backdrop / panel-floating pair.
//
// relies on globals board.js already defines: api, escapeHtml, showRun, onBoardEnter,
// currentBoardId, indicateFocus, activateTab, jumpToCard, boards.
// relies on pile.js's pure geometry (loaded before this file): stackHeight, fanHeight, pileSlot,
// MIN_PILED_CARDS, PILE, PEEK, computeColumnLayout, pileByRecency, pileLayerJitter.

const INDICATOR_POLL_MS = 10000;
const INBOX_CARD_GAP_FALLBACK = 10; // matches pile.js's own CARD_GAP fallback

// header + list; scope remembered across opens for the life of the page, reset on reload
const ib = {backdrop: null, panel: null, headerLabel: null, listEl: null, rows: [], scope: null, scopes: []};
let indicatorEl = null;

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

// ---- inbox fit: computeColumnFit's own three regimes (pile.js), mirrored for a fixed landscape
// card height instead of a width-derived square one - columns.js's own copy can't be reused as-is
// since it hardcodes squareCard(width). pure, same shape as computeColumnFit's own return value
function computeInboxFit(total, available, gap, cardHeight) {
  const card = cardHeight;
  if (stackHeight(total, card) <= available) return {regime: 1, n: total, card, piles: false, scrolls: false};
  const fanned = fanHeight(total, card);
  if (fanned <= available || total < MIN_PILED_CARDS) {
    return {regime: 2, n: total, card, piles: false, scrolls: fanned > available};
  }
  const piles = 2 * pileSlot(gap);
  for (let n = total - 1; n >= 1; n--) {
    if (fanHeight(n, card) + piles <= available) return {regime: 3, n, card, piles: true, scrolls: false};
  }
  return {regime: 3, n: 1, card, piles: true, scrolls: true};
}

function inboxCardHeight() {
  const raw = parseFloat(globalThis.getComputedStyle?.(document.documentElement)?.getPropertyValue?.('--inbox-card-height') || '');
  return Number.isFinite(raw) ? raw : 170;
}

// the room the list actually has - it is a flex:1 sibling under the fixed header, so its own
// rendered box already excludes the header without any extra measurement
function availableInboxHeight(listEl) {
  return Math.max(0, listEl.getBoundingClientRect().height);
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
  prev.textContent = '‹';
  prev.onclick = () => cycleScope(-1);
  const label = document.createElement('span');
  label.className = 'inbox-scope-label';
  const next = document.createElement('span');
  next.className = 'inbox-nav toggle inbox-next';
  next.textContent = '›';
  next.onclick = () => cycleScope(1);
  header.append(prev, label, next);

  const list = document.createElement('div');
  list.className = 'inbox-list bucket-rows';
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
  ib.start = 0;
  ib.anchor = 'top';
  renderScopeHeader();
  renderInboxList();
}

// built with createElement/appendChild, not innerHTML - the test dom stub does not parse innerHTML
// strings back into a tree, same reason the prompt editor's role row is built this way (board.js)
function buildInboxCard(row) {
  const card = document.createElement('div');
  card.className = 'row inbox-card focus-glow';
  card.tabIndex = -1;
  card.dataset.cardId = row.card_id;

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
  since.textContent = sinceLabel(row.since);
  head.append(title, board, reason, since);

  // the call to action comes first, above the note it came from, so a scan of the inbox reads as a
  // list of things to do
  const action = document.createElement('div');
  action.className = 'inbox-action';
  action.textContent = `next: ${row.action || row.hint || 'open the card'}`;

  const question = document.createElement('div');
  question.className = 'inbox-question';
  question.textContent = row.question || '(no question text)';

  const extras = [];
  if (row.reason === 'LEASE_CONFLICT' && row.wants && row.wants.length) {
    extras.push(buildLeaseApproveRow(row));
  }

  card.addEventListener('keydown', evt => onCardKey(evt, card));

  // an answer cannot move a decision or a rate limit - the action line already says what will
  if (row.answerable === false) {
    card.append(head, action, question, ...extras);
    return card;
  }

  const answerRow = document.createElement('div');
  answerRow.className = 'inbox-answer-row';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'inbox-answer text-field';
  input.placeholder = 'answer, then enter';
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
  card.append(head, action, question, ...extras, answerRow);
  card.dataset.answerable = 'true';
  return card;
}

// enter on a focused card, not its answer field, puts the cursor there - escape in the field gives
// focus back to the card (above); escape on the card itself closes the panel
function onCardKey(evt, card) {
  if (evt.target !== card) return;
  if (evt.code === 'Enter') {
    const input = card.querySelector('.inbox-answer');
    if (input) { evt.preventDefault(); input.focus(); }
    return;
  }
  if (evt.code === 'Escape') closeInboxPanel();
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
  button.className = 'inbox-approve toggle';
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

// a landscape pile row, the exact desk-pile look columns.js draws for a column (card-pile,
// card-pile-layer, jittered per card id) - every inbox row is an attention card, so the pile
// always wears the attention edge
function buildInboxPile(cards, side) {
  const el = document.createElement('div');
  el.className = 'row card-pile card-pile-attention';
  el.tabIndex = -1;
  el.dataset.pile = 'true';
  el.dataset.side = side;
  const drawn = pileByRecency(cards, side);
  const width = ib.listEl?.getBoundingClientRect?.().width || INBOX_PILE_TILT_WIDTH;
  for (let i = drawn.length - 1; i >= 0; i--) {
    el.appendChild(buildInboxPileLayer(drawn[i], i === 0, width));
  }
  const count = document.createElement('div');
  count.className = 'card-pile-count';
  const badge = document.createElement('span');
  badge.textContent = String(cards.length);
  count.appendChild(badge);
  el.appendChild(count);
  el.addEventListener('click', () => expandInboxPile());
  return el;
}

// the jitter's angle is tuned for a board card about 230px wide; a landscape inbox card is several
// times wider, so the same angle lifts its far corners several times higher - scale it down
const INBOX_PILE_TILT_WIDTH = 230;

function buildInboxPileLayer(row, isTop, width = INBOX_PILE_TILT_WIDTH) {
  const {dx, dy, rot} = pileLayerJitter(row.card_id);
  const tilt = rot * Math.min(1, INBOX_PILE_TILT_WIDTH / Math.max(width, 1));
  const layer = document.createElement('div');
  layer.className = isTop ? 'card-pile-layer card-pile-top' : 'card-pile-layer';
  layer.dataset.pileCard = row.card_id;
  layer.style.transform = `translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px) rotate(${tilt.toFixed(2)}deg)`;
  layer.style.borderColor = 'var(--fill-attention)';
  if (!isTop) return layer;
  const title = document.createElement('div');
  title.className = 'card-title';
  title.textContent = row.title || '';
  layer.appendChild(title);
  return layer;
}

// clicking a pile expands the whole scope flat, same affordance a board column's pile gives
function expandInboxPile() {
  ib.expanded = true;
  renderInboxList();
}

// works on both the real DOM (where .children is read-only) and the test stub - child.remove()
// is defined on both, unlike reassigning .innerHTML or .children
function clearChildren(el) {
  [...el.children].forEach(child => child.remove());
}

// applies the fit numbers to the drawn rows, same as columns.js's fitColumn: every card the fit's
// one height, every pile PILE, and each card's join as a negative top margin cancelling the gap
function applyInboxFit(fit, layout, gap) {
  ib.listEl.classList.toggle('bucket-rows-piled', fit.regime !== 1);
  ib.listEl.classList.toggle('bucket-rows-scrolls', fit.regime !== 1 && fit.scrolls);
  ib.listEl.classList.toggle('bucket-rows-bottom', fit.piles && !fit.scrolls && layout.anchor === 'bottom');
  Array.from(ib.listEl.children).forEach(el => {
    if (el.classList.contains('card-pile')) {
      el.style.height = `${PILE}px`;
      return;
    }
    const join = el.dataset.join;
    el.classList.toggle('card-covered', el.dataset.covered === 'true');
    el.style.height = `${fit.card}px`;
    el.style.marginTop = join === 'peek' ? `${-(fit.card - PEEK + gap)}px` : join === 'flush' ? `${-gap}px` : '';
  });
}

// redraws the list from ib.scope alone: filters the rows, picks the regime (computeInboxFit),
// lays out the rows (computeColumnLayout, shared with the board's own columns) and applies it
function renderInboxList() {
  clearChildren(ib.listEl);
  const scope = scopeByKey(ib.scope);
  const rows = scope ? scope.rows : [];
  if (!rows.length) {
    const label = scope && scope.key !== 'all' ? ` on ${scope.label}` : '';
    ib.listEl.appendChild(hazardPlaceholder(`nothing is waiting on you${label}`));
    return;
  }
  const gap = parseFloat(globalThis.getComputedStyle?.(ib.listEl)?.getPropertyValue?.('--card-gap') || '') || INBOX_CARD_GAP_FALLBACK;
  const available = availableInboxHeight(ib.listEl);
  const natural = computeInboxFit(rows.length, available, gap, inboxCardHeight());
  const fit = ib.expanded
    ? {...natural, regime: 1, n: rows.length, piles: false, scrolls: natural.regime > 1}
    : natural;
  const layout = computeColumnLayout(rows, ib.focusIndex ?? null, ib.start || 0, ib.anchor || 'top', fit);
  ib.start = layout.start;
  ib.anchor = layout.anchor;
  layout.rows.forEach(entry => {
    if (entry.type === 'pile') {
      ib.listEl.appendChild(buildInboxPile(entry.cards, entry.side));
      return;
    }
    const card = buildInboxCard(entry.card);
    card.dataset.idx = String(entry.idx);
    card.dataset.join = entry.join;
    card.dataset.covered = String(entry.covered);
    ib.listEl.appendChild(card);
  });
  applyInboxFit(fit, layout, gap);
  focusInboxIndex(ib.focusIndex);
}

function focusInboxIndex(idx) {
  const cards = Array.from(ib.listEl.children);
  cards.forEach(el => { if (!el.classList.contains('card-pile')) el.tabIndex = -1; });
  if (idx == null) return;
  const target = cards.find(el => el.dataset.idx === String(idx));
  if (!target) return;
  target.tabIndex = 0;
  target.focus();
  indicateFocus?.(target);
}

// ArrowUp/ArrowDown move focus card by card, the group moving with it (placeGroup, inside
// computeColumnLayout) exactly as a board column's pile does
function onInboxListKey(evt) {
  if (evt.code !== 'ArrowDown' && evt.code !== 'ArrowUp') return;
  if (evt.target.closest?.('.inbox-answer')) return;
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
    ib.focusIndex = Number(row.dataset.idx);
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
  if (evt.code === 'Escape') { evt.stopPropagation(); closeInboxPanel(); return; }
  const targetTag = (evt.target?.tagName || evt.target?.tag || '').toUpperCase();
  const inField = targetTag === 'INPUT' || targetTag === 'TEXTAREA';
  if (inField) return;
  if (evt.code === 'ArrowLeft') { evt.stopPropagation(); evt.preventDefault?.(); cycleScope(-1); return; }
  if (evt.code === 'ArrowRight') { evt.stopPropagation(); evt.preventDefault?.(); cycleScope(1); return; }
}

function openInboxPanel() {
  if (!ib.backdrop) buildInboxDom();
  ib.expanded = false;
  ib.focusIndex = null;
  ib.start = 0;
  ib.anchor = 'top';
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
