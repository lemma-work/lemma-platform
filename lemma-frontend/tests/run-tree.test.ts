import test from "node:test";
import assert from "node:assert/strict";
import { buildTree, entryOf, idsIn, leadOf, progressOf, readGraph, sayDecided, stateOfTrace, tracesByNode } from "../src/workflow/run-tree.ts";
import { readSteps } from "../src/workflow/runs.ts";

const node = (id: string, type: string, config: Record<string, unknown> = {}, label: string | null = null) => ({ id, type, label, config });
const edge = (source: string, target: string) => ({ id: source + ">" + target, source, target });

test("a decision's arms nest under it, and the run carries on at their join", () => {
    const graph = readGraph({
        nodes: [
            node("done", "END"),
            node("collect", "FORM"),
            node("decide", "DECISION", { rules: [{ condition: "a > 1", next_node_id: "approve" }, { condition: "a > 0", next_node_id: "pay" }] }),
            node("approve", "FORM", {}, "Approval"),
            node("pay", "FUNCTION"),
        ],
        edges: [edge("collect", "decide"), edge("approve", "done"), edge("pay", "done")],
    })!;
    assert.equal(entryOf(graph), "collect");
    const tree = buildTree(graph);
    assert.deepEqual(tree.map((item) => item.type + ":" + item.id), ["step:collect", "decision:decide", "step:done"]);
    const decision = tree[1];
    assert.equal(decision.type, "decision");
    if (decision.type !== "decision") return;
    assert.deepEqual(decision.arms.map((arm) => [arm.label, arm.condition, idsIn(arm.items)]), [
        ["Approval", "a > 1", ["approve"]],
        ["Rule 2", "a > 0", ["pay"]],
    ]);
});

test("a decision's default edge is the Otherwise arm", () => {
    const graph = readGraph({
        nodes: [node("d", "DECISION", { rules: [{ condition: "x", next_node_id: "a" }] }), node("a", "AGENT"), node("b", "AGENT"), node("end", "END")],
        edges: [edge("d", "b"), edge("a", "end"), edge("b", "end")],
    })!;
    const [decision, end] = buildTree(graph);
    assert.equal(end?.id, "end");
    assert.ok(decision.type === "decision");
    if (decision.type !== "decision") return;
    assert.deepEqual(decision.arms.map((arm) => arm.label), ["Rule 1", "Otherwise"]);
    assert.equal(decision.arms[1].condition, null);
});

test("a question's routes are its arms, named by the answers that take them", () => {
    // A decision that asks a question branches on config.question.routes and
    // unsure_next_node_id, not on rules (domain/nodes/decision.py). Answers
    // routed to the same node are one arm, and the default edge is Otherwise.
    const graph = readGraph({
        nodes: [
            node("triage", "DECISION", {
                question: {
                    answer: { type: "boolean", description: "Is it a refund?" },
                    routes: { true: "refund", false: "reply" },
                    unsure_next_node_id: "ask",
                },
            }),
            node("refund", "FUNCTION"),
            node("reply", "AGENT"),
            node("ask", "FORM"),
            node("other", "AGENT"),
            node("end", "END"),
        ],
        edges: [edge("triage", "other"), edge("refund", "end"), edge("reply", "end"), edge("ask", "end"), edge("other", "end")],
    })!;
    assert.equal(entryOf(graph), "triage", "route targets point at their nodes");
    const [decision, end] = buildTree(graph);
    assert.equal(end?.id, "end");
    assert.ok(decision.type === "decision");
    if (decision.type !== "decision") return;
    assert.deepEqual(decision.arms.map((arm) => [arm.label, arm.condition, idsIn(arm.items)]), [
        ["If yes", null, ["refund"]],
        ["If no", null, ["reply"]],
        ["If it cannot tell", null, ["ask"]],
        ["Otherwise", null, ["other"]],
    ]);

    const shared = readGraph({
        nodes: [
            node("triage", "DECISION", { question: { routes: { billing: "refund", bug: "refund", other: "reply" } } }),
            node("refund", "FUNCTION"),
            node("reply", "FUNCTION"),
        ],
        edges: [],
    })!;
    const [merged] = buildTree(shared);
    assert.ok(merged.type === "decision");
    if (merged.type !== "decision") return;
    assert.deepEqual(merged.arms.map((arm) => arm.label), ["If billing or bug", "If other"]);
});

test("a decision says what it decided, from either kind of output", () => {
    const names = (id: string) => ({ refund: "Issue the refund" })[id] ?? id;
    assert.equal(
        sayDecided({ answer: "billing", confidence: 0.82, provider: "typesafe", model: "m", route: "refund" }, names),
        "Answered billing (82% sure), so it went on to Issue the refund.",
    );
    assert.equal(sayDecided({ answer: false, confidence: null, route: "reply" }), "Answered no, so it went on to reply.");
    assert.equal(sayDecided({ answer: null, confidence: null, route: "ask" }), "Could not tell from what it was given, so it went on to ask.");
    // A run failed for want of a route records the answer and no route.
    assert.equal(sayDecided({ answer: 3, error: "no route" }), "Answered 3.");
    assert.equal(sayDecided({ matched_condition: "a > 1" }), "Matched a > 1.");
    assert.equal(sayDecided({ matched_condition: null }), "No rule matched, so it took the default path.");
    // A question still being weighed has no output yet.
    assert.equal(sayDecided(undefined), "Not decided yet.");
    assert.equal(sayDecided({ something: "else" }), "Decided.");
});

test("a loop holds its body and does not walk round it forever", () => {
    const graph = readGraph({
        nodes: [
            node("intake", "FORM"),
            node("each", "LOOP", { items_path: "intake.list", child_node_id: "check" }),
            node("check", "AGENT"),
            node("record", "FUNCTION"),
            node("confirm", "FORM"),
        ],
        edges: [edge("intake", "each"), edge("check", "record"), edge("record", "each"), edge("each", "confirm")],
    })!;
    const tree = buildTree(graph);
    assert.deepEqual(tree.map((item) => item.id), ["intake", "each", "confirm"]);
    const loop = tree[1];
    assert.ok(loop.type === "loop");
    if (loop.type !== "loop") return;
    assert.deepEqual(idsIn(loop.body), ["check", "record"]);
});

test("a step nothing reaches is appended, not lost, and junk is skipped", () => {
    const graph = readGraph({
        nodes: [node("a", "FUNCTION"), node("b", "AGENT"), node("orphan", "FUNCTION"), "not a node"],
        edges: [edge("a", "b"), edge("b", "missing")],
    })!;
    assert.deepEqual(idsIn(buildTree(graph)), ["a", "b", "orphan"]);
});

test("history hangs on its node, loop iterations included, and progress ignores END", () => {
    const steps = readSteps({
        step_history: [
            { step_index: 0, node_id: "intake", status: "COMPLETED" },
            { step_index: 1, node_id: "check", status: "COMPLETED" },
            { step_index: 2, node_id: "check", status: "RUNNING" },
        ],
    });
    const traces = tracesByNode(steps);
    assert.equal(traces.get("check")?.length, 2);
    assert.equal(stateOfTrace("RUNNING"), "running");
    assert.equal(stateOfTrace("WAITING"), "waiting");
    const graph = readGraph({ nodes: [node("intake", "FORM"), node("check", "AGENT"), node("end", "END")], edges: [] })!;
    assert.deepEqual(progressOf(graph, steps), { done: 2, total: 2 });
});

test("the lead sentence comes from the conventional keys, and nothing else", () => {
    assert.equal(leadOf({ rows: 3, summary: " Found three. " }), "Found three.");
    assert.equal(leadOf("plain"), "plain");
    assert.equal(leadOf({ rows: 3 }), null);
    assert.equal(leadOf(null), null);
});

test("an unreadable graph is null, not a throw", () => {
    assert.equal(readGraph(null), null);
    assert.deepEqual(buildTree(readGraph({})!), []);
});
