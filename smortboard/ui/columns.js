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

const CARD_STRIP_HEIGHT = 180; // a card's typical height (layout.css cuts ui_base's by a seventh) - the "fits at all" line
const BUCKET_ROW_GAP = 18; // vertical gap between rows in a bucket - matches .bucket-rows in css
const MIN_PILED_CARDS = 5; // below this, 2 full + pile + 2 full has nothing left to pile - all full
const MIN_PILE_HEIGHT = 48; // enough for the count line plus the layer edges below it
const MIN_COVERED_VISIBLE = 60; // a full card overlapped by its neighbour still shows its title band
// a pile's face stops 9px above its bottom (layout.css), and a card tucked under it must end 2px
// above that too - its own edge showing at the face's rim reads as a second card's border
const PILE_FACE_INSET = 9 + 2;

function stackHeight(count, rowHeight) {
  return count ? count * rowHeight + (count - 1) * BUCKET_ROW_GAP : 0;
}

function isAttentionCard(card) {
  return !!(card.blocked_reason_code || card.review_flag);
}

// one letter per column status, plus attention - accepted borrows 'v' and rejected 'r' so neither
// collides with attention's own 'a' (ambiguity call, see the PR notes)
const STATUS_LETTER = {todo: 't', doing: 'd', checking: 'c', accepted: 'v', rejected: 'r'};
const STATUS_NAME = {todo: 'to do', doing: 'doing', checking: 'checking', accepted: 'accepted', rejected: 'rejected'};
const LETTER_ORDER = ['d', 'a', 't', 'c', 'v', 'r'];

// doing first, then attention (blocked or flagged), then everything else - stable within each group
function sortColumnCards(cards) {
  const rank = card => (card.status === 'doing' && !isAttentionCard(card)) ? 0 : isAttentionCard(card) ? 1 : 2;
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

// a stack of real card edges behind the top one - straight, each layer 3px lower, so only the bottom
// edges show. a rotated layer lifted its corners over the face and read as another card's top edge
function buildPileRow(cards, status) {
  const el = document.createElement('div');
  // the pile wears the state edge a card would: attention if it holds one, else doing's own
  const state = cards.some(isAttentionCard) ? ' card-pile-attention' : status === 'doing' ? ' card-pile-doing' : '';
  el.className = `row card-pile${state}`;
  el.tabIndex = -1;
  el.dataset.pile = 'true';
  const layers = Math.min(cards.length, 4);
  for (let i = layers - 1; i >= 0; i--) {
    const layer = document.createElement('div');
    layer.className = 'card-pile-layer';
    // edges peek out below the face, inside the pile's own box - never over a neighbouring row
    layer.style.transform = `translateY(${i * 3}px)`;
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

// pure: rows in order ({type: 'card'|'pile', height, focused} - a pile's height is ignored), the
// available height and the row gap -> each pile's height and how much to cut off each full card.
// piles share the leftover; if that leaves a pile under its minimum, one card of each adjacent full
// pair gives up its lower part to pay for it - never the focused one, never past its title band
function computePileFit(rows, available, gap) {
  const piles = rows.filter(r => r.type === 'pile').length;
  const cardsHeight = rows.reduce((sum, r) => sum + (r.type === 'card' ? r.height : 0), 0);
  const leftover = available - cardsHeight - Math.max(0, rows.length - 1) * gap;
  const cuts = rows.map(() => 0);
  if (!piles) return {pileHeight: 0, cuts};
  if (leftover / piles >= MIN_PILE_HEIGHT) return {pileHeight: Math.floor(leftover / piles), cuts};
  // the pair's upper card gives way unless it holds focus, then the lower one does
  const victims = [];
  rows.forEach((r, i) => {
    if (r.type === 'card' && rows[i - 1]?.type === 'card') victims.push(rows[i - 1].focused ? i : i - 1);
  });
  // smallest card first, so one hitting its floor spills the rest onto the others
  let remaining = piles * MIN_PILE_HEIGHT - leftover;
  victims.sort((a, b) => rows[a].height - rows[b].height).forEach((v, k) => {
    cuts[v] = Math.ceil(Math.max(0, Math.min(rows[v].height - MIN_COVERED_VISIBLE, remaining / (victims.length - k))));
    remaining -= cuts[v];
  });
  return {pileHeight: MIN_PILE_HEIGHT, cuts};
}

// measures the drawn rows and applies computePileFit - card heights only exist once in the dom.
// no excursion yet counts as focus on the first card, so the column opens with that one uncut
function fitPiledColumn(bucketRowsEl) {
  const focusIdx = String(bucketRowsEl._pile?.focusIndex ?? 0);
  const rowEls = Array.from(bucketRowsEl.children);
  const rows = rowEls.map(el => el.classList.contains('card-pile')
    ? {type: 'pile', height: 0}
    : {type: 'card', height: el.getBoundingClientRect().height, focused: el.dataset.idx === focusIdx});
  const gap = parseFloat(getComputedStyle(bucketRowsEl).rowGap) || BUCKET_ROW_GAP;
  const {pileHeight, cuts} = computePileFit(rows, availableColumnHeight(bucketRowsEl), gap);
  // REAL OVERLAP, NOT A SHORTER CARD. the row after a cut card slides up over its lower part and
  // paints opaque on top; each row still starts exactly where a plain cut would have put it
  const slides = rowEls.map(() => 0);
  rowEls.forEach((el, i) => {
    if (rows[i].type === 'pile') { el.style.height = `${pileHeight}px`; return; }
    const next = rows[i + 1];
    el.classList.toggle('card-covered', cuts[i] > 0 && !!next);
    el.classList.toggle('card-clipped', cuts[i] > 0 && !next);
    el.style.height = '';
    if (!cuts[i]) return;
    const shown = rows[i].height - cuts[i];
    // a pile is shorter than the part it covers, so a card under one stops behind the pile's face;
    // the last row has nothing below it at all, so it simply ends where the cut says
    let kept = rows[i].height;
    if (!next) kept = shown;
    else if (next.type === 'pile') kept = Math.min(kept, shown + gap + pileHeight - PILE_FACE_INSET);
    if (kept < rows[i].height) el.style.height = `${kept}px`;
    if (next) slides[i + 1] = shown - kept;
  });
  rowEls.forEach((el, i) => { el.style.marginTop = slides[i] ? `${slides[i]}px` : ''; });
}

// pure: sorted cards, the index currently focused (or null - no excursion yet), and which edge the
// excursion started from -> the ordered list of rows to draw. at rest (or at either edge) the
// fixed first two and last two are full and everything between is one pile. mid-excursion, the
// anchor's own pair (the edge the user came from) stays full and fixed while the OTHER pair moves
// with focus - a top-anchored excursion piles passed cards at the top, a bottom-anchored one piles
// them at the bottom, and the shrinking middle pile sits between the two moving/fixed pairs either way
function computePileLayout(sorted, focusIndex, anchor) {
  const n = sorted.length;
  const inMiddle = focusIndex != null && anchor && focusIndex >= 2 && focusIndex <= n - 3;
  const out = [];
  const card = idx => ({type: 'card', idx, card: sorted[idx]});
  const pile = (from, to) => { if (to >= from) out.push({type: 'pile', cards: sorted.slice(from, to + 1)}); };
  if (!inMiddle) {
    out.push(card(0), card(1));
    pile(2, n - 3);
    out.push(card(n - 2), card(n - 1));
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
    bucketRowsEl.classList.remove('bucket-rows-piled');
    return;
  }
  bucketRowsEl.style.maxHeight = '';
  bucketRowsEl.classList.remove('bucket-rows-expanded');
  bucketRowsEl.classList.add('bucket-rows-piled');
  computePileLayout(sorted, state.focusIndex, state.anchor).forEach(entry => {
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
  const fits = stackHeight(sorted.length, CARD_STRIP_HEIGHT) <= availableColumnHeight(bucketRowsEl);
  bucketRowsEl._pile = {sorted, status, fits, focusIndex: null, anchor: null, expanded: bucketRowsEl._pile?.expanded || false};
  renderColumnHeader(bucketEl, cards, status);
  drawColumn(bucketRowsEl);
  wireColumnFocus(bucketRowsEl);
}
