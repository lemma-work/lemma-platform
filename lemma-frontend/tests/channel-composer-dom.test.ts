import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/** The composer and the delivery line under a sent line, drawn in a page:
 *  a send that fails keeps what was typed, sending again is the same line,
 *  nothing can be changed while it sends, a draft stays with its thread --
 *  and the reader's own line says where it went, with Retry. */

type Posted = { clientMessageId?: string | null; text: string; threadRootId?: string | null; mentions?: unknown[] };
const posts: Posted[] = [];
const retried: string[] = [];
let answer: (() => Promise<unknown>) | null = null;
(globalThis as { __channelApi?: unknown }).__channelApi = {
    async post(_channelId: string, body: Posted) {
        posts.push(body);
        if (answer) return answer();
        return { id: "l-new", seq: 9, sender: { kind: "MEMBER", id: "me", name: "Dana" }, text: body.text, createdAt: "", parts: [] };
    },
    async retryDelivery(_channelId: string, _lineId: string, deliveryId: string) { retried.push(deliveryId); },
};

registerHooks({ load(url, context, nextLoad) {
    if (url.endsWith("/src/channels/api.ts")) {
        return { format: "module", shortCircuit: true, source: "export const channelApi = () => globalThis.__channelApi;" };
    }
    if (url.endsWith("/src/shell/address.ts")) {
        return { format: "module", shortCircuit: true, source: 'export const NOWHERE = {}; export const writeAddress = (a) => "/t/" + a.podId + "/" + a.tabId;' };
    }
    if (!url.endsWith(".tsx")) return nextLoad(url, context);
    return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
    }).outputText };
} });

async function page() {
    const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { pretendToBeVisual: true });
    const saved = new Map<string, PropertyDescriptor | undefined>();
    for (const [key, value] of Object.entries({
        window: dom.window, document: dom.window.document, navigator: dom.window.navigator,
        HTMLElement: dom.window.HTMLElement, Node: dom.window.Node,
        requestAnimationFrame: (run: () => void) => setTimeout(run, 0),
        IS_REACT_ACT_ENVIRONMENT: true,
    })) {
        saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
        Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
    }
    const { createRoot } = await import("react-dom/client");
    const root = createRoot(dom.window.document.getElementById("root")!);
    const cache = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const close = async () => {
        await act(async () => { root.unmount(); });
        cache.clear();
        for (const [key, descriptor] of saved) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else delete (globalThis as Record<string, unknown>)[key];
        }
        dom.window.close();
    };
    const type = async (text: string) => {
        const box = dom.window.document.querySelector("textarea")!;
        await act(async () => {
            Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, "value")!.set!.call(box, text);
            box.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
        });
    };
    const send = async () => {
        await act(async () => { dom.window.document.querySelector("form")!.requestSubmit(); });
    };
    const flush = async () => { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 5)); }); };
    return { dom, root, cache, close, type, send, flush };
}

const channel = {
    id: "c1", kind: "GROUP", title: "launch", topic: null, person: null, visibility: "PUBLIC", sources: [], assignee: null,
    agentsAnswer: true, handBackAfterHours: 12, status: null, attention: null, archived: false, joined: true, lastLine: null,
    lastMessageAt: null, lastSeq: 8, createdAt: "", unread: { lines: 0, mentions: 0, waiting: 0, readSeq: 8 },
    places: [{ id: "s-slack", platform: "SLACK", kind: "CHAT", title: "#launch", surfaceId: null, pending: false, lastInboundAt: null },
        { id: "s-wa", platform: "WHATSAPP", kind: "CHAT", title: null, surfaceId: null, pending: false, lastInboundAt: null }],
    members: [], me: { joined: true, notify: "ALL", notifyVia: null, mutedUntil: null, readSeq: 8, role: "owner", canManage: true },
};

test("a send that fails keeps what was typed; sending again is the same line; nothing changes while it sends", async () => {
    const { Composer } = await import("../src/channels/composer.tsx");
    const view = await page();
    const done: unknown[] = [];
    try {
        await act(async () => {
            view.root.render(createElement(QueryClientProvider, { client: view.cache },
                createElement(Composer, { podId: "pod", channel: channel as never, people: [], placeholder: "Write in launch", onPosted: (line: unknown) => done.push(line) })));
        });
        await view.type("Ship it Thursday");
        let fail!: (error: Error) => void;
        answer = () => new Promise((_resolve, reject) => { fail = reject; });
        await view.send();
        // The send is still out (``answer`` never settles until ``fail``):
        // let the pending state reach the page before reading it.
        await view.flush();
        const box = view.dom.window.document.querySelector("textarea")!;
        const button = view.dom.window.document.querySelector<HTMLButtonElement>(".chcompose__send")!;
        assert.equal(box.readOnly, true, "the text cannot change under a send in flight");
        assert.equal(button.disabled, true);
        await act(async () => { fail(new Error("The server is restarting.")); });
        await view.flush();
        assert.equal(box.value, "Ship it Thursday", "a failed send keeps every word");
        assert.equal(box.readOnly, false);
        assert.match(view.dom.window.document.body.textContent ?? "", /The server is restarting\. What you wrote is still here\./);

        answer = null;
        await view.send();
        await view.flush();
        assert.equal(posts.length, 2);
        assert.ok(posts[0].clientMessageId);
        assert.equal(posts[1].clientMessageId, posts[0].clientMessageId, "a retry is the same line, never a second");
        assert.equal(box.value, "", "sent, the box is empty again");
        assert.equal(done.length, 1);

        await view.type("Something new");
        await view.send();
        await view.flush();
        assert.notEqual(posts[2].clientMessageId, posts[0].clientMessageId, "a new line is a new name");
    } finally {
        answer = null;
        posts.length = 0;
        await view.close();
    }
});

test("what is typed in a thread stays with that thread, and a reader who may not write is told why", async () => {
    const { Composer } = await import("../src/channels/composer.tsx");
    const view = await page();
    const draw = async (threadRootId: string | undefined, disabledReason: string | null = null) => {
        await act(async () => {
            view.root.render(createElement(QueryClientProvider, { client: view.cache },
                createElement(Composer, { key: threadRootId ?? "main", podId: "pod", channel: channel as never, people: [], threadRootId, placeholder: "Reply", disabledReason, onPosted: () => undefined })));
        });
    };
    try {
        await draw("root-1");
        await view.type("half a reply");
        await draw("root-2");
        assert.equal(view.dom.window.document.querySelector("textarea")!.value, "", "another thread starts empty");
        await draw("root-1");
        assert.equal(view.dom.window.document.querySelector("textarea")!.value, "half a reply");
        await draw(undefined, "You can read this chat. Writing here takes a role in Acme that can use it.");
        assert.equal(view.dom.window.document.querySelector("textarea"), null);
        assert.match(view.dom.window.document.body.textContent ?? "", /You can read this chat/);
    } finally {
        await view.close();
    }
});

test("the reader's own line says where it went, and a send that gave up can be retried", async () => {
    const { DeliveryStatus } = await import("../src/channels/parts.tsx");
    const view = await page();
    try {
        const line = {
            id: "l7", seq: 7, sender: { kind: "MEMBER", id: "me", name: "Dana" }, text: "hello", parts: [], threadRootId: null,
            delivered: [
                { id: "d1", sourceId: "s-slack", platform: "SLACK", status: "SENT", reason: null },
                { id: "d2", sourceId: "s-wa", platform: "WHATSAPP", status: "DEAD", reason: "The number is not on WhatsApp." },
            ],
        };
        await act(async () => {
            view.root.render(createElement(QueryClientProvider, { client: view.cache },
                createElement(DeliveryStatus, { podId: "pod", channel: channel as never, line: line as never, canRetry: true })));
        });
        await view.flush();
        const said = view.dom.window.document.body.textContent ?? "";
        assert.match(said, /Sent to #launch \(Slack\)/);
        assert.match(said, /Couldn’t reach WhatsApp: The number is not on WhatsApp/);
        const retry = [...view.dom.window.document.querySelectorAll("button")].find((button) => button.textContent === "Retry")!;
        assert.ok(retry, "a send that gave up offers Retry");
        await act(async () => { retry.click(); });
        await view.flush();
        assert.deepEqual(retried, ["d2"]);
    } finally {
        await view.close();
    }
});
