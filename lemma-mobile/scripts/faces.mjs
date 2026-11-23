// Draws each Lemma's face the way the web draws it: the character's rig
// (`lemma-frontend/src/app/characters/`), at rest, on a transparent ground.
// The web animates that rig wherever a face is 30px or more
// (`shell/character-puppet.tsx`); the phone shows it still, from these
// files. Run `node scripts/faces.mjs` from lemma-mobile/ after the web's
// characters change; the WebP files in assets/rig/ are committed.
//
// Needs `npm ci` in lemma-frontend (React, and Playwright with its Chromium)
// and in lemma-typescript (esbuild).
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const PHONE = join(dirname(fileURLToPath(import.meta.url)), "..");
const WEB = join(PHONE, "..", "lemma-frontend");
const OUT = join(PHONE, "assets", "rig");
/** Drawn at most 135pt wide on a 3x screen. */
const PIXELS = 512;
const QUALITY = 0.9;

/** `CHARACTERS` in the web's `shell/cast.ts`. */
const CAST = [
    "arch", "bean", "bloom", "bolt", "cloud", "coil", "cube", "frame", "gem", "grid", "kite", "knot",
    "loop", "moon", "peak", "pleat", "prism", "ring", "spark", "spool", "sprout", "stack", "wave", "wedge",
];

/** One character's rig, chosen as `CharacterPuppet` chooses it, paused:
 *  the still pose, no greeting, nothing mid-motion. */
const ENTRY = `
import { createRoot } from "react-dom/client";
import { ExtendedPuppet } from "./extended-puppet";
import { CastPuppet } from "./cast-puppet";
import { LoopPuppet } from "./loop/loop-puppet";
const character = new URLSearchParams(location.search).get("c");
const common = { mood: "idle", greeting: 0, paused: true, pixels: ${PIXELS} };
const rig = character === "loop" ? <LoopPuppet {...common} />
    : character === "pleat" || character === "frame" ? <CastPuppet character={character} {...common} />
    : <ExtendedPuppet character={character} {...common} />;
createRoot(document.getElementById("root")).render(
    <div id="rig" style={{ position: "relative", width: ${PIXELS}, height: ${PIXELS} }}>{rig}</div>,
);
`;

const esbuild = createRequire(join(PHONE, "..", "lemma-typescript", "package.json"))("esbuild");
const { chromium } = createRequire(join(WEB, "package.json"))("playwright");

const built = await esbuild.build({
    stdin: { contents: ENTRY, loader: "tsx", resolveDir: join(WEB, "src", "app", "characters") },
    bundle: true,
    write: false,
    outdir: "out",
    format: "esm",
    jsx: "automatic",
    define: { "process.env.NODE_ENV": '"production"' },
    logLevel: "error",
});
const code = built.outputFiles.find((file) => file.path.endsWith(".js")).text;
const css = built.outputFiles.find((file) => file.path.endsWith(".css"))?.text ?? "";
const page = `<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;background:transparent}${css}</style></head>
<body><div id="root"></div><script type="module">${code}</script></body></html>`;

mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const tab = await browser.newPage({ viewport: { width: PIXELS, height: PIXELS }, deviceScaleFactor: 1 });
/* The rigs ask for their layers at the web's own paths. */
await tab.route("**/*", (route) => {
    const { pathname } = new URL(route.request().url());
    return pathname.startsWith("/teammates/")
        ? route.fulfill({ body: readFileSync(join(WEB, "public", pathname)), contentType: "image/webp" })
        : route.fulfill({ body: page, contentType: "text/html" });
});
for (const character of CAST) {
    await tab.goto(`http://faces.local/?c=${character}`);
    await tab.waitForSelector("#rig svg image");
    await tab.waitForLoadState("networkidle");
    /* A frame or two for the paused pose to be set. */
    await tab.waitForTimeout(300);
    const png = await tab.locator("#rig").screenshot({ omitBackground: true });
    const webp = await tab.evaluate(async ({ base64, quality }) => {
        const picture = new Image();
        picture.src = "data:image/png;base64," + base64;
        await picture.decode();
        const canvas = document.createElement("canvas");
        canvas.width = picture.width;
        canvas.height = picture.height;
        canvas.getContext("2d").drawImage(picture, 0, 0);
        return canvas.toDataURL("image/webp", quality).split(",")[1];
    }, { base64: png.toString("base64"), quality: QUALITY });
    writeFileSync(join(OUT, `${character}.webp`), Buffer.from(webp, "base64"));
    console.log(`assets/rig/${character}.webp`);
}
await browser.close();
