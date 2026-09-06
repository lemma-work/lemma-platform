import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { after, before, test } from 'node:test';
import { chromium } from 'playwright';
let browser;
before(async () => { browser = await chromium.launch({ channel: process.env.LEMMA_TEST_BROWSER_CHANNEL || undefined }); });
after(async () => { await browser?.close(); });
async function prompt(t, overrides = {}) {
  const context = await browser.newContext({ viewport: { width: 650, height: 600 } });
  t.after(() => context.close());
  const page = await context.newPage();
  await page.route('https://desktop.test/**', async (route) => {
    const name = new URL(route.request().url()).pathname.slice(1);
    if (!['confirmation.html', 'confirmation.js'].includes(name)) return route.abort();
    await route.fulfill({ body: await readFile(new URL(`../../ui/${name}`, import.meta.url)), contentType: name.endsWith('html') ? 'text/html' : 'text/javascript' });
  });
  await page.addInitScript((overrides) => {
    window.__LEMMA_CONFIRMATION__ = { id: 'owned-operation', title: 'Erase local Lemma?', message: 'Permanently deletes local data.\nNo automatic backup.', confirmLabel: 'Erase Local Lemma', cancelable: true, ...overrides };
    window.calls = [];
    window.fail = false;
    window.__TAURI__ = { core: { invoke: async (command, args) => {
      window.calls.push({ command, args });
      if (window.fail) throw new Error('Confirmation is no longer active');
    } } };
  }, overrides);
  await page.goto('https://desktop.test/confirmation.html');
  await page.getByRole('dialog').waitFor();
  return page;
}
test('Enter and Escape cancel; only an explicit action approves cleanup', async (t) => {
  for (const key of ['Enter', 'Escape']) {
    const page = await prompt(t);
    assert.equal(await page.locator(':focus').textContent(), 'Cancel');
    await page.keyboard.press(key);
    assert.deepEqual(await page.evaluate(() => window.calls), [{ command: 'resolve_confirmation', args: { id: 'owned-operation', confirmed: false } }]);
  }
  const page = await prompt(t);
  await page.getByRole('button', { name: 'Erase Local Lemma' }).click();
  assert.deepEqual(await page.evaluate(() => window.calls), [{ command: 'resolve_confirmation', args: { id: 'owned-operation', confirmed: true } }]);
  assert.equal(await page.getByRole('button', { name: 'Erase Local Lemma' }).isDisabled(), true);
});
test('prompt copy is inert, keyboard focus is contained, and small windows scroll', async (t) => {
  const page = await prompt(t, { title: '<img src=x onerror=alert(1)>', message: 'Keep this text.\n'.repeat(80) });
  assert.equal(await page.locator('img').count(), 0);
  await page.setViewportSize({ width: 400, height: 320 });
  await page.keyboard.press('Tab');
  assert.equal(await page.locator(':focus').textContent(), 'Erase Local Lemma');
  await page.keyboard.press('Tab');
  assert.equal(await page.locator(':focus').textContent(), 'Cancel');
  assert.equal(await page.locator('dialog').evaluate((el) => el.scrollHeight > el.clientHeight), true);
});
test('failed approval remains visible and can be cancelled; notices have one Close button', async (t) => {
  const page = await prompt(t);
  await page.evaluate(() => { window.fail = true; });
  await page.getByRole('button', { name: 'Erase Local Lemma' }).click();
  await page.getByRole('alert').waitFor();
  assert.match(await page.getByRole('alert').textContent(), /no longer active/);
  assert.equal(await page.locator(':focus').textContent(), 'Cancel');
  const notice = await prompt(t, { cancelable: false, confirmLabel: 'Close' });
  assert.equal(await notice.getByRole('button').count(), 1);
  assert.equal(await notice.locator(':focus').textContent(), 'Close');
});
