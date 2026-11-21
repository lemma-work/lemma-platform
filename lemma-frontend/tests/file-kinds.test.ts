import test from "node:test";
import assert from "node:assert/strict";
import {
    countWords,
    crumbsOf,
    excerptOf,
    inListOrder,
    isLemmasOwn,
    isWithin,
    kindLine,
    kindOf,
    parentOf,
    readStateOf,
    type FileItem,
} from "../src/outcomes/file-kinds.ts";

function file(name: string, extra: Partial<FileItem> = {}): FileItem {
    return { id: name, name, kind: "file", path: "/" + name, updated: "2026-11-20T10:00:00Z", detail: "", ...extra };
}

test("a kind is said in words, from the name first and never as a mime type", () => {
    assert.deepEqual(kindOf(file("Q4 pipeline.xlsx")), { mark: "spreadsheet", word: "Spreadsheet" });
    assert.deepEqual(kindOf(file("Pricing 2026.md")), { mark: "markdown", word: "Markdown" });
    assert.deepEqual(kindOf(file("Northwind notes.pdf")), { mark: "pdf", word: "PDF" });
    assert.deepEqual(kindOf(file("call.m4a")), { mark: "audio", word: "Audio" });
    assert.deepEqual(kindOf(file("clip.MOV")), { mark: "video", word: "Video" });
    assert.deepEqual(kindOf({ name: "Proposals", kind: "folder" }), { mark: "folder", word: "Folder" });
    /* No extension: the stored type, carried as `mime` or left in `detail`. */
    assert.deepEqual(kindOf(file("scan", { mime: "image/png" })), { mark: "image", word: "Image" });
    assert.deepEqual(kindOf(file("notes", { detail: "text/markdown" })), { mark: "markdown", word: "Markdown" });
    /* A description in `detail` is not a type. */
    assert.deepEqual(kindOf(file("brief", { detail: "Weekly progress and next steps" })), { mark: "other", word: "File" });
});

test("the kind line carries the size, or a folder's count where it is known", () => {
    assert.equal(kindLine(file("a.pdf", { size: 1_258_291 })), "PDF · 1.2 MB");
    assert.equal(kindLine(file("a.pdf")), "PDF");
    const folder: FileItem = { id: "p", name: "Proposals", kind: "folder", path: "/Proposals", updated: "", detail: "" };
    assert.equal(kindLine(folder, { files: 3, folders: 0 }), "Folder · 3 files");
    assert.equal(kindLine(folder, null), "Folder");
    assert.equal(countWords({ files: 1, folders: 0 }), "1 file");
    assert.equal(countWords({ files: 2, folders: 1 }), "1 folder, 2 files");
    assert.equal(countWords({ files: 0, folders: 0 }), "Empty");
});

test("the Lemma's own folders are left out of the list, the person's files are not", () => {
    for (const path of ["/memory", "/skills/", "/pages", "/me", "/system", "/.internal", "/docs/.cache", "/me/agents", "/me/AGENTS.md", "/memory/brand-voice.md"]) {
        assert.equal(isLemmasOwn(path), true, path);
    }
    for (const path of ["/Proposals", "/Proposals/pages", "/me/notes.md", "/me/Proposals", "/sample-report.md"]) {
        assert.equal(isLemmasOwn(path), false, path);
    }
});

test("the path reads from the top of whose files they are", () => {
    assert.deepEqual(crumbsOf("/", "everyone"), [{ label: "Files", path: "/" }]);
    assert.deepEqual(crumbsOf("/Proposals/2026/", "everyone").map((step) => step.label), ["Files", "Proposals", "2026"]);
    assert.deepEqual(crumbsOf("/Proposals/2026", "everyone").at(-1), { label: "2026", path: "/Proposals/2026" });
    assert.deepEqual(crumbsOf("/me/Receipts", "mine"), [{ label: "My files", path: "/me" }, { label: "Receipts", path: "/me/Receipts" }]);
    assert.equal(parentOf("/Proposals/a.pdf"), "/Proposals");
    assert.equal(parentOf("/a.pdf"), "/");
    assert.equal(parentOf("/Proposals/"), "/");
});

test("a file on its way shows in the folder it went to and the ones above it", () => {
    assert.equal(isWithin("/Proposals", "/", "everyone"), true);
    assert.equal(isWithin("/Proposals/2026", "/Proposals", "everyone"), true);
    assert.equal(isWithin("/Contracts", "/Proposals", "everyone"), false);
    assert.equal(isWithin("/Proposals-old", "/Proposals", "everyone"), false);
    /* Everyone's top folder does not hold the reader's own. */
    assert.equal(isWithin("/me", "/", "everyone"), false);
    assert.equal(isWithin("/me/Receipts", "/me", "mine"), true);
});

test("reading is said only from statuses the datastore writes", () => {
    assert.equal(readStateOf("COMPLETED"), "read");
    assert.equal(readStateOf("processing"), "reading");
    assert.equal(readStateOf("PENDING"), "reading");
    assert.equal(readStateOf("FAILED_PERMANENT"), "failed");
    assert.equal(readStateOf("NOT_REQUIRED"), "skipped");
    assert.equal(readStateOf(undefined), null);
    assert.equal(readStateOf("SOMETHING_NEW"), null);
});

test("an excerpt leaves front matter off and cuts at a word", () => {
    assert.equal(excerptOf("---\nname: x\n---\n# Pricing\n\nPro stays."), "# Pricing\n\nPro stays.");
    const long = "word ".repeat(300);
    const cut = excerptOf(long, 100);
    assert.ok(cut.endsWith("…"));
    assert.ok(cut.length <= 101);
    assert.ok(!cut.slice(0, -1).endsWith(" "));
});

test("folders come first, then the most recently changed", () => {
    const listed = [
        file("old.pdf", { updated: "2026-01-01T00:00:00Z" }),
        { ...file("Proposals", { updated: "2025-01-01T00:00:00Z" }), kind: "folder" as const },
        file("new.pdf", { updated: "2026-11-01T00:00:00Z" }),
    ];
    assert.deepEqual(inListOrder(listed).map((item) => item.name), ["Proposals", "new.pdf", "old.pdf"]);
});
