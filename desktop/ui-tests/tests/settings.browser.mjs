import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { after, before, test } from 'node:test';
import { chromium } from 'playwright';
import { reportPolicyViolations, servedHeaders } from '../drivers/app-csp.mjs';

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
  reportPolicyViolations(page, errors);
  t.after(() => assert.deepEqual(errors, []));
  await page.route('https://desktop.test/**', async (route) => {
    const name = new URL(route.request().url()).pathname.slice(1);
    // The page and its modules; anything else is a request the app would
    // not serve either.
    if (!['control.html', 'control.js', 'control.css'].includes(name) && !/^control\/[a-z-]+\.js$/.test(name)) {
      return route.abort();
    }
    await route.fulfill({
      body: await readFile(new URL(`../../ui/${name}`, import.meta.url)),
      contentType: name.endsWith('.html') ? 'text/html' : name.endsWith('.css') ? 'text/css' : 'text/javascript',
      headers: servedHeaders(name),
    });
  });
  await page.addInitScript(({mode, daemonOffline}) => {
    const listeners = {};
    const emit = (event) => listeners['lemma:locald-event']?.({ payload: event });
    const fixture = {
      calls: [],
      // Whether this build answers `telemetry_status` as one that can send
      // install health, which is what puts a real field on the page.
      telemetry: false,
      snapshot: {
        event: 'control.snapshot',
        state: { ready: true, running: true, status: 'ready' },
        services: [{ id: 'backend', running: true }, { id: 'frontend', running: true }],
        managed_runtime: {}, agent_host: { available: true, running: true, targets: [] },
        sharing: { mode: 'this_computer', phase: 'ready' },
      },
      refresh() { emit(structuredClone(this.snapshot)); },
      // The daemon's channel, with whatever a test wants to put on it.
      emit(event) { emit(event); },
      disconnect() { listeners['lemma:locald-disconnected']?.({ payload: null }); },
      // What the menu sends when it opens a page on an already-open window.
      openPage(page) { listeners['lemma:control-page']?.({ payload: page }); },
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
        if (command === 'confirm_destructive_action') return false;
        if (command === 'runtime_info') return { desktopRelease: 'test', repairAvailable: false };
        if (command === 'check_for_app_update') return { updatesSupported: false, currentVersion: 'test', channel: 'dev' };
        if (command === 'telemetry_status') {
          return { available: fixture.telemetry, enabled: true, host: 'https://telemetry.example' };
        }
      } },
      event: { listen(name, listener) { listeners[name] = listener; return Promise.resolve(() => {}); } },
    };
  }, {mode, daemonOffline});
  await page.goto('https://desktop.test/control.html');
  if (daemonOffline) await page.waitForFunction(() => !document.getElementById('snapshot-unavailable').hidden);
  // The first snapshot has been drawn once the pill stops saying it is still
  // connecting -- in every mode, where the health metric reads differently.
  else await page.waitForFunction(() => document.getElementById('state-pill').textContent !== 'Connecting…');
  return page;
}

test('settings before deployment selection do not claim the workspace is in the cloud', async t => {
  const page = await settings(t, 'undecided');
  const description = await page.locator('#deployment-description').textContent();
  assert.match(description, /Choose Lemma Cloud or Local Lemma/);
  assert.doesNotMatch(description, /Your workspace data and orchestration live in Lemma Cloud/);
});

test('settings content remains readable when an embedded webview suspends animation', async (t) => {
  const page = await settings(t, 'cloud');
  // Through the CSSOM rather than `addStyleTag`: that inserts an inline
  // <style>, which the app's policy refuses, exactly as it should.
  await page.evaluate(() => {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync('* { animation-play-state: paused !important; }');
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
  });
  for (const name of ['This computer', 'Recovery', 'Diagnostics']) {
    // Prefix, not exact: a nav item's accessible name can carry its health as
    // well, which is what stops it being colour alone.
    await page.getByRole('button', { name: new RegExp(`^${name}`) }).click();
    const content = await page.locator('.page.active').evaluate(element => ({
      opacity: getComputedStyle(element).opacity,
      height: element.getBoundingClientRect().height,
      text: element.innerText.trim(),
    }));
    assert.equal(content.opacity, '1', `${name} must paint without waiting for animation frames`);
    assert.ok(content.height > 0 && content.text.length > 0, `${name} has readable content`);
  }
});

// Local settings is often opened because something is already broken, so an
// outage has to be said on the page, and the next snapshot has to clear it
// rather than leave the banner standing over live numbers.
test('a disconnect is said on the page and the next snapshot clears it', async (t) => {
  const page = await settings(t);
  await page.evaluate(() => window.__fixture.disconnect());
  assert.equal(await page.locator('#state-pill').textContent(), 'Disconnected');
  assert.equal(await page.locator('#snapshot-unavailable').isVisible(), true);
  // Not the last snapshot's "Healthy" and "running": nothing is answering.
  assert.equal(await page.locator('#metric-app').textContent(), 'Not answering');
  assert.match(await page.locator('#overview-attention').textContent(), /background service isn't answering/);
  assert.doesNotMatch(await page.locator('#overview-services').textContent(), /running/);
  await page.evaluate(() => window.__fixture.refresh());
  assert.equal(await page.locator('#snapshot-unavailable').isVisible(), false);
  assert.equal(await page.locator('#metric-app').textContent(), 'Healthy');
  assert.match(await page.locator('#overview-services').textContent(), /running/);
});

// A cloud user had no update control at all: This Mac is local-only and this
// page kept its update panel on Overview, which cloud mode cannot open.
test('cloud mode shows the update panel on This computer, and Check for Updates opens it', async (t) => {
  const page = await settings(t, 'hosted');
  const panel = page.locator('#app-update-panel');
  assert.equal(await panel.isVisible(), true);
  assert.equal(await panel.evaluate(node => node.closest('.page').dataset.page), 'computer');
  const checks = () => page.evaluate(() => window.__fixture.calls.filter(call => call.command === 'check_for_app_update').length);
  const before = await checks();
  await page.getByRole('button', { name: 'Recovery' }).click();
  await page.evaluate(() => window.__fixture.openPage('updates'));
  assert.equal(await page.locator('#page-title').textContent(), 'This computer');
  assert.equal(await panel.isVisible(), true);
  assert.equal(await checks(), before + 1, 'opening it checks again');
  // Nothing to start or restart in cloud mode.
  await page.getByRole('button', { name: 'Recovery' }).click();
  assert.equal(await page.getByRole('button', { name: 'Restart application' }).isDisabled(), true);
});

test('locally, Check for Updates lands on the panel on Overview', async (t) => {
  const page = await settings(t);
  await page.getByRole('button', { name: /^Recovery/ }).click();
  await page.evaluate(() => window.__fixture.openPage('updates'));
  assert.equal(await page.locator('#page-title').textContent(), 'Overview');
  assert.equal(await page.locator('#app-update-panel').evaluate(node => node.closest('.page').dataset.page), 'overview');
});

// An update that stopped mid-migration was written to a log nobody reads.
test('startup warnings are said above the page with a way to act on them', async (t) => {
  const page = await settings(t, 'hosted');
  await page.evaluate(() => {
    window.__fixture.snapshot.warnings = [{
      code: 'update-interrupted',
      message: "Your last update didn't finish. Install Lemma 0.9.0 to continue — don't reopen the older version.",
      version: '0.9.0',
    }];
    window.__fixture.refresh();
  });
  const warning = page.locator('#startup-warnings [data-warning-code="update-interrupted"]');
  await warning.waitFor();
  assert.match(await warning.textContent(), /Your last update didn't finish/);
  assert.match(await warning.textContent(), /Install Lemma 0\.9\.0/);
  await page.getByRole('button', { name: /^Recovery/ }).click();
  assert.equal(await warning.isVisible(), true, 'on every page, not only Overview');
  await warning.getByRole('button', { name: 'Check for updates' }).click();
  assert.equal(await page.locator('#page-title').textContent(), 'This computer');

  await page.evaluate(() => { window.__fixture.snapshot.warnings = []; window.__fixture.refresh(); });
  await page.locator('#startup-warnings').waitFor({ state: 'hidden' });
});

test('Review on a sharing problem goes to the control that turns sharing off', async (t) => {
  const page = await settings(t);
  await page.evaluate(() => {
    window.__fixture.snapshot.sharing = { mode: 'public', phase: 'error', last_error: 'The tunnel stopped.' };
    window.__fixture.refresh();
  });
  await page.getByRole('button', { name: /^Recovery/ }).click();
  await page.getByRole('button', { name: /^Overview/ }).click();
  await page.locator('#overview-attention [data-summary-page="sharing"]').click();
  assert.equal(await page.evaluate(() => document.activeElement?.id), 'sharing-disable');
});

test('services that need attention say what the buttons do, and Recovery can restart', async (t) => {
  const page = await settings(t);
  await page.evaluate(() => {
    window.__fixture.snapshot.services = [{ id: 'backend', running: false }];
    window.__fixture.refresh();
  });
  assert.match(await page.locator('#overview-attention').textContent(), /Start missing services/);
  assert.equal(await page.getByRole('button', { name: 'Reconcile' }).count(), 0);
  await page.getByRole('button', { name: /^Recovery/ }).click();
  await page.locator('.page[data-page="recovery"]').getByRole('button', { name: 'Restart application' }).click();
  const commands = await page.evaluate(() => window.__fixture.calls.map(call => call.command));
  assert.equal(commands.includes('restart'), true);
});

// Provider drafts, saving one section at a time, model discovery, a save that
// conflicts or whose completion is lost, and the save-or-discard decision on
// closing all moved with their forms to lemma-frontend's This Mac settings;
// lemma-frontend/tests/this-mac.test.ts covers them there.

test('cloud mode opens this computer without provisioning a local stack', async (t) => {
  const page = await settings(t, 'hosted');
  assert.equal(await page.locator('#page-title').textContent(), 'This computer');
  assert.equal(await page.locator('#attention-banner').isVisible(), false, 'cloud mode has no local application stack to repair');
  assert.equal(await page.getByRole('button', { name: /^Overview/ }).isDisabled(), true);
  await page.getByRole('button', { name: 'Open coding agents in Lemma' }).click();
  const calls = await page.evaluate(() => window.__fixture.calls);
  const commands = calls.map((call) => call.command);
  // A hosted workspace shows this computer's agents under Models.
  assert.deepEqual(calls.find((call) => call.command === 'close_local_settings')?.args, { section: 'models' });
  assert.equal(commands.includes('runtime.prepare'), false);
  assert.equal(commands.includes('prepare_runtime'), false);
  assert.equal(commands.includes('start'), false);
});

test('force cleanup is reachable without a daemon and cancellation never reports erased data', async (t) => {
  const page = await settings(t, 'hosted', true);
  await page.getByRole('button', { name: /^Recovery/ }).click();
  await page.getByRole('button', { name: 'Force cleanup and reinstall', exact: true }).click();
  const calls = await page.evaluate(() => window.__fixture.calls);
  assert.equal(calls.filter((call) => call.command === 'reset_full_reinstall').length, 1);
  assert.equal(calls.some((call) => call.command === 'prepare_runtime' || call.command === 'start'), false);
  assert.doesNotMatch(await page.locator('#toast').textContent(), /was removed|were removed|was erased/);
  assert.equal(await page.getByRole('button', { name: 'Force cleanup and reinstall', exact: true }).isEnabled(), true);
});

// Choosing a mode, public-link confirmation, who can join and the QR code
// moved to lemma-frontend's This Mac settings; lemma-frontend/tests/this-mac.test.ts
// covers them. What stays is the way back: a shared workspace moves this
// window to the shared address, where the workspace cannot reach this
// computer's settings, so turning sharing off has to work from here.
test('stopping sharing is offered only while shared, and asks the daemon to disable it', async t => {
  const page = await settings(t);
  const stop = page.getByRole('button', { name: 'Return to This computer' });
  assert.equal(await stop.isVisible(), false, 'nothing to stop on a private installation');

  await page.evaluate(() => {
    window.__fixture.snapshot.sharing = {
      mode: 'local_network',
      phase: 'ready',
      canonical_url: 'https://192.168.1.20:7423',
    };
    window.__fixture.refresh();
  });
  await stop.waitFor();
  assert.match(await page.locator('#metric-exposure').textContent(), /Local network/);

  await stop.click();
  const sent = await page.evaluate(() => window.__fixture.calls
    .filter(call => call.command === 'sharing_action')
    .map(call => call.args.action));
  assert.deepEqual(sent, ['disable']);
  // Held until the daemon reports the change, so a second press cannot start
  // a second transition while the first is running.
  assert.equal(await stop.isDisabled(), true);

  await page.evaluate(() => {
    window.__fixture.snapshot.sharing = { mode: 'this_computer', phase: 'ready' };
    window.__fixture.emit({ event: 'sharing.changed', sharing: window.__fixture.snapshot.sharing });
  });
  await stop.waitFor({ state: 'hidden' });
});

// Escape closed the whole settings window from anywhere, including from
// inside a field. Nothing here is a draft any more, so closing asks nothing --
// but the key pressed to leave a control still belongs to that control first.
test('Escape leaves the field before it leaves settings', async t => {
  const page = await settings(t);
  await page.evaluate(() => { window.__fixture.telemetry = true; });
  await page.getByRole('button', { name: /^Diagnostics/ }).click();
  await page.locator('#telemetry-enabled').focus();

  await page.keyboard.press('Escape');
  assert.equal(
    await page.evaluate(() => window.__fixture.calls.some(call => call.command === 'close_local_settings')),
    false,
    'the first Escape belongs to the field',
  );
  assert.notEqual(
    await page.evaluate(() => document.activeElement?.id), 'telemetry-enabled',
    'and it should have left the field',
  );

  await page.keyboard.press('Escape');
  await page.waitForFunction(() => window.__fixture.calls
    .some(call => call.command === 'close_local_settings'));
  assert.equal(
    await page.evaluate(() => window.__fixture.calls.filter(call => call.command === 'close_local_settings').length),
    1,
    'closing is one request, with no decision asked first',
  );
});

// Events cross the bridge from the daemon and were read here unparsed. A
// shape the page did not expect threw partway through rendering, after some
// of it had run -- leaving a mix of old and new on screen and no error.
test('an event missing what it needs is reported, not half-applied', async t => {
  const page = await settings(t);
  await page.evaluate(() => window.__fixture.emit({
    event: 'control.snapshot',
    services: [],
    sharing: { mode: 'public', phase: 'ready' },
  }));

  await page.getByText('control.snapshot arrived without state', { exact: false }).waitFor();
  assert.equal(
    await page.locator('#metric-exposure').textContent(), 'This computer',
    'nothing from an event the page could not use reaches the screen',
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

// Which page you are on was a background colour, and each item's health was a
// coloured dot with no text anywhere. Listening to this nav gave you buttons
// that read identically, and no way to find the one that wants you.
test('the settings nav says where you are and what needs you', async t => {
  const page = await settings(t);

  const overview = page.getByRole('button', { name: /^Overview/ });
  assert.equal(await overview.getAttribute('aria-current'), 'page');

  await page.getByRole('button', { name: /^Recovery/ }).click();
  assert.equal(await overview.getAttribute('aria-current'), null,
    'only one page is the current one');
  assert.equal(
    await page.getByRole('button', { name: /^Recovery/ }).getAttribute('aria-current'),
    'page',
  );

  // The dots carry their meaning in words, so the nav item's own name does.
  // The two that report nothing are hidden rather than left as an
  // unexplained dot; every dot that is exposed says what its colour means.
  const meanings = await page.locator('#nav .health-dot:not([aria-hidden])').evaluateAll(
    nodes => nodes.map(node => node.getAttribute('aria-label')),
  );
  assert.equal(await page.locator('#nav .health-dot[aria-hidden="true"]').count(), 2);
  assert.ok(
    meanings.every(label => label && label.length),
    `every dot has to say what its colour means: ${JSON.stringify(meanings)}`,
  );
  assert.ok(
    meanings.some(label => label === 'healthy'),
    'a ready installation has at least one healthy item',
  );
});

// The settings window can be resized, and a person who does that should not
// lose the controls off the side of it.
test('settings stay reachable in a narrow window', async t => {
  const page = await settings(t);
  await page.setViewportSize({ width: 400, height: 700 });

  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    true,
    'the page must not scroll sideways',
  );
  for (const name of ['Overview', 'Recovery']) {
    const item = page.getByRole('button', { name: new RegExp(`^${name}`) });
    await item.scrollIntoViewIfNeeded();
    const box = await item.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= 400, `${name} is off screen: ${JSON.stringify(box)}`);
  }
});
