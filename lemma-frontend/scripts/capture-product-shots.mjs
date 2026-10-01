/* Real screens for the product pages (/product/*), from the sample
   teammates' spaces at /demo/landing. Run against a dev server:

     node scripts/capture-product-shots.mjs [origin] [out-dir]

   Each page shows a different teammate's job: Scout's research for pages,
   Remy's pipeline for tables and memory, June's imports for workflows.
   Most moving pictures on those pages are coded vignettes
   (src/app/(marketing)/vignettes); this takes the real screens and the one
   recording they sit beside. The
   spaces are in src/marketing/sample-spaces.ts and
   src/marketing/preview-fixtures.ts. Stills are taken at 2x and written as
   WebP; the short loops are recorded in a real browser session, with a
   pointer drawn where the mouse is, and written as MP4 and WebM with a
   poster. When the product changes, run this again rather than editing the
   images. Needs ffmpeg on PATH for the loops. */
import { chromium } from "playwright";
import sharp from "sharp";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";

const run = promisify(execFile);
const origin = process.argv[2] ?? "http://localhost:3000";
const out = path.resolve(process.argv[3] ?? path.join(import.meta.dirname, "../public/product"));

const WIDTH = 1440;
const HEIGHT = 900;
// The dev server's own badge, which no visitor sees.
const QUIET = "nextjs-portal { display: none !important }";
/** A dialog alone, on nothing, so the picture has clear corners. */
const DIALOG_ALONE = "body > *:not(.modal) { visibility: hidden !important } .modal { background: transparent !important; backdrop-filter: none !important } html, body { background: transparent !important }";
/** A pointer for the recordings, which have none of their own. */
const POINTER = `(() => {
    const dot = document.createElement("div");
    dot.style.cssText = "position:fixed;z-index:2147483647;left:-40px;top:-40px;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;background:rgb(20 20 19/.28);border:2px solid #fff;box-shadow:0 2px 8px rgb(0 0 0/.25);pointer-events:none;transition:transform .12s";
    addEventListener("mousemove", (e) => { dot.style.left = e.clientX + "px"; dot.style.top = e.clientY + "px"; }, true);
    addEventListener("mousedown", () => { dot.style.transform = "scale(.7)"; }, true);
    addEventListener("mouseup", () => { dot.style.transform = ""; }, true);
    addEventListener("DOMContentLoaded", () => document.body.appendChild(dot));
})();`;

/** Where the app starts: below the strip that labels the sample, which is
 *  for visitors of the live sample, not for a picture of it. */
async function belowBanner(page) {
    return page.evaluate(() => {
        const banner = [...document.body.querySelectorAll("*")].find((node) => /sample workspace/i.test(node.textContent ?? "") && node.getBoundingClientRect().height < 80 && node.getBoundingClientRect().top < 10);
        return banner ? Math.ceil(banner.getBoundingClientRect().bottom) : 0;
    });
}

/** The box around every element matching the selectors, plus a margin. */
async function around(page, selectors, pad = 0) {
    const box = await page.evaluate(([list, margin]) => {
        const boxes = list.flatMap((selector) => [...document.querySelectorAll(selector)]).map((node) => node.getBoundingClientRect()).filter((b) => b.width > 0);
        if (boxes.length === 0) return null;
        const left = Math.max(0, Math.min(...boxes.map((b) => b.left)) - margin);
        const top = Math.max(0, Math.min(...boxes.map((b) => b.top)) - margin);
        const right = Math.min(Math.max(...boxes.map((b) => b.right)) + margin, innerWidth);
        const bottom = Math.min(Math.max(...boxes.map((b) => b.bottom)) + margin, innerHeight);
        return { x: left, y: top, width: right - left, height: bottom - top };
    }, [selectors, pad]);
    if (!box) throw new Error("Nothing on screen matches " + selectors.join(", "));
    return box;
}

/** The work area: everything right of the sidebar and below the banner. */
async function paneBox(page) {
    const top = await belowBanner(page);
    const left = await page.evaluate(() => {
        const edges = [".side", ".trail"].map((s) => document.querySelector(s)?.getBoundingClientRect()).filter((b) => b && b.width > 0);
        return Math.max(0, ...edges.map((b) => Math.ceil(b.right)));
    });
    return { x: left, y: top, width: WIDTH - left, height: HEIGHT - top };
}

async function write(png, name, quality = 88) {
    await sharp(png).webp({ quality }).toFile(path.join(out, name + ".webp"));
    console.log("wrote", name);
}

/** The work area alone: the picture at the top of each product page. */
async function pane(page, name) {
    await write(await page.screenshot({ type: "png", clip: await paneBox(page) }), name, 86);
}

async function part(page, name, selectors, pad = 0) {
    await write(await page.screenshot({ type: "png", clip: await around(page, selectors, pad) }), name);
}

async function step(page, index) {
    await page.evaluate((to) => window.postMessage({ type: "lemma-tour:step", step: to }, location.origin), index);
    await page.waitForTimeout(3500);
}

async function teammate(page, name) {
    await page.click(`.trail__mate[aria-label^="${name}"]`);
    await page.waitForTimeout(2200);
}

async function open(page, place) {
    await page.click(`.side__item[title="${place}"]`);
    await page.waitForTimeout(1800);
}

async function row(page, list, text) {
    await page.locator(list).getByText(text, { exact: true }).first().click();
    await page.waitForTimeout(3000);
}

async function ready(page) {
    await page.goto(origin + "/demo/landing", { waitUntil: "networkidle" });
    await page.addStyleTag({ content: QUIET });
    await page.waitForTimeout(4000);
}

/** Moves the pointer the way a hand does, so the recordings read as use. */
async function glide(page, locator) {
    const box = await locator.boundingBox();
    if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 24 });
    await page.waitForTimeout(250);
}

async function duration(file) {
    const { stdout } = await run("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", file]);
    return Number(stdout.trim()) || 0;
}

/** Records `act` in a fresh browser and writes `<name>.mp4`, `.webm` and a
 *  poster, cropped to the work area and trimmed to the act itself. */
async function loop(browser, name, setUp, act) {
    const folder = await mkdtemp(path.join(tmpdir(), "lemma-loop-"));
    const context = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT }, recordVideo: { dir: folder, size: { width: WIDTH, height: HEIGHT } } });
    await context.addInitScript(POINTER);
    const page = await context.newPage();
    await ready(page);
    await setUp(page);
    const box = await paneBox(page);
    const started = Date.now();
    await act(page);
    await page.waitForTimeout(800);
    const seconds = (Date.now() - started) / 1000;
    await context.close();
    const [video] = (await readdir(folder)).filter((file) => file.endsWith(".webm"));
    const source = path.join(folder, video);
    const from = String(Math.max(0, (await duration(source)) - seconds));
    const crop = `crop=${box.width}:${box.height}:${box.x}:${box.y}`;
    await run("ffmpeg", ["-y", "-ss", from, "-i", source, "-vf", crop, "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", "-movflags", "+faststart", path.join(out, name + ".mp4")]);
    await run("ffmpeg", ["-y", "-ss", from, "-i", source, "-vf", crop, "-an", "-c:v", "libvpx-vp9", "-crf", "34", "-b:v", "0", path.join(out, name + ".webm")]);
    await run("ffmpeg", ["-y", "-ss", from, "-i", source, "-vf", crop, "-frames:v", "1", path.join(folder, "poster.png")]);
    await sharp(path.join(folder, "poster.png")).webp({ quality: 82 }).toFile(path.join(out, name + "-poster.webp"));
    await rm(folder, { recursive: true, force: true });
    console.log("wrote", name, "loop,", seconds.toFixed(1) + "s");
}

await mkdir(out, { recursive: true });
const browser = await chromium.launch();
const only = process.env.ONLY_LOOPS === "1";
const page = await browser.newPage({ viewport: { width: WIDTH, height: HEIGHT }, deviceScaleFactor: 2 });
if (!only) await stills(page);
await page.close();

async function stills(page) {
await ready(page);

/* ── Pages: Scout's research ────────────────────────────────────────── */
await teammate(page, "Scout");
await open(page, "Pages");
await row(page, ".all", "Why trials stall");
await pane(page, "pages-hero");
// The view block alone: no sliver of the heading above it.
await part(page, "pages-view", [".pblock--view"], 2);

/* ── Tables: Remy's pipeline ────────────────────────────────────────── */
await teammate(page, "Remy");
await open(page, "Tables");
await row(page, ".all", "Deals");
await pane(page, "tables-hero");

/* ── Workflows: June's imports ──────────────────────────────────────── */
await teammate(page, "June");
await open(page, "Workflows");
// The tabs and the three workflows, not the empty page under them.
await part(page, "workflows-list", [".wfindex__tabs", ".wfindex"], 20);
await row(page, ".wfindex__row", "First import check");
// How it starts and its runs, side by side, big enough to read.
await part(page, "workflows-starts", [".wfstart", ".wfdock"], 16);
await row(page, ".wfdock", "Waiting on a person");
// The run: its title, the waiting banner with the form, the first steps.
{
    const box = await around(page, [".runpage__head", ".runpage .standing"], 20);
    await write(await page.screenshot({ type: "png", clip: { ...box, height: Math.min(box.height + 230, HEIGHT - box.y) } }), "workflows-run");
}

/* ── Apps ───────────────────────────────────────────────────────────── */
await open(page, "Apps");
await page.locator(".all").getByText("Sales", { exact: true }).first().click();
await page.waitForTimeout(1500);
// The ideas on their own, so a card’s title reads at section size.
await part(page, "apps-catalog", [".app-catalog"], 16);

/* ── Memory: what Remy keeps ────────────────────────────────────────── */
await teammate(page, "Remy");
await page.click(".side__mate");
await page.waitForTimeout(2500);
await page.locator('[data-about="memory"]').scrollIntoViewIfNeeded();
await page.waitForTimeout(800);
await part(page, "memory-shared", ['[data-about="memory"]'], 12);
await page.locator('[data-about="memory"] button', { hasText: "Personal" }).first().click();
await page.waitForTimeout(1200);
await part(page, "memory-personal", ['[data-about="memory"]'], 12);

/* The moment it learns, in Remy's own conversation: the correction, the
   reply, and the "noted this" line the write under it draws. */
await page.locator(".side").getByText("Who’s waiting on us?", { exact: true }).first().click();
await page.waitForTimeout(3000);
await page.locator(".noted").last().scrollIntoViewIfNeeded();
await page.waitForTimeout(600);
{
    const clip = await page.evaluate(() => {
        const asks = [...document.querySelectorAll(".msg--you")];
        const notes = [...document.querySelectorAll(".noted")];
        const top = asks[asks.length - 1]?.getBoundingClientRect();
        const bottom = notes[notes.length - 1]?.getBoundingClientRect();
        const column = document.querySelector(".convo")?.getBoundingClientRect();
        if (!top || !bottom || !column) return null;
        return { x: Math.max(0, column.left - 28), y: top.top - 24, width: column.width + 44, height: bottom.bottom - top.top + 48 };
    });
    if (!clip) throw new Error("No correction and noted line in Remy's conversation");
    await write(await page.screenshot({ type: "png", clip }), "memory-noted", 90);
}

/* ── Channels ───────────────────────────────────────────────────────── */
await step(page, 4);
{
    const alone = await page.addStyleTag({ content: DIALOG_ALONE });
    await write(await page.screenshot({ type: "png", clip: await around(page, ['[role="dialog"]']), omitBackground: true }), "channels-reach");
    await alone.evaluate((node) => node.remove());
}
}

/* ── Loops ──────────────────────────────────────────────────────────── */

// June's import waits on Dev; opening the run shows exactly where.
await loop(browser, "workflows-loop", async (p) => {
    await teammate(p, "June");
    await open(p, "Workflows");
}, async (p) => {
    const flow = p.locator(".wfindex__row").getByText("First import check", { exact: true }).first();
    await glide(p, flow);
    await flow.click();
    await p.waitForTimeout(2000);
    const waiting = p.locator(".wfdock").getByText("Waiting on a person", { exact: true }).first();
    await glide(p, waiting);
    await waiting.click();
    await p.waitForTimeout(2200);
    const tick = p.locator(".runpage input[type=checkbox]").first();
    await glide(p, tick);
    await tick.check().catch(() => undefined);
    await p.waitForTimeout(1500);
});

await browser.close();
