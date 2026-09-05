import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { after, before, test } from 'node:test';
import { chromium } from 'playwright';

let browser;
before(async () => {
  browser = await chromium.launch({ channel: process.env.LEMMA_TEST_BROWSER_CHANNEL || undefined });
});
after(async () => { await browser?.close(); });

async function settings(t, mode = 'local', daemonOffline = false) {
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
      body: await readFile(new URL(`../../ui/${name}`, import.meta.url)),
      contentType: name.endsWith('.html') ? 'text/html' : name.endsWith('.css') ? 'text/css' : 'text/javascript',
    });
  });
  await page.addInitScript(({mode, daemonOffline}) => {
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
      failSave(message = 'Settings changed elsewhere. Review and retry.') {
        const { args } = this.calls.findLast((call) => call.command === 'apply_operator_config');
        emit({ event: 'error', id: args.id, code: 'config-conflict', message });
      },
      completeSave(emitEvent = true) {
        const { args } = this.calls.findLast((call) => call.command === 'apply_operator_config');
        const { section } = args.payload;
        this.snapshot.operator.config[section.name] = section.value;
        this.snapshot.operator.config.revision += 1;
        this.snapshot.config_operations = {
          ...this.snapshot.config_operations,
          [args.id]: { status: 'succeeded', operator: structuredClone(this.snapshot.operator) },
        };
        if (emitEvent) emit({ event: 'config.applied', id: args.id, operator: structuredClone(this.snapshot.operator) });
      },
    };
    if (mode !== 'local') {
      fixture.snapshot.services = null;
      fixture.snapshot.managed_runtime = null;
      fixture.snapshot.state = { ready: false, running: false, status: 'stopped' };
    }
    window.__fixture = fixture;
    window.__LEMMA_DESKTOP__ = { mode };
    window.__TAURI__ = {
      core: { async invoke(command, args) {
        fixture.calls.push({ command, args });
        if (command === 'control_snapshot') {
          if (daemonOffline) throw new Error('The old daemon cannot start');
          fixture.refresh(); return;
        }
        if (command === 'reset_full_reinstall' || command === 'reset_local_data') return 'cancelled';
        if (command === 'runtime_info') return { desktopRelease: 'test', repairAvailable: false };
        if (command === 'check_for_app_update') return { updatesSupported: false, currentVersion: 'test', channel: 'dev' };
        if (command === 'discover_provider_models') {
          if (fixture.delayDiscovery) return new Promise((resolve) => { fixture.finishDiscovery = resolve; });
          return ['discovered'];
        }
      } },
      event: { listen(name, listener) { listeners[name] = listener; return Promise.resolve(() => {}); } },
    };
  }, {mode, daemonOffline});
  await page.goto('https://desktop.test/control.html');
  if (daemonOffline) await page.waitForFunction(() => !document.getElementById('snapshot-unavailable').hidden);
  else await page.waitForFunction(() => document.getElementById('metric-ai').textContent === 'Ready');
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
  assert.equal(await page.locator('#attention-banner').isVisible(), false, 'cloud mode has no local application stack to repair');
  assert.equal(await page.getByRole('button', { name: 'AI provider', exact: true }).isDisabled(), true);
  await page.getByRole('button', { name: 'Open agent setup in Lemma' }).click();
  const commands = await page.evaluate(() => window.__fixture.calls.map((call) => call.command));
  assert.equal(commands.includes('open_app'), true);
  assert.equal(commands.includes('runtime.prepare'), false);
  assert.equal(commands.includes('prepare_runtime'), false);
  assert.equal(commands.includes('start'), false);
});

test('a failed apply keeps credentials and its inline error through refresh', async (t) => {
  const page = await settings(t);
  await page.getByRole('button', { name: 'AI provider', exact: true }).click();
  await page.locator('#ai-key').fill('replacement-canary');
  await page.locator('.config-page.active [data-save]').click();
  await page.evaluate(() => window.__fixture.failSave());
  await page.evaluate(() => window.__fixture.refresh());
  assert.match(await page.locator('.config-page.active .section-error').textContent(), /changed elsewhere/);
  assert.equal(await page.locator('#ai-key').inputValue(), 'replacement-canary');
  assert.equal(await page.locator('.config-page.active [data-save]').isEnabled(), true);
  await page.locator('.config-page.active [data-save]').click();
  const payload = await page.evaluate(() => window.__fixture.calls.findLast((call) => call.command === 'apply_operator_config').args.payload);
  assert.equal(payload.expected_revision, 1);
  assert.deepEqual(payload.secrets['ai.api_key'], { action: 'replace', value: 'replacement-canary' });
});

test('reconnect consumes a durable save outcome when the completion event was lost', async (t) => {
  const page = await settings(t);
  await page.getByRole('button', { name: 'AI provider', exact: true }).click();
  await page.locator('#ai-key').fill('save-canary');
  await page.locator('.config-page.active [data-save]').click();
  await page.evaluate(() => {
    window.__fixture.disconnect();
    window.__fixture.completeSave(false);
    window.__fixture.refresh();
  });
  assert.equal(await page.locator('#ai-key').inputValue(), '');
  assert.equal(await page.locator('.config-page.active').evaluate((node) => node.classList.contains('dirty')), false);
  await page.getByRole('button', { name: 'Back to Lemma' }).click();
  assert.equal(await page.evaluate(() => window.__fixture.calls.filter((call) => call.command === 'close_local_settings').length), 1);
  assert.equal(await page.evaluate(() => window.__fixture.calls.filter((call) => call.command === 'apply_operator_config').length), 1);
});

test('model discovery reuses the saved credential and rejects an answer for an old endpoint', async (t) => {
  const page = await settings(t);
  await page.getByRole('button', { name: 'AI provider', exact: true }).click();
  await page.evaluate(() => { window.__fixture.delayDiscovery = true; });
  await page.locator('#ai-discover').click();
  const payload = await page.evaluate(() => window.__fixture.calls.findLast((call) => call.command === 'discover_provider_models').args.payload);
  assert.equal(Object.hasOwn(payload, 'api_key'), false);
  await page.locator('#ai-base').fill('https://new-draft.example/v1');
  await page.evaluate(() => window.__fixture.finishDiscovery(['stale-model']));
  await page.waitForFunction(() => !document.getElementById('ai-discover').disabled);
  assert.equal(await page.locator('#ai-model-panel').isVisible(), false);
  assert.equal(await page.locator('#ai-model option').count(), 0);
});

test('closing with two dirty sections saves them sequentially before leaving', async (t) => {
  const page = await settings(t);
  await page.getByRole('button', { name: 'AI provider', exact: true }).click();
  await page.locator('#ai-key').fill('provider-canary');
  await page.getByRole('button', { name: 'Integrations', exact: true }).click();
  await page.locator('summary').filter({ hasText: 'Gmail, Calendar, and Drive OAuth app' }).click();
  await page.locator('#google-id').fill('integration-canary');
  await page.getByRole('button', { name: 'Back to Lemma' }).click();
  await page.locator('#unsaved-save').click();
  await page.waitForFunction(() => window.__fixture.calls.filter((call) => call.command === 'apply_operator_config').length === 1);
  assert.equal(await page.evaluate(() => window.__fixture.calls.some((call) => call.command === 'close_local_settings')), false);
  await page.evaluate(() => window.__fixture.completeSave());
  await page.waitForFunction(() => window.__fixture.calls.filter((call) => call.command === 'apply_operator_config').length === 2);
  const saves = await page.evaluate(() => window.__fixture.calls.filter((call) => call.command === 'apply_operator_config').map((call) => call.args.payload));
  assert.deepEqual(saves.map((save) => [save.section.name, save.expected_revision]), [['ai', 1], ['integrations', 2]]);
  assert.equal(saves[1].section.value.google_client_id, 'integration-canary');
  assert.equal(Object.hasOwn(saves[1].secrets, 'ai.api_key'), false);
  await page.evaluate(() => window.__fixture.completeSave());
  await page.waitForFunction(() => window.__fixture.calls.some((call) => call.command === 'close_local_settings'));
  assert.equal(await page.locator('#unsaved-dialog').isVisible(), false);
});

test('discard cannot close settings while an admitted save is unfinished', async (t) => {
  const page = await settings(t);
  await page.getByRole('button', { name: 'AI provider', exact: true }).click();
  await page.locator('#ai-key').fill('pending-canary');
  await page.locator('.config-page.active [data-save]').click();
  await page.getByRole('button', { name: 'Back to Lemma' }).click();
  await page.locator('#unsaved-discard').click();
  assert.match(await page.locator('#unsaved-status').textContent(), /still running/);
  assert.equal(await page.evaluate(() => window.__fixture.calls.some((call) => call.command === 'close_local_settings')), false);
  await page.evaluate(() => window.__fixture.failSave());
  await page.locator('#unsaved-discard').click();
  assert.equal(await page.evaluate(() => window.__fixture.calls.filter((call) => call.command === 'close_local_settings').length), 1);
});

test('force cleanup is reachable without a daemon and cancellation never reports erased data', async (t) => {
  const page = await settings(t, 'hosted', true);
  await page.getByRole('button', { name: 'Recovery', exact: true }).click();
  await page.getByRole('button', { name: 'Force cleanup and reinstall', exact: true }).click();
  const calls = await page.evaluate(() => window.__fixture.calls);
  assert.equal(calls.filter((call) => call.command === 'reset_full_reinstall').length, 1);
  assert.equal(calls.some((call) => call.command === 'prepare_runtime' || call.command === 'start'), false);
  assert.doesNotMatch(await page.locator('#toast').textContent(), /was removed|were removed|was erased/);
  assert.equal(await page.getByRole('button', { name: 'Force cleanup and reinstall', exact: true }).isEnabled(), true);
});
