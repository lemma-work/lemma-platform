import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/** The dialog a channel is made in and the panel that says what one is,
 *  drawn in a page: the people offered are the pod's now, each checkbox
 *  named for its person; each picker sits under its own list, a long name
 *  can be read in full; an answered question says what was answered. */

type Api = Record<string, (...args: never[]) => unknown>;
const api: Api = {};
(globalThis as { __channelApi?: unknown }).__channelApi = api;
(globalThis as { __agents?: unknown }).__agents = [{ id: "agent-bolt", label: "Bolt" }, { id: "agent-scout", label: "Scout" }];

registerHooks({ load(url, context, nextLoad) {
    if (url.endsWith("/src/channels/api.ts")) {
        return { format: "module", shortCircuit: true, source: "export const channelApi = () => globalThis.__channelApi;" };
    }
    if (url.endsWith("/src/data/index.ts")) {
        return { format: "module", shortCircuit: true, source: "export const source = { label: 'test', listAgents: async () => globalThis.__agents };" };
    }
    if (url.endsWith("/src/session/use-me.ts")) {
        return { format: "module", shortCircuit: true, source: 'export const useMe = () => "me";' };
    }
    if (url.endsWith("/src/thread/markdown.tsx")) {
        return { format: "module", shortCircuit: true, source: 'import { createElement } from "react"; export const Prose = ({ text }) => createElement("p", null, text);' };
    }
    if (url.endsWith("/src/channels/waiting-panel.tsx")) {
        return { format: "module", shortCircuit: true, source: "export const WaitingRow = () => null; export const useInbox = () => ({ data: undefined }); export const inboxKey = (podId) => ['inbox', podId];" };
    }
    if (url.endsWith("/src/shell/address.ts")) {
        return { format: "module", shortCircuit: true, source: 'export const NOWHERE = {}; export const writeAddress = (a) => "/t/" + a.podId + "/" + a.tabId;' };
    }
    /* The shell's icon and link, which bring the whole shell with them. */
    if (url.endsWith("/src/shell/channels.tsx")) {
        return { format: "module", shortCircuit: true, source: "export const ChannelIcon = () => null; export const platformName = (p) => String(p);" };
    }
    if (url.endsWith("/src/shell/place-link.tsx")) {
        return { format: "module", shortCircuit: true, source: 'import { createElement } from "react"; export const PlaceLink = ({ children }) => createElement("a", null, children); export const channelHref = () => "#"; export const goTo = () => {};' };
    }
    if (url.endsWith("/src/channels/source-settings.tsx")) {
        return { format: "module", shortCircuit: true, source: "export const SourceSettingsView = () => null;" };
    }
    if (url.endsWith(".css")) return { format: "module", shortCircuit: true, source: "" };
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
        HTMLElement: dom.window.HTMLElement, Node: dom.window.Node, CSS: { escape: (value: string) => value },
        requestAnimationFrame: (run: () => void) => setTimeout(run, 0),
        IS_REACT_ACT_ENVIRONMENT: true,
    })) {
        saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
        Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
    }
    const { createRoot } = await import("react-dom/client");
    const root = createRoot(dom.window.document.getElementById("root")!);
    const cache = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const draw = async (element: unknown) => {
        await act(async () => { root.render(createElement(QueryClientProvider, { client: cache }, element as never)); });
    };
    const flush = async () => { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 5)); }); };
    const close = async () => {
        await act(async () => { root.unmount(); });
        cache.clear();
        for (const [key, descriptor] of saved) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else delete (globalThis as Record<string, unknown>)[key];
        }
        dom.window.close();
    };
    return { dom, cache, draw, flush, close, body: () => dom.window.document.body.textContent ?? "" };
}

const pod = {
    id: "pod", name: "Bolt", iconUrl: null,
    members: [
        { id: "m-me", userId: "me", name: "Me", initials: "M", kind: "person" },
        { id: "m-sam", userId: "user-sam", name: "Sam Okonkwo-Fairweather", initials: "SO", kind: "person" },
        { id: "m-lee", userId: "user-lee", name: "Lee", initials: "L", kind: "person" },
    ],
};

/** The accessible name a control has, the way a browser works it out for
 *  these: `aria-label`, else the label that names it. */
function nameOf(control: Element): string {
    const label = control.getAttribute("aria-label");
    if (label) return label;
    const id = control.getAttribute("id");
    const by = id ? control.ownerDocument.querySelector(`label[for="${id}"]`) : control.closest("label");
    return by?.textContent?.trim() ?? "";
}

test("starting a shared chat asks for the pod's members again, and names each checkbox for its person", async () => {
    const { NewSharedChat } = await import("../src/channels/new-channel.tsx");
    const view = await page();
    let reads = 0;
    try {
        /* The shell's roster, read when the space opened. */
        await view.cache.prefetchQuery({ queryKey: ["pod-detail", "pod"], queryFn: async () => { reads += 1; return { members: [] }; }, staleTime: 5 * 60_000 });
        await view.draw(createElement(NewSharedChat, { pod: pod as never, onClose: () => {}, onMade: () => {} }));
        await view.flush();
        assert.equal(view.cache.getQueryState(["pod-detail", "pod"])?.isInvalidated, true, "the roster held is asked for again");
        const boxes = [...view.dom.window.document.querySelectorAll("fieldset input[type=checkbox]")];
        assert.deepEqual(boxes.map(nameOf), ["Sam Okonkwo-Fairweather", "Lee"]);
        assert.ok(reads >= 1);
    } finally {
        await view.close();
    }
});

const detail = {
    id: "c1", kind: "GROUP", title: "launch", topic: null, person: null, visibility: "PUBLIC", sources: [], assignee: null,
    agentsAnswer: true, handBackAfterHours: 12, status: null, attention: null, archived: false, joined: true, lastLine: null,
    lastMessageAt: null, lastSeq: 8, createdAt: "", unread: { lines: 0, mentions: 0, waiting: 0, readSeq: 8 }, places: [],
    members: [
        { person: { kind: "MEMBER", id: "me", name: "Me" }, role: "owner", answers: null },
        { person: { kind: "MEMBER", id: "user-sam", name: "Sam Okonkwo-Fairweather" }, role: "member", answers: null },
        { person: { kind: "AGENT", id: "agent-bolt", name: "Bolt" }, role: "member", answers: "MENTIONED" },
    ],
    me: { joined: true, notify: "ALL", notifyVia: null, mutedUntil: null, readSeq: 8, role: "owner", canManage: true },
};
const may = { manage: true, write: true, writeRefused: null, contactOps: false, delete: true, rename: true, archive: true, visibility: true };

test("a chat's details list each person in full, and offer only the tabs the reader can use", async () => {
    const { ChatDetails } = await import("../src/channels/chat-details.tsx");
    const view = await page();
    try {
        await view.draw(createElement(ChatDetails, { pod: pod as never, channel: detail as never, may: may as never, at: "members", onAt: () => {}, onClose: () => {}, onLeft: () => {} }));
        await view.flush();
        const doc = view.dom.window.document;
        const tabs = [...doc.querySelectorAll("[role=tab]")].map((one) => one.textContent);
        assert.deepEqual(tabs, ["About", "Members2", "Agents1", "Settings"]);
        const sam = [...doc.querySelectorAll(".cdet__name")].find((one) => one.textContent?.startsWith("Sam"));
        assert.equal(sam?.getAttribute("title"), "Sam Okonkwo-Fairweather");
        assert.ok([...doc.querySelectorAll("button")].some((one) => one.textContent === "Add people"), "whoever may change it can add people");
    } finally {
        await view.close();
    }
});

test("an answered question says what was answered", async () => {
    const { LineView } = await import("../src/channels/line.tsx");
    const view = await page();
    const asked = {
        id: "q1", seq: 3, sender: { kind: "AGENT", id: "agent-bolt", name: "Bolt" }, onBehalfOf: null, text: "Tea or coffee?", createdAt: "", editedAt: null,
        deleted: false, withheld: false, membersOnly: false, sourceId: null, threadRootId: null, replyCount: 0, parts: [], clientMessageId: null, delivered: null,
        interaction: {
            kind: "ASK", state: "ANSWERED", question: {}, awaits: { kind: "MEMBER", id: "me", name: "Me" },
            /* As the API stores an agent's ask_user answer. */
            answer: { answers: { Drink: "Coffee" } }, answeredBy: { kind: "MEMBER", id: "me", name: "Me" }, answeredAt: "",
        },
    };
    try {
        await view.draw(createElement("ol", null, createElement(LineView, { podId: "pod", channel: detail as never, line: asked as never })));
        assert.match(view.body(), /Answered by you: Coffee/);
    } finally {
        await view.close();
    }
});

test("who may change a group switches whether agents' answers are shown to everyone there", async () => {
    const { ChatDetails } = await import("../src/channels/chat-details.tsx");
    const view = await page();
    const asked: unknown[] = [];
    api.update = ((channelId: string, body: unknown) => {
        asked.push({ channelId, body });
        return Promise.resolve({ ...detail, answersShared: false });
    }) as never;
    try {
        await view.draw(createElement(ChatDetails, { pod: pod as never, channel: { ...detail, answersShared: true } as never, may: may as never, at: "about", onAt: () => {}, onClose: () => {}, onLeft: () => {} }));
        await view.flush();
        const doc = view.dom.window.document;
        const toggle = doc.querySelector<HTMLInputElement>("input[role=switch]");
        assert.ok(toggle, "a switch is offered");
        assert.equal(toggle.checked, true, "on by default: everyone reads the answers");
        assert.match(nameOf(toggle), /^Show agents’ answers to everyone here/);
        assert.match(view.body(), /Everyone here reads what an agent answers/);
        await act(async () => { toggle.click(); });
        await view.flush();
        assert.deepEqual(asked, [{ channelId: "c1", body: { answersShared: false } }]);
    } finally {
        delete api.update;
        await view.close();
    }
});

test("somebody who may not change a group is told it keeps answers to the asker, not offered a switch", async () => {
    const { ChatDetails } = await import("../src/channels/chat-details.tsx");
    const view = await page();
    try {
        await view.draw(createElement(ChatDetails, { pod: pod as never, channel: { ...detail, answersShared: false } as never, may: { ...may, manage: false } as never, at: "about", onAt: () => {}, onClose: () => {}, onLeft: () => {} }));
        await view.flush();
        assert.equal(view.dom.window.document.querySelector("input[role=switch]"), null);
        assert.match(view.body(), /Keep an answer to whoever asked until they share it/);
    } finally {
        await view.close();
    }
});
