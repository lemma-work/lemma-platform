import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/** The editor writes a page because something in it changed, not because it
 *  had focus when it did. "Show view" on a live view, "Show it" on an HTML
 *  block and a to-do's tick all change the page from outside the caret, and
 *  each was dropped unless somebody went on to type. A read is still not a
 *  change: opening a file, or reading one in from elsewhere, writes nothing. */

const writes: string[] = [];

registerHooks({
    load(url, context, nextLoad) {
        let source: string | undefined;
        if (url.endsWith("/src/data/index.ts")) {
            source = `export const source = {
                label: "sample",
                async writeFile(_podId, _path, text) { globalThis.__writes.push(text); },
                async readFile() { throw new Error("not in this test"); },
            };`;
        }
        if (url.endsWith("/src/session/client.ts")) source = 'export const lemma = () => { throw new Error("no network in this test"); };';
        /* The HTML block's sandboxed frame: what it draws is not what this checks. */
        if (url.endsWith("/src/thread/embed-preview.tsx")) source = "export function EmbedPreview() { return null; }";
        if (source !== undefined) return { format: "module", shortCircuit: true, source };
        if (!url.endsWith(".tsx")) return nextLoad(url, context);
        return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
            compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
        }).outputText };
    },
});

const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { pretendToBeVisual: true });
const { window } = dom;
/* What ProseMirror measures and jsdom does not lay out. */
const noRects = () => Object.assign([], { item: () => null });
const zeroRect = () => ({ x: 0, y: 0, top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0, toJSON() {} });
window.Range.prototype.getClientRects = noRects as never;
window.Range.prototype.getBoundingClientRect = zeroRect as never;
window.Element.prototype.getClientRects = noRects as never;
window.document.elementFromPoint = () => null;
for (const [key, value] of Object.entries({
    window, document: window.document, navigator: window.navigator,
    HTMLElement: window.HTMLElement, Element: window.Element, Node: window.Node, Text: window.Text,
    MutationObserver: window.MutationObserver, DOMParser: window.DOMParser, getSelection: window.getSelection.bind(window),
    getComputedStyle: window.getComputedStyle.bind(window),
    requestAnimationFrame: window.requestAnimationFrame.bind(window), cancelAnimationFrame: window.cancelAnimationFrame.bind(window),
    IS_REACT_ACT_ENVIRONMENT: true,
    __writes: writes,
})) {
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
}

const { createRoot } = await import("react-dom/client");
const { DocumentEditor } = await import("../src/thread/document-editor.tsx");
const { PageToolsContext } = await import("../src/docpages/page-context.tsx");
const { SAVE_AFTER_MS } = await import("../src/thread/document-save.ts");

type Shown = { text: string; page?: boolean };

/** One editor on a page of its own; `show` hands it the file as it now is. */
async function mount(first: Shown) {
    writes.length = 0;
    const host = window.document.createElement("div");
    window.document.body.append(host);
    const root = createRoot(host);
    const cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const tools = { podId: "pod", path: "/pages/notes.md", botName: "the bot", openFile: () => {}, sendToBot: null, comments: null };
    const show = ({ text, page }: Shown) => act(async () => {
        const editor = createElement(DocumentEditor, { podId: "pod", path: "/pages/notes.md", text });
        root.render(createElement(QueryClientProvider, { client: cache },
            page ? createElement(PageToolsContext.Provider, { value: tools }, editor) : editor));
    });
    await show(first);
    return {
        host,
        show,
        /** Past the autosave's pause, with whatever it wrote in `writes`. */
        settle: () => act(async () => { await new Promise((done) => setTimeout(done, SAVE_AFTER_MS + 150)); }),
        unmount: async () => { await act(async () => { root.unmount(); }); host.remove(); },
    };
}

/** Typed into a React-controlled field the way a person does. */
function type(field: HTMLTextAreaElement, value: string) {
    Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")!.set!.call(field, value);
    field.dispatchEvent(new window.Event("input", { bubbles: true }));
}

function button(host: HTMLElement, label: string): HTMLButtonElement {
    const found = [...host.querySelectorAll("button")].find((one) => one.textContent?.trim() === label);
    assert.ok(found, `no "${label}" button`);
    return found;
}

test("opening a file, or reading one in from elsewhere, writes nothing", async () => {
    const editor = await mount({ text: "# Notes\n\n* one\n* two\n" });
    try {
        await editor.settle();
        assert.deepEqual(writes, []);
        /* With the caret in it, too: a read used to count as an edit then. */
        await act(async () => { (editor.host.querySelector(".ProseMirror") as HTMLElement).focus(); });
        await editor.show({ text: "# Notes\n\n* one\n* two\n* three\n" });
        await editor.settle();
        assert.equal(editor.host.querySelectorAll("li").length, 3);
        assert.deepEqual(writes, []);
    } finally {
        await editor.unmount();
    }
});

test("a to-do ticked without the caret in the page is written", async () => {
    const editor = await mount({ text: "# Notes\n\n- [ ] call back\n" });
    try {
        const box = editor.host.querySelector<HTMLInputElement>("input[type=checkbox]");
        assert.ok(box);
        await act(async () => { box.click(); });
        await editor.settle();
        assert.equal(writes.length, 1);
        assert.match(writes[0], /- \[x\] call back/);
    } finally {
        await editor.unmount();
    }
});

test("\"Show it\" on an HTML block is written without anybody typing after", async () => {
    const editor = await mount({ text: "# Notes\n\n```lemma-widget\n<p>old</p>\n```\n", page: true });
    try {
        await act(async () => { button(editor.host, "Edit HTML").click(); });
        const field = editor.host.querySelector<HTMLTextAreaElement>(".pblock__editor textarea");
        assert.ok(field);
        await act(async () => { field.focus(); type(field, "<p>new</p>"); });
        await act(async () => { button(editor.host, "Show it").click(); });
        await editor.settle();
        assert.equal(writes.length, 1);
        assert.match(writes[0], /```lemma-widget\n<p>new<\/p>\n```/);
    } finally {
        await editor.unmount();
    }
});
