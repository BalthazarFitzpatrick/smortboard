// landing lock (q): who holds the push lock on each repo/target, and who is queued behind them -
// the board's own card runs and any outside smortboard-land agent share this one view, since the
// lock itself is stored server-side (GET /api/landing). live refresh while open, closes on q/esc.
//
// same modal-backdrop / panel-floating pair as pulls.js, for the same reason: nothing here needs
// arrow/enter hijacked from the document, just a poll and a close key.
//
// relies on globals board.js already defines: api, reenterIfFocusLost.

const ld = {backdrop: null, panel: null, listEl: null, timer: null};

// while a heartbeat is due roughly every 30s, a stale poll would sit for minutes - this is how
// fast a currently-open panel notices a grant, a release, or an eviction
const LANDING_REFRESH_MS = 3000;

function buildLandingDom() {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop landing-backdrop';
  const panel = document.createElement('div');
  panel.className = 'panel-floating landing-panel';

  const title = document.createElement('div');
  title.className = 'popup-title';
  title.textContent = 'landing lock';
  const rule = document.createElement('div');
  rule.className = 'h-divider';

  const list = document.createElement('div');
  list.className = 'landing-list';

  panel.append(title, rule, list);
  backdrop.appendChild(panel);
  backdrop.addEventListener('mousedown', evt => { if (evt.target === backdrop) closeLandingPanel(); });

  Object.assign(ld, {backdrop, panel, listEl: list});
  return backdrop;
}

function landingHazard(text) {
  const box = document.createElement('div');
  box.className = 'hazard-stripes hazard-placeholder';
  const label = document.createElement('span');
  label.className = 'hazard-label';
  label.textContent = text;
  return box.appendChild(label), box;
}

function landingClearChildren(el) {
  [...el.children].forEach(child => child.remove());
}

function landingAge(iso) {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}

function buildLandingRow(entry) {
  const rowEl = document.createElement('div');
  rowEl.className = 'landing-row';
  rowEl.appendChild(document.createElement('div')).className = 'h-divider';

  const head = document.createElement('div');
  head.className = 'landing-row-head';
  const repo = document.createElement('span');
  repo.className = 'landing-repo';
  repo.textContent = entry.repo_key;
  const target = document.createElement('span');
  target.className = 'field-label landing-target';
  target.textContent = `-> ${entry.target}`;
  head.append(repo, target);
  rowEl.appendChild(head);

  if (entry.holder) {
    const holder = document.createElement('div');
    holder.className = 'landing-holder';
    const who = document.createElement('span');
    who.className = 'stat';
    who.textContent = `held by ${entry.holder.holder} (${entry.holder.branch})`;
    const since = document.createElement('span');
    since.className = 'field-label';
    since.textContent = `since ${landingAge(entry.holder.since)} ago, heartbeat ${landingAge(entry.holder.last_heartbeat)} ago, ttl ${entry.holder.ttl_s}s`;
    holder.append(who, since);
    rowEl.appendChild(holder);
  } else {
    const free = document.createElement('div');
    free.className = 'landing-holder landing-free';
    free.textContent = 'free';
    rowEl.appendChild(free);
  }

  if (entry.queue.length) {
    const queue = document.createElement('div');
    queue.className = 'landing-queue';
    entry.queue.forEach(q => {
      const item = document.createElement('div');
      item.className = 'landing-queue-item';
      item.textContent = `${q.position}. ${q.holder} (${q.branch}) - queued ${landingAge(q.queued_at)} ago`;
      queue.appendChild(item);
    });
    rowEl.appendChild(queue);
  }

  return rowEl;
}

function renderLanding(entries) {
  landingClearChildren(ld.listEl);
  if (!entries.length) {
    ld.listEl.appendChild(landingHazard('no repo is locked or queued right now'));
    return;
  }
  entries.forEach(entry => ld.listEl.appendChild(buildLandingRow(entry)));
}

async function loadLanding() {
  try {
    const entries = await api('/api/landing');
    renderLanding(entries);
  } catch (err) {
    landingClearChildren(ld.listEl);
    ld.listEl.appendChild(landingHazard(`could not load: ${err.message}`));
  }
}

// q toggles open/close through the global handler in shortcuts.js (toggleLandingPanel) - only
// escape is handled here, same split pulls.js uses for v vs its own onPullsKey
function onLandingKey(evt) {
  if (evt.code === 'Escape') { closeLandingPanel(); }
}

function openLandingPanel() {
  if (!ld.backdrop) buildLandingDom();
  document.body.appendChild(ld.backdrop);
  document.addEventListener('keydown', onLandingKey);
  loadLanding();
  ld.timer = setInterval(loadLanding, LANDING_REFRESH_MS);
}

function closeLandingPanel() {
  if (!ld.backdrop || !ld.backdrop.parentNode) return;
  document.removeEventListener('keydown', onLandingKey);
  if (ld.timer) { clearInterval(ld.timer); ld.timer = null; }
  ld.backdrop.remove();
  reenterIfFocusLost();
}

function toggleLandingPanel() {
  if (ld.backdrop && ld.backdrop.parentNode) closeLandingPanel();
  else openLandingPanel();
}
