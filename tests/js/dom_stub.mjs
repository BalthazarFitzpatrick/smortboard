// a small dom stub shared by the smortboard interface tests, in the same spirit as ui_base's own
// per-file stubs (menu_sections.mjs, buckets_navigation.mjs, expander.mjs) but with just enough
// selector support (tag, .class, [attr="val"], #id, single descendant combinator) for board.js's
// own querySelector calls plus what shell.js / buckets.js / expand.js need internally.

import {readFileSync} from 'node:fs';

export class Element {}

// ui_base's assets: a sibling ../smortui checkout when developing the two repos in lockstep, else
// the installed package's own copy (UI_BASE_ASSETS_DIR, set by test_js_suite.py) - so the same
// test runs unchanged on a machine with the sibling checkout and inside the gate container, which
// has neither the checkout nor a fetchable one
export function uiBaseAsset(root, name) {
  try {
    return readFileSync(new URL(`../smortui/ui_base/assets/${name}`, root), 'utf8');
  } catch (err) {
    if (err.code !== 'ENOENT' || !process.env.UI_BASE_ASSETS_DIR) throw err;
    return readFileSync(`${process.env.UI_BASE_ASSETS_DIR}/${name}`, 'utf8');
  }
}

function compoundMatch(el, compound) {
  if (compound.startsWith('#')) return el.id === compound.slice(1);
  // both forms: [attr="val"] (must equal) and bare [attr] (must merely be present)
  const attrRe = /\[([\w-]+)(?:="([^"]*)")?\]/g;
  const attrs = [];
  let m;
  while ((m = attrRe.exec(compound))) attrs.push([m[1], m[2]]);
  const rest = compound.replace(attrRe, '');
  const classes = (rest.match(/\.[\w-]+/g) || []).map(c => c.slice(1));
  const tag = rest.replace(/\.[\w-]+/g, '') || null;
  if (tag && el.tag !== tag) return false;
  for (const c of classes) if (!(el.className || '').split(' ').includes(c)) return false;
  for (const [k, v] of attrs) {
    const key = k.startsWith('data-')
      ? k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())
      : k;
    const present = (el.dataset || {})[key] !== undefined;
    if (v === undefined) { if (!present) return false; }
    else if (String((el.dataset || {})[key] ?? '') !== v) return false;
  }
  return true;
}

function walkDesc(el, cb) {
  (el.children || []).forEach(c => { cb(c); walkDesc(c, cb); });
}

export function queryAll(root, sel) {
  const parts = sel.trim().split(/\s+/);
  let scopes = [root];
  for (const part of parts) {
    const found = [];
    scopes.forEach(scope => walkDesc(scope, el => { if (compoundMatch(el, part)) found.push(el); }));
    scopes = found;
  }
  return scopes;
}

export function element(tag, className = '') {
  const el = Object.assign(new Element(), {
    tag, className, id: '', dataset: {}, style: {}, tabIndex: -1, children: [], parentNode: null,
    textContent: '', value: '', placeholder: '', title: '', onclick: null, onkeydown: null,
    _listeners: {},
    addEventListener(type, fn) { (el._listeners[type] ||= []).push(fn); },
    removeEventListener(type, fn) { el._listeners[type] = (el._listeners[type] || []).filter(f => f !== fn); },
    appendChild(child) { child.parentNode = el; el.children.push(child); return child; },
    append(...kids) { kids.forEach(k => el.appendChild(k)); },
    remove() {
      if (el.parentNode) el.parentNode.children = el.parentNode.children.filter(c => c !== el);
      el.parentNode = null; el.removed = true;
    },
    focus() { el.focused = true; globalThis.document.activeElement = el; },
    // like a browser: blurring the focused element hands activeElement back to <body>
    blur() {
      el.focused = false;
      if (globalThis.document.activeElement === el) globalThis.document.activeElement = globalThis.document.body;
    },
    matches(sel) { return sel.split(',').some(s => compoundMatch(el, s.trim())); },
    closest(sel) { let cur = el; while (cur) { if (cur.matches && cur.matches(sel)) return cur; cur = cur.parentNode; } return null; },
    contains(other) { let cur = other; while (cur) { if (cur === el) return true; cur = cur.parentNode; } return false; },
    querySelector(sel) { return queryAll(el, sel)[0] || null; },
    querySelectorAll(sel) { return queryAll(el, sel); },
    getBoundingClientRect: () => ({left: 0, top: 0, right: 0, bottom: 0, width: 100, height: 30}),
    setAttribute(name, v) { el[name] = v; },
    removeAttribute(name) { delete el[name]; },
    classList: {
      contains: name => (el.className || '').split(' ').filter(Boolean).includes(name),
      add(name) { if (!this.contains(name)) el.className = `${el.className} ${name}`.trim(); },
      remove(name) { el.className = el.className.split(' ').filter(c => c && c !== name).join(' '); },
      toggle(name, force) {
        const has = this.contains(name);
        const on = force === undefined ? !has : force;
        if (on) this.add(name); else this.remove(name);
        return on;
      },
    },
  });
  // like a browser: `el.innerHTML = ''` empties the element, which is how a column is redrawn.
  // other markup is kept as a string only - nothing here parses it
  let html = '';
  Object.defineProperty(el, 'innerHTML', {
    get: () => html,
    set(value) {
      html = String(value);
      if (html === '') { el.children.forEach(c => { c.parentNode = null; }); el.children = []; }
    },
  });
  // a textarea's growth math needs just enough geometry to be meaningful: clientHeight tracks the
  // visible box (one unit per row), scrollHeight tracks the content (one unit per line the value
  // actually breaks into) - a real browser's own units, not these, drive the real thing
  // a test that stands in for a scrolled log assigns both directly, and that assignment wins
  let rows = 1;
  const geometry = {};
  Object.defineProperty(el, 'rows', {get: () => rows, set: v => { rows = v; }});
  Object.defineProperty(el, 'clientHeight', {
    get: () => geometry.clientHeight ?? rows * 20, set: v => { geometry.clientHeight = v; },
  });
  Object.defineProperty(el, 'scrollHeight', {
    get: () => geometry.scrollHeight ?? ((el.value.match(/\n/g) || []).length + 1) * 20,
    set: v => { geometry.scrollHeight = v; },
  });
  return el;
}

export function installStubDom({fetchImpl} = {}) {
  const docListeners = {};
  const store = {};
  const root = element('document-root');

  globalThis.Element = Element;
  globalThis.document = Object.assign(root, {
    body: element('body'),
    activeElement: null,
    createElement: element,
    getElementById(id) { return queryAll(root, `#${id}`)[0] || null; },
    addEventListener(type, fn) { (docListeners[type] ||= []).push(fn); },
    removeEventListener(type, fn) { docListeners[type] = (docListeners[type] || []).filter(f => f !== fn); },
  });
  document.appendChild(document.body);
  document._dispatch = (type, evt) => (docListeners[type] || []).forEach(fn => fn(evt));

  globalThis.window = {
    innerWidth: 1200, innerHeight: 800,
    addEventListener() {}, removeEventListener() {},
  };
  globalThis.requestAnimationFrame = fn => fn();
  // ui_base's indicate.js reads a marker's transform; no layout here, so nothing is ever moved
  globalThis.getComputedStyle = () => ({transform: 'none', getPropertyValue: () => ''});
  globalThis.localStorage = {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = v; },
    removeItem: k => { delete store[k]; },
  };
  globalThis.fetch = fetchImpl || (() => new Promise(() => {})); // never resolves unless overridden

  return {document: globalThis.document, dispatchDoc: document._dispatch};
}
