import test from "node:test";
import assert from "node:assert/strict";
import {
    findQuery,
    isBoundTo,
    RESOURCE_KEY,
    resourceInstructions,
    resourceKey,
    resourceTitle,
} from "../src/thread/resource-conversation.ts";

test("a resource has one key however it was typed", () => {
    // A table looked up as `Invoices` and bound as `invoices` would quietly get
    // two conversations, and the second would look empty for no visible reason.
    assert.equal(resourceKey("table", "invoices"), "table:invoices");
    assert.equal(resourceKey("table", "Invoices"), "table:invoices");
    assert.equal(resourceKey("table", "  invoices  "), "table:invoices");
});

test("two kinds sharing a name are two different things", () => {
    assert.notEqual(resourceKey("table", "reports"), resourceKey("file", "reports"));
});

test("a conversation knows which resource it belongs to", () => {
    const bound = { metadata: { cwd: "/workspace/c/x", [RESOURCE_KEY]: "table:invoices" } };

    assert.equal(isBoundTo(bound, "table", "invoices"), true);
    assert.equal(isBoundTo(bound, "table", "Invoices"), true);
    assert.equal(isBoundTo(bound, "table", "receipts"), false);
    assert.equal(isBoundTo(bound, "file", "invoices"), false);
});

test("a conversation with no binding belongs to nothing", () => {
    // The server puts its own keys in this object, so "has metadata" is not
    // "is bound".
    assert.equal(isBoundTo({ metadata: { cwd: "/workspace/c/x" } }, "table", "invoices"), false);
    assert.equal(isBoundTo({ metadata: null }, "table", "invoices"), false);
    assert.equal(isBoundTo(null, "table", "invoices"), false);
    assert.equal(isBoundTo({ metadata: { [RESOURCE_KEY]: 42 } } as never, "table", "invoices"), false);
});

test("the find query asks for exactly the one conversation", () => {
    const query = findQuery("table", "Invoices");

    assert.equal(query["metadata." + RESOURCE_KEY], "table:invoices");
    assert.equal(query.type, "PROJECT", "standing conversations stay out of ordinary chat history");
    assert.equal(query.limit, 1);
});

test("the instruction says what the conversation is about, not how to do anything", () => {
    // An instruction that describes a method goes stale the moment the toolset
    // changes, and a stale instruction is worse than none because it is believed.
    const said = resourceInstructions("table", "invoices");

    assert.match(said, /table `invoices`/);
    assert.doesNotMatch(said, /pod_write_record|pod_tables|pod_query|workspace/i);
});

test("a conversation is titled after the thing it is about", () => {
    assert.equal(resourceTitle("table", "invoices"), "invoices · table");
    assert.equal(resourceTitle("workflow", "weekly-sweep"), "weekly-sweep · workflow");
});

test("a file is bound by its path, not its name", () => {
    // Two `notes.md` in two folders are two files. Binding by basename gives
    // one conversation claiming to be about both.
    assert.notEqual(
        resourceKey("file", "/me/launch/notes.md"),
        resourceKey("file", "/me/archive/notes.md"),
    );
});

test("every kind can carry a conversation and be told what it is about", () => {
    // A `Record<ResourceKind, …>` that gained a member without gaining a label
    // would produce "undefined" in the title of a real conversation.
    for (const kind of ["table", "file", "app", "workflow", "agent", "function", "schedule"] as const) {
        assert.match(resourceTitle(kind, "thing"), /^thing · [a-z]+$/, kind);
        assert.match(resourceInstructions(kind, "thing"), /`thing`/, kind);
        assert.doesNotMatch(resourceTitle(kind, "thing"), /undefined/, kind);
    }
});

test("a file is called by its name and identified by its path", () => {
    // Using the path for both put `/me/launch/notes/2026/q1/summary.md · file`
    // in a history panel where every row is one line.
    const deep = "/me/launch/notes/2026/q1/summary.md";

    assert.equal(resourceTitle("file", deep), "summary.md · file");
    assert.equal(resourceKey("file", deep), "file:" + deep.toLowerCase());
});

test("shortening the title does not merge two files that share a name", () => {
    // The label is allowed to collide; the key is not.
    assert.equal(resourceTitle("file", "/a/notes.md"), resourceTitle("file", "/b/notes.md"));
    assert.notEqual(resourceKey("file", "/a/notes.md"), resourceKey("file", "/b/notes.md"));
});

test("nothing but a file is shortened", () => {
    assert.equal(resourceTitle("table", "a/b"), "a/b · table");
});
