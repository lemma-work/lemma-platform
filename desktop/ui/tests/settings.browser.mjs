import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { after, before, test } from 'node:test';
import { chromium } from 'playwright';

let browser;
before(async () => {
  browser = await chromium.launch({ channel: process.env.LEMMA_TEST_BROWSER_CHANNEL || undefined });
});
after(async () => { await browser?.close(); });

async function settings(t, mode = 'local') {
  const context = await browser.newContext({ viewport: { width: 1000, height: 760 } });
  t.after(() => context.close());
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  await page.route('https://desktop.test/**', async (route) => {
    const name = new URL(route.request().url()).pathname.slice(1);
    if (!['control.html', 'control.js', 'control.css'].includes(name)) return route.abort();
    await route.fulfill({
      body: await readFile(new URL(`../${name}`, import.meta.url)),
      contentType: name.endsWith('.html') ? 'text/html' : name.endsWith('.css') ? 'text/css' : 'text/javascript',
    });
  });
  await page.addInitScript((mode) => {
    const listeners = {};
    const emit = (event) => listeners['lemma:locald-event']?.({ payload: event });
    const fixture = {
      calls: [],
      snapshot: {
        event: 'control.snapshot',
        state: { ready: true, running: true, status: 'ready' },
        services: [{ id: 'backend', running: true }, { id: 'frontend', running: true }],
        managed_runtime: {}, agent_host: { available: true, running: true, targets: [] },
        sharing: { mode: 'this_computer', phase: 'idle' },
        operator: {
          config: {
            revision: 1, install_id: 'test', schema_version: 1,
            ai: { protocol: 'openai_compat', base_url: 'https://saved.example/v1', default_model: 'saved', models: ['saved'], vision_models: [] },
            integrations: { composio_enabled: false, google_client_id: '', microsoft_client_id: '', github_client_id: '', slack_client_id: '' },
            surfaces: { slack_socket_mode: false, telegram_polling: false, teams_app_id: '', teams_tenant_id: '', whatsapp_phone_number_id: '', whatsapp_waba_id: '', resend_inbound_domain: '' },
          },
          secrets: { 'ai.api_key': true },
          readiness: { ai: 'ready', integrations: 'optional', surfaces: 'optional' },
        },
      },
      refresh() { emit(structuredClone(this.snapshot)); },
      disconnect() { listeners['lemma:locald-disconnected']?.({ payload: null }); },
      completeSave() {
        const { args } = this.calls.findLast((call) => call.command === 'apply_operator_config');
        const { section } = args.payload;
        this.snapshot.operator.config[section.name] = section.value;
        this.snapshot.operator.config.revision += 1;
        emit({ event: 'config.applied', id: args.id, operator: structuredClone(this.snapshot.operator) });
      },
    };
    window.__fixture = fixture;
    window.__LEMMA_DESKTOP__ = { mode };
    window.__TAURI__ = {
      core: { async invoke(command, args) {
        fixture.calls.push({ command, args });
        if (command === 'control_snapshot') { fixture.refresh(); return; }
        if (command === 'runtime_info') return { desktopRelease: 'test', repairAvailable: false };
        if (command === 'check_for_app_update') return { updatesSupported: false, currentVersion: 'test', channel: 'dev' };
        if (command === 'discover_provider_models') return ['discovered'];
      } },
      event: { listen(name, listener) { listeners[name] = listener; return Promise.resolve(() => {}); } },
    };
  }, mode);
  await page.goto('https://desktop.test/control.html');
  await page.waitForFunction(() => document.getElementById('metric-ai').textContent === 'Ready');
  return page;
}

test('real settings DOM preserves drafts across health refresh, navigation, and closing', async (t) => {
  const page = await settings(t);
  await page.getByRole('button', { name: 'AI provider', exact: true }).click();
  await page.locator('#ai-base').fill('https://draft.example/v1');
  await page.locator('#ai-key').fill('draft-key');
  await page.evaluate(() => window.__fixture.refresh());
  assert.equal(await page.locator('#ai-base').inputValue(), 'https://draft.example/v1');
  assert.equal(await page.locator('#ai-key').inputValue(), 'draft-key');
  await page.evaluate(() => window.__fixture.disconnect());
  assert.equal(await page.locator('#state-pill').textContent(), 'Disconnected');
  assert.equal(await page.locator('#ai-key').inputValue(), 'draft-key');
  await page.evaluate(() => window.__fixture.refresh());
  assert.equal(await page.locator('#snapshot-unavailable').isVisible(), false);
  await page.getByRole('button', { name: 'This computer', exact: true }).click();
  await page.getByRole('button', { name: 'AI provider', exact: true }).click();
  assert.equal(await page.locator('#ai-key').inputValue(), 'draft-key');
  await page.getByRole('button', { name: 'Back to Lemma' }).click();
  await page.getByRole('dialog', { name: 'Save your settings changes?' }).waitFor();
  assert.equal(await page.evaluate(() => document.activeElement.id), 'unsaved-cancel');
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#unsaved-dialog').isVisible(), false);
  assert.equal(await page.evaluate(() => window.__fixture.calls.some((call) => call.command === 'close_local_settings')), false);
  await page.locator('.config-page.active').getByRole('button', { name: 'Discard changes' }).click();
  assert.equal(await page.locator('#ai-base').inputValue(), 'https://saved.example/v1');
  assert.equal(await page.locator('#ai-key').inputValue(), '');
});

test('a save submits one section and does not erase typing during activation', async (t) => {
  const page = await settings(t);
  await page.getByRole('button', { name: 'AI provider', exact: true }).click();
  await page.locator('#ai-key').fill('first-key');
  await page.locator('.config-page.active [data-save]').click();
  const payload = await page.evaluate(() => window.__fixture.calls.find((call) => call.command === 'apply_operator_config').args.payload);
  assert.equal(payload.section.name, 'ai');
  assert.equal(payload.expected_revision, 1);
  assert.deepEqual(payload.secrets['ai.api_key'], { action: 'replace', value: 'first-key' });
  assert.equal(Object.hasOwn(payload, 'config'), false);
  await page.locator('#ai-key').fill('second-key');
  await page.evaluate(() => window.__fixture.completeSave());
  assert.equal(await page.locator('#ai-key').inputValue(), 'second-key');
  assert.equal(await page.locator('.config-page.active').evaluate((node) => node.classList.contains('dirty')), true);
});

test('cloud mode opens this computer without provisioning a local stack', async (t) => {
  const page = await settings(t, 'hosted');
  assert.equal(await page.locator('#page-title').textContent(), 'This computer');
  assert.equal(await page.getByRole('button', { name: 'AI provider', exact: true }).isDisabled(), true);
  await page.getByRole('button', { name: 'Open agent setup in Lemma' }).click();
  const commands = await page.evaluate(() => window.__fixture.calls.map((call) => call.command));
  assert.equal(commands.includes('open_app'), true);
  assert.equal(commands.includes('runtime.prepare'), false);
  assert.equal(commands.includes('prepare_runtime'), false);
  assert.equal(commands.includes('start'), false);
});
