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
  {code: 'KeyR', label: 'r', action: 'run the focused card', group: 'cards'},
  {code: 'KeyK', label: 'k', action: 'stop the focused card if it is running', group: 'cards'},
  {code: 'KeyY', label: 'y', action: 'accept the focused card', group: 'cards'},
  {code: 'KeyX', label: 'x', action: 'reject the focused card', group: 'cards'},
  {code: 'KeyM', label: 'm', action: "cycle the card's model", group: 'cards'},
  {code: 'KeyE', label: 'e', action: 'edit: open the focused card', group: 'cards'},
  {code: 'KeyJ', label: 'j', action: 'move the focused card to another status', group: 'cards'},
  {code: 'Delete', label: 'del', action: 'delete the focused card, with confirmation', group: 'cards'},
  {code: 'KeyT', label: 't', action: "run replay: scrub the focused card's run step by step", group: 'cards'},
  {code: 'Slash', label: '/', action: "type: the open card's comment, or the open chat", group: 'cards'},
  {code: 'KeyG', label: 'g', action: 'toggle kanban / workstream grouping', group: 'cards'},
  {code: 'KeyW', label: 'w', action: 'run the board: start / stop the queue', group: 'cards'},
  {code: 'KeyF', label: 'f', action: 'fold: merge the todo cards one agent should do as one (asks first)', group: 'cards'},
  {code: 'KeyU', label: 'u', action: 'usage: rate-limit windows and per-model spend', group: 'panels'},
  {code: 'KeyI', label: 'i', action: 'cost telemetry: card attempts, or the board cost table', group: 'panels'},
  {code: 'KeyC', label: 'c', action: 'cost overview: spend across every board', group: 'panels'},
  {code: 'KeyA', label: 'a', action: 'agent roster: jump to a card an agent is working on', group: 'panels'},
  {code: 'KeyD', label: 'd', action: 'morning digest: pull requests and open questions', group: 'panels'},
  {code: 'KeyS', label: 's', action: 'this shortcut overlay', group: 'panels'},
  // one binding row for both: the letter acts on a card elsewhere in this table, shift on the
  // board - see keyboard_bindings.mjs's frozen count of codes, which a second KeyP row would break
  {code: 'KeyP', label: 'p / shift+p', action: 'p: orchestrator, worker and reviewer prompts. shift+p: credential profiles', group: 'panels'},
  {code: 'KeyB', label: 'b', action: 'boards and repos: create a board, register a repo', group: 'panels'},
  {code: 'KeyN', label: 'n', action: 'attention inbox: answer a blocked card, across every board', group: 'panels'},
  {code: 'KeyH', label: 'h', action: 'pre-flight checklist: what is missing before a card can run', group: 'panels'},
  {code: 'KeyO', label: 'o', action: 'settings: mission control preferences', group: 'panels'},
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
  const [group, label] = BINDING_GROUPS[index];
  return {kind: 'list', label, items: overlayRows(BINDINGS.filter(b => b.group === group))};
}

function openShortcutOverlay() {
  toggleOverlay('KeyS', () => {
    let page = 0;
    const menu = new Menu({
      title: 'keyboard shortcuts',
      sections: [shortcutPageSection(page)],
      onDismiss: () => { if (openOverlay && openOverlay.key === 'KeyS') openOverlay = null; },
    });
    menu.openAt({x: Math.max(16, window.innerWidth / 2 - 280), y: 60});
    menu.el?.classList.add('shortcut-overlay', 'menu-centered');
    // left/right move here; wrapping means either direction reaches every page
    menu.turnPage = dir => {
      page = (page + dir + BINDING_GROUPS.length) % BINDING_GROUPS.length;
      menu.refresh([shortcutPageSection(page)]);
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
  if (card) { card.focus(); indicateFocus(card); return true; }
  returnToBoardBar();
  return true;
}

// a held cmd, ctrl or alt belongs to the browser and the os - copy, paste, reload. without this,
// cmd+c opened the cost panel and cmd+r ran the focused card
function withModifier(evt) {
  return evt.metaKey || evt.ctrlKey || evt.altKey;
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
  if (evt.code === 'KeyF') { openFoldConfirm(); return; }
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
  if (evt.code === 'KeyE') { editCard(); return; }
  if (evt.code === 'KeyJ') { openMoveStatusMenu(); return; }
  if (evt.code === 'Delete') { deleteCard(); return; }
  if (evt.code === 'KeyT') { toggleReplay(); return; }
  if (evt.code === 'KeyS') { openShortcutOverlay(); return; }
  if (evt.code === 'KeyP' && evt.shiftKey) { evt.preventDefault(); toggleProfilesPanel(); return; }
  if (evt.code === 'KeyP') { togglePromptEditor(); return; }
  if (evt.code === 'KeyN') { toggleInboxPanel(); return; }
  if (evt.code === 'KeyH') { evt.preventDefault(); togglePreflightPanel(); return; }
  if (evt.code === 'KeyO') { evt.preventDefault(); toggleSettingsPanel(); return; }
  // preventDefault: the panel focuses its first input, and the key that opened it typed itself there
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
