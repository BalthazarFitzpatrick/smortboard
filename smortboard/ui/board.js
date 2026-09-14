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

let boards = [];
let currentBoardId = null;
let bucketsApi = null;
let grouped = false; // g toggles this; workstream layout itself ships post-v1
let openCard = null; // {cardId, expander, sectionsApi, input} while a card panel is open

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.status === 204 ? null : res.json();
}

// like api(), but a 409 is a normal outcome here (accept/reject refusal) rather than a throw -
// the caller needs the server's error text, which plain api() discards
async function apiOrError(path, opts) {
  const res = await fetch(path, opts);
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  return {ok: res.ok, body};
}

// ---- board bar ------------------------------------------------------------------

function renderBoardBar() {
  const bar = document.getElementById('board-bar');
  bar.innerHTML = '';
  boards.forEach(board => {
    const btn = document.createElement('div');
    btn.className = 'nav-tab toggle';
    btn.dataset.tab = board.id;
    btn.textContent = board.name;
    bar.appendChild(btn);
  });
  // fold acts on whichever board is open. built here with the tabs, since this function wipes the
  // bar - and not a .nav-tab, which shell.js would treat as one more board
  if (boards.length) {
    const fold = document.createElement('div');
    fold.className = 'toggle board-fold';
    fold.textContent = 'fold (f)';
    fold.addEventListener('click', () => openFoldConfirm());
    bar.appendChild(fold);
  }
}

async function loadBoards() {
  boards = await api('/api/boards');
  renderBoardBar();
  renderEmptyState(boards.length === 0);
  if (boards.length === 0) return;
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
  // a fresh board has its own cards under these ids - forget the old board's snapshot so
  // followRunsOnce learns this one before it starts diffing against it
  followedCardStates = null;
  const cards = await api(`/api/boards/${boardId}/cards`);
  renderBuckets(cards);
  // resumes this board's send queue (a reload landed here with something still unsent) whether or
  // not mission control is open - a message keeps retrying in the background either way.
  // guarded: messageQueue.js is a separate script (see index.html's load order) and some isolated
  // test bundles load board.js without it - this backs off quietly rather than throwing there
  if (typeof createMessageQueue === 'function') mcQueueFor(boardId);
  // mission control is per-board, so a board switch while it is open reloads its conversation
  if (drawers.right && drawers.right.isOpen()) loadMissionControl();
}

// ---- buckets of card strips -------------------------------------------------------
// per-card classes, CTA and the strip itself live in card_panel.js; the tier a column renders at
// (full cards, title chips, or chips plus aggregates) is columns.js's renderBucketColumn - this is
// only the five-column shell that hands each status's cards to it

function renderBuckets(cards) {
  const row = document.getElementById('bucket-row');
  STATUSES.forEach(status => {
    const bucketEl = row.querySelector(`.bucket[data-status="${status}"]`);
    // a blocked card still belongs to a column: it keeps the status it was in and carries the
    // reason code, so it renders in place with the gold outline rather than vanishing
    renderBucketColumn(bucketEl, cards.filter(c => c.status === status), status);
  });
  refreshBucketNav();
  // the cream marker glides to whatever took focus, rather than every card drawing its own ring.
  // focusin rather than a per-card handler, so it also catches focus arriving by click or by tab
  row.addEventListener('focusin', evt => indicateFocus(evt.target));
}

// rebinds the 2D grid nav to whatever is currently in the buckets - a full renderBuckets always
// needs this, and so does a targeted redrawCardStrip that moved a strip to a new bucket
function refreshBucketNav() {
  const row = document.getElementById('bucket-row');
  bucketsApi = makeBuckets(row, {onExitTop: returnToBoardBar});
}

function bucketHasCards(bucket) {
  return bucket.querySelector('.bucket-rows')?.children.length > 0;
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
  if (target) { target.focus(); indicateFocus(target); }
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

function showRun(cardId, text, href, detail) {
  const badge = runBadge(cardId);
  if (!badge) return;
  badge.hidden = false;
  badge.textContent = '';
  // the foot is one line and must stay one line - the long version is the comment the run left
  badge.title = detail || '';
  if (href) {
    const link = document.createElement('a');
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

  const runtime = await api('/api/runtime');
  if (!runtime.ready) { reportNotReady(runtime.missing); return; }

  showRun(cardId, 'starting');
  const state = await api(`/api/cards/${cardId}/run`, {method: 'POST'});
  if (!state.running) { finishRun(cardId, state); return; }
  pollRun(cardId);
}

function pollRun(cardId) {
  setTimeout(async () => {
    const state = await api(`/api/cards/${cardId}/run`);
    if (state.running) {
      showRun(cardId, state.phase);
      pollRun(cardId);
      return;
    }
    finishRun(cardId, state);
  }, RUN_POLL_MS);
}

// A CARD CAN CHANGE COLUMN MID-RUN, NOT ONLY WHEN THE RUN ENDS. todo -> doing and doing -> checking
// happen inside lifecycle.py while the run is still going, and whatever wrote the change (this
// page's own run, another run, a key, mission control) already committed it before we asked. so
// the board polls every card's status and updated_at and redraws only the strips that moved -
// leaving every other strip, and the DOM in general, untouched
const FOLLOW_RUNS_MS = 4000;
let followedCardStates = null; // Map<card id, `${status}|${updated_at}`> as of the last poll

// swaps one card's strip for a freshly rendered one in its (possibly new) bucket, and refocuses it
// if it held focus - the open card's own strip is never passed in here, see followRunsOnce
function redrawCardStrip(card) {
  const old = document.querySelector(`.card-strip[data-card-id="${card.id}"]`);
  const bucket = document.querySelector(`.bucket[data-status="${card.status}"] .bucket-rows`);
  if (!old || !bucket) return;
  const hadFocus = old.contains(document.activeElement);
  const strip = renderCardStrip(card);
  bucket.appendChild(strip);
  old.remove();
  if (hadFocus) { strip.focus(); indicateFocus(strip); }
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
  cards.forEach(card => {
    const state = states.get(card.id);
    if (before.get(card.id) === state) return;
    // the open card keeps its panel - its entry is left stale here so the next poll sees it as
    // changed again, and it gets its redraw once the card closes
    if (openCard && openCard.cardId === card.id) return;
    toRedraw.push(card);
    next.set(card.id, state);
  });
  followedCardStates = next;
  if (!toRedraw.length) return false;
  toRedraw.forEach(redrawCardStrip);
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
  const menu = new Menu({
    title: 'stop this run?',
    sections: [{
      kind: 'list',
      items: [
        {id: 'stop', label: 'stop the run'},
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
  if (currentBoardId) await onBoardEnter(currentBoardId);
  // the reload built new strips, so put focus back on the card that ran. without this the user
  // loses their place after every run AND the result is invisible: only the focused card's foot
  // is showing in the fan, so the badge lands on a card nobody can see
  const strip = document.querySelector(`.card-strip[data-card-id="${cardId}"]`);
  if (strip) { strip.focus(); indicateFocus(strip); }
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

async function acceptOrRejectCard(action) {
  const cardId = actionableCardId();
  if (!cardId) return;
  // CLOSE FIRST, THEN MOVE. deciding from inside an open card used to slide the strip into its new
  // column behind a panel still standing over it
  if (openCard) openCard.expander.close();

  const {ok, body} = await apiOrError(`/api/cards/${cardId}/${action}`, {method: 'POST'});
  // NAME THE ACTION THAT WAS REFUSED. a bare "refused" beside the card's own "accepted" read as
  // the two labels swapped
  if (!ok) { showRun(cardId, `can't ${action}`, null, (body && body.error) || ''); return; }

  // same order finishRun uses and for the same reason: reload first, THEN focus, THEN badge, or
  // the reload's fresh strips throw the badge and the focus away with the old ones
  const from = document.querySelector(`.card-strip[data-card-id="${cardId}"]`)
    ?.getBoundingClientRect();
  if (currentBoardId) await onBoardEnter(currentBoardId);
  const strip = document.querySelector(`.card-strip[data-card-id="${cardId}"]`);
  if (strip) {
    if (from) slideFrom(strip, from);
    strip.focus();
    indicateFocus(strip);
  }
  // no badge on success: the foot's status already says accepted or rejected, and the border says it
  // in colour - a second "accepted" beside the first was noise
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
  const menu = build();
  openOverlay = {key, menu};
  return menu;
}

// ---- fold (f) - merge the todo cards one agent should do as one ------------------------------

// ASKS FIRST, every time: a fold is a model run over the whole board, not a free local action
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
        {kind: 'list', items: [{id: 'yes', label: 'yes, fold (y)'}, {id: 'no', label: 'no (n)'}],
          onPick: item => answerFold(item.id === 'yes', boardId)},
      ],
      onDismiss: () => { if (openOverlay && openOverlay.key === 'KeyF') openOverlay = null; },
    });
    menu.openAt({x: window.innerWidth / 2 - 200, y: 80});
    menu.el?.classList.add('menu-centered');
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
  foldPoll = setInterval(async () => {
    const state = await api(`/api/boards/${boardId}/fold`).catch(() => null);
    if (state && state.running) return;
    clearInterval(foldPoll);
    foldPoll = null;
    if (currentBoardId === boardId) await onBoardEnter(boardId);
  }, 3000);
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
  const strip = document.querySelector(`.card-strip[data-card-id="${cardId}"]`);
  if (!strip) return;
  strip.focus();
  indicateFocus(strip);
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
    if (fraction != null) row.appendChild(fillBar(fraction, window.status === 'allowed' ? '' : 'warn'));
    row.appendChild(textLine(windowStats(window), 'stat'));
  } else {
    row.appendChild(fillBar(0));
    row.appendChild(textLine('no usage yet', 'stat'));
  }
  return row;
}

// windows grouped by type, each type a section holding one row per known profile - windows carry
// no profile of their own before this card, so a window with none reads as the "default" profile
function windowSections(windows, profileNames) {
  const byType = new Map();
  windows.forEach(w => {
    if (!byType.has(w.type)) byType.set(w.type, new Map());
    byType.get(w.type).set(w.profile || 'default', w);
  });
  return [...byType.entries()].map(([type, byProfile]) => {
    const section = usageSection(windowLabel(type), 'usage-window');
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
  const profileNames = (data.profiles || []).map(p => p.name);
  const windowParts = windowSections(data.windows || [], profileNames);
  const modelParts = [];
  const models = data.models || [];
  if (models.length) {
    const section = usageSection('spend by model', 'usage-models');
    const total = data.total_cost_usd || 0;
    models.forEach(m => {
      const row = document.createElement('div');
      row.className = 'usage-model';
      const share = total > 0 ? (m.cost_usd || 0) / total : null;
      const shareNote = share != null ? ` - ${Math.round(share * 100)}% of spend` : '';
      row.appendChild(textLine(`${m.model} - $${(m.cost_usd || 0).toFixed(2)}${shareNote}`, 'usage-model-name'));
      if (share != null) row.appendChild(fillBar(share));
      row.appendChild(textLine(`in ${formatTokenCount(m.input_tokens)} - out ${formatTokenCount(m.output_tokens)} - ` +
        `cache ${formatTokenCount((m.cache_read_tokens || 0) + (m.cache_creation_tokens || 0))}`, 'stat'));
      section.appendChild(row);
    });
    modelParts.push(section);
  }
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
  foot.appendChild(textLine(`${data.runs || 0} runs - $${(data.total_cost_usd || 0).toFixed(2)}`, 'stat'));
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
    if (evt.code === 'Escape') { evt.stopPropagation(); closePromptEditor(); return; }
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
  pe.textarea.focus();
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
// who the operator is, for their own lines in the chats and comments - "you" until the board answers
api('/health').then(h => { if (h && h.operator) operatorName = h.operator; }).catch(() => {});
