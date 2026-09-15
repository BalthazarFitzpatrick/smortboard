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
  foot.appendChild(textLine('mission-control turns are not counted - the log carries no cost for them', 'stat cost-note'));
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

async function loadBoardsOverview(menu) {
  try {
    const data = await api('/api/costs');
    menu.refresh(costsOverviewSections(data));
  } catch (err) {
    menu.refresh([{kind: 'list', items: [], empty: `could not load costs: ${err.message}`}]);
  }
}

function openCostsOverviewPanel() {
  toggleOverlay('KeyC', () => {
    const menu = new Menu({
      title: 'cost overview',
      sections: [{kind: 'list', items: [], empty: 'loading...'}],
      onDismiss: () => { if (openOverlay && openOverlay.key === 'KeyC') openOverlay = null; },
    });
    menu.openAt({x: window.innerWidth / 2 - 200, y: 80});
    menu.el?.classList.add('menu-centered');
    loadBoardsOverview(menu);
    return menu;
  });
}
