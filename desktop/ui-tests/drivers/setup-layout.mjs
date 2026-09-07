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
  const { SetupShell, SetupSplitPanel, SetupPrimaryButton, SetupStandalonePage, SetupPanel, SetupChoicesPage } = await renderer.ssrLoadModule('/components/onboarding/account-onboarding-chrome.tsx');
  const { HarnessRow } = await renderer.ssrLoadModule('/components/agents/harness-row.tsx');
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
  const agents = ['Claude Code', 'Codex', 'OpenCode', 'Cursor'].map((name, index) => h(HarnessRow, {
    key: name, compact: true, className: 'border border-[var(--border-subtle)]',
    harness: { harness_key: `qa-agent-${index}`, display_name: name, adapter_version: '1',
      health: index === 3 ? 'PROBE_FAILED' : 'READY',
      stale_reason: index === 3 ? 'The agent executable is missing. Install it, then rescan.' : null,
      config_options: [{ category: 'model', options: [{ value: 'model', name: 'Model' }] }],
    },
    action: usable => usable ? h('button', { className: 'px-2 py-1 text-sm' }, 'Use in chats') : null,
  }));
  markup.set('choices', renderToStaticMarkup(h(SetupShell, { fullBleed: true }, h(SetupChoicesPage, {
    title: 'What should answer in your chats?',
    subtitle: 'Use an agent on this computer, connect a model provider, or set up both.',
    footer: h(SetupPrimaryButton, {}, 'Continue'),
  },
  h('section', { className: 'space-y-2' }, h('p', { className: 'py-2 text-xs' }, 'Agents on this computer'), ...agents,
    h('p', { className: 'mt-3 text-xs' }, 'Agents use their own sign-in. No API key needed.')),
  h('section', { className: 'space-y-3 md:border-l md:pl-8' }, h('p', { className: 'text-xs' }, 'Model providers'),
    h('div', { className: 'grid grid-cols-2 gap-2' }, ...['Ollama', 'LM Studio', 'OpenAI', 'Anthropic', 'OpenRouter'].map(name => h('button', { key: name, className: 'setup-path-choice flex flex-col px-3 py-2 text-left' }, h('span', { className: 'text-sm' }, name), h('span', { className: 'text-xs' }, 'API key or local server')))),
    h('p', { className: 'mt-3 text-xs' }, 'The provider is shared by this local installation. Prompts and tool results can be sent to the service you choose.'))
  ))));
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

for (const [width, height] of [[980, 650], [1273, 828], [1511, 870]]) {
  test(`agent and provider choices fit without scrolling at ${width}x${height}`, async t => {
    const page = await browser.newPage({ viewport: { width, height } });
    t.after(() => page.close());
    await page.setContent(pages.get('choices'));
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.getByTestId('setup-content').evaluate(el => el.scrollHeight > el.clientHeight), false);
    await page.getByRole('button', { name: 'Continue', exact: true }).click({ trial: true });
    await page.getByRole('button', { name: /OpenRouter/ }).click({ trial: true });
    if (process.env.LEMMA_LAYOUT_SCREENSHOT && width === 1273) {
      await page.screenshot({ path: process.env.LEMMA_LAYOUT_SCREENSHOT });
    }
  });
}
