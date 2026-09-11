// ---- RUN REPLAY (t) - scrub a card's run step by step -------------------------------------------
// NOT a Menu, same reasoning as the prompt editor: arrow keys are how you scrub the timeline, so a
// component that eats ArrowUp/Down/Escape for its own list navigation would fight the panel's own.
// built from the same .modal-backdrop / .panel-floating pair board.js's prompt editor rides.
//
// depends on globals from board.js, loaded first: api(), escapeHtml(), actionableCardId(), openCard.

const rp = {
  backdrop: null, panel: null, rail: null, detail: null, attemptRow: null,
  cardId: null, attempts: [], steps: [], index: 0, playTimer: null,
};

function replayStepLabel(step) {
  return `${step.seq}. ${step.label}`;
}

// hollow = no verdict, lichen = ok, stone red = refused/failed - the same language the card
// panel's own run timeline already uses (see .outcome-part in layout.css)
function replayMarkerClass(step) {
  if (step.ok === true) return 'ok';
  if (step.ok === false) return 'fail';
  return 'none';
}

function buildReplayDom() {
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop replay-backdrop';
  const panel = document.createElement('div');
  panel.className = 'panel-floating replay-panel';

  const title = document.createElement('div');
  title.className = 'popup-title';
  title.textContent = 'run replay';

  const attemptRow = document.createElement('div');
  attemptRow.className = 'replay-attempts';

  const rule = document.createElement('div');
  rule.className = 'h-divider';

  const body = document.createElement('div');
  body.className = 'replay-body';
  const rail = document.createElement('div');
  rail.className = 'replay-rail';
  const detail = document.createElement('div');
  detail.className = 'replay-detail';
  body.append(rail, detail);

  const footer = document.createElement('div');
  footer.className = 'replay-footer';
  const play = document.createElement('div');
  play.className = 'toggle replay-play';
  play.textContent = 'play';
  play.onclick = () => toggleReplayPlay();
  const hint = document.createElement('span');
  hint.className = 'replay-hint';
  hint.textContent = 'j/k or up/down to step - home/end to jump - esc to close';
  footer.append(play, hint);

  panel.append(title, attemptRow, rule, body, footer);
  backdrop.appendChild(panel);
  backdrop.addEventListener('mousedown', evt => { if (evt.target === backdrop) closeReplay(); });
  // every key here is the panel's own; nothing should reach board.js's global keydown handler
  panel.addEventListener('keydown', onReplayPanelKey);

  Object.assign(rp, {backdrop, panel, rail, detail, attemptRow, playEl: play});
  return backdrop;
}

function renderReplayAttempts() {
  rp.attemptRow.innerHTML = '';
  rp.attempts.forEach(a => {
    const btn = document.createElement('div');
    btn.className = 'toggle replay-attempt' + (a.attempt === rp.currentAttempt ? ' on' : '');
    btn.textContent = `attempt ${a.attempt}${a.reached_worker ? '' : ' (refused)'}`;
    btn.onclick = () => loadReplay(rp.cardId, a.attempt);
    rp.attemptRow.appendChild(btn);
  });
}

function renderReplayRail() {
  rp.rail.innerHTML = '';
  rp.steps.forEach((step, i) => {
    const row = document.createElement('div');
    row.className = 'replay-step' + (i === rp.index ? ' on' : '');
    row.dataset.index = String(i);
    const marker = document.createElement('span');
    marker.className = `replay-marker replay-marker-${replayMarkerClass(step)}`;
    const label = document.createElement('span');
    label.className = 'replay-step-label';
    label.textContent = replayStepLabel(step);
    row.append(marker, label);
    row.onclick = () => { stopReplayPlay(); setReplayIndex(i); };
    rp.rail.appendChild(row);
  });
}

function replayDiffLines(oldText, newText) {
  // a plain line-oriented diff, not a real LCS - good enough for a preview of what changed, and
  // cheap enough to run on every scrub. every removed line first, then every added line
  const oldLines = oldText.split('\n');
  const newLines = newText.split('\n');
  let common = 0;
  while (common < oldLines.length && common < newLines.length && oldLines[common] === newLines[common]) common++;
  let tailCommon = 0;
  while (
    tailCommon < oldLines.length - common &&
    tailCommon < newLines.length - common &&
    oldLines[oldLines.length - 1 - tailCommon] === newLines[newLines.length - 1 - tailCommon]
  ) tailCommon++;
  const removed = oldLines.slice(common, oldLines.length - tailCommon);
  const added = newLines.slice(common, newLines.length - tailCommon);
  return {removed, added};
}

function renderReplayDetail() {
  const step = rp.steps[rp.index];
  if (!step) { rp.detail.innerHTML = '<div class="empty">no steps</div>'; return; }
  const d = step.detail || {};
  const parts = [];
  parts.push(`<div class="replay-detail-head"><span class="replay-detail-kind">${escapeHtml(step.kind)}</span>` +
    `<span class="replay-detail-time">${escapeHtml(step.created_at || '')}</span></div>`);

  if (step.kind === 'edit') {
    const {removed, added} = replayDiffLines(d.old_text || '', d.new_text || '');
    parts.push(`<div class="replay-field">${escapeHtml(d.file_path || '')}</div>`);
    parts.push('<pre class="replay-diff">' +
      removed.map(l => `<span class="replay-diff-line replay-diff-removed">- ${escapeHtml(l)}</span>`).join('\n') +
      (removed.length && added.length ? '\n' : '') +
      added.map(l => `<span class="replay-diff-line replay-diff-added">+ ${escapeHtml(l)}</span>`).join('\n') +
      '</pre>');
    if (d.old_truncated || d.new_truncated) parts.push('<div class="replay-note">truncated</div>');
  } else if (step.kind === 'write') {
    parts.push(`<div class="replay-field">${escapeHtml(d.file_path || '')}</div>`);
    parts.push(`<pre class="replay-pre">${escapeHtml(d.preview || '')}</pre>`);
    if (d.truncated) parts.push('<div class="replay-note">truncated</div>');
  } else if (step.kind === 'read') {
    parts.push(`<div class="replay-field">${escapeHtml(d.file_path || '')}</div>`);
  } else if (step.kind === 'run') {
    parts.push(`<pre class="replay-pre">${escapeHtml(d.command || '')}</pre>`);
    if (d.output) parts.push(`<pre class="replay-pre replay-output">${escapeHtml(d.output)}</pre>`);
  } else if (step.kind === 'search') {
    parts.push(`<div class="replay-field">${escapeHtml(d.pattern || '')}${d.path ? ` in ${escapeHtml(d.path)}` : ''}</div>`);
  } else if (step.kind === 'refused') {
    parts.push(`<div class="replay-field replay-refused">${escapeHtml(d.reason || 'refused')}</div>`);
    if (d.output) parts.push(`<pre class="replay-pre">${escapeHtml(d.output)}</pre>`);
  } else if (step.kind === 'narration' || step.kind === 'summary') {
    parts.push(`<div class="replay-text">${escapeHtml(d.text || '')}</div>`);
  } else if (step.kind === 'test') {
    parts.push(`<div class="replay-field">${escapeHtml(d.command || '')} (exit ${d.exit_code})</div>`);
    if (d.output) parts.push(`<pre class="replay-pre">${escapeHtml(d.output)}</pre>`);
  } else if (step.kind === 'review') {
    if (d.error) parts.push(`<div class="replay-field">${escapeHtml(d.error)}</div>`);
    if ((d.findings || []).length) {
      parts.push('<ul class="replay-findings">' +
        d.findings.map(f => `<li>${escapeHtml(f.severity || '')} ${escapeHtml(f.category || '')}: ${escapeHtml(f.message || '')}</li>`).join('') +
        '</ul>');
    }
  } else if (step.kind === 'pull request') {
    if (d.url) parts.push(`<a href="${escapeHtml(d.url)}" target="_blank" rel="noopener">${escapeHtml(d.url)}</a>`);
    if (d.refusal) parts.push(`<div class="replay-field">${escapeHtml(d.refusal)}</div>`);
  } else if (step.kind === 'decision') {
    parts.push(`<div class="replay-field">${escapeHtml(d.decision || '')}</div>`);
  } else if (step.kind === 'fix round') {
    parts.push(`<div class="replay-field">round ${escapeHtml(String(d.round || ''))}</div>`);
  } else {
    parts.push(`<pre class="replay-pre">${escapeHtml(JSON.stringify(d.input || d, null, 2))}</pre>`);
  }

  rp.detail.innerHTML = parts.join('');
}

function setReplayIndex(i) {
  if (!rp.steps.length) return;
  rp.index = Math.max(0, Math.min(i, rp.steps.length - 1));
  [...rp.rail.children].forEach((row, idx) => row.classList.toggle('on', idx === rp.index));
  rp.rail.children[rp.index]?.scrollIntoView?.({block: 'nearest'});
  renderReplayDetail();
}

function stopReplayPlay() {
  if (rp.playTimer) { clearInterval(rp.playTimer); rp.playTimer = null; }
  rp.playEl?.classList.remove('on');
  if (rp.playEl) rp.playEl.textContent = 'play';
}

function toggleReplayPlay() {
  if (rp.playTimer) { stopReplayPlay(); return; }
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  rp.playEl.classList.add('on');
  rp.playEl.textContent = 'pause';
  const step = () => {
    if (rp.index >= rp.steps.length - 1) { stopReplayPlay(); return; }
    setReplayIndex(rp.index + 1);
  };
  rp.playTimer = setInterval(step, reduced ? 0 : 600);
}

async function loadReplay(cardId, attempt) {
  try {
    const query = attempt ? `?attempt=${attempt}` : '';
    const data = await api(`/api/cards/${cardId}/timeline${query}`);
    rp.cardId = cardId;
    rp.attempts = data.attempts || [];
    rp.currentAttempt = data.attempt;
    rp.steps = data.steps || [];
    rp.index = 0;
    renderReplayAttempts();
    renderReplayRail();
    if (!rp.steps.length) rp.detail.innerHTML = '<div class="empty">no steps in this attempt</div>';
    else setReplayIndex(0);
  } catch (err) {
    rp.rail.innerHTML = '';
    rp.detail.innerHTML = `<div class="empty">could not load replay: ${escapeHtml(err.message)}</div>`;
  }
}

function onReplayPanelKey(evt) {
  evt.stopPropagation();
  if (evt.code === 'Escape') { closeReplay(); return; }
  if (evt.target.matches?.('input, textarea')) return;
  if (evt.code === 'ArrowDown' || evt.code === 'KeyJ') { evt.preventDefault(); stopReplayPlay(); setReplayIndex(rp.index + 1); return; }
  if (evt.code === 'ArrowUp' || evt.code === 'KeyK') { evt.preventDefault(); stopReplayPlay(); setReplayIndex(rp.index - 1); return; }
  if (evt.code === 'Home') { evt.preventDefault(); stopReplayPlay(); setReplayIndex(0); return; }
  if (evt.code === 'End') { evt.preventDefault(); stopReplayPlay(); setReplayIndex(rp.steps.length - 1); return; }
  if (evt.code !== 'Escape') stopReplayPlay();
}

function openReplay() {
  const cardId = actionableCardId();
  if (!cardId) return;
  if (!rp.backdrop) buildReplayDom();
  document.body.appendChild(rp.backdrop);
  rp.panel.tabIndex = -1;
  rp.panel.focus();
  loadReplay(cardId);
}

function closeReplay() {
  if (!rp.backdrop || !rp.backdrop.parentNode) return;
  stopReplayPlay();
  rp.backdrop.remove();
  reenterIfFocusLost();
}

function toggleReplay() {
  if (rp.backdrop && rp.backdrop.parentNode) closeReplay();
  else openReplay();
}
