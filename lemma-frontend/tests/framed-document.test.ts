import test from "node:test";
import assert from "node:assert/strict";
import { runInNewContext } from "node:vm";
import { framedDocument } from "../src/thread/framed-document.ts";

test("missing inline HTML leaves the frame source unset", () => {
    assert.equal(framedDocument(undefined, "frame"), undefined);
});

test("frame IDs remain data inside the bridge script and round-trip unchanged", (context) => {
    for (const [name, value] of Object.entries({
        document: { documentElement: { dataset: { theme: "light" } }, body: {} },
        getComputedStyle: () => ({ getPropertyValue: () => "", fontFamily: "sans-serif" }),
    })) {
        const previous = Object.getOwnPropertyDescriptor(globalThis, name);
        Object.defineProperty(globalThis, name, { value, configurable: true });
        context.after(() => {
            if (previous) Object.defineProperty(globalThis, name, previous);
            else Reflect.deleteProperty(globalThis, name);
        });
    }
    for (const id of ["frame-1", '</script><script>throw new Error("injected")</script>', '<!--</ScRiPt>\u2028\u2029"\\']) {
        const html = framedDocument("<p>Report</p>", id)!;
        const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/gi)];
        assert.equal(scripts.length, 1);
        assert.equal((html.match(/<\/script>/gi) ?? []).length, 1);
        const messages: Array<{ id: string; height: number }> = [];
        runInNewContext(scripts[0][1], {
            document: { body: { scrollHeight: 120 }, documentElement: { scrollHeight: 100 } },
            parent: { postMessage: (message: { id: string; height: number }) => messages.push(message) },
            ResizeObserver: class { observe() {} },
            addEventListener() {},
            window: {},
        });
        assert.deepEqual(messages.map(message => [message.id, message.height]), [[id, 120]]);
        assert.ok(html.includes("<p>Report</p>"));
    }
});
