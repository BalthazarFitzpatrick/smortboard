// asks the REAL computeColumnFit (smortboard/ui/columns.js) which regime each demo column lands
// in. not under tests/js/ on purpose: the js suite runs every tests/js/*.mjs with no arguments,
// and this one is driven by tests/test_demo_regimes.py, which passes the demo's own column counts
// and the viewport they are stated at as one JSON argument.
//
// in: {"width": px, "available": px, "gap": px, "boards": [{"board": name, "columns": {col: n}}]}
// out (stdout, JSON): [{"board": name, "regimes": {col: regime}}]
import {readFileSync} from 'node:fs';
import {installStubDom} from '../js/dom_stub.mjs';

const root = new URL('../../', import.meta.url);
installStubDom({fetchImpl: () => new Promise(() => {})});
const columns = readFileSync(new URL('smortboard/ui/columns.js', root), 'utf8');
const {computeColumnFit} = new Function(`${columns}\n;return {computeColumnFit};`)();

const spec = JSON.parse(process.argv[2]);
const out = spec.boards.map(board => {
  const regimes = {};
  for (const [column, count] of Object.entries(board.columns)) {
    regimes[column] = computeColumnFit(count, spec.available, spec.gap, spec.width).regime;
  }
  return {board: board.board, regimes};
});
process.stdout.write(JSON.stringify(out));
