import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import test from 'node:test';

test('Tauri web assets exclude the test harness and package dependencies', async () => {
  const desktop = new URL('../../', import.meta.url);
  const config = JSON.parse(await readFile(new URL('tauri.conf.json', desktop), 'utf8'));
  const assets = new URL(`${config.build.frontendDist}/`, desktop);
  // Forward slashes on every platform: Windows lists `control\\core.js`.
  const entries = (await readdir(assets, { recursive: true })).map((entry) => entry.replaceAll('\\', '/'));
  const forbidden = entries.filter((entry) =>
    /(^|[/\\])(node_modules|package(?:-lock)?\.json|tests)([/\\]|$)/.test(entry));
  assert.deepEqual(forbidden, [], 'Tauri bundles this directory; test dependencies must stay outside it');
  for (const page of [
    'index.html', 'splash.js', 'splash-orb.js', 'splash.css', 'screen-state.mjs',
    'control.html', 'control.js', 'control.css',
    'control/core.js', 'control/events.js', 'control/config.js',
    'confirmation.html', 'confirmation.js', 'confirmation.css',
  ]) {
    assert.ok(entries.includes(page), `missing shipped asset: ${page}`);
  }
});

test('the app\'s own pages run under a policy with no inline script or style', async () => {
  const desktop = new URL('../../', import.meta.url);
  const config = JSON.parse(await readFile(new URL('tauri.conf.json', desktop), 'utf8'));
  const directives = Object.fromEntries(config.app.security.csp.split(';').map((directive) => {
    const [name, ...sources] = directive.trim().split(/\s+/);
    return [name, sources];
  }));
  for (const name of ['script-src', 'style-src']) {
    assert.ok(directives[name], `${name} is declared`);
    assert.ok(!directives[name].includes("'unsafe-inline'"), `${name} allows inline code`);
  }
  // And nothing shipped still needs it: the browser suites enforce the same
  // policy, but only on the pages they load.
  const assets = new URL(`${config.build.frontendDist}/`, desktop);
  for (const page of (await readdir(assets)).filter((entry) => entry.endsWith('.html'))) {
    const html = await readFile(new URL(page, assets), 'utf8');
    assert.doesNotMatch(html, /<script(?![^>]*\ssrc=)[^>]*>/, `${page} has an inline script`);
    assert.doesNotMatch(html, /<style[\s>]/, `${page} has an inline stylesheet`);
    assert.doesNotMatch(html, /\son[a-z]+=/, `${page} has an inline event handler`);
    assert.doesNotMatch(html, /\sstyle=/, `${page} has an inline style attribute`);
  }
});
