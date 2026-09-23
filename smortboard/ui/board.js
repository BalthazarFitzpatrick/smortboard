// smortboard interface wiring - configuration of ui_base components against the json api.
// no visual primitive is defined here; anything that looks like one belongs in ui_base instead.
//
// THE SHELL, NOT THE WHOLE APP. card render/CTA/menus live in card_panel.js, the panel's masonry
// layout in columns.js, mission control and workforce in chat.js, and the keyboard model in
// shortcuts.js - each a file a card-ui card can lease on its own, listed before this one in
// index.html. this file keeps the board bar, the run/stop/accept/reject lifecycle, the roster,
// usage and prompt-editor panels, the shared api/escapeHtml helpers, and the bootstrap that wires
// everything together.

const STATUSES = ['todo', 'doing', 'checking', 'accepted', 'rejected'];
// the board's own column order, not the data model - attention is not a stored status (a card
// keeps its real one underneath) but a presentation column between doing and checking, pulled
// out by isAttentionCard (columns.js) rather than driven by card.status
const COLUMNS = ['todo', 'doing', 'attention', 'checking', 'accepted', 'rejected'];

// which column a card actually renders in: a card the queue is holding shows in doing as pending
// whatever its stored status says, then attention wins over its own real status - excluding a
// card the board is already retrying itself (isAttentionCard already reads handled_by_board)
function columnFor(card) {
  if (isPendingCard(card)) return 'doing';
  return isAttentionCard(card) ? 'attention' : card.status;
}

let boards = [];
let currentBoardId = null;
let bucketsApi = null;
let grouped = false; // g toggles this; workstream layout itself ships post-v1
let openCard = null; // {cardId, expander, input} while a card panel is open

// a 401 means this tab has no api key cookie - opened by hand rather than from the printed link
function noteMissingKey(res) {
  if (res.status !== 401 || document.getElementById('key-missing')) return;
  const note = document.createElement('div');
  note.id = 'key-missing';
  note.className = 'hazard-label';
  note.textContent = 'this tab has no api key - open the link smortboard printed in the terminal';
  document.body.prepend(note);
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  noteMissingKey(res);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.status === 204 ? null : res.json();
}

// like api(), but a 409 is a normal outcome here (accept/reject refusal) rather than a throw -
// the caller needs the server's error text, which plain api() discards
async function apiOrError(path, opts) {
  const res = await fetch(path, opts);
  noteMissingKey(res);
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  return {ok: res.ok, status: res.status, body};
}

// ---- board bar ------------------------------------------------------------------

// the bar's right-hand group - queue status, settings, attention count - in one fixed flex row, so
// they never draw over each other. outside #board-bar because renderBoardBar() wipes its children;
// the bar reserves the group's width (--bar-corner-width) so board tabs never run under it
function barCorner() {
  let el = document.getElementById('bar-corner');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'bar-corner';
  el.className = 'bar-corner';
  document.body.appendChild(el);
  if (typeof ResizeObserver === 'function') {
    new ResizeObserver(() => {
      document.documentElement.style.setProperty('--bar-corner-width', `${el.offsetWidth}px`);
    }).observe(el);
  }
  return el;
}

// ---- beta: submit issue ------------------------------------------------------------------
// the repo's own bug form, FIRST in the corner group so it reads before the board's own numbers.
// an <a>, not a .toggle div like the settings button: tab reaches it and enter opens it, so it is
// operable without a mouse without taking one of the few single keys still free
const ISSUE_FORM_URL =
  'https://github.com/BalthazarFitzpatrick/smortboard/issues/new?template=bug.yml';

function buildIssueButton() {
  const link = document.createElement('a');
  link.id = 'issue-button';
  link.className = 'toggle issue-button';
  link.textContent = 'beta: submit issue';
  link.href = ISSUE_FORM_URL;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  const corner = barCorner();
  corner.insertBefore(link, corner.firstChild);
  return link;
}

function renderBoardBar() {
  const bar = document.getElementById('board-bar');
  bar.innerHTML = '';
  boards.forEach(board => {
    const btn = document.createElement('div');
    // focus-glow-soft is the quiet focus treatment (ui_base).
    // FOCUS AND ACTIVE SAY DIFFERENT THINGS: the glow is the tab the keyboard is on, the accent
    // fill (.board-bar .nav-tab.active) is the board being shown - the active tab focused wears both
    btn.className = 'nav-tab toggle focus-glow focus-glow-soft';
    btn.dataset.tab = board.id;
    btn.textContent = board.name;
    bar.appendChild(btn);
  });
}

async function loadBoards() {
  boards = await api('/api/boards');
  renderBoardBar();
  renderEmptyState(boards.length === 0);
  if (boards.length === 0) {
    currentBoardId = null;
    renderBoardMergeMode();
    return;
  }
  initShell({onEnter: onBoardEnter, fallback: boards[0]?.id || ''});
}

// a fresh install has no boards - say so instead of showing five silent empty columns. the columns
// are hidden, not removed: the first board created from b renders into them
function renderEmptyState(empty) {
  const row = document.getElementById('bucket-row');
  row.querySelectorAll('.bucket').forEach(bucket => { bucket.hidden = empty; });
  row.querySelector('.empty-state')?.remove();
  if (!empty) return;
  const box = document.createElement('div');
  box.className = 'hazard-stripes hazard-placeholder empty-state';
  box.innerHTML = `<span class="hazard-label">no boards yet</span>
    <span class="hazard-note">press b to create a board and register a repo</span>
    <span class="hazard-note">press h to check the machine is ready</span>`;
  row.appendChild(box);
}

async function onBoardEnter(boardId) {
  currentBoardId = boardId;
  renderBoardMergeMode();
  // forget the old board and seed polling from what we actually draw
  followedCardStates = null;
  const cards = await api(`/api/boards/${boardId}/cards`);
  renderBuckets(cards);
  followedCardStates = new Map(cards.map(c => [c.id, `${c.status}|${c.updated_at}`]));
  // resumes this board's send queue (a reload landed here with something still unsent) whether or
  // not mission control is open - a message keeps retrying in the background either way.
  // guarded: messageQueue.js is a separate script (see index.html's load order) and some isolated
  // test bundles load board.js without it - this backs off quietly rather than throwing there
  if (typeof createMessageQueue === 'function') mcQueueFor(boardId);
  // mission control is per-board, so a board switch while it is open reloads its conversation
  if (drawers.right && drawers.right.isOpen()) loadMissionControl();
  // MALL CAM ENTERS ON ITS OWN. a board just focus-selected (rather than a specific card opened)
  // has nothing focused on it yet, so a stale pin from the board just left would otherwise survive
  // the switch and show a card that no longer belongs to this board's roster. clearing it here
  // means the next time workforce opens (already open or not), resolveWorkforceTarget falls back
  // to cycling this board's own active cards, exactly the state a focus-selected board should open
  // into - without forcing the drawer open itself, which would fight its own toggle key
  resetWorkforceTarget();
  if (drawers.left && drawers.left.isOpen()) await loadWorkforce();
}

function renderBoardMergeMode() {
  const board = boards.find(b => b.id === currentBoardId);
  const free = board?.merge_mode === 'free';
  document.getElementById('bucket-row')?.classList.toggle('edge-pulse', free);
  let label = document.getElementById('merge-mode-label');
  if (!label) {
    label = document.createElement('span');
    label.id = 'merge-mode-label';
    label.className = 'hazard-note';
    barCorner().appendChild(label);
  }
  label.hidden = !board;
  label.textContent = free ? 'free merge' : 'review required';
  label.title = 'shift+a: change merge mode';
}

function toggleBoardMergeMode() {
  const board = boards.find(b => b.id === currentBoardId);
  if (!board) return;
  const mode = board.merge_mode === 'free' ? 'review' : 'free';
  const title = mode === 'free'
    ? 'merge cards into their base branch without asking? main stays protected'
    : 'stop at a pull request for review?';
  openActionConfirm(title, 'confirm', 'cancel', async () => {
    const {ok, body} = await apiOrError(`/api/boards/${board.id}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({merge_mode: mode}),
    });
    if (!ok) {
      openActionConfirm(body?.error || 'could not change merge mode', 'close', 'cancel', () => {});
      return;
    }
    board.merge_mode = mode;
    renderBoardMergeMode();
  });
}

// ---- buckets of card strips -------------------------------------------------------
// per-card classes, CTA and the strip itself live in card_panel.js; the tier a column renders at
// (full cards, title chips, or chips plus aggregates) is columns.js's renderBucketColumn - this is
// only the five-column shell that hands each status's cards to it

function renderBuckets(cards) {
  const row = document.getElementById('bucket-row');
  COLUMNS.forEach(column => {
    const bucketEl = row.querySelector(`.bucket[data-status="${column}"]`);
    // a column not present in this page's markup is simply skipped - lets an older or a test
    // fixture without the attention bucket keep working against the five real statuses alone
    if (!bucketEl) return;
    renderBucketColumn(bucketEl, cards.filter(c => columnFor(c) === column), column);
  });
  refreshBucketNav();
  // the cream marker glides to whatever took focus, rather than every card drawing its own ring.
  // focusin rather than a per-card handler, so it also catches focus arriving by click or by tab
  row.addEventListener('focusin', evt => indicateCardFocus(evt.target));
}

// rebinds the 2D grid nav to whatever is currently in the buckets - a full renderBuckets always
// needs this, and so does redrawColumns after a poll moved a card to a new bucket
function refreshBucketNav() {
  const row = document.getElementById('bucket-row');
  bucketsApi = makeBuckets(row, {onExitTop: returnToBoardBar});
}

function bucketHasCards(bucket) {
  return bucket.querySelector('.bucket-rows')?.children.length > 0;
}

// ---- cmd/ctrl+f: a live filter over the board's cards -------------------------------------------
// view-only: it narrows which cards each column draws (columns.js shownCards), never their order,
// status or anything on the server. shortcuts.js takes the key and calls focusCardFilter
let cardFilterQuery = '';

// what a card matches on: title, short id, description, every comment (the timeline an opened card
// shows) and the name of the column it sits in, so a column's name finds its cards
function cardFilterText(card) {
  const column = columnFor(card);
  const comments = (card.comments || []).map(c => c.body || '');
  return [card.title, shortId(card.id), card.description, ...comments, STATUS_NAME[column] || column]
    .filter(Boolean).join('\n').toLowerCase();
}

function cardMatchesFilter(card, query = cardFilterQuery) {
  const q = String(query || '').trim().toLowerCase();
  return !q || cardFilterText(card).includes(q);
}

const sameCards = (a, b) => a.length === b.length && a.every((card, i) => card === b[i]);

// redraws only the columns whose drawn cards change. such a column starts from rest: its focus
// index counts the cards it draws, and that list just changed under it
function applyCardFilter(query) {
  cardFilterQuery = String(query || '').trim().toLowerCase();
  document.querySelectorAll('#bucket-row .bucket-rows').forEach(rows => {
    const state = rows._pile;
    if (!state || sameCards(shownCards(state), state.shown || [])) return;
    Object.assign(state, {focusIndex: null, start: 0, anchor: 'top'});
    refitColumn(rows);
  });
}

// built on first use from ui_base's text-field and left in the corner, so escape has something to
// clear back to rather than a field popping in and out
function buildCardFilterInput() {
  const input = document.createElement('input');
  input.id = 'card-filter';
  input.className = 'text-field card-filter';
  input.placeholder = 'filter cards';
  input.title = 'cmd/ctrl+f - title, id, description, comments or column';
  input.addEventListener('input', () => applyCardFilter(input.value));
  input.addEventListener('keydown', evt => {
    if (evt.code !== 'Escape') return;
    // the board's own escape must not also act on this press
    evt.stopPropagation();
    clearCardFilter();
    returnToBoardBar();
  });
  barCorner().appendChild(input);
  return input;
}

function clearCardFilter() {
  const input = document.getElementById('card-filter');
  if (input) input.value = '';
  applyCardFilter('');
}

function focusCardFilter() {
  const input = document.getElementById('card-filter') || buildCardFilterInput();
  input.focus();
  return input;
}

// left/right should never park focus on a column with nothing in it. this runs in the capture
// phase - ahead of makeBuckets' own bubble listener on bucket-row - so it can step aside for a
// plain adjacent move and only take over once the next column in that direction is empty
function skipEmptyColumns(evt) {
  if (evt.code !== 'ArrowLeft' && evt.code !== 'ArrowRight') return;
  if (withModifier(evt)) return;
  const currentBucket = evt.target.closest?.('.bucket');
  if (!currentBucket) return;
  const buckets = Array.from(document.querySelectorAll('#bucket-row .bucket')).filter(b => !b.hidden);
  const currentIndex = buckets.indexOf(currentBucket);
  if (currentIndex === -1) return;
  const step = evt.code === 'ArrowRight' ? 1 : -1;
  const adjacent = buckets[currentIndex + step];
  if (!adjacent || bucketHasCards(adjacent)) return; // a normal move - leave it to the default nav
  let targetIndex = currentIndex + step;
  while (buckets[targetIndex] && !bucketHasCards(buckets[targetIndex])) targetIndex += step;
  // swallow the key either way: a run of empties was crossed, or there is nothing further that
  // way - neither case is the default nav's plain adjacent move, and falling through would wrap
  evt.preventDefault();
  evt.stopPropagation();
  const target = buckets[targetIndex]?.querySelector('.bucket-rows .row');
  if (target) { target.focus(); indicateCardFocus(target); }
}
document.addEventListener('keydown', skipEmptyColumns, {capture: true});

function returnToBoardBar() {
  const tab = document.querySelector('.board-bar .nav-tab.active');
  if (!tab) return;
  tab.focus();
  indicateFocus(tab);
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
}

// a link target from data is only ever a web url, never javascript: or data:
function safeUrl(url) {
  try {
    return ['https:', 'http:'].includes(new URL(url).protocol) ? url : '';
  } catch (err) {
    return '';
  }
}


// ---- running a card (r) -------------------------------------------------------------

// how often the board asks a running card where it got to. a card takes minutes, so a fast poll
// buys nothing but requests - the phases it moves through are the only thing changing
const RUN_POLL_MS = 2000;

function focusedCardId() {
  const el = document.activeElement;
  const strip = el && el.closest ? el.closest('[data-card-id]') : null;
  return strip ? strip.dataset.cardId : null;
}

function runBadge(cardId) {
  const strip = document.querySelector(`.card-strip[data-card-id="${cardId}"]`);
  return strip ? strip.querySelector('.card-run') : null;
}

// the compact action note, so the scheduler can correct it for a card whose stored status lags
// what the queue actually knows - see scheduler.js's applyScheduleToCards
function actionNote(cardId) {
  const strip = document.querySelector(`.card-strip[data-card-id="${cardId}"]`);
  return strip ? strip.querySelector('.card-action') : null;
}

// ---- one run-state resolver for the card foot ---------------------------------------------------
// pollRun (a run this tab itself started) and the run-all scheduler each learn a card's run state on
// their own clock. both used to write the foot's corners directly, which let the two race: a card
// polled by both wrote whichever poller's timer fired last, flickering between the two vocabularies.
// every writer now reports what it knows here instead, and this is the only place that turns state
// into words - so scheduler.js and pollRun never touch the foot except through it
const liveRunPhases = new Map();  // cardId -> phase text, this tab's own run only
const queuePositions = new Map(); // cardId -> {kind: 'running'|'waiting'|'queued', reason, index, total}

// a card already reporting how it's running is not still waiting for a turn - live phase always
// outranks a stale queue position for the same card
function setLiveRunPhase(cardId, phase) {
  if (phase) liveRunPhases.set(cardId, phase);
  else liveRunPhases.delete(cardId);
  renderRunFoot(cardId);
}

function setQueueState(cardId, queueState) {
  if (queueState) queuePositions.set(cardId, queueState);
  else queuePositions.delete(cardId);
  // joining or leaving the queue changes which column the card belongs in and what colour it wears;
  // moving up the queue changes neither, so a poll that only advances positions redraws nothing
  const wasPending = isPendingCard(cardId);
  // pending is being in the queue: waiting on a lease, a dependency or a limit still counts, since
  // the scheduler runs the card as soon as that clears. a waiting entry with no place is not in it
  const inQueue = queueState?.kind === 'queued' || (queueState?.kind === 'waiting' && queueState.index != null);
  setPendingCard(cardId, inQueue ? queueState.index : null);
  if (wasPending !== isPendingCard(cardId)) markQueueMove(cardId);
  renderRunFoot(cardId);
}

// every id the scheduler's last view named - anything else the queue used to know about has left it
// (stopped, finished, skipped) and goes back to its own column, since nothing about it was stored
function retainQueueStates(ids) {
  [...queuePositions.keys()].forEach(id => { if (!ids.has(id)) setQueueState(id, null); });
}

// a run that ended (finished, stopped, failed) leaves nothing behind for either poller to relitigate
function clearRunState(cardId) {
  liveRunPhases.delete(cardId);
  setQueueState(cardId, null);
}

// ---- the queue moving a card between columns ---------------------------------------------------
// PRESENTATION ONLY: nothing here writes status, blocked_reason_code or review_flag, so a queue
// that empties puts every card back where it was. the ids are collected and drawn in one pass, so a
// poll that queues ten cards costs one redraw rather than ten
const queueMoved = new Set();
let queueRedrawTimer = null;

function markQueueMove(cardId) {
  queueMoved.add(cardId);
  if (queueRedrawTimer) return;
  // unref where it exists: a test process must not be held open by a redraw nobody is watching
  queueRedrawTimer = setTimeout(redrawQueueMoves, 0);
  queueRedrawTimer?.unref?.();
}

// the card objects the columns are already holding - the queue only changes how a card is drawn,
// never what the server said about it, so there is nothing to refetch
function cardsInColumns(ids) {
  const found = [];
  document.querySelectorAll('#bucket-row .bucket-rows').forEach(rows => {
    (rows._pile?.sorted || []).forEach(card => { if (ids.has(card.id)) found.push(card); });
  });
  return found;
}

function redrawQueueMoves() {
  queueRedrawTimer = null;
  const ids = new Set(queueMoved);
  queueMoved.clear();
  // a column holding the open card is never rebuilt under its panel, the same rule followRunsOnce
  // follows - the next queue change redraws it once the card is closed
  const openBucket = openCard ? bucketHolding(openCard.cardId) : null;
  const cards = cardsInColumns(ids).filter(card => {
    if (openCard && openCard.cardId === card.id) return false;
    const to = document.querySelector(`.bucket[data-status="${columnFor(card)}"]`);
    return !openBucket || (bucketHolding(card.id) !== openBucket && to !== openBucket);
  });
  if (!cards.length) return;
  redrawColumns(cards);
  refreshBucketNav();
  // the redraw built fresh strips, so the feet lost what the queue had just written on them
  ids.forEach(renderRunFoot);
}

// attention already has its own CTA label (a reason code, "needs attention") - a poller learning
// about a run mid-transition must never clobber that with "Running…" on one corner while the other
// still names the phase, which is what an attention card showing running left and starting right was
function footIsAttention(cardId) {
  const strip = document.querySelector(`.card-strip[data-card-id="${cardId}"]`);
  return !!strip && strip.classList.contains('card-attention');
}

// the one place that decides both corners together, so they always change in the same call and
// never independently - left names the action, right names the run phase, same vocabulary either
// poller uses
function renderRunFoot(cardId) {
  const note = actionNote(cardId);
  const attention = footIsAttention(cardId);
  const phase = liveRunPhases.get(cardId);
  if (phase) {
    if (note && !attention) note.textContent = 'Running…';
    showRun(cardId, phase);
    return;
  }
  const queue = queuePositions.get(cardId);
  if (!queue) return;
  if (queue.kind === 'running') {
    if (note && !attention) note.textContent = 'Running…';
    showRun(cardId, 'running');
  } else if (queue.kind === 'waiting') {
    showRun(cardId, 'waiting', null, queue.reason);
  } else if (queue.kind === 'queued') {
    // a card re-queued while blocked kept its 'doing' status, so the note still read 'Running…' -
    // it hasn't actually started again yet, the queue has
    if (note && !attention && note.textContent === 'Running…') note.textContent = 'Queued';
    showRun(cardId, `queued, ${queue.index} of ${queue.total}`);
  }
}

function showRun(cardId, text, href, detail) {
  const badge = runBadge(cardId);
  if (!badge) return;
  badge.hidden = false;
  badge.textContent = '';
  // the foot is one line and must stay one line - the long version is the comment the run left
  badge.title = detail || '';
  if (safeUrl(href)) {
    const link = document.createElement('a');
    link.className = 'pr-link';
    link.href = href;
    link.target = '_blank';
    link.rel = 'noreferrer';
    link.textContent = text;
    badge.appendChild(link);
  } else {
    badge.textContent = text;
  }
}

// THE BOARD SAYS WHY IT CANNOT RUN, RATHER THAN DOING NOTHING. docker and the card credential both
// live outside the board, and a button that silently fails is indistinguishable from a broken one
function reportNotReady(missing) {
  const box = document.createElement('div');
  box.className = 'hazard-stripes hazard-placeholder';
  box.innerHTML = `<span class="hazard-label">cannot run a card yet</span>` +
    missing.map(m => `<span class="hazard-note">${escapeHtml(m)}</span>`).join('');
  const menu = new Menu({title: 'card runtime', sections: [{kind: 'node', node: box}]});
  menu.openAt({x: window.innerWidth / 2 - 200, y: 80});
  menu.el?.classList.add('menu-centered');
}

async function runFocusedCard(cardId = focusedCardId()) {
  if (!cardId) return;
  openActionConfirm('run this card?', 'run it', 'cancel', () => doRunFocusedCard(cardId));
}

async function doRunFocusedCard(cardId) {
  const runtime = await api('/api/runtime');
  if (!runtime.ready) { reportNotReady(runtime.missing); return; }

  // a manual retry means whatever the CTA named ("fix leases", "answer question") is either
  // done or moot - the run itself is now the story, so the stale reason-code label goes
  setLiveRunPhase(cardId, 'starting');
  const state = await api(`/api/cards/${cardId}/run`, {method: 'POST'});
  if (!state.running) { finishRun(cardId, state); return; }
  pollRun(cardId);
}

function pollRun(cardId) {
  setTimeout(async () => {
    const state = await api(`/api/cards/${cardId}/run`);
    if (state.running) {
      setLiveRunPhase(cardId, state.phase);
      pollRun(cardId);
      return;
    }
    finishRun(cardId, state);
  }, RUN_POLL_MS);
}

// A CARD CAN CHANGE COLUMN MID-RUN, NOT ONLY WHEN THE RUN ENDS. todo -> doing and doing -> checking
// happen inside lifecycle.py while the run is still going, and whatever wrote the change (this
// page's own run, another run, a key, mission control) already committed it before we asked. so
// the board polls every card's status and updated_at and redraws only the columns a changed card
// left or joined, through the same column layout as a full render - every other column is untouched
const FOLLOW_RUNS_MS = 4000;
let followedCardStates = null; // Map<card id, `${status}|${updated_at}`> as of the last poll

// by the column's own card list first, not its drawn strips - a card the filter leaves out has no
// strip, and a poll moving it would otherwise leave a copy behind in the column it left
function bucketHolding(cardId) {
  const rows = Array.from(document.querySelectorAll('#bucket-row .bucket-rows'))
    .find(el => (el._pile?.sorted || []).some(card => card.id === cardId));
  const strip = document.querySelector(`.card-strip[data-card-id="${cardId}"]`);
  return rows?.closest('.bucket') || strip?.closest('.bucket') || null;
}

// swaps a card into its (possibly new) column's list and redraws each touched column whole, so a
// moved card gets the column's card size and pile layout rather than its unfitted natural height.
// focus stays on whichever card had it, by id, since a redraw rebuilds every row in the column
function redrawColumns(changed) {
  const activeCardId = document.activeElement?.dataset?.cardId ?? null;
  const touched = new Set();
  changed.forEach(card => {
    const from = bucketHolding(card.id);
    const to = document.querySelector(`.bucket[data-status="${columnFor(card)}"]`);
    [from, to].forEach(bucket => {
      const pile = bucket?.querySelector('.bucket-rows')?._pile;
      if (!pile) return;
      pile.sorted = pile.sorted.filter(c => c.id !== card.id);
      if (bucket === to) pile.sorted.push(card);
      touched.add(bucket);
    });
  });
  touched.forEach(bucket => {
    const rows = bucket.querySelector('.bucket-rows');
    renderBucketColumn(bucket, rows._pile.sorted, bucket.dataset.status);
  });
  if (!activeCardId) return;
  const strip = document.querySelector(`.card-strip[data-card-id="${activeCardId}"]`);
  if (!strip) return;
  strip.tabIndex = 0;
  strip.focus();
  indicateCardFocus(strip);
}

async function followRunsOnce() {
  if (!currentBoardId) return false;
  let cards;
  try { cards = await api(`/api/boards/${currentBoardId}/cards`); } catch (err) { return false; } // server restarting
  const states = new Map(cards.map(c => [c.id, `${c.status}|${c.updated_at}`]));
  const before = followedCardStates;
  if (!before) { followedCardStates = states; return false; } // the first poll only learns where things stand

  const next = new Map(before);
  const toRedraw = [];
  // a column holding the open card is never rebuilt under its panel, so a change that touches it
  // waits too - its entry is left stale, and the next poll after the card closes redraws it
  const openBucket = openCard ? bucketHolding(openCard.cardId) : null;
  cards.forEach(card => {
    const state = states.get(card.id);
    if (before.get(card.id) === state) return;
    if (openCard && openCard.cardId === card.id) return;
    const to = document.querySelector(`.bucket[data-status="${columnFor(card)}"]`);
    if (openBucket && (bucketHolding(card.id) === openBucket || to === openBucket)) return;
    toRedraw.push(card);
    next.set(card.id, state);
  });
  followedCardStates = next;
  if (!toRedraw.length) return false;
  redrawColumns(toRedraw);
  refreshBucketNav();
  return true;
}

function followRuns() {
  followRunsOnce().finally(() => {
    // unref where it exists: the browser has none, and a test process must not stay alive for this
    setTimeout(followRuns, FOLLOW_RUNS_MS)?.unref?.();
  });
}

// ---- stopping a running card (k) ------------------------------------------------------

// same focus source y/x use: the strip under keyboard focus, or the card whose panel is open
async function stopFocusedCard(cardId = actionableCardId()) {
  if (!cardId) return;
  const state = await api(`/api/cards/${cardId}/run`);
  if (!state.running) return; // nothing to stop
  openStopConfirm(cardId);
}

function openStopConfirm(cardId) {
  const returnTo = document.activeElement;
  const menu = new Menu({
    title: 'stop this run?',
    sections: [{
      kind: 'list',
      items: [
        // stopping spends nothing and undoes nothing - the action you already pressed k for holds
        // focus, so enter carries it through
        {id: 'stop', label: 'stop the run', autofocus: true},
        {id: 'keep', label: 'keep running'},
      ],
      onPick: item => {
        menu.close();
        if (item.id === 'stop') doStopCard(cardId);
      },
    }],
  });
  menu.openAt({x: window.innerWidth / 2 - 200, y: 80});
  menu.el?.classList.add('menu-centered');
  focusMenuItem(menu, 'stop');
  returnFocusOnDismiss(menu, returnTo);
}

async function doStopCard(cardId) {
  const {ok, body} = await apiOrError(`/api/cards/${cardId}/stop`, {method: 'POST'});
  if (!ok) { showRun(cardId, "can't stop", null, (body && body.error) || ''); return; }
  showRun(cardId, 'stopping');
}

// the run is over: reload the board so the card's new status, reason code and comments are what is
// on screen, THEN say where the run landed.
// ORDER MATTERS AND IT BIT ONCE. Setting the badge first and reloading after threw the badge away
// with the strip it was on - the whole chain fired correctly and the result was invisible.
async function finishRun(cardId, state) {
  // the run is over - neither poller has anything left to say about it, and the terminal label
  // below is the last word until a new run starts
  clearRunState(cardId);
  if (currentBoardId) await onBoardEnter(currentBoardId);
  // the reload built new strips, so put focus back on the card that ran. without this the user
  // loses their place after every run AND the result is invisible: only the focused card's foot
  // is showing in the fan, so the badge lands on a card nobody can see
  const strip = document.querySelector(`.card-strip[data-card-id="${cardId}"]`);
  if (strip) { strip.focus(); indicateCardFocus(strip); }
  if (state.pr_url) showRun(cardId, 'pull request', state.pr_url);
  else if (state.blocked_reason_code) showRun(cardId, state.blocked_reason_code);
  else showRun(cardId, state.error ? 'failed' : 'refused', null,
               state.refusal || state.error || state.phase);
}

// ---- accept / reject a checking card (y / x) -----------------------------------------

// same focus source runFocusedCard uses, plus the card whose panel is open - focus inside the
// panel is not under a [data-card-id] strip, so openCard has to remember which card that is
function actionableCardId() {
  return focusedCardId() || (openCard && openCard.cardId) || null;
}

// ONE BEAT FOR EVERY MOTION: ui_base's --motion-duration and --motion-ease, so a card changing
// columns moves like a card opening. the fallbacks cover a page without the tokens and the test stub
function motion() {
  const css = globalThis.getComputedStyle?.(document.documentElement);
  const raw = (css?.getPropertyValue('--motion-duration') || '').trim();
  const seconds = raw.endsWith('s') && !raw.endsWith('ms');
  const duration = (parseFloat(raw) || 0) * (seconds ? 1000 : 1) || 220;
  const ease = (css?.getPropertyValue('--motion-ease') || '').trim() || 'ease';
  const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  return {duration: still ? 0 : duration, ease};
}

// FLIP: the rebuilt strip is drawn back where the old one stood, then let go into its new column.
// `translate`, not `transform`, so whatever transform the fan layout sets is left alone
function slideFrom(strip, from) {
  const {duration, ease} = motion();
  const to = strip.getBoundingClientRect();
  const dx = from.left - to.left, dy = from.top - to.top;
  if (!duration || (!dx && !dy)) return;
  Object.assign(strip.style, {transition: 'none', translate: `${dx}px ${dy}px`});
  // two frames, as in expand.js: the start position has to be painted before it is released
  requestAnimationFrame(() => requestAnimationFrame(() => {
    Object.assign(strip.style, {transition: `translate ${duration}ms ${ease}`, translate: ''});
    setTimeout(() => { strip.style.transition = ''; }, duration);
  }));
}

function acceptOrRejectCard(action) {
  const cardId = actionableCardId();
  if (!cardId) return;
  const verb = action === 'accept' ? 'accept' : 'reject';
  // a status a menu can move back is reversible, so the confirm opens ON the yes: enter straight
  // after y or x carries it through without breaking the flow
  openActionConfirm(`${verb} this card?`, verb, 'cancel',
    () => doAcceptOrRejectCard(action, cardId), null, 'confirm');
}

async function doAcceptOrRejectCard(action, cardId) {
  // CLOSE FIRST, THEN MOVE. deciding from inside an open card used to slide the strip into its new
  // column behind a panel still standing over it
  if (openCard) openCard.expander.close();

  // FOCUS STAYS IN THE SOURCE COLUMN, never rides along into the destination one - capture where
  // it belongs (the card above, or which column to hop left from) before the move erases both
  const strip = document.querySelector(`.card-strip[data-card-id="${cardId}"]`);
  const aboveCardId = strip?.previousElementSibling?.dataset.cardId || null;
  const oldStatus = strip?.closest('.bucket')?.dataset.status || null;

  const {ok, status, body} = await apiOrError(`/api/cards/${cardId}/${action}`, {method: 'POST'});
  // NAME THE ACTION THAT WAS REFUSED. a bare "refused" beside the card's own "accepted" read as
  // the two labels swapped
  if (!ok) { showRun(cardId, `can't ${action}`, null, (body && body.error) || ''); return; }
  if (status === 202) {
    showRun(cardId, 'landing');
    return;
  }

  // same order finishRun uses and for the same reason: reload first, THEN focus, THEN badge, or
  // the reload's fresh strips throw the badge and the focus away with the old ones
  const from = document.querySelector(`.card-strip[data-card-id="${cardId}"]`)
    ?.getBoundingClientRect();
  if (currentBoardId) await onBoardEnter(currentBoardId);
  const moved = document.querySelector(`.card-strip[data-card-id="${cardId}"]`);
  if (from && moved) slideFrom(moved, from);
  focusAfterColumnExit(aboveCardId, oldStatus);
  // no badge on success: the foot's status already says accepted or rejected, and the border says it
  // in colour - a second "accepted" beside the first was noise
}

// where focus lands once a card leaves its column: the card that stood directly above it there,
// else the topmost card of the nearest non-empty column to the left (skipping empty ones, never
// wrapping right). when neither exists, focus is parked on nothing rather than falling through to
// the card's new column or the board bar
function focusAfterColumnExit(aboveCardId, oldStatus) {
  const above = aboveCardId && document.querySelector(`.card-strip[data-card-id="${aboveCardId}"]`);
  if (above) { above.focus(); indicateCardFocus(above); return; }
  const buckets = Array.from(document.querySelectorAll('#bucket-row .bucket')).filter(b => !b.hidden);
  let index = buckets.findIndex(b => b.dataset.status === oldStatus) - 1;
  while (index >= 0 && !bucketHasCards(buckets[index])) index -= 1;
  const landing = index >= 0 ? buckets[index].querySelector('.bucket-rows .row') : null;
  if (landing) { landing.focus(); indicateCardFocus(landing); return; }
  document.activeElement?.blur?.();
}

// ---- overlay panels (menu.js-backed) - the key that opens also closes ---------------------------

// THE KEY THAT OPENS AN OVERLAY ALSO CLOSES IT. Menu dismisses on outside click and escape but
// knows nothing about the key that summoned it, so the caller remembers which one is open. onDismiss
// clears the record however the menu went away, or the next press reopens what the user just closed
let openOverlay = null;

function toggleOverlay(key, build) {
  if (openOverlay && openOverlay.key === key) {
    openOverlay.menu.close();
    openOverlay = null;
    return;
  }
  if (openOverlay) { openOverlay.menu.close(); openOverlay = null; }
  const returnTo = document.activeElement;
  const menu = build();
  // a menu that refreshes once its fetch lands REPLACES its own panel, which drops focus on
  // <body> and leaves the keyboard dead until the next key recovers it. the rebuilt panel is a
  // plain div, so it needs the tabIndex back before it can hold focus at all
  const refresh = menu?.refresh?.bind(menu);
  if (refresh) {
    menu.refresh = sections => {
      refresh(sections);
      if (!menu.el) return;
      menu.el.tabIndex = -1;
      menu.el.focus?.();
    };
  }
  returnFocusOnDismiss(menu, returnTo);
  openOverlay = {key, menu};
  return menu;
}

// ---- fold (f) - merge the todo cards one agent should do as one ------------------------------
// KEYBOARD ONLY: no board-bar button, per the operator - f is the whole affordance, and the
// shortcut overlay is where it is written down

// ASKS FIRST, every time: a fold is a model run over the whole board, not a free local action.
// a destructive confirm, so "no" holds focus - a stray enter must never start it
function openFoldConfirm() {
  if (!currentBoardId) return;
  const boardId = currentBoardId;
  toggleOverlay('KeyF', () => {
    const note = document.createElement('div');
    note.className = 'fold-note';
    note.textContent = 'an agent reads every card and the ledger, then merges the todo cards one '
      + 'agent should do as one. this costs tokens and takes a few minutes.';
    const menu = new Menu({
      title: "fold this board's cards?",
      sections: [
        {kind: 'node', node: note},
        {kind: 'list',
          items: [{id: 'yes', label: 'yes, fold (y)'}, {id: 'no', label: 'no (n)', autofocus: true}],
          onPick: item => answerFold(item.id === 'yes', boardId)},
      ],
      onDismiss: () => { if (openOverlay && openOverlay.key === 'KeyF') openOverlay = null; },
    });
    menu.openAt({x: window.innerWidth / 2 - 200, y: 80});
    menu.el?.classList.add('menu-centered');
    focusMenuItem(menu, 'no');
    return menu;
  });
}

function answerFold(yes, boardId = currentBoardId) {
  if (openOverlay && openOverlay.key === 'KeyF') { openOverlay.menu.close(); openOverlay = null; }
  if (yes && boardId) startFold(boardId);
}

let foldPoll = null;

// the board writes its progress and the result into mission control, so that is where it shows;
// the cards re-render once the fold is done
async function startFold(boardId) {
  const {ok} = await apiOrError(`/api/boards/${boardId}/fold`, {method: 'POST'});
  drawerFor('right').open();
  if (!ok) return; // a 409 is a fold already running, whose messages are already there
  clearInterval(foldPoll);
  // unref where it exists, same as followRuns: a browser has none, and a test process must not be
  // held open for three seconds at a time by a poll nobody is watching
  foldPoll = setInterval(async () => {
    const state = await api(`/api/boards/${boardId}/fold`).catch(() => null);
    if (state && state.running) return;
    clearInterval(foldPoll);
    foldPoll = null;
    if (currentBoardId === boardId) await onBoardEnter(boardId);
  }, 3000);
  foldPoll?.unref?.();
}

// ---- agent roster (a) -----------------------------------------------------------------------

function openRosterPanel() {
  toggleOverlay('KeyA', () => {
    const menu = new Menu({
      title: 'agent roster',
      sections: [{kind: 'list', items: [], empty: 'loading...'}],
      onDismiss: () => { if (openOverlay && openOverlay.key === 'KeyA') openOverlay = null; },
    });
    menu.openAt({x: window.innerWidth / 2 - 200, y: 80});
    menu.el?.classList.add('menu-centered');
    loadRoster(menu);
    return menu;
  });
}

async function loadRoster(menu) {
  try {
    const roster = await api('/api/roster');
    if (!roster.length) {
      const box = document.createElement('div');
      box.className = 'hazard-stripes hazard-placeholder';
      box.innerHTML = '<span class="hazard-label">nothing is running - check the inbox (n) for anything waiting</span>';
      menu.refresh([{kind: 'node', node: box}]);
      return;
    }
    const items = roster.map(r => ({id: r.card_id, label: r.title, stats: r.activity, on: true}));
    menu.refresh([{kind: 'list', items, onPick: item => jumpToCard(item.id, roster)}]);
  } catch (err) {
    menu.refresh([{kind: 'list', items: [], empty: `could not load the roster: ${err.message}`}]);
  }
}

// picking a roster row does exactly what selecting that card by hand would: switch board if
// needed, wait for it to render, focus the strip, mark it, and open it
async function jumpToCard(cardId, roster) {
  const row = roster.find(r => r.card_id === cardId);
  if (!row) return;
  if (row.board_id !== currentBoardId) {
    activateTab(row.board_id);
    await onBoardEnter(row.board_id);
  }
  // a jump asks to see this card, so a filter leaving it out gives way
  if (cardFilterQuery && !document.querySelector(`.card-strip[data-card-id="${cardId}"]`)) clearCardFilter();
  const strip = document.querySelector(`.card-strip[data-card-id="${cardId}"]`);
  if (!strip) return;
  strip.focus();
  indicateCardFocus(strip);
  strip._expander?.open();
}

// ---- usage (u) --------------------------------------------------------------------------------

function windowLabel(type) {
  if (type === 'five_hour' || type === 'five-hour') return 'five-hour window';
  if (type === 'seven_day' || type === 'seven-day') return 'seven-day window';
  return type;
}

// resets_at is a unix-epoch second count; shown in whoever's looking at the drawer's own local time
function formatResetTime(resetsAt) {
  if (resetsAt == null) return null;
  const d = new Date(resetsAt * 1000);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `resets ${hh}:${mm}`;
}

function formatTokenCount(n) {
  const count = n || 0;
  return count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(count);
}

// each rate-limit window's length, for how far through it we are when no utilisation is reported
const WINDOW_SECONDS = {five_hour: 5 * 3600, seven_day: 7 * 86400};

function formatDuration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  return h ? `${h}h ${m}m` : `${m}m`;
}

// WHAT THE BAR MEASURES IS NAMED BESIDE IT. utilisation when the server reports one; otherwise how
// far through its window we are - never a guess at usage, and the stat line says which it is
function windowMeasure(w, now = Date.now() / 1000) {
  if (w.utilization != null) return {fraction: w.utilization, note: `${(w.utilization * 100).toFixed(1)}% used`};
  const length = WINDOW_SECONDS[String(w.type).replace('-', '_')];
  if (w.resets_at == null || !length) return {fraction: null, note: null};
  const left = w.resets_at - now;
  // A PAST RESET MEANS THE DATA IS OLD: the window rolled over after the last run reported it, so
  // there is nothing current to draw - a full bar reading "0m left" said the opposite
  if (left <= 0) return {fraction: null, note: 'reset since the last run reported it'};
  const fraction = Math.min(1, Math.max(0, 1 - left / length));
  // the number reads as time, not usage: "through" the window, never "used"
  return {fraction, note: `${Math.round(fraction * 100)}% through - ${formatDuration(left)} left in the window`};
}

function windowStats(w, now = Date.now() / 1000) {
  const when = formatResetTime(w.resets_at);
  const reset = when && w.resets_at <= now ? when.replace('resets', 'reset at') : when;
  return [w.status, reset, windowMeasure(w, now).note].filter(Boolean).join(' - ');
}

// ui_base's fill bar: .progress-row > .bar > .bar-fill, the fill's width the fraction
function fillBar(fraction, tone = '') {
  const row = document.createElement('div');
  row.className = 'progress-row';
  const bar = document.createElement('div');
  bar.className = 'bar';
  const fill = document.createElement('div');
  fill.className = tone ? `bar-fill ${tone}` : 'bar-fill';
  fill.style.width = `${Math.round(fraction * 1000) / 10}%`;
  bar.appendChild(fill);
  row.appendChild(bar);
  return row;
}

function textLine(text, className) {
  const el = document.createElement('div');
  el.className = className;
  el.textContent = text;
  return el;
}

function usageSection(label, className) {
  const section = document.createElement('div');
  section.className = `usage-section ${className}`;
  section.appendChild(textLine(label, 'field-label'));
  return section;
}

// one row per credential profile inside a window section - a profile the server never sent a
// window for (no rate_limit_event recorded under it yet) still gets a row, with a zero/empty bar
// rather than being left out, per the usage-overlay-per-profile card
function profileRow(profileName, window) {
  const row = document.createElement('div');
  row.className = 'usage-profile';
  row.appendChild(textLine(profileName, 'field-label'));
  if (window) {
    const {fraction} = windowMeasure(window);
    if (fraction != null) row.appendChild(fillBar(fraction, ['allowed', 'ok'].includes(window.status) ? '' : 'warn'));
    row.appendChild(textLine(windowStats(window), 'stat'));
  } else {
    row.appendChild(fillBar(0));
    row.appendChild(textLine('no usage yet', 'stat'));
  }
  return row;
}

// windows grouped by type, each type a section holding one row per known profile - windows carry
// no profile of their own before this card, so a window with none reads as the "default" profile
function windowSections(windows, profileNames, lab = '') {
  const byType = new Map();
  windows.forEach(w => {
    if (!byType.has(w.type)) byType.set(w.type, new Map());
    byType.get(w.type).set(w.profile || 'default', w);
  });
  return [...byType.entries()].map(([type, byProfile]) => {
    const section = usageSection(`${lab ? lab + ' - ' : ''}${windowLabel(type)}`, 'usage-window');
    const names = profileNames.length ? profileNames : [...byProfile.keys()];
    names.forEach(name => section.appendChild(profileRow(name, byProfile.get(name))));
    // the section total is the sum of the rows it holds, never a figure computed apart from them
    const total = names.reduce((sum, name) => {
      const w = byProfile.get(name);
      const fraction = w ? windowMeasure(w).fraction : null;
      return sum + (fraction || 0);
    }, 0);
    section.appendChild(textLine(`combined: ${Math.round(total * 100)}%`, 'stat'));
    return section;
  });
}

// A CARD'S LANGUAGE: ruled sections with dim labels, and a foot carrying the total - built with
// createElement so every line is its own element, which is also what the node tests read
function usageCard(data) {
  const labOf = row => row.lab || (row.model?.includes('/') ? row.model.split('/')[0] : 'anthropic');
  const labs = [...new Set([...(data.profiles || []), ...(data.windows || []), ...(data.models || [])].map(labOf))];
  const windowParts = labs.flatMap(lab => windowSections(
    (data.windows || []).filter(w => labOf(w) === lab),
    (data.profiles || []).filter(p => labOf(p) === lab).map(p => p.name), lab));
  const modelParts = [];
  const money = row => row.cost_usd == null ? 'unknown' : `${row.cost_estimated ? '~' : ''}$${row.cost_usd.toFixed(2)}`;
  labs.forEach(lab => {
    const models = (data.models || []).filter(m => labOf(m) === lab);
    if (!models.length) return;
    const section = usageSection(`${lab} - spend by model`, 'usage-models');
    const total = data.total_cost_usd;
    models.forEach(m => {
      const row = document.createElement('div');
      row.className = 'usage-model';
      const share = total > 0 && m.cost_usd != null ? m.cost_usd / total : null;
      const shareNote = share != null ? ` - ${Math.round(share * 100)}% of spend` : '';
      row.appendChild(textLine(`${m.model} - ${money(m)}${shareNote}`, 'usage-model-name'));
      if (share != null) row.appendChild(fillBar(share));
      row.appendChild(textLine(`in ${formatTokenCount(m.input_tokens)} - out ${formatTokenCount(m.output_tokens)} - ` +
        `cache ${formatTokenCount((m.cache_read_tokens || 0) + (m.cache_creation_tokens || 0))}`, 'stat'));
      section.appendChild(row);
    });
    modelParts.push(section);
  });
  const card = document.createElement('div');
  card.className = 'usage-card usage-wide';
  // wide, not tall: the rate-limit windows in one column, the spend by model in the other
  const columns = document.createElement('div');
  columns.className = 'usage-columns';
  [windowParts, modelParts].filter(group => group.length).forEach(group => {
    const column = document.createElement('div');
    column.className = 'usage-column';
    group.forEach((part, i) => {
      if (i) column.appendChild(textLine('', 'h-divider'));
      column.appendChild(part);
    });
    columns.appendChild(column);
  });
  card.appendChild(columns);
  card.appendChild(textLine('', 'h-divider'));
  const foot = document.createElement('div');
  foot.className = 'card-foot usage-foot';
  foot.appendChild(textLine(`${data.runs || 0} runs - ${money({cost_usd: data.total_cost_usd, cost_estimated: data.cost_estimated})}`, 'stat'));
  card.appendChild(foot);
  return card;
}

function usageSections(data) {
  const windows = data.windows || [];
  const models = data.models || [];
  if (!windows.length && !models.length && !data.runs) {
    const box = document.createElement('div');
    box.className = 'hazard-stripes hazard-placeholder';
    box.innerHTML = '<span class="hazard-label">no usage yet</span>';
    return [{kind: 'node', node: box}];
  }
  return [{kind: 'node', node: usageCard(data)}];
}

function openUsagePanel() {
  toggleOverlay('KeyU', () => {
    const menu = new Menu({
      title: 'usage',
      sections: [{kind: 'list', items: [], empty: 'loading...'}],
      onDismiss: () => { if (openOverlay && openOverlay.key === 'KeyU') openOverlay = null; },
    });
    menu.openAt({x: window.innerWidth / 2 - 200, y: 80});
    menu.el?.classList.add('menu-centered');
    loadUsage(menu);
    return menu;
  });
}

async function loadUsage(menu) {
  try {
    const [data, profiles] = await Promise.all([api('/api/usage'), api('/api/profiles')]);
    menu.refresh(usageSections({...data, profiles}));
  } catch (err) {
    menu.refresh([{kind: 'list', items: [], empty: `could not load usage: ${err.message}`}]);
  }
}

// ---- prompt editor (p) - edit the orchestrator/worker/reviewer role prompts ---------------------
// NOT a Menu: Menu listens for arrow/enter/escape on the whole document while open, which would
// eat every one of those keys typed into a textarea. built from the same modal-backdrop /
// panel-floating pair expand.js and the roster/usage menus already ride, with its own escape and
// outside-click dismiss - see the brief's note on menu.js before assuming Menu fits here.
//
// UNSAVED EDITS ARE KEPT AS A PER-ROLE DRAFT IN MEMORY, not warned-and-discarded: pe.drafts holds
// whatever is in the textarea per role, surviving a role switch or a close/reopen, and is only
// cleared once that role's save actually lands.
const promptRoles = ['orchestrator', 'worker', 'reviewer'];
const pe = {
  backdrop: null, panel: null, roleRow: null, textarea: null, statusEl: null,
  role: promptRoles[0], cache: {}, drafts: {},
};

function promptRoleLabel(row) {
  return row ? (row.is_default ? 'default' : `version ${row.version}`) : '';
}

function buildPromptEditorDom() {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop prompt-backdrop';
  const panel = document.createElement('div');
  panel.className = 'panel-floating prompt-panel';

  const title = document.createElement('div');
  title.className = 'popup-title';
  title.textContent = 'prompt editor';
  const rule = document.createElement('div');
  rule.className = 'h-divider';

  const roleRow = document.createElement('div');
  roleRow.className = 'prompt-roles';
  // built with createElement/appendChild, not innerHTML, so the version span stays a queryable
  // live node - the test dom stub does not parse innerHTML strings back into a tree (see the same
  // note on terminalDom above)
  promptRoles.forEach(role => {
    const btn = document.createElement('div');
    btn.className = 'toggle prompt-role';
    btn.dataset.role = role;
    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = role;
    const version = document.createElement('span');
    version.className = 'prompt-role-version';
    btn.append(name, version);
    btn.onclick = () => selectPromptRole(role);
    roleRow.appendChild(btn);
  });

  const textarea = document.createElement('textarea');
  textarea.className = 'prompt-body text-field';
  textarea.rows = 18;
  textarea.spellcheck = false;
  // escape steps out of the panel rather than out of the textarea's own undo history, and
  // cmd/ctrl+s saves without leaving the field - both stop here so they never reach the board's
  // own keydown handler (which the typing check below already shields from every other key)
  textarea.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); stepOutOfField(textarea); return; }
    if ((evt.metaKey || evt.ctrlKey) && evt.code === 'KeyS') { evt.preventDefault(); savePromptEditor(); }
  });
  textarea.addEventListener('input', () => { pe.drafts[pe.role] = textarea.value; });

  const footer = document.createElement('div');
  footer.className = 'prompt-footer';
  const save = document.createElement('div');
  save.className = 'toggle prompt-save';
  save.textContent = 'save';
  save.onclick = () => savePromptEditor();
  const status = document.createElement('span');
  status.className = 'prompt-status';
  footer.append(save, status);

  panel.append(title, rule, roleRow, textarea, footer);
  backdrop.appendChild(panel);
  // outside click closes, same as expand.js's backdrop - only a click ON the dim, not the panel
  backdrop.addEventListener('mousedown', evt => { if (evt.target === backdrop) closePromptEditor(); });

  Object.assign(pe, {backdrop, panel, roleRow, textarea, statusEl: status});
  return backdrop;
}

function highlightPromptRole() {
  [...pe.roleRow.children].forEach(btn => btn.classList.toggle('on', btn.dataset.role === pe.role));
}

function renderPromptRoleLabels() {
  [...pe.roleRow.children].forEach(btn => {
    btn.querySelector('.prompt-role-version').textContent = promptRoleLabel(pe.cache[btn.dataset.role]);
  });
}

function setPromptStatus(text, tone) {
  pe.statusEl.textContent = text;
  pe.statusEl.className = 'prompt-status' + (tone ? ` prompt-${tone}` : '');
}

// a draft in memory always wins over the server's body - it is the point of keeping one
function selectPromptRole(role) {
  pe.role = role;
  highlightPromptRole();
  setPromptStatus('');
  const draft = pe.drafts[role];
  pe.textarea.value = draft !== undefined ? draft : (pe.cache[role] ? pe.cache[role].body : '');
}

async function loadPromptEditor() {
  try {
    const rows = await api('/api/prompts');
    rows.forEach(row => { pe.cache[row.role] = row; });
    renderPromptRoleLabels();
    selectPromptRole(pe.role);
  } catch (err) {
    setPromptStatus(`could not load prompts: ${err.message}`, 'error');
  }
}

async function savePromptEditor() {
  const role = pe.role;
  setPromptStatus('saving...');
  try {
    const res = await fetch(`/api/prompts/${role}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({body: pe.textarea.value}),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) { setPromptStatus((data && data.error) || `save failed (${res.status})`, 'error'); return; }
    pe.cache[role] = data;
    delete pe.drafts[role];
    renderPromptRoleLabels();
    // worker and reviewer are read at run time; the orchestrator picks it up at its next turn
    setPromptStatus(`saved as version ${data.version} - applies from the next run`, 'dim');
  } catch (err) {
    setPromptStatus(`could not save: ${err.message}`, 'error');
  }
}

function onPromptEditorKey(evt) {
  if (evt.code === 'Escape') closePromptEditor();
}

function openPromptEditor() {
  if (!pe.backdrop) buildPromptEditorDom();
  document.body.appendChild(pe.backdrop);
  document.addEventListener('keydown', onPromptEditorKey);
  highlightPromptRole();
  selectPromptRole(pe.role);
  loadPromptEditor();
  // the panel takes focus, not the textarea: / is the way into typing here as everywhere else,
  // and a panel that opens in typing mode swallows every board key
  focusPanel(pe.panel);
}

function closePromptEditor() {
  if (!pe.backdrop || !pe.backdrop.parentNode) return;
  document.removeEventListener('keydown', onPromptEditorKey);
  pe.backdrop.remove();
  reenterIfFocusLost();
}

function togglePromptEditor() {
  if (pe.backdrop && pe.backdrop.parentNode) closePromptEditor();
  else openPromptEditor();
}

// ---- bootstrap --------------------------------------------------------------------------------

// FOCUS STARTS ON THE BOARD BAR, per the brief. returnToBoardBar was wired only to onExitTop, so
// nothing ever focused on load and every key was dead until the user clicked - which no test saw
loadBoards().then(() => { buildDrawers(); returnToBoardBar(); followRuns(); });
// whether the pointer affordances are on - shortcuts.js owns the flag, board.js owns api()
loadMouseSetting();
loadModelCatalog().catch(() => {});
const issueButton = buildIssueButton();
// who the operator is, for their own lines in the chats and comments - "you" until the board
// answers - and the version the bug form asks for as its first field (version, in bug.yml), which
// is the only field id prefilled: the rest are the tester's to fill in
api('/health').then(h => {
  if (!h) return;
  if (h.operator) operatorName = h.operator;
  if (h.version) issueButton.href = `${ISSUE_FORM_URL}&version=${encodeURIComponent(h.version)}`;
}).catch(() => {});
