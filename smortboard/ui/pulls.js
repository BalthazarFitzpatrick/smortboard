// pull requests (v): every open PR across every board, in merge order - the inbox for what is left
// to land, cross-board like the attention inbox (inbox.js). built from the same modal-backdrop /
// panel-floating pair, not a Menu, for the same reason inbox.js gives: nothing here needs
// arrow/enter/escape hijacked from the document.
//
// row selection is tracked as pl.activeIndex, never real dom focus - a focused row carrying
// data-card-id would match board.js's focusedCardId() ([data-card-id] anywhere in the ancestor
// chain), which is how every single-letter card shortcut (r, x, del, m, ...) finds its target. a
// focused pull-request row would then take r, x and friends meant for the board underneath it -
// see acceptActivePullsRow, which posts accept itself rather than leaning on that lookup.
//
// relies on globals board.js already defines: api, apiOrError, onBoardEnter, currentBoardId,
// reenterIfFocusLost.

const pl = {backdrop: null, panel: null, listEl: null, rows: [], activeIndex: 0, confirming: false};

function buildPullsDom() {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop pulls-backdrop';
  const panel = document.createElement('div');
  panel.className = 'panel-floating pulls-panel';

  const title = document.createElement('div');
  title.className = 'popup-title';
  title.textContent = 'pull requests';
  const rule = document.createElement('div');
  rule.className = 'h-divider';

  const list = document.createElement('div');
  list.className = 'pulls-list';

  panel.append(title, rule, list);
  backdrop.appendChild(panel);
  backdrop.addEventListener('mousedown', evt => { if (evt.target === backdrop) closePullsPanel(); });

  Object.assign(pl, {backdrop, panel, listEl: list});
  return backdrop;
}

function pullsHazard(text) {
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

function pullsMarker(text, className) {
  const el = document.createElement('span');
  el.className = `stat pulls-marker ${className}`;
  el.textContent = text;
  return el;
}

// built with createElement/appendChild, not innerHTML - the test dom stub does not parse innerHTML
// strings back into a tree, same reason inbox.js's rows are built this way
function buildPullsRow(row) {
  const rowEl = document.createElement('div');
  rowEl.className = 'pulls-row';

  rowEl.appendChild(document.createElement('div')).className = 'h-divider';

  const head = document.createElement('div');
  head.className = 'pulls-row-head';
  const title = document.createElement('span');
  title.className = 'pulls-title';
  title.textContent = row.title;
  const board = document.createElement('span');
  board.className = 'field-label pulls-board';
  board.textContent = row.board_name;
  const state = document.createElement('span');
  state.className = 'field-label pulls-state';
  state.textContent = row.state || 'unknown';
  head.append(title, board, state);

  const link = document.createElement('a');
  link.href = safeUrl(row.url);
  link.target = '_blank';
  link.rel = 'noreferrer';
  link.className = 'stat pulls-link';
  link.textContent = row.url;

  const markers = document.createElement('div');
  markers.className = 'pulls-markers';
  if (row.conflicting) markers.appendChild(pullsMarker('conflicting', 'pulls-marker-conflicting'));
  if (row.dependency_unmerged) {
    markers.appendChild(pullsMarker('dependency not merged yet', 'pulls-marker-dependency'));
  }

  const status = document.createElement('span');
  status.className = 'pulls-status';

  rowEl.append(head, link, markers, status);
  return rowEl;
}

function pullsRowEls() {
  return [...pl.listEl.querySelectorAll('.pulls-row')];
}

function setActiveIndex(index) {
  if (!pl.rows.length) return;
  pl.activeIndex = Math.max(0, Math.min(pl.rows.length - 1, index));
  pullsRowEls().forEach((el, i) => el.classList.toggle('pulls-row-active', i === pl.activeIndex));
}

function renderPulls() {
  clearChildren(pl.listEl);
  if (!pl.rows.length) {
    pl.listEl.appendChild(pullsHazard('nothing open right now'));
    return;
  }
  pl.activeIndex = Math.min(pl.activeIndex, pl.rows.length - 1);
  pl.rows.forEach(row => pl.listEl.appendChild(buildPullsRow(row)));
  setActiveIndex(pl.activeIndex);
}

async function loadPulls() {
  clearChildren(pl.listEl);
  pl.listEl.appendChild(pullsHazard('loading...'));
  try {
    pl.rows = await api('/api/pulls');
  } catch (err) {
    clearChildren(pl.listEl);
    pl.listEl.appendChild(pullsHazard(`could not load: ${err.message}`));
    return;
  }
  pl.activeIndex = 0;
  renderPulls();
}

// enter opens the active row's pull request in a new tab - never anchor.click(), so this works the
// same whether the row was built with a real <a> or (in tests) a dom stub that has none
function openActivePullsRowPR() {
  const row = pl.rows[pl.activeIndex];
  if (!row || !safeUrl(row.url)) return;
  window.open?.(row.url, '_blank', 'noopener');
}

// y accepts the row's card directly, through the same route the board's own y does
// (/api/cards/{id}/accept) - reimplemented here rather than borrowed from board.js's
// acceptOrRejectCard because that reads the focused card-strip, and nothing in this panel is one.
// confirmed the same way board.js's y is: openActionConfirm, not window.confirm
function acceptActivePullsRow() {
  const index = pl.activeIndex;
  const row = pl.rows[index];
  if (!row) return;
  pl.confirming = true;
  openActionConfirm('accept this pull request?', 'accept', 'cancel',
    () => doAcceptActivePullsRow(index, row), () => { pl.confirming = false; });
}

async function doAcceptActivePullsRow(index, row) {
  const status = pullsRowEls()[index]?.querySelector('.pulls-status');
  const {ok, body} = await apiOrError(`/api/cards/${row.card_id}/accept`, {method: 'POST'});
  if (!ok) {
    if (status) {
      status.textContent = (body && body.error) || 'could not accept';
      status.className = 'pulls-status pulls-error';
    }
    return;
  }
  if (currentBoardId) onBoardEnter(currentBoardId);
  // stays on the list until gh reports it merged - reloading just picks up its fresh state
  await loadPulls();
}

function onPullsKey(evt) {
  // while the accept confirm is up, its own Menu keydown handler owns every key - this must not
  // also close the whole panel on the same Escape that is cancelling just the confirm
  if (pl.confirming) return;
  if (evt.code === 'Escape') { closePullsPanel(); return; }
  if (evt.code === 'ArrowDown') { evt.preventDefault(); setActiveIndex(pl.activeIndex + 1); return; }
  if (evt.code === 'ArrowUp') { evt.preventDefault(); setActiveIndex(pl.activeIndex - 1); return; }
  if (evt.code === 'Enter') { evt.preventDefault(); openActivePullsRowPR(); return; }
  if (evt.code === 'KeyY') { evt.preventDefault(); acceptActivePullsRow(); return; }
}

function openPullsPanel() {
  if (!pl.backdrop) buildPullsDom();
  document.body.appendChild(pl.backdrop);
  document.addEventListener('keydown', onPullsKey);
  loadPulls();
}

function closePullsPanel() {
  if (!pl.backdrop || !pl.backdrop.parentNode) return;
  document.removeEventListener('keydown', onPullsKey);
  pl.backdrop.remove();
  reenterIfFocusLost();
}

function togglePullsPanel() {
  if (pl.backdrop && pl.backdrop.parentNode) closePullsPanel();
  else openPullsPanel();
}
