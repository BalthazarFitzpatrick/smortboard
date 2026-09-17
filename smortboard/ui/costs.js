// board cost overview (c): every board's spend in one panel, costliest first, one row per board so
// nine boards still fit. click a board's name to go to it; 1-9 do the same from anywhere. reuses
// board.js and telemetry.js globals: api, fillBar, textLine, formatUsd, toggleOverlay, Menu.

function costPerPrLabel(row) {
  return row.cost_per_pr_usd == null ? null : `${formatUsd(row.cost_per_pr_usd)} / pr`;
}

// an em dash rather than a divide-by-zero when a group or figure holds no cards/prs yet
function dashOrUsd(value) {
  return value == null ? '—' : formatUsd(value);
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

function costCell(text, cls = '') {
  const cell = document.createElement('div');
  cell.className = cls;
  cell.textContent = text;
  return cell;
}

const COST_GROUP_ORDER = ['total', 'accepted', 'refused'];

// one cell of the 3x3 grid: spend, price per card, price per pr for one group - card/pr counts
// shown small beside the figures they divide, so the two divisions can be checked by eye
function costGroupBox(title, group) {
  const box = document.createElement('div');
  box.className = `cost-group cost-group-${title}`;
  box.appendChild(costCell(title, 'cost-group-title'));
  box.appendChild(costCell(`${formatUsd(group.cost_usd)} spend`, 'cost-group-figure'));
  box.appendChild(costCell(`${dashOrUsd(group.cost_per_card_usd)} / card`, 'cost-group-figure'));
  box.appendChild(costCell(`${dashOrUsd(group.cost_per_pr_usd)} / pr`, 'cost-group-figure'));
  box.appendChild(costCell(`${plural(group.cards, 'card')} · ${plural(group.prs, 'pr')}`, 'cost-group-counts'));
  return box;
}

// the 3x3 grid: total, accepted, refused left to right - each carrying its own spend / per-card /
// per-pr, read straight off cost_groups so an empty group shows the dash rather than dividing by zero
function costGroupsGrid(costGroups) {
  const grid = document.createElement('div');
  grid.className = 'cost-groups-grid';
  COST_GROUP_ORDER.forEach(name => grid.appendChild(costGroupBox(name, costGroups[name])));
  return grid;
}

const COST_COLUMNS = ['board', 'share', 'spend', 'runs', 'accepted', 'prs', 'per pr', 'spend w/ denial'];

// one board: its name, its share of all spend as a bar, then the figures in aligned columns
function boardCostRow(row, share) {
  const line = document.createElement('div');
  line.className = 'cost-row';
  const name = costCell(row.board_name, 'cost-name');
  name.onclick = () => jumpToBoard(row.board_id);
  line.append(
    name,
    fillBar(share),
    costCell(formatUsd(row.cost_usd), 'num'),
    costCell(String(row.runs), 'num'),
    costCell(`${row.cards_accepted}/${row.cards}`, 'num'),
    costCell(String(row.pull_requests_opened), 'num'),
    costCell(row.cost_per_pr_usd == null ? '-' : formatUsd(row.cost_per_pr_usd), 'num'),
    costCell(row.refusal_cost_usd ? formatUsd(row.refusal_cost_usd) : '-', 'num'),
  );
  return line;
}

function totalsFoot(totals) {
  const foot = document.createElement('div');
  foot.className = 'card-foot usage-foot cost-foot';
  const wasteNote = totals.refusal_cost_usd
    ? ` - ${formatUsd(totals.refusal_cost_usd)} on runs with a permission denial`
    : '';
  const prNote = totals.cost_per_pr_usd == null ? '' : ` - ${costPerPrLabel(totals)}`;
  foot.appendChild(
    textLine(
      `${plural(totals.boards, 'board')} - ${plural(totals.cards, 'card')} - ${plural(totals.runs, 'run')} - ` +
        `${formatUsd(totals.cost_usd)}${prNote}${wasteNote}`,
      'stat'
    )
  );
  const models = (totals.spend_by_model || [])
    .slice()
    .sort((a, b) => b.cost_usd - a.cost_usd)
    .map(m => `${m.model} ${formatUsd(m.cost_usd)}`)
    .join(', ');
  const roles = `worker ${formatUsd(totals.worker_cost_usd || 0)} - reviewer ${formatUsd(totals.reviewer_cost_usd || 0)}`;
  foot.appendChild(textLine(models ? `${roles} - ${models}` : roles, 'stat'));
  foot.appendChild(textLine(
    `mission control and fold turns ${formatUsd(totals.turn_cost_usd || 0)} - not in the card totals above`,
    'stat cost-note'
  ));
  return foot;
}

// jumping to a board does exactly what clicking its tab would: switch and let the board render
async function jumpToBoard(boardId) {
  if (boardId === currentBoardId) return;
  activateTab(boardId);
  await onBoardEnter(boardId);
}

function costsOverviewSections(data) {
  if (!data.boards.length) {
    const box = document.createElement('div');
    box.className = 'hazard-stripes hazard-placeholder';
    box.innerHTML = '<span class="hazard-label">no boards yet</span>';
    return [{kind: 'node', node: box}];
  }
  const total = data.totals.cost_usd || 0;
  const card = document.createElement('div');
  card.className = 'usage-card usage-wide cost-card';

  // the 3x3 grid leads - total / accepted / refused, each with spend, per-card and per-pr - what
  // a scan of the panel needs first, before the per-board detail table below it
  const emptyGroup = {cards: 0, cost_usd: 0, prs: 0, cost_per_card_usd: null, cost_per_pr_usd: null};
  const groups = data.cost_groups || {total: emptyGroup, accepted: emptyGroup, refused: emptyGroup};
  card.appendChild(costGroupsGrid(groups));
  card.appendChild(textLine('', 'h-divider'));

  const rows = document.createElement('div');
  rows.className = 'cost-rows';
  const head = document.createElement('div');
  head.className = 'cost-row';
  COST_COLUMNS.forEach((label, i) => head.appendChild(costCell(label, i > 1 ? 'cost-head num' : 'cost-head')));
  rows.appendChild(head);
  data.boards.forEach(row => rows.appendChild(boardCostRow(row, total > 0 ? row.cost_usd / total : 0)));
  card.appendChild(rows);
  card.appendChild(textLine('', 'h-divider'));
  card.appendChild(totalsFoot({...data.totals, boards: data.boards.length}));
  return [{kind: 'node', node: card}];
}

// ---- cost optimisation view: cap fit, suggested caps, waste, model fit ------------------------

const COST_COMPLEXITY_LABELS = {1: 'low', 2: 'medium', 3: 'high'};

function optSectionHeading(text) {
  const heading = document.createElement('div');
  heading.className = 'field-label opt-section-heading';
  heading.textContent = text;
  return heading;
}

function optRow(cells) {
  const row = document.createElement('div');
  row.className = 'opt-row';
  cells.forEach(([text, cls]) => row.appendChild(costCell(text, cls)));
  return row;
}

// a header row from plain column labels - every numeric column right-aligned, like COST_COLUMNS
function optHeadRow(labels, numericFrom) {
  return optRow(labels.map((label, i) => [label, i >= numericFrom ? 'cost-head num' : 'cost-head']));
}

function capFitSection(capFit) {
  const box = document.createElement('div');
  box.className = 'opt-section';
  box.appendChild(optSectionHeading(`cap fit - worker cap ${formatUsd(capFit.worker_cap_usd)}`));
  const rows = document.createElement('div');
  rows.className = 'opt-rows opt-cap-fit';
  rows.appendChild(optHeadRow(['complexity', 'source', 'runs', 'within cap', 'hit cap', 'median', 'max'], 2));
  if (!capFit.rows.length) rows.appendChild(costCell('no worker runs yet', 'opt-empty'));
  capFit.rows.forEach(r => rows.appendChild(optRow([
    [COST_COMPLEXITY_LABELS[r.complexity] || String(r.complexity)],
    [r.source],
    [String(r.runs), 'num'],
    [String(r.within_cap), 'num'],
    [String(r.hit_cap), 'num'],
    [formatUsd(r.median_cost_usd), 'num'],
    [formatUsd(r.max_cost_usd), 'num'],
  ])));
  box.appendChild(rows);
  return box;
}

function suggestedCapsSection(suggestedCaps) {
  const box = document.createElement('div');
  box.className = 'opt-section';
  box.appendChild(optSectionHeading('suggested caps'));
  const rows = document.createElement('div');
  rows.className = 'opt-rows opt-suggested-caps';
  rows.appendChild(optHeadRow(['role', 'current', 'p90', 'suggested', 'would cut', 'spend diff'], 1));
  Object.entries(suggestedCaps).forEach(([role, info]) => {
    if (info.note) {
      rows.appendChild(optRow([
        [role], [formatUsd(info.current_cap_usd), 'num'], [info.note], ['', 'num'], ['', 'num'], ['', 'num'],
      ]));
      return;
    }
    rows.appendChild(optRow([
      [role],
      [formatUsd(info.current_cap_usd), 'num'],
      [formatUsd(info.p90_cost_usd), 'num'],
      [formatUsd(info.suggested_cap_usd), 'num'],
      [String(info.runs_that_would_be_cut), 'num'],
      [formatUsd(info.spend_difference_usd), 'num'],
    ]));
  });
  box.appendChild(rows);
  return box;
}

function wasteSection(waste) {
  const box = document.createElement('div');
  box.className = 'opt-section';
  box.appendChild(optSectionHeading(`waste - ${formatUsd(waste.total_cost_usd)} total`));
  const rows = document.createElement('div');
  rows.className = 'opt-rows opt-waste';
  rows.appendChild(optHeadRow(['reason', 'count', 'spend'], 1));
  if (!waste.rows.length) rows.appendChild(costCell('nothing wasted', 'opt-empty'));
  waste.rows.forEach(r => rows.appendChild(optRow([
    [r.reason], [String(r.count), 'num'], [formatUsd(r.cost_usd), 'num'],
  ])));
  box.appendChild(rows);
  return box;
}

function modelFitSection(modelFit) {
  const box = document.createElement('div');
  box.className = 'opt-section';
  box.appendChild(optSectionHeading('model fit'));
  const rows = document.createElement('div');
  rows.className = 'opt-rows opt-model-fit';
  rows.appendChild(optHeadRow(['model', 'complexity', 'cards', 'accepted', 'cost / accepted', 'fix rate'], 2));
  if (!modelFit.length) rows.appendChild(costCell('no cards yet', 'opt-empty'));
  modelFit.forEach(r => rows.appendChild(optRow([
    [r.model],
    [COST_COMPLEXITY_LABELS[r.complexity] || String(r.complexity)],
    [String(r.cards), 'num'],
    [String(r.accepted_cards), 'num'],
    [dashOrUsd(r.cost_per_accepted_card_usd), 'num'],
    [r.fix_round_share == null ? '—' : `${Math.round(r.fix_round_share * 100)}%`, 'num'],
  ])));
  box.appendChild(rows);
  return box;
}

function costOptimisationCard(data) {
  const card = document.createElement('div');
  card.className = 'usage-card usage-wide cost-card opt-card';
  card.appendChild(capFitSection(data.cap_fit));
  card.appendChild(textLine('', 'h-divider'));
  card.appendChild(suggestedCapsSection(data.suggested_caps));
  card.appendChild(textLine('', 'h-divider'));
  card.appendChild(wasteSection(data.waste));
  card.appendChild(textLine('', 'h-divider'));
  card.appendChild(modelFitSection(data.model_fit));
  return card;
}

// ---- cost / cost-optimisation header - same fixed-row, arrows-either-side shape inbox.js's
// scope header uses (inbox.css's .inbox-header/.inbox-nav/.inbox-scope-label, reused as-is) -----

const COSTS_VIEWS = ['cost', 'cost optimisation'];

const cv = {menu: null, view: 0, overview: null, optimisation: null, optimisationLoaded: false};

function costsHazardNode(text) {
  const box = document.createElement('div');
  box.className = 'hazard-stripes hazard-placeholder';
  const label = document.createElement('span');
  label.className = 'hazard-label';
  label.textContent = text;
  box.appendChild(label);
  return box;
}

function costsHeaderNode() {
  const header = document.createElement('div');
  header.className = 'inbox-header';
  const prev = document.createElement('span');
  prev.className = 'inbox-nav toggle';
  prev.textContent = '←';
  prev.onclick = () => cycleCostsView(-1);
  const label = document.createElement('span');
  label.className = 'inbox-scope-label';
  label.textContent = COSTS_VIEWS[cv.view];
  const next = document.createElement('span');
  next.className = 'inbox-nav toggle';
  next.textContent = '→';
  next.onclick = () => cycleCostsView(1);
  header.append(prev, label, next);
  return header;
}

function costsOverviewNode() {
  if (!cv.overview) return costsHazardNode('loading...');
  if (cv.overview.error) return costsHazardNode(`could not load costs: ${cv.overview.error}`);
  return costsOverviewSections(cv.overview)[0].node;
}

function costsOptimisationNode() {
  if (!cv.optimisation) return costsHazardNode('loading...');
  if (cv.optimisation.error) return costsHazardNode(`could not load costs: ${cv.optimisation.error}`);
  return costOptimisationCard(cv.optimisation);
}

function renderCostsPanel() {
  if (!cv.menu) return;
  const wrap = document.createElement('div');
  wrap.className = 'costs-view';
  wrap.appendChild(costsHeaderNode());
  const body = document.createElement('div');
  body.className = 'costs-view-body';
  body.appendChild(cv.view === 0 ? costsOverviewNode() : costsOptimisationNode());
  wrap.appendChild(body);
  cv.menu.refresh([{kind: 'node', node: wrap}]);
}

function cycleCostsView(dir) {
  cv.view = (cv.view + dir + COSTS_VIEWS.length) % COSTS_VIEWS.length;
  renderCostsPanel();
  if (cv.view === 1 && !cv.optimisationLoaded) loadCostsOptimisation();
}

// capture, not bubble - same reason inbox.js's onInboxKey runs in capture: it must steal
// left/right before the board tab bar's own bubble-phase handler ever sees them
function onCostsKey(evt) {
  const targetTag = (evt.target?.tagName || '').toUpperCase();
  if (targetTag === 'INPUT' || targetTag === 'TEXTAREA') return;
  if (evt.code === 'ArrowLeft') { evt.stopPropagation(); evt.preventDefault?.(); cycleCostsView(-1); return; }
  if (evt.code === 'ArrowRight') { evt.stopPropagation(); evt.preventDefault?.(); cycleCostsView(1); return; }
}

async function loadBoardsOverview() {
  try {
    cv.overview = await api('/api/costs');
  } catch (err) {
    cv.overview = {error: err.message};
  }
  renderCostsPanel();
}

async function loadCostsOptimisation() {
  cv.optimisationLoaded = true;
  try {
    cv.optimisation = await api('/api/costs/optimisation');
  } catch (err) {
    cv.optimisation = {error: err.message};
  }
  renderCostsPanel();
}

function openCostsOverviewPanel() {
  toggleOverlay('KeyC', () => {
    cv.view = 0;
    cv.overview = null;
    cv.optimisation = null;
    cv.optimisationLoaded = false;
    const menu = new Menu({
      title: 'cost overview',
      sections: [{kind: 'node', node: costsHazardNode('loading...')}],
      onDismiss: () => {
        document.removeEventListener('keydown', onCostsKey, {capture: true});
        cv.menu = null;
        if (openOverlay && openOverlay.key === 'KeyC') openOverlay = null;
      },
    });
    menu.openAt({x: window.innerWidth / 2 - 200, y: 80});
    menu.el?.classList.add('menu-centered');
    cv.menu = menu;
    // idempotent: a previous close that never fired onDismiss (a test stub's Menu.close, say)
    // must not leave two listeners both toggling the view on the same keypress
    document.removeEventListener('keydown', onCostsKey, {capture: true});
    document.addEventListener('keydown', onCostsKey, {capture: true});
    renderCostsPanel();
    loadBoardsOverview();
    return menu;
  });
}
