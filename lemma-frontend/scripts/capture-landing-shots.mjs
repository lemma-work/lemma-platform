/* Real screens for the landing page, from the sample workspace at
   /demo/landing. Run against a dev server:

     node scripts/capture-landing-shots.mjs [origin] [out-dir]

   Each shot puts the demo on a screen, in its light theme, then crops to the
   part the page shows. All taken at 2x and written as WebP. When the product
   changes, run this again rather than editing the images, and check the hero's
   labels against the positions it prints (src/app/(marketing)/hero.tsx). */
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

/* The rest are Kit's own space, light, below the demo's banner. */
const light = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, colorScheme: "light" });
await light.addInitScript(() => { try { localStorage.setItem("lemma-tour:theme", "light"); } catch { /* storage refused: light is the default anyway */ } });
const kit = await light.newPage();

async function open(viewport) {
    await kit.setViewportSize(viewport);
    await kit.goto(origin + "/demo/landing", { waitUntil: "networkidle" });
    await kit.addStyleTag({ content: QUIET });
    await kit.waitForTimeout(3500);
}

/** The whole window under the banner, with the badge hidden in any app frame too. */
async function whole(name, height) {
    await kit.waitForTimeout(2500);
    for (const frame of kit.frames()) await frame.addStyleTag({ content: QUIET }).catch(() => undefined);
    const top = await belowBanner(kit);
    await write(await kit.screenshot({ type: "png", clip: { x: 0, y: top, width: 1440, height: height - top } }), name, 86);
}

async function go(place) {
    await kit.getByRole("button", { name: place }).first().click();
    await kit.waitForTimeout(1500);
}

async function choose(text) {
    const found = await kit.evaluate((wanted) => {
        const node = [...document.querySelectorAll("*")].find((el) => el.childElementCount === 0 && el.textContent.trim() === wanted && el.getBoundingClientRect().width > 0);
        if (!node) return false;
        (node.closest("a, button, [role=row], tr, li") ?? node).click();
        return true;
    }, text);
    if (!found) throw new Error("Nothing on screen says " + text);
    await kit.waitForTimeout(2500);
}

/* The hero: the whole space, Kit's turn folded, the ask and the answer in
   view. Prints where each part sits, as shares of the picture, for the
   labels in hero.tsx. */
await open({ width: 1440, height: 860 });
await whole("day-space", 860);
console.log("hero parts", JSON.stringify(await kit.evaluate(() => {
    const share = (node) => {
        if (!node) return null;
        const box = node.getBoundingClientRect();
        const top = document.querySelector(".preview-document__note")?.getBoundingClientRect().bottom ?? 0;
        return { x: (box.left / 1440 * 100).toFixed(1) + "%", right: (box.right / 1440 * 100).toFixed(1) + "%", y: ((box.top + box.height / 2 - top) / (860 - top) * 100).toFixed(1) + "%" };
    };
    const button = (text) => [...document.querySelectorAll("button, a")].find((node) => node.textContent.trim().startsWith(text) && node.getBoundingClientRect().width > 0);
    return { apps: share(button("Apps")), tables: share(button("Tables")), files: share(button("Files")), workflows: share(button("Workflows")), share: share(button("Share")), steps: share(button("Worked for")) };
})));

/* It builds: one whole window per part of the space. */
await open({ width: 1440, height: 900 });
await kit.getByRole("button", { name: "About Kit" }).first().click();
await kit.waitForTimeout(1500);
await choose("Standing work");
await whole("day-agents", 900);
await open({ width: 1440, height: 900 });
await go("Workflows");
await choose("Close the loop");
await kit.mouse.move(900, 600);
await kit.mouse.wheel(0, 700);
await whole("day-workflows", 900);
await open({ width: 1440, height: 900 });
await go("Apps");
await choose("Feedback loop");
await kit.waitForTimeout(2500);
await whole("day-apps", 900);
await open({ width: 1440, height: 900 });
await go("Tables");
await choose("Feedback Themes");
await whole("day-tables", 900);
await open({ width: 1440, height: 900 });
await go("Pages");
await choose("Feedback report, week 40");
await whole("day-pages", 900);

/* Who's in: the people dialog, on nothing. */
await open({ width: 1440, height: 900 });
await step(kit, 1);
{
    const alone = await kit.addStyleTag({ content: DIALOG_ALONE });
    await write(await kit.screenshot({ type: "png", clip: await around(kit, ['[role="dialog"]']), omitBackground: true }), "day-people");
    await alone.evaluate((node) => node.remove());
}

/* What it learned, with who taught each rule: the Feedback rules note. */
await step(kit, 3);
await kit.locator(".notes__row").first().click();
await kit.waitForTimeout(3500);
await write(await kit.screenshot({ type: "png", clip: { x: 340, y: 125, width: 760, height: 255 } }), "day-rules");

/* The product pages: the main panel of a sample teammate's space, at the
   size a section column shows it, so its words stay readable. Written to
   public/product as day-*.webp; the words beside them are in
   src/marketing/product-pages.ts. */
const productOut = path.join(out, "../product");
async function openAs(who) {
    await open({ width: 1200, height: 800 });
    if (who) {
        await kit.locator(`button[aria-label^="${who}"]`).first().click();
        await kit.waitForTimeout(2500);
    }
}
async function panel(name, tallest = Infinity) {
    await kit.waitForTimeout(2500);
    for (const frame of kit.frames()) await frame.addStyleTag({ content: QUIET }).catch(() => undefined);
    const top = await belowBanner(kit);
    /* The main panel only: its rounded card, without the sidebar beside it. */
    const box = await kit.evaluate((above) => {
        const crumbs = [...document.querySelectorAll("nav, header, div")].find((node) => /^Acme\s*\//.test(node.textContent.trim()) && node.getBoundingClientRect().left > 200);
        let card = crumbs;
        while (card && card.getBoundingClientRect().height < window.innerHeight - above - 40) card = card.parentElement;
        const rect = (card ?? document.body).getBoundingClientRect();
        return { x: Math.floor(rect.left), y: Math.floor(Math.max(rect.top, above)), width: Math.ceil(rect.width), height: Math.ceil(Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, above)) };
    }, top);
    box.height = Math.min(box.height, tallest);
    const png = await kit.screenshot({ type: "png", clip: box });
    await sharp(png).webp({ quality: 86 }).toFile(path.join(productOut, "day-" + name + ".webp"));
    console.log("wrote product", name);
}
async function chooseLike(text) {
    const found = await kit.evaluate((wanted) => {
        const node = [...document.querySelectorAll("a, button, [role=row], tr, li")].find((el) => el.textContent.trim().startsWith(wanted) && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().top > 120);
        if (!node) return false;
        node.click();
        return true;
    }, text);
    if (!found) throw new Error("Nothing on screen starts with " + text);
    await kit.waitForTimeout(3000);
}

await openAs("Scout");
await chooseLike("Why are our trials stalling");
await panel("scout-chat");
await openAs("Remy");
await go("Tables");
await panel("remy-tables", 320);
await chooseLike("Follow Ups");
await panel("remy-followups");
await openAs("Remy");
await go("Workflows");
await panel("remy-workflows-empty");
await openAs("Remy");
await go("Apps");
await choose("Deal desk");
await panel("remy-dealdesk");
await openAs("June");
await go("Workflows");
await choose("First import check");
await panel("june-workflow");
await kit.mouse.move(800, 500);
await kit.mouse.wheel(0, 650);
await panel("june-workflow-steps");
await openAs("June");
await go("Workflows");
await choose("First import check");
await choose("Waiting on a person");
await panel("june-run-waiting");
await openAs("June");
await go("Apps");
await choose("Customer launchpad");
await panel("june-launchpad");
await openAs();
await go("Apps");
await choose("Feedback loop");
await panel("kit-app");
await openAs();
await kit.getByRole("button", { name: "About Kit" }).first().click();
await panel("kit-about");

await light.close();
await browser.close();
