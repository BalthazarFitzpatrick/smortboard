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

// tuning knobs, the card box and every pure fit/layout/motion helper now live in ui_base's
// pile.js (readGapVar, squareCard, stackHeight, fanHeight, pileSlot, computeColumnFit, placeGroup,
// computeColumnLayout, pileByRecency, pileLayerJitter, the fold and flip frames, planPileMotion).
// what stays here is what a pile means to THIS board: which state paints which edge, how a count
// reads, and every dom builder and animation player that turns those numbers into rows

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

// every card strip carries its own shadow as a child <canvas>, so the shadow is part of the card:
// it rides every slide, focus lift, refit and resize the card does and can never be left behind
// where a card used to be - the one shared layer per bucket it replaces was placed once from
// measured rects and went stale the moment a card moved (the operator's 2026-09-15 screenshots).
// paint order comes free: layout.css gives every strip z-index 0, and the canvas z-index -1 inside
// that stacking context puts it over every earlier card, under its own card and every later one
function applyCardShadows(bucketRowsEl) {
  bucketRowsEl.closest?.('.bucket')?.querySelector('.card-shadow-layer')?.remove();
  Array.from(bucketRowsEl.children).forEach(row => {
    if (row.classList.contains('card-strip')) attachCardShadow(row); // piles keep their own shadow
  });
}

// the layout box, not getBoundingClientRect - that one includes a focus scale or a slide in flight.
// a canvas already sized for this box is kept, so a repeat call costs one measurement
function attachCardShadow(row) {
  const rect = row.getBoundingClientRect();
  const w = Math.round(row.offsetWidth || rect.width);
  const h = Math.round(row.offsetHeight || rect.height);
  const size = `${w}x${h}`;
  const old = row.children.find ? row.children.find(c => c.classList?.contains('card-shade'))
    : row.querySelector('.card-shade');
  if (old && old.dataset.size === size) return;
  old?.remove();
  if (!w || !h) return;
  const shade = shadowImage(w, h);
  // a fresh <canvas> per row, repainted from the cached ImageData - never an <img src="data:...">,
  // see shadowImage's own comment for why
  const canvas = document.createElement('canvas');
  canvas.className = 'card-shade';
  canvas.dataset.size = size;
  canvas.width = shade.cw;
  canvas.height = shade.ch;
  canvas.getContext('2d').putImageData(shade.imageData, 0, 0);
  // absolute children sit against the padding box, so the card's own border is stepped back out
  const css = globalThis.getComputedStyle?.(row);
  canvas.style.top = `${-(shade.pad + (parseFloat(css?.borderTopWidth) || 0))}px`;
  canvas.style.left = `${-(shade.pad + (parseFloat(css?.borderLeftWidth) || 0))}px`;
  canvas.style.width = `${shade.cw}px`;
  canvas.style.height = `${shade.ch}px`;
  row.appendChild(canvas);
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
  // only a column dense enough to overlap has anything to expand - a spread one already shows all
  const pileable = state.regime > 1;
  expandBtn.hidden = !pileable;
  expandBtn.textContent = state.expanded ? 'collapse' : 'expand';
}

// the room a column actually has below its own top, down to the viewport's bottom edge - real
// measurement, taken fresh per render since a window resize or drawer changes it
// measured from the rows' content top: a piled column's shadow-room padding (layout.css) sits above
// it and is cancelled by an equal negative margin, so the content itself never moved
function availableColumnHeight(bucketRowsEl) {
  const pad = parseFloat(globalThis.getComputedStyle?.(bucketRowsEl)?.paddingTop) || 0;
  const top = bucketRowsEl.getBoundingClientRect().top + pad;
  return Math.max(0, (window.innerHeight || 0) - top - 24);
}

// a desk pile of real cards, each nudged and turned a little from its own card's id
// (pileLayerJitter) and wearing that card's own state edge - what protrudes tells you what is
// inside. painted back to front, so THE CARD THAT WENT ON LAST IS THE ONE ON TOP, jittered like
// every other layer and carrying its own title - there is no flat card-sized face standing in for
// it any more. jitter stays inside the row gap (clamped to +-10px) so a layer never reaches a
// neighbour, and the count rides over the whole pile as its own level badge (layout.css)
function buildPileRow(cards, status, side) {
  const el = document.createElement('div');
  // the pile wears the state edge a card would: attention if it holds one, else doing's own
  const state = cards.some(isAttentionCard) ? ' card-pile-attention' : status === 'doing' ? ' card-pile-doing' : '';
  el.className = `row card-pile${state}`;
  el.tabIndex = -1;
  el.dataset.pile = 'true';
  const drawn = pileByRecency(cards, side).slice(0, Math.min(MAX_PILE_LAYERS, cards.length));
  for (let i = drawn.length - 1; i >= 0; i--) {
    el.appendChild(buildPileLayer(drawn[i], i === 0));
  }
  const count = document.createElement('div');
  count.className = 'card-pile-count';
  count.appendChild(countsNode(letterCounts(cards, status)));
  el.appendChild(count);
  el.addEventListener('click', () => expandColumnFor(el));
  el._cards = cards;
  return el;
}

// one drawn card in a pile. the topmost one is a real card rather than an edge: it names the card
// the fold just delivered, so what you see on the pile is what went onto it
function buildPileLayer(card, isTop) {
  const {dx, dy, rot} = pileLayerJitter(card.id);
  const layer = document.createElement('div');
  layer.className = isTop ? 'card-pile-layer card-pile-top' : 'card-pile-layer';
  layer.dataset.pileCard = card.id; // NOT cardId - board.js's focusedCardId() reads that one off any ancestor
  layer.style.transform = `translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px) rotate(${rot.toFixed(2)}deg)`;
  layer.style.borderColor = cardEdgeVar(card);
  if (!isTop) return layer;
  const title = document.createElement('div');
  title.className = 'card-title';
  title.textContent = card.title || '';
  layer.appendChild(title);
  return layer;
}

// every card row, whichever regime draws it - fitColumn gives it its height and its join
function buildFullRow(card, idx) {
  const strip = renderCardStrip(card);
  strip.dataset.idx = String(idx);
  return strip;
}

// ---- column motion, option C (derived/column_motion, operator-approved): a group of at most n
// cards sits between a pile above and a pile below, the fan's own overlap inside the group and the
// focused card open with nothing over it. the geometry that picks the regime and lays out the rows
// is ui_base's (computeColumnFit, placeGroup, computeColumnLayout); what follows applies it to real
// dom -----------------------------------------------------------------------------------------

// applies the fit to the drawn rows: EVERY card the fit's one height, every pile PILE, and each
// card's join as a negative top margin. bucket-rows' own flex `gap` still applies UNDER a margin
// (they add, never cancel), so each margin cancels the gap too, or every card would come out gap px
// low. regime 1 states no margin at all and the flex gap is the whole spacing; regimes 2 and 3 pin
// the column to exactly `available` and clip what runs past it, no scrollbar
function fitColumn(bucketRowsEl, fit, anchor, available, gap) {
  const spread = fit.regime === 1;
  bucketRowsEl.classList.toggle('bucket-rows-expanded', !!fit.expanded);
  bucketRowsEl.classList.toggle('bucket-rows-piled', !spread);
  bucketRowsEl.classList.toggle('bucket-rows-scrolls', !spread && fit.scrolls);
  bucketRowsEl.classList.toggle('bucket-rows-bottom', fit.piles && !fit.scrolls && anchor === 'bottom');
  bucketRowsEl.style.maxHeight = fit.expanded && fit.scrolls ? `${available}px` : '';
  bucketRowsEl.style.height = spread ? '' : `${available}px`;
  Array.from(bucketRowsEl.children).forEach(el => {
    if (el.classList.contains('card-pile')) {
      el.style.height = `${PILE}px`;
      el.style.marginTop = '';
      return;
    }
    const join = el.dataset.join;
    el.classList.toggle('card-covered', el.dataset.covered === 'true');
    el.style.height = `${fit.card}px`;
    el.style.marginTop = join === 'peek' ? `${-(fit.card - PEEK + gap)}px` : join === 'flush' ? `${-gap}px` : '';
  });
}

// true if a card's role (drawn full, or folded into which pile) changed since the prior draw - a
// plain refit or poll redraw that reproduces the same roles reports nothing new
function roleChanged(id, role, priorRoles) {
  return priorRoles.get(id) !== role;
}

// ---- motion: a redraw replaces every row, so each row is replayed from where it was drawn last
// to where it is drawn now. the frames themselves are ui_base's (flipDelta, cardFlipFrames,
// foldFrames, planPileMotion and the timings they run at); this half is the dom work - finding the
// rows, lifting a leaving one out of the flow as a ghost, and seating it where it paints ---------

// a row's identity across redraws: a card by its id, a pile by the side it sits on
function rowKey(el) {
  return el.classList.contains('card-pile') ? `pile-${el.dataset.side}` : `card-${el.dataset.cardId}`;
}

// the drawn rows only - a ghost still folding into (or growing out of) a pile is not one of them
function measureRows(bucketRowsEl) {
  const rows = Array.from(bucketRowsEl.children).filter(el => !el.classList.contains('row-ghost'));
  return new Map(rows.map(el => [rowKey(el), {el, rect: el.getBoundingClientRect()}]));
}

// no motion at all under prefers-reduced-motion, on a first draw, or without the web animations api
function motionAllowed(bucketRowsEl, before) {
  if (!before.size || typeof bucketRowsEl.animate !== 'function') return false;
  return !globalThis.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
}

// lifts a node out of the drawn flow so it can be replayed once where it was: absolute against the
// bucket, dropping .row and its idx so buckets.js's nav and the pile keys never count it, removed
// when its animation ends. the caller seats it where it paints
function detachAsGhost(bucketEl, prior) {
  const ghost = prior.el;
  const base = bucketEl.getBoundingClientRect();
  ghost.classList.remove('row', 'row-enter');
  ghost.classList.add('row-ghost');
  delete ghost.dataset.idx;
  Object.assign(ghost.style, {
    position: 'absolute', margin: '0', zIndex: '0',
    left: `${prior.rect.left - base.left}px`, top: `${prior.rect.top - base.top}px`,
    width: `${prior.rect.width}px`, height: `${prior.rect.height}px`,
  });
  return ghost;
}

// a pile that emptied shrinks away where it was. the card drawn off it stays under it, so it sits
// right over that card in paint order; with no card drawn off it, under every live row (z-index -1
// in .bucket's stacking context)
function ghostPile(bucketEl, prior, timing, over) {
  const ghost = detachAsGhost(bucketEl, prior);
  if (over) {
    ghost.dataset.over = over.dataset.cardId;
    over.parentNode.insertBefore(ghost, over.nextElementSibling);
  } else {
    ghost.style.zIndex = '-1';
    bucketEl.appendChild(ghost);
  }
  // a card growing back out of it takes its place from the first frame, so the pile hands over at
  // once rather than shrinking on top of it
  const anim = ghost.animate(over ? PILE_HANDOVER_OUT : [REST_FRAME, GROW_FRAME], {...timing, fill: 'forwards'});
  anim.onfinish = anim.oncancel = () => ghost.remove();
}

// EVERY card that goes onto a pile is eaten the same way, whether that pile was already there or is
// forming in this very room: it shrinks into the edge facing the pile and is gone. it never slides
// through the pile and out the far side, which is what the mouth-slide it replaces did
function foldIntoPile(bucketEl, prior, pile, timing) {
  const ghost = seatGhostAtPile(bucketEl, prior, pile);
  // it still holds the room the rows below are sliding up into, so it stays over them - and under
  // the pile, which is the only thing allowed to take that room from it
  ghost.style.zIndex = '1';
  pile.el.style.zIndex = '2';
  const {open, folded} = foldFrames(prior.rect, isAbovePile(pile.el));
  const anim = ghost.animate([open, folded], {...timing, fill: 'forwards'});
  anim.onfinish = anim.oncancel = () => { pile.el.style.zIndex = ''; ghost.remove(); };
}

// a leaving card's ghost, seated right before the pile in paint order so the pile and every row
// after it paint over it
function seatGhostAtPile(bucketEl, prior, pile) {
  const ghost = detachAsGhost(bucketEl, prior);
  ghost.dataset.into = pile.el.dataset.side;
  pile.el.parentNode.insertBefore(ghost, pile.el);
  return ghost;
}

const isAbovePile = pileEl => pileEl?.dataset?.side === 'above';

// the exact mirror of foldIntoPile: a card drawn off a pile is spat out of the edge that faces it,
// growing from nothing to full height in its own place - it never starts whole somewhere inside the
// pile and travels out. it takes the room the rows are sliding out of, so it stays over them, and
// stays under the pile it came from for as long as that pile is still there
function growOutOfPile(el, rect, from, timing, pileEl) {
  const {open, folded} = foldFrames(rect, isAbovePile(from.el));
  el.style.zIndex = '1';
  if (pileEl) pileEl.style.zIndex = '2';
  const anim = el.animate([folded, open], timing);
  anim.onfinish = anim.oncancel = () => {
    el.style.zIndex = '';
    if (pileEl) pileEl.style.zIndex = '';
  };
}

// a redraw replaces every row, but a card still folding into a pile (or a pile still shrinking over
// the card drawn off it) keeps going: re-seated where it paints against the new rows, or dropped
// once what it was seated against is gone
function reseatGhosts(bucketRowsEl, ghosts) {
  const rows = Array.from(bucketRowsEl.children);
  ghosts.forEach(ghost => {
    const {into, over} = ghost.dataset;
    const pile = into && rows.find(r => r.classList.contains('card-pile') && r.dataset.side === into);
    const card = over && rows.find(r => r.dataset.cardId === over);
    // the pile it folded into is drawn for real now, so the ghost drops back under it and the rows
    if (pile) { ghost.style.zIndex = '0'; bucketRowsEl.insertBefore(ghost, pile); }
    else if (card) bucketRowsEl.insertBefore(ghost, card.nextElementSibling);
    else ghost.remove();
  });
}

// swaps a pile's count for the one `cards` gives - a pop on the number when it is a landing tick.
// a pile forming shows no count at all until its first card is in
function setPileCount(pileEl, cards, status, pop) {
  const face = pileEl.querySelector('.card-pile-count');
  if (!face) return;
  face.innerHTML = '';
  if (!cards.length) return;
  const counts = countsNode(letterCounts(cards, status));
  face.appendChild(counts);
  if (pop) counts.animate?.([{scale: '1.35'}, {scale: '1'}], {duration: 2 * PILE_SETTLE_MS, easing: 'ease-out'});
}

// a pile eating a card keeps the count it had until the card is all the way in
function tickOnLanding(pileEl, priorCards, status, countAt) {
  setPileCount(pileEl, priorCards, status, false);
  setTimeout(() => setPileCount(pileEl, pileEl._cards, status, true), countAt);
}

// every drawn row against the snapshot taken before the redraw: one still here slides from its old
// box; a card just drawn off a pile grows out of it; a pile just formed grows in; a row that is new
// outright gets the plain .row-enter fade. then the rows that went away: a card now on a pile is
// eaten by it, an emptied pile shrinks away over the card drawn off it
function animateRows(bucketRowsEl, before, plan, priorRoles, nextRoles, status) {
  const now = measureRows(bucketRowsEl);
  const timing = {duration: plan.duration, easing: plan.easing};
  const drawnOff = new Map(); // a pile's key -> the last row drawn off it
  now.forEach(({el, rect}, key) => {
    const prior = before.get(key);
    const isPile = el.classList.contains('card-pile');
    if (prior && movesAtAll(flipDelta(prior.rect, rect))) {
      // a pile is always the same box, so its flip is a plain slide; a card's height really changes
      if (isPile) el.animate([flipFrame(flipDelta(prior.rect, rect)), REST_FRAME], timing);
      else el.animate(cardFlipFrames(prior.rect, rect), timing);
    }
    if (isPile) {
      const pile = plan.piles.get(key);
      // a pile forming under a card folding into it waits for that card; one forming on its own grows
      if (!prior) el.animate(pile?.landing.length ? PILE_HANDOVER_IN : [GROW_FRAME, REST_FRAME], timing);
      if (pile?.landing.length) tickOnLanding(el, prior?.el._cards || [], status, pile.countAt);
      return;
    }
    if (prior) return;
    const fromKey = priorRoles.get(el.dataset.cardId);
    const from = before.get(fromKey);
    if (!from) { el.classList.add('row-enter'); return; }
    growOutOfPile(el, rect, from, timing, now.get(fromKey)?.el);
    drawnOff.set(fromKey, el);
  });
  const bucketEl = bucketRowsEl.closest('.bucket');
  if (!bucketEl) return;
  before.forEach((prior, key) => {
    if (now.has(key)) return;
    if (key.startsWith('pile-')) { ghostPile(bucketEl, prior, timing, drawnOff.get(key)); return; }
    const pileKey = nextRoles.get(prior.el.dataset.cardId);
    const pile = now.get(pileKey);
    if (pile) foldIntoPile(bucketEl, prior, pile, timing);
  });
}

// redraws bucketRowsEl from its own _pile state: ONE path for every column, the regime deciding
// whether its cards spread, fan, or fan between two piles. expanded is the one override - the
// operator asked for the whole column as a scrolling list, so it spreads however dense it is. every
// row is then replayed from its prior box (animateRows); with no motion allowed, a row new in its
// current role (a card appearing, or moving in/out of a pile) gets the plain .row-enter fade
function drawColumn(bucketRowsEl) {
  const state = bucketRowsEl._pile;
  const priorRoles = bucketRowsEl._rowRoles || new Map();
  const nextRoles = new Map();
  const before = measureRows(bucketRowsEl);
  const ghosts = Array.from(bucketRowsEl.children).filter(el => el.classList.contains('row-ghost'));
  const motion = motionAllowed(bucketRowsEl, before);
  const place = (el, role, cards) => {
    if (!motion && cards.some(c => roleChanged(c.id, role, priorRoles))) el.classList.add('row-enter');
    cards.forEach(c => nextRoles.set(c.id, role));
    bucketRowsEl.appendChild(el);
  };
  bucketRowsEl.innerHTML = '';
  if (!state) { bucketRowsEl._rowRoles = nextRoles; return; }
  const {sorted, status} = state;
  const gap = parseFloat(getComputedStyle(bucketRowsEl).rowGap) || CARD_GAP;
  const width = bucketRowsEl.getBoundingClientRect().width;
  const available = availableColumnHeight(bucketRowsEl);
  const natural = computeColumnFit(sorted.length, available, gap, width);
  state.regime = natural.regime;
  // expanded draws the column spread whatever its regime says, scrolling if that overflows
  const fit = state.expanded
    ? {...natural, regime: 1, n: sorted.length, piles: false, scrolls: natural.regime > 1, expanded: true}
    : natural;
  const layout = computeColumnLayout(sorted, state.focusIndex, state.start, state.anchor, fit);
  state.start = layout.start;
  state.anchor = layout.anchor;
  layout.rows.forEach(entry => {
    if (entry.type === 'pile') {
      const el = buildPileRow(entry.cards, status, entry.side);
      el.dataset.side = entry.side;
      place(el, `pile-${entry.side}`, entry.cards);
    } else {
      const strip = buildFullRow(entry.card, entry.idx);
      strip.dataset.part = entry.part;
      strip.dataset.join = entry.join;
      strip.dataset.covered = String(entry.covered);
      place(strip, 'card', [entry.card]);
    }
  });
  fitColumn(bucketRowsEl, fit, layout.anchor, available, gap);
  applyCardShadows(bucketRowsEl);
  reseatGhosts(bucketRowsEl, ghosts);
  if (motion) animateRows(bucketRowsEl, before, planPileMotion(priorRoles, nextRoles), priorRoles, nextRoles, status);
  bucketRowsEl._rowRoles = nextRoles;
}

// every card, in any column type, draws its own focus ring (layout.css), which rides the card's own
// lift and is covered by exactly what covers the card - layout.css hides indicate.js's shared marker
// while any card has focus, so the two never draw together. the marker is still placed, so it glides
// on from here when focus moves off the cards. every card-focusing call site goes through here
function indicateCardFocus(target) {
  indicateFocus(target);
}

function focusPileIndex(bucketRowsEl, idx) {
  const rows = Array.from(bucketRowsEl.children);
  rows.forEach(row => { row.tabIndex = -1; });
  const target = rows.find(row => row.dataset.idx === String(idx));
  if (!target) return;
  target.tabIndex = 0;
  target.focus();
  indicateCardFocus(target);
}

// a column whose cards overlap - fanned or piled. the arrow keys drive it card by card, since the
// focused card has to be redrawn open with nothing over it
function isPiled(state) {
  return !!state && !state.expanded && state.regime > 1;
}

// ArrowDown/Up inside a piled column, intercepted ahead of buckets.js's own roving nav (see
// wireColumnFocus) so a press moves focus one card and the group follows it (placeGroup) instead of
// landing focus on a pile. idx 0 going up, or the last idx going down, falls through untouched -
// buckets.js's default nav exits the column (onExitTop) or clamps in place, same as any other column
function handlePileKey(bucketRowsEl, evt) {
  const state = bucketRowsEl._pile;
  if (!isPiled(state)) return;
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
  // a press lands focus before its click, and a redraw then would swap the row out from under the
  // click that opens the card - a pointer's focus leaves the column as drawn until a later refocus
  bucketRowsEl.addEventListener('pointerdown', () => {
    bucketRowsEl._pointerFocus = true;
    setTimeout(() => { bucketRowsEl._pointerFocus = false; });
  }, {capture: true});
  // focus arriving any other way - buckets.js's left/right nav, tab, a card panel closing - redraws
  // the column around it, so the focused card is always the open one (the start of the column used
  // to show card 1 as a covered strip under the marker)
  bucketRowsEl.addEventListener('focusin', evt => {
    const row = evt.target.closest?.('[data-idx]');
    const state = bucketRowsEl._pile;
    if (!row || !state || bucketRowsEl._pointerFocus) return;
    const idx = Number(row.dataset.idx);
    if (state.focusIndex === idx) return;
    state.focusIndex = idx;
    if (!isPiled(state)) return;
    drawColumn(bucketRowsEl);
    focusPileIndex(bucketRowsEl, idx);
    // this event names a row the redraw just replaced - the refocus above announced the new one
    evt.stopPropagation();
  });
  // focus stepping out to another column or the board bar puts the column back at rest: the card
  // that was open shows its band again. a panel or menu opened from a card
  // is not leaving - redrawing then would swap the strip out from under the panel's own animation
  bucketRowsEl.addEventListener('focusout', evt => {
    const to = evt.relatedTarget;
    if (!to || bucketRowsEl.contains(to) || !to.closest?.('#bucket-row, .board-bar')) return;
    requestAnimationFrame(() => leaveColumn(bucketRowsEl));
  });
}

// the redraw waits a frame, until focus has landed - never while the old row is mid-blur
function leaveColumn(bucketRowsEl) {
  const state = bucketRowsEl._pile;
  if (!state || state.focusIndex == null || bucketRowsEl.contains(document.activeElement)) return;
  state.focusIndex = null;
  if (isPiled(state)) drawColumn(bucketRowsEl);
}

// builds one status column - spread, fanned, or fanned between two piles, whichever its own
// measurement picks (computeColumnFit), top-anchored at rest - replacing bucketEl's header and
// rows. card_panel.js's renderCardStrip renders every real card, full-size in every regime
function renderBucketColumn(bucketEl, cards, status) {
  const bucketRowsEl = bucketEl.querySelector('.bucket-rows');
  const sorted = sortColumnCards(cards);
  bucketRowsEl._pile = {
    sorted, status, regime: 1, focusIndex: null, start: 0, anchor: 'top',
    expanded: bucketRowsEl._pile?.expanded || false,
  };
  renderColumnHeader(bucketEl, cards, status);
  drawColumn(bucketRowsEl); // the regime is only known once the column has measured itself
  updateExpandButton(bucketEl);
  wireColumnFocus(bucketRowsEl);
}

// ---- resize refit: card size and the group's n both come from the column's own measured
// width/height (computeColumnFit), which only a real resize (not a poll) can change. refits every
// column from its own _pile state alone - no refetch,
// no full renderBuckets - and recomputes `fits` too, since widening a column can drop its pile
// entirely, same as renderBucketColumn does on first draw --------------------------------------

function refitColumn(bucketRowsEl) {
  if (!bucketRowsEl._pile) return;
  drawColumn(bucketRowsEl); // remeasures width and room, so the regime is picked again from scratch
  const bucketEl = bucketRowsEl.closest?.('.bucket');
  if (bucketEl) updateExpandButton(bucketEl);
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
