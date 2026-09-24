import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { BrowserTransport } from "./browser-transport-fixture.ts";

const fixture = new URL("./browser-transport-fixture.ts", import.meta.url).href;
registerHooks({ load(url, context, nextLoad) {
    let source: string | undefined;
    if (url.endsWith("/src/session/client.ts")) source = 'export const apiUrl = () => "https://example.test"; export const sessionToken = () => null;';
    if (url.endsWith("/src/computer/queries.ts")) source = 'export const useBrowserResize = () => ({ mutate() {} });';
    if (url.endsWith("/@novnc/novnc/core/rfb.js")) source = `export { BrowserTransport as default } from ${JSON.stringify(fixture)};`;
    if (source !== undefined) return { format: "module", shortCircuit: true, source };
    if (!url.endsWith(".tsx")) return nextLoad(url, context);
    return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
    }).outputText };
} });

const { LiveScreen } = await import("../src/computer/live-screen.tsx");

test("connecting, focusing and replacing the browser never removes React's hint", async () => {
    const dom = new JSDOM('<div id="root"></div>');
    const saved = new Map<string, PropertyDescriptor | undefined>();
    for (const [key, value] of Object.entries({
        window: dom.window, document: dom.window.document,
        IS_REACT_ACT_ENVIRONMENT: true,
        WebSocket: class { addEventListener() {} },
    })) {
        saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
        Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
    }
    const container = dom.window.document.getElementById("root")!;
    const root = createRoot(container);
    const errors: unknown[] = [];
    dom.window.addEventListener("error", event => { errors.push(event.error); event.preventDefault(); });
    const render = (reopen: number) => createElement(LiveScreen, { mode: "control", autoResize: false, reopen, onState: () => {} });
    try {
        await act(async () => { root.render(render(0)); });
        const first = BrowserTransport.clients.at(-1)!;
        await act(async () => { first.emit("connect"); });
        assert.ok(container.querySelector(".screen-hint"), "connecting must preserve the React-owned hint");
        await act(async () => { first.canvas.focus(); });
        assert.equal(container.querySelector(".screen-hint"), null);
        await act(async () => { first.canvas.blur(); });
        assert.ok(container.querySelector(".screen-hint"));
        await act(async () => { root.render(render(1)); });
        const second = BrowserTransport.clients.at(-1)!;
        assert.notEqual(first, second);
        await act(async () => { second.emit("connect"); });
        assert.equal(container.querySelectorAll("canvas").length, 1);
        assert.ok(container.querySelector(".screen-hint"));
        await act(async () => { second.canvas.focus(); });
        assert.equal(container.querySelector(".screen-hint"), null);
        assert.deepEqual(errors, []);
    } finally {
        await act(async () => { root.unmount(); });
        dom.window.close();
        for (const [key, descriptor] of saved) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else Reflect.deleteProperty(globalThis, key);
        }
    }
});
