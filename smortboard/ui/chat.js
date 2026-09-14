// mission control (.) and workforce (,) - the two chats, the terminal shape they share, and the
// side drawers that hold them. split out of board.js so a card-ui card can lease this file alone;
// it reaches back into board.js globals (api, escapeHtml, currentBoardId, onBoardEnter, openCard,
// focusedCardId) the same way every other split file does.

// ---- terminal chat, the shared shape behind mission control (.) and workforce (,) ---------------
// a log of author-prefixed lines plus one input row - no bubbles, no avatars, the board's own
// monospace and 2px lines. styling lives in layout.css as configuration, same rule board.js
// states at the top of its own file

// following (pinned to the newest line) is the default. scrolling up stops it - new lines then
// leave the view alone and ui_base's count badge, sitting above the input, says how many arrived.
// the pill, focusing the log and pressing down twice, or sending a message all jump back down and
// resume following. keyed by the log element so mission control and workforce track independently
const followState = new WeakMap();

function nearBottom(log) {
  // a few px of slack absorbs the sub-pixel rounding some browsers report on scrollTop
  return log.scrollHeight - log.scrollTop - log.clientHeight < 4;
}

// indicateBadge renders the number; the pill showing only while there is one to show is ours to
// guarantee regardless of what indicateBadge does internally with a host at zero
function updateBadge(state) {
  indicateBadge(state.jump, state.count);
  state.jump.hidden = state.count === 0;
}

// the one place "jump to the newest line and resume following" happens, so the pill, down-down
// and sending a message all land on identical behaviour rather than three near-duplicates
function scrollToBottom(log) {
  log.scrollTop = log.scrollHeight;
  const state = followState.get(log);
  if (!state) return;
  state.following = true;
  state.count = 0;
  updateBadge(state);
}

// a single new line arriving on top of what is already rendered (a send, a poll error) - as
// opposed to a full redraw, which settles itself in redrawLog below
function settleAfterAppend(log) {
  const state = followState.get(log);
  if (!state || state.following) { scrollToBottom(log); return; }
  state.count += 1;
  updateBadge(state);
}

// mission control and workforce both replace their whole log on every redraw instead of diffing
// it, so the pill counts the growth across the rebuild rather than once per appended line, and the
// scroll offset (which a real browser drops to 0 the moment the log empties) is put back by hand
function redrawLog(log, fill) {
  const state = followState.get(log);
  const before = state ? state.rendered || 0 : 0;
  const savedScrollTop = log.scrollTop;
  log.innerHTML = '';
  fill();
  if (!state) return;
  state.rendered = log.children.length;
  if (state.following) { scrollToBottom(log); return; }
  log.scrollTop = savedScrollTop;
  const added = Math.max(0, state.rendered - before);
  if (added) { state.count += added; updateBadge(state); }
}

function initFollow(log, jump) {
  followState.set(log, {following: true, count: 0, jump, rendered: 0});
  jump.hidden = true;
  jump.addEventListener('click', () => scrollToBottom(log));
  log.addEventListener('scroll', () => {
    const state = followState.get(log);
    if (nearBottom(log)) scrollToBottom(log);
    else state.following = false;
  });
  let downStreak = 0;
  log.addEventListener('keydown', evt => {
    if (evt.code !== 'ArrowDown') { downStreak = 0; return; }
    downStreak += 1;
    if (downStreak >= 2) { downStreak = 0; scrollToBottom(log); }
  });
}

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

  // ui_base's count badge, reused as the new-messages pill rather than a primitive of our own
  const jump = document.createElement('button');
  jump.type = 'button';
  jump.className = 'terminal-jump count-badge';
  jump.setAttribute('aria-label', 'jump to the newest line');
  initFollow(log, jump);

  const inputRow = document.createElement('div');
  inputRow.className = 'terminal-input-row';
  const prompt = document.createElement('span');
  prompt.className = 'terminal-prompt';
  prompt.textContent = promptGlyph;
  const input = document.createElement('textarea');
  input.className = 'terminal-input text-field';
  input.rows = 1;
  inputRow.append(prompt, input);

  wrap.append(header, subheader, log, jump, inputRow);
  return wrap;
}

// the name shown for the operator's own lines - /health says who; stored rows keep the key "operator"
let operatorName = 'you';
function authorLabel(author) {
  return author === 'operator' ? operatorName : author;
}

// author drives the line's colour class; cls overrides it for board/error/thinking lines whose
// author name (e.g. "orchestrator") shouldn't paint the same as an authored message would.
// a bare append with no scroll or pill side effects - a single new line settles itself with
// settleAfterAppend below, a full redraw settles once for the whole batch with redrawLog
function appendLine(log, author, body, cls) {
  const line = document.createElement('div');
  line.className = `terminal-line author-${cls || author}`;
  line.innerHTML = `<div class="terminal-author">${escapeHtml(authorLabel(author))}</div>` +
    `<div class="terminal-body">${escapeHtml(body)}</div>`;
  log.appendChild(line);
  return line;
}

// enter sends, shift+enter is left alone so the textarea's own newline behaviour handles it.
// escape leaves typing and hands focus back to the board, with the drawer still open - , and .
// work again from there. onClear (optional) lets a composer re-collapse once its own text is gone
function wireTerminalInput(input, log, onSend, onClear) {
  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') { evt.stopPropagation(); input.blur(); reenterIfFocusLost(); return; }
    if (evt.code === 'Enter' && !evt.shiftKey) {
      evt.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      input.value = '';
      onClear?.();
      onSend(text);
    }
  });
}

// mission control's composer line cap comes from the operator's global config (a window global
// the server can inject before this script loads) - unset or invalid falls back to 6
function composerMaxLines() {
  const configured = window.smortboardConfig?.composerMaxLines;
  return Number.isInteger(configured) && configured > 0 ? configured : 6;
}

// grows the composer by one row per wrapped or broken line, up to maxLinesFn(). resetting to one
// row before measuring means the loop only ever adds the rows actually needed, so there is nothing
// to snap back from after the first character - and scrollHeight still exceeding the box beyond
// the cap is exactly what leaves the textarea's own internal scrolling to take over
function growComposer(input, maxLinesFn) {
  const max = maxLinesFn();
  input.rows = 1;
  while (input.scrollHeight > input.clientHeight && input.rows < max) input.rows += 1;
}

// ---- mission control (.) - the orchestrator's chat for the current board ------------------------

const mc = {header: null, cycle: null, log: null, jump: null, input: null, poll: null, queues: new Map()};

// one send queue per board, so a reload or a board switch resumes the right backlog rather than
// mixing boards together. lazy: the first call for a board both creates the queue and, via
// createMessageQueue's own resume-on-construct, kicks off whatever a reload left queued
function mcQueueFor(boardId) {
  if (!mc.queues.has(boardId)) {
    mc.queues.set(boardId, createMessageQueue(
      `mission-control-${boardId}`,
      body => sendMissionControlMessage(boardId, body),
      {
        onChange: () => renderMissionControlQueue(boardId),
        // fires once the message is delivered AND already off the queue, so the confirmed
        // transcript replaces the queued line rather than sitting next to a duplicate of it
        onSent: body => { if (boardId === currentBoardId) renderMissionControl(body); },
      },
    ));
  }
  return mc.queues.get(boardId);
}

// the actual network call a queued message makes. rejects on a network error AND on a non-2xx
// (409 - still thinking - included) so createMessageQueue treats both as retryable rather than
// dropping the message
async function sendMissionControlMessage(boardId, text) {
  const res = await fetch(`/api/boards/${boardId}/orchestrator`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: text}),
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error((body && body.error) || `request failed (${res.status})`);
  return body;
}

// the queue's own lines: one per message not yet confirmed by the server, each tagged with its
// state so pending/sending/failed/sent are all visible rather than the message just vanishing
// while it retries. renderMissionControl already redraws the confirmed transcript from the server,
// so this only ever adds or updates lines for what that transcript does not have yet
function renderMissionControlQueue(boardId) {
  if (boardId !== currentBoardId || !mc.log) return;
  const items = mcQueueFor(boardId).items();
  const stale = mc.log.querySelectorAll('.terminal-line[data-queue-id]');
  stale.forEach(line => { if (!items.some(it => it.id === line.dataset.queueId)) line.remove(); });
  items.forEach(item => {
    let line = mc.log.querySelector(`.terminal-line[data-queue-id="${item.id}"]`);
    if (!line) {
      line = appendLine(mc.log, 'operator', item.body);
      line.dataset.queueId = item.id;
      // a queued line is a new line like any other: it follows, or it counts on the pill
      settleAfterAppend(mc.log);
    }
    line.className = `terminal-line author-operator queue-${item.state}`;
    let badge = line.querySelector('.terminal-state');
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'terminal-state';
      line.appendChild(badge);
    }
    badge.textContent = item.state;
  });
}

function buildMissionControlDom(drawer) {
  const term = terminalDom('>');
  drawer.body.appendChild(term);
  mc.header = term.querySelector('.terminal-title');
  mc.log = term.querySelector('.terminal-log');
  mc.jump = term.querySelector('.terminal-jump');
  mc.input = term.querySelector('.terminal-input');
  const resizeComposer = () => growComposer(mc.input, composerMaxLines);
  mc.input.addEventListener('input', resizeComposer);
  wireTerminalInput(mc.input, mc.log, sendMissionControl, resizeComposer);
}

async function openMissionControl() {
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
    scrollToBottom(mc.log); // a fresh panel, not a new line arriving mid-read
    return;
  }
  try {
    const data = await api(`/api/boards/${currentBoardId}/orchestrator`);
    renderMissionControl(data);
  } catch (err) {
    mc.header.textContent = 'mission control';
    mc.log.innerHTML = '';
    appendLine(mc.log, 'board', `could not reach the orchestrator: ${err.message}`, 'error');
    scrollToBottom(mc.log);
  }
}

// justFinished marks a poll result, the only moment a newly-created card should pull the board
function renderMissionControl(data, {justFinished = false} = {}) {
  mc.header.textContent = `mission control - ${data.model || '?'}`;
  redrawLog(mc.log, () => {
    (data.messages || []).forEach(m => {
      appendLine(mc.log, m.author, m.body);
      if (m.cards && m.cards.length) {
        appendLine(mc.log, 'board', `created: ${m.cards.map(c => c.title).join(', ')}`, 'board');
      }
    });
    if (data.error) appendLine(mc.log, 'board', data.error, 'error');
    if (data.thinking) appendLine(mc.log, 'orchestrator', 'orchestrator is thinking', 'thinking');
  });
  // the redraw just wiped any queued-but-unconfirmed lines too - put back whatever this
  // board's queue still has that the server transcript does not
  if (currentBoardId) renderMissionControlQueue(currentBoardId);
  if (data.thinking) {
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
    settleAfterAppend(mc.log);
  }
}

// hands the message to this board's send queue and returns immediately - the queue writes it to
// localStorage before attempting anything, so a failed request (or a closed tab) never loses it.
// the queue's onChange callback (renderMissionControlQueue) is what actually draws the line
function sendMissionControl(text) {
  if (!currentBoardId) { appendLine(mc.log, 'board', 'no board selected', 'error'); settleAfterAppend(mc.log); return; }
  mcQueueFor(currentBoardId).enqueue(text);
  scrollToBottom(mc.log); // sending always jumps to the newest line and resumes following
}

// ---- workforce (,) - one card's agent chat -------------------------------------------------------

const wf = {header: null, cycle: null, count: null, subheader: null, log: null, jump: null, input: null,
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
  wf.jump = term.querySelector('.terminal-jump');
  wf.input = term.querySelector('.terminal-input');
  wireTerminalInput(wf.input, wf.log, sendWorkforce);
  wf.prev = term.querySelector('.terminal-prev');
  wf.next = term.querySelector('.terminal-next');
  wf.prev.onclick = () => cycleWorkforce(-1);
  wf.next.onclick = () => cycleWorkforce(1);
}

async function openWorkforce() {
  // OPENING LEAVES FOCUS ON THE BOARD, so , closes what , opened and the focused card stays the
  // target. / is the way into the input
  await loadWorkforce(await resolveWorkforceTarget());
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
    scrollToBottom(wf.log); // a fresh panel, not a new line arriving mid-read
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
    redrawLog(wf.log, () => (data.messages || []).forEach(m => appendLine(wf.log, m.author, m.body)));
    if (data.running && drawers.left && drawers.left.isOpen()) {
      wf.poll = setTimeout(renderWorkforceConversation, 2000);
    }
  } catch (err) {
    wf.header.textContent = 'workforce';
    appendLine(wf.log, 'board', `could not load conversation: ${err.message}`, 'error');
    settleAfterAppend(wf.log);
  }
}

async function sendWorkforce(text) {
  if (!wf.cardId) return;
  appendLine(wf.log, 'operator', text);
  scrollToBottom(wf.log); // sending always jumps to the newest line and resumes following
  try {
    const res = await fetch(`/api/cards/${wf.cardId}/conversation`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: text}),
    });
    const body = await res.json().catch(() => null);
    if (!res.ok) {
      appendLine(wf.log, 'board', (body && body.error) || `request failed (${res.status})`, 'error');
      settleAfterAppend(wf.log);
      return;
    }
    if (body && body.delivery) {
      wf.subheader.hidden = false;
      wf.subheader.textContent = DELIVERY_LABEL[body.delivery] || DELIVERY_LABEL.next_run;
    }
  } catch (err) {
    appendLine(wf.log, 'board', `could not reach the agent: ${err.message}`, 'error');
    settleAfterAppend(wf.log);
  }
}

// ---- the two side drawers -------------------------------------------------------------------

// the two side drawers, parked off-screen with a sliver showing. built once and reused: a drawer
// rebuilt per keypress loses its open state and re-animates from parked every time
const drawers = {};

// the gap above and below a drawer, equal at both ends so it reads as pinned rather than floating
const DRAWER_INSET_PX = 50;

// / picks the chat of the drawer opened last when both are open
let lastDrawerEdge = null;

// / IS THE ONE WAY INTO TYPING: the open card's comment first, else the chat of an open drawer.
// opening a card or a drawer never focuses an input, so every letter stays a board key until /
function focusTypingTarget() {
  if (openCard?.input) { openCard.input.focus(); return; }
  const edges = [lastDrawerEdge, lastDrawerEdge === 'right' ? 'left' : 'right'];
  const edge = edges.find(e => e && drawers[e]?.isOpen());
  if (edge) (edge === 'right' ? mc.input : wf.input).focus();
}

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
    onOpen: () => {
      lastDrawerEdge = edge;
      return edge === 'right' ? openMissionControl() : openWorkforce();
    },
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
