/* Real screens for the landing page, from the sample workspace at
   /demo/landing. Run against a dev server:

     node scripts/capture-landing-shots.mjs [origin] [out-dir]

   Each shot puts the demo on a screen with the same message the landing's
   Try buttons send, then crops to the part the page shows. All taken at 2x
   and written as WebP. When the product changes, run this again rather than
   editing the images. */
import { chromium } from "playwright";
import sharp from "sharp";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const origin = process.argv[2] ?? "http://localhost:3000";
const out = path.resolve(process.argv[3] ?? path.join(import.meta.dirname, "../public/landing"));

/** The strip over a standalone demo ("Acme · Back to Lemma") is for its
 *  visitors, not for a picture of it. */
async function belowBanner(page) {
    return page.evaluate(() => {
        const shell = document.querySelector(".shell, .app, main") ?? document.body;
        const banner = document.querySelector(".preview-document__note");
        return banner ? Math.ceil(banner.getBoundingClientRect().bottom) : shell.getBoundingClientRect().top;
    });
}

/** The box around every element matching the selectors, plus a margin. */
async function around(page, selectors, pad = 0) {
    const box = await page.evaluate(([list, margin]) => {
        const boxes = list.flatMap((selector) => [...document.querySelectorAll(selector)]).map((node) => node.getBoundingClientRect());
        if (boxes.length === 0) return null;
        const left = Math.min(...boxes.map((b) => b.left)) - margin;
        const top = Math.min(...boxes.map((b) => b.top)) - margin;
        const right = Math.max(...boxes.map((b) => b.right)) + margin;
        const bottom = Math.max(...boxes.map((b) => b.bottom)) + margin;
        return { x: Math.max(0, left), y: Math.max(0, top), width: right - left, height: bottom - top };
    }, [selectors, pad]);
    if (!box) throw new Error("Nothing on screen matches " + selectors.join(", "));
    return box;
}

async function write(png, name, quality = 88) {
    await sharp(png).webp({ quality }).toFile(path.join(out, name + ".webp"));
    console.log("wrote", name);
}

async function step(page, index) {
    await page.evaluate((to) => window.postMessage({ type: "lemma-tour:step", step: to }, location.origin), index);
    await page.waitForTimeout(3500);
}

/** A dialog alone, on nothing: the page behind it hidden and the backdrop
 *  clear, so the picture has transparent corners instead of a grey smear. */
const DIALOG_ALONE = "body > *:not(.modal) { visibility: hidden !important } .modal { background: transparent !important; backdrop-filter: none !important } html, body { background: transparent !important }";
// The dev server's own badge, which no visitor sees, and About's jump row.
const QUIET = "nextjs-portal { display: none !important; } .aboutpage__jumps { visibility: hidden !important }";

await mkdir(out, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });

/* Each sample teammate's own app, below the banner: the tabs under "The
   tools the job needs". All the same size, 1280×720, so switching tabs never
   moves the page; 1280 rather than 1440 so the text stays readable at the
   size the landing shows it. */
const APPS = ["kit", "remy", "june", "scout"];
const apps = await browser.newPage({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2 });
for (const teammate of APPS) {
    await apps.goto(origin + "/demo/launch?teammate=" + teammate, { waitUntil: "networkidle" });
    await apps.addStyleTag({ content: QUIET });
    await apps.waitForTimeout(2500);
    const top = await belowBanner(apps);
    await apps.setViewportSize({ width: 1280, height: top + 720 });
    await apps.waitForTimeout(800);
    await write(await apps.screenshot({ type: "png", clip: { x: 0, y: top, width: 1280, height: 720 } }), "app-" + teammate, 86);
    await apps.setViewportSize({ width: 1280, height: 800 });
}
await apps.close();

await page.goto(origin + "/demo/landing", { waitUntil: "networkidle" });
await page.addStyleTag({ content: QUIET });
await page.waitForTimeout(4000);

/* Who's in: the people dialog, on nothing. */
await step(page, 1);
{
    const alone = await page.addStyleTag({ content: DIALOG_ALONE });
    await write(await page.screenshot({ type: "png", clip: await around(page, ['[role="dialog"]']), omitBackground: true }), "people");
    await alone.evaluate((node) => node.remove());
}

/* What it remembers, and one of those notes opened. */
await step(page, 3);
await write(await page.screenshot({ type: "png", clip: await around(page, ['[data-about="memory"]'], 12) }), "remembers");
await page.locator(".notes__row").first().click();
await page.waitForTimeout(3500);
await write(await page.screenshot({ type: "png", clip: { x: 350, y: 125, width: 820, height: 210 } }), "note-open");

/* The moment it learns, in Kit's own demo conversation: the correction,
   the reply, and the "Kit noted this" line the write under it draws. */
await step(page, -1);
{
    await page.locator(".noted").last().scrollIntoViewIfNeeded();
    await page.waitForTimeout(600);
    const clip = await page.evaluate(() => {
        const asks = [...document.querySelectorAll(".msg--you")];
        const notes = [...document.querySelectorAll(".noted")];
        const top = asks[asks.length - 1]?.getBoundingClientRect();
        const bottom = notes[notes.length - 1]?.getBoundingClientRect();
        const column = document.querySelector(".convo")?.getBoundingClientRect();
        if (!top || !bottom || !column) return null;
        return { x: Math.max(0, column.left - 28), y: top.top - 24, width: column.width + 44, height: bottom.bottom - top.top + 48 };
    });
    if (!clip) throw new Error("No correction and noted line in Kit's conversation");
    await write(await page.screenshot({ type: "png", clip }), "noted", 90);
}

await browser.close();
