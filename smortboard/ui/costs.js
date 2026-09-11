// board cost overview (c): every board's spend in one panel, costliest first, with a totals row.
// reuses telemetry.js's formatUsd/refusalLabel and board.js's globals - api, escapeHtml, fillBar,
// textLine, toggleOverlay, Menu, activateTab, onBoardEnter - rather than redeclaring any of them.

// ui_base's fill bar rendered as a markup string, since a list item's label/stats are innerHTML -
// same classes fillBar() builds as a node, .progress-row > .bar > .bar-fill
function fillBarHtml(fraction, tone = '') {
  const pct = Math.round(fraction * 1000) / 10;
  const fillClass = tone ? `bar-fill ${tone}` : 'bar-fill';
  return `<div class="progress-row"><div class="bar"><div class="${fillClass}" style="width:${pct}%"></div></div></div>`;
}

function costPerPrLabel(row) {
  return row.cost_per_pr_usd == null ? null : `${formatUsd(row.cost_per_pr_usd)} / pr`;
}

// one board's row: title line, fill bar for its share of total spend, then the figures - same
// ruled-section language as the cost table, but as a list item so enter/arrows and onPick work
function boardRowLabel(row, share) {
  const modelBits = row.spend_by_model
    .slice()
    .sort((a, b) => b.cost_usd - a.cost_usd)
    .map(m => `${escapeHtml(m.model)} ${formatUsd(m.cost_usd)}`)
    .join(', ');
  const lines = [
    `<span class="field-label">${escapeHtml(row.board_name)}</span>`,
    `<span class="usage-model-name">${formatUsd(row.cost_usd)}</span>`,
    fillBarHtml(share, row.refusal_cost_usd ? 'warn' : ''),
  ];
  const statBits = [
    `${row.runs} run${row.runs === 1 ? '' : 's'}`,
    `${row.cards_accepted}/${row.cards} accepted`,
    `${row.pull_requests_opened} pr${row.pull_requests_opened === 1 ? '' : 's'}`,
    `worker ${formatUsd(row.worker_cost_usd)} - reviewer ${formatUsd(row.reviewer_cost_usd)}`,
    refusalLabel(row.refusal_count),
    costPerPrLabel(row),
  ].filter(Boolean);
  lines.push(`<span class="stat">${statBits.join(' - ')}</span>`);
  if (modelBits) lines.push(`<span class="stat cost-models">${modelBits}</span>`);
  return lines.join('');
}

function totalsFoot(totals) {
  const foot = document.createElement('div');
  foot.className = 'card-foot usage-foot';
  const wasteNote = totals.refusal_cost_usd
    ? ` - ${formatUsd(totals.refusal_cost_usd)} on runs with refusals`
    : '';
  const prNote = totals.cost_per_pr_usd == null ? '' : ` - ${costPerPrLabel(totals)}`;
  foot.appendChild(
    textLine(
      `${totals.boards} boards - ${totals.cards} cards - ${totals.runs} runs - ` +
        `${formatUsd(totals.cost_usd)}${prNote}${wasteNote}`,
      'stat'
    )
  );
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
  const items = data.boards.map(row => ({
    id: row.board_id,
    label: boardRowLabel(row, total > 0 ? row.cost_usd / total : 0),
    stats: '',
  }));
  const note = textLine('mission-control turns are not counted - the log carries no cost for them', 'stat');
  return [
    {kind: 'node', node: note},
    {kind: 'list', items, onPick: item => jumpToBoard(item.id)},
    {kind: 'node', node: totalsFoot({...data.totals, boards: data.boards.length})},
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
