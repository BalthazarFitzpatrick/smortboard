// board cost overview (c): every board's spend in one panel, costliest first, one row per board so
// nine boards still fit. click a board's name to go to it; 1-9 do the same from anywhere. reuses
// board.js and telemetry.js globals: api, fillBar, textLine, formatUsd, toggleOverlay, Menu.

// the priced part of a figure with its unpriced runs counted beside it: "$4.99 + 3 unknown", and
// plain "unknown" only when nothing in it has a price. prefix picks worker_, reviewer_, turn_ ...
function spendLabel(src, prefix = '', estimated = false) {
  const known = src[`${prefix}known_cost_usd`];
  const unknown = src[`${prefix}unknown_costs`] || 0;
  if (!unknown || known == null) return formatUsd(src[`${prefix}cost_usd`], estimated);
  return known ? `${formatUsd(known, estimated)} + ${unknown} unknown` : 'unknown';
}

// the priced part alone, for share bars and divisions - falls back to the plain figure
function knownSpend(src, prefix = '') {
  return src[`${prefix}known_cost_usd`] ?? src[`${prefix}cost_usd`];
}

function costPerPrLabel(row) {
  const perPr = row.known_cost_per_pr_usd ?? row.cost_per_pr_usd;
  return perPr == null ? null : `${formatUsd(perPr, row.cost_estimated)} / pr`;
}

// an em dash rather than a divide-by-zero when a group or figure holds no cards/prs yet
function dashOrUsd(value, estimated = false) {
  return value == null ? '—' : formatUsd(value, estimated);
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
  const perCard = group.known_cost_per_card_usd ?? group.cost_per_card_usd;
  const perPr = group.known_cost_per_pr_usd ?? group.cost_per_pr_usd;
  box.appendChild(costCell(`${spendLabel(group, '', group.cost_estimated)} spend`, 'cost-group-figure'));
  box.appendChild(costCell(`${dashOrUsd(perCard, group.cost_estimated)} / card`, 'cost-group-figure'));
  box.appendChild(costCell(`${dashOrUsd(perPr, group.cost_estimated)} / pr`, 'cost-group-figure'));
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
  const perPr = row.known_cost_per_pr_usd ?? row.cost_per_pr_usd;
  const refused = knownSpend(row, 'refusal_') || row.refusal_unknown_costs;
  line.append(
    name,
    fillBar(share),
    costCell(spendLabel(row, '', row.cost_estimated), 'num'),
    costCell(String(row.runs), 'num'),
    costCell(`${row.cards_accepted}/${row.cards}`, 'num'),
    costCell(String(row.pull_requests_opened), 'num'),
    costCell(perPr == null ? '-' : formatUsd(perPr), 'num'),
    costCell(refused ? spendLabel(row, 'refusal_') : '-', 'num'),
  );
  return line;
}

function totalsFoot(totals) {
  const foot = document.createElement('div');
  foot.className = 'card-foot usage-foot cost-foot';
  const wasteNote = knownSpend(totals, 'refusal_') || totals.refusal_unknown_costs
    ? ` - ${spendLabel(totals, 'refusal_')} on runs with a permission denial`
    : '';
  const prLabel = costPerPrLabel(totals);
  const prNote = prLabel == null ? '' : ` - ${prLabel}`;
  foot.appendChild(
    textLine(
      `${plural(totals.boards, 'board')} - ${plural(totals.cards, 'card')} - ${plural(totals.runs, 'run')} - ` +
        `${spendLabel(totals, '', totals.cost_estimated)}${prNote}${wasteNote}`,
      'stat'
    )
  );
  const roles = `worker ${spendLabel(totals, 'worker_', totals.worker_cost_estimated)} - reviewer ${spendLabel(totals, 'reviewer_', totals.reviewer_cost_estimated)}`;
  foot.appendChild(textLine(roles, 'stat'));
  const byLab = new Map();
  (totals.spend_by_model || []).forEach(row => {
    const lab = row.model.includes('/') ? row.model.split('/')[0] : 'anthropic';
    if (!byLab.has(lab)) byLab.set(lab, []);
    byLab.get(lab).push(row);
  });
  byLab.forEach((models, lab) => {
    const values = models.map(m => `${m.model} ${spendLabel(m, '', m.cost_estimated)}`).join(', ');
    foot.appendChild(textLine(`${lab} - ${values}`, 'stat'));
  });
  foot.appendChild(textLine(
    `mission control and fold turns ${spendLabel(totals, 'turn_', totals.turn_cost_estimated)} - not in the card totals above`,
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
  const total = knownSpend(data.totals) || 0;
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
  data.boards.forEach(row => rows.appendChild(boardCostRow(row, total > 0 ? (knownSpend(row) || 0) / total : 0)));
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
  if (!cv.overview) return waitingPlaceholder('loading');
  if (cv.overview.error) return costsHazardNode(`could not load costs: ${cv.overview.error}`);
  return costsOverviewSections(cv.overview)[0].node;
}

function costsOptimisationNode() {
  if (!cv.optimisation) return waitingPlaceholder('loading');
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
      sections: [{kind: 'node', node: waitingPlaceholder('loading')}],
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
