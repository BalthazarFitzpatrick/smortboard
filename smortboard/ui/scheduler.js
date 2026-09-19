// overnight throughput: run-all scheduling and the morning digest, on top of board.js.
// loaded after board.js in index.html and relies on its globals: api, escapeHtml, showRun,
// onBoardEnter, currentBoardId, toggleOverlay, Menu. nothing here is redefined from board.js -
// see the brief's list of what is safe to assume is already global.

// how often the board bar asks the scheduler where it got to. a card run takes minutes, same
// reasoning as board.js's RUN_POLL_MS - fast polling buys nothing but requests
const SCHEDULE_POLL_MS = 2000;

let scheduleTimer = null;
// true from the moment `w` starts a run-all until it drains or is stopped - what toggleRunAll
// reads to decide whether the next press starts or stops
let scheduleRunning = false;

function scheduleStatusEl() {
  let el = document.getElementById('schedule-status');
  if (!el) {
    el = document.createElement('div');
    el.id = 'schedule-status';
    el.className = 'schedule-status';
    el.hidden = true;
    // the bar corner, not #board-bar: a board list reload would wipe it from there
    barCorner().appendChild(el);
  }
  return el;
}

function formatPausedUntil(pausedUntil) {
  const d = new Date(pausedUntil * 1000);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `paused til ${hh}:${mm}`;
}

// "2 running, 3 queued" in the board bar - hidden the moment there is nothing left to say
function renderScheduleStatus(view) {
  const el = scheduleStatusEl();
  const running = view.running.length;
  const queued = view.queued.length;
  if (!running && !queued && !view.paused_until) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  const parts = [`${running} running`, `${queued} queued`];
  if (view.paused_until) parts.push(formatPausedUntil(view.paused_until));
  el.textContent = parts.join(' - ');
}

// running/queued/waiting cards each carry their state on the strip's foot, the same place a
// manual run's phase shows (board.js's showRun) - a card started by run-all looks no different
// from one started by hand, which is the point
function applyScheduleToCards(view) {
  view.running.forEach(id => {
    showRun(id, 'running');
    // whatever the CTA named before this run started (a blocked reason, a stale queue note) is
    // stale the moment the queue actually starts the card - the run itself is now the story
    const note = actionNote(id);
    if (note) note.textContent = 'Running…';
  });
  const waitingIds = new Set(Object.keys(view.waiting));
  Object.entries(view.waiting).forEach(([id, reason]) => showRun(id, 'waiting', null, reason));
  // position is 1-based so "queued, 1 of 3" reads as the front of the line, not the back
  const total = view.queued.length;
  view.queued.forEach((id, index) => {
    if (waitingIds.has(id)) return;
    showRun(id, `queued, ${index + 1} of ${total}`);
    // a card re-queued while blocked kept its 'doing' status, so the compact note still reads
    // 'Running…' - it hasn't actually started again yet, the queue has
    const note = actionNote(id);
    if (note && note.textContent === 'Running…') note.textContent = 'Queued';
  });
}

async function pollSchedule() {
  scheduleTimer = null;
  if (!currentBoardId) return;
  let view;
  try {
    view = await api(`/api/boards/${currentBoardId}/schedule`);
  } catch (err) {
    scheduleTimer = setTimeout(pollSchedule, SCHEDULE_POLL_MS);
    return;
  }
  renderScheduleStatus(view);
  applyScheduleToCards(view);
  const active = view.running.length > 0 || view.queued.length > 0;
  scheduleRunning = active;
  if (active) scheduleTimer = setTimeout(pollSchedule, SCHEDULE_POLL_MS);
}

// w starts running the current board; pressing it again while a schedule is going stops the
// queue - running cards are left to finish, exactly as run-all/stop promises server-side
async function toggleRunAll() {
  if (!currentBoardId) return;
  // stopping is never confirmed, same as k - it only winds work down, never starts it
  if (scheduleRunning) {
    if (scheduleTimer) { clearTimeout(scheduleTimer); scheduleTimer = null; }
    scheduleRunning = false;
    const view = await api(`/api/boards/${currentBoardId}/run-all/stop`, {method: 'POST'});
    renderScheduleStatus(view);
    applyScheduleToCards(view);
    return;
  }
  openActionConfirm('run every queued card on this board?', 'run the queue', 'cancel', doStartRunAll);
}

async function doStartRunAll() {
  if (!currentBoardId) return;
  if (scheduleTimer) { clearTimeout(scheduleTimer); scheduleTimer = null; }
  scheduleRunning = true;
  const view = await api(`/api/boards/${currentBoardId}/run-all`, {method: 'POST'});
  renderScheduleStatus(view);
  applyScheduleToCards(view);
  pollSchedule();
}

// ---- morning digest (d) -----------------------------------------------------------------------

// "since" defaults to the last time the operator opened the digest, remembered per browser - a fresh
// tab with no history just shows everything, which is the honest default for "never opened before"
const DIGEST_SINCE_KEY = 'smortboard-digest-since';

function digestSinceDefault() {
  try {
    const stored = localStorage.getItem(DIGEST_SINCE_KEY);
    return stored ? Number(stored) : 0;
  } catch (err) {
    return 0; // private browsing or storage disabled - the digest just always shows everything
  }
}

function rememberDigestOpened() {
  try {
    localStorage.setItem(DIGEST_SINCE_KEY, String(Math.floor(Date.now() / 1000)));
  } catch (err) {
    // nothing to do - the next open just falls back to "everything" again
  }
}

function dLine(text, className) {
  const el = document.createElement('div');
  el.className = className;
  el.textContent = text;
  return el;
}

function digestDivider() {
  return dLine('', 'h-divider');
}

function digestPrRow(pr) {
  const row = document.createElement('div');
  row.className = 'digest-section digest-pr';
  row.appendChild(dLine(pr.title, 'field-label'));
  const link = document.createElement('a');
  link.href = safeUrl(pr.url);
  link.target = '_blank';
  link.rel = 'noreferrer';
  link.className = 'stat digest-pr-link';
  link.textContent = pr.url;
  row.appendChild(link);
  if (pr.dependency_not_in_batch) {
    row.appendChild(dLine(
      'a dependency did not open its own pull request in this window (merged earlier, or not run yet)',
      'stat digest-warn',
    ));
  }
  return row;
}

function digestWaitingRow(row) {
  const el = document.createElement('div');
  el.className = 'digest-section digest-waiting';
  el.appendChild(dLine(`${row.title} - ${row.reason}`, 'field-label'));
  el.appendChild(dLine(row.question || 'no note left', 'stat'));
  return el;
}

function digestBody(data) {
  const wrap = document.createElement('div');
  wrap.className = 'digest-card';

  wrap.appendChild(dLine('pull requests, in merge order', 'field-label'));
  if (data.pull_requests.length) {
    data.pull_requests.forEach(pr => wrap.appendChild(digestPrRow(pr)));
  } else {
    wrap.appendChild(dLine('none since last time', 'empty'));
  }
  wrap.appendChild(digestDivider());

  wrap.appendChild(dLine('waiting on you', 'field-label'));
  if (data.waiting_on_operator.length) {
    data.waiting_on_operator.forEach(row => wrap.appendChild(digestWaitingRow(row)));
  } else {
    wrap.appendChild(dLine('nothing blocked', 'empty'));
  }
  wrap.appendChild(digestDivider());

  const spend = data.runs_and_spend;
  const foot = document.createElement('div');
  foot.className = 'card-foot digest-foot';
  foot.appendChild(dLine(`${spend.runs} runs - $${(spend.total_cost_usd || 0).toFixed(2)}`, 'stat'));
  wrap.appendChild(foot);
  return wrap;
}

function openDigestPanel() {
  if (!currentBoardId) return;
  toggleOverlay('KeyD', () => {
    const menu = new Menu({
      title: 'morning digest',
      sections: [{kind: 'list', items: [], empty: 'loading...'}],
      onDismiss: () => { if (openOverlay && openOverlay.key === 'KeyD') openOverlay = null; },
    });
    menu.openAt({x: window.innerWidth / 2 - 220, y: 80});
    menu.el?.classList.add('menu-centered');
    loadDigest(menu);
    return menu;
  });
}

async function loadDigest(menu) {
  const since = digestSinceDefault();
  try {
    const data = await api(`/api/boards/${currentBoardId}/digest?since=${since}`);
    menu.refresh([{kind: 'node', node: digestBody(data)}]);
    rememberDigestOpened();
  } catch (err) {
    menu.refresh([{kind: 'list', items: [], empty: `could not load the digest: ${err.message}`}]);
  }
}
