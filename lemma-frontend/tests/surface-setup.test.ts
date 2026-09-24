import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

registerHooks({ load(url, context, nextLoad) {
    if (!url.endsWith(".tsx")) return nextLoad(url, context);
    return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
        compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
    }).outputText };
} });
const { SetupActions } = await import("../src/shell/surface-setup.tsx");

test("setup instructions show webhook URLs while verify tokens stay masked", () => {
    const html = renderToStaticMarkup(createElement(SetupActions, { actions: [{
        key: "webhook", title: "Connect the webhook", description: "Paste the callback in your provider dashboard.",
        link: "https://example.com/dashboard", link_label: "Open provider", steps: ["Subscribe to messages"],
        fields: [{ label: "Callback", value: "https://example.com/callback" }, { label: "Verify token", value: "test-only-secret", secret: true }],
    }] }));
    assert.match(html, /Subscribe to messages/);
    assert.match(html, /https:\/\/example.com\/callback/);
    assert.match(html, /Reveal Verify token/);
    assert.match(html, /Copy Verify token/);
    assert.doesNotMatch(html, /test-only-secret/);
});
