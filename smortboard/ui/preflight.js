// pre-flight checklist (h): everything that has to be true before a card can run, and how to fix
// each thing that isn't - across the machine and every repo registered on any board.
//
// built from the same modal-backdrop / panel-floating pair as the attention inbox (inbox.js), not
// a Menu, for the same reason: nothing here needs arrow/enter/escape hijacked from the document.
//
// relies on globals board.js already defines: api, reenterIfFocusLost.

const pf = {backdrop: null, panel: null, summaryEl: null, listEl: null, checks: []};

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

function buildGroup(name, checks) {
  const groupEl = document.createElement('div');
  groupEl.className = 'preflight-group';
  const label = document.createElement('div');
  label.className = 'preflight-group-label';
  label.textContent = name === 'machine' ? 'this machine' : name;
  groupEl.appendChild(label);
  checks.forEach(check => groupEl.appendChild(buildCheckRow(check)));
  return groupEl;
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
  order.forEach(name => pf.listEl.appendChild(buildGroup(name, byGroup.get(name))));
}

async function loadPreflight() {
  clearChildren(pf.listEl);
  pf.summaryEl.textContent = 'checking...';
  pf.listEl.appendChild(hazardPlaceholder('checking...'));
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
