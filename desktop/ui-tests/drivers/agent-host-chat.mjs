import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createWriteStream } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
let input = '';
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const listener = createServer();
listener.listen(0, '127.0.0.1');
await once(listener, 'listening');
const port = listener.address().port;
await new Promise((resolve, reject) => listener.close(error => error ? reject(error) : resolve()));
const origin = `http://127.0.0.1:${port}`;
await mkdir(config.artifactDirectory, { recursive: true });
const log = createWriteStream(path.join(config.artifactDirectory, 'frontend.log'));
const network = createWriteStream(path.join(config.artifactDirectory, 'requests.jsonl'));
const server = spawn(process.execPath, [
  path.join(root, 'lemma-frontend/node_modules/next/dist/bin/next'),
  'dev', '--hostname', '127.0.0.1', '--port', String(port),
], {
  cwd: path.join(root, 'lemma-frontend'),
  env: {
    ...process.env,
    NEXT_TELEMETRY_DISABLED: '1',
    NEXT_PUBLIC_API_URL: config.apiUrl,
    NEXT_PUBLIC_SITE_URL: origin,
    NEXT_PUBLIC_AUTH_URL: origin,
    NEXT_PUBLIC_ANALYTICS_KEY: '',
  },
  stdio: ['ignore', 'pipe', 'pipe'],
});
server.stdout.pipe(log);
server.stderr.pipe(log);
let browser;
let page;
try {
  await new Promise((resolve, reject) => {
    const deadline = setTimeout(() => reject(new Error('Frontend startup timed out')), 90_000);
    server.once('error', reject);
    server.once('exit', code => {
      clearTimeout(deadline);
      reject(new Error(`Frontend exited before readiness: ${code}`));
    });
    server.stdout.on('data', chunk => {
      if (String(chunk).includes('Ready in')) {
        clearTimeout(deadline);
        resolve();
      }
    });
  });
  browser = await chromium.launch({
    channel: process.env.LEMMA_TEST_BROWSER_CHANNEL || undefined,
    headless: true,
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const recordRequest = (request, phase, status) => {
    if (request.url().startsWith(config.apiUrl)) network.write(`${JSON.stringify({
      time: Date.now(), phase, method: request.method(),
      path: new URL(request.url()).pathname, status,
    })}\n`);
  };
  context.on('request', request => recordRequest(request, 'request'));
  context.on('response', response => recordRequest(response.request(), 'response', response.status()));
  context.on('requestfailed', request => recordRequest(request, 'failed', request.failure()?.errorText));
  await context.addInitScript(token => localStorage.setItem('lemma_token', token), config.token);
  // This is deployment configuration, not a mock of any application endpoint.
  await context.route('**/runtime-config.js', route => route.fulfill({
    contentType: 'application/javascript',
    body: `window.__ENV = ${JSON.stringify({
      NEXT_PUBLIC_API_URL: config.apiUrl,
      NEXT_PUBLIC_SITE_URL: origin,
      NEXT_PUBLIC_AUTH_URL: origin,
      NEXT_PUBLIC_ANALYTICS_KEY: '',
    })};`,
  }));
  page = await context.newPage();
  page.setDefaultTimeout(45_000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`${origin}${config.conversationUrl}`, { timeout: 120_000 });
  const composer = page.locator('textarea.lm-composer-input:visible');
  await composer.fill('Read the project file for this test.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  let answer;
  if (config.action === 'parallel') {
    const approvals = page.getByRole('button', { name: 'Approve once', exact: true });
    await approvals.nth(1).waitFor();
    assert.equal(await approvals.count(), 2, 'both requests must be actionable before either decision');
    await page.screenshot({ path: path.join(config.artifactDirectory, 'approval.png'), fullPage: true });
    const denied = page.waitForRequest(request => request.url().includes('/approvals/agent-host-permission:read-b/decision'));
    await page.getByRole('button', { name: 'Deny', exact: true }).nth(1).click();
    assert.equal((await denied).postDataJSON().decision, 'DENY');
    const approved = page.waitForRequest(request => request.url().includes('/approvals/agent-host-permission:read-a/decision'));
    await approvals.first().click();
    assert.equal((await approved).postDataJSON().decision, 'APPROVE_ONCE');
    answer = /Read approved: # File A\s*Read denied; no file was accessed[.]/;
  } else if (config.action === 'cancel') {
    await page.getByText('Started the requested work.', { exact: true }).waitFor();
    const stopped = page.waitForResponse(response => response.url().endsWith('/stop'));
    await page.getByRole('button', { name: 'Stop', exact: true }).click();
    assert.equal((await stopped).status(), 200);
    const reattached = page.waitForResponse(response => new URL(response.url()).pathname.endsWith('/stream'));
    await page.reload();
    assert.equal((await reattached).status(), 200);
    await writeFile(config.releaseFile, 'The browser reattached while stopping.');
    answer = /Started the requested work[.]\s+Stopped as requested[.]/;
  } else if (config.action === 'approve' || config.action === 'deny') {
    await page.getByText('I will read the project file.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Approve once', exact: true }).waitFor();
    await page.screenshot({ path: path.join(config.artifactDirectory, 'approval.png'), fullPage: true });
    const decisionPath = '/approvals/agent-host-permission:read-project/decision';
    const decisionSent = page.waitForRequest(request => request.url().includes(decisionPath));
    await page.getByRole('button', {
      name: config.action === 'approve' ? 'Approve once' : 'Deny', exact: true,
    }).click();
    const decision = await decisionSent;
    assert.equal(decision.postDataJSON().decision, config.action === 'approve' ? 'APPROVE_ONCE' : 'DENY');
    answer = config.action === 'approve'
      ? 'Read approved: # Mock project'
      : 'Read denied; no file was accessed.';
  } else {
    await page.getByText('前 café 👩🏽‍💻', { exact: true }).waitFor();
    await page.screenshot({ path: path.join(config.artifactDirectory, 'partial.png'), fullPage: true });
    if (config.action === 'crash') {
      await page.getByText(/encountered an error on this computer/).waitFor();
      answer = '前 café 👩🏽‍💻';
    } else {
      await page.getByRole('button', { name: 'Stop', exact: true }).waitFor();
      if (config.action === 'disconnect') {
        await page.close();
        page = await context.newPage();
        page.setDefaultTimeout(45_000);
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(`${origin}${config.conversationUrl}`);
      }
      await writeFile(config.releaseFile, 'The browser observed the first text.');
      answer = /前 café 👩🏽‍💻\s+second line\s+完成/;
    }
  }
  await page.getByText(answer, { exact: typeof answer === 'string' }).waitFor();
  await page.getByRole('button', { name: 'Stop', exact: true }).waitFor({ state: 'hidden' });
  await page.reload();
  if (config.action === 'cancel') answer = /Started the requested work[.]\s+Stopped as requested[.]/;
  await page.getByText(answer, { exact: typeof answer === 'string' }).waitFor();
  await page.getByRole('button', { name: 'Stop', exact: true }).waitFor({ state: 'hidden' });
  assert.equal(await page.getByRole('button', { name: 'Approve once', exact: true }).count(), 0);
  assert.deepEqual(errors, [], 'the chat must not raise unhandled browser errors');
  await page.screenshot({ path: path.join(config.artifactDirectory, 'completed.png'), fullPage: true });
} catch (error) {
  if (page) {
    await page.screenshot({ path: path.join(config.artifactDirectory, 'failure.png'), fullPage: true }).catch(() => {});
    await writeFile(path.join(config.artifactDirectory, 'page.txt'), await page.locator('body').innerText().catch(() => 'Page unavailable'));
  }
  throw error;
} finally {
  await browser?.close();
  server.kill('SIGTERM');
  log.end();
  network.end();
}
