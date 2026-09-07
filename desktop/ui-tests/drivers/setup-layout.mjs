import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { after, before, test } from 'node:test';
import { chromium } from 'playwright';

const frontend = fileURLToPath(new URL('../../../lemma-frontend/', import.meta.url));
const require = createRequire(`${frontend}/package.json`);
const { createServer } = await import(require.resolve('vite'));
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const postcss = require('postcss');
const tailwind = require('@tailwindcss/postcss');
let browser;
let renderer;
const pages = new Map();
before(async () => {
  renderer = await createServer({
    root: frontend, configFile: false,
    resolve: { alias: { '@': frontend } },
    esbuild: { jsx: 'automatic' },
    server: { middlewareMode: true, watch: null },
  });
  const { SetupShell, SetupSplitPanel, SetupPrimaryButton, SetupStandalonePage, SetupPanel } = await renderer.ssrLoadModule('/components/onboarding/account-onboarding-chrome.tsx');
  const h = React.createElement;
  const fields = Array.from({ length: 30 }, (_, i) => h('label', { key: i, className: 'block py-3' },
    `Setup field ${i}`, h('input', { className: 'block', 'aria-label': `Setup field ${i}` })));
  const markup = new Map();
  for (const [name, Component] of [['split', SetupSplitPanel], ['standalone', SetupStandalonePage], ['panel', SetupPanel]]) {
    markup.set(name, renderToStaticMarkup(h(SetupShell, { fullBleed: true }, h(Component, {
      title: 'What should answer in your chats?',
      subtitle: 'A coding agent on this computer, an API provider, or both.',
      currentStep: 'intelligence', onBack() {},
      footer: h(SetupPrimaryButton, { type: 'button' }, 'Continue'),
      preview: h('p', {}, 'Your local agents'),
    }, h('div', {}, fields)))));
  }
  const source = `${frontend}/app/globals.css`;
  const css = await postcss([tailwind({ base: frontend })]).process(await readFile(source, 'utf8'), { from: source });
  for (const [name, content] of markup) pages.set(name, `<!doctype html><html><head><style>${css.css}</style></head><body>${content}</body></html>`);
  browser = await chromium.launch({ channel: process.env.LEMMA_TEST_BROWSER_CHANNEL || undefined });
});
after(async () => {
  await browser?.close();
  await renderer?.close();
});

for (const layout of ['split', 'standalone', 'panel']) {
for (const [width, height, textSize] of [[980, 650, 16], [1280, 828, 16], [980, 650, 32], [640, 480, 24]]) {
  test(`onboarding ${layout} actions remain visible at ${width}x${height}, ${textSize}px text`, async t => {
    const page = await browser.newPage({ viewport: { width, height }, reducedMotion: 'reduce' });
    t.after(() => page.close());
    await page.setContent(pages.get(layout));
    await page.addStyleTag({ content: `html { font-size: ${textSize}px; }` });
    await page.evaluate(async () => {
      await document.fonts.ready;
      await new Promise(requestAnimationFrame);
      await new Promise(requestAnimationFrame);
    });
    const action = page.getByRole('button', { name: 'Continue', exact: true });
    const before = await action.boundingBox();
    assert.ok(before && before.y >= 0 && before.y + before.height <= height, 'Continue is fully inside the viewport');
    assert.ok(before.x >= 0 && before.x + before.width <= width, 'Continue is horizontally visible');
    const overflow = await page.evaluate(() => document.documentElement.scrollHeight > innerHeight);
    assert.equal(overflow, false, 'the page itself must not scroll its actions away');
    await page.getByRole('textbox', { name: 'Setup field 29', exact: true }).focus();
    const after = await action.boundingBox();
    assert.equal(after.y, before.y, 'focusing the last setup field must not move Continue');
    assert.ok(await page.getByTestId('setup-content').evaluate(el => el.scrollTop > 0));
    await action.click({ trial: true });
  });
}

}
