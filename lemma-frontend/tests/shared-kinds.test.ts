import test from "node:test";
import assert from "node:assert/strict";
import { readAs, toRows } from "../src/app/d/[code]/kinds.ts";

test("content-type decides, and the filename breaks the ties it leaves", () => {
    // The server knows best when it says anything specific.
    assert.equal(readAs("text/markdown", "anything"), "markdown");
    assert.equal(readAs("application/pdf", "report"), "pdf");
    assert.equal(readAs("image/png", "shot"), "image");
    assert.equal(readAs("text/html", "page"), "html");

    // …but markdown and CSV both arrive as plain text often enough that the
    // name is the better witness. Getting this wrong shows a reader a wall of
    // asterisks instead of a document.
    assert.equal(readAs("text/plain", "review.md"), "markdown");
    assert.equal(readAs("text/plain", "rows.csv"), "csv");
    assert.equal(readAs("text/plain", "rows.tsv"), "csv");
    assert.equal(readAs("application/octet-stream", "slides.pdf"), "pdf");

    // Structured text is readable as itself.
    assert.equal(readAs("application/json", "data.json"), "text");
    assert.equal(readAs("text/plain", "notes.txt"), "text");

    // And anything this page cannot draw honestly says so rather than guessing.
    assert.equal(readAs("application/zip", "bundle.zip"), "other");
    assert.equal(readAs("", "mystery"), "other");
});

test("a CSV survives commas, quotes and blank lines", () => {
    const rows = toRows('name,note\n"Ada","said ""hello"", then left"\n\n"Grace",found a bug\n', ",");
    assert.deepEqual(rows, [
        ["name", "note"],
        ["Ada", 'said "hello", then left'],
        ["Grace", "found a bug"],
    ]);
});

test("tabs separate when the file says they do", () => {
    assert.deepEqual(toRows("a\tb\n1\t2\n", "\t"), [["a", "b"], ["1", "2"]]);
});

test("a file with no final newline keeps its last row", () => {
    assert.deepEqual(toRows("a,b\n1,2", ","), [["a", "b"], ["1", "2"]]);
});
