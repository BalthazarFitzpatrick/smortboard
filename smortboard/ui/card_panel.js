// one card's own rendering and actions - the strip, the open panel, the CTA, the short id, and the
// overflow/model/status/delete menus a card offers. split out of board.js so a card-ui card can
// lease this file alone; board.js wires it in (renderBuckets calls renderCardStrip) and it reaches
// back into board.js globals (openCard, currentBoardId, api, showRun, actionableCardId,
// returnToBoardBar, onBoardEnter, escapeHtml) the same way every other split file does.

// ---- PR references as real links, wherever a card or a chat line shows one ------------------
// display only: this never touches card state, it only decides how a pr_url (or, once a card
// carries its own repo_url, a bare pr number) turns into an <a> instead of dead text. lives here
// (not board.js) because card_panel.js loads before chat.js, which also calls linkifyPrRefs

function isPrUrl(value) {
  return typeof value === 'string' && /^https?:\/\//.test(value);
}

// a full url renders as itself; a bare number needs the card's own repo_url to become a link at
// all - with neither, the ref still shows (as plain text) rather than vanishing or breaking
function prAnchorHtml(ref, repoUrl) {
  if (ref === null || ref === undefined || ref === '') return '';
  const url = isPrUrl(ref) ? ref : (repoUrl ? `${String(repoUrl).replace(/\/+$/, '')}/pull/${ref}` : null);
  const label = isPrUrl(ref) ? ref : `#${ref}`;
  return url
    ? `<a class="pr-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`
    : escapeHtml(label);
}

// a PR url sitting inside a longer line (a worker summary, a comment, a chat message) - matched
// lazily up to the first /pull/<number> so the rest of the sentence is untouched and still escaped
const PR_URL_IN_TEXT_RE = /https?:\/\/\S+?\/pull\/\d+/g;

function linkifyPrRefs(text) {
  if (!text) return '';
  const str = String(text);
  let out = '';
  let last = 0;
  for (const match of str.matchAll(PR_URL_IN_TEXT_RE)) {
    out += escapeHtml(str.slice(last, match.index)) + prAnchorHtml(match[0]);
    last = match.index + match[0].length;
  }
  return out + escapeHtml(str.slice(last));
}

// ---- buckets of card strips: per-card classes, CTA and the strip itself ---------------------

function cardClasses(card) {
  // NOT ui_base's fan: cards never overlap here. columns.js sizes and spaces every row itself, in
  // every column type, and the fan's own -80% margin was the second layout system fighting it
  // focus-glow is ui_base's whole focus treatment - the lift, the card's own inset ring, the inner
  // glow and the coloured light over the face - so a card focused in any column type wears it
  const classes = ['row', 'card', 'card-strip', 'focus-glow'];
  // blocked wins over working: a card waiting on you is not a card making progress, and showing
  // both reads as progress. flagged counts too - a refused or stopped card has no reason code
  // but sits in the inbox, and without this the board drew it plain
  // handled_by_board: the board is already retrying this itself, so it reads as working, not as
  // a thing waiting on operator - the glow is reserved for a card that actually needs him
  if (card.handled_by_board) classes.push('card-working');
  else if (card.blocked_reason_code || card.review_flag) classes.push('card-attention');
  else if (card.status === 'doing') classes.push('card-working');
  else if (card.status === 'rejected') classes.push('card-rejected');
  else if (card.status === 'accepted') classes.push('card-accepted');
  // PREVIEW ONLY - the fan, still being judged
  // PREVIEW - the fan, plus one treatment per column for telling a collapsed stack apart
  return classes.join(' ');
}

// a blocked card's CTA names the fix, not the bare reason code - the same instinct next_action_short
// already applies to the strip's footer, just aimed at the one button that matters
// a Map, not a plain object: blocked_reason_code is a server string, and a Map has no prototype
// chain for a stray value like "constructor" to fall through into
const CTA_BLOCKED_LABELS = new Map([
  ['CRASH', 'Investigate crash'],
  ['USAGE_LIMIT', 'Resume run'],
  ['LEASE_CONFLICT', 'Fix leases'],
  ['AGENT_QUESTION', 'Answer question'],
  ['TESTS_FAILED', 'Review failure'],
  ['REVIEW_REJECTED', 'Review findings'],
  ['DEPENDENCY_REJECTED', 'Review dependency'],
  ['MERGE_CONFLICT', 'Resolve conflicts'],
  ['API_UNREACHABLE', 'Retrying automatically'],
]);

// the one action a card wants next, off the same status and reason code cardClasses reads - never
// a second source of truth for what state a card is in. action is what the CTA's click performs;
// attention is whether it wears the waiting-on-you treatment
function ctaFor(card) {
  // the board is retrying this one on its own - the CTA says when, not that it needs a decision
  if (card.handled_by_board) {
    return {label: card.next || 'retrying automatically', action: 'open', attention: false};
  }
  if (card.blocked_reason_code) {
    return {label: CTA_BLOCKED_LABELS.get(card.blocked_reason_code) || 'Needs attention', action: 'open', attention: true};
  }
  if (card.review_flag) return {label: 'Needs attention', action: 'open', attention: true};
  if (card.status === 'doing') return {label: 'Running…', action: 'stop', attention: false};
  if (card.status === 'checking') return {label: 'Review PR', action: 'open', attention: false};
  if (card.status === 'accepted') return {label: 'View', action: 'open', attention: false};
  if (card.status === 'rejected') return {label: 'Rerun', action: 'run', attention: false};
  return {label: 'Run', action: 'run', attention: false};
}

// the first 8 chars of the id - short enough to say in conversation, long enough not to collide
function shortId(id) {
  return String(id).slice(0, 8);
}

const SUMMARY_WORD_LIMIT = 14;
// an ALL-CAPS line ending in a colon - "GOAL:", "OUT OF SCOPE:", "RULES:" - is a section header,
// not content, so it never survives into the overview's one-line summary
const SECTION_HEADER_RE = /^[A-Z][A-Z ]*:$/;

// the overview's plain <=14-word summary (9bd5a207): strips section headers and bullet markers,
// keeps the first content it finds, cut to the word limit. the opened card panel still shows the
// full description untouched - this is a derived view, never a second stored field
function summarizeDescription(description) {
  const words = [];
  for (const rawLine of String(description || '').split('\n')) {
    const line = rawLine.trim();
    if (!line || SECTION_HEADER_RE.test(line)) continue;
    const cleaned = line.replace(/^[-*]\s*/, '');
    for (const word of cleaned.split(/\s+/)) {
      if (!word) continue;
      words.push(word);
      if (words.length >= SUMMARY_WORD_LIMIT) break;
    }
    if (words.length >= SUMMARY_WORD_LIMIT) break;
  }
  return words.join(' ');
}

// the compact note's colour follows the action it names, not a flat grey line - attention wins
// (the same red the card's own border wears), otherwise doing/running reads as the working green,
// checking/accepted borrow the colour their own column state already uses elsewhere, and a plain
// todo/rejected note stays quiet text
function ctaColorClass(cta, card) {
  if (cta.attention) return 'card-action-attention';
  if (card.handled_by_board || cta.action === 'stop') return 'card-action-working';
  if (card.status === 'checking') return 'card-action-review';
  if (card.status === 'accepted') return 'card-action-accepted';
  return 'card-action-quiet';
}

function renderCardStrip(card) {
  const strip = document.createElement('div');
  strip.className = cardClasses(card);
  strip.tabIndex = -1;
  strip.dataset.cardId = card.id;
  // openMoveStatusMenu reads this back to grey out the column the card is already in
  strip.dataset.status = card.status;
  // a card, not a strip: a title band at the top, a rule, the description with the room, and the
  // secondary facts sitting on the floor. ui_base draws the rule with .h-divider - the parent
  // spaces its children and the rule only draws the line
  // the compact note IS the one thing worth reading - no separate full-width button (reverted,
  // see 3919ce56): coloured and bold in place, left-aligned ahead of the workstream.
  // next_action_short is the more specific guidance the board writes for a blocked card ("widen
  // or answer") - it wins over ctaFor's blunter reason-code label ("Fix leases") when both exist
  const cta = ctaFor(card);
  const actionClass = ctaColorClass(cta, card);
  const label = card.next_action_short || cta.label;
  const actionHtml = `<span class="card-action ${actionClass}" ` +
    `title="${escapeHtml(card.blocked_reason_code || card.next_action || '')}">${escapeHtml(label)}</span>`;
  strip.innerHTML = `
    <div class="card-head"><div class="card-title">${escapeHtml(card.title)}</div></div>
    <div class="h-divider"></div>
    <div class="card-body">${escapeHtml(summarizeDescription(card.description))}</div>
    <div class="h-divider"></div>
    <div class="card-foot">
      ${actionHtml}
      <span class="card-workstream">${escapeHtml(card.workstream || '')}</span>
      <span class="card-run" hidden></span>
    </div>
  `;

  // appended rather than templated into the string above: the test dom stub does not parse
  // innerHTML back into a tree (see the same note on terminalDom further down), so a live listener
  // needs a real node - appendChild gives one in the stub and in a real browser alike
  const overflow = document.createElement('span');
  overflow.className = 'toggle card-overflow';
  overflow.title = 'edit, delete, change model, move status';
  overflow.textContent = '⋯';
  // stop here - strip.addEventListener('click', open) (ui_base's expander) would otherwise also
  // open the card behind the menu, since a plain click bubbles up from this child
  overflow.onclick = evt => { evt.stopPropagation(); openCardOverflowMenu(card.id, overflow); };
  strip.appendChild(overflow);
  const expander = makeExpander(strip, {
    // THREE TIMES THE DEFAULT WIDTH. at 1:3 an open card was a narrow column that wrapped every
    // line of its outcome; makeExpander keeps the height and sizes width from the ratio, so 1:1
    // triples it - still capped at 90% of the viewport width
    expandedRatio: {w: 1, h: 1},
    // THE PLATE BECOMES A BORDER ON THE WAY OPEN. a panel painted with the plate would make a whole
    // screen of it, and the colour stops being a signal once it is the background you are reading
    // on - as an edge it survives the expansion without taking the panel over
    onOpen: panel => {
      cardClasses(card).split(' ')
        .filter(c => c.startsWith('card-') && c !== 'card-strip')
        .forEach(c => panel.classList.add(c));
      // set before the fetch, so space and y/x can close this card while it is still loading
      openCard = {cardId: card.id, expander};
      openCardPanel(panel, card.id);
    },
    onClose: () => { openCard = null; },
  });
  strip.addEventListener('keydown', evt => {
    if (withModifier(evt)) return;
    if (evt.code !== 'Enter' && evt.code !== 'NumpadEnter' && evt.code !== 'Space') return;
    evt.preventDefault();
    // the document handler also closes on space, so a press handled here stops here
    evt.stopPropagation();
    // SPACE TOGGLES. focus stays on the strip behind an open panel, so this handler sees the
    // second press too - it closes the card it opened
    if (evt.code === 'Space' && openCard?.expander === expander) expander.close();
    else expander.open();
  });
  strip._expander = expander;
  return strip;
}

// ---- card panel: the open card's sections, and the outcome they render -----------------------

async function openCardPanel(panel, cardId) {
  const [card, outcome] = await Promise.all([
    api(`/api/cards/${cardId}`),
    api(`/api/cards/${cardId}/outcome`),
  ]);
  // closed while loading - space or y/x can shut it before the fetch lands
  if (!panel.isConnected) return;
  panel.classList.add('card-panel');
  // DESIGN ARCHIVE - a card whose workstream is layout-N wears panel-layouts.css's variant N, so
  // the fifteen layouts behind the 2026-09-10 pick stay on their own board to look back on
  const layout = /^layout-(\d+)$/.exec(card.workstream || '');
  if (layout) panel.classList.add(`panel-layout-${layout[1]}`);
  panel.innerHTML = cardPanelHtml(card, outcome);
  // the design archive keeps its own historical grid per variant in panel-layouts.css - masonry
  // would fight it for the same inline top/left/width
  if (!layout) {
    layoutCardSections(panel);
    watchCardSections(panel);
  }

  // the panel's one .card-sections div is a single-column bucket - reuses the 2D grid nav as a
  // plain vertical list rather than inventing a second focus system for "move between sections"
  const sectionsApi = makeBuckets(panel, {bucketSel: '.card-sections', rowSel: '.card-section'});

  const input = panel.querySelector('.comment-input');
  // stop Escape here so the panel's own Escape (added by makeExpander) sees a still-open card and
  // only takes the input->panel step - the panel->closed step is makeExpander's own job
  input.addEventListener('keydown', evt => {
    if (evt.code === 'Escape') {
      evt.stopPropagation();
      // NOT input.blur(). blur drops focus on <body>, and from there every arrow key is dead - the
      // panel has to take it back so escape steps out of the input rather than out of the app
      const section = panel.querySelector('.card-section');
      if (section) section.focus(); else input.blur();
    }
    if (evt.code === 'Enter') {
      evt.preventDefault();
      const body = input.value.trim();
      if (body) api(`/api/cards/${cardId}/comments`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({author: 'operator', body}),
      }).then(() => openCardPanel(panel, cardId));
    }
  });

  // a board note's own primary action, where the CTA does something other than re-open this
  // already-open panel - run/stop reuse the same handlers the strip's CTA does
  panel.querySelectorAll('.comment-cta').forEach(btn => {
    btn.addEventListener('click', () => {
      const action = btn.dataset.ctaAction;
      if (action === 'run') runFocusedCard(cardId);
      else if (action === 'stop') stopFocusedCard(cardId);
    });
  });

  if (openCard && openCard.cardId === cardId) Object.assign(openCard, {sectionsApi, input});
}

// worker summary, test gate, reviewer verdict, PR link and the fix-round count - all of it null
// until a run has actually landed on this card, in which case the section says so plainly
// reason codes that stop a card on the operator rather than on a fault in the work
const ATTENTION_CODES = new Set(['AGENT_QUESTION', 'LEASE_CONFLICT', 'USAGE_LIMIT', 'DEPENDENCY_REJECTED']);

// ---- card timeline entries: header over small dash-led paragraphs, the review verdict's own
// shape - deriveEntryHeader/splitEntryParagraphs come from ui_base's entrytext.js (board-agnostic
// text layout); this wrapper only owns the html/escaping/pr-linking, which is this file's business
function timelineEntryHtml(header, bodyText, extraLines = []) {
  const paragraphs = [...splitEntryParagraphs(bodyText), ...extraLines].filter(Boolean);
  const lines = paragraphs.map(p => `<div class="timeline-line">- ${linkifyPrRefs(p)}</div>`).join('');
  const body = lines ? `<div class="timeline-body">${lines}</div>` : '';
  return `<div class="timeline-header verdict">${escapeHtml(header)}</div>${body}`;
}

function outcomeSectionHtml(outcome, card = {}) {
  const working = card.status === 'doing' && !card.blocked_reason_code;
  const empty = !outcome || (!outcome.summary && !outcome.tests && !outcome.review && !outcome.pr_url);
  const workingPart = '<div class="outcome-part outcome-working" data-state="doing"><span class="verdict">working</span></div>';
  if (empty) {
    return sectionHtml('outcome', 'outcome', working ? workingPart : '<span class="empty">not run yet</span>');
  }
  // the run's steps in the order they happened, each dot saying what that step came to: done, a
  // problem, waiting on the operator, or still going
  const parts = [];
  if (outcome.summary) {
    const waiting = !outcome.tests && ATTENTION_CODES.has(card.blocked_reason_code);
    const entry = timelineEntryHtml(deriveEntryHeader(outcome.summary), outcome.summary);
    parts.push(`<div class="outcome-part outcome-summary" data-state="${waiting ? 'attention' : 'ok'}">${entry}</div>`);
  }
  if (outcome.tests) {
    const t = outcome.tests;
    const entry = timelineEntryHtml(t.passed ? 'tests passed' : 'tests failed', `${t.command} (exit ${t.exit_code})`);
    parts.push(`<div class="outcome-part outcome-tests" data-ok="${t.passed}" data-state="${t.passed ? 'ok' : 'problem'}">${entry}</div>`);
  }
  if (outcome.review) {
    const r = outcome.review;
    const findingLines = (r.findings || [])
      .map(f => `${f.severity} ${f.category} in ${f.file}: ${f.message}`);
    // a review that did not approve comes to the operator (or back to the worker, still going)
    const reviewState = r.approved ? 'ok' : (working ? 'doing' : 'attention');
    const entry = timelineEntryHtml(r.approved ? 'approved' : 'not approved', r.error || '', findingLines);
    parts.push(`<div class="outcome-part outcome-review" data-ok="${r.approved}" data-state="${reviewState}">${entry}</div>`);
  }
  if (outcome.pr_url) {
    parts.push(`<div class="outcome-part outcome-pr" data-state="ok">${prAnchorHtml(outcome.pr_url, card.repo_url)}</div>`);
  }
  if (working && !outcome.pr_url) parts.push(workingPart);
  parts.push(`<div class="outcome-route">findings route: ${escapeHtml(outcome.findings_route || '')}, fix rounds: ${outcome.fix_rounds ?? 0}</div>`);
  return sectionHtml('outcome', 'outcome', parts.join(''));
}

// one section per field: data-section names it for the layouts, .field-value holds what it says
function sectionHtml(name, label, value) {
  return `<div class="card-section" tabindex="0" data-section="${name}"><div class="field-label">${label}</div><div class="section-value">${value}</div></div>`;
}

// board-written notes lead with a one-line headline now (lifecycle.py's _note callers write it
// that way) - everything after the first line is the detail a person opens on purpose, not what
// they scan past to find the one thing that matters
const BOARD_COMMENT_AUTHOR = 'smortboard';

function splitCommentHeadline(body) {
  const text = (body || '').trim();
  const at = text.indexOf('\n');
  if (at === -1) return {headline: text, rest: ''};
  return {headline: text.slice(0, at).trim(), rest: text.slice(at + 1).trim()};
}

// one board comment: headline, an optional primary action button (only on the card's most recent
// note, and only when the CTA does something here rather than just re-opening the panel we're
// already looking at), and the rest closed behind <details> by default
function renderComment(comment, card, isLatestBoardNote) {
  const label = `<span class="field-label">${escapeHtml(authorLabel(comment.author))}</span>`;
  if (comment.author !== BOARD_COMMENT_AUTHOR) {
    return `<li>${label}<div class="comment-body">${linkifyPrRefs(comment.body)}</div></li>`;
  }
  const {headline, rest} = splitCommentHeadline(comment.body);
  const cta = isLatestBoardNote ? ctaFor(card) : null;
  const actionBtn = cta && cta.action !== 'open'
    ? `<button type="button" class="comment-cta" data-cta-action="${escapeHtml(cta.action)}">${escapeHtml(cta.label)}</button>`
    : '';
  const details = rest
    ? `<details class="comment-details"><summary>details</summary><div class="comment-body">${linkifyPrRefs(rest)}</div></details>`
    : '';
  return `<li>${label}<div class="comment-headline">${linkifyPrRefs(headline)}</div>${actionBtn}${details}</li>`;
}

// a list, or a quiet "none" - an empty <ul> drew a label with nothing under it
function listHtml(items) {
  return items.length ? `<ul>${items.join('')}</ul>` : '<span class="empty">none</span>';
}

// a dependency's title, read off its strip on the board; a card on another board has no strip here
function dependencyLabel(cardId) {
  const title = document.querySelector(`.card-strip[data-card-id="${cardId}"] .card-title`);
  return (title && title.textContent) || `card ${String(cardId).slice(0, 8)}`;
}

function cardPanelHtml(card, outcome) {
  const tasks = (card.tasks || []).map(t => `<li>${t.done ? '[x]' : '[ ]'} ${escapeHtml(t.text)}</li>`);
  const criteria = (card.criteria || []).map(c => `<li>${escapeHtml(c.text)}</li>`);
  // depends_on holds ids only - shown by title when that card is on this board, a short id otherwise
  const deps = (card.depends_on || []).map(d => `<li>${escapeHtml(dependencyLabel(d))}</li>`);
  const attachments = (card.attachments || []).map(a => `<li>${escapeHtml(a.filename)}</li>`);
  const allComments = card.comments || [];
  const latestBoardIndex = [...allComments]
    .map((c, i) => (c.author === BOARD_COMMENT_AUTHOR ? i : -1))
    .filter(i => i !== -1)
    .pop();
  const comments = allComments.map((c, i) => renderComment(c, card, i === latestBoardIndex));
  const status = escapeHtml(card.status) + (card.blocked_reason_code ? ` (${escapeHtml(card.blocked_reason_code)})` : '');
  // the paths its agent may write - an empty lease is why a run gets refused, so say so here
  const globs = (card.leases || []).map(l => escapeHtml(l.path_glob));
  const lease = globs.length ? globs.join(', ') : '<span class="empty">none - it will not run</span>';
  // the call to action leads the status, so an open card says what to do before anything else
  const next = card.next_action ? `<div class="card-next">next: ${escapeHtml(card.next_action)}</div>` : '';
  return `
    <div class="card-sections">
      ${sectionHtml('title', 'title', `${escapeHtml(card.title)} <span class="card-id">${escapeHtml(shortId(card.id))}</span>`)}
      ${sectionHtml('workstream', 'workstream', escapeHtml(card.workstream || '') || '<span class="empty">none</span>')}
      ${sectionHtml('status', 'status', `${next}${status}<div class="card-model">model: ${escapeHtml(modelLabel(card.model))}</div><div class="card-model">lease: ${lease}</div>`)}
      ${outcomeSectionHtml(outcome, card)}
      ${sectionHtml('description', 'description', escapeHtml(card.description || ''))}
      ${sectionHtml('tasks', 'tasks', listHtml(tasks))}
      ${sectionHtml('criteria', 'acceptance criteria', listHtml(criteria))}
      ${sectionHtml('deps', 'dependencies', listHtml(deps))}
      ${sectionHtml('attachments', 'attachments', listHtml(attachments))}
      <div class="card-section" tabindex="0" data-section="comments"><div class="field-label">comments</div>
        <div class="section-value">${listHtml(comments)}</div>
        <input class="comment-input text-field" placeholder="add a comment, enter to send">
      </div>
    </div>
  `;
}

// ---- model (m) - which model the focused card's worker runs on --------------------------------

// null is the board default (the worker_model setting, else sonnet); the rest are claude aliases
const CARD_MODELS = [null, 'haiku', 'sonnet', 'opus'];

function modelLabel(model) {
  return model || 'board default';
}

// cycles default -> haiku -> sonnet -> opus -> default. a model set outside the cycle (a full id)
// steps back to the default, so the key always lands somewhere the next press can leave
async function cycleCardModel(cardId = actionableCardId()) {
  if (!cardId) return;
  try {
    const card = await api(`/api/cards/${cardId}`);
    const next = CARD_MODELS[(CARD_MODELS.indexOf(card.model ?? null) + 1) % CARD_MODELS.length];
    const updated = await api(`/api/cards/${cardId}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({model: next}),
    });
    const label = `model: ${modelLabel(updated.model)}`;
    showRun(cardId, label);
    const shown = document.querySelector('.card-panel [data-section="status"] .card-model');
    if (shown && openCard && openCard.cardId === cardId) shown.textContent = label;
  } catch (err) {
    showRun(cardId, "can't change model", null, err.message);
  }
}

// ---- card overflow menu (...) - edit, delete, change model, move status ---------------------------
// the four actions any card can take. change model reuses cycleCardModel above as-is; edit reuses
// the same open-to-edit the strip's own Enter/Space already does - the panel is where every field
// on a card lives, so there is nothing further to build for it. delete and move-status are new.

function editCard(cardId = actionableCardId()) {
  if (!cardId || (openCard && openCard.cardId === cardId)) return;
  document.querySelector(`.card-strip[data-card-id="${cardId}"]`)?._expander?.open();
}

const STATUS_LABELS = {todo: 'to do', doing: 'doing', checking: 'checking', accepted: 'accepted', rejected: 'rejected'};

// a direct status write, unlike y/x which call the accept/reject routes and their gates - this is
// the manual override for every other move a card can make
async function moveCardStatus(cardId, status) {
  try {
    await api(`/api/cards/${cardId}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({status}),
    });
  } catch (err) {
    showRun(cardId, "can't move", null, err.message);
    return;
  }
  if (currentBoardId) await onBoardEnter(currentBoardId);
  const strip = document.querySelector(`.card-strip[data-card-id="${cardId}"]`);
  if (strip) { strip.focus(); indicateCardFocus(strip); }
}

function openMoveStatusMenu(cardId = actionableCardId()) {
  if (!cardId) return;
  const current = document.querySelector(`.card-strip[data-card-id="${cardId}"]`)?.dataset.status;
  const menu = new Menu({
    title: 'move to',
    sections: [{
      kind: 'list',
      items: STATUSES.map(s => ({id: s, label: STATUS_LABELS[s] || s, disabled: s === current})),
      onPick: item => { menu.close(); moveCardStatus(cardId, item.id); },
    }],
  });
  menu.openAt({x: window.innerWidth / 2 - 200, y: 80});
  menu.el?.classList.add('menu-centered');
  return menu;
}

// same two-item confirm shape as the stop-a-run menu above - a destructive action states the
// consequence and makes the operator pick "keep it" over actually saying delete
function openDeleteConfirm(cardId) {
  const menu = new Menu({
    title: 'delete this card?',
    sections: [{
      kind: 'list',
      items: [
        {id: 'delete', label: 'delete the card'},
        {id: 'keep', label: 'keep it'},
      ],
      onPick: item => {
        menu.close();
        if (item.id === 'delete') doDeleteCard(cardId);
      },
    }],
  });
  menu.openAt({x: window.innerWidth / 2 - 200, y: 80});
  menu.el?.classList.add('menu-centered');
}

async function doDeleteCard(cardId) {
  // close its panel first, same order acceptOrRejectCard closes before moving a card elsewhere
  if (openCard && openCard.cardId === cardId) openCard.expander.close();
  try {
    await api(`/api/cards/${cardId}`, {method: 'DELETE'});
  } catch (err) {
    showRun(cardId, "can't delete", null, err.message);
    return;
  }
  if (currentBoardId) await onBoardEnter(currentBoardId);
  returnToBoardBar();
}

function deleteCard(cardId = actionableCardId()) {
  if (cardId) openDeleteConfirm(cardId);
}

// the ⋯ trigger on a card strip - always acts on that card, not whatever is focused, so a click
// on card B's menu never touches card A even while A holds keyboard focus
function openCardOverflowMenu(cardId, anchor) {
  const menu = new Menu({
    title: 'card actions',
    sections: [{
      kind: 'list',
      items: [
        {id: 'edit', label: 'edit'},
        {id: 'model', label: 'change model'},
        {id: 'status', label: 'move status'},
        {id: 'delete', label: 'delete'},
      ],
      onPick: item => {
        menu.close();
        if (item.id === 'edit') editCard(cardId);
        else if (item.id === 'model') cycleCardModel(cardId);
        else if (item.id === 'status') openMoveStatusMenu(cardId);
        else if (item.id === 'delete') deleteCard(cardId);
      },
    }],
  });
  const rect = anchor.getBoundingClientRect();
  menu.openAt({x: rect.left, y: rect.bottom});
  return menu;
}
