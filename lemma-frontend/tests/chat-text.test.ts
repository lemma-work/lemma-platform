import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import { parseChatTextSize, readChatTextSize } from "../src/session/chat-text.ts";

test("chat text falls back safely for missing, invalid, and inaccessible preferences", () => {
    for (const value of [null, "", "huge", "16", "default"]) assert.equal(parseChatTextSize(value), "default");
    for (const value of ["small", "large"] as const) {
        assert.equal(readChatTextSize({ getItem: () => value }, "size"), value);
    }
    assert.equal(readChatTextSize({ getItem: () => { throw new Error("Storage unavailable"); } }, "size"), "default");
});

test("the actual before-paint script restores chat size and isolates demo preferences", () => {
    const layout = readFileSync(new URL("../src/app/layout.tsx", import.meta.url), "utf8");
    const literal = layout.match(/const themeScript = (`[^`]+`);/)?.[1];
    assert.ok(literal);
    const script = runInNewContext(literal, { PREFIX: "lemma-app" }) as string;
    for (const pathname of ["/t", "/demo/landing", "/demo/launch/"]) {
        const dataset: Record<string, string> = {};
        const stored: Record<string, string> = { "lemma-app:chat-text-size": "large", "lemma-tour:chat-text-size": "small" };
        runInNewContext(script, { document: { documentElement: { dataset } }, location: { pathname }, localStorage: { getItem: (name: string) => stored[name] ?? null } });
        assert.equal(dataset.chatTextSize, pathname === "/t" ? "large" : "small");
    }
});
