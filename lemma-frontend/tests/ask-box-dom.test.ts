import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { JSDOM } from "jsdom";
import { act, createElement } from "react";

/** The box on Home and on a teammate's page starts a conversation rather than
 *  holding one — it hands the words to the Chat tab, which creates the
 *  conversation and sends them. Files have to go the same way, and the box is
 *  where they are chosen: a box with no attach button and no drop or paste
 *  handling is a first message that cannot carry anything.
 *
 *  What it hands over is what this checks. The upload itself is the Chat
 *  tab's, because there is no `pod_cwd` to put a file in until the
 *  conversation exists. */

registerHooks({
    load(url, context, nextLoad) {
        if (!url.endsWith(".tsx")) return nextLoad(url, context);
        return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
            compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
        }).outputText };
    },
});

/* jsdom's File takes its size from its parts, and a 100 MB part is 100 MB of
   memory for a test about a refusal; the getter is shadowed instead. */
function file(name: string, size = 10): File {
    const made = new File([new Uint8Array(1)], name);
    Object.defineProperty(made, "size", { value: size });
    return made;
}

const MAX_BYTES = 100 * 1024 * 1024;

type Asked = { text: string; files: string[] };

/** A box on a page of its own, with what it hands over kept in `asked`. */
async function mount() {
    const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', { pretendToBeVisual: true });
    const saved = new Map<string, PropertyDescriptor | undefined>();
    for (const [key, value] of Object.entries({
        window: dom.window, document: dom.window.document, navigator: dom.window.navigator,
        HTMLElement: dom.window.HTMLElement, Node: dom.window.Node,
        IS_REACT_ACT_ENVIRONMENT: true,
    })) {
        saved.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
        Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
    }
    /* After the page exists: React DOM decides how to hear events when it
       loads, and loaded without a document it listens the way old browsers
       needed. */
    const { createRoot } = await import("react-dom/client");
    const { AskBox } = await import("../src/chat/ask-box.tsx");
    const asked: Asked[] = [];
    const container = dom.window.document.getElementById("root")!;
    const root = createRoot(container);
    await act(async () => {
        root.render(createElement(AskBox, {
            placeholder: "Ask Teammate…",
            onAsk: (text: string, files: File[]) => { asked.push({ text, files: files.map((one) => one.name) }); },
        }));
    });
    const form = () => container.querySelector("form")!;
    const box = () => container.querySelector("textarea")!;
    const chips = () => Array.from(container.querySelectorAll(".askbox__files .attached__name")).map((one) => one.textContent);
    const note = () => container.querySelector(".askbox__note")?.textContent ?? null;
    const hand = (kind: "drop" | "paste", carried: File[]) => {
        const event = new dom.window.Event(kind, { bubbles: true, cancelable: true });
        Object.defineProperty(event, "dataTransfer", { value: { types: ["Files"], files: carried } });
        Object.defineProperty(event, "clipboardData", { value: { types: ["Files"], files: carried } });
        /* A drop lands on the box; a paste lands in the field the person is
           typing in, which is where the listener is. */
        const on = kind === "drop" ? form() : box();
        return act(async () => { on.dispatchEvent(event); });
    };
    const type = (words: string) => act(async () => {
        const setValue = Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype, "value")!.set!;
        setValue.call(box(), words);
        box().dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    });
    const send = () => act(async () => { (form() as HTMLFormElement).requestSubmit(); });
    return { dom, container, asked, form, box, chips, note, hand, type, send };
}

test("the box that starts a conversation offers to attach a file", async () => {
    const box = await mount();

    const attach = box.container.querySelector("button.askbox__attach");
    assert.ok(attach, "a box that can send a first message must offer to attach one to it");
    assert.equal(attach.getAttribute("aria-label"), "Attach a file");
    assert.ok(box.container.querySelector("input[type=file]"), "the button opens a picker");
});

test("a file dropped on the box goes with the words to the Chat tab", async () => {
    const box = await mount();

    await box.hand("drop", [file("brief.pdf")]);
    assert.deepEqual(box.chips(), ["brief.pdf"], "a dropped file is listed, so it can be seen and taken off");
    assert.equal(box.asked.length, 0, "dropping a file does not send it");

    await box.type("read this and tell me what it says");
    await box.send();

    assert.deepEqual(box.asked, [{ text: "read this and tell me what it says", files: ["brief.pdf"] }]);
});

test("a screenshot pasted into the box is attached like a dropped one", async () => {
    const box = await mount();

    await box.hand("paste", [file("shot.png", 2048)]);

    assert.deepEqual(box.chips(), ["shot.png"]);
});

test("a file with nothing typed still goes, and a file too large never does", async () => {
    const box = await mount();

    await box.hand("drop", [file("huge.bin", MAX_BYTES + 1)]);
    assert.deepEqual(box.chips(), [], "a refused file is not listed as if it were going");
    assert.equal(box.note(), "huge.bin is too large to attach (100 MB).");

    await box.hand("drop", [file("brief.pdf")]);
    await box.send();

    assert.deepEqual(box.asked, [{ text: "", files: ["brief.pdf"] }], "here, look at this — with nothing typed");
});
