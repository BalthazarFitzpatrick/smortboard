// one card's own rendering and actions - the strip, the open panel, the CTA, the short id, and the
// overflow/model/status/delete menus a card offers. split out of board.js so a card-ui card can
// lease this file alone; board.js wires it in (renderBuckets calls renderCardStrip) and it reaches
// back into board.js globals (openCard, currentBoardId, api, showRun, actionableCardId,
// returnToBoardBar, onBoardEnter, escapeHtml) the same way every other split file does.

// ---- coming back to whatever opened a card ----------------------------------------------------
// a surface that sends you into a card (the inbox's glance) registers here, and is called once the
// card panel closes however it closed - escape, space, a decision. one-shot: each watcher fires
// once and is dropped, so a surface that is no longer there cannot be called twice
let cardClosedWatchers = [];

function afterCardCloses(watcher) {
  cardClosedWatchers.push(watcher);
}

function runCardClosedWatchers() {
  const watchers = cardClosedWatchers;
  cardClosedWatchers = [];
  watchers.forEach(watcher => watcher());
}

// ---- one confirm shape for every "starts or lands work" action -------------------------------
// same two-item menu openDeleteConfirm/openStopConfirm already use, generalised so every shortcut
// that spends money or moves a card gets the same gate. y confirms without touching the mouse;
// escape (Menu's own handler) cancels - neither key ever reaches onConfirm on its own.

// WHICH BUTTON THE CONFIRM OPENS ON, so a stray enter does the safe thing: a reversible move
// (accept, reject, stop) opens on yes, anything expensive or destructive (run, model, delete,
// run the board, fold) opens on no. enter then activates whatever is focused, Menu's own job
function focusMenuItem(menu, id) {
  const row = menu?.el?.querySelector?.(`.menu-item[data-id="${id}"]`);
  if (!row) return;
  row.tabIndex = -1;
  row.focus();
}

function openActionConfirm(title, confirmLabel, cancelLabel, onConfirm, onDismiss, defaultTo = 'cancel') {
  const returnTo = document.activeElement;
  const menu = new Menu({
    title,
    sections: [{
      kind: 'list',
      items: [
        {id: 'confirm', label: confirmLabel, autofocus: defaultTo === 'confirm'},
        {id: 'cancel', label: cancelLabel, autofocus: defaultTo !== 'confirm'},
      ],
      onPick: item => {
        menu.close();
        if (item.id === 'confirm') onConfirm();
      },
    }],
    onDismiss,
  });
  menu.openAt({x: window.innerWidth / 2 - 200, y: 80});
  menu.el?.classList.add('menu-centered');
  focusMenuItem(menu, defaultTo === 'confirm' ? 'confirm' : 'cancel');
  returnFocusOnDismiss(menu, returnTo);
  // y is the explicit yes whatever holds focus; enter is deliberately NOT bound here, so it
  // activates the focused button instead of always confirming
  menu.el?.addEventListener('keydown', evt => {
    if (evt.key !== 'y') return;
    evt.preventDefault();
    evt.stopPropagation();
    menu.close();
    onConfirm();
  });
  return menu;
}

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
  // a thing waiting on the operator - the glow is reserved for a card that actually needs him
  // the queue holds it but no agent does: grey, ahead of every other state, so neither the blue of
  // working nor the yellow of attention claims a card that has not started yet
  if (isPendingCard(card)) classes.push('card-pending');
  else if (card.handled_by_board) classes.push('card-working');
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
  ['BASE_RED', 'Fix the base'],
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
// keeps the first content it finds, cut to the word limit. the open card's about leads with a
// longer cut of the same - a derived view, never a second stored field
function summarizeDescription(description, limit = SUMMARY_WORD_LIMIT) {
  const words = [];
  for (const rawLine of String(description || '').split('\n')) {
    const line = rawLine.trim();
    if (!line || SECTION_HEADER_RE.test(line)) continue;
    const cleaned = line.replace(/^[-*]\s*/, '');
    for (const word of cleaned.split(/\s+/)) {
      if (!word) continue;
      words.push(word);
      if (words.length >= limit) break;
    }
    if (words.length >= limit) break;
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
    onClose: () => { openCard = null; runCardClosedWatchers(); },
  });
  strip.addEventListener('keydown', evt => {
    if (withModifier(evt)) return;
    // a panel over the board owns space and enter - they used to open this card underneath it
    if (surfaceOverBoard()) return;
    if (evt.code !== 'Enter' && evt.code !== 'NumpadEnter' && evt.code !== 'Space') return;
    evt.preventDefault();
    // the document handler also closes on space, so a press handled here stops here
    evt.stopPropagation();
    // SPACE TOGGLES. focus stays on the strip behind an open panel, so this handler sees the
    // second press too - it closes the card it opened
    if (evt.code === 'Space' && openCard?.expander === expander) expander.close();
    else expander.open();
  });
  // WITH THE MOUSE ENABLED (settings, o), the pointer does what the arrows do: hovering focuses
  // this card, which is the same call that lights it and fans its column - one state, not two
  strip.addEventListener('mouseenter', () => hoverFocus(strip, () => {
    strip.tabIndex = 0;
    strip.focus();
    indicateCardFocus(strip);
  }));
  // right-click is m for the mouse: the same menu, anchored where the pointer is. the browser's
  // own menu is suppressed on a card only, and only while the setting is on
  strip.addEventListener('contextmenu', evt => {
    if (!mouseAffordances()) return;
    evt.preventDefault();
    // the menu and the keyboard must agree on which card this is, so the click focuses it first
    strip.tabIndex = 0;
    strip.focus();
    indicateCardFocus(strip);
    openCardOverflowMenu(card.id, {x: evt.clientX, y: evt.clientY});
  });
  strip._expander = expander;
  return strip;
}

// ---- card panel: the open card's sections, and the outcome they render -----------------------

// the open card is a surface of its own: its sections are the rows the cursor walks, the way the
// board's arrows walk cards. the first one takes focus when the panel renders, so up and down have
// somewhere to move from
function focusFirstCardSection(panel) {
  const section = panel.querySelector('.card-section');
  if (!section) return null;
  section.tabIndex = 0;
  section.focus();
  return section;
}

// the card's one text entry. escape stops here so the panel's own escape (added by makeExpander)
// sees a still-open card and only takes the input->card step; the card->closed step is its job
function wireCommentInput(input, panel, cardId) {
  if (!input) return;
  input.addEventListener('keydown', evt => {
    // ARROWS INSIDE THE BOX BELONG TO THE BOX. makeBuckets listens on the panel and the input sits
    // inside a .card-section, so without this, down from the caret stepped to the next section
    if (evt.code === 'ArrowUp' || evt.code === 'ArrowDown') { evt.stopPropagation(); return; }
    if (evt.code === 'Escape') {
      evt.stopPropagation();
      // NOT input.blur(). blur drops focus on <body>, and from there every arrow key is dead - the
      // card has to take it back so escape steps out of the input rather than out of the app
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
}

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
  // THE OPEN CARD TAKES THE KEYBOARD. focus used to stay on the strip behind the panel, so up and
  // down did nothing here and the sections were only reachable by / and then escape
  focusFirstCardSection(panel);

  // kept as a const: openCard carries it below, which is what / falls back to when the card is
  // the only thing open
  const input = panel.querySelector('.comment-input');
  wireCommentInput(input, panel, cardId);

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

// ---- the open card: a head band, then about -> done -> needs down the left and the card's own
// facts down the right. every section is pinned to its column (data-pin), so the reading order is
// the same on every card rather than whatever the shortest-column rule makes of it

// one section per field: data-section names it for the layouts, .section-value holds what it says.
// focus-glow-soft is the quiet version of the card's own focus treatment (ui_base): a section is a
// smaller thing than a card, so it wears the same look at a third of the lift and light
const SECTION_CLASS = 'card-section focus-glow focus-glow-soft';
function sectionHtml(name, label, value, {pin = null, accent = false} = {}) {
  const pinAttr = pin === null ? '' : ` data-pin="${pin}"`;
  const accentAttr = accent ? ' data-accent="attention"' : '';
  return `<div class="${SECTION_CLASS}" tabindex="0" data-section="${name}"${pinAttr}${accentAttr}>` +
    `<div class="field-label">${label}</div><div class="section-value">${value}</div></div>`;
}

// a label with its count on the right, or the bare label when there is nothing to count
function countLabel(label, count) {
  return count ? `${label}<span class="field-count">${count}</span>` : label;
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

// a list, or a quiet "none" - an empty <ul> drew a label with nothing under it
function listHtml(items) {
  return items.length ? `<ul class="card-lines">${items.join('')}</ul>` : '<span class="empty">none</span>';
}

// a dependency's title, read off its strip on the board; a card on another board has no strip here
function dependencyLabel(cardId) {
  const title = document.querySelector(`.card-strip[data-card-id="${cardId}"] .card-title`);
  return (title && title.textContent) || `card ${String(cardId).slice(0, 8)}`;
}

// long text the operator opens on purpose - a brief, a summary with no block - folded shut
function foldHtml(label, text) {
  const body = String(text || '');
  return `<details class="card-fold"><summary>${escapeHtml(label)} - ${body.length} chars</summary>` +
    `<div class="card-fold-body">${linkifyPrRefs(body)}</div></details>`;
}

function pathListHtml(paths) {
  return paths.map(path => `<span class="card-path">${escapeHtml(path)}</span>`).join(', ');
}

// ---- the worker's closing block (runner.SYSTEM_PROMPT): ACTION and WHY one line each, then DONE
// and NOT DONE as "- " lists. read from the last ACTION: line on, so narration above it stays out
// of the lists; null when there is no block - an older run, or one that stopped before writing it
const WORKER_BLOCK_LABEL_RE = /^(ACTION|WHY|DONE|NOT DONE|SCREENSHOT):\s*(.*)$/;

function parseWorkerBlock(summary) {
  const lines = String(summary || '').split('\n').map(line => line.trim());
  let start = -1;
  lines.forEach((line, i) => { if (line.startsWith('ACTION:')) start = i; });
  if (start === -1) return null;
  const block = {action: '', why: '', done: [], notDone: [], before: lines.slice(0, start).join('\n').trim()};
  let list = null;
  for (const line of lines.slice(start)) {
    const label = WORKER_BLOCK_LABEL_RE.exec(line);
    if (label) {
      const [, name, value] = label;
      // SCREENSHOT names a view for the board to shoot - not something done or left
      list = name === 'DONE' ? block.done : (name === 'NOT DONE' ? block.notDone : null);
      if (name === 'ACTION') block.action = value;
      else if (name === 'WHY') block.why = value;
      else if (list && value) list.push(value);
      continue;
    }
    const item = line.replace(/^[-*]\s*/, '');
    if (list && item) list.push(item);
  }
  return block;
}

// "none" on its own says there is nothing - "none, because ..." still says something
function isNoneLine(text) {
  return /^none[.\s]*$/i.test(String(text || '').trim());
}

// the commit a summary names ("committed as ce051d14") - read off prose, so best effort
const COMMIT_IN_SUMMARY_RE = /\bcommit(?:ted)?(?:\s+(?:as|at|in))?\s+([0-9a-f]{7,40})\b/i;

function commitFromSummary(summary) {
  const match = COMMIT_IN_SUMMARY_RE.exec(String(summary || ''));
  return match ? match[1].slice(0, 8) : null;
}

// ---- head: one meta line over the title, on the band that wears the card's state edge -------

// a reason code in plain words for the meta line - TESTS_FAILED reads "tests failed"
function reasonWords(code) {
  return String(code).toLowerCase().replace(/_/g, ' ');
}

function metaModelText(model, lab) {
  return model ? modelLabel(model, lab) : 'default model';
}

function metaComplexityText(card) {
  return `complexity: ${complexityLabel(card)}`;
}

// runs, turns and cost across every attempt, off the outcome's totals - empty for a card never run
function spendLabel(outcome) {
  const runs = outcome?.runs || 0;
  if (!runs) return '';
  const parts = [`${runs} ${runs === 1 ? 'run' : 'runs'}`];
  if (outcome.turns) parts.push(`${outcome.turns} turns`);
  if (typeof outcome.cost_usd === 'number') {
    parts.push(`${outcome.cost_estimated ? '~' : ''}$${outcome.cost_usd.toFixed(2)}`);
  }
  return parts.join(' · ');
}

function cardHeadHtml(card, outcome) {
  const meta = [
    `<span class="card-id">${escapeHtml(shortId(card.id))}</span>`,
    `<span>${escapeHtml(STATUS_LABELS[card.status] || card.status || '')}</span>`,
  ];
  if (card.blocked_reason_code) {
    meta.push(`<span class="card-meta-flag">blocked: ${escapeHtml(reasonWords(card.blocked_reason_code))}</span>`);
  }
  if (card.workstream) meta.push(`<span>${escapeHtml(card.workstream)}</span>`);
  meta.push(`<span class="card-model">${escapeHtml(metaModelText(card.model, card.lab))}</span>`);
  meta.push(`<span class="card-complexity">${escapeHtml(metaComplexityText(card))}</span>`);
  const spend = spendLabel(outcome);
  if (spend) meta.push(`<span>${escapeHtml(spend)}</span>`);
  return `<div class="${SECTION_CLASS}" tabindex="0" data-section="title">` +
    `<div class="card-meta">${meta.join('')}</div>` +
    `<div class="section-value">${escapeHtml(card.title)}</div></div>`;
}

// ---- left: about, done, needs ------------------------------------------------------------------

const ABOUT_WORD_LIMIT = 20;

function aboutSectionHtml(card) {
  const description = card.description || '';
  const lead = summarizeDescription(description, ABOUT_WORD_LIMIT);
  const cut = summarizeDescription(description, Infinity).length > lead.length;
  let body = lead ? `<div class="card-lead">${escapeHtml(lead)}${cut ? '…' : ''}</div>` : '<span class="empty">no brief</span>';
  // the whole brief stays one click away, unless the lead already is the whole brief
  if (description.trim() && description.trim() !== lead) body += foldHtml('full brief', description);
  const tasks = (card.tasks || []).map(t => `<li>${t.done ? '[x]' : '[ ]'} ${escapeHtml(t.text)}</li>`);
  if (tasks.length) body += `<ul class="card-tasks">${tasks.join('')}</ul>`;
  return sectionHtml('about', 'about', body, {pin: 0});
}

// one step of the run: tests, review, pr. the dot and the verdict colour come from data-state -
// ok, problem, attention, doing, or none for a step that has not happened
function railRowHtml(key, state, valueHtml, {title = '', lines = []} = {}) {
  const extra = lines.map(line => `<div class="rail-line">${linkifyPrRefs(line)}</div>`).join('');
  const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
  return `<div class="outcome-part outcome-${key.replace(/\s+/g, '-')}" data-state="${state}"${titleAttr}>` +
    `<span class="rail-key">${escapeHtml(key)}</span><span class="verdict">${valueHtml}</span>${extra}</div>`;
}

function runRailHtml(card, outcome) {
  const working = card.status === 'doing' && !card.blocked_reason_code;
  const tests = outcome.tests;
  const review = outcome.review;
  const rows = [
    tests
      ? railRowHtml('tests', tests.passed ? 'ok' : 'problem',
        `${tests.passed ? 'passed' : 'failed'}, exit ${escapeHtml(String(tests.exit_code))}`, {title: tests.command || ''})
      : railRowHtml('tests', 'none', 'not run'),
  ];
  if (review) {
    const findings = (review.findings || [])
      .map(f => `${f.severity} ${f.category} in ${f.file}: ${f.message || f.detail || ''}`);
    // a review that did not approve comes to the operator (or back to the worker, still going)
    const state = review.approved ? 'ok' : (working ? 'doing' : 'attention');
    rows.push(railRowHtml('review', state, review.approved ? 'approved' : 'not approved',
      {lines: [review.error, ...findings].filter(Boolean)}));
  } else {
    rows.push(railRowHtml('review', 'none', 'not run'));
  }
  rows.push(outcome.pr_url
    ? railRowHtml('pr', 'ok', prAnchorHtml(outcome.pr_url, card.repo_url))
    : railRowHtml('pr', 'none', 'none yet'));
  if (outcome.fix_rounds > 0) rows.push(railRowHtml('fix rounds', 'none', String(outcome.fix_rounds)));
  return `<div class="run-rail">${rows.join('')}</div>`;
}

function doneSectionHtml(card, outcome, block) {
  const ran = outcome.summary || outcome.tests || outcome.review || outcome.pr_url;
  if (!ran) {
    const working = card.status === 'doing' && !card.blocked_reason_code;
    return sectionHtml('done', 'done', `<span class="empty">${working ? 'nothing yet' : 'not run yet'}</span>`, {pin: 0});
  }
  let body = '';
  if (block) {
    body += block.done.length
      ? `<ul class="card-lines">${block.done.map(line => `<li>${linkifyPrRefs(line)}</li>`).join('')}</ul>`
      : '<span class="empty">nothing listed</span>';
    if (block.before) body += foldHtml('agent notes', block.before);
  } else if (outcome.summary) {
    // no closing block to read the lines from - the summary stays whole, one click away
    body += foldHtml('agent summary', outcome.summary);
  }
  body += runRailHtml(card, outcome);
  const commit = commitFromSummary(outcome.summary);
  const label = commit ? `done<span class="field-count">commit ${escapeHtml(commit)}</span>` : 'done';
  return sectionHtml('done', label, body, {pin: 0});
}

// what an open card that is NOT waiting on the operator says under needs
const QUIET_NEEDS = {
  todo: 'nothing yet, r runs it',
  doing: 'nothing, agent on it',
  checking: 'nothing, in checking',
  accepted: 'nothing, accepted',
  rejected: 'nothing, rejected',
};

function quietNeed(card) {
  if (card.handled_by_board) return `nothing, board on it${card.next ? ` - ${card.next}` : ''}`;
  if (isPendingCard(card)) return 'nothing, queued';
  return QUIET_NEEDS[card.status] || 'nothing';
}

// the call to action first, then the agent's own ask and what it left, the lease it wanted, the
// latest board note's run/stop, and the card's one text entry. only a card that really waits on the
// operator (the same test the strip's attention glow uses) wears the accent
function needsSectionHtml(card, outcome, block, hasBoardNote) {
  const attention = ctaFor(card).attention;
  const lines = [];
  if (attention) {
    // with no block to read, the reason itself has to say what went wrong
    if (!block && card.blocked_reason_code) lines.push(`<li>blocked: ${escapeHtml(reasonWords(card.blocked_reason_code))}</li>`);
    if (card.next_action) lines.push(`<li class="card-next">${escapeHtml(card.next_action)}</li>`);
    if (block?.action && !isNoneLine(block.action)) lines.push(`<li>agent: ${linkifyPrRefs(block.action)}</li>`);
    if (block?.why) lines.push(`<li>why: ${linkifyPrRefs(block.why)}</li>`);
  } else {
    lines.push(`<li class="empty">${escapeHtml(quietNeed(card))}</li>`);
  }
  (block?.notDone || []).filter(line => !isNoneLine(line))
    .forEach(line => lines.push(`<li>left: ${linkifyPrRefs(line)}</li>`));
  if (outcome.lease_wanted?.length) lines.push(`<li>wants lease: ${pathListHtml(outcome.lease_wanted)}</li>`);
  if (outcome.lease_expanded?.length) lines.push(`<li>lease widened: ${pathListHtml(outcome.lease_expanded)}</li>`);
  // only where the CTA does something other than re-open this already-open panel - run/stop reuse
  // the same handlers the strip's CTA does
  const cta = hasBoardNote ? ctaFor(card) : null;
  const button = cta && cta.action !== 'open'
    ? `<button type="button" class="toggle comment-cta" data-cta-action="${escapeHtml(cta.action)}">${escapeHtml(cta.label)}</button>`
    : '';
  const input = '<input class="comment-input text-field" placeholder="note to agent, enter sends">';
  return sectionHtml('needs', 'needs', `<ul class="card-lines">${lines.join('')}</ul>${button}${input}`,
    {pin: 0, accent: attention});
}

// ---- right: criteria, lease, dependencies, attachments, history ------------------------------

// MM-DD HH:MM in the viewer's own local time, the way every other clock on the board reads
function formatNoteTime(iso) {
  const at = new Date(iso);
  if (!iso || Number.isNaN(at.getTime())) return '';
  const pad = n => String(n).padStart(2, '0');
  return `${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

// one line per note: its time and headline, the operator's own marked "you". a longer note keeps
// its body behind the headline, opened on purpose
function historyRowHtml(comment) {
  const {headline, rest} = splitCommentHeadline(comment.body);
  const you = comment.author === 'operator';
  const head = `<time>${escapeHtml(formatNoteTime(comment.created_at))}</time>` +
    `<span class="history-head${you ? ' history-you' : ''}">${you ? 'you: ' : ''}${linkifyPrRefs(headline)}</span>`;
  if (!rest) return `<li><div class="history-line">${head}</div></li>`;
  return `<li><details class="history-note"><summary class="history-line">${head}</summary>` +
    `<div class="comment-body">${linkifyPrRefs(rest)}</div></details></li>`;
}

function historySectionHtml(comments) {
  const body = comments.length
    ? `<ul class="card-history">${comments.map(historyRowHtml).join('')}</ul>`
    : '<span class="empty">none</span>';
  return sectionHtml('history', countLabel('history', comments.length), body, {pin: 1});
}

function cardPanelHtml(card, outcome) {
  const run = outcome || {};
  const block = parseWorkerBlock(run.summary);
  const comments = card.comments || [];
  const hasBoardNote = comments.some(c => c.author === BOARD_COMMENT_AUTHOR);
  const criteria = (card.criteria || []).map(c => `<li>${escapeHtml(c.text)}</li>`);
  // the paths its agent may write - an empty lease is why a run gets refused, so say so here
  const globs = (card.leases || []).map(l => `<li class="card-path">${escapeHtml(l.path_glob)}</li>`);
  const lease = globs.length ? `<ul class="card-lines">${globs.join('')}</ul>` : '<span class="empty">none - it will not run</span>';
  // depends_on holds ids only - shown by title when that card is on this board, a short id otherwise
  const deps = (card.depends_on || []).map(d => `<li>${escapeHtml(dependencyLabel(d))}</li>`);
  const attachments = (card.attachments || []).map(a => `<li>${escapeHtml(a.filename)}</li>`);
  return `
    <div class="card-sections" data-column-shares="3 2">
      ${cardHeadHtml(card, run)}
      ${aboutSectionHtml(card)}
      ${doneSectionHtml(card, run, block)}
      ${needsSectionHtml(card, run, block, hasBoardNote)}
      ${sectionHtml('criteria', countLabel('criteria', criteria.length), listHtml(criteria), {pin: 1})}
      ${sectionHtml('lease', countLabel('lease', globs.length), lease, {pin: 1})}
      ${deps.length ? sectionHtml('deps', countLabel('dependencies', deps.length), listHtml(deps), {pin: 1}) : ''}
      ${attachments.length ? sectionHtml('attachments', countLabel('attachments', attachments.length), listHtml(attachments), {pin: 1}) : ''}
      ${historySectionHtml(comments)}
    </div>
  `;
}

// ---- complexity - low/medium/high, rated or estimated ------------------------------------------

const COMPLEXITY_LEVELS = [1, 2, 3];
const COMPLEXITY_LABELS = {1: 'low', 2: 'medium', 3: 'high'};

// mirrors telemetry.estimate_complexity - a display-only guess for an unrated card, never sent
// back to the server. see that function's docstring for the scoring rule
function estimateComplexity(card) {
  const globs = (card.leases || []).map(l => l.path_glob || '');
  let score = (card.criteria || []).length + (card.tasks || []).length;
  if (globs.some(g => g.includes('**'))) score += 2;
  else if (globs.length) score += 1;
  if ((modelCatalog[card.lab || 'anthropic']?.models || []).some(m => m.id === card.model && m.tier === 'deep')) score += 2;
  if (score <= 3) return 1;
  if (score <= 6) return 2;
  return 3;
}

function complexityLabel(card) {
  const level = typeof card === 'object' ? card.complexity : card;
  if (COMPLEXITY_LEVELS.includes(level)) return COMPLEXITY_LABELS[level];
  if (typeof card === 'object') return `${COMPLEXITY_LABELS[estimateComplexity(card)]} (estimated)`;
  return 'unrated';
}

// cycles low -> medium -> high -> low, same wrap as cycleCardModel
async function cycleCardComplexity(cardId = actionableCardId()) {
  if (!cardId) return;
  let next;
  try {
    const card = await api(`/api/cards/${cardId}`);
    // unrated lands on low first; otherwise the same wrap as cycleCardModel
    const currentIndex = COMPLEXITY_LEVELS.indexOf(card.complexity);
    next = COMPLEXITY_LEVELS[(currentIndex + 1) % COMPLEXITY_LEVELS.length];
  } catch (err) {
    showRun(cardId, "can't change complexity", null, err.message);
    return;
  }
  openActionConfirm(`switch to ${COMPLEXITY_LABELS[next]}?`, 'switch complexity', 'cancel',
    () => doCycleCardComplexity(cardId, next));
}

async function doCycleCardComplexity(cardId, next) {
  try {
    const updated = await api(`/api/cards/${cardId}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({complexity: next}),
    });
    const label = metaComplexityText(updated);
    showRun(cardId, label);
    const shown = document.querySelector('.card-panel .card-complexity');
    if (shown && openCard && openCard.cardId === cardId) shown.textContent = label;
  } catch (err) {
    showRun(cardId, "can't change complexity", null, err.message);
  }
}

// ---- model (m) - which model the focused card's worker runs on --------------------------------

let modelCatalog = {};

function modelLabel(model, lab) {
  return model ? (model.includes('/') ? model : `${lab || 'anthropic'}/${model}`) : 'board default';
}

async function loadModelCatalog() {
  modelCatalog = await api('/api/catalog');
  return modelCatalog;
}

async function openModelPicker(onPick, anchor = {x: window.innerWidth / 2 - 200, y: 80}, onBack = null) {
  const catalog = await loadModelCatalog();
  const labs = Object.keys(catalog);
  let selectedLab = labs.find(lab => catalog[lab].available !== false) || labs[0];
  let selectedModel = null;
  let picked = false;
  let menu = null;

  const buildSections = () => [{
    kind: 'columns',
    columns: [
      {
        label: 'lab',
        multi: false,
        items: Object.entries(catalog).map(([lab, entry]) => ({
          id: lab,
          label: lab,
          on: lab === selectedLab,
          disabled: entry.available === false,
          stats: entry.available === false ? entry.unavailable_reason || 'no usable profile' : '',
        })),
        onPick: item => {
          selectedLab = item.id;
          selectedModel = null;
          menu.refresh(buildSections());
        },
      },
      {
        label: 'model',
        multi: false,
        empty: selectedLab ? 'no models available' : 'choose a lab',
        items: catalog[selectedLab]?.available === false ? []
          : (catalog[selectedLab]?.models || []).map(model => ({
            id: model.id, label: model.label, stats: model.tier, on: model.id === selectedModel,
          })),
        onPick: item => {
          selectedModel = item.id;
          menu.refresh(buildSections());
        },
      },
    ],
  }, {
    kind: 'buttons',
    buttons: [
      {id: 'save-model', label: 'save', enabled: selectedModel !== null, onClick: openMenu => {
        picked = true;
        openMenu.close();
        onPick(selectedLab, selectedModel);
      }},
      {id: 'model-default', label: 'board default', onClick: openMenu => {
        picked = true;
        openMenu.close();
        onPick(null, null);
      }},
    ],
  }];

  menu = new Menu({
    title: 'choose model',
    persistent: true,
    sections: buildSections(),
    onDismiss: () => { if (!picked && onBack) onBack(); },
  });
  menu.openAt(anchor);
}

async function cycleCardModel(cardId = actionableCardId()) {
  if (!cardId) return;
  try {
    await openModelPicker((lab, model) => doCycleCardModel(cardId, model, lab), undefined,
      () => openCardActionsMenu(cardId));
  } catch (err) {
    showRun(cardId, "can't change model", null, err.message);
  }
}

async function doCycleCardModel(cardId, next, lab = null) {
  try {
    const updated = await api(`/api/cards/${cardId}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({lab, model: next}),
    });
    const label = `model: ${modelLabel(updated.model, updated.lab)}`;
    showRun(cardId, label);
    const shown = document.querySelector('.card-panel .card-model');
    if (shown && openCard && openCard.cardId === cardId) shown.textContent = metaModelText(updated.model, updated.lab);
  } catch (err) {
    showRun(cardId, "can't change model", null, err.message);
  }
}

// ---- the card's own menu (m, and the ⋯ trigger) - edit, model, complexity, move to, delete ------
// THE ONLY ROUTE TO FOUR OF THESE. e, j, del and m-cycles-the-model were each their own binding;
// they are rows here now, so the menu has to be fully operable from the keyboard: m opens it,
// arrows move, enter picks, escape closes it and hands the card back.
// change model reuses cycleCardModel below as-is; edit reuses the same open-to-edit the strip's
// own Enter/Space already does - the panel is where every field on a card lives.

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

// onBack (optional) is what escape goes back TO: opened from the card menu, one level back is
// that menu rather than the board - the same one-level rule, applied inside a menu
function openMoveStatusMenu(cardId = actionableCardId(), onBack = null) {
  if (!cardId) return;
  const returnTo = document.activeElement;
  let picked = false;
  const current = document.querySelector(`.card-strip[data-card-id="${cardId}"]`)?.dataset.status;
  const menu = new Menu({
    title: 'move to',
    sections: [{
      kind: 'list',
      items: STATUSES.map(s => ({id: s, label: STATUS_LABELS[s] || s, disabled: s === current})),
      onPick: item => { picked = true; menu.close(); moveCardStatus(cardId, item.id); },
    }],
  });
  menu.openAt({x: window.innerWidth / 2 - 200, y: 80});
  menu.el?.classList.add('menu-centered');
  if (onBack) menu.onDismiss = () => { if (!picked) onBack(); };
  else returnFocusOnDismiss(menu, returnTo);
  return menu;
}

// same two-item confirm shape as the stop-a-run menu above - a destructive action states the
// consequence and makes the operator pick "keep it" over actually saying delete
function openDeleteConfirm(cardId) {
  const returnTo = document.activeElement;
  const menu = new Menu({
    title: 'delete this card?',
    sections: [{
      kind: 'list',
      items: [
        {id: 'delete', label: 'delete the card'},
        // destructive: "keep it" holds focus, so a stray enter never deletes
        {id: 'keep', label: 'keep it', autofocus: true},
      ],
      onPick: item => {
        menu.close();
        if (item.id === 'delete') doDeleteCard(cardId);
      },
    }],
  });
  menu.openAt({x: window.innerWidth / 2 - 200, y: 80});
  menu.el?.classList.add('menu-centered');
  focusMenuItem(menu, 'keep');
  returnFocusOnDismiss(menu, returnTo);
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
// on card B's menu never touches card A even while A holds keyboard focus.
// `anchor` is the element it hangs under, or a {x, y} point - which is what a right-click and the
// keyboard's own m both need
function openCardOverflowMenu(cardId, anchor) {
  const menu = new Menu({
    title: 'card actions',
    sections: [{
      kind: 'list',
      items: [
        {id: 'edit', label: 'edit'},
        {id: 'model', label: 'change model'},
        {id: 'complexity', label: 'change complexity'},
        {id: 'status', label: 'move status'},
        {id: 'delete', label: 'delete'},
      ],
      onPick: item => {
        menu.close();
        if (item.id === 'edit') editCard(cardId);
        else if (item.id === 'model') cycleCardModel(cardId);
        else if (item.id === 'complexity') cycleCardComplexity(cardId);
        else if (item.id === 'status') openMoveStatusMenu(cardId, () => openCardActionsMenu(cardId));
        else if (item.id === 'delete') deleteCard(cardId);
      },
    }],
  });
  const rect = anchor?.getBoundingClientRect?.();
  menu.openAt(rect ? {x: rect.left, y: rect.bottom} : {x: anchor.x, y: anchor.y});
  // escape closes just this menu and hands the keyboard back to the card it was opened on - and
  // stands aside where a row's own pick (delete, move to) has just opened a menu of its own
  const strip = document.querySelector(`.card-strip[data-card-id="${cardId}"]`);
  returnFocusOnDismiss(menu, openCard ? null : strip);
  return menu;
}

// the same menu from the keyboard (l) - anchored on the focused card's own strip, since there is
// no click position to open against
function openCardActionsMenu(cardId = actionableCardId()) {
  if (!cardId) return;
  const strip = document.querySelector(`.card-strip[data-card-id="${cardId}"]`);
  if (!strip) return;
  return openCardOverflowMenu(cardId, strip);
}
