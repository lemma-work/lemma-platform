import test from "node:test";
import assert from "node:assert/strict";
import { draftKeyFor, readDraft, writeDraft } from "../src/thread/drafts.ts";
import { key, type KeyValueStore } from "../src/session/storage.ts";

/** Unsent words, kept per conversation across switching away and back. */

function store(): KeyValueStore & { data: Map<string, string> } {
    const data = new Map<string, string>();
    return {
        data,
        getItem: (name) => data.get(name) ?? null,
        setItem: (name, value) => { data.set(name, value); },
        removeItem: (name) => { data.delete(name); },
    };
}

test("a draft is still there when the conversation is opened again", () => {
    const browser = store();
    writeDraft(browser, "pod:c1", "Can you also check");
    writeDraft(browser, "pod:c2", "Different thread");

    assert.equal(readDraft(browser, "pod:c1"), "Can you also check");
    assert.equal(readDraft(browser, "pod:c2"), "Different thread");
    assert.equal(readDraft(browser, "pod:c3"), "");
});

test("sending empties the draft and leaves nothing behind", () => {
    const browser = store();
    writeDraft(browser, "pod:c1", "Ship it");
    writeDraft(browser, "pod:c1", "");

    assert.equal(readDraft(browser, "pod:c1"), "");
    assert.equal(browser.data.has(key("drafts")), false, "an empty map is no entry at all");
});

test("whitespace is not a draft", () => {
    const browser = store();
    writeDraft(browser, "pod:c1", "   \n ");
    assert.equal(browser.data.has(key("drafts")), false);
});

test("drafts abandoned for good are dropped, oldest first", () => {
    const browser = store();
    for (let index = 0; index < 45; index += 1) writeDraft(browser, "pod:c" + index, "draft " + index);

    assert.equal(readDraft(browser, "pod:c0"), "");
    assert.equal(readDraft(browser, "pod:c44"), "draft 44");
    assert.equal(Object.keys(JSON.parse(browser.data.get(key("drafts")) ?? "{}")).length, 40);
});

test("two conversations with one teammate are two drafts, not one", () => {
    assert.equal(draftKeyFor("pod", "c1"), "pod:c1");
    assert.notEqual(draftKeyFor("pod", "c1"), draftKeyFor("pod", "c2"));
});

test("a pane with no conversation yet keeps a draft of its own", () => {
    /* The chat beside a doc, before anything is sent: named for the doc, so
       the words wait for it instead of following somebody to the next one. */
    assert.equal(draftKeyFor("pod", null, "file:notes.md"), "pod:new:file:notes.md");
    assert.notEqual(draftKeyFor("pod", null, "file:notes.md"), draftKeyFor("pod", null, "file:plan.md"));
    /* The main pane, which is about nothing in particular, keeps the one
       "new" draft it always had. */
    assert.equal(draftKeyFor("pod", null), "pod:new");
    /* And once the conversation exists, what it is about stops mattering. */
    assert.equal(draftKeyFor("pod", "c1", "file:notes.md"), "pod:c1");
});

test("storage that is broken or refuses writes costs the draft, not the page", () => {
    const broken: KeyValueStore = {
        getItem: () => "{not json",
        setItem: () => { throw new Error("QuotaExceededError"); },
        removeItem: () => {},
    };
    assert.equal(readDraft(broken, "pod:c1"), "");
    assert.doesNotThrow(() => writeDraft(broken, "pod:c1", "text"));
});
