import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chromium } from 'playwright';

/* A page is read on whatever screen it is opened on. A desktop gives it a wide
   margin either side of a 68ch measure; a phone cannot afford that margin, and
   at the desktop's value the text sat in a narrow column with a third of the
   screen spent on two empty strips. These are the widths that must not drift
   back together, and the sideways scroll that appears when a fix for one is a
   fix for the other.

   Run it against a development server in sample mode:

       NEXT_PUBLIC_DATA=sample npm run dev
       node --test tests/browser/page-margins.test.mjs

   `LEMMA_TEST_ORIGIN` overrides the origin. Sample mode is what makes the page
   reachable: the path below is one of its fixtures, and a real deployment
   would need somebody's session to open a page. */

const origin = process.env.LEMMA_TEST_ORIGIN || 'http://localhost:3000';
const PAGE = '/l/marketing/files/launch/monday-launch-v3.md';

/** What the page is laid out as, measured in the browser rather than read off
 *  the stylesheet: a rule that loses a specificity fight still reads correctly
 *  in the source.
 *
 *  `beside` marks the pane the way the shell does when the page is opened
 *  beside a conversation — on a phone that pane is the sheet over the chat, and
 *  it carries margins of its own. Sample mode has no conversation that opens a
 *  markdown page beside it, so the attribute is set by hand rather than driven
 *  through the app. */
async function measure(browser, width, height, { beside = false } = {}) {
    const context = await browser.newContext({
        viewport: { width, height },
        isMobile: width < 600,
        hasTouch: width < 1024,
    });
    try {
        const page = await context.newPage();
        const response = await page.goto(origin + PAGE, { waitUntil: 'networkidle', timeout: 60_000 });
        assert.ok(response?.ok(), `${origin + PAGE} did not load — is the server running, with NEXT_PUBLIC_DATA=sample?`);
        await page.locator('.docspace__page').waitFor();
        return await page.evaluate((right) => {
            const sheet = document.querySelector('.docspace__page');
            const text = document.querySelector('.md');
            const prose = document.querySelector('.md > p');
            if (!sheet || !text) throw new Error('the page did not render');
            if (right) sheet.closest('.pane')?.setAttribute('data-side', 'right');
            const style = getComputedStyle(sheet);
            return {
                left: parseFloat(style.paddingLeft),
                right: parseFloat(style.paddingRight),
                text: text.getBoundingClientRect().width,
                prose: prose ? prose.getBoundingClientRect().width : null,
                viewport: window.innerWidth,
                sideways: document.documentElement.scrollWidth - window.innerWidth,
            };
        }, beside);
    } finally {
        await context.close();
    }
}

test('a page on a phone spends a strip, not a third of the screen, on its margins', async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const phone = await measure(browser, 375, 812);
        assert.ok(phone.left <= 24 && phone.right <= 24, `a phone keeps ${phone.left}px and ${phone.right}px of margin`);
        assert.ok(phone.left >= 12, 'the text has to stay clear of the screen edge');
        assert.ok(phone.text / phone.viewport > 0.85, `the text is ${Math.round(phone.text)} of ${phone.viewport}px wide`);
        assert.equal(phone.sideways, 0, 'the page scrolls sideways');
    } finally {
        await browser.close();
    }
});

test('a page opened beside a conversation on a phone spends a strip as well', async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const sheet = await measure(browser, 375, 812, { beside: true });
        assert.ok(sheet.left <= 24 && sheet.right <= 24, `the sheet keeps ${sheet.left}px and ${sheet.right}px of margin`);
        assert.ok(sheet.text / sheet.viewport > 0.85, `the text is ${Math.round(sheet.text)} of ${sheet.viewport}px wide`);
        assert.equal(sheet.sideways, 0, 'the sheet scrolls sideways');
    } finally {
        await browser.close();
    }
});

test('a page on a laptop keeps its measure and its wider margins', async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const laptop = await measure(browser, 1440, 900);
        assert.equal(laptop.left, 56);
        assert.equal(laptop.right, 56);
        // 760px of sheet, and the paragraph held to its 68-character measure.
        assert.ok(laptop.text <= 761, `the sheet grew to ${laptop.text}px`);
        assert.ok(laptop.prose > 600 && laptop.prose < 720, `a paragraph is ${Math.round(laptop.prose)}px wide`);
        assert.equal(laptop.sideways, 0, 'the page scrolls sideways');
    } finally {
        await browser.close();
    }
});
