import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { JSDOM } from "jsdom";
import { act, createElement } from "react";

/** A row that links to another table's row is filled in by picking that row
 *  by what it is called. It used to be a box for the other row's id, which
 *  nobody filling in a row knows. The other table's rows are put in the
 *  cache under the key the picker reads, so nothing here touches a server. */

registerHooks({
    load(url, context, nextLoad) {
        if (!url.endsWith(".tsx")) return nextLoad(url, context);
        return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
            compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
        }).outputText };
    },
});

const CONTACTS = [
    { id: "c1", name: "Northwind", created_at: "2026-10-01T09:00:00Z" },
    { id: "c2", name: "Globex", created_at: "2026-10-02T09:00:00Z" },
];

async function mount(t: { after: (done: () => void | Promise<void>) => void }, value: string | null, next: string | null) {
    const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { pretendToBeVisual: true });
    for (const [key, one] of Object.entries({
        window: dom.window, document: dom.window.document, navigator: dom.window.navigator,
        HTMLElement: dom.window.HTMLElement, Node: dom.window.Node,
        IS_REACT_ACT_ENVIRONMENT: true,
    })) {
        Object.defineProperty(globalThis, key, { value: one, configurable: true, writable: true });
    }
    const { createRoot } = await import("react-dom/client");
    const { QueryClient, QueryClientProvider } = await import("@tanstack/react-query");
    const { LinkPicker } = await import("../src/library/record-editor.tsx");
    const client = new QueryClient();
    client.setQueryData(["table", "p1", "contacts", "linkable"], { items: CONTACTS, next });
    const picked: unknown[] = [];
    const container = dom.window.document.getElementById("root")!;
    const root = createRoot(container);
    await act(async () => {
        root.render(createElement(QueryClientProvider, { client }, createElement(LinkPicker, {
            id: "rf-contact_id",
            podId: "p1",
            reference: { table: "contacts", column: "id" },
            value,
            onChange: (next: unknown) => { picked.push(next); },
        })));
    });
    /* Unmounted, the cache cleared and the window closed: a cache keeps a
       timer for every query it holds, and an open window keeps its own, and
       either one held the test process open for a minute after it passed. */
    t.after(async () => {
        await act(async () => { root.unmount(); });
        client.clear();
        dom.window.close();
    });
    return { dom, container, picked };
}

test("a link to another table's row is picked by that row's name, and stores its id", async (t) => {
    const view = await mount(t, "c2", null);
    const select = view.container.querySelector("select")!;
    assert.ok(select, "a list to pick from, not a box for an id");
    const options = Array.from(select.querySelectorAll("option")).map((one) => [one.value, one.textContent]);
    assert.deepEqual(options, [["", "—"], ["c1", "Northwind"], ["c2", "Globex"]]);
    assert.equal(select.value, "c2", "the row it links to now is the one picked");

    await act(async () => {
        select.value = "c1";
        select.dispatchEvent(new view.dom.window.Event("change", { bubbles: true }));
    });
    assert.deepEqual(view.picked, ["c1"], "what is stored is the id");
    assert.equal(view.container.querySelector("button"), null, "every row is listed, so there is nothing to type");
});

test("a table longer than one page still takes a typed id", async (t) => {
    const view = await mount(t, null, "page-2");
    const more = view.container.querySelector("button")!;
    assert.equal(more?.textContent, "Not listed? Type its id");
    await act(async () => { more.click(); });
    assert.ok(view.container.querySelector("input[type=text]"), "the box is there for a row not listed");
});
