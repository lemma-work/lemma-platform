import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { readdir, readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

// This suite requires an isolated backend and its filesystem email transport.
// No auth route is intercepted: email contents and session checks are real.
const origin = process.env.LEMMA_AUTH_QA_ORIGIN;
const api = process.env.LEMMA_AUTH_QA_API;
const mailbox = process.env.LEMMA_AUTH_QA_MAILBOX;
const enabled = Boolean(origin && api && mailbox);
const options = { skip: !enabled, timeout: 120_000 };
let browser;
before(async () => {
    if (!enabled) return;
    for (const value of [origin, api]) {
        assert.ok(['localhost', '127.0.0.1', '[::1]'].includes(new URL(value).hostname), 'Live auth tests must use an isolated loopback stack');
    }
    browser = await chromium.launch({ headless: true, channel: process.env.LEMMA_TEST_BROWSER_CHANNEL || undefined });
});
after(async () => { await browser?.close(); });

async function pageFor(t) {
    const context = await browser.newContext();
    context.setDefaultTimeout(30_000);
    t.after(() => context.close());
    return context.newPage();
}
const address = prefix => `${prefix}-${crypto.randomUUID()}@example.com`;
async function mailFor(email) {
    for (const file of (await readdir(mailbox)).sort().reverse()) {
        if (!file.endsWith('.json')) continue;
        const mail = JSON.parse(await readFile(mailbox + '/' + file, 'utf8'));
        if (mail.to_email === email) {
            assert.equal(mail.transport, 'filesystem');
            return mail.text_content;
        }
    }
    throw new Error('No local test email was delivered');
}
async function codeFor(email) { return (await mailFor(email)).match(/\b\d{6}\b/)[0]; }
async function emailLink(email, path) {
    const links = (await mailFor(email)).match(/https?:\/\/[^\s]+/g) || [];
    const link = links.find(value => new URL(value).pathname.includes(path));
    assert.ok(link, 'Expected link in local test inbox');
    assert.equal(new URL(link).origin, origin);
    return link;
}
async function enterEmail(page, email, path = '/auth/signup') {
    await page.goto(origin + path);
    await page.getByLabel('Email', { exact: true }).fill(email);
    await page.getByRole('button', { name: 'Continue with email', exact: true }).click();
}
async function verifyCode(page, code) {
    await page.getByLabel('Verification code').fill(code);
    const response = page.waitForResponse(r => r.url().endsWith('/email-code/verify'));
    await page.getByRole('button', { name: 'Verify and continue', exact: true }).click();
    return (await response).json();
}
async function account(page) {
    await page.waitForURL(url => !url.pathname.startsWith('/auth'));
    const response = await page.request.get(api + '/users/me');
    assert.equal(response.status(), 200);
    return response.json();
}

test('real OTP signup, wrong-code recovery and returning sign-in preserve one account', options, async t => {
    const email = address('otp');
    const signup = await pageFor(t);
    await enterEmail(signup, email);
    await signup.getByLabel('Verification code').waitFor();
    const code = await codeFor(email);
    assert.equal((await verifyCode(signup, code === '000000' ? '111111' : '000000')).code, 'EMAIL_CODE_INVALID');
    await verifyCode(signup, code);
    const created = await account(signup);
    const signin = await pageFor(t);
    await enterEmail(signin, email, '/auth');
    await signin.getByLabel('Verification code').waitFor();
    await verifyCode(signin, await codeFor(email));
    assert.equal((await account(signin)).id, created.id);
});

test('real email correction abandons the old challenge immediately', options, async t => {
    const page = await pageFor(t);
    await enterEmail(page, address('typo'));
    await page.getByRole('button', { name: 'Change email', exact: true }).click();
    const corrected = address('corrected');
    await page.getByLabel('Email', { exact: true }).fill(corrected);
    await page.getByRole('button', { name: 'Continue with email', exact: true }).click();
    await page.getByLabel('Verification code').waitFor();
    await verifyCode(page, await codeFor(corrected));
    await account(page);
});

test('real password signup, verification, lookup from signup, and reset', options, async t => {
    const email = address('password'), password = 'LocalTest!12345', replacement = 'UpdatedLocal!12345';
    const page = await pageFor(t);
    await page.goto(origin + '/auth/signup');
    await page.getByRole('button', { name: 'Use a password instead' }).click();
    await page.getByLabel('Email', { exact: true }).fill(email);
    await page.getByLabel('Password', { exact: true }).fill(password);
    await page.getByRole('button', { name: 'Make my account' }).click();
    await page.getByRole('heading', { name: 'Check your email', exact: true }).waitFor();
    const verification = await page.context().newPage();
    await verification.goto(await emailLink(email, 'verify-email'));
    await page.getByRole('heading', { name: 'Your email is verified', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
    const created = await account(page);
    const signin = await pageFor(t);
    await enterEmail(signin, email);
    await signin.getByLabel('Password', { exact: true }).fill('wrong-password');
    await signin.getByRole('button', { name: 'Sign in', exact: true }).click();
    await signin.getByRole('alert').filter({ hasText: 'do not go together' }).waitFor();
    await signin.getByLabel('Password', { exact: true }).fill(password);
    await signin.getByRole('button', { name: 'Sign in', exact: true }).click();
    assert.equal((await account(signin)).id, created.id);
    const reset = await pageFor(t);
    await reset.goto(origin + '/auth/reset-password');
    await reset.getByLabel('Email', { exact: true }).fill(email);
    await reset.getByRole('button', { name: 'Email me a link' }).click();
    await reset.getByRole('heading', { name: 'Check your email', exact: true }).waitFor();
    await reset.goto(await emailLink(email, 'reset-password'));
    await reset.getByLabel('New password', { exact: true }).fill(replacement);
    await reset.getByRole('button', { name: 'Save password' }).click();
    await reset.getByRole('heading', { name: 'Password updated.' }).waitFor();
    await enterEmail(reset, email, '/auth');
    await reset.getByLabel('Password', { exact: true }).fill(replacement);
    await reset.getByRole('button', { name: 'Sign in', exact: true }).click();
    assert.equal((await account(reset)).id, created.id);
});

test('real attempt limit and timed resend reject the old code and accept its replacement', options, async t => {
    const email = address('resend'), page = await pageFor(t);
    await enterEmail(page, email);
    await page.getByLabel('Verification code').waitFor();
    const old = await codeFor(email), wrong = old === '000000' ? '111111' : '000000';
    for (let attempt = 0; attempt < 4; attempt++) {
        assert.equal((await verifyCode(page, wrong)).code, attempt < 3 ? 'EMAIL_CODE_INVALID' : 'EMAIL_CODE_EXPIRED');
    }
    await page.getByText('This code can no longer be used. Request a new code below.').waitFor();
    assert.equal(await page.getByRole('button', { name: 'Verify and continue' }).isEnabled(), false);
    await page.getByRole('button', { name: 'Resend code', exact: true }).waitFor({ timeout: 70_000 });
    await page.getByRole('button', { name: 'Resend code', exact: true }).click();
    await page.getByRole('button', { name: /Resend in/ }).waitFor();
    const replacement = await codeFor(email);
    if (old !== replacement) assert.equal((await verifyCode(page, old)).code, 'EMAIL_CODE_INVALID');
    await verifyCode(page, replacement);
    await account(page);
});
