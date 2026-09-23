import test from "node:test";
import assert from "node:assert/strict";
import {
    describeSave, editableKind, holdsMarkup, lockedBecause, sayLocked,
} from "../src/thread/document-save.ts";
import { joinFrontmatter, splitFrontmatter } from "../src/skills/skill-frontmatter.ts";

test("only markdown is editable, whatever else can be read as text", () => {
    // The editor rewrites the whole file in its own markdown dialect on every
    // save. Prose survives that; a JSON config does not, and it would happen on
    // a 700ms timer with nobody asked.
    assert.equal(editableKind("markdown"), true);
    for (const kind of ["text", "html", "pdf", "image", "video", "audio", "binary"] as const) {
        assert.equal(editableKind(kind), false, kind + " must not be editable");
    }
});

test("a document you only read says nothing about itself", () => {
    // "Saved" over a document nobody touched claims a change that never
    // happened, and a chip in the corner of every file is chrome.
    assert.equal(describeSave({ state: "idle", dirty: false }), null);
});

test("unsaved work says so before the tab is closed, not after", () => {
    assert.deepEqual(describeSave({ state: "idle", dirty: true }), { label: "Unsaved changes", tone: "quiet" });
    assert.deepEqual(describeSave({ state: "saving", dirty: true }), { label: "Saving…", tone: "quiet" });
    assert.deepEqual(describeSave({ state: "saved", dirty: false }), { label: "Saved", tone: "quiet" });
});

test("a failure outranks everything else and is the only coloured state", () => {
    // Typing through a failed save leaves it both dirty and failed. The failure
    // is the thing worth reading.
    assert.deepEqual(describeSave({ state: "failed", dirty: true }), { label: "Couldn’t save", tone: "bad" });
    assert.deepEqual(describeSave({ state: "failed", dirty: false }), { label: "Couldn’t save", tone: "bad" });
    for (const state of ["idle", "saving", "saved"] as const) {
        assert.notEqual(describeSave({ state, dirty: true })?.tone, "bad");
    }
});

test("a document with HTML in it is read rather than written in", () => {
    // The editor holds a document as a tree of the nodes it knows, and raw HTML
    // is not one of them: it goes in and does not come out. Losing an agent's
    // `<details>` block to one keystroke is worse than not being able to edit.
    assert.equal(holdsMarkup("<details><summary>Why</summary>Because.</details>"), true);
    assert.equal(holdsMarkup("# Report\n\nA line.\n\n<div style=\"color:red\">Late</div>\n"), true);
    assert.equal(holdsMarkup("Ends with a break<br/>here."), true);
    assert.equal(lockedBecause("<div>x</div>"), "markup");
});

test("prose about HTML is still prose", () => {
    // A check that refuses to let anybody edit a document *about* markup has
    // misread its own subject.
    assert.equal(holdsMarkup("Use `<div>` for a block.\n"), false);
    assert.equal(holdsMarkup("```html\n<div class=\"x\">hi</div>\n```\n\nThat is the shape.\n"), false);
    assert.equal(holdsMarkup("~~~\n<section>\n~~~\n"), false);
    assert.equal(lockedBecause("# Notes\n\n- one\n- two\n"), null);
});

test("an autolink is markdown, not markup", () => {
    // `<https://…>` survives the round trip. Treating it as HTML would make
    // every document that links to something read-only.
    assert.equal(holdsMarkup("See <https://example.com/a-page> for more.\n"), false);
    assert.equal(holdsMarkup("Mail <sam@example.com> about it.\n"), false);
});

test("each reason a document is locked has its own sentence", () => {
    // They send the reader to different places: one is a permission, the other
    // is this app admitting a limit.
    assert.notEqual(sayLocked("markup"), sayLocked("forbidden"));
    for (const locked of ["markup", "forbidden"] as const) {
        const said = sayLocked(locked);
        assert.match(said, /read-only/);
        assert.equal(said.split(". ").length, 1, "one sentence: " + said);
    }
});

test("frontmatter is held aside and comes back exactly as it was written", () => {
    // The editor never sees it: `---` renders as a rule and a setext heading,
    // and the round trip would write that back — which is how a SKILL.md stops
    // being a skill.
    const file = "---\nname: weekly-report\ndescription: \"Use when: it is Friday\"\n---\n\n# Friday\n\nDone.\n";
    const split = splitFrontmatter(file);

    assert.equal(split.front, "---\nname: weekly-report\ndescription: \"Use when: it is Friday\"\n---");
    assert.equal(split.body, "# Friday\n\nDone.\n");
    assert.equal(joinFrontmatter(split.front, split.body), file);
});

test("a document that merely opens with a rule keeps its first paragraph", () => {
    // An opening fence that never closes is not frontmatter. Treating it as one
    // would hide real content from the editor, which then writes the file back
    // without it.
    const file = "---\nA line under a rule, and no closing fence.\n";
    assert.deepEqual(splitFrontmatter(file), { front: null, body: file });
    assert.deepEqual(splitFrontmatter("# Plain\n\nNo frontmatter here.\n").front, null);
});

test("an edited body is rejoined with one blank line, whatever it arrived with", () => {
    const front = "---\nname: x\n---";
    assert.equal(joinFrontmatter(front, "New body.\n"), "---\nname: x\n---\n\nNew body.\n");
    assert.equal(joinFrontmatter(null, "New body.\n"), "New body.\n");
});

test("a file with Windows line endings keeps them in the block it did not edit", () => {
    // The block is held verbatim rather than normalised, so a save does not
    // silently rewrite bytes nobody touched.
    const split = splitFrontmatter("---\r\nname: x\r\n---\r\n\r\nBody.\r\n");
    assert.equal(split.front, "---\r\nname: x\r\n---\r");
    assert.equal(split.body, "Body.\r\n");
});


test("autolinks cannot join separate text into an HTML tag", () => {
    assert.equal(holdsMarkup("<scr<https://example.com>ipt>"), false);
    assert.equal(holdsMarkup("See <sam@example.com> and <script>alert(1)</script>"), true);
    assert.equal(holdsMarkup("`<div>` and <https://example.com> then <details>notes</details>"), true);
});
