import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ASKS_REFETCH_MS, CONVERSATION_ASKS, conversationAsksQuery } from "@/thread/waiting-on-you";

/** "Waiting on you" — the inbox beside the bell, the rail's badges and Home —
 *  used to refresh only when somebody navigated, so a question asked in
 *  another conversation sat unseen until then. One query, polled, and an open
 *  conversation that tells it when what it waits on changes. */

const pods = [
    { id: "p1", name: "Kit" },
    { id: "p2", name: "Ada" },
];

test("the asks every list reads poll once a minute under one key", () => {
    const query = conversationAsksQuery(pods, false);
    assert.deepEqual(query.queryKey, [CONVERSATION_ASKS, "p1,p2"]);
    assert.equal(query.refetchInterval, ASKS_REFETCH_MS);
    assert.equal(ASKS_REFETCH_MS, 60_000);
    assert.equal(query.enabled, true);
});

test("the asks wait for the pod list, and for a caller that turns them off", () => {
    assert.equal(conversationAsksQuery([], false).enabled, false);
    assert.equal(conversationAsksQuery(pods, false, false).enabled, false);
});

/** A conversation on a page of its own, reporting what it waits on, with
 *  every invalidation the cache was asked for kept in `invalidated`. */
async function mount(first: string | null) {
    const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { pretendToBeVisual: true });
    for (const [key, value] of Object.entries({
        window: dom.window, document: dom.window.document, navigator: dom.window.navigator,
        HTMLElement: dom.window.HTMLElement, Node: dom.window.Node,
        IS_REACT_ACT_ENVIRONMENT: true,
    })) {
        Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
    }
    const { createRoot } = await import("react-dom/client");
    const { useAsksFollowWaiting } = await import("@/thread/use-conversation-asks");
    const cache = new QueryClient();
    const invalidated: unknown[] = [];
    const real = cache.invalidateQueries.bind(cache);
    cache.invalidateQueries = ((filters, options) => {
        invalidated.push(filters?.queryKey);
        return real(filters, options);
    }) as typeof cache.invalidateQueries;
    function Conversation({ waitingOn }: { waitingOn: string | null }) {
        useAsksFollowWaiting(waitingOn);
        return null;
    }
    const root = createRoot(dom.window.document.getElementById("root")!);
    const show = (waitingOn: string | null) => act(async () => {
        root.render(createElement(QueryClientProvider, { client: cache }, createElement(Conversation, { waitingOn })));
    });
    await show(first);
    return { invalidated, show, unmount: () => act(async () => { root.unmount(); }) };
}

test("opening a conversation that is already waiting asks for nothing", async () => {
    const page = await mount("ask-1");
    await page.show("ask-1");
    assert.deepEqual(page.invalidated, []);
    await page.unmount();
});

test("a new ask, and the same ask answered, each re-read what is owed", async () => {
    const page = await mount(null);

    await page.show("ask-1");
    assert.deepEqual(page.invalidated, [[CONVERSATION_ASKS]], "a run that stopped to ask reaches the rail now");

    await page.show(null);
    assert.deepEqual(page.invalidated, [[CONVERSATION_ASKS], [CONVERSATION_ASKS]], "an answer, from anywhere, clears it now");

    await page.show("ask-2");
    assert.equal(page.invalidated.length, 3, "a second ask after the first is news too");
    await page.unmount();
});
