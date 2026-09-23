import test from "node:test";
import assert from "node:assert/strict";
import {
    nameProblem,
    pathIn,
    renamedPath,
    withItem,
    withRenamedItem,
    withoutItem,
    type Pages,
} from "../src/library/library-cache.ts";
import type { LibraryItem } from "../src/data/types.ts";

function item(name: string, path: string): LibraryItem {
    return { id: path, name, kind: "file", path, updated: "2026-09-18T10:00:00Z", detail: "" };
}

function cache(): Pages {
    return {
        pageParams: [undefined, "p2"],
        pages: [
            { items: [item("a.md", "/a.md"), item("b.md", "/b.md")], next: "p2" },
            { items: [item("c.md", "/c.md")], next: null },
        ],
    };
}

test("something just made goes to the top of the first page", () => {
    // Where the listing already puts recent things, and where the person is
    // looking: they pressed the button.
    const next = withItem(cache(), item("new.md", "/new.md"));

    assert.deepEqual(next?.pages[0].items.map((one) => one.path), ["/new.md", "/a.md", "/b.md"]);
    assert.deepEqual(next?.pages[1].items.map((one) => one.path), ["/c.md"]);
});

test("uploading over a file replaces its row rather than adding a second", () => {
    // The server overwrote one file. Two rows would be one file said twice.
    const next = withItem(cache(), { ...item("a.md", "/a.md"), detail: "just now" });

    assert.deepEqual(next?.pages[0].items.map((one) => one.path), ["/a.md", "/b.md"]);
    assert.equal(next?.pages[0].items[0].detail, "just now");
});

test("a delete reaches whichever page the item is on", () => {
    const next = withoutItem(cache(), "/c.md");

    assert.deepEqual(next?.pages[1].items, []);
    assert.deepEqual(next?.pages[0].items.map((one) => one.path), ["/a.md", "/b.md"]);
});

test("a rename keeps the item where it was", () => {
    const next = withRenamedItem(cache(), "/b.md", "brief.md", "/brief.md");

    assert.deepEqual(next?.pages[0].items.map((one) => one.name), ["a.md", "brief.md"]);
    assert.equal(next?.pages[0].items[1].path, "/brief.md");
});

test("a patch that matches nothing returns the same cache", () => {
    const before = cache();

    assert.equal(withoutItem(before, "/not-here.md"), before);
    assert.equal(withRenamedItem(before, "/not-here.md", "x", "/x"), before);
    assert.equal(withItem(undefined, item("a", "/a")), undefined);
    assert.equal(withoutItem(undefined, "/a"), undefined);
});

test("a rename moves nothing", () => {
    // Same directory, new last segment. A rename that changed the directory
    // would be a move wearing a rename's clothes.
    assert.equal(renamedPath("/me/notes/draft.md", "final.md"), "/me/notes/final.md");
    assert.equal(renamedPath("/draft.md", "final.md"), "/final.md");
    assert.equal(renamedPath("/me/notes/", "papers"), "/me/papers");
});

test("a new item's path is its name inside the directory it was made in", () => {
    assert.equal(pathIn("/", "report.pdf"), "/report.pdf");
    assert.equal(pathIn("/me", "report.pdf"), "/me/report.pdf");
    assert.equal(pathIn("/me/notes/", "report.pdf"), "/me/notes/report.pdf");
    assert.equal(pathIn("/me", "  spaced.pdf  "), "/me/spaced.pdf");
});

test("a slash in a name is a move, and is refused as one", () => {
    // Renaming and moving are different acts with different consequences. One
    // should not become the other because somebody typed a character.
    assert.equal(nameProblem("notes/draft.md"), "A name cannot contain a slash.");
    assert.equal(nameProblem(""), "A name is required.");
    assert.equal(nameProblem("   "), "A name is required.");
    assert.equal(nameProblem(".."), "That name is reserved.");
    assert.equal(nameProblem("."), "That name is reserved.");
    assert.equal(nameProblem("x".repeat(256)), "That name is too long.");
    assert.equal(nameProblem("perfectly fine.md"), null);
});
