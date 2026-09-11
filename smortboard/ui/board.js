// smortboard interface wiring - configuration of ui_base components against the json api.
// no visual primitive is defined here; anything that looks like one belongs in ui_base instead.

const STATUSES = ['todo', 'doing', 'checking', 'accepted', 'rejected'];

// the binding table IS the shortcut overlay's source and the handler dispatch's source, so the
// two cannot drift apart - see openShortcutOverlay and the keydown handler below
// ORDERED BY GROUP: the overlay shows one column per group in this order, so the columns read
// together are still exactly this table
const BINDINGS = [
  {code: 'ArrowUp', label: 'up', action: 'move focus up / exit to board bar', group: 'cards'},
  {code: 'ArrowDown', label: 'down', action: 'move focus down', group: 'cards'},
  {code: 'ArrowLeft', label: 'left', action: 'move focus left', group: 'cards'},
  {code: 'ArrowRight', label: 'right', action: 'move focus right', group: 'cards'},
  {code: 'Enter', label: 'enter', action: 'open the focused card', group: 'cards'},
  {code: 'Space', label: 'space', action: 'open the focused card, or close the open one', group: 'cards'},
  {code: 'Escape', label: 'esc', action: 'one level back: input -> panel -> closed', group: 'cards'},
  {code: 'KeyR', label: 'r', action: 'run the focused card', group: 'cards'},
  {code: 'KeyK', label: 'k', action: 'stop the focused card if it is running', group: 'cards'},
  {code: 'KeyY', label: 'y', action: 'accept the focused card', group: 'cards'},
  {code: 'KeyX', label: 'x', action: 'reject the focused card', group: 'cards'},
  {code: 'KeyM', label: 'm', action: "cycle the card's model", group: 'cards'},
  {code: 'KeyT', label: 't', action: "run replay: scrub the focused card's run step by step", group: 'cards'},
  {code: 'Slash', label: '/', action: "focus the open card's comment input", group: 'cards'},
  {code: 'KeyG', label: 'g', action: 'toggle kanban / workstream grouping', group: 'cards'},
  {code: 'KeyW', label: 'w', action: 'run the board: start / stop the queue', group: 'cards'},
  {code: 'KeyU', label: 'u', action: 'usage: rate-limit windows and per-model spend', group: 'panels'},
  {code: 'KeyI', label: 'i', action: 'cost telemetry: card attempts, or the board cost table', group: 'panels'},
  {code: 'KeyC', label: 'c', action: 'cost overview: spend across every board', group: 'panels'},
  {code: 'KeyA', label: 'a', action: 'agent roster: jump to a working or blocked card', group: 'panels'},
  {code: 'KeyD', label: 'd', action: 'morning digest: pull requests and open questions', group: 'panels'},
  {code: 'KeyS', label: 's', action: 'this shortcut overlay', group: 'panels'},
  {code: 'KeyP', label: 'p', action: 'edit the orchestrator, worker and reviewer prompts', group: 'panels'},
  {code: 'KeyB', label: 'b', action: 'boards and repos: create a board, register a repo', group: 'panels'},
  {code: 'KeyN', label: 'n', action: 'attention inbox: answer a blocked card, across every board', group: 'panels'},
  {code: 'KeyH', label: 'h', action: 'pre-flight checklist: what is missing before a card can run', group: 'panels'},
  {code: 'Comma', label: ',', action: 'workforce: chat with the focused card\'s agent', group: 'panels'},
  {code: 'Period', label: '.', action: 'mission control: chat with the board orchestrator', group: 'panels'},
  ...Array.from({length: 9}, (_, i) => ({
    code: `Digit${i + 1}`, label: String(i + 1), action: `jump to board ${i + 1}`, group: 'panels',
  })),
];

// the overlay's two columns, left to right
const BINDING_GROUPS = [['cards', 'cards and the board'], ['panels', 'panels and boards']];

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
  const cards = await api(`/api/boards/${boardId}/cards`);
  renderBuckets(cards);
  // mission control is per-board, so a board switch while it is open reloads its conversation
  if (drawers.right && drawers.right.isOpen()) loadMissionControl();
}

// ---- buckets of card strips -------------------------------------------------------

function cardClasses(card) {
  // every card fans: the stack is the layout now, not a preview keyed off a workstream
  const classes = ['row', 'card', 'card-strip', 'fan-item'];
  // blocked wins over working: a card waiting on you is not a card making progress, and showing
  // both reads as progress
  if (card.blocked_reason_code) classes.push('card-attention');
  else if (card.status === 'doing') classes.push('card-working');
  else if (card.status === 'rejected') classes.push('card-rejected');
  else if (card.status === 'accepted') classes.push('card-accepted');
  // PREVIEW ONLY - the fan, still being judged
  // PREVIEW - the fan, plus one treatment per column for telling a collapsed stack apart
  return classes.join(' ');
}

function renderBuckets(cards) {
  const row = document.getElementById('bucket-row');
  STATUSES.forEach(status => {
    const bucket = row.querySelector(`.bucket[data-status="${status}"] .bucket-rows`);
    bucket.innerHTML = '';
    // a blocked card still belongs to a column: it keeps the status it was in and carries the
    // reason code, so it renders in place with the gold outline rather than vanishing
    cards.filter(c => c.status === status)
      .forEach(card => bucket.appendChild(renderCardStrip(card)));
  });
  bucketsApi = makeBuckets(row, {onExitTop: returnToBoardBar});
  // the cream marker glides to whatever took focus, rather than every card drawing its own ring.
  // focusin rather than a per-card handler, so it also catches focus arriving by click or by tab
  row.addEventListener('focusin', evt => indicateFocus(evt.target));
}

function renderCardStrip(card) {
  const strip = document.createElement('div');
  strip.className = cardClasses(card);
  strip.tabIndex = -1;
  strip.dataset.cardId = card.id;
  // a card, not a strip: a title band at the top, a rule, the description with the room, and the
  // secondary facts sitting on the floor. ui_base draws the rule with .h-divider - the parent
  // spaces its children and the rule only draws the line
  const stat = card.blocked_reason_code || card.status;
  strip.innerHTML = `
    <div class="card-head"><div class="card-title">${escapeHtml(card.title)}</div></div>
    <div class="h-divider"></div>
    <div class="card-body">${escapeHtml(card.description || '')}</div>
    <div class="h-divider"></div>
    <div class="card-foot">
      <span class="card-workstream">${escapeHtml(card.workstream || '')}</span>
      <span class="stat">${escapeHtml(stat)}</span>
      <span class="card-run" hidden></span>
    </div>
  `;
  const expander = makeExpander(strip, {
    // THREE TIMES THE DEFAULT WIDTH. at 1:3 an open card was a narrow column that wrapped every
    // line of its outcome; makeExpander keeps the height and sizes width from the ratio, so 1:1
    // triples it - still capped at 90% of the viewport width
    expandedRatio: {w: 1, h: 1},
    // THE PLATE BECOMES A BORDER ON THE WAY OPEN. a panel painted with the plate would make a whole
    // screen of it, and the colour stops being a signal once it is the background you are reading
    // on - as an edge it survives the expansion without taking the panel over
    onOpen: panel => {
      cardClasses(card).split(' ')
        .filter(c => c.startsWith('card-') && c !== 'card-strip')
        .forEach(c => panel.classList.add(c));
      // set before the fetch, so space and y/x can close this card while it is still loading
      openCard = {cardId: card.id, expander};
      openCardPanel(panel, card.id);
    },
    onClose: () => { openCard = null; },
  });
  strip.addEventListener('keydown', evt => {
    if (evt.code !== 'Enter' && evt.code !== 'NumpadEnter' && evt.code !== 'Space') return;
    evt.preventDefault();
    // the document handler also closes on space, so a press handled here stops here
    evt.stopPropagation();
    // SPACE TOGGLES. focus stays on the strip behind an open panel, so this handler sees the
    // second press too - it closes the card it opened
    if (evt.code === 'Space' && openCard?.expander === expander) expander.close();
    else expander.open();
  });
  strip._expander = expander;
  return strip;
}

function returnToBoardBar() {
  const tab = document.querySelector('.board-bar .nav-tab.active');
  if (!tab) return;
  tab.focus();
  indicateFocus(tab);
}

// ---- card panel -------------------------------------------------------------------

async function openCardPanel(panel, cardId) {
  const [card, outcome] = await Promise.all([
    api(`/api/cards/${cardId}`),
    api(`/api/cards/${cardId}/outcome`),
  ]);
  // closed while loading - space or y/x can shut it before the fetch lands
  if (!panel.isConnected) return;
  panel.classList.add('card-panel');
  // DESIGN ARCHIVE - a card whose workstream is layout-N wears panel-layouts.css's variant N, so
  // the fifteen layouts behind the 2026-09-10 pick stay on their own board to look back on
  const layout = /^layout-(\d+)$/.exec(card.workstream || '');
  if (layout) panel.classList.add(`panel-layout-${layout[1]}`);
  panel.innerHTML = cardPanelHtml(card, outcome);

  // the panel's one .card-sections div is a single-column bucket - reuses the 2D grid nav as a
  // plain vertical list rather than inventing a second focus system for "move between sections"
  const sectionsApi = makeBuckets(panel, {bucketSel: '.card-sections', rowSel: '.card-section'});

  const input = panel.querySelector('.comment-input');
  // stop Escape here so the panel's own Escape (added by makeExpander) sees a still-open card and
  // only takes the input->panel step - the panel->closed step is makeExpander's own job
  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') {
      evt.stopPropagation();
      // NOT input.blur(). blur drops focus on <body>, and from there every arrow key is dead - the
      // panel has to take it back so escape steps out of the input rather than out of the app
      const section = panel.querySelector('.card-section');
      if (section) section.focus(); else input.blur();
    }
    if (evt.code === 'Enter') {
      evt.preventDefault();
      const body = input.value.trim();
      if (body) api(`/api/cards/${cardId}/comments`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({author: 'fabian', body}),
      }).then(() => openCardPanel(panel, cardId));
    }
  });

  if (openCard && openCard.cardId === cardId) Object.assign(openCard, {sectionsApi, input});
}

// worker summary, test gate, reviewer verdict, PR link and the fix-round count - all of it null
// until a run has actually landed on this card, in which case the section says so plainly
// reason codes that stop a card on the operator rather than on a fault in the work
const ATTENTION_CODES = new Set(['AGENT_QUESTION', 'LEASE_CONFLICT', 'USAGE_LIMIT', 'DEPENDENCY_REJECTED']);

function outcomeSectionHtml(outcome, card = {}) {
  const working = card.status === 'doing' && !card.blocked_reason_code;
  const empty = !outcome || (!outcome.summary && !outcome.tests && !outcome.review && !outcome.pr_url);
  const workingPart = '<div class="outcome-part outcome-working" data-state="doing"><span class="verdict">working</span></div>';
  if (empty) {
    return sectionHtml('outcome', 'outcome', working ? workingPart : '<span class="empty">not run yet</span>');
  }
  // the run's steps in the order they happened, each dot saying what that step came to: done, a
  // problem, waiting on the operator, or still going
  const parts = [];
  if (outcome.summary) {
    const waiting = !outcome.tests && ATTENTION_CODES.has(card.blocked_reason_code);
    parts.push(`<div class="outcome-part outcome-summary" data-state="${waiting ? 'attention' : 'ok'}">${escapeHtml(outcome.summary)}</div>`);
  }
  if (outcome.tests) {
    const t = outcome.tests;
    parts.push(`<div class="outcome-part outcome-tests" data-ok="${t.passed}" data-state="${t.passed ? 'ok' : 'problem'}"><span class="verdict">${t.passed ? 'tests passed' : 'tests failed'}</span> - ${escapeHtml(t.command)} (exit ${t.exit_code})</div>`);
  }
  if (outcome.review) {
    const r = outcome.review;
    const findings = (r.findings || [])
      .map(f => `<li>${escapeHtml(f.severity)} ${escapeHtml(f.category)} in ${escapeHtml(f.file)}: ${escapeHtml(f.message)}</li>`)
      .join('');
    // a review that did not approve comes to the operator (or back to the worker, still going)
    const reviewState = r.approved ? 'ok' : (working ? 'doing' : 'attention');
    parts.push(`<div class="outcome-part outcome-review" data-ok="${r.approved}" data-state="${reviewState}"><span class="verdict">${r.approved ? 'approved' : 'not approved'}</span>${r.error ? `: ${escapeHtml(r.error)}` : ''}${findings ? `<ul class="outcome-findings">${findings}</ul>` : ''}</div>`);
  }
  if (outcome.pr_url) {
    parts.push(`<div class="outcome-part outcome-pr" data-state="ok"><a href="${escapeHtml(outcome.pr_url)}" target="_blank" rel="noreferrer">${escapeHtml(outcome.pr_url)}</a></div>`);
  }
  if (working && !outcome.pr_url) parts.push(workingPart);
  parts.push(`<div class="outcome-route">findings route: ${escapeHtml(outcome.findings_route || '')}, fix rounds: ${outcome.fix_rounds ?? 0}</div>`);
  return sectionHtml('outcome', 'outcome', parts.join(''));
}

// one section per field: data-section names it for the layouts, .field-value holds what it says
function sectionHtml(name, label, value) {
  return `<div class="card-section" tabindex="0" data-section="${name}"><div class="field-label">${label}</div><div class="section-value">${value}</div></div>`;
}

// a list, or a quiet "none" - an empty <ul> drew a label with nothing under it
function listHtml(items) {
  return items.length ? `<ul>${items.join('')}</ul>` : '<span class="empty">none</span>';
}

// a dependency's title, read off its strip on the board; a card on another board has no strip here
function dependencyLabel(cardId) {
  const title = document.querySelector(`.card-strip[data-card-id="${cardId}"] .card-title`);
  return (title && title.textContent) || `card ${String(cardId).slice(0, 8)}`;
}

function cardPanelHtml(card, outcome) {
  const tasks = (card.tasks || []).map(t => `<li>${t.done ? '[x]' : '[ ]'} ${escapeHtml(t.text)}</li>`);
  const criteria = (card.criteria || []).map(c => `<li>${escapeHtml(c.text)}</li>`);
  // depends_on holds ids only - shown by title when that card is on this board, a short id otherwise
  const deps = (card.depends_on || []).map(d => `<li>${escapeHtml(dependencyLabel(d))}</li>`);
  const attachments = (card.attachments || []).map(a => `<li>${escapeHtml(a.filename)}</li>`);
  const comments = (card.comments || [])
    .map(c => `<li><span class="field-label">${escapeHtml(authorLabel(c.author))}</span> ${escapeHtml(c.body)}</li>`);
  const status = escapeHtml(card.status) + (card.blocked_reason_code ? ` (${escapeHtml(card.blocked_reason_code)})` : '');
  return `
    <div class="card-sections">
      ${sectionHtml('title', 'title', escapeHtml(card.title))}
      ${sectionHtml('workstream', 'workstream', escapeHtml(card.workstream || '') || '<span class="empty">none</span>')}
      ${sectionHtml('status', 'status', `${status}<div class="card-model">model: ${escapeHtml(modelLabel(card.model))}</div>`)}
      ${outcomeSectionHtml(outcome, card)}
      ${sectionHtml('description', 'description', escapeHtml(card.description || ''))}
      ${sectionHtml('tasks', 'tasks', listHtml(tasks))}
      ${sectionHtml('criteria', 'acceptance criteria', listHtml(criteria))}
      ${sectionHtml('deps', 'dependencies', listHtml(deps))}
      ${sectionHtml('attachments', 'attachments', listHtml(attachments))}
      <div class="card-section" tabindex="0" data-section="comments"><div class="field-label">comments</div>
        <div class="section-value">${listHtml(comments)}</div>
        <input class="comment-input text-field" placeholder="add a comment, enter to send">
      </div>
    </div>
  `;
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

async function runFocusedCard() {
  const cardId = focusedCardId();
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

// ---- stopping a running card (k) ------------------------------------------------------

// same focus source y/x use: the strip under keyboard focus, or the card whose panel is open
async function stopFocusedCard() {
  const cardId = actionableCardId();
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

// ---- terminal chat, the shared shape behind mission control (.) and workforce (,) ---------------
// a log of author-prefixed lines plus one input row - no bubbles, no avatars, the board's own
// monospace and 2px lines. styling lives in layout.css as configuration, same rule board.js
// states at the top of this file

// built with createElement/appendChild rather than innerHTML, so the refs below are live nodes -
// the test dom stub does not parse innerHTML strings back into a tree, and a real browser doesn't
// care either way
function terminalDom(promptGlyph) {
  const wrap = document.createElement('div');
  wrap.className = 'terminal';

  const header = document.createElement('div');
  header.className = 'terminal-header';
  const title = document.createElement('span');
  title.className = 'terminal-title';
  const cycle = document.createElement('span');
  cycle.className = 'terminal-cycle';
  cycle.hidden = true;
  const prev = document.createElement('span');
  prev.className = 'terminal-prev toggle';
  prev.textContent = '<';
  const count = document.createElement('span');
  count.className = 'terminal-count';
  const next = document.createElement('span');
  next.className = 'terminal-next toggle';
  next.textContent = '>';
  cycle.append(prev, count, next);
  header.append(title, cycle);

  const subheader = document.createElement('div');
  subheader.className = 'terminal-subheader';
  subheader.hidden = true;

  const log = document.createElement('div');
  log.className = 'terminal-log';
  log.tabIndex = 0;

  const inputRow = document.createElement('div');
  inputRow.className = 'terminal-input-row';
  const prompt = document.createElement('span');
  prompt.className = 'terminal-prompt';
  prompt.textContent = promptGlyph;
  const input = document.createElement('textarea');
  input.className = 'terminal-input text-field';
  input.rows = 1;
  inputRow.append(prompt, input);

  wrap.append(header, subheader, log, inputRow);
  return wrap;
}

// the name shown for the operator's own lines - /health says who; stored rows keep the key "fabian"
let operatorName = 'you';
function authorLabel(author) {
  return author === 'fabian' ? operatorName : author;
}

// author drives the line's colour class; cls overrides it for board/error/thinking lines whose
// author name (e.g. "orchestrator") shouldn't paint the same as an authored message would
function appendLine(log, author, body, cls) {
  const line = document.createElement('div');
  line.className = `terminal-line author-${cls || author}`;
  line.innerHTML = `<div class="terminal-author">${escapeHtml(authorLabel(author))}</div>` +
    `<div class="terminal-body">${escapeHtml(body)}</div>`;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
  return line;
}

// enter sends, shift+enter is left alone so the textarea's own newline behaviour handles it.
// escape steps from the input to the log, which is focusable so the drawer's own key then closes it
function wireTerminalInput(input, log, onSend) {
  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); log.focus(); return; }
    if (evt.code === 'Enter' && !evt.shiftKey) {
      evt.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      input.value = '';
      onSend(text);
    }
  });
}

// ---- mission control (.) - the orchestrator's chat for the current board ------------------------

const mc = {header: null, cycle: null, log: null, input: null, poll: null};

function buildMissionControlDom(drawer) {
  const term = terminalDom('>');
  drawer.body.appendChild(term);
  mc.header = term.querySelector('.terminal-title');
  mc.log = term.querySelector('.terminal-log');
  mc.input = term.querySelector('.terminal-input');
  wireTerminalInput(mc.input, mc.log, sendMissionControl);
}

async function openMissionControl() {
  mc.input.focus();
  await loadMissionControl();
}

function closeMissionControl() {
  clearTimeout(mc.poll);
}

async function loadMissionControl() {
  clearTimeout(mc.poll);
  if (!currentBoardId) {
    mc.header.textContent = 'mission control';
    mc.log.innerHTML = '';
    appendLine(mc.log, 'board', 'no board selected', 'board');
    return;
  }
  try {
    const data = await api(`/api/boards/${currentBoardId}/orchestrator`);
    renderMissionControl(data);
  } catch (err) {
    mc.header.textContent = 'mission control';
    mc.log.innerHTML = '';
    appendLine(mc.log, 'board', `could not reach the orchestrator: ${err.message}`, 'error');
  }
}

// justFinished marks a poll result, the only moment a newly-created card should pull the board
function renderMissionControl(data, {justFinished = false} = {}) {
  mc.header.textContent = `mission control - ${data.model || '?'}`;
  mc.log.innerHTML = '';
  (data.messages || []).forEach(m => {
    appendLine(mc.log, m.author, m.body);
    if (m.cards && m.cards.length) {
      appendLine(mc.log, 'board', `created: ${m.cards.map(c => c.title).join(', ')}`, 'board');
    }
  });
  if (data.error) appendLine(mc.log, 'board', data.error, 'error');
  if (data.thinking) {
    appendLine(mc.log, 'orchestrator', 'orchestrator is thinking', 'thinking');
    mc.poll = setTimeout(pollMissionControl, 1500);
    return;
  }
  const last = data.messages && data.messages[data.messages.length - 1];
  if (justFinished && last && last.cards && last.cards.length && currentBoardId) {
    onBoardEnter(currentBoardId);
  }
}

async function pollMissionControl() {
  if (!drawers.right || !drawers.right.isOpen() || !currentBoardId) return;
  try {
    const data = await api(`/api/boards/${currentBoardId}/orchestrator`);
    renderMissionControl(data, {justFinished: true});
  } catch (err) {
    appendLine(mc.log, 'board', `lost contact with the orchestrator: ${err.message}`, 'error');
  }
}

async function sendMissionControl(text) {
  if (!currentBoardId) { appendLine(mc.log, 'board', 'no board selected', 'error'); return; }
  appendLine(mc.log, 'fabian', text);
  try {
    const res = await fetch(`/api/boards/${currentBoardId}/orchestrator`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: text}),
    });
    const body = await res.json().catch(() => null);
    if (res.status === 409) { appendLine(mc.log, 'board', (body && body.error) || 'already thinking', 'error'); return; }
    if (!res.ok) { appendLine(mc.log, 'board', (body && body.error) || `request failed (${res.status})`, 'error'); return; }
    renderMissionControl(body);
  } catch (err) {
    appendLine(mc.log, 'board', `could not reach the orchestrator: ${err.message}`, 'error');
  }
}

// ---- workforce (,) - one card's agent chat -------------------------------------------------------

const wf = {header: null, cycle: null, count: null, subheader: null, log: null, input: null,
  poll: null, rotate: null, prev: null, next: null, cardId: null, working: [], index: 0, pinned: false};

// MALL CAM: with no card focused or open, the workforce is not pinned to one - it rotates through
// every working card on its own, like a security monitor. arrows still step it by hand
const MALL_CAM_SECONDS = 8;

function buildWorkforceDom(drawer) {
  const term = terminalDom('$');
  drawer.body.appendChild(term);
  wf.header = term.querySelector('.terminal-title');
  wf.cycle = term.querySelector('.terminal-cycle');
  wf.count = term.querySelector('.terminal-count');
  wf.subheader = term.querySelector('.terminal-subheader');
  wf.log = term.querySelector('.terminal-log');
  wf.input = term.querySelector('.terminal-input');
  wireTerminalInput(wf.input, wf.log, sendWorkforce);
  wf.prev = term.querySelector('.terminal-prev');
  wf.next = term.querySelector('.terminal-next');
  wf.prev.onclick = () => cycleWorkforce(-1);
  wf.next.onclick = () => cycleWorkforce(1);
}

async function openWorkforce() {
  // THE CARD IS READ BEFORE THE INPUT TAKES FOCUS. focusing first left no card focused, so , said
  // "no card is focused" over the card you were looking at
  const target = resolveWorkforceTarget();
  wf.input.focus();
  await loadWorkforce(await target);
}

function closeWorkforce() {
  clearTimeout(wf.poll);
  clearTimeout(wf.rotate);
}

// focused card wins, then the open card, then the roster's first working row - so , always lands
// on the card you are looking at before it falls back to whatever is running
async function resolveWorkforceTarget() {
  const focused = focusedCardId();
  if (focused) return {cardId: focused, pinned: true};
  if (openCard) return {cardId: openCard.cardId, pinned: true};
  let roster = [];
  try { roster = await api('/api/roster'); } catch (err) { roster = []; }
  const working = roster.filter(r => r.state === 'working');
  if (!working.length) return {cardId: null, pinned: false, working: []};
  return {cardId: working[0].card_id, pinned: false, working, index: 0};
}

async function loadWorkforce(resolved) {
  const target = resolved || await resolveWorkforceTarget();
  wf.cardId = target.cardId;
  wf.pinned = target.pinned;
  wf.working = target.working || [];
  wf.index = target.index || 0;
  if (!wf.cardId) {
    wf.header.textContent = 'workforce';
    wf.subheader.hidden = true;
    wf.cycle.hidden = true;
    wf.log.innerHTML = '';
    appendLine(wf.log, 'board', 'no card is focused and no agent is running', 'board');
    return;
  }
  await renderWorkforceConversation();
}

// the header's mode: "pinned" to the card you are on, or the mall cam and where it is in its round
function updateWorkforceCycle() {
  const rotating = !wf.pinned && wf.working.length > 1;
  wf.cycle.hidden = false;
  if (wf.prev) wf.prev.hidden = !rotating;
  if (wf.next) wf.next.hidden = !rotating;
  if (wf.pinned) wf.count.textContent = 'pinned';
  else wf.count.textContent = rotating ? `mall cam ${wf.index + 1}/${wf.working.length}` : 'mall cam';
  clearTimeout(wf.rotate);
  if (rotating && drawers.left && drawers.left.isOpen()) {
    wf.rotate = setTimeout(() => cycleWorkforce(1), MALL_CAM_SECONDS * 1000);
  }
}

function cycleWorkforce(delta) {
  if (wf.pinned || wf.working.length < 2) return;
  wf.index = (wf.index + delta + wf.working.length) % wf.working.length;
  wf.cardId = wf.working[wf.index].card_id;
  renderWorkforceConversation();
}

// what each delivery mode reads as. a second spike (after the first) sent an unmarked mid-turn
// message between two tool calls and it reached the model there, inside the same turn - so "live"
// really does mean the agent's next step, not only after it finishes the whole turn
const DELIVERY_LABEL = {
  live: "delivered at the agent's next step",
  next_run: 'reaches the agent on its next run',
};

async function renderWorkforceConversation() {
  clearTimeout(wf.poll);
  wf.subheader.hidden = false;
  updateWorkforceCycle();
  try {
    const data = await api(`/api/cards/${wf.cardId}/conversation`);
    wf.subheader.textContent = DELIVERY_LABEL[data.delivery] || DELIVERY_LABEL.next_run;
    wf.header.textContent = `${data.title} - ${data.running ? `running - ${data.phase || '...'}` : 'idle'}`;
    wf.log.innerHTML = '';
    (data.messages || []).forEach(m => appendLine(wf.log, m.author, m.body));
    if (data.running && drawers.left && drawers.left.isOpen()) {
      wf.poll = setTimeout(renderWorkforceConversation, 2000);
    }
  } catch (err) {
    wf.header.textContent = 'workforce';
    appendLine(wf.log, 'board', `could not load conversation: ${err.message}`, 'error');
  }
}

async function sendWorkforce(text) {
  if (!wf.cardId) return;
  appendLine(wf.log, 'fabian', text);
  try {
    const res = await fetch(`/api/cards/${wf.cardId}/conversation`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: text}),
    });
    const body = await res.json().catch(() => null);
    if (!res.ok) {
      appendLine(wf.log, 'board', (body && body.error) || `request failed (${res.status})`, 'error');
      return;
    }
    if (body && body.delivery) {
      wf.subheader.hidden = false;
      wf.subheader.textContent = DELIVERY_LABEL[body.delivery] || DELIVERY_LABEL.next_run;
    }
  } catch (err) {
    appendLine(wf.log, 'board', `could not reach the agent: ${err.message}`, 'error');
  }
}

// ---- the two side drawers -------------------------------------------------------------------

// the two side drawers, parked off-screen with a sliver showing. built once and reused: a drawer
// rebuilt per keypress loses its open state and re-animates from parked every time
const drawers = {};

// the gap above and below a drawer, equal at both ends so it reads as pinned rather than floating
const DRAWER_INSET_PX = 50;

function drawerFor(edge) {
  if (drawers[edge]) return drawers[edge];
  const bar = document.querySelector('.board-bar');
  const drawer = makeDrawer({
    edge,
    // two fifths of the eighth it used to show - enough to say a drawer is there, little
    // enough that it stays out of the way of the board
    sliverRatio: 0.05,
    widthRatio: 0.85,
    top: (bar ? Math.round(bar.getBoundingClientRect().bottom) : 0) + DRAWER_INSET_PX,
    bottom: DRAWER_INSET_PX,
    onOpen: () => (edge === 'right' ? openMissionControl() : openWorkforce()),
    onClose: () => {
      // typing in a drawer's own input/log must not leave focus dangling once the drawer parks -
      // hand it back to the board, the same recovery reenterIfFocusLost already does elsewhere
      if (drawer.el.contains(document.activeElement)) {
        document.activeElement.blur?.();
        reenterIfFocusLost();
      }
      if (edge === 'right') closeMissionControl(); else closeWorkforce();
    },
  });
  if (edge === 'right') buildMissionControlDom(drawer);
  else buildWorkforceDom(drawer);
  drawers[edge] = drawer;
  return drawer;
}

// BUILT AT STARTUP, NOT ON FIRST PRESS. the sliver is the affordance that tells you a drawer is
// there at all, so a drawer that only exists once you already knew to press the key is useless
function buildDrawers() {
  drawerFor('left');
  drawerFor('right');
}

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
      box.innerHTML = '<span class="hazard-label">no agent is holding a card</span>';
      menu.refresh([{kind: 'node', node: box}]);
      return;
    }
    const items = roster.map(r => ({
      id: r.card_id, label: r.title,
      stats: r.state === 'blocked' ? `blocked: ${r.reason || ''}` : r.activity,
      on: r.state === 'working', disabled: r.state === 'blocked',
    }));
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

// ---- model (m) - which model the focused card's worker runs on ------------------------------------

// null is the board default (the worker_model setting, else sonnet); the rest are claude aliases
const CARD_MODELS = [null, 'haiku', 'sonnet', 'opus'];

function modelLabel(model) {
  return model || 'board default';
}

// cycles default -> haiku -> sonnet -> opus -> default. a model set outside the cycle (a full id)
// steps back to the default, so the key always lands somewhere the next press can leave
async function cycleCardModel() {
  const cardId = actionableCardId();
  if (!cardId) return;
  try {
    const card = await api(`/api/cards/${cardId}`);
    const next = CARD_MODELS[(CARD_MODELS.indexOf(card.model ?? null) + 1) % CARD_MODELS.length];
    const updated = await api(`/api/cards/${cardId}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({model: next}),
    });
    const label = `model: ${modelLabel(updated.model)}`;
    showRun(cardId, label);
    const shown = document.querySelector('.card-panel [data-section="status"] .card-model');
    if (shown && openCard && openCard.cardId === cardId) shown.textContent = label;
  } catch (err) {
    showRun(cardId, "can't change model", null, err.message);
  }
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

// A CARD'S LANGUAGE: ruled sections with dim labels, and a foot carrying the total - built with
// createElement so every line is its own element, which is also what the node tests read
function usageCard(data) {
  const windowParts = (data.windows || []).map(w => {
    const section = usageSection(windowLabel(w.type), 'usage-window');
    const {fraction} = windowMeasure(w);
    if (fraction != null) section.appendChild(fillBar(fraction, w.status === 'allowed' ? '' : 'warn'));
    section.appendChild(textLine(windowStats(w), 'stat'));
    return section;
  });
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
    const data = await api('/api/usage');
    menu.refresh(usageSections(data));
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

// ---- shortcut overlay, built from BINDINGS so it cannot drift -----------------------

// TWO COLUMNS, one per binding group, each a list section - Menu's column mode lays sections side
// by side with a divider between. the class widens this one menu to hold both
function openShortcutOverlay() {
  toggleOverlay('KeyS', () => {
    const sections = BINDING_GROUPS.map(([group, label]) => ({
      kind: 'list',
      label,
      items: BINDINGS.filter(b => b.group === group)
        .map(b => ({id: b.code, label: `${b.label} - ${b.action}`, disabled: true})),
    }));
    const menu = new Menu({
      title: 'keyboard shortcuts',
      columns: true,
      sections,
      onDismiss: () => { if (openOverlay && openOverlay.key === 'KeyS') openOverlay = null; },
    });
    menu.openAt({x: Math.max(16, window.innerWidth / 2 - 500), y: 60});
    menu.el?.classList.add('shortcut-overlay', 'menu-centered');
    return menu;
  });
}

// ---- global keyboard model, entirely on event.code ----------------------------------

// FOCUS ON <body> MEANS THE KEYBOARD IS DEAD, whatever dropped it there. rather than hunt every
// path that can lose focus, any bound key with nothing focused re-enters the board first and then
// does what it was going to do
function reenterIfFocusLost() {
  if (document.activeElement && document.activeElement !== document.body) return false;
  const card = document.querySelector('.bucket .row');
  if (card) { card.focus(); indicateFocus(card); return true; }
  returnToBoardBar();
  return true;
}

document.addEventListener('keydown', evt => {
  const recovered = reenterIfFocusLost();
  const typing = evt.target.matches?.('input, textarea');
  const binding = BINDINGS.find(b => b.code === evt.code);
  if (!binding) return;

  if (recovered && evt.code.startsWith('Arrow')) { evt.preventDefault(); return; }
  if (evt.code === 'ArrowDown' && evt.target.closest?.('.board-bar')) {
    evt.preventDefault();
    document.querySelector('.bucket .row')?.focus();
    return;
  }
  if ((evt.code === 'Enter' || evt.code === 'Space') && evt.target.closest?.('.board-bar')) {
    return; // shell.js already activates the tab
  }
  if (typing) return; // letters and slash only fire the model outside text input

  // THE KEY THAT OPENS ALSO CLOSES, next to escape - and in the comment input, which returned
  // above, it stays a space
  if (evt.code === 'Space' && openCard) { evt.preventDefault(); openCard.expander.close(); return; }

  if (evt.code === 'KeyG') { grouped = !grouped; return; }
  if (evt.code === 'KeyW') { toggleRunAll(); return; }
  if (evt.code === 'KeyU') { openUsagePanel(); return; }
  if (evt.code === 'KeyI') { openTelemetryPanel(); return; }
  if (evt.code === 'KeyC') { openCostsOverviewPanel(); return; }
  if (evt.code === 'KeyA') { openRosterPanel(); return; }
  if (evt.code === 'KeyD') { openDigestPanel(); return; }
  if (evt.code === 'KeyR') { runFocusedCard(); return; }
  if (evt.code === 'KeyK') { stopFocusedCard(); return; }
  if (evt.code === 'KeyY') { acceptOrRejectCard('accept'); return; }
  if (evt.code === 'KeyX') { acceptOrRejectCard('reject'); return; }
  if (evt.code === 'KeyM') { cycleCardModel(); return; }
  if (evt.code === 'KeyT') { toggleReplay(); return; }
  if (evt.code === 'KeyS') { openShortcutOverlay(); return; }
  if (evt.code === 'KeyP') { togglePromptEditor(); return; }
  if (evt.code === 'KeyN') { toggleInboxPanel(); return; }
  if (evt.code === 'KeyH') { evt.preventDefault(); togglePreflightPanel(); return; }
  // preventDefault: the panel focuses its first input, and the key that opened it typed itself there
  if (evt.code === 'KeyB') { evt.preventDefault(); toggleBoardsPanel(); return; }
  // preventDefault: opening a drawer focuses its input, and the key that opened it typed itself there
  if (evt.code === 'Comma') { evt.preventDefault(); drawerFor('left').toggle(); return; }
  if (evt.code === 'Period') { evt.preventDefault(); drawerFor('right').toggle(); return; }
  if (evt.code === 'Slash' && openCard?.input) { evt.preventDefault(); openCard.input.focus(); return; }
  if (binding.code.startsWith('Digit')) {
    const index = Number(binding.label) - 1;
    const board = boards[index];
    if (board) activateTab(board.id);
  }
});

// FOCUS STARTS ON THE BOARD BAR, per the brief. returnToBoardBar was wired only to onExitTop, so
// nothing ever focused on load and every key was dead until the user clicked - which no test saw
loadBoards().then(() => { buildDrawers(); returnToBoardBar(); });
// who the operator is, for their own lines in the chats and comments - "you" until the board answers
api('/health').then(h => { if (h && h.operator) operatorName = h.operator; }).catch(() => {});
