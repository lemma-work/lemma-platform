import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chromium } from 'playwright';

// Run against a development server so this exercises the bundler's peer resolution.
test('SDK table hooks share the frontend QueryClient and receive cache updates', async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(new URL('/demo/query-context', process.env.LEMMA_TEST_ORIGIN || 'http://localhost:3000').href);
        await page.getByRole('button', { name: 'Update shared cache' }).click();
        await page.getByText('{"name":"Updated through the frontend"}', { exact: true }).waitFor();
        assert.equal(await page.getByRole('status').textContent(), 'Shared query cache connected');
        assert.deepEqual(errors, []);
    } finally {
        await browser.close();
    }
});
