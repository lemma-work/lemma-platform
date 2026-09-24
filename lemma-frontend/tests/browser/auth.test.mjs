import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { chromium } from 'playwright';

let origin;
let server;
let browser;
let output = '';
const stop = () => {
    if (!server?.pid) return;
    try { process.kill(-server.pid, 'SIGTERM'); } catch { /* Already stopped. */ }
};
process.once('exit', stop);
before(async () => {
    const reservation = createServer();
    await new Promise(resolve => reservation.listen(0, '127.0.0.1', resolve));
    const port = reservation.address().port;
    await new Promise(resolve => reservation.close(resolve));
    origin = `http://127.0.0.1:${port}`;
    server = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'dev', '--webpack', '--hostname', '127.0.0.1', '--port', String(port)], {
        env: { ...process.env, NEXT_PUBLIC_API_URL: origin + '/__auth-test', NEXT_PUBLIC_DATA: 'live', NEXT_TELEMETRY_DISABLED: '1' },
        stdio: ['ignore', 'pipe', 'pipe'],
        detached: true,
    });
    server.stdout.on('data', chunk => { output += chunk; });
    server.stderr.on('data', chunk => { output += chunk; });
    const deadline = Date.now() + 90_000;
    while (true) {
        if (server.exitCode !== null) throw new Error(output);
        try { if ((await fetch(origin + '/auth', { signal: AbortSignal.timeout(10_000) })).ok) break; } catch { /* server starting */ }
        if (Date.now() > deadline) throw new Error(output);
        await new Promise(resolve => setTimeout(resolve, 250));
    }
    browser = await chromium.launch({ headless: true, channel: process.env.LEMMA_TEST_BROWSER_CHANNEL || undefined });
}, { timeout: 120_000 });
after(async () => { await browser?.close(); stop(); });

async function fixture(t, { signedIn = false, verified = false } = {}) {
    const context = await browser.newContext();
    context.setDefaultTimeout(15_000);
    t.after(() => context.close());
    const state = { signedIn, verified, sends: 0, signups: 0, verifies: 0, blockedAccess: false, failAfterSignup: false, callbacks: 0, codeSends: 0, codeVerifies: 0, nonceRequests: 0, challengeId: "first-challenge", failAfterCode: false };
    const headers = () => ({
        'front-token': Buffer.from(JSON.stringify({ uid: 'test-user', ate: Date.now() + 3600_000, up: { 'st-ev': { v: state.verified, t: Date.now() } } })).toString('base64'),
    });
    await context.route(origin + '/__auth-test/**', async route => {
        const url = new URL(route.request().url());
        const path = url.pathname.replace('/__auth-test', '');
        const json = (body, status = 200, extra = {}) => route.fulfill({ status, json: body, headers: extra });
        if (path === '/users/me') {
            if (state.blockedAccess) return json({}, 503);
            return state.signedIn
                ? state.verified ? json({ id: 'test-user', email: 'person@example.test' }) : json({ claimValidationErrors: [{ id: 'st-ev' }] }, 403)
                : json({}, 401);
        }
        if (path.startsWith('/auth/email-code/')) {
            const body = route.request().postDataJSON();
            assert.equal(route.request().headers()['st-auth-mode'], 'cookie');
            if (path.endsWith('/browser')) { state.nonceRequests++; return json({ nonce: 'browser-binding' }); }
            assert.equal(body.nonce, 'browser-binding');
            if (path.endsWith('/start') || path.endsWith('/resend')) {
                if (path.endsWith('/resend')) {
                    assert.equal(body.challenge_id, state.challengeId);
                    state.challengeId = 'replacement-challenge';
                }
                state.codeSends++;
                return json({ challenge_id: state.challengeId, expires_at: new Date(Date.now() + 600_000).toISOString() });
            }
            assert.equal(body.challenge_id, state.challengeId);
            state.codeVerifies++;
            if (body.code !== '123456') return json({ message: 'The code did not match; try again' }, 400);
            state.signedIn = true;
            state.verified = true;
            if (state.failAfterCode) state.blockedAccess = true;
            return json({ status: 'complete' }, 200, headers());
        }
        if (path === '/auth/altcha/challenge') return json({ enabled: false });
        if (path === '/st/auth/authorisationurl') return json({ status: 'OK', urlWithQueryParams: origin + '/fake-provider?provider=' + url.searchParams.get('thirdPartyId') });
        if (path === '/st/auth/signup' || path === '/st/auth/signin' || path === '/st/auth/signinup') {
            state.signedIn = true;
            if (path.endsWith('/signup')) { state.signups++; if (state.failAfterSignup) state.blockedAccess = true; }
            if (path.endsWith('/signinup')) state.callbacks++;
            return json({ status: 'OK', user: { id: 'test-user', emails: ['person@example.test'], timeJoined: Date.now(), loginMethods: [] } }, 200, headers());
        }
        if (path === '/st/auth/session/refresh') return state.signedIn ? json({ status: 'OK' }, 200, headers()) : json({}, 401);
        if (path === '/st/auth/signout') { state.signedIn = false; return json({ status: 'OK' }, 200, { 'front-token': 'remove' }); }
        if (path === '/st/auth/user/email/verify/token') {
            state.sends++;
            return json({ status: state.verified ? 'EMAIL_ALREADY_VERIFIED_ERROR' : 'OK' });
        }
        if (path === '/st/auth/user/email/verify') {
            if (route.request().method() === 'POST') { state.verified = true; state.verifies++; return json({ status: 'OK' }); }
            return json({ status: 'OK', isVerified: state.verified });
        }
        if (path === '/st/auth/user/password/reset/token' || path === '/st/auth/user/password/reset') return json({ status: 'OK' });
        throw new Error('Unexpected auth request: ' + path);
    });
    await context.route(origin + '/fake-provider**', route => {
        const url = new URL(route.request().url());
        return route.fulfill({ status: 302, headers: { location: origin + '/auth/callback/' + url.searchParams.get('provider') + '?code=example&state=' + encodeURIComponent(url.searchParams.get('state')) } });
    });
    await context.route(origin + '/destination**', route => route.fulfill({ contentType: 'text/html', body: '<h1>Requested destination</h1>' }));
    const page = await context.newPage();
    return { page, context, state };
}

const start = '/auth?redirect_uri=' + encodeURIComponent('/destination?view=files#section');
async function submit(page, label = 'Make my account') {
    if (!(await page.getByLabel('Password', { exact: true }).isVisible())) await page.getByRole('button', { name: 'Use a password instead' }).click();
    await page.getByLabel('Email', { exact: true }).fill('person@example.test');
    await page.getByLabel('Password', { exact: true }).fill('ExamplePassword123!');
    await page.getByRole('button', { name: label, exact: true }).click();
}

test('signup sends verification, verifies in another tab, and resumes the original destination', { timeout: 60_000 }, async t => {
    const { page, context, state } = await fixture(t);
    await page.goto(origin + start);
    await page.getByRole('link', { name: 'Make an account', exact: true }).click();
    await submit(page);
    await page.getByRole('heading', { name: 'Check your email', exact: true }).waitFor();
    assert.equal(state.signups, 1);
    assert.equal(state.sends, 1);
    const email = await context.newPage();
    await email.goto(origin + '/auth/verify-email?token=example-token&tenantId=public');
    await email.getByRole('heading', { name: 'Your email is verified' }).waitFor();
    await page.getByRole('heading', { name: 'Your email is verified' }).waitFor();
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await page.waitForURL(origin + '/destination?view=files#section');
    assert.equal(state.verifies, 1);
});

test('a returning user goes straight to the requested destination', async t => {
    const { page } = await fixture(t, { signedIn: true, verified: true });
    await page.goto(origin + start);
    await page.waitForURL(origin + '/destination?view=files#section');
});

test('password reset preserves the destination through the emailed link in the same tab', async t => {
    const { page, state } = await fixture(t, { verified: true });
    await page.goto(origin + start);
    await page.getByRole('button', { name: 'Use a password instead' }).click();
    await page.getByRole('link', { name: 'Forgotten your password?' }).click();
    await page.getByLabel('Email', { exact: true }).fill('person@example.test');
    await page.getByRole('button', { name: 'Email me a link' }).click();
    await page.getByRole('heading', { name: 'Check your email', exact: true }).waitFor();
    await page.goto(origin + '/auth/reset-password?token=example-token');
    await page.getByLabel('New password', { exact: true }).fill('Replacement123!');
    await page.getByRole('button', { name: 'Save password' }).click();
    await page.getByRole('link', { name: 'Sign in', exact: true }).click();
    await submit(page, 'Sign in');
    await page.waitForURL(origin + '/destination?view=files#section');
    assert.equal(state.signups, 0);
});

test('legacy signup links show account creation and unverified sessions resume verification', async t => {
    const { page, state } = await fixture(t);
    await page.goto(origin + '/auth?show=signup');
    await page.getByRole('heading', { name: 'Make an account', exact: true }).waitFor();
    state.signedIn = true;
    await page.goto(origin + start);
    await page.getByRole('heading', { name: 'Check your email', exact: true }).waitFor();
    assert.equal(state.sends, 1);
});

for (const provider of ['Google', 'Microsoft']) {
    test(`${provider} returns to the saved destination after its callback`, async t => {
        const { page, state } = await fixture(t, { verified: true });
        await page.goto(origin + start);
        await page.getByRole('button', { name: 'Continue with ' + provider }).click();
        await page.waitForURL(origin + '/destination?view=files#section');
        assert.equal(state.callbacks, 1);
    });
}

test('retrying account completion never creates a second account', async t => {
    const { page, state } = await fixture(t, { verified: true });
    state.failAfterSignup = true;
    await page.goto(origin + '/auth/signup?redirect_uri=%2Fdestination');
    await submit(page);
    await page.getByRole('alert').filter({ hasText: 'check your account' }).waitFor();
    state.blockedAccess = false;
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await page.waitForURL(origin + '/destination');
    assert.equal(state.signups, 1);
});

for (const path of ['/auth', '/auth/signup']) {
    test(`email code from ${path} verifies and returns without a password or verification email`, async t => {
        const { page, state } = await fixture(t);
        await page.goto(origin + path + '?redirect_uri=%2Fdestination%3Fview%3Dfiles%23section');
        await page.getByLabel('Email', { exact: true }).fill('person@example.test');
        assert.equal(await page.getByLabel('Password', { exact: true }).count(), 0);
        await page.getByRole('button', { name: 'Continue with email', exact: true }).click();
        await page.getByLabel('Verification code').fill('000000');
        await page.getByRole('button', { name: 'Verify and continue' }).click();
        await page.getByRole('alert').filter({ hasText: 'did not match' }).waitFor();
        await page.getByLabel('Verification code').fill('123456');
        await page.getByRole('button', { name: 'Verify and continue' }).click();
        await page.waitForURL(origin + '/destination?view=files#section');
        assert.equal(state.nonceRequests, 1);
        assert.equal(state.codeSends, 1);
        assert.equal(state.codeVerifies, 2);
        assert.equal(state.sends, 0);
        assert.equal(state.signups, 0);
    });
}

test('resending replaces the challenge and keeps the browser binding', async t => {
    const { page, state } = await fixture(t);
    await page.clock.install();
    await page.goto(origin + start);
    await page.getByLabel('Email', { exact: true }).fill('person@example.test');
    await page.getByRole('button', { name: 'Continue with email', exact: true }).click();
    await page.getByLabel('Verification code').waitFor();
    assert.equal(await page.getByRole('button', { name: /Resend in/ }).isEnabled(), false);
    await page.clock.fastForward(61_000);
    await page.getByRole('button', { name: 'Resend code', exact: true }).click();
    await page.getByRole('button', { name: /Resend in/ }).waitFor();
    await page.getByLabel('Verification code').fill('123456');
    await page.getByRole('button', { name: 'Verify and continue' }).click();
    await page.waitForURL(origin + '/destination?view=files#section');
    assert.equal(state.nonceRequests, 1);
    assert.equal(state.codeSends, 2);
});

test('a failed post-code account check retries completion without consuming the code again', async t => {
    const { page, state } = await fixture(t);
    state.failAfterCode = true;
    await page.goto(origin + start);
    await page.getByLabel('Email', { exact: true }).fill('person@example.test');
    await page.getByRole('button', { name: 'Continue with email', exact: true }).click();
    await page.getByLabel('Verification code').fill('123456');
    await page.getByRole('button', { name: 'Verify and continue' }).click();
    await page.getByRole('alert').filter({ hasText: 'check your account' }).waitFor();
    state.blockedAccess = false;
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    await page.waitForURL(origin + '/destination?view=files#section');
    assert.equal(state.codeVerifies, 1);
});
