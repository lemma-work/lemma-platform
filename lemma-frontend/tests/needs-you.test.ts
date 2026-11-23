import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

registerHooks({
    load(url, context, nextLoad) {
        if (url.endsWith(".css")) return { format: "module", shortCircuit: true, source: "export default {};" };
        if (!url.endsWith(".tsx")) return nextLoad(url, context);
        return { format: "module", shortCircuit: true, source: ts.transpileModule(readFileSync(new URL(url), "utf8"), {
            compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX },
        }).outputText };
    },
});
const { NeedsYou, needsLine } = await import("../src/outcomes/needs.tsx");
const { saidOf } = await import("../src/outcomes/answer.tsx");

const NOW = Date.now();
const HOUR = 3_600_000;

function pod(id: string, name: string) {
    return { id, orgId: "org", name, iconUrl: null, teammate: { name, initials: name[0], iconUrl: null }, subtitle: "", members: [], waiting: "" };
}
const PODS = [pod("p-sales", "Sales"), pod("p-support", "Support"), pod("p-hiring", "Hiring")];

function item(over: Record<string, unknown>) {
    return { key: "k", kind: "answer", podId: "p-sales", podName: "Sales", title: "A question", detail: null, sinceMs: NOW - HOUR, conversationId: null, runId: null, contactId: null, wait: null, line: null, ...over };
}
const QUEUE = [
    item({ key: "ask:refund", kind: "decide", podId: "p-support", podName: "Support", title: "Refund the second charge", detail: "Double charge", sinceMs: NOW - 30 * HOUR, conversationId: "c1" }),
    item({ key: "ask:day", kind: "answer", title: "Which day should the proposal go out?", detail: "Northwind brief", sinceMs: NOW - 3 * HOUR, conversationId: "c2" }),
    item({ key: "contact:kai", kind: "answer", podId: "p-support", podName: "Support", title: "Reply to Kai Novak", detail: "Asked for a person", sinceMs: NOW - HOUR / 2, contactId: "ch-kai" }),
];

const render = (props: Record<string, unknown>) => renderToStaticMarkup(createElement(QueryClientProvider, { client: new QueryClient() },
    createElement(NeedsYou, { pods: PODS, queue: [], loading: false, failed: false, onRetry: () => undefined, go: () => undefined, ...props })));

test("a failed read of what waits says so once in the header, and is never 'nothing needs you'", () => {
    const html = render({ failed: true });
    assert.match(html, /What waits on you couldn’t be read just now\./);
    assert.match(html, /Couldn’t read what waits on you/);
    assert.match(html, /That isn’t the same as nothing waiting\./);
    assert.match(html, /Try again/);
    assert.doesNotMatch(html, /Nothing needs you|Nothing waits on you/);
    assert.equal(html.match(/couldn’t be read/g)?.length, 1);
});

test("an empty read is nothing waiting, with the Lemmas' faces", () => {
    const html = render({});
    assert.match(html, /Nothing needs you/);
    assert.match(html, /Nothing waits on you in your 3 Lemmas\./);
    assert.match(html, /it comes here and to your chat with it/);
    assert.equal(html.match(/class="tface nface"/g)?.length, 3);
    assert.doesNotMatch(html, /Couldn’t read/);
});

test("while it is first read, the page holds its shape and claims nothing", () => {
    const html = render({ loading: true });
    assert.match(html, /aria-label="Reading what waits on you"/);
    assert.doesNotMatch(html, /Nothing needs you|Couldn’t read|things across/);
});

test("what waits is counted, grouped by what it asks, oldest first", () => {
    const html = render({ queue: QUEUE });
    assert.match(html, /3 things across 2 of 3 Lemmas, oldest first\./);
    /* A kind with nothing in it has no chip at all, rather than a dead one. */
    assert.match(html, /All<span>3<\/span>/);
    assert.match(html, /Decide<span>1<\/span>/);
    assert.match(html, /Answer<span>2<\/span>/);
    assert.doesNotMatch(html, /Do<span>/);
    assert.doesNotMatch(html, /disabled=""/);
    assert.match(html, /yes or no, from you/);
    assert.match(html, /questions, and people waiting on a person/);
    assert.doesNotMatch(html, /forms to fill in/);
    assert.match(html, /Answered here or where it was asked, it leaves this list either way\./);
    assert.ok(html.indexOf("Refund the second charge") < html.indexOf("Which day should"));
});

test("a row says whose it is, how long it has waited, and what doing it is called", () => {
    const html = render({ queue: QUEUE });
    assert.match(html, /Support · In Double charge/);
    assert.match(html, /Sales · In Northwind brief/);
    /* Past a day it is said warm. */
    assert.match(html, /class="nage nage--old" title="Waiting 30 h">30 h</);
    assert.match(html, /class="nage" title="Waiting 3 h">3 h</);
    assert.match(html, /aria-expanded="false"[^>]*>Decide</);
    assert.match(html, /aria-expanded="false"[^>]*>Answer</);
    /* A person waiting on a person is answered in their chat. */
    assert.match(html, /<a class="b b--s b--secondary nrow__go"[^>]*>Reply</);
});

test("the header's line", () => {
    assert.equal(needsLine({ pods: PODS, live: [], loading: false, failed: false }), "Nothing waits on you in your 3 Lemmas.");
    assert.equal(needsLine({ pods: [PODS[0]], live: [], loading: false, failed: false }), "Nothing waits on you in Sales.");
    assert.equal(needsLine({ pods: PODS, live: [], loading: true, failed: false }), null);
    assert.equal(needsLine({ pods: PODS, live: [], loading: false, failed: true }), "What waits on you couldn’t be read just now.");
    assert.equal(needsLine({ pods: PODS, live: QUEUE.slice(0, 1), loading: false, failed: false }), "1 thing across 1 of 3 Lemmas, oldest first.");
});

test("a row doesn't name its Lemma twice", () => {
    assert.equal(saidOf(item({ detail: "Sales weekly", conversationId: "c" }), true), "In Sales weekly");
    assert.equal(saidOf(item({ detail: "Northwind brief", conversationId: "c" }), false), "In Northwind brief");
    assert.equal(saidOf(item({ detail: null, line: { channelId: "x", messageId: "m", choices: [], prompt: null } }), true), "Sales");
    assert.equal(saidOf(item({ detail: "Offer", runId: "r", conversationId: "c" }), true), "Sales · Offer");
});
