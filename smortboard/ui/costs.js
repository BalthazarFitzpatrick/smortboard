// board cost overview (c): every board's spend in one panel, costliest first, with a totals foot.
// the usage panel's language - a wide card of ruled sections with fill bars - plus a compact list
// underneath so arrows and enter still jump to a board. reuses board.js and telemetry.js globals:
// api, escapeHtml, fillBar, textLine, usageSection, formatUsd, refusalLabel, toggleOverlay, Menu.

function costPerPrLabel(row) {
  return row.cost_per_pr_usd == null ? null : `${formatUsd(row.cost_per_pr_usd)} / pr`;
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

// one board: name as the section label, spend and its share, a neutral fill bar, then the figures
function boardCostSection(row, share) {
  const section = usageSection(row.board_name, 'cost-board');
  section.appendChild(textLine(`${formatUsd(row.cost_usd)} - ${Math.round(share * 100)}% of spend`, 'usage-model-name'));
  section.appendChild(fillBar(share));
  const statBits = [
    plural(row.runs, 'run'),
    `${row.cards_accepted}/${row.cards} accepted`,
    plural(row.pull_requests_opened, 'pr'),
    costPerPrLabel(row),
  ].filter(Boolean);
  section.appendChild(textLine(statBits.join(' - '), 'stat'));
  const roleBits = [`worker ${formatUsd(row.worker_cost_usd)}`, `reviewer ${formatUsd(row.reviewer_cost_usd)}`];
  // the row carries the spend, not a count - the waste is what matters at board level
  if (row.refusal_cost_usd) roleBits.push(`${formatUsd(row.refusal_cost_usd)} on runs with refusals`);
  section.appendChild(textLine(roleBits.join(' - '), 'stat'));
  const models = row.spend_by_model
    .slice()
    .sort((a, b) => b.cost_usd - a.cost_usd)
    .map(m => `${m.model} ${formatUsd(m.cost_usd)}`)
    .join(', ');
  if (models) section.appendChild(textLine(models, 'stat'));
  return section;
}

function totalsFoot(totals) {
  const foot = document.createElement('div');
  foot.className = 'card-foot usage-foot cost-foot';
  const wasteNote = totals.refusal_cost_usd
    ? ` - ${formatUsd(totals.refusal_cost_usd)} on runs with refusals`
    : '';
  const prNote = totals.cost_per_pr_usd == null ? '' : ` - ${costPerPrLabel(totals)}`;
  foot.appendChild(
    textLine(
      `${plural(totals.boards, 'board')} - ${plural(totals.cards, 'card')} - ${plural(totals.runs, 'run')} - ` +
        `${formatUsd(totals.cost_usd)}${prNote}${wasteNote}`,
      'stat'
    )
  );
  foot.appendChild(textLine('mission-control turns are not counted - the log carries no cost for them', 'stat cost-note'));
  return foot;
}

// jumping to a board row does exactly what clicking its tab would: switch and let the board render
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
  const grid = document.createElement('div');
  grid.className = 'cost-boards';
  data.boards.forEach(row => grid.appendChild(boardCostSection(row, total > 0 ? row.cost_usd / total : 0)));
  card.appendChild(grid);
  card.appendChild(textLine('', 'h-divider'));
  card.appendChild(totalsFoot({...data.totals, boards: data.boards.length}));
  const items = data.boards.map(row => ({
    id: row.board_id,
    label: escapeHtml(row.board_name),
    stats: formatUsd(row.cost_usd),
  }));
  return [
    {kind: 'node', node: card},
    {kind: 'list', label: 'jump to a board', items, onPick: item => jumpToBoard(item.id)},
  ];
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
