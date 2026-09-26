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
const render = (overrides: Partial<Interaction> = {}, runEnded = false) => renderToStaticMarkup(createElement(InteractionCard, { interaction: { ...interaction, ...overrides }, teammate: "Teammate", runEnded }));

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

const hostDetails = {
    ...interaction.details, hostPermission: true, canApproveForSession: false,
};

test("a coding agent's request says when it runs out, and offers only what it can keep", () => {
    const html = render({ open: true, decision: "", askedAtMs: Date.now(), details: hostDetails });
    assert.match(html, /Answer by/);
    assert.match(html, />Approve once<\/button>/);
    assert.doesNotMatch(html, /Approve for this conversation/);

    const labelled = render({
        open: true, decision: "", askedAtMs: Date.now(),
        details: { ...hostDetails, canApproveForSession: true, sessionLabel: "Always allow ls" },
    });
    assert.match(labelled, />Always allow ls<\/button>/);
});

test("a coding agent's request nobody is waiting for is expired, not answerable", () => {
    for (const html of [
        render({ open: true, decision: "", askedAtMs: Date.now() - 31 * 60_000, details: hostDetails }),
        render({ open: true, decision: "", askedAtMs: Date.now(), details: hostDetails }, true),
    ]) {
        assert.match(html, /Expired — the agent continued without it/);
        assert.doesNotMatch(html, /<button/);
        assert.match(html, /data-state="expired"/);
    }
    /* An ordinary approval is not a coding agent's, and does not expire. */
    assert.match(render({ open: true, decision: "" }, true), />Approve once<\/button>/);
});
