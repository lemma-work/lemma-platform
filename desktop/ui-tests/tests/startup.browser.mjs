// The splash after a deployment has been chosen: the running stack, the log
// panel, and the two recovery actions.
//
// onboarding.browser.mjs covers everything up to the moment a mode is picked,
// and the shutdown intents. What happens in between -- progress arriving,
// reaching ready, the workspace opening on its own, a stopped stack being
// started again, reading a log, resetting local data -- had no browser
// coverage at all, on the screen every user sees on every launch.
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { chromium } from 'playwright';
import { launchSplash } from '../drivers/splash.mjs';

let browser;
before(async () => {
  browser = await chromium.launch({ channel: process.env.LEMMA_TEST_BROWSER_CHANNEL || undefined });
});
after(async () => { await browser?.close(); });

const RUNNING = { mode: 'local', phaseKey: 'boot', status: 'Starting', running: true, ready: false, error: false };
const READY = { mode: 'local', phaseKey: 'ready', status: 'ready', running: true, ready: true, error: false };

const splash = (t, options = {}) => launchSplash(browser, t, { initialState: RUNNING, ...options });

const commandCalls = (page, names) => page.evaluate(
  wanted => window.__fixture.calls.filter(call => wanted.includes(call.command)),
  names,
);

const push = (page, state) => page.evaluate(s => window.__fixture.renderState(s), state);

// The headline fades out and back in, so it is waited for rather than read.
const lineSays = (page, text) => page.waitForFunction(
  wanted => document.getElementById('line').textContent === wanted,
  text,
);

test('progress is shown while starting, and reaching ready opens the workspace', async t => {
  const page = await splash(t);

  await push(page, { ...RUNNING, phaseKey: 'download', phase: 'Downloading services', progress: 20 });
  await page.getByText('Downloading services', { exact: false }).waitFor();
  assert.equal(await page.locator('#bar').evaluate(bar => bar.style.width), '20%');
  assert.equal(await page.locator('#open-app').isVisible(), false,
    'nothing is open-able while services are still coming up');

  await push(page, { ...RUNNING, phaseKey: 'backend', phase: 'Starting backend', progress: 70 });
  assert.equal(await page.locator('#bar').evaluate(bar => bar.style.width), '70%');

  await push(page, { ...READY, progress: 100 });
  await page.getByText("It's ready. Opening Lemma.", { exact: true }).waitFor();
  assert.equal(await page.locator('#open-app').textContent(), 'Open Lemma');
  await page.waitForFunction(() => window.__fixture.calls.some(call => call.command === 'open_app'));
});

// `scheduleReadyOpen` waits 650ms and clears any pending timer first. The
// daemon re-emits its snapshot freely, so without that the same window would
// be asked to open once per snapshot.
test('repeated ready snapshots open the workspace once, not once each', async t => {
  const page = await splash(t);
  for (let i = 0; i < 4; i += 1) await push(page, READY);
  await page.waitForTimeout(1000);
  assert.deepEqual(
    (await commandCalls(page, ['open_app'])).length, 1,
    'the ready screen must debounce, not queue an open per snapshot',
  );
});

// The failure branch of the automatic open. It is the only thing standing
// between "Lemma did not open" and a window that sits on the ready screen
// forever with no way forward and no reason given.
test('an automatic open that fails leaves a usable button and says why', async t => {
  const page = await splash(t);
  await page.locator('#toggle-log').click();
  await page.evaluate(() => { window.__fixture.rejectOpen = true; });

  await push(page, READY);
  await page.getByText('the workspace window did not open', { exact: false }).waitFor();

  const open = page.locator('#open-app');
  assert.equal(await open.isVisible(), true);
  assert.equal(await open.isDisabled(), false, 'the button has to be usable again');
  assert.equal(await open.textContent(), 'Open Lemma');
});

test('a stopped stack offers Start, and starting it is not opening it', async t => {
  const page = await splash(t, {
    initialState: { mode: 'local', phaseKey: 'stopped', status: 'stopped', running: false, ready: false },
  });

  const button = page.locator('#open-app');
  await button.waitFor({ state: 'visible' });
  assert.equal(await button.textContent(), 'Start Lemma');
  await button.click();

  assert.deepEqual(
    (await commandCalls(page, ['start', 'open_app'])).map(call => call.command),
    ['start'],
    'Start Lemma must start the stack, never open a workspace that is not up',
  );
});

test('the log panel lists its sources, switches between them, and closes', async t => {
  const page = await splash(t, {
    logs: {
      sources: [{ id: 'events', label: 'Events' }, { id: 'backend', label: 'Backend' }],
      entries: { events: 'events log body', backend: 'backend log body' },
    },
  });

  await page.locator('#toggle-log').click();
  await page.getByText('events log body', { exact: false }).waitFor();
  assert.equal(await page.locator('#toggle-log').textContent(), 'close log');
  assert.deepEqual(
    await page.locator('#log-tabs button').allTextContents(),
    ['Events', 'Backend'],
  );
  assert.equal(
    await page.locator('#log-tabs button.active').textContent(), 'Events',
    'the tab being read has to be the one that looks selected',
  );

  await page.locator('#log-tabs button', { hasText: 'Backend' }).click();
  await page.getByText('backend log body', { exact: false }).waitFor();
  assert.equal(await page.locator('#log-tabs button.active').textContent(), 'Backend');
  assert.deepEqual(
    (await commandCalls(page, ['diagnostic_logs']))
      .filter(call => call.args.cursor === null)
      .map(call => call.args.source),
    // The first is the read the page makes at boot to fill the tab bar
    // before the panel has ever been opened.
    ['events', 'events', 'backend'],
    'switching sources must re-read from the start, not tail the previous one',
  );

  await page.locator('#close-log').click();
  assert.equal(await page.locator('#log-panel').isVisible(), false);
  assert.equal(await page.locator('#toggle-log').textContent(), 'log');
});

// The live tail and the stored log share one pane. A log opened during a
// running start must show both, or the panel looks empty exactly when
// somebody has opened it to find out why nothing is happening.
test('the live tail and the stored log appear together', async t => {
  const page = await splash(t, { logs: { sources: [{ id: 'events', label: 'Events' }], entries: { events: 'stored line' } } });
  await page.locator('#toggle-log').click();
  await page.evaluate(() => window.__fixture.emitLog('live line'));
  await page.getByText('live line', { exact: false }).waitFor();
  const shown = await page.locator('#log').textContent();
  assert.ok(shown.includes('stored line'), `stored log is missing: ${shown}`);
  assert.ok(shown.includes('live line'), `live tail is missing: ${shown}`);
});

// "View log" on a failure should land on the log for *that* failure. Landing
// on the events log means the person who just hit a migration error has to
// know which of six sources to pick before they can read anything useful.
test('opening the log from an error preselects the log for that failure', async t => {
  const page = await splash(t, {
    logs: { sources: [{ id: 'migrations', label: 'Migrations' }], entries: { migrations: 'relation already exists' } },
  });
  await push(page, {
    mode: 'local', phaseKey: 'error', error: true, running: false, ready: false,
    // What the daemon actually sends. It classifies the failure from the raw
    // error, before anything reshapes it for a person to read, and puts the
    // answer in the event; this used to omit it and the splash re-derived the
    // answer by searching the displayed status for words.
    status: 'A database migration failed', errorCode: 'locald-start-failed',
    logSource: 'migrations',
  });
  await page.getByRole('button', { name: 'View log', exact: true }).click();
  await page.getByText('relation already exists', { exact: false }).waitFor();
  assert.deepEqual(
    (await commandCalls(page, ['diagnostic_logs'])).map(call => call.args.source),
    // Again, the boot read comes first; what matters is that View log then
    // asks for the source belonging to this failure rather than for events.
    ['events', 'migrations'],
  );
});

// An error event with no `log_source` -- an older daemon, or a path that does
// not set one. The error *code* decides, because it is an enumerated value;
// the status text beside it is prose and used to be what decided.
test('an error with no named log falls back to its code, not to its wording', async t => {
  const page = await splash(t, {
    logs: {
      sources: [{ id: 'locald-stderr', label: 'Service manager startup' }],
      entries: { 'locald-stderr': 'could not bind the control socket' },
    },
  });
  await push(page, {
    mode: 'local', phaseKey: 'error', error: true, running: false, ready: false,
    status: 'A database migration failed', errorCode: 'locald-start-failed',
  });
  await page.getByRole('button', { name: 'View log', exact: true }).click();
  await page.getByText('could not bind the control socket', { exact: false }).waitFor();
  assert.deepEqual(
    (await commandCalls(page, ['diagnostic_logs'])).map(call => call.args.source),
    ['events', 'locald-stderr'],
  );
});

// A source the shell does not serve is not passed through. `read_diagnostic_log`
// refuses an id it has no file for, so the panel would show an error where the
// log should be -- and "infrastructure" is what the daemon answered for a
// crashed guest kernel.
test('a log source the shell cannot serve falls back to events', async t => {
  const page = await splash(t, {
    logs: { sources: [{ id: 'events', label: 'Events' }], entries: { events: 'boot line' } },
  });
  await push(page, {
    mode: 'local', phaseKey: 'error', error: true, running: false, ready: false,
    status: 'the guest kernel crashed', errorCode: 'host-operation-failed',
    logSource: 'infrastructure',
  });
  await page.getByRole('button', { name: 'View log', exact: true }).click();
  await page.getByText('boot line', { exact: false }).waitFor();
  assert.deepEqual(
    (await commandCalls(page, ['diagnostic_logs'])).map(call => call.args.source),
    ['events', 'events'],
  );
});

// Data this release cannot read: retrying reproduces it exactly, so Try again
// is withheld and the two recovery actions are offered instead.
test('unreadable data offers reset and start over instead of a pointless retry', async t => {
  const page = await splash(t, {
    recoveryOptions: { dataResetAvailable: true, fullReinstallAvailable: true },
  });
  await push(page, {
    mode: 'local', phaseKey: 'error', error: true, running: false, ready: false,
    status: 'Local data was written by a newer version of Lemma',
    errorCode: 'local-data-incompatible',
  });

  const reset = page.getByRole('button', { name: 'Reset local data', exact: true });
  await reset.waitFor({ state: 'visible' });
  assert.equal(await page.getByRole('button', { name: 'Start over', exact: true }).isVisible(), true);
  assert.equal(await page.getByRole('button', { name: 'Try again', exact: true }).isVisible(), false,
    'a retry would reproduce the same failure, so it must not be offered');

  await reset.click();
  assert.deepEqual(
    (await commandCalls(page, ['reset_local_data', 'reset_full_reinstall'])).map(call => call.command),
    ['reset_local_data'],
  );
  // The confirmation is native and lives in the shell; what the page owes the
  // user is a button that works again afterwards.
  await assert.doesNotReject(reset.waitFor({ state: 'visible' }));
  assert.equal(await reset.textContent(), 'Reset local data');
  assert.equal(await reset.isDisabled(), false);
});

test('a recovery action that fails reports the reason rather than restoring in silence', async t => {
  const page = await splash(t, {
    recoveryOptions: { dataResetAvailable: true, fullReinstallAvailable: true },
  });
  await page.evaluate(() => { window.__fixture.rejectReset = true; });
  await push(page, {
    mode: 'local', phaseKey: 'error', error: true, running: false, ready: false,
    status: 'Local data was written by a newer version of Lemma',
    errorCode: 'local-data-incompatible',
  });

  await page.getByRole('button', { name: 'Start over', exact: true }).click();
  await page.getByText('the runtime is still holding local data', { exact: false }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Start over', exact: true }).isDisabled(), false);
});

// Asking the daemon what it can offer is itself a call that can fail -- and it
// fails precisely when the daemon is the thing that is broken, which is when
// the recovery buttons matter most.
test('recovery is still offered when the daemon cannot say what is available', async t => {
  const page = await splash(t);
  await page.evaluate(() => {
    const inner = window.__TAURI__.core.invoke;
    window.__TAURI__.core.invoke = async (command, args) => {
      if (command === 'local_recovery_options') throw new Error('locald is not answering');
      return inner(command, args);
    };
  });
  await push(page, {
    mode: 'local', phaseKey: 'error', error: true, running: false, ready: false,
    status: 'Local data was written by a newer version of Lemma', errorCode: 'local-data-incompatible',
  });
  await page.getByRole('button', { name: 'Start over', exact: true }).waitFor({ state: 'visible' });
  assert.equal(await page.getByRole('button', { name: 'Reset local data', exact: true }).isVisible(), false,
    'the tier that needs the daemon cannot be promised when the daemon is gone');
});

// A service that stopped has nothing to do with the data. The screen used to
// offer "Reset local data" beside Try again for it, and pressing the button on
// offer erased everything for what a restart would have fixed.
test('a stopped local service offers Try again, the log and Recovery, and never an erase', async t => {
  const page = await splash(t, {
    recoveryOptions: { dataResetAvailable: true, fullReinstallAvailable: true },
  });
  for (const errorCode of ['locald-disconnected', 'locald-start-failed']) {
    await push(page, {
      mode: 'local', phaseKey: 'error', error: true, running: false, ready: false,
      status: 'Local service manager disconnected', errorCode,
    });
    await page.getByRole('button', { name: 'Recovery', exact: true }).waitFor({ state: 'visible' });
    await lineSays(page, "Lemma's local service stopped.");
    assert.equal(await page.getByRole('button', { name: 'Try again', exact: true }).isVisible(), true, errorCode);
    assert.equal(await page.getByRole('button', { name: 'View log', exact: true }).isVisible(), true, errorCode);
    assert.equal(await page.getByRole('button', { name: 'Reset local data', exact: true }).isVisible(), false, errorCode);
    assert.equal(await page.getByRole('button', { name: 'Start over', exact: true }).isVisible(), false, errorCode);
  }
  await page.getByText('Your data is untouched.', { exact: true }).waitFor({ state: 'hidden' });
  await push(page, {
    mode: 'local', phaseKey: 'error', error: true, running: false, ready: false,
    status: 'Local service manager disconnected', errorCode: 'locald-disconnected',
  });
  await page.getByText('Your data is untouched.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Recovery', exact: true }).click();
  assert.deepEqual(
    (await commandCalls(page, ['open_control_center', 'reset_local_data'])).map(call => call.command),
    ['open_control_center'],
  );
});

test('a runtime that did not install says so, and offers no erase', async t => {
  const page = await splash(t, {
    recoveryOptions: { dataResetAvailable: true, fullReinstallAvailable: true },
  });
  await push(page, {
    mode: 'local', phaseKey: 'runtime-install', error: true, running: false, ready: false,
    status: 'Lemma could not reach github.com to download its runtime. Check your internet connection and try again.',
    errorCode: 'runtime-install-failed',
  });
  await page.getByText('Lemma needs the internet once to finish installing.', { exact: false }).waitFor();
  await lineSays(page, "Lemma couldn't finish installing.");
  assert.equal(await page.getByRole('button', { name: 'Reset local data', exact: true }).isVisible(), false);
  assert.equal(await page.getByRole('button', { name: 'Try again', exact: true }).isVisible(), true);
});

// Data another release wrote is kept by going back to that release. Erasing it
// is still there, second, and still behind the shell's own confirmation.
test('data from another release offers the previous version first and erasing second', async t => {
  const page = await splash(t, {
    recoveryOptions: { dataResetAvailable: true, fullReinstallAvailable: true },
  });
  await push(page, {
    mode: 'local', phaseKey: 'error', error: true, running: false, ready: false,
    status: 'the workspace database on this computer was created by PostgreSQL 16 and this release runs PostgreSQL 17; local data must be reset',
    errorCode: 'local-data-incompatible',
  });
  const keep = page.getByRole('link', { name: 'Keep my data — get the previous version', exact: true });
  await keep.waitFor({ state: 'visible' });
  assert.equal(await keep.getAttribute('href'), 'https://github.com/lemma-work/lemma-platform/releases');
  assert.equal(await keep.getAttribute('target'), '_blank');
  await lineSays(page, "This version of Lemma can't open your existing data.");
  await page.getByText('Your data is still on this computer.', { exact: true }).waitFor();
  const erase = page.getByRole('button', { name: 'Erase and start fresh', exact: true });
  await erase.waitFor({ state: 'visible' });
  assert.equal(await erase.evaluate(node => node.classList.contains('dark')), false,
    'erasing is never the filled button when keeping the data is offered');
  assert.equal(await page.getByRole('button', { name: 'Try again', exact: true }).isVisible(), false);
  await erase.click();
  assert.deepEqual(
    (await commandCalls(page, ['reset_local_data', 'reset_full_reinstall'])).map(call => call.command),
    ['reset_local_data'],
  );
  await erase.waitFor({ state: 'visible' });
  assert.equal(await erase.textContent(), 'Erase and start fresh');
});

// The services are up and the window failed. The box's Try again starts the
// stack, which is the wrong retry for that -- so it is not offered, and the
// button that is offered opens.
test('a failed Open Lemma retries opening, not starting', async t => {
  const page = await splash(t);
  await page.evaluate(() => { window.__fixture.rejectOpen = true; });
  await push(page, READY);
  const open = page.locator('#open-app');
  // The automatic open fails first; the test starts from what it leaves.
  await page.waitForFunction(() => window.__fixture.calls.some(call => call.command === 'open_app'));
  await open.waitFor({ state: 'visible' });
  await page.evaluate(() => {
    window.__fixture.calls.length = 0;
  });
  await open.click();
  await page.getByText('Lemma did not open.', { exact: true }).waitFor();
  assert.equal(await page.locator('#errwrap').isVisible(), true);
  assert.equal(await page.getByRole('button', { name: 'Try again', exact: true }).isVisible(), false,
    'Try again would start the stack, not open the window');
  assert.equal(await open.textContent(), 'Try opening Lemma again');
  await open.click();
  assert.deepEqual(
    (await commandCalls(page, ['start', 'open_app'])).map(call => call.command),
    ['open_app', 'open_app'],
  );
});

// `open_control_center` used to return Ok before it had done anything and
// report failure by emitting `lemma:control-error`, an event nothing in the
// app listens for -- so this `.catch` could never run. And even once it can,
// the box it writes into is hidden unless a failing state put it on screen,
// and a settings window that will not open is not a failing state. Pressing
// recovery and getting nothing at all was the sum of the two.
test('a recovery window that will not open says so instead of doing nothing', async t => {
  const page = await splash(t);
  await page.evaluate(() => {
    const inner = window.__TAURI__.core.invoke;
    window.__TAURI__.core.invoke = async (command, args) => {
      if (command === 'open_control_center') throw new Error('Local settings could not be created');
      return inner(command, args);
    };
  });

  await page.locator('#open-recovery').click();
  // `waitFor` requires visibility, so a message written into a hidden box
  // fails this rather than passing on `textContent` alone.
  await page.getByText('Local settings could not be created', { exact: false }).waitFor();
  assert.equal(await page.locator('#errwrap').isVisible(), true);
});

// A `role="tablist"` whose children are plain buttons announces a tab list
// with nothing in it, marks the selected one by colour alone, and does not
// answer arrow keys. Somebody reading a failure log with a screen reader gets
// a list of unlabelled buttons and no way to tell which one they are reading.
test('the log sources are real tabs, not buttons in a box', async t => {
  const page = await splash(t, {
    logs: {
      sources: [{ id: 'events', label: 'Events' }, { id: 'backend', label: 'Backend' }],
      entries: { events: 'events body', backend: 'backend body' },
    },
  });
  await page.locator('#toggle-log').click();
  await page.getByText('events body', { exact: false }).waitFor();

  const tabs = page.getByRole('tab');
  assert.deepEqual(await tabs.allTextContents(), ['Events', 'Backend']);
  assert.equal(await page.locator('#log-tabs').getAttribute('aria-label'), 'Log sources');
  assert.equal(await page.getByRole('tab', { name: 'Events' }).getAttribute('aria-selected'), 'true');
  assert.equal(await page.getByRole('tab', { name: 'Backend' }).getAttribute('aria-selected'), 'false');
  // The panel says which tab it belongs to, so its content is not orphaned.
  assert.equal(
    await page.locator('#log').getAttribute('aria-labelledby'),
    'log-tab-events',
  );

  // One tab stop for the set, then arrows move within it.
  assert.deepEqual(
    await tabs.evaluateAll(nodes => nodes.map(node => node.tabIndex)),
    [0, -1],
  );
  await page.getByRole('tab', { name: 'Events' }).focus();
  await page.keyboard.press('ArrowRight');
  await page.getByText('backend body', { exact: false }).waitFor();
  assert.equal(await page.getByRole('tab', { name: 'Backend' }).getAttribute('aria-selected'), 'true');
  assert.equal(await page.evaluate(() => document.activeElement?.id), 'log-tab-backend');
});

// The phase is announced once, through `say()`, which skips a repeat of the
// same line. The byte counter beside it changes every second or so, and while
// it was inside a live region a screen reader re-read the whole progress line
// on every tick -- for the length of a 473 MB download.
test('progress numbers are shown without being announced', async t => {
  const page = await splash(t);
  assert.equal(await page.locator('#operation-status').getAttribute('aria-live'), null);

  await push(page, {
    ...RUNNING, phaseKey: 'download', phase: 'Downloading services', progress: 20,
    downloadedBytes: 104857600, totalBytes: 496435200,
  });
  // Still visible: this is not about hiding it, it is about not narrating it.
  await page.locator('#operation-status').waitFor({ state: 'visible' });
  assert.notEqual(await page.locator('#operation-meta').textContent(), '');
  // And the phase itself does reach the live region.
  assert.equal(
    await page.locator('.statement').getAttribute('aria-live'), 'polite',
  );
  await page.getByText('Downloading services', { exact: false }).waitFor();
});
