import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { after, before, test } from 'node:test';
import { chromium } from 'playwright';

let browser;
before(async () => {
  browser = await chromium.launch({ channel: process.env.LEMMA_TEST_BROWSER_CHANNEL || undefined });
});
after(async () => { await browser?.close(); });

async function onboarding(t, { viewport = { width: 1100, height: 760 }, windows = false, initialState = null, deferState = false, intent = '', colorScheme = 'light' } = {}) {
  const context = await browser.newContext({
    viewport,
    reducedMotion: 'reduce',
    colorScheme,
    userAgent: windows ? 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
      : 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
  });
  t.after(() => context.close());
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  const assets = new URL('../../ui/', import.meta.url);
  await page.route('https://desktop.test/**', async route => {
    const name = new URL(route.request().url()).pathname.slice(1);
    const file = new URL(name, assets);
    if (!file.href.startsWith(assets.href)) return route.abort();
    const contentType = name.endsWith('.html') ? 'text/html'
      : name.endsWith('.js') ? 'text/javascript'
        : name.endsWith('.json') ? 'application/json' : 'application/octet-stream';
    try {
      await route.fulfill({ body: await readFile(file), contentType });
    } catch {
      await route.abort();
    }
  });
  await page.addInitScript(({ initialState, deferState }) => {
    window.__fixture = { calls: [], rejectInstall: false };
    window.__TAURI__ = {
      event: { listen: async (name, listener) => {
        if (name === 'lemma:state') window.__fixture.renderState = state => listener({ payload: state });
      } },
      core: { async invoke(command, args) {
        window.__fixture.calls.push({ command, args });
        if (command === 'get_state' && deferState) return new Promise(() => {});
        if (command === 'get_state') return initialState || {
          mode: 'undecided', phaseKey: 'boot', status: 'waiting',
          running: false, ready: false, error: false, setup: true,
        };
        if (command === 'diagnostic_logs') return { entries: '', sources: [] };
        if (command === 'local_recovery_options') return {};
        if (command === 'set_connection_mode' && window.__fixture.rejectInstall) {
          throw new Error('Not enough disk space for the local runtime. Free space and retry.');
        }
      } },
    };
  }, { initialState, deferState });
  await page.goto(`https://desktop.test/index.html?intent=${encodeURIComponent(intent)}`);
  if (!initialState) await page.locator('#choose').waitFor({ state: 'visible' });
  return page;
}

async function deploymentCalls(page) {
  return page.evaluate(() => window.__fixture.calls.filter(call =>
    ['set_connection_mode', 'start', 'reset_local_data', 'reset_full_reinstall'].includes(call.command)));
}

test('shutdown shows its purpose before the daemon supplies a snapshot', async t => {
  for (const intent of ['quit', 'stop']) {
    const page = await onboarding(t, { intent, initialState: {}, deferState: true });
    assert.equal(await page.locator('#line').textContent(),
      intent === 'quit' ? 'Stopping Lemma.' : 'Winding down.');
    assert.deepEqual(await deploymentCalls(page), []);
  }
});

test('shutdown ignores stale startup readiness and retries shutdown without starting services', async t => {
  const page = await onboarding(t, { intent: 'quit', initialState: {
    mode: 'local', phaseKey: 'supertokens', status: 'Starting authentication', running: true,
  } });
  await page.getByText('Stopping Lemma.', { exact: true }).waitFor();
  await page.evaluate(() => window.__fixture.renderState({
    mode: 'local', phaseKey: 'ready', ready: true, running: true,
  }));
  assert.equal(await page.locator('#open-app').count(), 1);
  assert.equal(await page.locator('#open-app').isVisible(), false);
  await page.evaluate(() => window.__fixture.renderState({
    mode: 'local', phaseKey: 'stopping', error: true, status: 'The runtime did not stop',
  }));
  await page.getByRole('button', { name: 'Retry shutdown', exact: true }).click();
  const calls = await page.evaluate(() => window.__fixture.calls.filter(call =>
    ['stop', 'start', 'open_app', 'set_connection_mode'].includes(call.command)));
  assert.deepEqual(calls, [{ command: 'stop', args: { includeInfra: true } }]);
});

test('opening or reloading the splash never duplicates shell-owned startup', async t => {
  for (const windows of [false, true]) {
    for (const phaseKey of ['boot', 'stopped']) {
      const page = await onboarding(t, { windows, initialState: {
        mode: 'local', phaseKey, running: false, ready: false, error: false,
      } });
      assert.deepEqual(await deploymentCalls(page), [], `${phaseKey} page load must only observe`);
      await page.reload();
      assert.equal(await page.locator('#orb canvas').count(), 0, 'reduced motion must not create a continuously rendered WebGL surface');
      assert.deepEqual(await deploymentCalls(page), [], `${phaseKey} reload must only observe`);
      if (phaseKey === 'stopped') {
        await page.getByRole('button', { name: 'Start Lemma', exact: true }).click();
        assert.deepEqual((await deploymentCalls(page)).map(call => call.command), ['start']);
      }
    }
  }
});

test('deployment choices disclose storage and execution before cloud sign-in', async t => {
  const page = await onboarding(t);
  const chooser = page.locator('#choose');
  assert.match(await chooser.innerText(), /Workspace data.*Lemma Cloud/);
  assert.match(await chooser.innerText(), /coding agents.*this computer/);
  assert.match(await chooser.innerText(), /stored application data.*this Mac/);
  assert.match(await chooser.innerText(), /providers.*connectors.*internet/);
  assert.deepEqual(await deploymentCalls(page), []);
  await page.getByRole('button', { name: 'Use Lemma Cloud', exact: true }).click();
  assert.deepEqual(await deploymentCalls(page), [{ command: 'set_connection_mode', args: { mode: 'hosted' } }]);
});

for (const colorScheme of ['light', 'dark']) {
  for (const viewport of [{ width: 980, height: 680 }, { width: 1280, height: 860 }]) {
    test(`welcome fits ${viewport.width}×${viewport.height} in ${colorScheme} mode without scrolling`, async t => {
      const page = await onboarding(t, { viewport, colorScheme });
      await page.getByRole('heading', { name: 'Better work, together.' }).waitFor();
      for (const name of ['Use Lemma Cloud', 'Use Local Lemma']) {
        const button = page.getByRole('button', { name, exact: true });
        const bounds = await button.boundingBox();
        assert.ok(bounds && bounds.x >= 0 && bounds.y >= 0);
        assert.ok(bounds.x + bounds.width <= viewport.width);
        assert.ok(bounds.y + bounds.height <= viewport.height, `${name} must be visible without scrolling`);
        assert.equal(await button.isEnabled(), true);
      }
      assert.equal(await page.locator('#scene').evaluate(el => el.scrollHeight <= el.clientHeight), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      assert.equal(await page.locator('#orb').isVisible(), false);
      assert.equal(await page.locator('#whisper').isVisible(), false);
      if (process.env.LEMMA_UI_SCREENSHOT_DIR) {
        await page.screenshot({ path: `${process.env.LEMMA_UI_SCREENSHOT_DIR}/welcome-${viewport.width}-${colorScheme}.png` });
      }
    });
  }
}

test('Cloud is the initial keyboard action and explains always-on work accurately', async t => {
  const page = await onboarding(t);
  assert.equal(await page.getByRole('button', { name: 'Use Lemma Cloud', exact: true }).evaluate(el => el === document.activeElement), true);
  const copy = await page.locator('#choose').innerText();
  assert.match(copy, /Cloud agents and schedules run even when your computer is off/);
  assert.match(copy, /Local agents need this computer running/);
  assert.match(copy, /Set up your own integrations and sharing/);
  assert.deepEqual(await deploymentCalls(page), []);
  await page.keyboard.press('Enter');
  assert.deepEqual(await deploymentCalls(page), [{ command: 'set_connection_mode', args: { mode: 'hosted' } }]);
});

test('local review and Back do not install; explicit confirmation installs once', async t => {
  const page = await onboarding(t);
  await page.getByRole('button', { name: 'Use Local Lemma', exact: true }).click();
  const review = page.locator('#local-confirm');
  assert.match(await review.innerText(), /service images/);
  assert.match(await review.innerText(), /Download and storage needs depend on the release and cached files/);
  assert.match(await review.innerText(), /Interrupted runtime downloads can resume/);
  assert.match(await review.innerText(), /send data to their providers/);
  assert.match(await review.innerText(), /macOS may ask for Local Network access/);
  assert.deepEqual(await deploymentCalls(page), []);
  await page.getByRole('button', { name: 'Back', exact: true }).click();
  assert.deepEqual(await deploymentCalls(page), []);
  await page.getByRole('button', { name: 'Use Local Lemma', exact: true }).click();
  await page.getByRole('button', { name: 'Install local services', exact: true }).click();
  assert.deepEqual(await deploymentCalls(page), [{ command: 'set_connection_mode', args: { mode: 'local' } }]);
});

test('a failed local setup keeps an actionable error and permits a deliberate retry', async t => {
  const page = await onboarding(t);
  await page.evaluate(() => { window.__fixture.rejectInstall = true; });
  await page.getByRole('button', { name: 'Use Local Lemma', exact: true }).click();
  await page.getByRole('button', { name: 'Install local services', exact: true }).click();
  await page.getByText('Not enough disk space for the local runtime.', { exact: false }).waitFor();
  await page.evaluate(() => { window.__fixture.rejectInstall = false; });
  await page.getByRole('button', { name: 'Try local setup again', exact: true }).click();
  assert.equal((await deploymentCalls(page)).length, 2);
});

test('small Windows setup keeps both choices and installation controls reachable', async t => {
  const page = await onboarding(t, { viewport: { width: 640, height: 520 }, windows: true });
  const chooser = page.locator('#choose');
  assert.match(await chooser.innerText(), /this PC/);
  assert.doesNotMatch(await chooser.innerText(), /this Mac/);
  await page.getByRole('button', { name: 'Use Local Lemma', exact: true }).click();
  assert.equal(await page.locator('#local-network-permission').isVisible(), false);
  await page.getByRole('button', { name: 'Install local services', exact: true }).scrollIntoViewIfNeeded();
  const bounds = await page.getByRole('button', { name: 'Install local services', exact: true }).boundingBox();
  assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 640, 'installation action fits the window width');
  assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= 520, 'installation action can be scrolled into view');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.getByRole('button', { name: 'Install local services', exact: true }).click();
  assert.deepEqual(await deploymentCalls(page), [{ command: 'set_connection_mode', args: { mode: 'local' } }]);
});

test('a connection failure keeps recovery readable and retries without offering data erasure', async t => {
  const page = await onboarding(t, { viewport: { width: 640, height: 520 } });
  await page.getByRole('button', { name: 'Use Local Lemma', exact: true }).click();
  await page.getByRole('button', { name: 'Install local services', exact: true }).click();
  await page.evaluate(() => window.__fixture.renderState({
    mode: 'local', phaseKey: 'error', error: true, running: false, ready: false,
    status: "Lemma cannot connect to its local services. In System Settings > Privacy & Security > Local Network, check that Lemma is allowed, then return here and choose Try again. macOS requires this access to reach Lemma's private virtual machine on this Mac. If access is already allowed, restart Lemma and check any VPN or firewall rules. Your local data is preserved; a factory reset is not needed for this connection error.",
  }));
  await page.getByRole('button', { name: 'Try again', exact: true }).scrollIntoViewIfNeeded();
  const bounds = await page.getByRole('button', { name: 'Try again', exact: true }).boundingBox();
  assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= 520, 'retry stays inside the window');
  assert.equal(await page.getByRole('button', { name: 'Reset local data', exact: true }).isVisible(), false);
  assert.equal(await page.getByRole('button', { name: 'Start over', exact: true }).isVisible(), false);
  await page.getByRole('button', { name: 'Try again', exact: true }).click();
  assert.deepEqual((await deploymentCalls(page)).map(call => call.command), ['set_connection_mode', 'start']);
});
