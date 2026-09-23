import test from "node:test";
import assert from "node:assert/strict";
import { applyArchived, applyTitle, titleToSend, titleToShow, unbound, UNTITLED } from "../src/thread/conversation-list.ts";
import type { ConversationRef } from "../src/data/types.ts";

function list(): ConversationRef[] {
    return [
        { id: "a", title: "Renewal triage", at: "Today", kind: "CHAT" },
        { id: "b", title: UNTITLED, at: "Today", kind: "CHAT" },
        { id: "c", title: "Ticket sweep", at: "Fri", kind: "CHAT" },
    ];
}

test("a generated title lands on the conversation it belongs to", () => {
    const next = applyTitle(list(), "b", "Q1 vendor totals");

    assert.deepEqual(next?.map((entry) => entry.title), ["Renewal triage", "Q1 vendor totals", "Ticket sweep"]);
});

test("a patch that changes nothing returns the same array", () => {
    // The history panel re-renders on identity. A title event for a title we
    // already hold — a reconnecting stream replays them — must not repaint a
    // list somebody is reading.
    const before = list();

    assert.equal(applyTitle(before, "a", "Renewal triage"), before);
    assert.equal(applyTitle(before, "not-in-this-pod", "Anything"), before);
    assert.equal(applyArchived(before, "not-in-this-pod"), before);
});

test("a conversation with no title of its own reads as untitled, not as blank", () => {
    assert.equal(titleToShow(null), UNTITLED);
    assert.equal(titleToShow(""), UNTITLED);
    assert.equal(titleToShow("   "), UNTITLED);
    assert.equal(titleToShow(null, "Call"), "Call");
    assert.equal(titleToShow("  Renewal triage  "), "Renewal triage");
});

test("clearing a title hands it back to the generator rather than emptying it", () => {
    // `null` is what makes a conversation eligible for auto-titling again.
    // `""` is a title — the generator reads it as already done and never
    // touches the conversation again, so a name cleared that way stays gone.
    assert.equal(titleToSend(""), null);
    assert.equal(titleToSend("    "), null);
    assert.equal(titleToSend("  Renewal triage "), "Renewal triage");
});

test("a title patched to nothing shows as untitled", () => {
    const next = applyTitle(list(), "a", null);

    assert.equal(next?.find((entry) => entry.id === "a")?.title, UNTITLED);
});

test("an archived conversation leaves the list it can no longer be opened from", () => {
    const next = applyArchived(list(), "a");

    assert.deepEqual(next?.map((entry) => entry.id), ["b", "c"]);
});

test("an empty cache is left alone rather than invented", () => {
    // The panel can patch before its first fetch resolves. Producing a list
    // here would put a conversation on screen that nothing has confirmed.
    assert.equal(applyTitle(undefined, "a", "Anything"), undefined);
    assert.equal(applyArchived(undefined, "a"), undefined);
});

test("a resource's conversation is not listed as recent history", () => {
    // Its front door is the resource. Listing it here too fills a five-row
    // panel with tables and files and pushes the real conversations off.
    const listed: ConversationRef[] = [
        { id: "a", title: "Renewal triage", at: "Today", kind: "CHAT" },
        { id: "b", title: "invoices · table", at: "Today", kind: "PROJECT", boundTo: "table:invoices" },
        { id: "c", title: "Ticket sweep", at: "Fri", kind: "CHAT" },
    ];

    assert.deepEqual(unbound(listed).map((e) => e.id), ["a", "c"]);
});

test("the test is the binding, not the type", () => {
    // The default listing returns PROJECT conversations like any other, so
    // filtering on type would both miss bound ones and hide unbound ones.
    const listed: ConversationRef[] = [
        { id: "a", title: "A project", at: "Today", kind: "PROJECT" },
        { id: "b", title: "bound", at: "Today", kind: "CHAT", boundTo: "file:/x.md" },
    ];

    assert.deepEqual(unbound(listed).map((e) => e.id), ["a"]);
});

test("nothing listed is nothing filtered", () => {
    assert.deepEqual(unbound(undefined), []);
    assert.deepEqual(unbound([]), []);
});
