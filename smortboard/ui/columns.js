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

// ---- progressive column density: collapse an overflowing column through three tiers -----------
// tier 1 is renderCardStrip's normal fan card. tier 2 collapses every strip to a title-only chip
// (same element, a css class). tier 3 additionally folds the doing cards into one aggregate slot
// and the attention/blocked cards into another, leaving the rest as chips. board.js's renderBuckets
// calls renderBucketColumn per status column; card_panel.js's renderCardStrip builds every real
// card, here or collapsed - this file only decides which tier fits and wires the accordion/draw-out

const CARD_STRIP_HEIGHT = 210; // a fanned card's typical height - tier 1's own budget line
const CHIP_HEIGHT = 56; // two title-text rows plus the strip's usual padding, see layout.css
const AGGREGATE_HEIGHT = CHIP_HEIGHT * 2; // "2-title-tall", per the card's own wording - matches
// .card-aggregate's fixed height in layout.css, which cannot read this constant
const BUCKET_ROW_GAP = 18; // vertical gap between rows in a bucket - matches .bucket-rows in css

function stackHeight(count, rowHeight) {
  return count ? count * rowHeight + (count - 1) * BUCKET_ROW_GAP : 0;
}

function isAttentionCard(card) {
  return !!(card.blocked_reason_code || card.review_flag);
}

// groups the doing cards into one aggregate slot and the attention/blocked cards into another,
// each placed where its first member would otherwise have sat - every other card stays a chip.
// ORDER: leading chips, then the doing aggregate, then the attention/vanilla one, then trailing
// chips - the fixed order the card asks for, not a second sort keyed off card position
function buildAggregateItems(cards) {
  const doingCards = cards.filter(c => c.status === 'doing' && !isAttentionCard(c));
  const attentionCards = cards.filter(isAttentionCard);
  const grouped = new Set([...doingCards, ...attentionCards]);
  const firstGroupedIndex = cards.findIndex(c => grouped.has(c));
  const leading = firstGroupedIndex === -1 ? cards.filter(c => !grouped.has(c))
    : cards.slice(0, firstGroupedIndex).filter(c => !grouped.has(c));
  const trailing = cards.filter(c => !grouped.has(c) && !leading.includes(c));
  const items = leading.map(card => ({type: 'chip', card}));
  if (doingCards.length) items.push({type: 'aggregate', status: 'doing', cards: doingCards});
  if (attentionCards.length) items.push({type: 'aggregate', status: 'attention', cards: attentionCards});
  trailing.forEach(card => items.push({type: 'chip', card}));
  return items;
}

// pure: given a column's cards and the height actually available, picks the tier and the items to
// render at it. measured against real height (see availableColumnHeight), never a hard-coded card
// count, per the card's own rule
function pickColumnPlan(cards, availableHeight) {
  if (!cards.length) return {tier: 1, items: []};
  if (stackHeight(cards.length, CARD_STRIP_HEIGHT) <= availableHeight) {
    return {tier: 1, items: cards.map(card => ({type: 'card', card}))};
  }
  const chipItems = cards.map(card => ({type: 'chip', card}));
  if (stackHeight(cards.length, CHIP_HEIGHT) <= availableHeight) {
    return {tier: 2, items: chipItems};
  }
  return {tier: 3, items: buildAggregateItems(cards)};
}

// the room a column actually has below its own top, down to the viewport's bottom edge - real
// measurement, taken fresh per render since a window resize or drawer changes it
function availableColumnHeight(bucketRowsEl) {
  const top = bucketRowsEl.getBoundingClientRect().top;
  return Math.max(0, (window.innerHeight || 0) - top - 24);
}

function aggregateLabel(status) {
  return status === 'doing' ? 'doing' : 'attention';
}

// the 2-title-tall placeholder: only a status label and a count, per the card - never a real card's
// own title or body. 'row' so buckets.js's roving-tabindex nav treats it like any other focusable
function renderAggregateRow(item) {
  const el = document.createElement('div');
  el.className = `row card-aggregate card-aggregate-${item.status}`;
  el.tabIndex = -1;
  el.dataset.aggregateStatus = item.status;
  const label = document.createElement('span');
  label.className = 'aggregate-label';
  label.textContent = aggregateLabel(item.status);
  const count = document.createElement('span');
  count.className = 'aggregate-count';
  count.textContent = String(item.cards.length);
  el.append(label, count);
  el._cards = item.cards.slice();
  return el;
}

// fan-item is base.css's stacked-deck look for tier 1 only - a chip (collapsed or, mid-accordion,
// briefly expanded) sits in a plain list among its still-collapsed siblings, and the -80% margin
// that makes a fan a fan would just pile every chip on top of the first one
function markChip(strip) {
  strip.classList.remove('fan-item');
  strip.classList.add('card-chip');
}

// tracks which strip is expanded out of its column's collapsed tier, so a second focus elsewhere
// collapses the first back down - accordion, one card open at a time, per the card
let expandedChipStrip = null;

function collapseChip(strip) {
  if (strip && strip !== expandedChipStrip) return;
  if (strip) strip.classList.add('card-chip');
  if (expandedChipStrip === strip) expandedChipStrip = null;
}

// the focused strip fans out to its normal size; whatever was expanded before collapses back to a
// chip. a tier 1 strip (never carries card-chip) is left alone - there is nothing to accordion
function expandChip(strip) {
  if (!strip.classList.contains('card-chip') && strip !== expandedChipStrip) return;
  if (expandedChipStrip && expandedChipStrip !== strip) collapseChip(expandedChipStrip);
  strip.classList.remove('card-chip');
  expandedChipStrip = strip;
}

// focusing an aggregate reveals its next card, one at a time, as a real (collapsed) chip inserted
// just before the placeholder - and decrements the count. the placeholder itself is removed, its
// spot taken by the newly drawn chip, once its count reaches zero
function drawFromAggregate(aggregateEl) {
  const cards = aggregateEl._cards;
  if (!cards || !cards.length) return;
  const card = cards.shift();
  const strip = renderCardStrip(card);
  markChip(strip);
  aggregateEl.parentNode.insertBefore(strip, aggregateEl);
  aggregateEl.querySelector('.aggregate-count').textContent = String(cards.length);
  if (!cards.length) aggregateEl.remove();
  refreshBucketNav();
  strip.focus();
  indicateFocus(strip);
}

// wires the accordion (expand the focused chip, collapse the last one) and the aggregate draw-out
// on top of buckets.js's own roving focus - ONCE PER ELEMENT, NOT PER RENDER. renderBucketColumn
// runs on every board switch and poll; the persistent .bucket-rows node survives each of those
// (only its children are replaced), so a plain addEventListener here would stack a duplicate
// listener per render - and a duplicate handler drew a whole aggregate out in one focus instead
// of one card at a time, which is how this got caught
function wireColumnFocus(bucketRowsEl) {
  if (bucketRowsEl._densityWired) return;
  bucketRowsEl._densityWired = true;
  bucketRowsEl.addEventListener('focusin', evt => {
    const row = evt.target.closest('.row');
    if (!row) return;
    if (row.classList.contains('card-aggregate')) { drawFromAggregate(row); return; }
    expandChip(row);
  });
}

// builds one status column at whichever tier its available height picks, replacing bucketRowsEl's
// contents - card_panel.js's renderCardStrip renders every real card, tier 1 or collapsed alike
function renderBucketColumn(bucketRowsEl, cards) {
  bucketRowsEl.innerHTML = '';
  expandedChipStrip = null;
  const {items} = pickColumnPlan(cards, availableColumnHeight(bucketRowsEl));
  items.forEach(item => {
    if (item.type === 'aggregate') { bucketRowsEl.appendChild(renderAggregateRow(item)); return; }
    const strip = renderCardStrip(item.card);
    if (item.type === 'chip') markChip(strip);
    bucketRowsEl.appendChild(strip);
  });
  wireColumnFocus(bucketRowsEl);
}
