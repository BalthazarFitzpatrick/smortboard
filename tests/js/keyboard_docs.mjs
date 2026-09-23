// the docs site's keyboard page against the app's own BINDINGS table: every key the board binds has
// its row on docs/reference/keyboard.md, word for word, so the published list cannot drift from
// what the keys do. run: node tests/js/keyboard_docs.mjs
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const root = new URL('../../', import.meta.url);
const source = readFileSync(new URL('smortboard/ui/shortcuts.js', root), 'utf8');
const page = readFileSync(new URL('docs/reference/keyboard.md', root), 'utf8');

// the array literal after `const NAME = `, matched by bracket depth with strings and line
// comments skipped - shortcuts.js is a browser script, so it is read rather than imported
function arrayLiteral(name) {
  const marker = `const ${name} = `;
  const start = source.indexOf(marker) + marker.length;
  let depth = 0;
  let quote = null;
  for (let i = start; i < source.length; i++) {
    const c = source[i];
    if (quote) {
      if (c === '\\') i++;
      else if (c === quote) quote = null;
      continue;
    }
    if (c === "'" || c === '"' || c === '`') { quote = c; continue; }
    if (c === '/' && source[i + 1] === '/') { i = source.indexOf('\n', i); continue; }
    if ('[{('.includes(c)) depth++;
    if (']})'.includes(c) && --depth === 0) return vm.runInNewContext(source.slice(start, i + 1));
  }
  throw new Error(`no ${name} array in shortcuts.js`);
}

// copied into this realm: an array made inside vm has its own Array prototype, which strict
// equality would count as a difference
const bindings = Array.from(arrayLiteral('BINDINGS'));
assert.ok(bindings.length > 20, 'BINDINGS was read, not an empty match');

// the nine board keys are one row on the page, as they are in the in-app overlay
const rows = bindings
  .filter(b => !/^Digit[2-9]$/.test(b.code))
  .map(b => (b.code === 'Digit1'
    ? '| `1 .. 9` | jump to board 1 .. 9 |'
    : `| \`${b.label}\` | ${b.action.replace(/\|/g, '\\|')} |`));

const lines = new Set(page.split('\n'));
const missing = rows.filter(row => !lines.has(row));
assert.deepEqual(missing, [], 'every binding has its row on docs/reference/keyboard.md');

console.log(`keyboard_docs: ${rows.length} bindings, all on the page`);
