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

// ---- dense column presentation: a full column as itself, or a small group of full cards between
// two piles of card-edges holding the rest (column motion option C, below) ----------------------
// board.js's renderBuckets calls renderBucketColumn per status column, passing the .bucket element
// (label + rows) and the column's status; card_panel.js's renderCardStrip builds every real card,
// full-size whether it sits at rest or in the moving group. presentation only - never touches card status
// or any stored state, only which cards are drawn full vs folded into a pile right now

// tuning-page constants: css custom properties on body (layout.css), read once here so the sizing
// maths and the actual drawn gap always agree - a fallback matches today's value exactly, so a
// missing var (an older stylesheet, or this test stub) changes nothing
function readGapVar(name, fallback) {
  const target = (typeof document !== 'undefined' && (document.documentElement || document.body)) || null;
  const css = target && globalThis.getComputedStyle?.(target);
  const raw = parseFloat(css?.getPropertyValue?.(name) || '');
  return Number.isFinite(raw) ? raw : fallback;
}

const BUCKET_ROW_GAP = 18; // vertical gap between rows in a bucket - matches .bucket-rows in css
const MIN_PILED_CARDS = 5; // below this, a group between two piles has nothing left to pile
const PILE = 100; // a pile's fixed height - never shrinks or grows
const PEEK = readGapVar('--stack-peek', 60); // the title-strip band an earlier card still shows
const PILE_GAP_ABOVE = readGapVar('--pile-gap-above', 5); // extra space above a pile, past the row gap
const PILE_GAP_BELOW = readGapVar('--pile-gap-below', 5); // extra space below a pile, past the row gap
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

// ---- card shadow: a pixel field, not a css box-shadow (box-shadow can't put more darkness at a
// corner than along a side) - ported from derived/shadow_tuner/index.html, operator-approved
// values. drawn outside each card's own overflow box (applyCardShadows), so a later card's shadow
// falls on the earlier card it covers, the way the tuner's own cardwrap/shade pair does -------

const SHADOW_STRENGTH = 0.83; // darkest point, at the corners
const SHADOW_CORE = 1.5; // solid band right at the edge before the fade starts, in px
const SHADOW_SIDE = 10.5; // how far the shadow reaches out along the long sides and the top, in px
const SHADOW_SIDE_STRENGTH = 0.58; // the sides' darkness as a share of the corners'
const SHADOW_RADIUS = 21; // how far the shadow reaches out around each corner, in px
const SHADOW_CREEP = 87; // how far along each edge the corner's reach/darkness carry before fading
const SHADOW_SOFT = 1.6; // fade curve: 1 is linear, higher drops off faster near the edge
const SHADOW_BOTTOM = true; // the bottom edge casts a shadow too
const SHADOW_DROP_Y = false; // no downward nudge

// pure: a point (x, y) relative to a w x h card's top-left corner -> 0..255 alpha at that point.
// dx/dy alone (a side) fades over SIDE (or blends toward RADIUS near a corner, over CREEP); both
// nonzero (past a corner) fades over RADIUS - so a corner reaches further and stays darker longer
function shadowAlpha(x, y, w, h) {
  const dx = x < 0 ? -x : x > w ? x - w : 0;
  const dy = y < 0 ? -y : y > h ? y - h : 0;
  if (!dx && !dy) return 0;
  if (!SHADOW_BOTTOM && y > h) return 0;
  const fall = (d, span) => {
    if (d <= SHADOW_CORE) return 1;
    if (span <= 0) return 0;
    const t = 1 - (d - SHADOW_CORE) / span;
    return t <= 0 ? 0 : Math.pow(t, SHADOW_SOFT);
  };
  let a;
  if (dx && dy) {
    a = fall(Math.hypot(dx, dy), SHADOW_RADIUS);
  } else {
    const u = dx ? Math.min(Math.max(y, 0), Math.max(h - y, 0)) : Math.min(Math.max(x, 0), Math.max(w - x, 0));
    const k = SHADOW_CREEP > 0 ? Math.max(0, 1 - u / SHADOW_CREEP) : 0;
    const spanHere = SHADOW_SIDE + (SHADOW_RADIUS - SHADOW_SIDE) * k;
    const strengthHere = SHADOW_SIDE_STRENGTH + (1 - SHADOW_SIDE_STRENGTH) * k;
    a = fall(dx || dy, spanHere) * strengthHere;
  }
  return Math.round(255 * SHADOW_STRENGTH * a);
}

// one ImageData per card size, generated once and cached - never per frame or per redraw. reach
// pads the canvas past the card's own box on every side (the top if SHADOW_BOTTOM is off), which
// is exactly what a plain css box-shadow could never place outside an overflow:hidden card.
// cached as ImageData, not a canvas.toDataURL() string: a data-uri <img> painted this alpha-only
// (straight-black) content invisibly in some real layouts (headless-verified against the tuner,
// 1067c3e) - a live <canvas> per instance, repainted from the same cached pixels, always composites
const shadowImageCache = new Map();
function shadowImage(w, h) {
  const key = `${w}x${h}`;
  if (shadowImageCache.has(key)) return shadowImageCache.get(key);
  const reach = Math.max(SHADOW_SIDE, SHADOW_RADIUS) + SHADOW_CORE;
  const pad = Math.ceil(reach) + 3;
  const cw = w + 2 * pad, ch = h + 2 * pad;
  const scratch = document.createElement('canvas');
  scratch.width = cw;
  scratch.height = ch;
  const ctx = scratch.getContext('2d');
  const imageData = ctx.createImageData(cw, ch);
  const oy = SHADOW_DROP_Y ? 2 : 0;
  for (let py = 0; py < ch; py++) {
    const y = py - pad - oy;
    for (let px = 0; px < cw; px++) {
      const alpha = shadowAlpha(px - pad, y, w, h);
      if (alpha > 0) imageData.data[(py * cw + px) * 4 + 3] = alpha;
    }
  }
  const out = {imageData, pad, cw, ch};
  shadowImageCache.set(key, out);
  return out;
}

// draws every card strip's shadow as an absolutely-positioned image, layered by the shadow layer's
// own DOM order - a later row's shadow is appended after an earlier row's, so it paints on top,
// same as the strip covering it does. lives on .bucket (not .bucket-rows), one layer per bucket,
// rebuilt from the rows' own measured position - real geometry, not the sizing math's own guess
function applyCardShadows(bucketRowsEl) {
  const bucketEl = bucketRowsEl.closest('.bucket');
  if (!bucketEl) return;
  let layer = bucketEl.querySelector('.card-shadow-layer');
  if (!layer) {
    layer = document.createElement('div');
    layer.className = 'card-shadow-layer';
    bucketEl.appendChild(layer);
  }
  layer.innerHTML = '';
  const bucketRect = bucketEl.getBoundingClientRect();
  // z-index interleaves shadow and card, 4 apart per row: a shadow sits above the card BEFORE it
  // (which it covers) but below the card it belongs to and everything after - same order the
  // tuner's own DOM nesting gives for free, done explicitly since the shadows live in one shared
  // layer rather than one wrapper per card (see .bucket's own z-index:0 - its own stacking context).
  // the gap (was 2) leaves room for a lift: an attention card's ring glow, or a focused card's own
  // inset ring (layout.css), sit in that same paint order and used to lose to the next card's
  // shadow painting over them - lifting just that one row, only above that one shadow and still
  // below the next card itself, shows the ring/frame without uncovering anything
  const ROW_STEP = 4;
  const DECORATION_LIFT = 3;
  let cardIndex = 0;
  Array.from(bucketRowsEl.children).forEach(row => {
    if (!row.classList.contains('card-strip')) return; // piles keep their own existing shadow
    const rect = row.getBoundingClientRect();
    const w = Math.round(rect.width), h = Math.round(rect.height);
    const base = cardIndex * ROW_STEP;
    // only the piled layout actually overlaps cards (buildFullRow drops fan-item there) - the
    // plain list's own fan-item:focus z-index lift stays the only one in play for that case, so
    // this never fights it
    if (!row.classList.contains('fan-item')) {
      const decorated = row.classList.contains('card-attention') || row === document.activeElement;
      row.style.zIndex = String(decorated ? base + DECORATION_LIFT : base);
    }
    if (w && h) {
      const shade = shadowImage(w, h);
      // a fresh <canvas> per row, repainted from the cached ImageData - never an <img src="data:...">,
      // see shadowImage's own comment for why
      const canvas = document.createElement('canvas');
      canvas.className = 'card-shade';
      canvas.width = shade.cw;
      canvas.height = shade.ch;
      canvas.getContext('2d').putImageData(shade.imageData, 0, 0);
      canvas.style.top = `${(rect.top - bucketRect.top) - shade.pad}px`;
      canvas.style.left = `${(rect.left - bucketRect.left) - shade.pad}px`;
      canvas.style.width = `${shade.cw}px`;
      canvas.style.height = `${shade.ch}px`;
      canvas.style.zIndex = String(base - 2);
      layer.appendChild(canvas);
    }
    cardIndex += 1;
  });
  // a piled column cuts what runs past its edge, so the shadows of those rows are cut at the same
  // line - only on the side that clips (the bottom when top-anchored, the top when bottom-anchored)
  const clips = bucketRowsEl.classList.contains('bucket-rows-piled') && !bucketRowsEl.classList.contains('bucket-rows-scrolls');
  if (!clips) { layer.style.clipPath = ''; return; }
  const rowsRect = bucketRowsEl.getBoundingClientRect();
  const reach = SHADOW_RADIUS + SHADOW_CORE + 20;
  const fromBottom = bucketRowsEl.classList.contains('bucket-rows-bottom');
  const top = rowsRect.top - bucketRect.top - (fromBottom ? 0 : reach);
  const bottom = bucketRect.bottom - rowsRect.bottom - (fromBottom ? reach : 0);
  layer.style.clipPath = `inset(${top}px -${reach}px ${bottom}px -${reach}px)`;
}

function isAttentionCard(card) {
  // the board handling a card itself reads as working, not as waiting on fabian - cardClasses
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
// and its focus slide pushed the bottom pair off screen - fitPiledColumn places these instead
function buildFullRow(card, idx, fanned = true) {
  const strip = renderCardStrip(card);
  strip.dataset.idx = String(idx);
  if (!fanned) strip.classList.remove('fan-item');
  return strip;
}

// ---- column motion, option C (derived/column_motion, operator-approved): a group of at most n
// cards sits between a pile above and a pile below. inside the group, passed cards are PEEK strips
// and the focused card is open. stepping past the group's end draws one card off the lower pile and
// puts the group's oldest onto the upper pile, so the group never grows past n ------------------

const EDGE_CARDS = 2; // the column's last (or first) cards shown past the far pile, cut at the edge

// pure: the tallest a group of n cards gets - the focused card open at the group's start, the
// group's last card still full below it, every card between a PEEK strip
function groupHeight(n, card) {
  return n <= 1 ? card : 2 * card + (n - 2) * PEEK;
}

// pure: one pile's share of the column - its own height, its above/below knobs (the same margins
// .card-pile draws) and the one flex gap between it and the group
function pileSlot(gap) {
  return PILE + PILE_GAP_ABOVE + PILE_GAP_BELOW + gap;
}

// pure: the total card count and the room/row gap/width a column has -> {n, card, piles, scrolls}.
// every card as one group when even its tallest shape fits (no piles at all), else the largest n
// in {3, 2} whose tallest group plus both piles fits, else 1. only when even 1 card at square size
// cannot fit do cards shrink (never below MIN_CARD), and past that floor the column scrolls
function computeGroupFit(total, available, gap, width) {
  const full = squareCard(width);
  if (groupHeight(total, full) <= available) return {n: total, card: full, piles: false, scrolls: false};
  const piles = 2 * pileSlot(gap);
  for (const n of [3, 2, 1]) {
    if (groupHeight(n, full) + piles <= available) return {n, card: full, piles: true, scrolls: false};
  }
  const card = Math.max(MIN_CARD, Math.floor(available - piles));
  return {n: 1, card, piles: true, scrolls: card + piles > available};
}

// pure: moves the group only as far as focus left it - one card for one arrow press - and flips
// the anchor at either end: the last card anchors the column to the bottom edge, and only the
// first card anchors it back to the top
function placeGroup(total, n, focusIndex, start, anchor) {
  let next = start ?? 0;
  let side = anchor || 'top';
  if (focusIndex != null) {
    if (focusIndex === 0) side = 'top';
    if (focusIndex === total - 1) side = 'bottom';
    if (focusIndex < next) next = focusIndex;
    if (focusIndex > next + n - 1) next = focusIndex - n + 1;
  }
  return {start: Math.max(0, Math.min(next, total - n)), anchor: side};
}

// pure: sorted cards, the focused index (or null at rest), the group's prior start and anchor, and
// n -> {start, anchor, rows}. top anchor: pile above, group, pile below, then the column's last
// cards, cut by the column's bottom edge. bottom anchor mirrors it: the first cards (cut by the top
// edge), pile above, group, pile below. a card row's join says how it meets the row before it:
// 'peek' slides over that card leaving a PEEK strip, 'flush' starts at the open focused card's
// bottom edge, 'none' is plain flow
function computePileLayout(sorted, focusIndex, start, anchor, n) {
  const total = sorted.length;
  const place = placeGroup(total, n, focusIndex, start, anchor);
  const end = place.start + n; // one past the group's last card
  const edge = Math.min(n, EDGE_CARDS);
  const rows = [];
  const pile = (from, to, side) => {
    if (to > from) rows.push({type: 'pile', side, cards: sorted.slice(from, to)});
  };
  const run = (from, to, part) => {
    for (let i = from; i < to; i++) {
      const join = i === from ? 'none' : i - 1 === focusIndex ? 'flush' : 'peek';
      rows.push({type: 'card', idx: i, card: sorted[i], join, part});
    }
  };
  if (place.anchor === 'top') {
    const tailFrom = Math.max(end, total - edge);
    pile(0, place.start, 'above');
    run(place.start, end, 'group');
    pile(end, tailFrom, 'below');
    run(tailFrom, total, 'edge');
  } else {
    const headTo = Math.min(edge, place.start);
    run(0, headTo, 'edge');
    pile(headTo, place.start, 'above');
    run(place.start, end, 'group');
    pile(end, total, 'below');
  }
  rows.forEach((row, i) => {
    if (row.type === 'card') row.covered = rows[i + 1]?.join === 'peek';
  });
  return {start: place.start, anchor: place.anchor, rows};
}

// applies computeGroupFit to the drawn rows: every card the fit's square size, every pile PILE, and
// each card's join as a margin. bucket-rows' own flex `gap` still applies UNDER a margin (they add,
// never cancel), so a peek margin cancels the gap too, or every strip comes out gap px too tall.
// the column is exactly `available` tall and clips what runs past it, no scrollbar
function fitPiledColumn(bucketRowsEl, fit, anchor, available, gap) {
  const rowEls = Array.from(bucketRowsEl.children);
  bucketRowsEl.classList.toggle('bucket-rows-scrolls', fit.scrolls);
  bucketRowsEl.classList.toggle('bucket-rows-bottom', fit.piles && !fit.scrolls && anchor === 'bottom');
  bucketRowsEl.style.height = `${available}px`;
  rowEls.forEach((el, i) => {
    if (el.classList.contains('card-pile')) {
      el.style.height = `${PILE}px`;
      el.style.marginTop = '';
      return;
    }
    el.style.height = `${fit.card}px`;
    const join = el.dataset.join;
    el.classList.toggle('card-covered', rowEls[i + 1]?.dataset?.join === 'peek');
    el.style.marginTop = join === 'peek' ? `${-(fit.card - PEEK + gap)}px` : join === 'flush' ? `${-gap}px` : '';
  });
}

// true if a card's role (drawn full, or folded into a pile) changed since the prior draw - a
// plain refit or poll redraw that reproduces the same roles reports nothing new, so nothing animates
function roleChanged(id, role, priorRoles) {
  return priorRoles.get(id) !== role;
}

// redraws bucketRowsEl from its own _pile state - expanded and "fits anyway" both mean every card
// full in one plain list; otherwise the group between two piles above. every row that is new in its
// current role (a card appearing, or moving in/out of a pile) gets .row-enter (layout.css); a row
// whose role is unchanged - the common case, a resize refit or an unchanged poll - gets nothing
function drawColumn(bucketRowsEl) {
  const state = bucketRowsEl._pile;
  const priorRoles = bucketRowsEl._rowRoles || new Map();
  const nextRoles = new Map();
  bucketRowsEl.innerHTML = '';
  if (!state) { bucketRowsEl._rowRoles = nextRoles; return; }
  const {sorted, status} = state;
  if (state.expanded || state.fits || sorted.length < MIN_PILED_CARDS) {
    sorted.forEach((c, idx) => {
      const strip = buildFullRow(c, idx);
      if (roleChanged(c.id, 'card', priorRoles)) strip.classList.add('row-enter');
      nextRoles.set(c.id, 'card');
      bucketRowsEl.appendChild(strip);
    });
    bucketRowsEl.style.maxHeight = state.expanded && !state.fits ? `${availableColumnHeight(bucketRowsEl)}px` : '';
    bucketRowsEl.style.height = '';
    bucketRowsEl.classList.toggle('bucket-rows-expanded', !!state.expanded && !state.fits);
    bucketRowsEl.classList.remove('bucket-rows-piled', 'bucket-rows-scrolls', 'bucket-rows-bottom');
    applyCardShadows(bucketRowsEl);
    bucketRowsEl._rowRoles = nextRoles;
    return;
  }
  bucketRowsEl.style.maxHeight = '';
  bucketRowsEl.classList.remove('bucket-rows-expanded');
  bucketRowsEl.classList.add('bucket-rows-piled');
  const gap = parseFloat(getComputedStyle(bucketRowsEl).rowGap) || BUCKET_ROW_GAP;
  const width = bucketRowsEl.getBoundingClientRect().width;
  const available = availableColumnHeight(bucketRowsEl);
  const fit = computeGroupFit(sorted.length, available, gap, width);
  const layout = computePileLayout(sorted, state.focusIndex, state.start, state.anchor, fit.n);
  state.start = layout.start;
  state.anchor = layout.anchor;
  layout.rows.forEach(entry => {
    if (entry.type === 'pile') {
      const entering = entry.cards.some(c => roleChanged(c.id, 'pile', priorRoles));
      const el = buildPileRow(entry.cards, status);
      if (entering) el.classList.add('row-enter');
      entry.cards.forEach(c => nextRoles.set(c.id, 'pile'));
      bucketRowsEl.appendChild(el);
    } else {
      const strip = buildFullRow(entry.card, entry.idx, false);
      strip.dataset.join = entry.join;
      if (roleChanged(entry.card.id, 'card', priorRoles)) strip.classList.add('row-enter');
      nextRoles.set(entry.card.id, 'card');
      bucketRowsEl.appendChild(strip);
    }
  });
  fitPiledColumn(bucketRowsEl, fit, layout.anchor, available, gap);
  applyCardShadows(bucketRowsEl);
  bucketRowsEl._rowRoles = nextRoles;
}

// indicate.js's marker is domain-free and glides to a target's whole box - right for a plain card,
// wrong for a covered one, which only shows its own peek band (fitPiledColumn's negative margin
// slides the covering card up over the rest of it). a covered card stays covered: this clips the
// shared marker to the visible band (the target's own top down to where its covering neighbour
// begins), so the marker's sides and bottom edge never run across a card sitting on top of it.
// every card-focusing call site in this app goes through here instead of indicateFocus directly -
// a plain or covering card gets no clip at all, same behaviour as before this existed
function indicateCardFocus(target) {
  indicateFocus(target);
  const marker = document.querySelector('.focus-marker');
  if (!marker) return;
  const next = target.classList?.contains('card-covered') ? target.nextElementSibling : null;
  const clip = () => {
    if (!next) { marker.style.clipPath = ''; return; }
    const visible = next.getBoundingClientRect().top - target.getBoundingClientRect().top;
    marker.style.clipPath = visible > 0 ? `inset(0 0 calc(100% - ${visible}px) 0)` : '';
  };
  clip();
  // indicate.js re-places the marker on the next frame and again once the target's own slide
  // transition ends - the clip has to be reapplied after each, or the full box flashes back
  globalThis.requestAnimationFrame?.(clip);
  target.addEventListener('transitionend', clip, {once: true});
}

function focusPileIndex(bucketRowsEl, idx) {
  const rows = Array.from(bucketRowsEl.children);
  rows.forEach(row => { row.tabIndex = -1; });
  const target = rows.find(row => row.dataset.idx === String(idx));
  if (!target) return;
  target.tabIndex = 0;
  target.focus();
  indicateCardFocus(target);
  // applyCardShadows' decoration lift reads document.activeElement - drawColumn's own call (just
  // before this) ran ahead of target.focus(), so it never saw the new target as focused
  applyCardShadows(bucketRowsEl);
}

// ArrowDown/Up inside a piled column, intercepted ahead of buckets.js's own roving nav (see
// wireColumnFocus) so a press moves focus one card and the group follows it (placeGroup) instead of
// landing focus on a pile. idx 0 going up, or the last idx going down, falls through untouched -
// buckets.js's default nav exits the column (onExitTop) or clamps in place, same as any other column
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
  state.focusIndex = nextIdx;
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
    // a plain mouse/tab focus move (not through handlePileKey) still needs the decoration lift
    // reapplied against the new document.activeElement
    applyCardShadows(bucketRowsEl);
  });
  // losing focus (a covered card in particular) drops the lift the same way - reapply with no
  // row matching document.activeElement inside this column
  bucketRowsEl.addEventListener('focusout', () => applyCardShadows(bucketRowsEl));
}

// builds one status column - every card full if it fits (or expanded, or too few to pile), else
// the group between two piles, top-anchored at rest - replacing bucketEl's header and rows.
// card_panel.js's renderCardStrip renders every real card, full-size whether at rest or moving
function renderBucketColumn(bucketEl, cards, status) {
  const bucketRowsEl = bucketEl.querySelector('.bucket-rows');
  const sorted = sortColumnCards(cards);
  const width = bucketRowsEl.getBoundingClientRect().width;
  const fits = stackHeight(sorted.length, squareCard(width)) <= availableColumnHeight(bucketRowsEl);
  bucketRowsEl._pile = {
    sorted, status, fits, focusIndex: null, start: 0, anchor: 'top',
    expanded: bucketRowsEl._pile?.expanded || false,
  };
  renderColumnHeader(bucketEl, cards, status);
  drawColumn(bucketRowsEl);
  wireColumnFocus(bucketRowsEl);
}

// ---- resize refit: card size and the group's n both come from the column's own measured
// width/height (computeGroupFit), which only a real resize (not a poll) can change. refits every already-piled column from its own _pile state alone - no refetch,
// no full renderBuckets - and recomputes `fits` too, since widening a column can drop its pile
// entirely, same as renderBucketColumn does on first draw --------------------------------------

function refitColumn(bucketRowsEl) {
  const state = bucketRowsEl._pile;
  if (!state) return;
  const width = bucketRowsEl.getBoundingClientRect().width;
  state.fits = stackHeight(state.sorted.length, squareCard(width)) <= availableColumnHeight(bucketRowsEl);
  drawColumn(bucketRowsEl);
}

const PILE_REFIT_DEBOUNCE_MS = 100;
let pileRefitTimer = null;

// keeps focus on whatever card had it, by id - drawColumn always rebuilds the row it lives in, so
// the dom node itself never survives a refit even when nothing about that card actually changed
function refitPiledColumns() {
  const activeCardId = document.activeElement?.dataset?.cardId ?? null;
  document.querySelectorAll('#bucket-row .bucket-rows').forEach(refitColumn);
  refreshBucketNav();
  if (!activeCardId) return;
  const strip = document.querySelector(`.card-strip[data-card-id="${activeCardId}"]`);
  if (!strip) return;
  strip.tabIndex = 0;
  strip.focus();
  indicateCardFocus(strip);
}

if (typeof window !== 'undefined') {
  window.addEventListener('resize', () => {
    clearTimeout(pileRefitTimer);
    pileRefitTimer = setTimeout(refitPiledColumns, PILE_REFIT_DEBOUNCE_MS);
  });
}
