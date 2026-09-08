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
  {code: 'KeyS', label: 's', action: 'this shortcut overlay'},
  {code: 'Slash', label: '/', action: "focus the open card's comment input"},
  {code: 'Comma', label: ',', action: 'workforce panel (placeholder)'},
  {code: 'Period', label: '.', action: 'mission control panel (placeholder)'},
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
  initShell({onEnter: onBoardEnter, fallback: boards[0]?.id || ''});
}

async function onBoardEnter(boardId) {
  currentBoardId = boardId;
  const cards = await api(`/api/boards/${boardId}/cards`);
  renderBuckets(cards);
}

// ---- buckets of card strips -------------------------------------------------------

function cardClasses(card) {
  const classes = ['row', 'card-strip'];
  // blocked wins over working: a card waiting on you is not a card making progress, and showing
  // both reads as progress
  if (card.blocked_reason_code) classes.push('card-attention');
  else if (card.status === 'doing') classes.push('card-working');
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
}

function renderCardStrip(card) {
  const strip = document.createElement('div');
  strip.className = cardClasses(card);
  strip.tabIndex = -1;
  strip.dataset.cardId = card.id;
  strip.innerHTML = `
    <div class="card-title">${escapeHtml(card.title)}</div>
    <div class="card-workstream">${escapeHtml(card.workstream || '')}</div>
  `;
  const expander = makeExpander(strip, {
    onOpen: panel => openCardPanel(panel, card.id),
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
  document.querySelector('.board-bar .nav-tab.active')?.focus();
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
    if (evt.code === 'Escape') { evt.stopPropagation(); input.blur(); }
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

// ---- placeholders (,  .  u  a) ------------------------------------------------------

// the two side drawers, parked off-screen with a sliver showing. built once and reused: a drawer
// rebuilt per keypress loses its open state and re-animates from parked every time
const drawers = {};

function drawerFor(edge, label) {
  if (drawers[edge]) return drawers[edge];
  const bar = document.querySelector('.board-bar');
  const drawer = makeDrawer({
    edge,
    sliverRatio: 0.125,
    heightRatio: 0.625,
    top: bar ? Math.round(bar.getBoundingClientRect().bottom) : 0,
  });
  const box = document.createElement('div');
  box.className = 'hazard-stripes hazard-placeholder';
  box.innerHTML = `<span class="hazard-label">${escapeHtml(label)}</span>`;
  drawer.body.appendChild(box);
  drawers[edge] = drawer;
  return drawer;
}

function openPlaceholder(label) {
  const box = document.createElement('div');
  box.className = 'hazard-stripes hazard-placeholder';
  box.innerHTML = `<span class="hazard-label">${escapeHtml(label)}</span>`;
  new Menu({title: label, sections: [{kind: 'node', node: box}]})
    .openAt({x: window.innerWidth / 2 - 160, y: window.innerHeight / 2 - 80});
}

// ---- shortcut overlay, built from BINDINGS so it cannot drift -----------------------

function openShortcutOverlay() {
  const items = BINDINGS.map(b => ({id: b.code, label: `${b.label} - ${b.action}`, disabled: true}));
  new Menu({title: 'keyboard shortcuts', sections: [{kind: 'list', items}]})
    .openAt({x: window.innerWidth / 2 - 160, y: 60});
}

// ---- global keyboard model, entirely on event.code ----------------------------------

document.addEventListener('keydown', evt => {
  const typing = evt.target.matches?.('input, textarea');
  const binding = BINDINGS.find(b => b.code === evt.code);
  if (!binding) return;

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
  if (evt.code === 'KeyU') { openPlaceholder('usage'); return; }
  if (evt.code === 'KeyA') { openPlaceholder('agent roster'); return; }
  if (evt.code === 'KeyS') { openShortcutOverlay(); return; }
  if (evt.code === 'Comma') { drawerFor('left', 'workforce').toggle(); return; }
  if (evt.code === 'Period') { drawerFor('right', 'mission control').toggle(); return; }
  if (evt.code === 'Slash' && openCard) { evt.preventDefault(); openCard.input.focus(); return; }
  if (binding.code.startsWith('Digit')) {
    const index = Number(binding.label) - 1;
    const board = boards[index];
    if (board) activateTab(board.id);
  }
});

// FOCUS STARTS ON THE BOARD BAR, per the brief. returnToBoardBar was wired only to onExitTop, so
// nothing ever focused on load and every key was dead until the user clicked - which no test saw
loadBoards().then(returnToBoardBar);
