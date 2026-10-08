// top-bar usage cells: how much of each lab's account allowance is used, always in view.
// codex gets its 7d cell, claude its 5h and 7d cells, in board.js's bar corner. only real account
// readings count (a window carrying a `source`); run-derived windows have none and are ignored,
// so a cell never shows an estimate as if the account had said it

const USAGE_POLL_MS = 60000; // the server caches account readings for 60s, so faster is wasted
const USAGE_ATTENTION_FROM = 70; // percent used: vanilla from here, stone from the next line
const USAGE_WARN_FROM = 90;

const USAGE_CELLS = [
  {group: 'codex', lab: 'openai', type: 'seven_day', label: 'codex 7d'},
  {group: 'claude', lab: 'anthropic', type: 'five_hour', label: 'claude 5h'},
  {group: 'claude', lab: 'anthropic', type: 'seven_day', label: 'claude 7d'},
];

// pure: the mean used fraction (0..1) over a lab's real, live windows of one type, or null when
// there is none. null is "no data" and draws an empty frame, never 0%
function labUsage(windows, lab, type) {
  const used = windows
    .filter(w => w.lab === lab && w.type === type && w.source && !w.rolled_over
      && typeof w.utilization === 'number')
    .map(w => w.utilization);
  if (!used.length) return null;
  return used.reduce((sum, value) => sum + value, 0) / used.length;
}

// pure: the ui_base fill-cell tone for a percent used. '' is the default (good) tone
function usageTone(percent) {
  if (percent >= USAGE_WARN_FROM) return 'warn';
  if (percent >= USAGE_ATTENTION_FROM) return 'attention';
  return '';
}

function renderUsageCell(el, fraction) {
  const percent = fraction === null ? null : Math.min(100, Math.max(0, Math.round(fraction * 100)));
  const tone = percent === null ? '' : usageTone(percent);
  el.classList.toggle('attention', tone === 'attention');
  el.classList.toggle('warn', tone === 'warn');
  el.querySelector('.fill-cell-bar').style.width = percent === null ? '0' : `${percent}%`;
  el.querySelector('.fill-cell-value').textContent = percent === null ? '--' : `${percent}%`;
}

function buildUsageCell(spec) {
  const el = document.createElement('div');
  el.className = 'fill-cell usage-cell';
  el.dataset.cell = spec.label;
  el.title = `${spec.label} used (account allowance)`;
  const bar = document.createElement('div');
  bar.className = 'fill-cell-bar';
  const label = document.createElement('span');
  label.className = 'fill-cell-label';
  label.textContent = spec.label;
  const value = document.createElement('span');
  value.className = 'fill-cell-value';
  const sep = document.createElement('span');
  sep.className = 'fill-cell-sep';
  sep.textContent = '|';
  el.append(bar, label, sep, value);
  renderUsageCell(el, null);
  return el;
}

// one group per lab, a ui_base divider between groups and between a lab's own cells
function buildUsageCells() {
  const root = document.createElement('div');
  root.id = 'usage-cells';
  root.className = 'usage-cells';
  const cells = [];
  let group = null;
  USAGE_CELLS.forEach(spec => {
    if (!group || group.dataset.group !== spec.group) {
      if (group) root.appendChild(Object.assign(document.createElement('div'), {className: 'divider'}));
      group = document.createElement('div');
      group.className = 'usage-group';
      group.dataset.group = spec.group;
      root.appendChild(group);
    } else {
      group.appendChild(Object.assign(document.createElement('div'), {className: 'divider'}));
    }
    const el = buildUsageCell(spec);
    group.appendChild(el);
    cells.push({spec, el});
  });
  barCorner().appendChild(root);
  return cells;
}

let usageCells = [];

function renderUsageCells(windows) {
  usageCells.forEach(({spec, el}) => renderUsageCell(el, labUsage(windows, spec.lab, spec.type)));
}

async function pollUsageCells() {
  try {
    const usage = await api('/api/usage');
    renderUsageCells(usage.windows || []);
  } catch {
    // a failed poll leaves the last reading showing rather than blanking every cell
  }
}

function startUsageCells() {
  usageCells = buildUsageCells();
  pollUsageCells();
  const timer = setInterval(pollUsageCells, USAGE_POLL_MS);
  // node keeps a bare setInterval alive forever; the js test suite needs it to let the process exit
  timer.unref?.();
}

startUsageCells();
