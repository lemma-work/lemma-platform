import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import test from 'node:test';

test('Tauri web assets exclude the test harness and package dependencies', async () => {
  const desktop = new URL('../../', import.meta.url);
  const config = JSON.parse(await readFile(new URL('tauri.conf.json', desktop), 'utf8'));
  const assets = new URL(`${config.build.frontendDist}/`, desktop);
  const entries = await readdir(assets, { recursive: true });
  const forbidden = entries.filter((entry) =>
    /(^|[/\\])(node_modules|package(?:-lock)?\.json|tests)([/\\]|$)/.test(entry));
  assert.deepEqual(forbidden, [], 'Tauri bundles this directory; test dependencies must stay outside it');
  for (const page of ['index.html', 'control.html', 'control.js', 'control.css']) {
    assert.ok(entries.includes(page), `missing shipped asset: ${page}`);
  }
});
