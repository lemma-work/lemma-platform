import { readFile, readdir } from "node:fs/promises";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..");
const SEARCHED = "src/styles";

/** The palette itself. A file whose job is to name colours is the one place a
 *  literal colour belongs. */
const PALETTE = new Set(["tokens.css", "accents.css"]);

/** Colour set outside the palette, and the one reason each is there.
 *
 *  A pattern plus a reason, like the naming check's allowances, and under the
 *  same rule: an entry that stops matching is one nobody is reading any more,
 *  so it has to go rather than rot.
 */
const ALLOWED = [
    {
        file: "computer.css",
        reason: "the sandbox screen is a device — dark in both appearances, with its own micro-palette",
    },
    {
        file: "shell.css",
        line: /color-mix\(|mask-image|linear-gradient|#fffefa|#1b1916|#e1d3c9/,
        reason: "badge material: the strap, the metal clip, the die-cut well — printed stock, which does not move with the theme",
    },
    {
        file: "shell.css",
        line: /font-weight: 700;/,
        reason: "the badge wordmark, stamped rather than set — the one documented exception, scoped to .idcard__name",
    },
    {
        file: "shell.css",
        line: /data:image\/svg\+xml/,
        reason: "the noise plate over the badge field — an inline SVG whose fill is the grain, not a colour",
    },
    {
        file: "refinement.css",
        line: /\.shared__frame|\.shared__pdf|\.fileview__frame/,
        reason: "the backdrop for somebody else's document, authored against a white page",
    },
    {
        file: "refinement.css",
        line: /\.verify__qr-paper/,
        reason: "a QR code is read by a camera, so it is fixed ink on fixed paper in both themes or it does not scan",
    },
];

/** Three- to eight-digit hex, plus the two keywords that slip past a hex
 *  search. `white` and `black` are matched only as whole words, so
 *  `white-space` and `--line-2` are not colours. */
const COLOUR = /#[0-9a-fA-F]{3,8}\b|(?<![-\w])(?:white|black)(?![-\w])/;

/** DESIGN.md: weight never exceeds 500. Hierarchy is size and the space above
 *  it, and a 600 anywhere is the start of a second scale. */
const HEAVY = /font-weight:\s*(?:[6-9]00|bold(?:er)?)\b|font:\s*(?:[6-9]00|bold)\b/;

/** A fill taking white ink rather than the ink paired with it. The one that
 *  keeps coming back, because it looks right in whichever appearance the
 *  person writing it happened to have open. */
const UNPAIRED_INK = /color:\s*(?:#fff(?:fff)?|white)\b/;

/** Comments carry the reasoning, and the reasoning quotes the values it is
 *  about — this file's own rules name `#fff` several times. Blanked rather
 *  than dropped so line numbers survive. */
function withoutComments(text) {
    return text.replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, " "));
}

const offences = [];
const used = new Set();

function allowed(file, line) {
    for (const [index, rule] of ALLOWED.entries()) {
        if (rule.file !== file) continue;
        if (rule.line && !rule.line.test(line)) continue;
        used.add(index);
        return true;
    }
    return false;
}

for (const entry of await readdir(path.join(ROOT, SEARCHED))) {
    if (!entry.endsWith(".css")) continue;
    const relative = `${SEARCHED}/${entry}`;
    const code = withoutComments(await readFile(path.join(ROOT, relative), "utf8"));
    const palette = PALETTE.has(entry);

    code.split("\n").forEach((line, index) => {
        const at = { file: relative, line: index + 1, text: line.trim().slice(0, 110) };
        if (HEAVY.test(line) && !allowed(entry, line)) {
            offences.push({ ...at, note: "font weight above 500 — hierarchy is size and space, never bold" });
        }
        if (palette) return;
        if (UNPAIRED_INK.test(line) && !allowed(entry, line)) {
            offences.push({ ...at, note: "white ink — a fill takes the ink paired with it: --on-accent, --on-ok, --on-bad, --field-ink" });
        } else if (COLOUR.test(line) && !allowed(entry, line)) {
            offences.push({ ...at, note: "literal colour — use a token, or add an allowance with a reason in this file" });
        }
    });
}

/* An allowance nothing matched is one describing code that has moved on. The
   same rule the naming check applies to its own list. */
const stale = ALLOWED.filter((_, index) => !used.has(index));

if (offences.length === 0 && stale.length === 0) {
    console.log(`design: clean (${ALLOWED.length} allowed)`);
    process.exit(0);
}

for (const { file, line, note, text } of offences) {
    console.error(`${file}:${line}  ${note}\n    ${text}`);
}
for (const rule of stale) {
    console.error(`\nNothing in ${rule.file} matches the allowance for "${rule.reason}". Drop it.`);
}
if (offences.length) {
    console.error(
        `\nDESIGN.md has the reasoning. The short version: colour lives in` +
        `\n\`tokens.css\` and \`accents.css\`, a fill carries the ink paired with it,` +
        `\nand weight never exceeds 500.`,
    );
}
process.exit(1);
