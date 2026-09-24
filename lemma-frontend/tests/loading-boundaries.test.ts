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
const { PageLoading } = await import("../src/ui/loading.tsx");

test("page transitions announce their purpose without implying a conversation exists", () => {
    for (const label of ["Opening sign in", "Checking your session", "Opening sample workspace"]) {
        const html = renderToStaticMarkup(createElement(PageLoading, { label }));
        assert.match(html, /role="status"/);
        assert.ok(html.includes(label));
        assert.doesNotMatch(html, /workspace-loading|loading-shape|Loading conversation/);
    }
});

test("public and pre-session boundaries do not depend on the workspace skeleton", () => {
    for (const path of [
        "auth/portal.tsx", "app/auth/portal-host.tsx", "app/connect/page.tsx",
        "app/sign-in-to-site/sign-in-host.tsx", "app/t/workspace.tsx",
        "session/session.tsx", "marketing/workspace-preview.tsx", "app/(marketing)/hero.tsx",
    ]) {
        const source = readFileSync(new URL("../src/" + path, import.meta.url), "utf8");
        assert.doesNotMatch(source, /WorkspaceLoading|workspace-loading/, path);
    }
});
