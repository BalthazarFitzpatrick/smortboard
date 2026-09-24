// keyboard shortcuts: the bindings table, the overlay built from it, and the global keydown
// handler that dispatches every one of them. split out of board.js so a card-ui card can lease
// this file alone; it reaches back into board.js and the other split files' globals (openCard,
// boards, grouped, drawerFor, runFocusedCard, cycleCardModel, and so on) the same way every other
// split file does.

// the binding table IS the shortcut overlay's source and the handler dispatch's source, so the
// two cannot drift apart - see openShortcutOverlay and the keydown handler below
// ORDERED BY GROUP: the overlay pages through one group at a time in this order, so a binding's
// group alone decides which page it lands on - no second list to keep in sync
const BINDINGS = [
  {code: 'ArrowUp', label: 'up', action: 'move focus up / exit to board bar', group: 'cards'},
  {code: 'ArrowDown', label: 'down', action: 'move focus down', group: 'cards'},
  {code: 'ArrowLeft', label: 'left', action: 'move focus left', group: 'cards'},
  {code: 'ArrowRight', label: 'right', action: 'move focus right', group: 'cards'},
  {code: 'Enter', label: 'enter', action: 'open the focused card', group: 'cards'},
  {code: 'Space', label: 'space', action: 'open the focused card, or close the open one', group: 'cards'},
  {code: 'Escape', label: 'esc', action: 'one level back: input -> panel -> closed', group: 'cards'},
  {code: 'KeyR', label: 'r', action: 'run the focused card, with confirmation', group: 'cards'},
  {code: 'KeyK', label: 'k', action: 'stop the focused card if it is running', group: 'cards'},
  {code: 'KeyY', label: 'y', action: 'accept the focused card, with confirmation', group: 'cards'},
  {code: 'KeyX', label: 'x', action: 'reject the focused card, with confirmation', group: 'cards'},
  // ONE MENU INSTEAD OF FOUR KEYS: edit (e), move status (j), delete (del) and cycling the model
  // were each their own binding - they are the rows of this menu now, reachable in two presses
  {code: 'KeyM', label: 'm', action: "menu for the focused card: edit, model, complexity, move to, delete", group: 'cards'},
  {code: 'KeyT', label: 't', action: "run replay: scrub the focused card's run step by step", group: 'cards'},
  {code: 'Slash', label: '/', action: "type: the open card's comment, or the open chat", group: 'cards'},
  {code: 'KeyG', label: 'g', action: 'toggle kanban / workstream grouping', group: 'cards'},
  {code: 'KeyW', label: 'w', action: 'run the board: start (with confirmation) / stop the queue', group: 'cards'},
  {code: 'KeyF', label: 'f', action: 'fold: merge the todo cards one agent should do as one (asks first)', group: 'cards'},
  {code: 'KeyU', label: 'u', action: 'usage: rate-limit windows and per-model spend', group: 'panels'},
  {code: 'KeyI', label: 'i', action: 'cost telemetry: card attempts, or the board cost table', group: 'panels'},
  {code: 'KeyC', label: 'c', action: 'cost overview: spend across every board', group: 'panels'},
  {code: 'KeyA', label: 'a / shift+a', action: 'agent roster / change board merge mode, with confirmation', group: 'panels'},
  {code: 'KeyD', label: 'd', action: 'morning digest: pull requests and open questions', group: 'panels'},
  {code: 'KeyS', label: 's', action: 'this shortcut overlay', group: 'panels'},
  // one binding row for both: the letter acts on a card elsewhere in this table, shift on the
  // board - see keyboard_bindings.mjs's frozen count of codes, which a second KeyP row would break
  {code: 'KeyP', label: 'p / shift+p', action: 'p: orchestrator, worker and reviewer prompts. shift+p: credential profiles', group: 'panels'},
  {code: 'KeyB', label: 'b', action: 'boards and repos: create a board, register a repo', group: 'panels'},
  {code: 'KeyN', label: 'n', action: 'attention inbox: answer a blocked card, across every board', group: 'panels'},
  {code: 'KeyH', label: 'h', action: 'pre-flight checklist: what is missing before a card can run', group: 'panels'},
  {code: 'KeyO', label: 'o', action: 'settings: mission control preferences', group: 'panels'},
  {code: 'KeyV', label: 'v', action: 'pull requests: every open one across every board, in merge order', group: 'panels'},
  {code: 'KeyQ', label: 'q', action: 'landing lock: who holds the push lock on each repo, and the queue behind them', group: 'panels'},
  {code: 'Comma', label: ',', action: 'workforce: chat with the focused card\'s agent', group: 'panels'},
  {code: 'Period', label: '.', action: 'mission control: chat with the board orchestrator', group: 'panels'},
  ...Array.from({length: 9}, (_, i) => ({
    code: `Digit${i + 1}`, label: String(i + 1), action: `jump to board ${i + 1}`, group: 'panels',
  })),
];

// the overlay's pages, in the order left/right cycle through them
const BINDING_GROUPS = [['cards', 'cards and the board'], ['panels', 'panels and boards']];

// ---- shortcut overlay, built from BINDINGS so it cannot drift -----------------------

// ONE PAGE PER BINDING GROUP - left/right flips between them, so a new BINDINGS group needs no
// new overlay code, and a new binding lands on whichever page its group already renders.
// THE NINE BOARD KEYS ARE ONE ROW here: listed one by one they ran the page off the screen.
// BINDINGS keeps all nine, since the keyboard handler looks each one up
function overlayRows(bindings) {
  return bindings
    .filter(b => !/^Digit[2-9]$/.test(b.code))
    .map(b => (b.code === 'Digit1'
      ? {id: 'boards', label: '1 .. 9 - jump to board 1 .. 9', disabled: true}
      : {id: b.code, label: `${b.label} - ${b.action}`, disabled: true}));
}

// the single section for one page - built fresh from BINDINGS each time, never cached, so paging
// can never show a group's stale copy
function shortcutPageSection(index) {
  const [group] = BINDING_GROUPS[index];
  return {kind: 'list', items: overlayRows(BINDINGS.filter(b => b.group === group))};
}

// the page header: the same arrows-and-label row the attention inbox uses, so paging is visible
// and clickable rather than a key you have to know about
function shortcutPagerSection(index, turnPage) {
  const header = document.createElement('div');
  header.className = 'pager-header';
  const prev = document.createElement('span');
  prev.className = 'pager-nav toggle';
  prev.textContent = '←';
  prev.onclick = () => turnPage(-1);
  const label = document.createElement('span');
  label.className = 'pager-label';
  label.textContent = `${BINDING_GROUPS[index][1]} (${index + 1}/${BINDING_GROUPS.length})`;
  const next = document.createElement('span');
  next.className = 'pager-nav toggle';
  next.textContent = '→';
  next.onclick = () => turnPage(1);
  header.append(prev, label, next);
  return {kind: 'node', node: header};
}

function openShortcutOverlay() {
  toggleOverlay('KeyS', () => {
    let page = 0;
    const turnPage = dir => menu.turnPage(dir);
    const menu = new Menu({
      title: 'keyboard shortcuts',
      sections: [shortcutPagerSection(page, turnPage), shortcutPageSection(page)],
      onDismiss: () => { if (openOverlay && openOverlay.key === 'KeyS') openOverlay = null; },
    });
    menu.openAt({x: Math.max(16, window.innerWidth / 2 - 280), y: 60});
    menu.el?.classList.add('shortcut-overlay', 'menu-centered');
    // left/right move here; wrapping means either direction reaches every page
    menu.turnPage = dir => {
      page = (page + dir + BINDING_GROUPS.length) % BINDING_GROUPS.length;
      menu.refresh([shortcutPagerSection(page, turnPage), shortcutPageSection(page)]);
    };
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
  if (card) { card.focus(); indicateCardFocus(card); return true; }
  returnToBoardBar();
  return true;
}

// a held cmd, ctrl or alt belongs to the browser and the os - copy, paste, reload. without this,
// cmd+c opened the cost panel and cmd+r ran the focused card
function withModifier(evt) {
  return evt.metaKey || evt.ctrlKey || evt.altKey;
}

// ---- which surface owns the keyboard ------------------------------------------------

// A PANEL OR MENU STANDING OVER THE BOARD OWNS EVERY CARD KEY. the open card's own panel is not
// one of these - space still closes the card it opened
function surfaceOverBoard() {
  const overlays = Array.from(document.querySelectorAll('.modal-backdrop')).filter(
    el => !el.classList.contains('expand-backdrop') && !el.classList.contains('expand-closing'));
  return overlays.length > 0 || !!document.querySelector('.menu-panel');
}

// the keys that act on a card strip - dead while a surface stands over the board, which is what
// stopped space and enter opening the card behind an open panel
const CARD_ACTION_KEYS = new Set(['Space', 'Enter', 'NumpadEnter', 'KeyR', 'KeyK', 'KeyY', 'KeyX',
  'KeyM', 'KeyT', 'KeyG', 'KeyW', 'KeyF']);

// the panel a key is aimed at: the topmost open one, menus included
// plain selectors, one query each: a :not() or a comma is more than the node dom stub the tests
// run against can parse, and a selector that silently matches nothing would make them prove nothing
function topSurface() {
  const panels = Array.from(document.querySelectorAll('.modal-backdrop .panel-floating'))
    .filter(el => !el.closest('.expand-closing'))
    .concat(Array.from(document.querySelectorAll('.menu-panel')));
  return panels[panels.length - 1] || null;
}

// what / and the cursor may land on inside a surface - a checkbox or a slider is not typed into.
// two queries rather than one comma selector, for the same reason topSurface takes the long way:
// the node dom stub the tests run against parses neither, and would match nothing in silence.
// inputs first, then textareas - no panel today holds both, so that is still reading order
function surfaceFields(surface) {
  return [...surface.querySelectorAll('input'), ...surface.querySelectorAll('textarea')]
    .filter(el => !el.disabled && el.type !== 'checkbox' && el.type !== 'range');
}

// a panel takes the keyboard when it opens - without this the board keeps focus behind it and
// space opened the card underneath
function focusPanel(panel) {
  if (!panel) return;
  panel.tabIndex = -1;
  panel.focus();
}

// ---- the pointer sets the same focus the keyboard does -------------------------------------

// THE MOUSE IS OPTIONAL AND OFF BY DEFAULT. the board is meant to be driven from the keyboard;
// enable_mouse (settings, o) turns on the added pointer affordances - hover focusing what the
// arrow keys would, and right-click opening the card's menu. it never gates the clicks that
// always worked: opening a card, the overflow button, a menu row, every button in every panel
let mouseEnabled = false;

function setMouseEnabled(on) {
  mouseEnabled = on === true || on === 'on';
}

function mouseAffordances() {
  return mouseEnabled;
}

// read at startup (board.js's bootstrap calls this, once api() exists) and written again by the
// toggle itself, so switching it takes effect with no reload
async function loadMouseSetting() {
  try {
    const settings = await api('/api/settings');
    setMouseEnabled(settings.enable_mouse);
  } catch (err) {
    setMouseEnabled(false); // a failed read keeps the keyboard-only default
  }
}
// ONE STATE, NOT TWO. hovering focuses the thing under the pointer through the very same call the
// arrow keys use, so the focus ring, the lift and a piled column fanning cannot drift between the
// two input methods - and the column layout stays whatever the last focus made it.

// where a hover may land: inside the surface that owns the keyboard, or on the board when nothing
// stands over it. a hover never steals the caret out of a field someone is typing in
function hoverAllowed(el) {
  const active = document.activeElement;
  if (active && active.matches?.('input, textarea')) return false;
  const surface = topSurface();
  if (surface) return surface.contains(el);
  return true;
}

// a sweep across a dense column must not redraw it once per card it passes over: the focus lands
// where the pointer SETTLES. already-focused is not a move, so nothing redraws and no card is
// drawn out of its pile twice
const HOVER_SETTLE_MS = 60;
let hoverTimer = null;

function hoverFocus(el, focus) {
  if (!mouseAffordances()) return;
  if (el === document.activeElement) return;
  clearTimeout(hoverTimer);
  hoverTimer = setTimeout(() => {
    if (!el.parentNode || el === document.activeElement || !hoverAllowed(el)) return;
    focus();
  }, HOVER_SETTLE_MS);
  hoverTimer?.unref?.();
}

// A MENU CLOSING HANDS THE KEYBOARD BACK to whatever opened it. Menu drops focus wherever it was
// - on <body>, where every key is dead until the next press recovers the board by guessing
function returnFocusOnDismiss(menu, returnTo) {
  if (!menu) return menu;
  const dismiss = menu.onDismiss;
  // ONCE ONLY. picking a row closes the menu twice over - the handler's own close(), then Menu's
  // own close-after-pick - and handing focus back the second time snatched it from whatever the
  // pick had just opened (the delete confirm lost its focused button that way)
  let handed = false;
  menu.onDismiss = () => {
    if (handed) return;
    handed = true;
    dismiss?.();
    // a pick that opened another menu (delete asks first) has handed the keyboard to THAT menu -
    // taking it back here left the new one standing with no button focused
    if (document.querySelector('.menu-panel')) return;
    if (returnTo && returnTo !== document.body && returnTo.parentNode) {
      returnTo.focus?.();
      if (returnTo.classList?.contains?.('card-strip')) indicateCardFocus(returnTo);
      return;
    }
    reenterIfFocusLost();
  };
  return menu;
}

// ONE LEVEL, NOT TWO. escape in a panel's field steps out to the panel; the panel's own escape
// handler takes the second press. a field that saves on blur still saves on the way out
function stepOutOfField(field) {
  const panel = field.closest?.('.panel-floating');
  if (!panel) { field.blur?.(); return; }
  focusPanel(panel);
}

// up/down step between a panel's fields - the cursor is how the model reaches one of settings'
// many fields, since / never guesses between them. one field (or none) is left alone
function movePanelField(evt) {
  const surface = topSurface();
  if (!surface) return false;
  const fields = surfaceFields(surface);
  if (fields.length < 2) return false;
  const at = fields.indexOf(document.activeElement);
  const dir = evt.code === 'ArrowDown' ? 1 : -1;
  const next = fields[at === -1 ? 0 : Math.min(fields.length - 1, Math.max(0, at + dir))];
  if (!next || next === document.activeElement) return false;
  evt.preventDefault();
  next.focus();
  return true;
}

document.addEventListener('keydown', evt => {
  if (withModifier(evt)) return;
  // the fold question owns y and n while it is open - y is otherwise accept, and accepting the
  // focused card while answering "fold?" would be the worst possible misread
  if (openOverlay && openOverlay.key === 'KeyF' && (evt.code === 'KeyY' || evt.code === 'KeyN')) {
    evt.preventDefault();
    answerFold(evt.code === 'KeyY');
    return;
  }
  // the shortcut overlay owns left/right while it is open, ahead of the focus-recovery below -
  // otherwise a lost-focus reentry would eat the very same arrow press as a card move
  if (openOverlay && openOverlay.key === 'KeyS' && (evt.code === 'ArrowLeft' || evt.code === 'ArrowRight')) {
    evt.preventDefault();
    openOverlay.menu.turnPage(evt.code === 'ArrowRight' ? 1 : -1);
    return;
  }
  // up/down inside a panel are the panel's own: they step between its fields rather than moving
  // the card focus sitting behind it
  if ((evt.code === 'ArrowDown' || evt.code === 'ArrowUp') && surfaceOverBoard()
      && !evt.target.matches?.('textarea') && movePanelField(evt)) return;
  // re-entry hands focus to a card, which is the wrong place while a panel stands over the board
  const recovered = surfaceOverBoard() ? false : reenterIfFocusLost();
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

  // the surface over the board owns these: space and enter used to open the card behind an open
  // panel, and r/y/x/del still acted on it
  if (surfaceOverBoard() && CARD_ACTION_KEYS.has(evt.code)) return;

  // THE KEY THAT OPENS ALSO CLOSES, next to escape - and in the comment input, which returned
  // above, it stays a space
  if (evt.code === 'Space' && openCard) { evt.preventDefault(); openCard.expander.close(); return; }

  if (evt.code === 'KeyG') { grouped = !grouped; return; }
  if (evt.code === 'KeyW') { toggleRunAll(); return; }
  if (evt.code === 'KeyF') { openFoldConfirm(); return; }
  if (evt.code === 'KeyU') { openUsagePanel(); return; }
  if (evt.code === 'KeyI') { openTelemetryPanel(); return; }
  if (evt.code === 'KeyC') { openCostsOverviewPanel(); return; }
  if (evt.code === 'KeyA' && evt.shiftKey) { evt.preventDefault(); toggleBoardMergeMode(); return; }
  if (evt.code === 'KeyA') { openRosterPanel(); return; }
  if (evt.code === 'KeyD') { openDigestPanel(); return; }
  if (evt.code === 'KeyR') { runFocusedCard(); return; }
  if (evt.code === 'KeyK') { stopFocusedCard(); return; }
  if (evt.code === 'KeyY') { acceptOrRejectCard('accept'); return; }
  if (evt.code === 'KeyX') { acceptOrRejectCard('reject'); return; }
  if (evt.code === 'KeyM') { openCardActionsMenu(); return; }
  if (evt.code === 'KeyT') { toggleReplay(); return; }
  if (evt.code === 'KeyS') { openShortcutOverlay(); return; }
  if (evt.code === 'KeyP' && evt.shiftKey) { evt.preventDefault(); toggleProfilesPanel(); return; }
  if (evt.code === 'KeyP') { togglePromptEditor(); return; }
  if (evt.code === 'KeyN') { toggleInboxPanel(); return; }
  if (evt.code === 'KeyH') { evt.preventDefault(); togglePreflightPanel(); return; }
  if (evt.code === 'KeyO') { evt.preventDefault(); toggleSettingsPanel(); return; }
  if (evt.code === 'KeyV') { evt.preventDefault(); togglePullsPanel(); return; }
  if (evt.code === 'KeyQ') { evt.preventDefault(); toggleLandingPanel(); return; }
  // preventDefault, so the key that opened the panel never types itself into one of its fields
  if (evt.code === 'KeyB') { evt.preventDefault(); toggleBoardsPanel(); return; }
  // opening a drawer leaves focus on the board, so the key that opened it also closes it
  if (evt.code === 'Comma') { evt.preventDefault(); drawerFor('left').toggle(); return; }
  if (evt.code === 'Period') { evt.preventDefault(); drawerFor('right').toggle(); return; }
  // preventDefault: the / would otherwise type itself into the input it focuses
  if (evt.code === 'Slash') { evt.preventDefault(); focusTypingTarget(); return; }
  if (binding.code.startsWith('Digit')) {
    const index = Number(binding.label) - 1;
    const board = boards[index];
    if (board) activateTab(board.id);
  }
});

// ---- cmd/ctrl+f: the card filter, not the browser's find ----------------------------------------
// its own listener, since the one above hands every held modifier to the browser. only while a board
// is on screen with nothing over it - an open card, panel or menu keeps the browser's own find
document.addEventListener('keydown', evt => {
  if (evt.code !== 'KeyF' || !(evt.metaKey || evt.ctrlKey) || evt.altKey || evt.shiftKey) return;
  if (!currentBoardId || openCard || surfaceOverBoard()) return;
  if (evt.target?.matches?.('input, textarea') && evt.target.id !== 'card-filter') return;
  evt.preventDefault();
  focusCardFilter();
});
