/* Real screens for the repository README, from the sample workspace at
   /demo/landing: Kit's space, the app Kit built, who can open it, a page
   and a workflow run. Run against a dev server, after
   scripts/capture-landing-shots.mjs and capture-product-shots.mjs (the
   people, page and run pictures are theirs, converted):

     node scripts/capture-readme-shots.mjs [origin] [out-dir]

   PNG, because GitHub renders it everywhere, at a width the README column
   shows sharp on a high-density screen without shipping megabytes. When
   the product changes, run this again rather than editing the images. */
import { chromium } from "playwright";
import sharp from "sharp";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const origin = process.argv[2] ?? "http://localhost:3000";
const out = path.resolve(process.argv[3] ?? path.join(import.meta.dirname, "../../docs/Assets/Screenshots"));
const landing = path.join(import.meta.dirname, "../public/landing");
const product = path.join(import.meta.dirname, "../public/product");
// The dev badge, and the jump-to-newest button a scrolled-up thread shows.
const QUIET = "nextjs-portal { display: none !important; } [aria-label='Jump to the newest message'] { visibility: hidden !important }";

async function png(input, name, width) {
    await sharp(input).resize({ width, withoutEnlargement: true }).png({ compressionLevel: 9, palette: true, quality: 92 }).toFile(path.join(out, name + ".png"));
    console.log("wrote", name);
}

async function belowBanner(page) {
    return page.evaluate(() => Math.ceil(document.querySelector(".preview-document__note")?.getBoundingClientRect().bottom ?? 0));
}

await mkdir(out, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });

/* Kit's space, open on the first ask: what was asked, the work folded into
   its steps, the answer and the themes it found. */
await page.goto(origin + "/demo/landing", { waitUntil: "networkidle" });
await page.addStyleTag({ content: QUIET });
await page.waitForTimeout(4000);
for (let i = 0; i < 6; i++) {
    const earlier = page.getByText("Earlier").first();
    if (!(await earlier.count())) break;
    await earlier.click();
    await page.waitForTimeout(700);
}
await page.evaluate(() => {
    const scroller = [...document.querySelectorAll("*")].filter((node) => node.scrollHeight > node.clientHeight + 50 && /auto|scroll/.test(getComputedStyle(node).overflowY)).sort((a, b) => b.scrollHeight - a.scrollHeight)[0];
    const reply = [...document.querySelectorAll(".msg--you")][0];
    if (scroller && reply) scroller.scrollTop += reply.getBoundingClientRect().top - 120;
});
await page.mouse.move(1, 1);
await page.waitForTimeout(1500);
{
    const top = await belowBanner(page);
    await png(await page.screenshot({ type: "png", clip: { x: 0, y: top, width: 1440, height: 900 - top } }), "space", 1800);
}

/* The app Kit built, on its first screen. */
await page.goto(origin + "/demo/launch?teammate=kit", { waitUntil: "networkidle" });
await page.addStyleTag({ content: QUIET });
await page.waitForTimeout(2500);
{
    const top = await belowBanner(page);
    await png(await page.screenshot({ type: "png", clip: { x: 0, y: top, width: 1440, height: 900 - top } }), "feedback-loop", 1800);
}
await browser.close();

/* The landing's and product pages' own captures, as PNG for GitHub: who
   can open Kit, Scout's research page, and June's run waiting on Dev. */
await png(path.join(landing, "people.webp"), "people", 1040);
await png(path.join(product, "pages-hero.webp"), "page", 1200);
await png(path.join(product, "workflows-run.webp"), "workflow-run", 1200);
