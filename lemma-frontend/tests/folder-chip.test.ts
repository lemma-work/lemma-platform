import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { ConversationFolder } from "../src/desktop/folders.ts";

registerHooks({ load(url, context, nextLoad) {
    if (url.endsWith(".css")) return { format: "module", shortCircuit: true, source: "" };
    if (!url.endsWith(".tsx")) return nextLoad(url, context);
    return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
    }).outputText };
} });

const { FolderChip } = await import("../src/desktop/folder-chip.tsx");

test("the folder chip's buttons never submit the ask box they sit in", () => {
    const folder: ConversationFolder = { folder: "/Users/me/site", available: true, pendingId: "p", bind: async () => null, unbind: async () => undefined };
    const markup = renderToStaticMarkup(createElement(FolderChip, { folder }));
    const buttons = markup.match(/<button[^>]*>/g) ?? [];
    assert.equal(buttons.length, 2, "choose, and stop using the folder");
    for (const button of buttons) assert.match(button, /type="button"/, button);
});
