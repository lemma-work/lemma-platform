import test from "node:test";
import assert from "node:assert/strict";
import { buildTree, decisionSays, entryOf, idsIn, leadOf, progressOf, readGraph, stateOfTrace, tracesByNode } from "../src/workflow/run-tree.ts";
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

test("a question's branches are arms too, one per target, with the answers that take them", () => {
    const graph = readGraph({
        nodes: [
            node("triage", "DECISION", {
                rules: [{ condition: "spam", next_node_id: "archive" }],
                question: {
                    input: { type: "literal", value: 1 },
                    decider: "email-triage",
                    branches: { act: "reply", digest: "later", ignore: "later" },
                    on_open: "review",
                },
            }),
            node("reply", "AGENT"),
            node("later", "FUNCTION"),
            node("review", "FORM"),
            node("archive", "FUNCTION"),
            node("done", "END"),
        ],
        edges: [edge("reply", "done"), edge("later", "done"), edge("review", "done"), edge("archive", "done")],
    })!;
    // Nothing points at the decision; its question's targets count as pointed at.
    assert.equal(entryOf(graph), "triage");
    const [decision, done] = buildTree(graph);
    assert.equal(done?.id, "done");
    assert.ok(decision.type === "decision");
    if (decision.type !== "decision") return;
    assert.deepEqual(decision.arms.map((arm) => [arm.label, arm.condition, idsIn(arm.items)]), [
        ["Rule 1", "spam", ["archive"]],
        ["act", null, ["reply"]],
        ["digest / ignore", null, ["later"]],
        ["Left open", null, ["review"]],
    ]);
});

test("a decision's output is said in a sentence, whichever way it went", () => {
    assert.equal(decisionSays({ matched_condition: "a > 1" }), "Matched a > 1.");
    assert.equal(decisionSays({ matched_condition: null }), "No rule matched, so it took the default path.");
    const asked = { matched_condition: null, decision_id: "d-1", open: [] };
    assert.equal(decisionSays({ ...asked, choice: "act", answered_by: "system_one" }), "Answered act — by System One.");
    assert.equal(decisionSays({ ...asked, choice: "act", answered_by: "rules" }), "Answered act — by its rules.");
    assert.equal(
        decisionSays({ ...asked, choice: "ignore", answered_by: null, open: ["action"] }),
        "Nothing could answer, so it took ignore.",
    );
    assert.equal(decisionSays({ ...asked, choice: null, answered_by: null, open: ["action"] }), "Nothing could answer.");
    assert.equal(decisionSays(null), "Decided.");
});

