import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { JSDOM } from "jsdom";
import { act, createElement } from "react";

/** An app framed in a Lemma may take the whole screen: a deck's present mode
 *  asks for it, and a frame without `allow="fullscreen"` refuses silently. */

registerHooks({
    load(url, context, nextLoad) {
        if (url.endsWith(".css")) return { format: "module", shortCircuit: true, source: "" };
        if (!url.endsWith(".tsx")) return nextLoad(url, context);
        return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
            compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
        }).outputText };
    },
});

test("a framed app may ask for the whole screen", async () => {
    const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { pretendToBeVisual: true });
    for (const [key, value] of Object.entries({
        window: dom.window, document: dom.window.document, navigator: dom.window.navigator,
        HTMLElement: dom.window.HTMLElement, Node: dom.window.Node, IS_REACT_ACT_ENVIRONMENT: true,
    })) {
        Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
    }
    const { createRoot } = await import("react-dom/client");
    const { AppFrameView } = await import("../src/desktop/app-frame.tsx");
    const container = dom.window.document.getElementById("root")!;
    const root = createRoot(container);
    await act(async () => {
        root.render(createElement(AppFrameView, {
            url: "https://deck.apps.example.test/",
            frameRef: () => undefined,
            onFrameLoad: () => undefined,
        }));
    });
    const frame = container.querySelector("iframe");
    assert.ok(frame, "the app is framed on the web");
    assert.match(frame.getAttribute("allow") ?? "", /\bfullscreen\b/);
    assert.ok(frame.hasAttribute("allowfullscreen"));
    await act(async () => root.unmount());
});
