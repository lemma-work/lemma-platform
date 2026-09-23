import test from "node:test";
import assert from "node:assert/strict";
import { instructionWords, readFrontmatter, splitDescription } from "../src/skills/skill-frontmatter.ts";
import { cardFor, sayUnloadable, skillFoldersFrom, readableName } from "../src/skills/skills.ts";

/** The parser is a copy of `_parse_frontmatter` in the backend's
 *  `agent/tools/skills/skill_loader.py`. Every case here is a rule that file
 *  raises on — which means the teammate cannot load the skill at all, so
 *  getting one of them wrong here draws a confident card for something that
 *  does not exist. */

const good = [
    "---",
    "name: weekly-review",
    "description: Write the Friday note.",
    "---",
    "",
    "# Weekly review",
    "",
    "Run it on Friday.",
].join("\n");

test("a well-formed SKILL.md gives up its name and description", () => {
    const front = readFrontmatter(good, "weekly-review");

    assert.equal(front.name, "weekly-review");
    assert.equal(front.description, "Write the Friday note.");
    assert.equal(front.problem, null);
});

test("a file with no frontmatter is not a skill, and says which problem it has", () => {
    // Not "missing name": the frontmatter is not there at all, and being told
    // to add a name sends somebody looking in a block that does not exist.
    const front = readFrontmatter("# Weekly review\n\nRun it on Friday.\n", "weekly-review");

    assert.equal(front.name, "");
    assert.equal(front.description, "");
    assert.match(front.problem ?? "", /does not open with/);
});

test("frontmatter that is opened and never closed is refused", () => {
    const front = readFrontmatter("---\nname: x\ndescription: y\n\n# Heading\n", "x");

    assert.match(front.problem ?? "", /never closed/);
});

test("a missing name is refused, and what was read still comes back", () => {
    const front = readFrontmatter("---\ndescription: Does a thing.\n---\n\nBody.\n", "quiet-hours");

    assert.equal(front.name, "");
    assert.equal(front.description, "Does a thing.", "the description is still worth showing");
    assert.match(front.problem ?? "", /no `name`/);
});

test("a missing description is refused, and the name still comes back", () => {
    const front = readFrontmatter("---\nname: quiet-hours\n---\n\nBody.\n", "quiet-hours");

    assert.equal(front.name, "quiet-hours");
    assert.match(front.problem ?? "", /no `description`/);
});

test("a colon inside a value keeps the whole value", () => {
    // `split(":", 1)` on the backend, which is one split and not a tokenise.
    // Splitting on every colon is how "Use this when: the report is late"
    // becomes "Use this when".
    const front = readFrontmatter(
        "---\nname: late-report\ndescription: Use this when: the report is late, or 12:30 has passed.\n---\n",
        "late-report",
    );

    assert.equal(front.description, "Use this when: the report is late, or 12:30 has passed.");
    assert.equal(front.problem, null);
});

test("CRLF line endings are read, and reported as the reason the teammate cannot load it", () => {
    // The loader tests `content.startswith("---\n")` against bytes it decoded
    // itself, with no newline translation — so a pod's SKILL.md saved on
    // Windows raises before anything else is looked at. The fields are still
    // parsed here, because the person who has to fix it needs to see which
    // skill it is.
    const front = readFrontmatter(
        "---\r\nname: weekly-review\r\ndescription: Write the Friday note.\r\n---\r\n\r\n# Weekly review\r\n",
        "weekly-review",
    );

    assert.equal(front.name, "weekly-review");
    assert.equal(front.description, "Write the Friday note.");
    assert.match(front.problem ?? "", /Windows line endings/);
});

test("quotes around a value are stripped, and comments and indented lines are skipped", () => {
    const front = readFrontmatter(
        [
            "---",
            "# a comment the loader ignores",
            'name: "competitor-watch"',
            "description: 'Check the five named competitors.'",
            "  nested: not read",
            "not a pair",
            "---",
        ].join("\n"),
        "competitor-watch",
    );

    assert.equal(front.name, "competitor-watch");
    assert.equal(front.description, "Check the five named competitors.");
    assert.equal(front.problem, null);
});

test("a name that is not the loader's shape is refused", () => {
    assert.match(readFrontmatter("---\nname: Weekly Review\ndescription: x\n---\n").problem ?? "", /lowercase/);
    assert.match(readFrontmatter("---\nname: weekly--review\ndescription: x\n---\n").problem ?? "", /lowercase/);
    assert.match(readFrontmatter("---\nname: -weekly\ndescription: x\n---\n").problem ?? "", /lowercase/);
});

test("frontmatter renamed without its folder is refused, and both names are said", () => {
    const front = readFrontmatter("---\nname: launch-check\ndescription: x\n---\n", "launch-checklist");

    assert.match(front.problem ?? "", /launch-check/);
    assert.match(front.problem ?? "", /launch-checklist/);
});

test("the folder is only checked when there is one to check against", () => {
    assert.equal(readFrontmatter("---\nname: anything\ndescription: x\n---\n").problem, null);
});

test("words are counted over the instruction, not the frontmatter or its markup", () => {
    // "Weekly review" and "Run it on Friday." The lone `#` is markup: counting
    // it would make the rank a measure of how many headings somebody used.
    assert.equal(instructionWords(good), 6);
    assert.equal(instructionWords("---\nname: a\ndescription: b\n---\n"), 0);
    assert.equal(instructionWords("no frontmatter at all"), 4);
    assert.equal(instructionWords("## A\n\n- one\n- two\n"), 3);
    assert.equal(instructionWords(""), 0);
});

/* ── the listing ─────────────────────────────────────────────────────── */

test("only folders directly under /skills are skills", () => {
    const found = skillFoldersFrom([
        { name: "weekly-review", kind: "folder", path: "/skills/weekly-review", updated: "2026-09-15T09:20:00Z" },
        { name: "notes.md", kind: "file", path: "/skills/notes.md", updated: "" },
        { name: "scripts", kind: "folder", path: "/skills/weekly-review/scripts", updated: "" },
        { name: "documents", kind: "folder", path: "/documents", updated: "" },
        { name: "skills", kind: "folder", path: "/skills", updated: "" },
    ]);

    assert.deepEqual(found.map((one) => one.folder), ["weekly-review"]);
    assert.equal(found[0].file, "/skills/weekly-review/SKILL.md");
    assert.equal(found[0].dir, "/skills/weekly-review");
});

test("a listing that is not one produces no skills", () => {
    assert.deepEqual(skillFoldersFrom([]), []);
    assert.deepEqual(skillFoldersFrom([{}, { kind: "folder" }, { kind: "folder", path: "" }]), []);
});

test("the same folder listed twice is one skill", () => {
    const found = skillFoldersFrom([
        { kind: "folder", path: "/skills/one" },
        { kind: "folder", path: "/skills/one/" },
    ]);

    assert.equal(found.length, 1);
});

/* ── the card ────────────────────────────────────────────────────────── */

const folder = { folder: "weekly-review", dir: "/skills/weekly-review", file: "/skills/weekly-review/SKILL.md", updated: "" };

test("a readable skill becomes a card with its frontmatter name on it", () => {
    const card = cardFor(folder, { text: good });

    assert.equal(card.title, "Weekly review");
    assert.equal(card.description, "Write the Friday note.");
    assert.equal(card.problem, null);
    assert.equal(card.words, 6, "its rank, which is the one number a skill honestly has");
});

test("a skill whose SKILL.md cannot be read is still a card, named by its folder", () => {
    // It must not vanish. A skill missing because one request failed and a
    // skill nobody wrote look identical, and only one of them is fixable.
    const card = cardFor(folder, { failure: "Its SKILL.md could not be read." });

    assert.equal(card.title, "Weekly review");
    assert.equal(card.words, 0, "no rank, because there is nothing to rank");
    assert.equal(card.problem, "Its SKILL.md could not be read.");
    assert.equal(card.read, false, "so the card does not report on frontmatter nobody has seen");
});

test("a skill renamed in its frontmatter is titled by what is actually on disk", () => {
    // The folder is the thing somebody has to go and rename. Printing the
    // frontmatter's name over a flag saying the folder disagrees leaves the
    // reader holding two names and no path.
    const card = cardFor(folder, { text: "---\nname: weekly\ndescription: x\n---\n" });

    assert.equal(card.title, "Weekly review");
    assert.equal(card.read, true, "it was read; it just will not load");
    assert.match(card.problem ?? "", /weekly/);
});

test("a skill with no frontmatter name falls back to its folder rather than to nothing", () => {
    const card = cardFor(folder, { text: "just a body\n" });

    assert.equal(card.title, "Weekly review");
    assert.ok(card.problem);
});

test("the tally under the deck counts only what cannot be loaded", () => {
    const ok = cardFor(folder, { text: good });
    const bad = cardFor(folder, { failure: "nope" });

    assert.equal(sayUnloadable([ok, ok]), null, "nothing is said when nothing is wrong");
    assert.match(sayUnloadable([ok, bad]) ?? "", /^One of these/);
    assert.match(sayUnloadable([ok, bad, bad]) ?? "", /^2 of these/);
    assert.match(sayUnloadable([bad, bad]) ?? "", /^None of these/);
    assert.match(sayUnloadable([bad]) ?? "", /^This skill/);
});

test("a description splits into what it does and when to reach for it", () => {
    // The convention every shipped skill follows: what it does, then its
    // trigger. There is no `needs`/`produces` in a SKILL.md — only `name` and
    // `description` — so this is the honest source for a second row.
    const split = splitDescription(
        "Run rigorous, source-backed research in an existing Lemma pod. Use for investigations, literature or market scans, and fact-checking.",
    );

    assert.equal(split.does, "Run rigorous, source-backed research in an existing Lemma pod.");
    assert.equal(split.useWhen, "Investigations, literature or market scans, and fact-checking.");
});

test("an exclusion is never filed as a trigger", () => {
    // `lemma-widget` says "Use an app, not a widget, when the UI needs React".
    // Splitting on a bare "Use … when" would put that under "use it when" and
    // tell somebody to reach for the widget skill exactly when they should
    // not. The lead-in has to be the verb followed straight by when/for.
    const split = splitDescription(
        "Create lightweight inline Lemma widgets for conversations. Use an app, not a widget, when the UI needs React, routing, or substantial state.",
    );

    assert.equal(split.useWhen, null);
    assert.match(split.does, /^Create lightweight/);
    assert.match(split.does, /not a widget/, "the whole description survives rather than being cut at a false trigger");
});

test("a description that is nothing but its trigger stays whole", () => {
    // `browser` opens with "Use this skill when opening…", so splitting it
    // leaves an empty first row.
    const split = splitDescription("Use this skill when opening, inspecting, or debugging web pages.");

    assert.equal(split.useWhen, null);
    assert.match(split.does, /^Use this skill when opening/);
});

test("the three ways there is nothing to split", () => {
    assert.deepEqual(splitDescription(""), { does: "", useWhen: null });
    assert.deepEqual(splitDescription(null), { does: "", useWhen: null });
    assert.deepEqual(splitDescription(42), { does: "", useWhen: null });
    // Whitespace is normalised on the way through: a description wrapped
    // across lines in the frontmatter is one sentence to a reader.
    assert.equal(splitDescription("  Does   a\n  thing. ").does, "Does a thing.");
});

test("both 'use when' and 'use for' open a trigger, in any case", () => {
    assert.equal(splitDescription("Does a thing. USE WHEN it is needed.").useWhen, "It is needed.");
    assert.equal(splitDescription("Does a thing. Use for one job.").useWhen, "One job.");
    assert.equal(splitDescription("Does a thing. Use this skill for one job.").useWhen, "One job.");
    // Not a trigger: the word "use" inside a sentence.
    assert.equal(splitDescription("Does a thing you can use when idle.").useWhen, null);
});

test("a skill is named the way a folder is, and shown the way a name is", () => {
    // Skills are directories, so their names arrive as slugs. A row of those
    // reads as filenames somebody forgot to set in type.
    assert.equal(readableName("lemma-app-design"), "Lemma app design");
    assert.equal(readableName("liteparse_documents"), "Liteparse documents");
    assert.equal(readableName("browser"), "Browser");
    assert.equal(readableName("lemma--skill__creator"), "Lemma skill creator");
    // Nothing to work with is left alone rather than turned into an empty
    // heading.
    assert.equal(readableName(""), "");
    assert.equal(readableName("   "), "   ");
});

test("the folder keeps its own spelling, because a path is built from it", () => {
    // The display name must never become the identifier: the colour and mark
    // are seeded on `folder`, and the file is read from it. A card that showed
    // "Lemma app design" and then looked for a folder of that name would send
    // somebody to a file that is not there.
    const card = cardFor(
        { folder: "lemma-app-design", dir: "/skills/lemma-app-design", file: "/skills/lemma-app-design/SKILL.md", updated: "" },
        { text: "---\nname: lemma-app-design\ndescription: Designs things.\n---\nBody here.", failure: null },
    );

    assert.equal(card.title, "Lemma app design");
    assert.equal(card.folder, "lemma-app-design");
    assert.equal(card.file, "/skills/lemma-app-design/SKILL.md");
});
