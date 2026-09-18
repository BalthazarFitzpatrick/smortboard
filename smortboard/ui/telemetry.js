// cost telemetry (i): a card's own attempts when one is focused or open, else the board's cost
// table. reuses board.js's globals - api, escapeHtml, fillBar, textLine, actionableCardId,
// openCard, currentBoardId, toggleOverlay, Menu - rather than redeclaring any of them.

function formatUsd(n, estimated = false) {
  return n == null ? 'unknown' : `${estimated ? '~' : ''}$${n.toFixed(2)}`;
}

function refusalLabel(count) {
  if (!count) return null;
  return count === 1 ? '1 refusal' : `${count} refusals`;
}

// A CARD'S LANGUAGE: ruled sections, oldest attempt first, a fill bar for each attempt's share of
// the card's total spend - same shape usageCard uses for spend-by-model
function telemetryAttemptSection(attempt, cardTotal) {
  const section = document.createElement('div');
  section.className = 'usage-section telemetry-attempt';
  const when = attempt.started_at ? new Date(attempt.started_at).toLocaleString() : 'unknown time';
  section.appendChild(textLine(when, 'field-label'));

  const share = cardTotal > 0 && attempt.cost_usd != null ? attempt.cost_usd / cardTotal : null;
  // named by role: the same model twice read as a typo
  const modelBits = [
    attempt.worker_model ? `worker ${attempt.worker_model}` : null,
    attempt.reviewer_model ? `reviewer ${attempt.reviewer_model}` : null,
  ].filter(Boolean);
  const modelNote = modelBits.length ? ` - ${modelBits.join(', ')}` : '';
  section.appendChild(textLine(`${formatUsd(attempt.cost_usd, attempt.cost_estimated)}${modelNote}`, 'usage-model-name'));
  if (share != null) section.appendChild(fillBar(share, attempt.refusal_count ? 'warn' : ''));

  const statBits = [
    `${attempt.turns} turns`,
    attempt.fix_rounds ? `${attempt.fix_rounds} fix round${attempt.fix_rounds === 1 ? '' : 's'}` : null,
    refusalLabel(attempt.refusal_count),
    attempt.outcome,
  ].filter(Boolean);
  section.appendChild(textLine(statBits.join(' - '), 'stat'));

  if (attempt.refusals && attempt.refusals.length) {
    const list = document.createElement('ul');
    list.className = 'telemetry-refusals';
    attempt.refusals.forEach(r => {
      const li = document.createElement('li');
      li.textContent = `${r.tool}: ${r.target}`;
      list.appendChild(li);
    });
    section.appendChild(list);
  }
  return section;
}

function telemetryCard(data) {
  const card = document.createElement('div');
  card.className = 'usage-card telemetry-card';

  if (!data.attempts.length) {
    const box = document.createElement('div');
    box.className = 'hazard-stripes hazard-placeholder';
    box.innerHTML = '<span class="hazard-label">this card has not run yet</span>';
    card.appendChild(box);
    return card;
  }

  data.attempts.forEach((attempt, i) => {
    if (i) card.appendChild(textLine('', 'h-divider'));
    card.appendChild(telemetryAttemptSection(attempt, data.totals.cost_usd));
  });

  card.appendChild(textLine('', 'h-divider'));
  const foot = document.createElement('div');
  foot.className = 'card-foot usage-foot';
  const wasteNote = data.totals.refusal_cost_usd
    ? ` - ${formatUsd(data.totals.refusal_cost_usd)} on runs with refusals`
    : '';
  foot.appendChild(
    textLine(`${data.totals.attempts} attempt${data.totals.attempts === 1 ? '' : 's'} - ${formatUsd(data.totals.cost_usd, data.totals.cost_estimated)}${wasteNote}`, 'stat')
  );
  card.appendChild(foot);
  return card;
}

async function loadCardTelemetry(menu, cardId) {
  try {
    const data = await api(`/api/cards/${cardId}/telemetry`);
    menu.title = `telemetry: ${data.title}`;
    menu.refresh([{kind: 'node', node: telemetryCard(data)}]);
  } catch (err) {
    menu.refresh([{kind: 'list', items: [], empty: `could not load telemetry: ${err.message}`}]);
  }
}

// costliest first, one row per card - the board's own cost table
function costTable(rows) {
  const table = document.createElement('div');
  table.className = 'cost-table';
  if (!rows.length) {
    const box = document.createElement('div');
    box.className = 'hazard-stripes hazard-placeholder';
    box.innerHTML = '<span class="hazard-label">no cards on this board yet</span>';
    table.appendChild(box);
    return table;
  }
  const total = rows.some(r => r.cost_usd == null) ? null : rows.reduce((sum, r) => sum + r.cost_usd, 0);
  const refusalTotal = rows.reduce((sum, r) => sum + (r.refusal_cost_usd || 0), 0);

  let lastLab = null;
  rows.slice().sort((a, b) => (a.lab || 'anthropic').localeCompare(b.lab || 'anthropic')).forEach((row, i) => {
    if (i) table.appendChild(textLine('', 'h-divider'));
    const lab = row.lab || 'anthropic';
    if (lab !== lastLab) table.appendChild(textLine(lab, 'field-label'));
    lastLab = lab;
    const section = document.createElement('div');
    section.className = 'usage-section cost-row';
    section.appendChild(textLine(row.title, 'field-label'));
    const share = total > 0 ? (row.cost_usd || 0) / total : null;
    section.appendChild(
      textLine(`${formatUsd(row.cost_usd, row.cost_estimated)} - model: ${modelLabel(row.model, row.lab)}`, 'usage-model-name')
    );
    if (share != null) section.appendChild(fillBar(share, row.refusal_count ? 'warn' : ''));
    const statBits = [
      `${row.attempts} attempt${row.attempts === 1 ? '' : 's'}`,
      refusalLabel(row.refusal_count),
      row.last_outcome,
    ].filter(Boolean);
    section.appendChild(textLine(statBits.join(' - '), 'stat'));
    table.appendChild(section);
  });

  table.appendChild(textLine('', 'h-divider'));
  const foot = document.createElement('div');
  foot.className = 'card-foot usage-foot';
  const wasteNote = refusalTotal ? ` - ${formatUsd(refusalTotal)} on runs with refusals` : '';
  foot.appendChild(textLine(`${rows.length} cards - ${formatUsd(total, rows.some(row => row.cost_estimated))}${wasteNote}`, 'stat'));
  table.appendChild(foot);
  return table;
}

async function loadBoardCosts(menu, boardId) {
  try {
    const rows = await api(`/api/boards/${boardId}/costs`);
    menu.refresh([{kind: 'node', node: costTable(rows)}]);
  } catch (err) {
    menu.refresh([{kind: 'list', items: [], empty: `could not load costs: ${err.message}`}]);
  }
}

// a card focused or open drives its own telemetry; otherwise the board's cost table
function openTelemetryPanel() {
  toggleOverlay('KeyI', () => {
    const cardId = actionableCardId();
    const menu = new Menu({
      title: cardId ? 'telemetry' : 'costs',
      sections: [{kind: 'list', items: [], empty: 'loading...'}],
      onDismiss: () => { if (openOverlay && openOverlay.key === 'KeyI') openOverlay = null; },
    });
    menu.openAt({x: window.innerWidth / 2 - 200, y: 80});
    menu.el?.classList.add('menu-centered');
    if (cardId) loadCardTelemetry(menu, cardId);
    else if (currentBoardId) loadBoardCosts(menu, currentBoardId);
    else menu.refresh([{kind: 'list', items: [], empty: 'no board selected'}]);
    return menu;
  });
}
