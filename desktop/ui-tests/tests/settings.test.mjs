import assert from 'node:assert/strict';
import test from 'node:test';

import {
  fakeElement,
  fakePage,
  fresh,
  installDom,
  operatorConfig,
  resetShared,
  shared,
} from '../drivers/control-dom.mjs';

// Installed before the first import: `core` reads the platform and the
// connection mode once, when it loads.
let dom = installDom();

/** A fresh DOM and shared state, with the three config sections. */
async function page() {
  dom = installDom();
  const core = await resetShared();
  const pages = ['ai', 'integrations', 'channels'].map(fakePage);
  for (const section of pages) {
    dom.queries.set(`.config-page[data-page="${section.dataset.page}"]`, section);
  }
  dom.lists.set('.config-page', pages);
  core.store.snapshot = {
    operator: { config: operatorConfig(), secrets: { 'ai.api_key': true } },
  };
  return { core, pages };
}

/** A secret input, as `input[data-secret]` is, inside a section. */
function secretInput(section, name, value = '', clear = 'false') {
  return fakeElement({
    value,
    dataset: { secret: name, clear },
    closest: () => section,
    parentElement: fakeElement(),
  });
}

/** `setTimeout`, captured rather than run, for the duration of one test. */
function captureTimers(t) {
  const timers = [];
  const real = { set: globalThis.setTimeout, clear: globalThis.clearTimeout };
  globalThis.setTimeout = (callback, delay) => {
    timers.push({ callback, delay });
    return timers.length;
  };
  globalThis.clearTimeout = () => {};
  t.after(() => {
    globalThis.setTimeout = real.set;
    globalThis.clearTimeout = real.clear;
  });
  return timers;
}

test('a health snapshot preserves an unsaved provider draft', async () => {
  const { pages } = await page();
  const config = await shared('config');
  pages[0].classList.add('dirty');
  dom.element('ai-base').value = 'https://unsaved.example/v1';

  config.fillConfiguration();

  assert.equal(dom.element('ai-base').value, 'https://unsaved.example/v1');
  assert.equal(pages[0].classList.contains('dirty'), true);
});

test('model discovery leaves an unchanged saved key absent on the wire', async () => {
  await page();
  const config = await shared('config');
  dom.answer(async () => []);
  dom.element('ai-base').value = 'https://saved.example/v1';
  dom.element('ai-protocol').value = 'openai_compat';

  await config.discoverModels();

  const [{ args }] = dom.commands.filter(({ command }) => command === 'discover_provider_models');
  assert.equal(Object.hasOwn(args.payload, 'api_key'), false);
});

test('a discovery response cannot populate a different provider draft', async () => {
  await page();
  const config = await shared('config');
  let resolve;
  dom.answer(() => new Promise((done) => { resolve = done; }));
  dom.element('ai-base').value = 'https://first.example/v1';

  const pending = config.discoverModels();
  dom.element('ai-base').value = 'https://second.example/v1';
  resolve(['old-provider-model']);
  await pending;

  // The real `applyDiscoveredModels`, which would have filled the picker.
  assert.equal(dom.element('ai-model-count').textContent, '');
  assert.equal(dom.element('ai-model').children.length, 0);
});

test('saving integrations excludes provider and channel drafts and their secrets', async () => {
  const { core, pages } = await page();
  const config = await shared('config');
  dom.lists.set('input[data-secret]', [
    secretInput(pages[1], 'integrations.deepgram_api_key', 'replacement'),
    secretInput(pages[0], 'ai.api_key', 'unsaved-api-key'),
  ]);
  core.sectionRevisions.set('integrations', 7);
  dom.element('google-id').value = 'draft-google';
  dom.element('ai-base').value = 'https://unsaved.example';

  const patch = config.collectConfiguration('integrations');

  assert.equal(patch.expected_revision, 7);
  assert.equal(patch.section.name, 'integrations');
  assert.equal(patch.section.value.google_client_id, 'draft-google');
  assert.deepEqual(Object.keys(patch.secrets), ['integrations.deepgram_api_key']);
  assert.equal(patch.secrets['integrations.deepgram_api_key'].action, 'replace');
  assert.equal(Object.hasOwn(patch, 'config'), false);
});

test('secret keep and remove are distinct from replacement', async () => {
  const { pages } = await page();
  const config = await shared('config');
  const secret = secretInput(pages[0], 'ai.api_key');
  dom.lists.set('input[data-secret]', [secret]);

  assert.equal(config.collectConfiguration('ai').secrets['ai.api_key'].action, 'keep');
  secret.dataset.clear = 'true';
  assert.equal(config.collectConfiguration('ai').secrets['ai.api_key'].action, 'remove');
});

test('typing a replacement cancels a previously armed credential removal', async () => {
  const { pages } = await page();
  const config = await shared('config');
  const key = secretInput(pages[0], 'ai.api_key', 'replacement', 'true');

  config.markDirty(key);

  assert.equal(key.dataset.clear, 'false');
});

test('an existing snapshot does not prevent reconnect or hide an outage', async (t) => {
  await page();
  const events = await fresh('events');
  const timers = captureTimers(t);

  events.showSnapshotUnavailable('disconnected');
  events.scheduleSnapshotRetry();

  assert.equal(dom.element('snapshot-unavailable').hidden, false);
  // Said in words a person can act on, not the daemon's own error text.
  assert.match(dom.element('snapshot-unavailable-detail').textContent, /background service isn't running/);
  // The retry fires, which asks for a snapshot, which asks the shell.
  timers.shift().callback();
  timers.shift().callback();
  assert.ok(dom.commands.some(({ command }) => command === 'control_snapshot'));
});

test('an update that changes the Postgres major never reaches the installer', async () => {
  const { core } = await page();
  const actions = await shared('actions');
  core.store.appUpdate = {
    dataCompatibility: 'postgres-major-change',
    installedPostgresMajor: 18,
    candidatePostgresMajor: 19,
  };

  await actions.runDesktopAction(fakeElement({ dataset: { action: 'install-app-update' } }));

  assert.deepEqual(dom.commands.map(({ command }) => command), []);
  assert.match(dom.element('toast').textContent, /from Postgres 18 to Postgres 19/);
});

test('a refused update names the change it refuses', async () => {
  const updates = await shared('updates');
  const named = updates.postgresMajorChangeMessage({ installedPostgresMajor: 18, candidatePostgresMajor: 19 });
  assert.match(named, /from Postgres 18 to Postgres 19/);
  assert.match(named, /Nothing was changed/);
  assert.match(updates.postgresMajorChangeMessage({}), /a different Postgres version/);
});

/** A save in flight for `section`, recording how it completed. */
function pendingSave(core, section, extra = {}) {
  const outcome = {};
  core.pendingSaves.set('save', {
    page: section,
    button: fakeElement(),
    original: 'Save',
    complete: (value) => { outcome.completed = value; },
    ...extra,
  });
  return outcome;
}

test('save completion preserves edits made while the save was running', async (t) => {
  const { core, pages } = await page();
  const events = await fresh('events');
  captureTimers(t);
  core.store.snapshot = null;
  pages[0].classList.add('dirty');
  core.draftVersions.set('ai', 2);
  const outcome = pendingSave(core, pages[0], { version: 1 });

  events.handleLocaldEvent({ event: 'config.applied', id: 'save', operator: { config: { revision: 2 } } });

  assert.equal(pages[0].classList.contains('dirty'), true);
  assert.equal(outcome.completed, true);
});

test('a conflict preserves the draft and leaves a persistent section error', async (t) => {
  const { core, pages } = await page();
  const events = await fresh('events');
  captureTimers(t);
  pages[0].classList.add('dirty');
  const outcome = pendingSave(core, pages[0]);

  events.handleLocaldEvent({ event: 'error', id: 'save', code: 'config-conflict', message: 'Settings changed elsewhere' });

  assert.equal(pages[0].classList.contains('dirty'), true);
  assert.equal(pages[0].querySelector('.section-error')?.textContent, 'Settings changed elsewhere');
  assert.equal(outcome.completed, false);
});

test('reconnecting recovers a save whose completion event was lost', async (t) => {
  const { core, pages } = await page();
  const events = await fresh('events');
  captureTimers(t);
  pages[0].classList.add('dirty');
  const outcome = pendingSave(core, pages[0], { version: 0 });
  const operator = {
    config: operatorConfig({ revision: 2 }),
    secrets: {},
    readiness: { ai: 'ready', integrations: 'unset', surfaces: 'unset' },
  };

  // `state` because the daemon always sends it and the page requires it: a
  // snapshot without one cannot be rendered, and rendering the previous one as
  // though it were current is worse than saying so.
  events.handleLocaldEvent({
    event: 'control.snapshot',
    state: { ready: true },
    operator,
    config_operations: { save: { status: 'succeeded', operator } },
  });

  assert.equal(pages[0].classList.contains('dirty'), false);
  assert.equal(outcome.completed, true);
});

test('saving one section cannot silently rebase another draft past an unseen change', async (t) => {
  const { core, pages } = await page();
  const events = await fresh('events');
  captureTimers(t);
  core.store.snapshot = null;
  core.sectionRevisions.set('ai', 1);
  core.sectionRevisions.set('integrations', 2);
  pendingSave(core, pages[1], { version: 0, expectedRevision: 2 });

  events.handleLocaldEvent({ event: 'config.applied', id: 'save', operator: { config: { revision: 3 } } });

  assert.equal(core.sectionRevisions.get('ai'), 1);
  assert.equal(core.sectionRevisions.get('integrations'), 3);
});

test('the install-health switch is hidden unless this build can send anything', async () => {
  await page();
  const overview = await fresh('overview');
  // A build with no ingestion key sends nothing at all, so a switch would be a
  // control over nothing. The whole panel stays hidden rather than offering a
  // toggle that does not toggle anything.
  dom.answer(async () => ({ available: false, enabled: false, host: 'https://eu.i.posthog.com' }));
  dom.element('telemetry-panel').hidden = true;

  await overview.loadTelemetry();

  assert.equal(dom.element('telemetry-panel').hidden, true);
});

test('turning the install-health switch off is sent once and kept on failure', async () => {
  await page();
  const overview = await fresh('overview');
  const box = dom.element('telemetry-enabled');
  dom.element('telemetry-panel').hidden = true;
  dom.answer(async (command) => {
    if (command === 'telemetry_status') {
      return { available: true, enabled: true, host: 'https://eu.i.posthog.com', install_id: 'abcdef0123456789' };
    }
    throw new Error('the daemon said no');
  });

  await overview.loadTelemetry();

  assert.equal(dom.element('telemetry-panel').hidden, false);
  assert.equal(box.checked, true, 'the stored choice is what the switch shows');
  assert.match(dom.element('telemetry-detail').textContent, /eu\.i\.posthog\.com/);
  assert.match(dom.element('telemetry-detail').textContent, /abcdef01/, 'the install id is shown, abbreviated');

  box.checked = false;
  await box.listeners.change[0]();
  const sent = dom.commands.filter(({ command }) => command === 'set_telemetry_enabled');
  assert.equal(sent.length, 1);
  assert.equal(sent[0].args.enabled, false);
  assert.equal(box.checked, true, 'a refused save puts the switch back rather than lying');
});

test('a daemon that does not come back is asked less and less often', async (t) => {
  await page();
  const events = await fresh('events');
  const timers = captureTimers(t);

  const retries = [];
  for (let attempt = 0; attempt < 8; attempt += 1) {
    events.scheduleSnapshotRetry();
    // A second call while one is pending must not stack another timer.
    events.scheduleSnapshotRetry();
    const retry = timers.shift();
    retries.push(retry.delay);
    assert.equal(timers.length, 0, 'one retry at a time');
    // Firing it asks for a snapshot, which is its own short timer.
    retry.callback();
    timers.length = 0;
  }

  // It starts sooner than the flat five seconds it replaced, so the ordinary
  // case -- a daemon restarting -- is noticed faster, and it stops growing at
  // the ceiling rather than drifting to minutes.
  assert.deepEqual(retries.slice(0, 6), [1000, 2000, 4000, 8000, 16000, 30000]);
  assert.ok(retries.every((delay) => delay <= 30000), `${retries}`);

  // And a snapshot arriving puts it back, so the next outage is noticed
  // quickly rather than inheriting the interval the last one reached.
  events.resetSnapshotRetry();
  events.scheduleSnapshotRetry();
  assert.equal(timers[0].delay, 1000);
});
