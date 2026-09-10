// smortboard interface wiring - configuration of ui_base components against the json api.
// no visual primitive is defined here; anything that looks like one belongs in ui_base instead.

const STATUSES = ['todo', 'doing', 'checking', 'accepted', 'rejected'];

// the binding table IS the shortcut overlay's source and the handler dispatch's source, so the
// two cannot drift apart - see openShortcutOverlay and the keydown handler below
const BINDINGS = [
  {code: 'ArrowUp', label: 'up', action: 'move focus up / exit to board bar'},
  {code: 'ArrowDown', label: 'down', action: 'move focus down'},
  {code: 'ArrowLeft', label: 'left', action: 'move focus left'},
  {code: 'ArrowRight', label: 'right', action: 'move focus right'},
  {code: 'Enter', label: 'enter', action: 'open the focused card'},
  {code: 'Space', label: 'space', action: 'open the focused card, or close the open one'},
  {code: 'Escape', label: 'esc', action: 'one level back: input -> panel -> closed'},
  {code: 'KeyG', label: 'g', action: 'toggle kanban / workstream grouping'},
  {code: 'KeyU', label: 'u', action: 'usage dropdown (placeholder)'},
  {code: 'KeyA', label: 'a', action: 'agent roster (placeholder)'},
  {code: 'KeyR', label: 'r', action: 'run the focused card'},
  {code: 'KeyY', label: 'y', action: 'accept the focused card'},
  {code: 'KeyX', label: 'x', action: 'reject the focused card'},
  {code: 'KeyS', label: 's', action: 'this shortcut overlay'},
  {code: 'Slash', label: '/', action: "focus the open card's comment input"},
  {code: 'Comma', label: ',', action: 'agent observation deck'},
  {code: 'Period', label: '.', action: 'orchestration and planning agent'},
  ...Array.from({length: 9}, (_, i) => ({
    code: `Digit${i + 1}`, label: String(i + 1), action: `jump to board ${i + 1}`,
  })),
];

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
  if (boards.length === 0) {
    renderEmptyState();
    return;
  }
  initShell({onEnter: onBoardEnter, fallback: boards[0]?.id || ''});
}

// a fresh install has no boards - say so instead of showing five silent empty columns
function renderEmptyState() {
  const row = document.getElementById('bucket-row');
  row.innerHTML = '';
  const box = document.createElement('div');
  box.className = 'hazard-stripes hazard-placeholder';
  box.innerHTML = `<span class="hazard-label">no boards yet</span>
    <span class="hazard-note">POST /api/boards to create one</span>`;
  row.appendChild(box);
}

async function onBoardEnter(boardId) {
  currentBoardId = boardId;
  const cards = await api(`/api/boards/${boardId}/cards`);
  renderBuckets(cards);
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
function outcomeSectionHtml(outcome) {
  const empty = !outcome || (!outcome.summary && !outcome.tests && !outcome.review && !outcome.pr_url);
  if (empty) return sectionHtml('outcome', 'outcome', '<span class="empty">not run yet</span>');
  // the run's steps in the order they happened, each marked passed or not, so a layout can draw
  // them as a sequence
  const parts = [];
  if (outcome.summary) parts.push(`<div class="outcome-part outcome-summary">${escapeHtml(outcome.summary)}</div>`);
  if (outcome.tests) {
    const t = outcome.tests;
    parts.push(`<div class="outcome-part outcome-tests" data-ok="${t.passed}"><span class="verdict">${t.passed ? 'tests passed' : 'tests failed'}</span> - ${escapeHtml(t.command)} (exit ${t.exit_code})</div>`);
  }
  if (outcome.review) {
    const r = outcome.review;
    const findings = (r.findings || [])
      .map(f => `<li>${escapeHtml(f.severity)} ${escapeHtml(f.category)} in ${escapeHtml(f.file)}: ${escapeHtml(f.message)}</li>`)
      .join('');
    parts.push(`<div class="outcome-part outcome-review" data-ok="${r.approved}"><span class="verdict">${r.approved ? 'approved' : 'not approved'}</span>${r.error ? `: ${escapeHtml(r.error)}` : ''}${findings ? `<ul class="outcome-findings">${findings}</ul>` : ''}</div>`);
  }
  if (outcome.pr_url) {
    parts.push(`<div class="outcome-part outcome-pr"><a href="${escapeHtml(outcome.pr_url)}" target="_blank" rel="noreferrer">${escapeHtml(outcome.pr_url)}</a></div>`);
  }
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

function cardPanelHtml(card, outcome) {
  const tasks = (card.tasks || []).map(t => `<li>${t.done ? '[x]' : '[ ]'} ${escapeHtml(t.text)}</li>`);
  const criteria = (card.criteria || []).map(c => `<li>${escapeHtml(c.text)}</li>`);
  // depends_on is the store's key - this read card.deps, which never existed, so the section was
  // always empty
  const deps = (card.depends_on || []).map(d => `<li>${escapeHtml(d)}</li>`);
  const attachments = (card.attachments || []).map(a => `<li>${escapeHtml(a.filename)}</li>`);
  const comments = (card.comments || [])
    .map(c => `<li><span class="field-label">${escapeHtml(c.author)}</span> ${escapeHtml(c.body)}</li>`);
  const status = escapeHtml(card.status) + (card.blocked_reason_code ? ` (${escapeHtml(card.blocked_reason_code)})` : '');
  return `
    <div class="card-sections">
      ${sectionHtml('title', 'title', escapeHtml(card.title))}
      ${sectionHtml('workstream', 'workstream', escapeHtml(card.workstream || '') || '<span class="empty">none</span>')}
      ${sectionHtml('status', 'status', status)}
      ${outcomeSectionHtml(outcome)}
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

// ---- placeholders (,  .  u  a) ------------------------------------------------------

// the two side drawers, parked off-screen with a sliver showing. built once and reused: a drawer
// rebuilt per keypress loses its open state and re-animates from parked every time
const drawers = {};

// the gap above and below a drawer, equal at both ends so it reads as pinned rather than floating
const DRAWER_INSET_PX = 50;

// what each drawer is, and what it does - the second line is the part a colleague reads
const DRAWERS = {
  left: ['agent observation deck', 'loops over all active cards or pins to selected card'],
  right: ['orchestration and planning agent', 'this is where you will spend your time'],
};

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
  });
  const [title, note] = DRAWERS[edge];
  const box = document.createElement('div');
  box.className = 'hazard-stripes hazard-placeholder';
  box.innerHTML = `<span class="hazard-label">${escapeHtml(title)}</span>
    <span class="hazard-note">${escapeHtml(note)}</span>`;
  drawer.body.appendChild(box);
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

function openPlaceholder(key, label) {
  toggleOverlay(key, () => {
    const box = document.createElement('div');
    box.className = 'hazard-stripes hazard-placeholder';
    box.innerHTML = `<span class="hazard-label">${escapeHtml(label)}</span>`;
    const menu = new Menu({
      title: label,
      sections: [{kind: 'node', node: box}],
      onDismiss: () => { if (openOverlay && openOverlay.key === key) openOverlay = null; },
    });
    menu.openAt({x: window.innerWidth / 2 - 160, y: window.innerHeight / 2 - 80});
    return menu;
  });
}

// ---- shortcut overlay, built from BINDINGS so it cannot drift -----------------------

function openShortcutOverlay() {
  toggleOverlay('KeyS', () => {
    const items = BINDINGS.map(b => ({id: b.code, label: `${b.label} - ${b.action}`, disabled: true}));
    const menu = new Menu({
      title: 'keyboard shortcuts',
      sections: [{kind: 'list', items}],
      onDismiss: () => { if (openOverlay && openOverlay.key === 'KeyS') openOverlay = null; },
    });
    menu.openAt({x: window.innerWidth / 2 - 160, y: 60});
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
  if (evt.code === 'KeyU') { openPlaceholder('KeyU', 'usage'); return; }
  if (evt.code === 'KeyA') { openPlaceholder('KeyA', 'agent roster'); return; }
  if (evt.code === 'KeyR') { runFocusedCard(); return; }
  if (evt.code === 'KeyY') { acceptOrRejectCard('accept'); return; }
  if (evt.code === 'KeyX') { acceptOrRejectCard('reject'); return; }
  if (evt.code === 'KeyS') { openShortcutOverlay(); return; }
  if (evt.code === 'Comma') { drawerFor('left').toggle(); return; }
  if (evt.code === 'Period') { drawerFor('right').toggle(); return; }
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
