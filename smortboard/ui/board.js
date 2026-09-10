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
  {code: 'Space', label: 'space', action: 'open the focused card'},
  {code: 'Escape', label: 'esc', action: 'one level back: input -> panel -> closed'},
  {code: 'KeyG', label: 'g', action: 'toggle kanban / workstream grouping'},
  {code: 'KeyU', label: 'u', action: 'usage dropdown (placeholder)'},
  {code: 'KeyA', label: 'a', action: 'agent roster (placeholder)'},
  {code: 'KeyR', label: 'r', action: 'run the focused card'},
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
let openCard = null; // {expander, sectionsApi} while a card panel is open

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.status === 204 ? null : res.json();
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
    // THE PLATE BECOMES A BORDER ON THE WAY OPEN. a panel painted with the plate would make a whole
    // screen of it, and the colour stops being a signal once it is the background you are reading
    // on - as an edge it survives the expansion without taking the panel over
    onOpen: panel => {
      cardClasses(card).split(' ')
        .filter(c => c.startsWith('card-') && c !== 'card-strip')
        .forEach(c => panel.classList.add(c));
      openCardPanel(panel, card.id);
    },
    onClose: () => { openCard = null; },
  });
  strip.addEventListener('keydown', evt => {
    if (evt.code === 'Enter' || evt.code === 'NumpadEnter' || evt.code === 'Space') {
      evt.preventDefault();
      expander.open();
    }
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
  const card = await api(`/api/cards/${cardId}`);
  panel.classList.add('card-panel');
  panel.innerHTML = cardPanelHtml(card);

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
        body: JSON.stringify({author: 'operator', body}),
      }).then(() => openCardPanel(panel, cardId));
    }
  });

  openCard = {sectionsApi, input};
}

function cardPanelHtml(card) {
  const tasks = (card.tasks || []).map(t => `<li>${t.done ? '[x]' : '[ ]'} ${escapeHtml(t.text)}</li>`).join('');
  const criteria = (card.criteria || []).map(c => `<li>${escapeHtml(c.text)}</li>`).join('');
  const deps = (card.deps || []).map(d => `<li>${escapeHtml(d)}</li>`).join('');
  const attachments = (card.attachments || []).map(a => `<li>${escapeHtml(a.filename)}</li>`).join('');
  const comments = (card.comments || [])
    .map(c => `<li><span class="field-label">${escapeHtml(c.author)}</span> ${escapeHtml(c.body)}</li>`).join('');
  return `
    <div class="card-sections">
      <div class="card-section" tabindex="0"><div class="field-label">title</div>${escapeHtml(card.title)}</div>
      <div class="card-section" tabindex="0"><div class="field-label">workstream</div>${escapeHtml(card.workstream || '')}</div>
      <div class="card-section" tabindex="0"><div class="field-label">status</div>${escapeHtml(card.status)}${card.blocked_reason_code ? ` (${escapeHtml(card.blocked_reason_code)})` : ''}</div>
      <div class="card-section" tabindex="0"><div class="field-label">description</div>${escapeHtml(card.description || '')}</div>
      <div class="card-section" tabindex="0"><div class="field-label">tasks</div><ul>${tasks}</ul></div>
      <div class="card-section" tabindex="0"><div class="field-label">acceptance criteria</div><ul>${criteria}</ul></div>
      <div class="card-section" tabindex="0"><div class="field-label">dependencies</div><ul>${deps}</ul></div>
      <div class="card-section" tabindex="0"><div class="field-label">attachments</div><ul>${attachments}</ul></div>
      <div class="card-section" tabindex="0"><div class="field-label">comments</div><ul>${comments}</ul>
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

  if (evt.code === 'KeyG') { grouped = !grouped; return; }
  if (evt.code === 'KeyU') { openPlaceholder('KeyU', 'usage'); return; }
  if (evt.code === 'KeyA') { openPlaceholder('KeyA', 'agent roster'); return; }
  if (evt.code === 'KeyR') { runFocusedCard(); return; }
  if (evt.code === 'KeyS') { openShortcutOverlay(); return; }
  if (evt.code === 'Comma') { drawerFor('left').toggle(); return; }
  if (evt.code === 'Period') { drawerFor('right').toggle(); return; }
  if (evt.code === 'Slash' && openCard) { evt.preventDefault(); openCard.input.focus(); return; }
  if (binding.code.startsWith('Digit')) {
    const index = Number(binding.label) - 1;
    const board = boards[index];
    if (board) activateTab(board.id);
  }
});

// FOCUS STARTS ON THE BOARD BAR, per the brief. returnToBoardBar was wired only to onExitTop, so
// nothing ever focused on load and every key was dead until the user clicked - which no test saw
loadBoards().then(() => { buildDrawers(); returnToBoardBar(); });
