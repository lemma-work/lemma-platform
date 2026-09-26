import test from "node:test";
import assert from "node:assert/strict";
import { fileReading, readingProblem, withItemStatus } from "../src/library/file-status.ts";
import type { Pages } from "../src/library/library-cache.ts";
import type { LibraryItem } from "../src/data/types.ts";

test("a file still being read says so", () => {
    assert.deepEqual(fileReading("PENDING"), { state: "reading", label: "Reading…" });
    assert.deepEqual(fileReading("PROCESSING"), { state: "reading", label: "Reading…" });
});

test("a failed file says it could not be read, terminal or not", () => {
    assert.equal(fileReading("FAILED")?.state, "failed");
    assert.equal(fileReading("FAILED_PERMANENT")?.state, "failed");
    assert.equal(fileReading("failed")?.label, "Couldn’t read");
});

test("a file that was read, or is never read, says nothing", () => {
    // Quiet is the point: done is what every file is supposed to be.
    assert.equal(fileReading("COMPLETED"), null);
    assert.equal(fileReading("NOT_REQUIRED"), null);
    assert.equal(fileReading(undefined), null);
    assert.equal(fileReading("SOMETHING_NEW"), null);
});

test("the reason is the server's own words, or a plain fallback", () => {
    assert.equal(readingProblem("Lemma is still downloading its search model."), "Lemma is still downloading its search model.");
    assert.match(readingProblem(null), /could not read this file/);
    assert.match(readingProblem("   "), /could not read this file/);
});

function row(path: string, status?: string): LibraryItem {
    return { id: path, name: path.slice(1), kind: "file", path, updated: "2026-09-18T10:00:00Z", detail: "", status };
}

test("a retry flips only that row back to reading", () => {
    const cache: Pages = {
        pageParams: [undefined, "p2"],
        pages: [{ items: [row("/a.pdf", "COMPLETED")], next: "p2" }, { items: [row("/b.pdf", "FAILED")], next: null }],
    };
    const next = withItemStatus(cache, "/b.pdf", "PENDING");
    assert.notEqual(next, cache);
    assert.equal(next?.pages[1].items[0].status, "PENDING");
    // Untouched pages stay the same objects, so nothing else re-renders.
    assert.equal(next?.pages[0], cache.pages[0]);
    assert.equal(withItemStatus(next, "/b.pdf", "PENDING"), next);
});
