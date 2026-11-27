import test from "node:test";
import assert from "node:assert/strict";
import { move, samePage } from "../src/outcomes/move.ts";
import type { View } from "../src/outcomes/address.ts";

/** A page change fades the main area from one page to the next. A change on
 *  the page the reader is already on must not: an app that moves inside
 *  itself pushes an address on every click in it, and fading the whole
 *  screen for each read as the page jumping. */

const POD = "pod_3f9";

test("an app moving inside itself stays on its page", () => {
    const at = (path?: string): View => (path ? { name: "app", podId: POD, app: "crm", at: path } : { name: "app", podId: POD, app: "crm" });
    assert.equal(samePage(at(), at("/people/7")), true);
    assert.equal(samePage(at("/people/7"), at("/deals/2")), true);
    assert.equal(samePage(at("/deals/2"), at()), true);
});

test("another app, or the same app in another Lemma, is another page", () => {
    assert.equal(samePage({ name: "app", podId: POD, app: "crm" }, { name: "app", podId: POD, app: "inbox" }), false);
    assert.equal(samePage({ name: "app", podId: POD, app: "crm" }, { name: "app", podId: "pod_other", app: "crm" }), false);
});

test("another section of a Lemma's tab stays on its page; another tab does not", () => {
    assert.equal(samePage({ name: "outcome", podId: POD, tab: "automations", section: "workflows" }, { name: "outcome", podId: POD, tab: "automations", section: "schedules" }), true);
    assert.equal(samePage({ name: "outcome", podId: POD, tab: "settings" }, { name: "outcome", podId: POD, tab: "settings", section: "general" }), true);
    assert.equal(samePage({ name: "outcome", podId: POD, tab: "home" }, { name: "outcome", podId: POD, tab: "apps" }), false);
    assert.equal(samePage({ name: "outcome", podId: POD, tab: "home" }, { name: "outcome", podId: "pod_other", tab: "home" }), false);
});

test("History, Settings and a Lemma's machine keep their page as they narrow", () => {
    assert.equal(samePage({ name: "history", podId: null }, { name: "history", podId: POD }), true);
    assert.equal(samePage({ name: "settings", section: null }, { name: "settings", section: "billing" }), true);
    assert.equal(samePage({ name: "computer", podId: POD, conversationId: null }, { name: "computer", podId: POD, conversationId: "c_1" }), true);
});

test("the page already open is the same page; another place is not", () => {
    assert.equal(samePage({ name: "apps" }, { name: "apps" }), true);
    assert.equal(samePage({ name: "chats", open: { kind: "chat", podId: POD, id: "c_1" } }, { name: "chats", open: { kind: "chat", podId: POD, id: "c_1" } }), true);
    assert.equal(samePage({ name: "chats", open: { kind: "chat", podId: POD, id: "c_1" } }, { name: "chats", open: { kind: "chat", podId: POD, id: "c_2" } }), false);
    assert.equal(samePage({ name: "today" }, { name: "apps" }), false);
    assert.equal(samePage({ name: "table", podId: POD, table: "deals" }, { name: "record", podId: POD, table: "deals", recordId: "r_1" }), false);
});

/** A browser whose view transitions hold their update until told to run it,
 *  as a real one holds it for a frame while it captures the old page. */
function browserWithTransitions(): { pending: (() => Promise<void> | void)[]; restore: () => void } {
    const pending: (() => Promise<void> | void)[] = [];
    const done = Promise.resolve();
    const fake = {
        visibilityState: "visible",
        querySelector: () => null,
        startViewTransition: (update: () => Promise<void> | void) => {
            pending.push(update);
            return { ready: done, finished: done, skipTransition: () => undefined };
        },
    };
    const was = { document: (globalThis as { document?: unknown }).document, window: (globalThis as { window?: unknown }).window };
    Object.assign(globalThis, { document: fake, window: { matchMedia: () => ({ matches: false }), setTimeout: () => 0 } });
    return {
        pending,
        restore: () => Object.assign(globalThis, was),
    };
}

test("a page change still waiting to move does not land over a later change on the same page", async () => {
    const browser = browserWithTransitions();
    try {
        const made: string[] = [];
        const from: View = { name: "app", podId: POD, app: "crm" };
        move(from, { name: "apps" }, async () => { made.push("apps"); });
        move(from, { name: "app", podId: POD, app: "crm", at: "/people/7" }, async () => { made.push("app moved"); });
        assert.deepEqual(made, ["app moved"]);
        for (const update of browser.pending) await update();
        assert.deepEqual(made, ["app moved"]);
    } finally {
        browser.restore();
    }
});

test("of two page changes asked for in one frame, the later is the one made", async () => {
    const browser = browserWithTransitions();
    try {
        const made: string[] = [];
        move({ name: "today" }, { name: "apps" }, async () => { made.push("apps"); });
        move({ name: "today" }, { name: "connectors" }, async () => { made.push("connectors"); });
        for (const update of browser.pending) await update();
        assert.deepEqual(made, ["connectors"]);
    } finally {
        browser.restore();
    }
});

test("a page change on its own is made once its move starts", async () => {
    const browser = browserWithTransitions();
    try {
        const made: string[] = [];
        move({ name: "today" }, { name: "apps" }, async () => { made.push("apps"); });
        assert.deepEqual(made, []);
        for (const update of browser.pending) await update();
        assert.deepEqual(made, ["apps"]);
    } finally {
        browser.restore();
    }
});
