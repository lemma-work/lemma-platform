import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { Interaction } from "../src/thread/turns.ts";

registerHooks({
    load(url, context, nextLoad) {
        if (!url.endsWith(".tsx")) return nextLoad(url, context);
        return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
            compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
        }).outputText };
    },
});
const { InteractionCard } = await import("../src/thread/interaction-card.tsx");
const interaction: Interaction = {
    id: "approval-example", kind: "approval", open: false, decision: "APPROVE_ONCE", answers: {}, questions: [], at: "",
    details: { title: "Publish the sample app", request: "Publishing requires your approval.", toolName: "exec_command",
        params: [{ name: "Command", value: "lemma apps deploy sample --yes" }], canApproveForSession: true },
};
const render = (overrides: Partial<Interaction> = {}) => renderToStaticMarkup(createElement(InteractionCard, { interaction: { ...interaction, ...overrides }, teammate: "Teammate" }));

test("approved cards keep the action and scope visible with details collapsed", () => {
    for (const decision of ["APPROVE_ONCE", "APPROVE_FOR_SESSION"]) {
        const html = render({ decision });
        assert.match(html, /approval--compact/);
        assert.match(html, /<details class="approval__record"><summary/);
        assert.doesNotMatch(html, /<details[^>]*\sopen(?:=|\s|>)/);
        const summary = html.match(/<summary[^>]*>(.*?)<\/summary>/s)?.[1] ?? "";
        assert.match(summary, /Publish the sample app/);
        assert.match(summary, decision === "APPROVE_ONCE" ? /Approved once/ : /Approved for this conversation/);
        assert.doesNotMatch(summary, /Publishing requires|lemma apps deploy/);
        assert.match(html, /lemma apps deploy sample --yes/);
        assert.doesNotMatch(html, /<button/);
    }
});

test("pending approvals keep their details and decision controls visible", () => {
    const html = render({ open: true, decision: "" });
    assert.doesNotMatch(html, /<details|approval--compact/);
    assert.match(html, /Publishing requires your approval/);
    assert.match(html, /lemma apps deploy sample --yes/);
    assert.match(html, />Approve once<\/button>/);
});

test("denied requests and answered questions do not become approved rows", () => {
    assert.doesNotMatch(render({ decision: "DENY" }), /approval--compact/);
    assert.doesNotMatch(render({ kind: "question" }), /approval--compact/);
});
