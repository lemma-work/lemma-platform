import test from "node:test";
import assert from "node:assert/strict";
import { cellText, idOf, rowLabel, withRow, withUpdatedRow, withoutRow, type RowPages } from "../src/library/record-cache.ts";

function cache(): RowPages {
    return {
        pageParams: [undefined, "p2"],
        pages: [
            { items: [{ ref: "a", title: "Alpha" }, { ref: "b", title: "Beta" }], next: "p2" },
            { items: [{ ref: "c", title: "Gamma" }], next: null },
        ],
    };
}

test("a row is identified by the key the table declares, not by 'id'", () => {
    // A pod can name its primary key anything. A delete aimed at the wrong
    // column hits nothing — or an update writes over a different row.
    assert.equal(idOf({ ref: "a" }, "ref"), "a");
    assert.equal(idOf({ id: "a" }, "ref"), null);
    assert.equal(idOf({ ref: 7 }, "ref"), "7", "a numeric key is still a key");
    assert.equal(idOf({ ref: null }, "ref"), null);
    assert.equal(idOf({}, "ref"), null);
});

test("a new row goes where it can be seen", () => {
    const next = withRow(cache(), { ref: "d", title: "Delta" });

    assert.deepEqual(next?.pages[0].items.map((r) => r.ref), ["d", "a", "b"]);
    assert.deepEqual(next?.pages[1].items.map((r) => r.ref), ["c"]);
});

test("an update reaches whichever page the row is on", () => {
    const next = withUpdatedRow(cache(), "ref", "c", { title: "Changed" });

    assert.deepEqual(next?.pages[1].items, [{ ref: "c", title: "Changed" }]);
});

test("an update merges rather than replaces", () => {
    // A partial update's response carries what the server wrote, not every
    // column. Replacing would blank the ones it did not mention.
    const next = withUpdatedRow(cache(), "ref", "a", { title: "Renamed" });

    assert.deepEqual(next?.pages[0].items[0], { ref: "a", title: "Renamed" });
});

test("a delete removes exactly one row", () => {
    const next = withoutRow(cache(), "ref", "b");

    assert.deepEqual(next?.pages[0].items.map((r) => r.ref), ["a"]);
    assert.deepEqual(next?.pages[1].items.map((r) => r.ref), ["c"]);
});

test("a patch that matches nothing returns the same cache", () => {
    const before = cache();

    assert.equal(withoutRow(before, "ref", "nope"), before);
    assert.equal(withUpdatedRow(before, "ref", "nope", { title: "x" }), before);
    assert.equal(withRow(undefined, { ref: "a" }), undefined);
    assert.equal(withoutRow(undefined, "ref", "a"), undefined);
});

test("a cell says what is there, including that nothing is", () => {
    assert.equal(cellText(null), "—");
    assert.equal(cellText(undefined), "—");
    assert.equal(cellText(""), "");
    assert.equal(cellText(0), "0", "zero is a value, not an absence");
    assert.equal(cellText(false), "false");
    assert.equal(cellText({ a: 1 }), '{"a":1}');
});

test("a row is not named after a timestamp", () => {
    // Key order is the server's. On a real table it put `created_at` first and
    // the delete confirmation read "Delete 2026-09-17T20:45:37.523110Z?", which
    // names a moment rather than a row.
    const row = { id: "7f3a-uuid", created_at: "2026-09-17T20:45:37.523110Z", title: "Launch checklist" };

    assert.equal(rowLabel(row, "id"), "Launch checklist");
});

test("a naming column wins over whatever happens to come first", () => {
    assert.equal(rowLabel({ id: "x", zzz_note: "some note", name: "Acme" }, "id"), "Acme");
    assert.equal(rowLabel({ id: "x", body: "long-ish body", title: "Headline" }, "id"), "Headline");
});

test("a long title is shortened, not swapped for another column", () => {
    // A real row's title ran to 62 characters, the cap rejected it, and the
    // search moved on and named the row "post" after its format column. Long is
    // not the same as unusable.
    const long = 'AI at work — "Anatomy of an AI worker" story series (UGC play)';
    assert.ok(long.length > 60, "the fixture has to be over the cap to mean anything");

    const label = rowLabel({ id: "x", title: long, format: "post", status: "ready" }, "id");

    assert.notEqual(label, "post");
    assert.ok(label.startsWith("AI at work"));
    assert.ok(label.length <= 60);
    assert.ok(label.endsWith("…"));
});

test("bookkeeping and machine values are never a row's name", () => {
    assert.equal(rowLabel({ id: "x", updated_at: "2026-01-02T03:04:05Z" }, "id"), "x");
    assert.equal(rowLabel({ id: "x", ref: "0f9d2c1a-4b5e-4f6a-9c8d-1e2f3a4b5c6d" }, "id"), "x");
    assert.equal(rowLabel({ id: "x", creator_user_id: "someone" }, "id"), "x");
    assert.equal(rowLabel({ id: "x", pod_id: "a-pod" }, "id"), "x");
});

test("a row is named by something a person would recognise", () => {
    // A primary key is usually a uuid, and "delete 7f3a…" tells nobody which
    // row is about to go.
    assert.equal(rowLabel({ id: "7f3a-uuid", name: "Acme Ltd" }, "id"), "Acme Ltd");
    assert.equal(rowLabel({ id: "7f3a", count: 4 }, "id"), "7f3a", "nothing readable falls back to the key");
    // Past the naming columns, length matters again: the first sixty characters
    // of an arbitrary essay column identify nothing.
    assert.equal(rowLabel({ id: "7f3a", blurb: "x".repeat(400) }, "id"), "7f3a", "an essay is not a label");
    assert.equal(rowLabel({ id: "7f3a", note: "   " }, "id"), "7f3a", "blank is not a label");
});
