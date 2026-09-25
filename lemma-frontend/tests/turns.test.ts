import test from "node:test";
import assert from "node:assert/strict";
import { buildTurns, liveNote, openInteraction, spanOf, type RawMessage } from "../src/thread/turns.ts";

/** The rules that were actually wrong, pinned.
 *
 *  Each of these passed as a claim and failed as code: the approval was fetched
 *  from a list endpoint keyed by an id the server does not use, and speech that
 *  arrived mid-run was demoted into a trace nobody opens. */

const at = (seconds: number) => new Date(1_700_000_000_000 + seconds * 1000).toISOString();

function message(partial: Partial<RawMessage> & { sequence: number }): RawMessage {
    return { id: "m" + partial.sequence, created_at: at(partial.sequence), ...partial };
}

test("keeps speech, cards and pauses in the order they happened", () => {
    const [turn] = buildTurns([
        message({ sequence: 1, role: "user", text: "delete the draft" }),
        message({ sequence: 2, role: "assistant", text: "Found it." }),
        message({
            sequence: 3,
            kind: "TOOL_CALL",
            tool_name: "request_approval",
            tool_call_id: "call_a",
            tool_args: { tool_name: "pod_delete_file", title: "Delete draft.md" },
        }),
    ]);

    assert.deepEqual(turn.items.map((item) => item.kind), ["text", "interaction"]);
});

test("does not demote a mid-run beat into the trace", () => {
    const [turn] = buildTurns([
        message({ sequence: 1, role: "user", text: "go" }),
        message({ sequence: 2, role: "assistant", text: "Checking the table first." }),
        message({ sequence: 3, kind: "TOOL_CALL", tool_name: "pod_query_table", tool_args: {} }),
        message({ sequence: 4, role: "assistant", text: "Nine rows are stale." }),
    ]);

    assert.deepEqual(
        turn.items.flatMap((item) => (item.kind === "text" ? [item.text] : [])),
        ["Checking the table first.", "Nine rows are stale."],
    );
    // The trace holds the work, and only the work.
    assert.equal(turn.notes.length, 1);
});

test("carries the tool call id as the approval id", () => {
    // The whole bug in one assertion: there is no separate approval id to go
    // and fetch, and posting anything else leaves the run blocked forever.
    const turns = buildTurns([
        message({ sequence: 1, role: "user", text: "go" }),
        message({
            sequence: 2,
            kind: "TOOL_CALL",
            tool_name: "request_approval",
            tool_call_id: "call_xyz",
            tool_args: { title: "Send the email" },
        }),
    ]);

    assert.equal(openInteraction(turns)?.id, "call_xyz");
});

test("closes a pause when its tool return lands, and keeps the decision", () => {
    const turns = buildTurns([
        message({ sequence: 1, role: "user", text: "go" }),
        message({
            sequence: 2,
            kind: "TOOL_CALL",
            tool_name: "request_approval",
            tool_call_id: "call_a",
            tool_args: { title: "Send the email" },
        }),
        message({
            sequence: 3,
            kind: "TOOL_RETURN",
            tool_call_id: "call_a",
            tool_result: { decision: "APPROVE_ONCE" },
        }),
    ]);

    // Nothing is blocked any more...
    assert.equal(openInteraction(turns), null);
    // ...but the card stays, saying what was agreed to.
    const item = turns[0].items.find((entry) => entry.kind === "interaction");
    assert.equal(item?.kind === "interaction" && item.interaction.decision, "APPROVE_ONCE");
});

test("reads a decision nested under output", () => {
    const turns = buildTurns([
        message({ sequence: 1, kind: "TOOL_CALL", tool_name: "ask_user", tool_call_id: "c1", tool_args: {} }),
        message({ sequence: 2, kind: "TOOL_RETURN", tool_call_id: "c1", tool_result: { output: { decision: "DENY" } } }),
    ]);
    const item = turns[0].items.find((entry) => entry.kind === "interaction");
    assert.equal(item?.kind === "interaction" && item.interaction.decision, "DENY");
});

test("finds a pause behind a namespaced tool name", () => {
    const turns = buildTurns([
        message({
            sequence: 1,
            kind: "TOOL_CALL",
            tool_name: "mcp__lemma__request_approval",
            tool_call_id: "c9",
            tool_args: { title: "Run it" },
        }),
    ]);
    assert.equal(openInteraction(turns)?.id, "c9");
});

test("never folds a pause into the trace", () => {
    // Discovering you are being asked something by opening a disclosure is
    // not being asked.
    const [turn] = buildTurns([
        message({ sequence: 1, kind: "TOOL_CALL", tool_name: "ask_user", tool_call_id: "c1", tool_args: {} }),
    ]);
    assert.equal(turn.notes.length, 0);
    assert.equal(turn.items.length, 1);
});

test("the newest open pause is the one the run is blocked on", () => {
    const turns = buildTurns([
        message({ sequence: 1, kind: "TOOL_CALL", tool_name: "request_approval", tool_call_id: "old", tool_args: {} }),
        message({ sequence: 2, kind: "TOOL_RETURN", tool_call_id: "old", tool_result: { decision: "APPROVE_ONCE" } }),
        message({ sequence: 3, role: "user", text: "and again" }),
        message({ sequence: 4, kind: "TOOL_CALL", tool_name: "request_approval", tool_call_id: "new", tool_args: {} }),
    ]);
    assert.equal(openInteraction(turns)?.id, "new");
});

test("times the work from first assistant activity to last", () => {
    const [turn] = buildTurns([
        message({ sequence: 1, role: "user", text: "go" }),
        message({ sequence: 10, kind: "TOOL_CALL", tool_name: "pod_read_file", tool_args: {} }),
        message({ sequence: 84, role: "assistant", text: "Done." }),
    ]);
    // The user's own message is not work the teammate did.
    assert.equal(spanOf(turn.startedAtMs, turn.endedAtMs), "1m 14s");
});

test("says nothing about a run that took no time", () => {
    assert.equal(spanOf(0, 400), "");
    assert.equal(spanOf(undefined, 1), "");
});

test("scales the span to the length of the run", () => {
    assert.equal(spanOf(0, 9_000), "9s");
    assert.equal(spanOf(0, 134_000), "2m 14s");
    assert.equal(spanOf(0, 3_780_000), "1h 3m");
});

test("a widget shows if it has any of its three sources, and only vanishes with none", () => {
    /* The gate exists for a widget the harness rejected: nothing to render, and
       a row announcing that is worse than silence. But it lists the sources, so
       when `path` was added it read two of three and every file-backed widget
       disappeared before it became a card — nothing on screen, nothing to
       debug. Each source is pinned here so the next one cannot repeat it. */
    const widget = (sequence: number, args: Record<string, unknown>) =>
        message({
            sequence,
            kind: "TOOL_CALL",
            tool_name: "display_resource",
            tool_call_id: "call_" + sequence,
            tool_args: { type: "WIDGET", ...args },
        });

    const [turn] = buildTurns([
        message({ sequence: 1, role: "user", text: "show me" }),
        widget(2, { path: "/me/c/2026-09-15/pulse.html" }),
        widget(3, { content: "<div>7 open</div>" }),
        widget(4, { public_url: "https://example.com/w" }),
        widget(5, {}),
        widget(6, { content: "the fragment is below" }),
    ]);

    assert.deepEqual(
        turn.items.filter((item) => item.kind === "resource").map((item) => item.toolCallId),
        ["call_2", "call_3", "call_4"],
    );
});

test("a plan is a card in the conversation, not a folded working note", () => {

    // put the one thing a run writes about its own intent behind a disclosure.
    const [turn] = buildTurns([
        message({ sequence: 1, role: "user", text: "sort out the vendor totals" }),
        message({
            sequence: 2,
            kind: "TOOL_CALL",
            tool_name: "update_plan",
            tool_call_id: "call_p",
            tool_args: { plan: [
                { step: "read the table", status: "completed" },
                { step: "total by vendor", status: "in_progress" },
            ] },
        }),
    ]);

    assert.deepEqual(turn.items.map((item) => item.kind), ["plan"]);
    assert.equal(turn.notes.length, 0);
    const item = turn.items[0];
    assert.equal(item.kind === "plan" && item.steps.length, 2);
    assert.equal(item.kind === "plan" && item.steps[1].status, "in_progress");
});

test("a plan revised five times is one list that changed, not five lists", () => {
    // Otherwise the transcript answers "what is it doing" five times, and the
    // list walks down the page every time a step closes.
    const [turn] = buildTurns([
        message({ sequence: 1, role: "user", text: "go" }),
        message({ sequence: 2, kind: "TOOL_CALL", tool_name: "update_plan", tool_call_id: "p1",
            tool_args: { plan: [{ step: "read", status: "in_progress" }, { step: "total", status: "pending" }] } }),
        message({ sequence: 3, role: "assistant", text: "Reading it now." }),
        message({ sequence: 4, kind: "TOOL_CALL", tool_name: "update_plan", tool_call_id: "p2",
            tool_args: { plan: [{ step: "read", status: "completed" }, { step: "total", status: "in_progress" }] } }),
    ]);

    const plans = turn.items.filter((item) => item.kind === "plan");
    assert.equal(plans.length, 1);
    // The latest wins, in the place the first one appeared.
    assert.equal(turn.items[0].kind, "plan");
    assert.equal(plans[0].kind === "plan" && plans[0].steps[0].status, "completed");
});

/* ── a local agent, through the Agent Host ─────────────────────────── */

test("an Agent Host update_plan is the plan card", () => {
    const todos = [
        { content: "Run echo hello-lemma", status: "completed", priority: "high" },
        { content: "Edit one -> two", status: "in_progress", priority: "high" },
        { content: "Read notes.txt", status: "pending", priority: "medium" },
    ];
    const [turn] = buildTurns([
        message({ sequence: 1, role: "user", text: "go" }),
        message({ sequence: 2, kind: "TOOL_CALL", tool_name: "update_plan", tool_call_id: "plan-1", tool_args: { todos },
            metadata: { tool_source: "native" } }),
        message({ sequence: 3, kind: "TOOL_RETURN", tool_name: "update_plan", tool_call_id: "plan-1", tool_result: { todos } }),
    ]);
    const plan = turn.items.find((item) => item.kind === "plan");
    assert.ok(plan && plan.kind === "plan");
    assert.deepEqual(plan.steps.map((step) => step.status), ["completed", "in_progress", "pending"]);
    assert.equal(plan.steps[1].step, "Edit one -> two");
});

test("somebody else's ask_user is a step, not a question this app can answer", () => {
    const [turn] = buildTurns([
        message({ sequence: 1, role: "user", text: "go" }),
        message({ sequence: 2, kind: "TOOL_CALL", tool_name: "ask_user", tool_call_id: "c1", tool_args: { questions: [] },
            metadata: { tool_source: "mcp", tool_server: "helpdesk" } }),
    ]);
    assert.equal(turn.items.length, 0);
    assert.equal(turn.notes[0].label, "Ask user · helpdesk");
});

test("a sub-agent's steps sit under the task that started them", () => {
    const [turn] = buildTurns([
        message({ sequence: 1, role: "user", text: "go" }),
        message({ sequence: 2, kind: "TOOL_CALL", tool_name: "task", tool_call_id: "t1",
            tool_args: { description: "Find the test" }, metadata: { tool_source: "native" } }),
        message({ sequence: 3, kind: "TOOL_CALL", tool_name: "grep", tool_call_id: "g1",
            tool_args: { pattern: "flaky" }, metadata: { tool_source: "native", parent_call_id: "t1" } }),
        // A parent this page never saw indents under nothing, so it does not.
        message({ sequence: 4, kind: "TOOL_CALL", tool_name: "grep", tool_call_id: "g2",
            tool_args: { pattern: "x" }, metadata: { tool_source: "native", parent_call_id: "elsewhere" } }),
    ]);
    assert.deepEqual(turn.notes.map((note) => note.nested), [false, true, false]);
    assert.equal(turn.notes[0].card?.kind, "task");
});

test("a native step reads the adapter's title when it said nothing itself", () => {
    const [turn] = buildTurns([
        message({ sequence: 1, role: "user", text: "go" }),
        message({ sequence: 2, kind: "TOOL_CALL", tool_name: "notebook_edit", tool_call_id: "n1", tool_args: { cell: 3 },
            metadata: { tool_source: "native", tool_title: "Edit cell 3 of analysis.ipynb" } }),
        // Lemma's own tools are titled with their namespaced name: not shown.
        message({ sequence: 3, kind: "TOOL_CALL", tool_name: "pod_list_files", tool_call_id: "n2", tool_args: { path: "/me" },
            metadata: { tool_source: "lemma", tool_title: "mcp.lemma_tools.lemma_pod_list_files" } }),
    ]);
    assert.equal(turn.notes[0].detail, "Edit cell 3 of analysis.ipynb");
    assert.equal(turn.notes[1].detail, "/me");
});

test("the running indicator does not repeat a call that has already landed", () => {
    // The host sends the call's message first and the `tool` token after it.
    const [turn] = buildTurns([
        message({ sequence: 1, role: "user", text: "go" }),
        message({ sequence: 2, kind: "TOOL_CALL", tool_name: "exec_command", tool_call_id: "x1", tool_args: { cmd: "make" } }),
    ]);
    const streaming = { text: "", thinking: "", tool: { toolName: "exec_command", toolCallId: "x1", args: { cmd: "make" } } };
    assert.deepEqual(liveNote(streaming, turn.notes), []);
    // A streamed call that has not landed yet still gets its live row.
    assert.equal(liveNote({ ...streaming, tool: { ...streaming.tool, toolCallId: "x2" } }, turn.notes).length, 1);
});
