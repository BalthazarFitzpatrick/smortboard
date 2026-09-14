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
