// pre-flight checklist (h): everything that has to be true before a card can run, and how to fix
// each thing that isn't - across the machine and every repo registered on any board.
//
// built from the same modal-backdrop / panel-floating pair as the attention inbox (inbox.js), not
// a Menu, for the same reason: nothing here needs arrow/enter/escape hijacked from the document.
//
// relies on globals board.js already defines: api, reenterIfFocusLost.

// opened: group name -> the operator's own toggle, kept across re-checks so a fixed row does not
// slam its group shut under them; a group they never touched opens only while something is wrong
const pf = {backdrop: null, panel: null, summaryEl: null, listEl: null, checks: [], opened: new Map()};

function buildPreflightDom() {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop preflight-backdrop';
  const panel = document.createElement('div');
  panel.className = 'panel-floating preflight-panel';

  const title = document.createElement('div');
  title.className = 'popup-title';
  title.textContent = 'pre-flight checklist';

  const summary = document.createElement('div');
  summary.className = 'preflight-summary';

  const recheck = document.createElement('button');
  recheck.type = 'button';
  recheck.className = 'preflight-recheck toggle';
  recheck.textContent = 're-check';
  recheck.onclick = () => loadPreflight();

  const head = document.createElement('div');
  head.className = 'preflight-head';
  head.append(summary, recheck);

  const rule = document.createElement('div');
  rule.className = 'h-divider';

  const list = document.createElement('div');
  list.className = 'preflight-list';

  panel.append(title, head, rule, list);
  backdrop.appendChild(panel);
  backdrop.addEventListener('mousedown', evt => { if (evt.target === backdrop) closePreflightPanel(); });

  Object.assign(pf, {backdrop, panel, summaryEl: summary, listEl: list});
  return backdrop;
}

function hazardPlaceholder(text) {
  const box = document.createElement('div');
  box.className = 'hazard-stripes hazard-placeholder';
  const label = document.createElement('span');
  label.className = 'hazard-label';
  label.textContent = text;
  box.appendChild(label);
  return box;
}

function clearChildren(el) {
  [...el.children].forEach(child => child.remove());
}

// var(--lichen-milk) ok, var(--fill-attention) warn, var(--fill-warn) fail - per CLAUDE.md palette
function dotClass(status) {
  if (status === 'ok') return 'preflight-dot-ok';
  if (status === 'warn') return 'preflight-dot-warn';
  return 'preflight-dot-fail';
}

function buildCheckRow(check) {
  const rowEl = document.createElement('div');
  rowEl.className = 'preflight-row';
  rowEl.dataset.checkId = check.id;

  const head = document.createElement('div');
  head.className = 'preflight-row-head';
  const dot = document.createElement('span');
  dot.className = `preflight-dot ${dotClass(check.status)}`;
  const label = document.createElement('span');
  label.className = 'preflight-label';
  label.textContent = check.label;
  head.append(dot, label);

  const detail = document.createElement('div');
  detail.className = 'field-label preflight-detail';
  detail.textContent = check.detail;

  rowEl.append(head, detail);

  if (check.status !== 'ok' && check.fix) {
    const fix = document.createElement('div');
    fix.className = 'preflight-fix';
    fix.textContent = check.fix;
    rowEl.appendChild(fix);
  }
  return rowEl;
}

function groupSummary(checks) {
  const failing = checks.filter(c => c.status === 'fail').length;
  const warnings = checks.filter(c => c.status === 'warn').length;
  const ready = checks.filter(c => c.status === 'ok').length;
  return [
    failing ? `${failing} failing` : null,
    warnings ? `${warnings} ${warnings === 1 ? 'warning' : 'warnings'}` : null,
    ready ? `${ready} ready` : null,
  ].filter(Boolean).join(' / ');
}

function worstStatus(checks) {
  if (checks.some(c => c.status === 'fail')) return 'fail';
  if (checks.some(c => c.status === 'warn')) return 'warn';
  return 'ok';
}

// a group is one disclosure: the header carries the worst state and a one-line count, the body the
// rows with whatever needs attention first
function buildGroup(name, checks) {
  const worst = worstStatus(checks);
  const body = document.createElement('div');
  body.className = 'preflight-group-body';
  const problems = checks.filter(c => c.status !== 'ok');
  [...problems, ...checks.filter(c => c.status === 'ok')].forEach(check => body.appendChild(buildCheckRow(check)));

  const disclosure = makeDisclosure({
    title: name === 'machine' ? 'this machine' : name,
    summary: groupSummary(checks),
    open: pf.opened.has(name) ? pf.opened.get(name) : worst !== 'ok',
    body,
    onToggle: open => pf.opened.set(name, open),
  });
  disclosure.el.classList.add('preflight-group');
  disclosure.el.dataset.group = name;
  const dot = document.createElement('span');
  dot.className = `preflight-dot ${dotClass(worst)}`;
  const title = disclosure.el.querySelector('.disclosure-title');
  title.insertBefore(dot, title.firstChild);
  return disclosure;
}

function renderPreflight() {
  clearChildren(pf.listEl);
  const ready = pf.checks.filter(c => c.status === 'ok').length;
  const total = pf.checks.length;
  pf.summaryEl.textContent = `${ready} of ${total} ready`;
  pf.summaryEl.classList.toggle('preflight-summary-ok', ready === total && total > 0);

  if (!pf.checks.length) {
    pf.listEl.appendChild(hazardPlaceholder('nothing to check yet'));
    return;
  }

  // grouped in the order the api returned them - machine checks first, then each repo by name
  const order = [];
  const byGroup = new Map();
  pf.checks.forEach(check => {
    if (!byGroup.has(check.group)) { byGroup.set(check.group, []); order.push(check.group); }
    byGroup.get(check.group).push(check);
  });
  const set = makeDisclosureGroup(order.map(name => ({id: name, disclosure: buildGroup(name, byGroup.get(name))})));
  pf.listEl.appendChild(set.el);
}

async function loadPreflight() {
  clearChildren(pf.listEl);
  pf.summaryEl.textContent = 'checking...';
  pf.listEl.appendChild(waitingPlaceholder('checking'));
  try {
    pf.checks = await api('/api/preflight');
  } catch (err) {
    clearChildren(pf.listEl);
    pf.summaryEl.textContent = '';
    pf.listEl.appendChild(hazardPlaceholder(`could not load: ${err.message}`));
    return;
  }
  renderPreflight();
}

// KeyH is not handled here: board.js's own listener already binds it to togglePreflightPanel, so
// a second press closes the panel rather than re-checking - only the button re-checks, so this
// panel never shadows another key (like r) that board.js's listener still dispatches underneath it
function onPreflightKey(evt) {
  if (evt.code === 'Escape') closePreflightPanel();
}

function openPreflightPanel() {
  if (!pf.backdrop) buildPreflightDom();
  document.body.appendChild(pf.backdrop);
  document.addEventListener('keydown', onPreflightKey);
  loadPreflight();
  focusPanel(pf.panel);
}

function closePreflightPanel() {
  if (!pf.backdrop || !pf.backdrop.parentNode) return;
  document.removeEventListener('keydown', onPreflightKey);
  pf.backdrop.remove();
  reenterIfFocusLost();
}

function togglePreflightPanel() {
  if (pf.backdrop && pf.backdrop.parentNode) closePreflightPanel();
  else openPreflightPanel();
}
