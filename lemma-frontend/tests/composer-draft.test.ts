import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { JSDOM } from "jsdom";
import { key } from "../src/session/storage.ts";

registerHooks({
    load(url, context, nextLoad) {
        if (!url.endsWith(".tsx")) return nextLoad(url, context);
        return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
            compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
        }).outputText };
    },
});

const dom = new JSDOM('<div id="root"></div>', { url: "https://example.test/t/space" });
for (const [name, value] of Object.entries({
    window: dom.window,
    document: dom.window.document,
    navigator: dom.window.navigator,
    localStorage: dom.window.localStorage,
    IS_REACT_ACT_ENVIRONMENT: true,
    ResizeObserver: class { observe() {} unobserve() {} disconnect() {} },
})) {
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
}

const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { Composer } = await import("../src/thread/composer.tsx");

function drafts(): Record<string, string> {
    return JSON.parse(dom.window.localStorage.getItem(key("drafts")) ?? "{}") as Record<string, string>;
}

function seed(entries: Record<string, string>): void {
    dom.window.localStorage.setItem(key("drafts"), JSON.stringify(entries));
}

/** A mounted composer, and the two things a test does to it. */
async function mount(draftKey: string | null, onSend: (text: string) => void = () => undefined) {
    const container = dom.window.document.createElement("div");
    dom.window.document.body.append(container);
    const root = createRoot(container);
    const draw = async (next: string | null) => {
        await act(async () => {
            root.render(createElement(Composer, {
                placeholder: "Ask", busy: false, canStop: false, draftKey: next, onSend,
            }));
        });
    };
    await draw(draftKey);
    const box = () => container.querySelector("textarea") as HTMLTextAreaElement;
    const setter = Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, "value")!.set!;
    return {
        draw,
        value: () => box().value,
        async type(text: string) {
            await act(async () => {
                setter.call(box(), text);
                box().dispatchEvent(new dom.window.Event("input", { bubbles: true }));
            });
        },
        async send() {
            const button = container.querySelector('[aria-label="Send message"]') as HTMLButtonElement;
            await act(async () => { button.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true })); });
        },
        async unmount() {
            await act(async () => { root.unmount(); });
        },
        async settle(ms = 400) {
            await act(async () => { await new Promise(done => setTimeout(done, ms)); });
        },
    };
}

test("a conversation's kept draft is in the box when the pane opens", async () => {
    seed({ "pod:A": "half-written" });
    const pane = await mount("pod:A");
    assert.equal(pane.value(), "half-written");
    await pane.settle();
    assert.equal(drafts()["pod:A"], "half-written", "showing it must not delete it");
    await pane.unmount();
});

test("a conversation arriving after the box does shows the draft it kept", async () => {
    seed({ "pod:A": "half-written" });
    const pane = await mount("pod:new");
    assert.equal(pane.value(), "");
    await pane.draw("pod:A");
    assert.equal(pane.value(), "half-written");
    assert.equal(drafts()["pod:A"], "half-written");
    await pane.unmount();
});

test("words typed before the conversation exists move with it", async () => {
    const pane = await mount("pod:new");
    await pane.type("before you existed");
    await pane.settle();
    assert.equal(drafts()["pod:new"], "before you existed");
    await pane.draw("pod:A");
    assert.equal(pane.value(), "before you existed");
    await pane.settle();
    assert.equal(drafts()["pod:A"], "before you existed");
    assert.equal(drafts()["pod:new"], undefined, "the stand-in entry goes with the words");
    await pane.unmount();
});

test("an empty box does not delete what the conversation kept", async () => {
    seed({ "pod:A": "kept" });
    const pane = await mount("pod:new");
    await pane.draw("pod:A");
    await pane.settle();
    assert.equal(pane.value(), "kept");
    assert.equal(drafts()["pod:A"], "kept", "opening a conversation must not throw its draft away");
    await pane.unmount();
});

test("typing is written down after a pause and when the box goes", async () => {
    const pane = await mount("pod:A");
    await pane.type("typed");
    await pane.settle();
    assert.equal(drafts()["pod:A"], "typed");
    await pane.type("typed more");
    await pane.unmount();
    assert.equal(drafts()["pod:A"], "typed more", "the last keystrokes are not lost with the box");
});

test("emptying the box takes the draft with it", async () => {
    seed({ "pod:A": "kept" });
    const pane = await mount("pod:A");
    await pane.type("");
    await pane.settle();
    assert.equal(drafts()["pod:A"], undefined);
    await pane.unmount();
});

test("sending clears the draft rather than keeping it", async () => {
    const pane = await mount("pod:A");
    await pane.type("send me");
    await pane.settle();
    assert.equal(drafts()["pod:A"], "send me");
    await pane.send();
    await pane.settle();
    assert.equal(drafts()["pod:A"], undefined);
    await pane.unmount();
});
