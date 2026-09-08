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
        sharing: { mode: 'this_computer', phase: 'ready' },
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
      // The daemon's channel, with whatever a test wants to put on it.
      emit(event) { emit(event); },
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
        if (command === 'confirm_settings_changes') {
          if (fixture.failDecision) throw new Error('Confirmation unavailable');
          return new Promise(resolve => { fixture.finishDecision = value => { delete fixture.finishDecision; resolve(value); }; });
        }
        if (command === 'confirm_destructive_action') return fixture.publicDecision === true;
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

async function settingsDecision(page, decision) {
  await page.waitForFunction(() => typeof window.__fixture.finishDecision === 'function');
  await page.evaluate(value => window.__fixture.finishDecision(value), decision);
}

test('settings before deployment selection do not claim the workspace is in the cloud', async t => {
  const page = await settings(t, 'undecided');
  const description = await page.locator('#deployment-description').textContent();
  assert.match(description, /Choose Lemma Cloud or Local Lemma/);
  assert.doesNotMatch(description, /Your workspace data and orchestration live in Lemma Cloud/);
});

test('settings content remains readable when an embedded webview suspends animation', async (t) => {
  const page = await settings(t, 'cloud');
  await page.addStyleTag({ content: '* { animation-play-state: paused !important; }' });
  for (const name of ['Updates', 'This computer', 'Recovery']) {
    await page.getByRole('button', { name, exact: true }).click();
    const content = await page.locator('.page.active').evaluate(element => ({
      opacity: getComputedStyle(element).opacity,
      height: element.getBoundingClientRect().height,
      text: element.innerText.trim(),
    }));
    assert.equal(content.opacity, '1', `${name} must paint without waiting for animation frames`);
    assert.ok(content.height > 0 && content.text.length > 0, `${name} has readable content`);
  }
});

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
  await settingsDecision(page, 'cancel');
  assert.equal(await page.evaluate(() => window.__fixture.calls.some((call) => call.command === 'close_local_settings')), false);
  await page.locator('.config-page.active').getByRole('button', { name: 'Discard changes' }).click();
  assert.equal(await page.locator('#ai-base').inputValue(), 'https://saved.example/v1');
  assert.equal(await page.locator('#ai-key').inputValue(), '');
});

test('a single native decision owns close and Cancel restores the draft and focus', async (t) => {
  const page = await settings(t);
  await page.getByRole('button', { name: 'AI provider', exact: true }).click();
  await page.locator('#ai-base').fill('https://draft.example/v1');
  await page.getByRole('button', { name: 'Back to Lemma' }).click();
  await page.getByRole('button', { name: 'Back to Lemma' }).click();
  assert.equal(await page.evaluate(() => window.__fixture.calls.filter(call => call.command === 'confirm_settings_changes').length), 1);
  await settingsDecision(page, 'cancel');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'back-to-lemma');
  assert.equal(await page.locator('#ai-base').inputValue(), 'https://draft.example/v1');
  assert.equal(await page.evaluate(() => window.__fixture.calls.some(call => call.command === 'close_local_settings')), false);
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
  await settingsDecision(page, 'confirm');
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
});

test('discard cannot close settings while an admitted save is unfinished', async (t) => {
  const page = await settings(t);
  await page.getByRole('button', { name: 'AI provider', exact: true }).click();
  await page.locator('#ai-key').fill('pending-canary');
  await page.locator('.config-page.active [data-save]').click();
  await page.getByRole('button', { name: 'Back to Lemma' }).click();
  assert.match(await page.locator('#settings-close-status').textContent(), /still running/);
  assert.equal(await page.evaluate(() => window.__fixture.calls.some(call => call.command === 'confirm_settings_changes')), false);
  assert.equal(await page.evaluate(() => window.__fixture.calls.some((call) => call.command === 'close_local_settings')), false);
  await page.evaluate(() => window.__fixture.failSave());
  await page.getByRole('button', { name: 'Back to Lemma' }).click();
  await settingsDecision(page, 'discard');
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

test('a failed native decision preserves the draft and reports an inline error', async t => {
  const page = await settings(t);
  await page.getByRole('button', { name: 'AI provider', exact: true }).click();
  await page.locator('#ai-key').fill('unsaved-canary');
  await page.evaluate(() => { window.__fixture.failDecision = true; });
  await page.getByRole('button', { name: 'Back to Lemma' }).click();
  assert.match(await page.locator('#settings-close-status').textContent(), /Confirmation unavailable/);
  await page.evaluate(() => window.__fixture.refresh());
  assert.equal(await page.locator('#ai-key').inputValue(), 'unsaved-canary');
  assert.equal(await page.locator('#settings-close-status').isVisible(), true);
  assert.equal(await page.evaluate(() => window.__fixture.calls.some(call => call.command === 'close_local_settings')), false);
});

test('public sharing requires an affirmative app-owned confirmation on every activation', async t => {
  const page = await settings(t);
  await page.evaluate(() => {
    window.__fixture.snapshot.sharing.provider_readiness = { ngrok: { installed: true, authenticated: true } };
    window.__fixture.refresh();
  });
  await page.getByRole('button', { name: 'Sharing', exact: true }).click();
  await page.getByRole('radio', { name: /Public link/ }).check();
  await page.getByRole('button', { name: 'Create public link', exact: true }).click();
  assert.equal(await page.evaluate(() => window.__fixture.calls.some(call => call.command === 'sharing_action' && call.args.action === 'enable')), false);
  await page.evaluate(() => { window.__fixture.publicDecision = true; });
  await page.getByRole('button', { name: 'Create public link', exact: true }).click();
  const calls = await page.evaluate(() => window.__fixture.calls);
  assert.equal(calls.filter(call => call.command === 'confirm_destructive_action').length, 2);
  const enabled = calls.filter(call => call.command === 'sharing_action' && call.args.action === 'enable');
  assert.equal(enabled.length, 1);
  assert.equal(enabled[0].args.payload.public_warning_confirmed, true);
});

// This window can reinstall Lemma and write credentials, and its CSP allows
// inline script. The QR arrives as markup on the daemon's event stream, so if
// it ever reaches the DOM as HTML rather than as an image, anything that can
// write to that stream runs code with the settings window's privileges.
test('a QR code from the event stream is rendered as an image, never as live markup', async t => {
  const page = await settings(t);
  await page.evaluate(() => {
    window.__fixture.snapshot.sharing = {
      mode: 'local_network',
      phase: 'ready',
      canonical_url: 'https://192.168.1.20:7423',
      interfaces: [{ name: 'en0', address: '192.168.1.20' }],
      selected_interface: 'en0',
      qr_svg: '<svg xmlns="http://www.w3.org/2000/svg"><script>window.__pwned = true;<\/script></svg>',
    };
    window.__fixture.refresh();
  });
  await page.getByRole('button', { name: 'Sharing', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#sharing-qr-image img'));

  assert.equal(
    await page.evaluate(() => window.__pwned),
    undefined,
    'markup from the daemon must not execute in the settings window',
  );
  assert.equal(
    await page.evaluate(() => document.querySelector('#sharing-qr-image script')),
    null,
    'the QR must not be injected as live markup',
  );
  assert.ok(
    await page.evaluate(() => document.querySelector('#sharing-qr-image img').src.startsWith('data:image/svg+xml')),
    'the QR should load through an image, which cannot run script',
  );
  assert.ok(
    await page.evaluate(() => document.querySelector('#sharing-qr-image img').alt.length > 0),
    'the QR needs a text alternative',
  );
});

test('native Save keeps the page open after a failed apply or a newer draft', async t => {
  for (const fail of [true, false]) {
    const page = await settings(t);
    await page.getByRole('button', { name: 'AI provider', exact: true }).click();
    await page.locator('#ai-key').fill('first-canary');
    await page.getByRole('button', { name: 'Back to Lemma' }).click();
    await settingsDecision(page, 'confirm');
    await page.waitForFunction(() => window.__fixture.calls.some(call => call.command === 'apply_operator_config'));
    if (fail) await page.evaluate(() => window.__fixture.failSave());
    else {
      await page.locator('#ai-key').fill('newer-canary');
      await page.evaluate(() => window.__fixture.completeSave());
    }
    await page.locator('#settings-close-status').waitFor();
    assert.equal(await page.locator('#ai-key').inputValue(), fail ? 'first-canary' : 'newer-canary');
    assert.equal(await page.evaluate(() => window.__fixture.calls.some(call => call.command === 'close_local_settings')), false);
  }
});

// Escape closed the whole settings window from anywhere, including from
// inside a field being typed into. Dismissing the browser's own suggestion
// list while filling in a key should not throw the page away.
test('Escape leaves the field before it leaves settings', async t => {
  const page = await settings(t);
  await page.getByRole('button', { name: 'AI provider', exact: true }).click();
  await page.locator('#ai-base').focus();

  await page.keyboard.press('Escape');
  assert.deepEqual(
    await page.evaluate(() => window.__fixture.calls
      .filter(call => ['close_local_settings', 'confirm_settings_changes'].includes(call.command))),
    [],
    'the first Escape belongs to the field',
  );
  assert.notEqual(
    await page.evaluate(() => document.activeElement?.id), 'ai-base',
    'and it should have left the field',
  );

  await page.keyboard.press('Escape');
  await page.waitForFunction(() => window.__fixture.calls
    .some(call => call.command === 'close_local_settings'));
});

// Events cross the bridge from the daemon and were read here unparsed. A
// `config.applied` without an `operator` released the save button and dropped
// the pending save, *then* threw before refilling the page -- leaving stale
// settings on screen, no error, and a save the page believed had succeeded.
test('an event missing what it needs is reported, not half-applied', async t => {
  const page = await settings(t);
  await page.getByRole('button', { name: 'AI provider', exact: true }).click();
  await page.locator('#ai-key').fill('a-key');
  await page.locator('.config-page.active [data-save]').click();

  const id = await page.waitForFunction(() => window.__fixture.calls
    .findLast(call => call.command === 'apply_operator_config')?.args.id)
    .then(handle => handle.jsonValue());

  await page.evaluate(saveId => window.__fixture.emit({ event: 'config.applied', id: saveId }), id);

  await page.getByText('config.applied arrived without operator.config.revision', { exact: false })
    .waitFor();
  assert.equal(
    await page.locator('#ai-key').inputValue(), 'a-key',
    'the draft has to survive an event the page could not use',
  );
});

// A snapshot the page cannot read must not leave the previous one on screen
// looking current. Showing nothing is the honest outcome; showing stale
// numbers as live ones is the one worse than that.
test('a snapshot the page cannot read reports the daemon as unreachable', async t => {
  const page = await settings(t);
  await page.evaluate(() => window.__fixture.emit({ event: 'control.snapshot' }));

  await page.waitForFunction(() => !document.getElementById('snapshot-unavailable').hidden);
  assert.equal(await page.locator('#state-pill').textContent(), 'Disconnected');
});

test('an event that is not an object at all is ignored without throwing', async t => {
  const page = await settings(t);
  for (const payload of [null, 'ready', 42, []]) {
    await page.evaluate(value => window.__fixture.emit(value), payload);
  }
  await page.waitForFunction(() => !document.getElementById('snapshot-unavailable').hidden);
  // The `pageerror` assertion in `settings` is the other half of this: an
  // uncaught TypeError in the listener is what used to happen here.
});
