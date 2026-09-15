// masonry layout for the card panel's two-column sections - split out of board.js so a card-ui
// card can lease this file alone. wired in by card_panel.js's openCardPanel (on open) and the
// resize handler below (on reflow); board.js itself never calls into this file directly.

// ---- masonry: a section stacks beneath its own column's actual bottom, not a shared css-grid row
// height. a grid row (or a flex row) ties every cell in it to the tallest cell's box, leaving blank
// space under a shorter neighbour - this replaces that with real measurement instead -------------

const CARD_PANEL_ROW_GAP = 30; // vertical space between stacked sections - the old grid's row-gap
const CARD_PANEL_COL_GAP = 40; // horizontal space between columns - the old grid's column-gap
const CARD_PANEL_NARROW_PX = 760; // the width the two-column layout used to fold to one at
// title and the run read across the whole panel; every other section sits in a column
const FULL_WIDTH_SECTIONS = new Set(['title', 'outcome']);

// pure: given each item's own height (and whether it spans every column), returns where it lands.
// a full-width item syncs every column to one shared reach first, same as a css row would, but a
// column item only ever waits on the column it is actually going into
function computeMasonryLayout(items, columnCount, gap) {
  const reach = new Array(columnCount).fill(0);
  return items.map(item => {
    if (item.full) {
      const top = Math.max(...reach);
      const bottom = top + item.height + gap;
      reach.fill(bottom);
      return {column: null, top, bottom};
    }
    const column = reach.indexOf(Math.min(...reach));
    const top = reach[column];
    const bottom = top + item.height + gap;
    reach[column] = bottom;
    return {column, top, bottom};
  });
}

// the dom side: measure each section at its column's width (a section wraps differently at half
// width than at full width, so width has to land before height is read), run the pure layout
// above, then place every section with an inline top/left and size the container to what was used
function layoutCardSections(panel) {
  const container = panel.querySelector('.card-sections');
  const sections = container ? [...container.querySelectorAll('.card-section')] : [];
  if (!container || !sections.length) return;
  const width = container.getBoundingClientRect().width;
  // nothing sane to measure before the panel has a real box - a later call (resize, reopen) fixes it
  if (!width || width < 0) return;
  const columnCount = width < CARD_PANEL_NARROW_PX ? 1 : 2;
  const columnWidth = (width - CARD_PANEL_COL_GAP * (columnCount - 1)) / columnCount;
  const isFull = section => columnCount === 1 || FULL_WIDTH_SECTIONS.has(section.dataset.section);
  sections.forEach(section => { section.style.width = isFull(section) ? '100%' : `${columnWidth}px`; });
  const items = sections.map(section => ({full: isFull(section), height: section.getBoundingClientRect().height}));
  const placed = computeMasonryLayout(items, columnCount, CARD_PANEL_ROW_GAP);
  let reach = 0;
  sections.forEach((section, i) => {
    const {column, top, bottom} = placed[i];
    section.style.top = `${top}px`;
    section.style.left = column ? `${column * (columnWidth + CARD_PANEL_COL_GAP)}px` : '0px';
    reach = Math.max(reach, bottom);
  });
  container.style.height = `${Math.max(0, reach - CARD_PANEL_ROW_GAP)}px`;
}

// re-flow whenever the container gets its real width or a section changes height. the one pass at
// open can run before the expanding panel has a box, which left every section stacked at 0,0, and
// nothing but a window resize ever laid it out again [coalesced to one pass per frame]
function watchCardSections(panel) {
  if (panel.sectionsObserver) panel.sectionsObserver.disconnect();
  const container = panel.querySelector('.card-sections');
  if (!container || typeof ResizeObserver === 'undefined') return;
  let queued = false;
  const observer = new ResizeObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      if (panel.isConnected) layoutCardSections(panel);
      else observer.disconnect();
    });
  });
  observer.observe(container);
  container.querySelectorAll('.card-section').forEach(section => observer.observe(section));
  panel.sectionsObserver = observer;
}

// the open panel re-flows on resize. layoutCardSections reads the container's own width to decide
// its column count and each section's measured height, and a resize is the one moment either can
// go stale - a design-archive panel-layout-N keeps its own grid and is left alone, same as on open
// debounced: a window drag fires dozens of resize events, each of which would force a synchronous
// reflow of the container and every section - one settled layout pass is enough
let resizeLayoutTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeLayoutTimer);
  resizeLayoutTimer = setTimeout(() => {
    const panel = document.querySelector('.card-panel');
    if (panel && !/panel-layout-\d+/.test(panel.className)) layoutCardSections(panel);
  }, 150);
});

// ---- dense column presentation: a full column as itself, or two full cards top and bottom with
// a pile of card-edges holding the rest ---------------------------------------------------------
// board.js's renderBuckets calls renderBucketColumn per status column, passing the .bucket element
// (label + rows) and the column's status; card_panel.js's renderCardStrip builds every real card,
// full-size whether it sits at rest or mid-excursion. presentation only - never touches card status
// or any stored state, only which cards are drawn full vs folded into a pile right now

const BUCKET_ROW_GAP = 18; // vertical gap between rows in a bucket - matches .bucket-rows in css
const MIN_PILED_CARDS = 5; // below this, front stack + pile + back stack has nothing left to pile
const PILE = 96; // a pile's fixed height - never shrinks, only grows with whatever is left over
const PEEK = 50; // the title-strip band an earlier stacked card still shows under the one on top
const MIN_CARD = PILE + 16; // cards shrink no further than this before the column scrolls instead
const PORTRAIT_BELOW = 230; // a column narrower than this keeps 230px of card height (portrait)

function stackHeight(count, rowHeight) {
  return count ? count * rowHeight + (count - 1) * BUCKET_ROW_GAP : 0;
}

// a card is as tall as the column is wide - square - down to PORTRAIT_BELOW, where it keeps that
// height and turns portrait instead of shrinking further
function squareCard(width) {
  return Math.max(width, PORTRAIT_BELOW);
}

function isAttentionCard(card) {
  // the board handling a card itself reads as working, not as waiting on operator - cardClasses
  // already draws it that way (card-working wins over card-attention), the header count has to
  // agree or it flags a card nobody needs to look at
  if (card.handled_by_board) return false;
  return !!(card.blocked_reason_code || card.review_flag);
}

// a pile layer's own state edge - the same precedence card_panel.js's cardClasses draws a full
// card with, so what protrudes from the pile tells you what is actually inside it
function cardEdgeVar(card) {
  if (card.handled_by_board || card.status === 'doing') return 'var(--fill-good)';
  if (card.blocked_reason_code || card.review_flag) return 'var(--fill-attention)';
  if (card.status === 'rejected') return 'var(--fill-warn)';
  if (card.status === 'accepted') return 'var(--status-good)';
  return 'var(--grey-border)';
}

// ---- pile jitter: each drawn layer offset and rotated from that card's own id, so the same set
// of cards always draws the same pile and only changes when a card enters or leaves it. fnv-1a
// into a mulberry32 stream - picked in the jitter picker (derived/pile_picker), option E -------

function fnv1aHash(text) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const jitterBetween = (r, lo, hi) => lo + (hi - lo) * r();
const clampPileY = y => Math.max(-10, Math.min(10, y));

// pure: a card id -> its layer's offset and rotation. dx +-8px, dy +-10px (clamped, same as the
// row gap a layer may protrude into), rotation +-0.6..2.2deg with a random sign
function pileLayerJitter(cardId) {
  const r = mulberry32(fnv1aHash(cardId));
  const dx = jitterBetween(r, -8, 8);
  const dy = clampPileY(jitterBetween(r, -10, 10));
  const rot = (r() < 0.5 ? -1 : 1) * jitterBetween(r, 0.6, 2.2);
  return {dx, dy, rot};
}

// one letter per column status, plus attention - accepted borrows 'v' and rejected 'r' so neither
// collides with attention's own 'a' (ambiguity call, see the PR notes)
const STATUS_LETTER = {todo: 't', doing: 'd', attention: 'a', checking: 'c', accepted: 'v', rejected: 'r'};
const STATUS_NAME = {todo: 'to do', doing: 'doing', attention: 'attention', checking: 'checking', accepted: 'accepted', rejected: 'rejected'};
const LETTER_ORDER = ['d', 'a', 't', 'c', 'v', 'r'];

// a doing card actually held by a live agent right now, as opposed to one the board is only
// retrying on a timer - handled_by_board reads as working elsewhere (cardClasses, isAttentionCard)
// but nobody is at the wheel for it this instant, so within the doing column it sorts below a card
// an agent is really running
function isActiveDoing(card) {
  return card.status === 'doing' && !isAttentionCard(card) && !card.handled_by_board;
}

// within the doing column: an active agent first, then a card the board is only retrying, then
// attention (blocked or flagged) - elsewhere: doing first, then attention, then everything else.
// stable within each group
function sortColumnCards(cards) {
  const rank = card => isActiveDoing(card) ? 0
    : (card.status === 'doing' && !isAttentionCard(card)) ? 1
    : isAttentionCard(card) ? 2 : 3;
  return [...cards].sort((a, b) => rank(a) - rank(b));
}

// letter -> count, for a header (every card in the column) or a pile (whatever it holds) alike -
// a card's letter is 'a' if it needs attention, otherwise its column's own letter
function letterCounts(cards, status) {
  const counts = {};
  cards.forEach(card => {
    const letter = isAttentionCard(card) ? 'a' : STATUS_LETTER[status];
    counts[letter] = (counts[letter] || 0) + 1;
  });
  return counts;
}

// one state present -> the bare number. several -> "d: 3 | a: 2", each count in its state's color
function countsNode(counts) {
  const letters = LETTER_ORDER.filter(l => counts[l]);
  const node = document.createElement('span');
  node.className = 'bucket-counts';
  if (letters.length <= 1) {
    node.textContent = String(Object.values(counts).reduce((a, b) => a + b, 0));
    return node;
  }
  letters.forEach((letter, i) => {
    if (i) node.appendChild(document.createTextNode(' | '));
    const span = document.createElement('span');
    span.className = `count-${letter}`;
    span.textContent = `${letter}: ${counts[letter]}`;
    node.appendChild(span);
  });
  return node;
}

// column name left, counts right - counts cover the whole column, piled cards included, since they
// are read off the same `cards` array the pile layout is built from, not off what is drawn full
function renderColumnHeader(bucketEl, cards, status) {
  const label = bucketEl.querySelector('.bucket-label');
  if (!label) return;
  label.innerHTML = '';
  const name = document.createElement('span');
  name.className = 'bucket-name';
  name.textContent = STATUS_NAME[status] || status;
  const expandBtn = document.createElement('button');
  expandBtn.type = 'button';
  expandBtn.className = 'bucket-expand';
  expandBtn.addEventListener('click', () => toggleExpand(bucketEl));
  const right = document.createElement('span');
  right.className = 'bucket-header-right';
  right.append(countsNode(letterCounts(cards, status)), expandBtn);
  label.append(name, right);
  updateExpandButton(bucketEl);
}

function updateExpandButton(bucketEl) {
  const bucketRowsEl = bucketEl.querySelector('.bucket-rows');
  const expandBtn = bucketEl.querySelector('.bucket-expand');
  const state = bucketRowsEl?._pile;
  if (!expandBtn || !state) return;
  const pileable = !state.fits && state.sorted.length >= MIN_PILED_CARDS;
  expandBtn.hidden = !pileable;
  expandBtn.textContent = state.expanded ? 'collapse' : 'expand';
}

// the room a column actually has below its own top, down to the viewport's bottom edge - real
// measurement, taken fresh per render since a window resize or drawer changes it
function availableColumnHeight(bucketRowsEl) {
  const top = bucketRowsEl.getBoundingClientRect().top;
  return Math.max(0, (window.innerHeight || 0) - top - 24);
}

const MAX_PILE_LAYERS = 8; // more than this and the desk-pile look stops reading as individual cards

// a desk pile of real card edges behind the top one, each nudged and turned a little from its own
// card's id (pileLayerJitter) and wearing that card's own state edge - what protrudes tells you
// what is inside. bottom first: the first drawn card sits furthest back, the last right under the
// face. jitter stays inside the row gap (clamped to +-10px) so a layer never reaches a neighbour
function buildPileRow(cards, status) {
  const el = document.createElement('div');
  // the pile wears the state edge a card would: attention if it holds one, else doing's own
  const state = cards.some(isAttentionCard) ? ' card-pile-attention' : status === 'doing' ? ' card-pile-doing' : '';
  el.className = `row card-pile${state}`;
  el.tabIndex = -1;
  el.dataset.pile = 'true';
  const drawn = cards.slice(0, Math.min(MAX_PILE_LAYERS, cards.length));
  for (let i = drawn.length - 1; i >= 0; i--) {
    const source = drawn[i];
    const {dx, dy, rot} = pileLayerJitter(source.id);
    const layer = document.createElement('div');
    layer.className = 'card-pile-layer';
    layer.style.transform = `translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px) rotate(${rot.toFixed(2)}deg)`;
    layer.style.borderColor = cardEdgeVar(source);
    el.appendChild(layer);
  }
  const count = document.createElement('div');
  count.className = 'card-pile-count';
  count.appendChild(countsNode(letterCounts(cards, status)));
  el.appendChild(count);
  el.addEventListener('click', () => expandColumnFor(el));
  el._cards = cards;
  return el;
}

// a piled column drops fan-item: its -80% margin pulled the card after a pile up over the pile,
// and its focus slide pushed the bottom pair off screen - computePileFit places these instead
function buildFullRow(card, idx, fanned = true) {
  const strip = renderCardStrip(card);
  strip.dataset.idx = String(idx);
  if (!fanned) strip.classList.remove('fan-item');
  return strip;
}

// pure: rows in order ({type: 'card'|'pile'}), the available height, the row gap and the column's
// own width -> one square card height and one pile height. the pile is fixed at PILE and only
// ever grows with whatever is left over; cards shrink first, never below MIN_CARD - past that the
// column scrolls instead of shrinking further (the only case that scrolls)
function computePileFit(rows, available, gap, width) {
  const piles = rows.filter(r => r.type === 'pile').length;
  const stacks = [];
  rows.forEach(r => {
    if (r.type === 'pile') { stacks.push(null); return; }
    if (stacks.length && stacks[stacks.length - 1] !== null) stacks[stacks.length - 1] += 1;
    else stacks.push(1);
  });
  const groups = stacks.filter(n => n !== null);
  const fixedGaps = Math.max(0, rows.length - 1) * gap;
  const pileFixed = piles * PILE;
  const peekTotal = groups.reduce((sum, n) => sum + (n - 1) * PEEK, 0);
  const used = card => groups.length * card + peekTotal + pileFixed + fixedGaps;
  const full = squareCard(width);
  let card = full;
  let scrolls = false;
  if (used(full) > available) {
    const denom = groups.length || 1;
    card = Math.max(MIN_CARD, Math.floor((available - peekTotal - pileFixed - fixedGaps) / denom));
    scrolls = used(card) > available;
  }
  const leftover = Math.max(0, available - used(card));
  const pileHeight = PILE + (piles && !scrolls ? Math.floor(leftover / piles) : 0);
  return {card, pileHeight, scrolls};
}

// measures the drawn rows and applies computePileFit - the column's own width decides the square
// card size, not a measured dom height. an earlier card in a stack keeps only its PEEK-tall title
// band showing, overlapped by the one drawn after it (which keeps the regular drop shadow, so the
// cover reads as a real card on top rather than a shorter box)
function fitPiledColumn(bucketRowsEl) {
  const rowEls = Array.from(bucketRowsEl.children);
  const rows = rowEls.map(el => ({type: el.classList.contains('card-pile') ? 'pile' : 'card'}));
  const gap = parseFloat(getComputedStyle(bucketRowsEl).rowGap) || BUCKET_ROW_GAP;
  const width = bucketRowsEl.getBoundingClientRect().width;
  const {card, pileHeight, scrolls} = computePileFit(rows, availableColumnHeight(bucketRowsEl), gap, width);
  bucketRowsEl.classList.toggle('bucket-rows-scrolls', scrolls);
  rowEls.forEach((el, i) => {
    if (rows[i].type === 'pile') {
      el.style.height = `${pileHeight}px`;
      el.style.marginTop = '';
      return;
    }
    el.style.height = `${card}px`;
    // the earlier card of a pair wears card-covered (it gives way); the later one pulls itself up
    // over it with a negative margin-top - never the same row, or the later card would peek too
    const nextIsCard = i < rows.length - 1 && rows[i + 1].type === 'card';
    const prevIsCard = i > 0 && rows[i - 1].type === 'card';
    el.classList.toggle('card-covered', nextIsCard);
    el.style.marginTop = prevIsCard ? `${-(card - PEEK)}px` : '';
  });
}

// pure: the total card count, the room/width/gap a resting column has to fill, and one pile in the
// middle -> [front, back] stack sizes. each starts at 1 and grows one card at a time, round-robin,
// front first, for as long as the next card still fits without pushing the column into a scroll -
// so the two stacks differ by at most one and the pile only ever holds what is left over
function computeStackCounts(total, available, width, gap) {
  if (available == null) return [2, 2];
  const card = squareCard(width);
  const fixed = PILE + 2 * gap;
  const sizes = [1, 1];
  const used = () => (sizes[0] - 1) * PEEK + card + (sizes[1] - 1) * PEEK + card + fixed;
  if (used() > available) return sizes;
  let next = 0;
  while (sizes[0] + sizes[1] < total - 1) {
    sizes[next] += 1;
    if (used() > available) { sizes[next] -= 1; break; }
    next = 1 - next;
  }
  return sizes;
}

// pure: sorted cards, the index currently focused (or null - no excursion yet), which edge the
// excursion started from, and the room/width/gap to size the resting stacks by -> the ordered list
// of rows to draw. at rest (or at either edge) the front and back stacks each grow from 1 card,
// round-robin, front stack first, for as long as the next card still fits without scrolling -
// everything between is one pile. mid-excursion, the anchor's own pair (the edge the user came
// from) stays full and fixed at 2 while the OTHER pair moves with focus - a top-anchored excursion
// piles passed cards at the top, a bottom-anchored one piles them at the bottom, and the shrinking
// middle pile sits between the two moving/fixed pairs either way
function computePileLayout(sorted, focusIndex, anchor, available, width, gap) {
  const n = sorted.length;
  const inMiddle = focusIndex != null && anchor && focusIndex >= 2 && focusIndex <= n - 3;
  const out = [];
  const card = idx => ({type: 'card', idx, card: sorted[idx]});
  const pile = (from, to) => { if (to >= from) out.push({type: 'pile', cards: sorted.slice(from, to + 1)}); };
  if (!inMiddle) {
    // dynamic round-robin sizing only applies to the true resting view (no excursion started
    // yet) - the moment a key press sets a focus index, the edge pair goes back to a fixed 2 so
    // nav keeps landing on a real rendered card one step away, same as before this change
    const [front, back] = focusIndex == null ? computeStackCounts(n, available, width, gap) : [2, 2];
    for (let i = 0; i < front; i++) out.push(card(i));
    pile(front, n - 1 - back);
    for (let i = n - back; i < n; i++) out.push(card(i));
    return out;
  }
  if (anchor === 'top') {
    pile(0, focusIndex - 2);
    out.push(card(focusIndex - 1), card(focusIndex));
    pile(focusIndex + 1, n - 3);
    out.push(card(n - 2), card(n - 1));
  } else {
    out.push(card(0), card(1));
    pile(2, focusIndex - 1);
    out.push(card(focusIndex), card(focusIndex + 1));
    pile(focusIndex + 2, n - 1);
  }
  return out;
}

// redraws bucketRowsEl from its own _pile state - expanded and "fits anyway" both mean every card
// full in one plain list; otherwise the excursion-aware split above
function drawColumn(bucketRowsEl) {
  const state = bucketRowsEl._pile;
  bucketRowsEl.innerHTML = '';
  if (!state) return;
  const {sorted, status} = state;
  if (state.expanded || state.fits || sorted.length < MIN_PILED_CARDS) {
    sorted.forEach((c, idx) => bucketRowsEl.appendChild(buildFullRow(c, idx)));
    bucketRowsEl.style.maxHeight = state.expanded && !state.fits ? `${availableColumnHeight(bucketRowsEl)}px` : '';
    bucketRowsEl.classList.toggle('bucket-rows-expanded', !!state.expanded && !state.fits);
    bucketRowsEl.classList.remove('bucket-rows-piled', 'bucket-rows-scrolls');
    return;
  }
  bucketRowsEl.style.maxHeight = '';
  bucketRowsEl.classList.remove('bucket-rows-expanded');
  bucketRowsEl.classList.add('bucket-rows-piled');
  const gap = parseFloat(getComputedStyle(bucketRowsEl).rowGap) || BUCKET_ROW_GAP;
  const width = bucketRowsEl.getBoundingClientRect().width;
  const available = availableColumnHeight(bucketRowsEl);
  computePileLayout(sorted, state.focusIndex, state.anchor, available, width, gap).forEach(entry => {
    bucketRowsEl.appendChild(entry.type === 'pile' ? buildPileRow(entry.cards, status) : buildFullRow(entry.card, entry.idx, false));
  });
  fitPiledColumn(bucketRowsEl);
}

function focusPileIndex(bucketRowsEl, idx) {
  const rows = Array.from(bucketRowsEl.children);
  rows.forEach(row => { row.tabIndex = -1; });
  const target = rows.find(row => row.dataset.idx === String(idx));
  if (!target) return;
  target.tabIndex = 0;
  target.focus();
  indicateFocus(target);
}

// ArrowDown/Up inside a piled column, intercepted ahead of buckets.js's own roving nav (see
// wireColumnFocus) so a press draws the next card off the pile instead of landing focus on the
// pile itself. idx 0 going up, or the last idx going down, falls through untouched - buckets.js's
// default nav exits the column (onExitTop) or clamps in place, same as any other column
function handlePileKey(bucketRowsEl, evt) {
  const state = bucketRowsEl._pile;
  if (!state || state.expanded || state.fits || state.sorted.length < MIN_PILED_CARDS) return;
  if (evt.key !== 'ArrowDown' && evt.key !== 'ArrowUp') return;
  const n = state.sorted.length;
  const row = evt.target.closest?.('[data-idx]');
  const idx = row ? Number(row.dataset.idx) : (state.focusIndex ?? 0);
  const dir = evt.key === 'ArrowDown' ? 1 : -1;
  if (dir === 1 && idx >= n - 1) return;
  if (dir === -1 && idx <= 0) return;
  const nextIdx = idx + dir;
  const nextInMiddle = nextIdx >= 2 && nextIdx <= n - 3;
  let anchor = state.anchor;
  if (!nextInMiddle) anchor = null;
  else if (!anchor) anchor = idx <= 1 ? 'top' : idx >= n - 2 ? 'bottom' : (dir === 1 ? 'top' : 'bottom');
  state.focusIndex = nextIdx;
  state.anchor = anchor;
  evt.preventDefault();
  evt.stopPropagation();
  drawColumn(bucketRowsEl);
  focusPileIndex(bucketRowsEl, nextIdx);
}

function toggleExpand(bucketEl) {
  const bucketRowsEl = bucketEl.querySelector('.bucket-rows');
  const state = bucketRowsEl?._pile;
  if (!state) return;
  state.expanded = !state.expanded;
  drawColumn(bucketRowsEl);
  updateExpandButton(bucketEl);
  refreshBucketNav();
}

function expandColumnFor(pileEl) {
  const bucketRowsEl = pileEl.closest('.bucket-rows');
  const state = bucketRowsEl?._pile;
  if (!state) return;
  state.expanded = true;
  drawColumn(bucketRowsEl);
  const bucketEl = bucketRowsEl.closest('.bucket');
  if (bucketEl) updateExpandButton(bucketEl);
  refreshBucketNav();
}

// wires the pile key interception and keeps _pile.focusIndex in step with plain (mouse/tab) focus
// moves - ONCE PER ELEMENT, NOT PER RENDER. renderBucketColumn runs on every board switch and poll;
// the persistent .bucket-rows node survives each of those (only its children are replaced), so a
// plain addEventListener here would stack a duplicate listener per render
function wireColumnFocus(bucketRowsEl) {
  if (bucketRowsEl._densityWired) return;
  bucketRowsEl._densityWired = true;
  bucketRowsEl.addEventListener('keydown', evt => handlePileKey(bucketRowsEl, evt), {capture: true});
  bucketRowsEl.addEventListener('focusin', evt => {
    const row = evt.target.closest?.('[data-idx]');
    if (row && bucketRowsEl._pile) bucketRowsEl._pile.focusIndex = Number(row.dataset.idx);
  });
}

// builds one status column - every card full if it fits (or expanded, or too few to pile), else
// two full top, a pile, two full bottom - replacing bucketEl's header and rows. card_panel.js's
// renderCardStrip renders every real card, full-size whether at rest or mid-excursion
function renderBucketColumn(bucketEl, cards, status) {
  const bucketRowsEl = bucketEl.querySelector('.bucket-rows');
  const sorted = sortColumnCards(cards);
  const width = bucketRowsEl.getBoundingClientRect().width;
  const fits = stackHeight(sorted.length, squareCard(width)) <= availableColumnHeight(bucketRowsEl);
  bucketRowsEl._pile = {sorted, status, fits, focusIndex: null, anchor: null, expanded: bucketRowsEl._pile?.expanded || false};
  renderColumnHeader(bucketEl, cards, status);
  drawColumn(bucketRowsEl);
  wireColumnFocus(bucketRowsEl);
}
