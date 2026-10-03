import assert from 'node:assert/strict';
import {mkdtempSync, mkdirSync, writeFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {uiBaseAsset} from './dom_stub.mjs';

const fixture = mkdtempSync(join(tmpdir(), 'smortboard-assets-'));
const previous = {...process.env};
try {
  const installed = join(fixture, 'installed');
  const sibling = join(fixture, 'smortui', 'ui_base', 'assets');
  mkdirSync(installed);
  mkdirSync(sibling, {recursive: true});
  writeFileSync(join(installed, 'menu.js'), 'installed version');
  writeFileSync(join(sibling, 'menu.js'), 'unreleased sibling version');
  const repo = pathToFileURL(`${join(fixture, 'repo')}/`);
  process.env.UI_BASE_ASSETS_DIR = installed;
  delete process.env.UI_BASE_DEV_ASSETS_DIR;
  assert.equal(uiBaseAsset(repo, 'menu.js'), 'installed version');
  process.env.UI_BASE_DEV_ASSETS_DIR = sibling;
  assert.equal(uiBaseAsset(repo, 'menu.js'), 'unreleased sibling version');
  delete process.env.UI_BASE_DEV_ASSETS_DIR;
  delete process.env.UI_BASE_ASSETS_DIR;
  assert.throws(() => uiBaseAsset(repo, 'menu.js'), /UI_BASE_ASSETS_DIR/);
} finally {
  for (const key of ['UI_BASE_ASSETS_DIR', 'UI_BASE_DEV_ASSETS_DIR']) {
    if (previous[key] === undefined) delete process.env[key];
    else process.env[key] = previous[key];
  }
  rmSync(fixture, {recursive: true});
}
console.log('asset source: ok');
