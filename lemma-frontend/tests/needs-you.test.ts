import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

registerHooks({
    load(url, context, nextLoad) {
        if (url.endsWith(".css")) return { format: "module", shortCircuit: true, source: "export default {};" };
        if (!url.endsWith(".tsx")) return nextLoad(url, context);
        return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
            compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
        }).outputText };
    },
});
const { NeedsYou } = await import("../src/outcomes/needs.tsx");
const render = (failed: boolean) => renderToStaticMarkup(createElement(NeedsYou, {
    pods: [], queue: [], loading: false, failed, onRetry: () => undefined, go: () => undefined,
}));

test("a failed read of what waits says so, and is never 'nothing needs you'", () => {
    const html = render(true);
    assert.match(html, /Couldn’t read what’s waiting on you\./);
    assert.match(html, /Try again/);
    assert.doesNotMatch(html, /Nothing needs you|All clear here/);
});

test("an empty read is nothing waiting", () => {
    const html = render(false);
    assert.match(html, /Nothing needs you/);
    assert.doesNotMatch(html, /Couldn’t read/);
});
