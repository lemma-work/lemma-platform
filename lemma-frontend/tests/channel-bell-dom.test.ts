import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { JSDOM } from "jsdom";
import { act, createElement, createRef } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { readInbox } from "../src/channels/waiting.ts";

/** The bell, drawn: a mention or a question in a thread opens that thread,
 *  at that reply -- not the channel's timeline, where the reply is not. */

const opened: { podId: string; channelId: string; focus: { lineId: string; threadRootId: string | null } }[] = [];
(globalThis as { __opened?: unknown }).__opened = opened;

registerHooks({ load(url, context, nextLoad) {
    if (url.endsWith("/src/channels/focus.ts")) {
        return { format: "module", shortCircuit: true, source: "export const openAtLine = (podId, channelId, focus) => globalThis.__opened.push({ podId, channelId, focus });" };
    }
    if (url.endsWith("/src/thread/markdown.tsx")) {
        return { format: "module", shortCircuit: true, source: 'import { createElement } from "react"; export const Prose = ({ text }) => createElement("p", null, text);' };
    }
    if (url.endsWith("/src/channels/api.ts")) {
        return { format: "module", shortCircuit: true, source: "export const channelApi = () => ({});" };
    }
    if (!url.endsWith(".tsx")) return nextLoad(url, context);
    return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
    }).outputText };
} });

test("a mention or a question in a thread opens that thread at that reply", async () => {
    const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { pretendToBeVisual: true });
    for (const [key, value] of Object.entries({
        window: dom.window, document: dom.window.document, navigator: dom.window.navigator,
        HTMLElement: dom.window.HTMLElement, Node: dom.window.Node, IS_REACT_ACT_ENVIRONMENT: true,
        ResizeObserver: class { observe() {} disconnect() {} unobserve() {} },
    })) Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
    const { InboxPanel } = await import("../src/channels/waiting-panel.tsx");
    const { createRoot } = await import("react-dom/client");
    const root = createRoot(dom.window.document.getElementById("root")!);
    const cache = new QueryClient();
    const inbox = readInbox({
        totals: { channels: 1, mentions: 2, waiting: 0 },
        mentions: [
            { channel: { id: "c2", kind: "GROUP", title: "launch" }, message: { id: "m-reply", seq: 12, thread_root_id: "root-1", sender: { name: "Priya" }, text: "@Dana in the thread", created_at: "x" } },
            { channel: { id: "c2", kind: "GROUP", title: "launch" }, message: { id: "m-top", seq: 13, sender: { name: "Sam" }, text: "@Dana on top", created_at: "x" } },
        ],
    });
    try {
        await act(async () => {
            root.render(createElement(QueryClientProvider, { client: cache }, createElement(InboxPanel, {
                podId: "pod", inbox: { data: inbox, isPending: false, isError: false, refetch: async () => {} } as never,
                mine: null, at: { top: 0, right: 0 }, panel: createRef<HTMLDivElement>(), onClose: () => {},
            })));
        });
        const buttons = [...dom.window.document.querySelectorAll<HTMLButtonElement>(".notify__mentions button")];
        await act(async () => { buttons[0].click(); });
        await act(async () => { buttons[1].click(); });
        assert.deepEqual(opened, [
            { podId: "pod", channelId: "c2", focus: { lineId: "m-reply", threadRootId: "root-1" } },
            { podId: "pod", channelId: "c2", focus: { lineId: "m-top", threadRootId: null } },
        ]);
    } finally {
        await act(async () => { root.unmount(); });
        cache.clear();
        dom.window.close();
    }
});
