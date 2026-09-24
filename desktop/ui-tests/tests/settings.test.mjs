import assert from 'node:assert/strict';
import test from 'node:test';

import {
  fakeElement,
  fresh,
  installDom,
  resetShared,
  shared,
} from '../drivers/control-dom.mjs';

// Installed before the first import: `core` reads the platform and the
// connection mode once, when it loads.
let dom = installDom();

/** A fresh DOM and shared state, as a newly opened Local settings has them. */
async function page() {
  dom = installDom();
  const core = await resetShared();
  return { core };
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

// Provider drafts, model discovery, saving one section at a time and keeping
// or removing a stored secret moved with their forms to the workspace's own
// Settings, under This Mac; lemma-frontend/tests/this-mac.test.ts covers them.

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

// How a save completes -- edits made while it ran, a conflict with a change
// made elsewhere, a completion event lost to a reconnect -- went with the forms
// that saved; lemma-frontend/tests/this-mac.test.ts covers it there.

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

test('stopping sharing is offered only while this computer is shared', async () => {
  await page();
  const sharing = await shared('sharing');
  const button = dom.element('sharing-disable');

  sharing.renderSharingControls({ mode: 'this_computer', phase: 'ready' });
  assert.equal(button.hidden, true, 'nothing to stop on a private installation');

  sharing.renderSharingControls({ mode: 'public', phase: 'ready' });
  assert.equal(button.hidden, false);
  assert.equal(button.disabled, false);
});

test('stopping sharing asks the shell to disable it, and says so', async () => {
  const { core } = await page();
  const sharing = await shared('sharing');
  core.store.snapshot = { sharing: { mode: 'local_network', phase: 'ready' } };

  await sharing.disableSharing();

  const sent = dom.commands.filter(({ command }) => command === 'sharing_action');
  assert.equal(sent.length, 1);
  assert.equal(sent[0].args.action, 'disable');
  // Held busy until the daemon reports the change, so a second press cannot
  // start a second transition while the first is running.
  assert.equal(dom.element('sharing-disable').disabled, true);
  assert.match(dom.element('toast').textContent, /This computer/);
});

test('a refused stop puts the button back and says why', async () => {
  const { core } = await page();
  const sharing = await shared('sharing');
  core.store.snapshot = { sharing: { mode: 'public', phase: 'ready' } };
  dom.answer(async () => { throw new Error('control endpoint unavailable'); });

  await sharing.disableSharing();

  assert.equal(dom.element('sharing-disable').disabled, false);
  assert.match(dom.element('toast').textContent, /background service isn't running/);
});
