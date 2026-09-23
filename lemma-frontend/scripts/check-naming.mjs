import { readFile, readdir } from "node:fs/promises";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..");

const SEARCHED = ["src", "server", "tests", "scripts"];
const LOOSE_FILES = ["server.mjs", "README.md", "DESIGN.md", "Dockerfile", "next.config.ts"];
const EXTENSIONS = new Set([".ts", ".tsx", ".mjs", ".js", ".css", ".md", ".json", ".yml", ".yaml", ""]);

const ALLOWED = {
    "src/app/characters/loop/loop-scene.tsx": "Three.js RoomEnvironment is the upstream studio-lighting class, not a product entity",
    "src/session/storage.ts": "persisted browser preference compatibility",
    "tests/storage.test.ts": "persisted browser preference compatibility tests",
    "scripts/check-naming.mjs": "this file",

};

const WORD = /room/i;

async function* walk(dir) {
    let entries;
    try {
        entries = await readdir(path.join(ROOT, dir), { withFileTypes: true });
    } catch {
        return;
    }
    for (const entry of entries) {
        const relative = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
            yield* walk(relative);
        } else if (EXTENSIONS.has(path.extname(entry.name))) {
            yield relative;
        }
    }
}

async function collect() {
    const files = [];
    for (const dir of SEARCHED) for await (const file of walk(dir)) files.push(file);
    for (const file of LOOSE_FILES) files.push(file);
    return files;
}

const offences = [];
const allowedSeen = new Set();
const allowedRead = new Set();

for (const file of await collect()) {
    let contents;
    try {
        contents = await readFile(path.join(ROOT, file), "utf8");
    } catch {
        continue; // an optional file in LOOSE_FILES
    }
    if (file in ALLOWED) {
        allowedRead.add(file);
        /* Seen means the word is actually still in there. An allowance is
           stale when the file stopped saying it — not when the file was not
           read, which is a different thing entirely and depends on where this
           is running. */
        if (WORD.test(contents)) allowedSeen.add(file);
        continue;
    }
    contents.split("\n").forEach((line, index) => {
        if (WORD.test(line)) offences.push({ file, line: index + 1, text: line.trim().slice(0, 120) });
    });
}

/* An allowance for a file that no longer says the word is one nobody is
   reading. Judged only over files this run actually opened: the Docker build
   excludes `tests`, and reading "not present here" as "does not say it any
   more" failed the image build on a file that was sitting in the repository
   saying it the whole time. */
const stale = Object.keys(ALLOWED).filter(
    (file) => allowedRead.has(file) && !allowedSeen.has(file),
);

if (offences.length === 0 && stale.length === 0) {
    console.log(`naming: clean (${Object.keys(ALLOWED).length} allowed)`);
    process.exit(0);
}

for (const { file, line, text } of offences) {
    console.error(`${file}:${line}  ${text}`);
}
if (offences.length) {
    console.error(
        `\nNothing is called a room. A pod is a pod in the code and a teammate in the UI;` +
        `\nwhere the old word meant space, the app or the shell, say that instead.` +
        `\nIf one of these is genuinely about the old name, add it to ALLOWED in this file.`,
    );
}
for (const file of stale) {
    console.error(`\nALLOWED lists ${file}, which no longer says it. Drop the entry.`);
}
process.exit(1);
