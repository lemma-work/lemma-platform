import test from "node:test";
import assert from "node:assert/strict";
import {
    byNewest, byStuckLongest, millisOf, readAssignments, readRun, readRunDetail, readSteps,
    readWait, readWorkflow, readWorkflows, runMillis, runTone, sayCancelRefusal,
    sayFor, sayRefusal, sayStatus, sayStuckFor, sayWhen, stillGoing, waitTypeOf,
} from "../src/workflow/runs.ts";
import { readFlowStep, readShape, switchOf, tidy, type FlowItem } from "../src/workflow/shape.ts";

/* Shaped off the real responses: `WorkflowRunWaitAssignment` pairs a
   `WorkflowRunWaitResponse` with a `WorkflowRunSummaryResponse`
   (app/modules/workflow/api/schemas.py:558). */
const wait = {
    id: "w-1",
    run_id: "r-1",
    workflow_id: "f-1",
    pod_id: "p-1",
    node_id: "collect",
    wait_type: "HUMAN",
    status: "ACTIVE",
    assigned_pod_member_id: "m-1",
    created_at: "2026-09-18T09:00:00Z",
    payload: {
        input_schema: { type: "object", required: ["amount"], properties: { amount: { type: "number" } } },
        ui_schema: { "ui:order": ["amount"] },
    },
};

const run = {
    id: "r-1",
    workflow_id: "f-1",
    pod_id: "p-1",
    user_id: "u-1",
    status: "WAITING",
    start_type: "MANUAL",
    started_at: "2026-09-18T08:59:00Z",
    created_at: "2026-09-18T08:59:00Z",
};

test("a workflow list becomes rows, and a nameless one is not a row", () => {
    // Every workflow endpoint in the SDK takes a workflow *name*, so a row
    // without one cannot be opened, its runs cannot be listed, and drawing it
    // would be drawing a dead link.
    const rows = readWorkflows({
        items: [
            { id: "f-1", name: "Budget sign-off", description: "Two approvals.", node_count: 4, node_types: ["FORM", "AGENT", "END"], updated_at: "2026-09-01T00:00:00Z", allowed_actions: ["read", "run"] },
            { id: "f-2", description: "no name" },
            "nonsense",
            null,
        ],
    });

    assert.equal(rows.length, 1);
    assert.equal(rows[0].name, "Budget sign-off");
    assert.equal(rows[0].steps, 4);
    assert.deepEqual(rows[0].kinds, ["FORM", "AGENT", "END"]);
    assert.deepEqual(rows[0].may, ["read", "run"]);
    assert.equal(rows[0].active, true);
});

test("a workflow list with nothing in it is no rows, not a throw", () => {
    assert.deepEqual(readWorkflows(null), []);
    assert.deepEqual(readWorkflows({}), []);
    assert.deepEqual(readWorkflows({ items: "nonsense" }), []);
    assert.equal(readWorkflow(undefined), null);
    assert.equal(readWorkflow({ name: "   " }), null);
});

test("an unknown status is Unknown, never a guess", () => {
    // The worst possible wrong answer here is inventing "Completed" for a
    // status this app has not heard of — it says a thing finished when
    // nobody knows what it did.
    assert.equal(readRun({ id: "r", status: 7 })?.status, "UNKNOWN");
    assert.equal(sayStatus("HALTED"), "Unknown");
    assert.equal(runTone("HALTED"), "gone");
    assert.equal(stillGoing("HALTED"), false);
});

test("RUNNING says what it is actually stuck on", () => {
    // domain/run.py:41 — only a human form wait moves a run to WAITING. A run
    // parked on an agent, a function job or a timer stays RUNNING, and the
    // wait row is the only place that says which. "Running" on a run that has
    // sat on an agent for an hour is a lie the status field tells by itself.
    assert.equal(sayStatus("RUNNING", null), "Running");
    assert.equal(sayStatus("RUNNING", "AGENT"), "Waiting on an agent");
    assert.equal(sayStatus("RUNNING", "FUNCTION"), "Waiting on a function");
    assert.equal(sayStatus("RUNNING", "TIME"), "Waiting on a timer");
    // A HUMAN wait on a RUNNING run is not a thing the engine produces, and
    // the status is the one to trust if it ever is.
    assert.equal(sayStatus("RUNNING", "HUMAN"), "Running");
    assert.equal(sayStatus("WAITING"), "Waiting on a person");
});

test("only a live run offers to be cancelled", () => {
    // Mirrors TERMINAL_STATUSES (domain/run.py:57). Cancelling a finished run
    // is a 409, not a no-op.
    assert.equal(stillGoing("PENDING"), true);
    assert.equal(stillGoing("RUNNING"), true);
    assert.equal(stillGoing("WAITING"), true);
    assert.equal(stillGoing("COMPLETED"), false);
    assert.equal(stillGoing("FAILED"), false);
    assert.equal(stillGoing("CANCELLED"), false);
});

test("a wait carries the schema and the ui schema the form node resolved", () => {
    // execution/executors/form.py:49 writes both onto the wait, resolved, so
    // every consumer renders from the wait row alone.
    const read = readWait(wait);
    assert.ok(read);
    assert.equal(read.nodeId, "collect");
    assert.equal(read.type, "HUMAN");
    assert.deepEqual(read.uiSchema, { "ui:order": ["amount"] });
    assert.equal((read.schema as { type?: string }).type, "object");
});

test("a wait with no node id cannot be answered, so it is not a wait", () => {
    // node_id is what a submission is checked against (api/schemas.py:536).
    assert.equal(readWait({ ...wait, node_id: "" }), null);
    assert.equal(readWait({ ...wait, wait_type: "SOMETHING" }), null);
    assert.equal(readWait({ ...wait, id: null }), null);
    assert.equal(waitTypeOf({ wait_type: "TIME" }), "TIME");
    assert.equal(waitTypeOf(null), null);
});

test("a non-form wait has no schema rather than a broken one", () => {
    const timer = readWait({ ...wait, wait_type: "TIME", payload: { scheduled_at: "2026-09-20T00:00:00Z" } });
    assert.equal(timer?.schema, null);
    assert.equal(timer?.uiSchema, null);
    // A payload that is not an object at all is the same answer.
    assert.equal(readWait({ ...wait, payload: "nonsense" })?.schema, null);
});

test("an assignment needs both halves, and keeps the ones that have them", () => {
    const list = readAssignments({
        items: [
            { wait, run },
            { wait, run: { status: "WAITING" } },      // no run id
            { wait: { ...wait, node_id: "" }, run },   // unanswerable wait
            { run },
            null,
        ],
    });
    assert.equal(list.length, 1);
    assert.equal(list[0].run.id, "r-1");
    assert.equal(list[0].wait.nodeId, "collect");
});

test("the inbox puts what has been stuck longest at the top", () => {
    // An inbox sorted newest-first buries the one item on the list that is
    // actually going wrong.
    const made = (id: string, createdAt: string | null) => ({
        wait: readWait({ ...wait, id, created_at: createdAt })!,
        run: readRun(run)!,
    });
    const order = byStuckLongest([
        made("new", "2026-09-19T09:00:00Z"),
        made("old", "2026-09-12T09:00:00Z"),
        made("mid", "2026-09-17T09:00:00Z"),
    ]).map((one) => one.wait.id);

    assert.deepEqual(order, ["old", "mid", "new"]);
});

test("a run list reads newest first", () => {
    const rows = byNewest([
        readRun({ id: "a", started_at: "2026-09-10T00:00:00Z" })!,
        readRun({ id: "b", started_at: "2026-09-18T00:00:00Z" })!,
        // No started_at at all: the run never advanced. created_at is what it
        // has, and ordering it last is better than ordering it first.
        readRun({ id: "c", created_at: "2026-09-14T00:00:00Z" })!,
    ]);
    assert.deepEqual(rows.map((one) => one.id), ["b", "c", "a"]);
});

test("step history is ordered by the index the server gave it", () => {
    const steps = readSteps({
        step_history: [
            { step_index: 2, node_id: "notify", status: "COMPLETED", started_at: "2026-09-18T09:02:00Z" },
            { step_index: 0, node_id: "collect", status: "COMPLETED", started_at: "2026-09-18T09:00:00Z" },
            { step_index: 1, node_id: "review", status: "FAILED", started_at: "2026-09-18T09:01:00Z", error: "no reviewer" },
        ],
    });
    assert.deepEqual(steps.map((one) => one.nodeId), ["collect", "review", "notify"]);
    assert.equal(steps[1].error, "no reviewer");
});

test("a malformed step is still a step", () => {
    // Dropping it renumbers everything after it and quietly shortens the
    // history somebody is reading to work out what happened.
    const steps = readSteps({ step_history: [{ step_index: 0, node_id: "collect", status: "COMPLETED", started_at: "x" }, "nonsense", null] });
    assert.equal(steps.length, 3);
    assert.equal(steps[1].status, "UNKNOWN");
    assert.equal(steps[1].nodeId, "");
});

test("a run detail carries its steps, its wait, and its context", () => {
    const detail = readRunDetail({
        ...run,
        status: "FAILED",
        failed_node_id: "review",
        error: "The reviewer is not a pod member.",
        completed_at: "2026-09-18T09:04:00Z",
        active_wait: null,
        execution_context: { start: { by: "manual" }, collect: { amount: 40 } },
        step_history: [{ step_index: 0, node_id: "collect", status: "COMPLETED", started_at: "2026-09-18T08:59:00Z", output_data: { amount: 40 } }],
    });

    assert.ok(detail);
    assert.equal(detail.failedNodeId, "review");
    assert.equal(detail.wait, null);
    assert.equal(detail.steps.length, 1);
    assert.deepEqual(detail.steps[0].output, { amount: 40 });
    assert.deepEqual(Object.keys(detail.context), ["start", "collect"]);
});

test("a run detail with a free-form context does not throw on it", () => {
    // execution_context is `dict[str, Any]` (api/schemas.py:523) — the keys
    // are node ids the workflow's author chose.
    assert.deepEqual(readRunDetail({ id: "r", execution_context: "nonsense" })?.context, {});
    assert.deepEqual(readRunDetail({ id: "r" })?.steps, []);
    assert.equal(readRunDetail(null), null);
    assert.equal(readRunDetail({ status: "RUNNING" }), null);
});

test("a run time with no zone on it is UTC, wherever the browser is", () => {
    // `started_at` is a plain timestamp column and arrives as
    // "2026-10-07T19:42:36.5", no zone. Read as local time, a run started a
    // minute ago in India said "started 5h ago".
    const utc = Date.UTC(2026, 9, 7, 19, 42, 36, 500);
    assert.equal(millisOf("2026-10-07T19:42:36.500"), utc);
    assert.equal(millisOf("2026-10-07T19:42:36.500Z"), utc);
    assert.equal(millisOf("2026-10-08T01:12:36.500+05:30"), utc);
    assert.equal(millisOf("2026-10-07T14:42:36.500-0500"), utc);
    assert.equal(millisOf("2026-10-07"), Date.UTC(2026, 9, 7));
    assert.equal(millisOf("not a time"), null);
    assert.equal(millisOf(null), null);
    // Five minutes after, in every zone — not "5h ago" in Kolkata.
    assert.equal(sayWhen("2026-10-07T19:42:36.500", utc + 5 * 60_000), "5m ago");
});

test("how long it took, and how long it has been going", () => {
    const now = Date.parse("2026-09-18T09:10:00Z");
    const finished = readRun({ ...run, started_at: "2026-09-18T09:00:00Z", completed_at: "2026-09-18T09:02:30Z" })!;
    assert.equal(sayFor(runMillis(finished, now)), "3m");

    const going = readRun({ ...run, started_at: "2026-09-18T09:00:00Z" })!;
    assert.equal(sayFor(runMillis(going, now)), "10m");

    // A run that failed before its first advance has a created time and
    // nothing else; "unknown" for those is worse than the truth.
    const stillborn = readRun({ id: "r", created_at: "2026-09-18T09:09:59Z" })!;
    assert.equal(sayFor(runMillis(stillborn, now)), "1s");

    assert.equal(runMillis(readRun({ id: "r" })!, now), null);
    assert.equal(sayFor(null), null);
});

test("a sub-second step says so rather than saying zero", () => {
    // A decision node finishing in 40ms is a real thing that happened, and a
    // "0s" reads as a step that was skipped.
    assert.equal(sayFor(40), "under a second");
    assert.equal(sayFor(0), "under a second");
    assert.equal(sayFor(90_000), "2m");
    assert.equal(sayFor(3 * 3600_000), "3h");
    assert.equal(sayFor(5 * 86400_000), "5d");
});

test("when, said relatively until a fortnight and then as a date", () => {
    const now = Date.parse("2026-09-19T12:00:00Z");
    assert.equal(sayWhen("2026-09-19T11:59:30Z", now), "just now");
    assert.equal(sayWhen("2026-09-19T11:20:00Z", now), "40m ago");
    assert.equal(sayWhen("2026-09-19T04:00:00Z", now), "8h ago");
    assert.equal(sayWhen("2026-09-18T11:00:00Z", now), "yesterday");
    assert.equal(sayWhen("2026-09-14T12:00:00Z", now), "5d ago");
    // "19 days ago" is a number nobody converts.
    assert.ok(!/ago/.test(sayWhen("2026-08-31T12:00:00Z", now) ?? ""));
    assert.equal(sayWhen(null, now), null);
    assert.equal(sayWhen("not a date", now), null);
});

test("how long this has been sitting on you is said in the row", () => {
    const now = Date.parse("2026-09-19T09:00:00Z");
    // Hours rather than days up to a fortnight of them, because this is the
    // one place the age is the whole point: "stuck for 30h" lands and "1d"
    // rounds the urgency off.
    assert.equal(sayStuckFor(readWait(wait)!, readRun(run)!, now), "Waiting on you for 24h");
    // No timestamp anywhere: the row still says what it is, without a made-up
    // age beside it.
    const undated = readWait({ ...wait, created_at: null })!;
    assert.equal(sayStuckFor(undated, readRun({ id: "r" })!, now), "Waiting on you");
});

test("a refused submission is a sentence, not a status code", () => {
    // api/workflow_run_controller.py:81 — each of these is a real product
    // state rather than a fault.
    assert.match(sayRefusal(422, "x"), /moved to a different step/);
    assert.match(sayRefusal(409, "x"), /no longer waiting on a form/);
    assert.match(sayRefusal(403, "x"), /not assigned to you/);
    assert.equal(sayRefusal(500, "That did not go through."), "That did not go through.");
    assert.match(sayCancelRefusal(409, "x"), /already finished/);
    assert.equal(sayCancelRefusal(undefined, "That did not go through."), "That did not go through.");
});

/* ── the shape: what it runs, in the order it runs it ──────────────── */

/** A node, shaped the way `WorkflowNodeResponse` sends one: a discriminated
 *  union on `type`, with `id`/`label`/`position` off `BaseNode` and everything
 *  else under `config` (`domain/nodes/base.py:24`). */
function node(id: string, type: string, config: Record<string, unknown> = {}) {
    return { id, type, config, label: null, position: { x: 0, y: 0 } };
}

function edge(source: string, target: string) {
    return { id: source + "->" + target, source, target, label: null };
}

const ids = (steps: { id: string }[]) => steps.map((step) => step.id);

/** An outline, small enough to compare whole: a step is its id, a jump is an
 *  arrow and an id, and forks and loops keep what is inside them. */
function outlineOf(items: FlowItem[]): unknown[] {
    return items.map((item) => {
        switch (item.type) {
            case "step": return item.step.id;
            case "fork": return {
                fork: item.step.id,
                then: item.then?.id ?? null,
                on: item.on,
                arms: item.arms.map((arm) => ({ when: arm.when, items: outlineOf(arm.items) })),
            };
            case "loop": return { loop: item.step.id, body: outlineOf(item.body) };
            case "jump": return (item.back ? "↩ " : "→ ") + item.id;
            case "end": return item.inLoop ? "(next one)" : "(the run ends)";
        }
    });
}

test("a linear flow reads top to bottom, in the order it runs", () => {
    const shape = readShape({
        name: "quarterly-audit",
        nodes: [
            /* Deliberately out of order in the payload: `nodes` is a list, not
               a sequence, and the whole point of the walk is that the wiring
               decides the order rather than whatever the author saved last. */
            node("reconcile", "AGENT", { agent_name: "ledger-reconciler" }),
            node("done", "END"),
            node("pull-ledger", "FUNCTION", { function_name: "fetch-ledger" }),
        ],
        edges: [edge("pull-ledger", "reconcile"), edge("reconcile", "done")],
        start: { type: "MANUAL", config: null },
    });

    assert.ok(shape);
    assert.deepEqual(ids(shape.ordered), ["pull-ledger", "reconcile", "done"]);
    assert.deepEqual(shape.orphans, []);
    assert.equal(shape.trouble, null);
    assert.equal(shape.start.says, "Runs when you ask");
});

test("a branch puts both arms under the decision and the join under both", () => {
    // A decision's branches are config.rules[].next_node_id, not edges
    // (domain/graph.py:106). A walk that followed only `edges` here would
    // report every arm as unreachable — which is the most confident kind of
    // wrong, because the view would then print them under "nothing reaches
    // these" with total conviction.
    const shape = readShape({
        name: "budget-sign-off",
        nodes: [
            node("collect", "FORM", { input_schema: { type: "object", required: ["amount"], properties: { amount: { title: "Amount" }, cost_centre: {} } } }),
            node("decide", "DECISION", {
                rules: [
                    { condition: "collect.amount > `5000`", next_node_id: "escalate" },
                    { condition: "collect.amount > `0`", next_node_id: "pay" },
                ],
            }),
            node("escalate", "FORM", { input_schema: {}, assignee_pod_member_id_expression: "start.payload.owner" }),
            node("pay", "FUNCTION", { function_name: "pay-invoice", input_mapping: { amount: { type: "expression", value: "collect.amount" } } }),
            node("done", "END"),
        ],
        edges: [edge("collect", "decide"), edge("escalate", "done"), edge("pay", "done")],
        start: { type: "SCHEDULED", config: { schedule_type: "CRON" } },
    });

    assert.ok(shape);
    // Both arms adjacent, and `done` beneath both rather than in the middle of
    // the first one — which is what a plain depth-first walk would give.
    assert.deepEqual(ids(shape.ordered), ["collect", "decide", "escalate", "pay", "done"]);
    assert.deepEqual(shape.orphans, []);
    assert.equal(shape.trouble, null);

    const decide = shape.ordered[1];
    assert.equal(decide.says, "Branches");
    assert.deepEqual(decide.branches, ["escalate", "pay"]);
    // The rules are the shape. A decision that only says "branches" leaves the
    // reader to guess where, which is the question they opened this to answer.
    assert.deepEqual(decide.rules[0], { when: "collect.amount > `5000`", to: "escalate" });
    assert.deepEqual(decide.detail, []);

    // Drawn, the decision forks: one arm per rule, one for when none match,
    // and `done` back on the main line under the fork because both arms reach
    // it. The no-match arm ends the run instead, and says so.
    assert.deepEqual(outlineOf(shape.flow), [
        "collect",
        { fork: "decide", then: "done", on: null, arms: [
            { when: "collect.amount > 5000", items: ["escalate"] },
            { when: "collect.amount > 0", items: ["pay"] },
            { when: null, items: ["(the run ends)"] },
        ] },
        "done",
    ]);

    const collect = shape.ordered[0];
    assert.equal(collect.says, "Asks a person");
    // Required is starred; the title wins over the key where there is one.
    assert.equal(collect.detail[0], "For: Amount*, cost_centre");

    assert.equal(shape.ordered[3].says, "Runs pay-invoice");
    assert.equal(shape.ordered[3].detail[0], "With: amount");
    // The times live on a pod schedule, not on the workflow (domain/start.py:47).
    assert.match(shape.start.detail[0], /Over and over\. The timetable lives on a pod schedule/);
});

test("a loop prints its body before what comes after it, and does not hang", () => {
    // The body edges back to the loop, so a walk with no `seen` set never
    // returns — and a topological sort has no answer at all for a cycle.
    const shape = readShape({
        name: "supplier-onboarding",
        nodes: [
            node("intake", "FORM", { input_schema: { properties: { suppliers: {} } } }),
            node("each", "LOOP", { items_path: "intake.suppliers", item_var_name: "supplier", child_node_id: "check" }),
            node("check", "AGENT", { agent_name: "companies-house" }),
            node("record", "FUNCTION", { function_name: "insert-supplier" }),
            node("done", "END"),
        ],
        edges: [
            edge("intake", "each"),
            edge("check", "record"),
            edge("record", "each"),   // back to the loop: the cycle
            edge("each", "done"),
        ],
        start: { type: "DATASTORE_EVENT", config: { table_name: "suppliers", operations: ["INSERT", "UPDATE"] } },
    });

    assert.ok(shape);
    assert.deepEqual(ids(shape.ordered), ["intake", "each", "check", "record", "done"]);
    assert.deepEqual(shape.orphans, []);
    assert.equal(shape.trouble, null);

    const loop = shape.ordered[1];
    assert.deepEqual(loop.branches, ["check"]);
    assert.equal(loop.detail[0], "Over intake.suppliers, as loop.supplier");
    assert.equal(loop.detail.length, 1);
    // The body is drawn inside the loop, and the edge back to the loop is the
    // end of a pass rather than a jump anywhere.
    assert.deepEqual(outlineOf(shape.flow), ["intake", { loop: "each", body: ["check", "record"] }, "done"]);
    assert.equal(shape.start.detail[0], "When a row in suppliers is added or changed.");
});

test("arms that never meet again keep the biggest on the main line, and the rest read as side trips", () => {
    // Wired the way a real one came back: check the slot, then post, wait and
    // ask again, or stop; every outcome its own End. The decisions also carry
    // an edge per rule, as the editor saves them — and the engine only ever
    // reads the first edge as the fall-through (domain/workflow.py:71).
    // Nesting every arm in full put the whole posting path inside the first
    // arm and the waits a screen away from the question they answer.
    const fn = (name: string) => ({ function_name: name });
    const on = "(recheck.action || clock.action)";
    const shape = readShape({
        name: "post-lifecycle",
        nodes: [
            node("clock", "FUNCTION", fn("post_clock")),
            node("recheck", "FUNCTION", fn("post_clock")),
            node("route", "DECISION", { rules: [
                { condition: on + " == 'publish'", next_node_id: "publish" },
                { condition: on + " == 'wait_short'", next_node_id: "wait_short" },
                { condition: on + " == 'wait_long'", next_node_id: "wait_long" },
            ] }),
            node("wait_long", "WAIT_UNTIL", { timeout_seconds: 3600 }),
            node("wait_short", "WAIT_UNTIL", { timeout_seconds: 300 }),
            node("publish", "FUNCTION", fn("publish_post")),
            node("published", "DECISION", { rules: [
                { condition: "publish.ok == `true` && publish.dry_run == `false`", next_node_id: "wait_1h" },
                { condition: "publish.ok == `true`", next_node_id: "rehearsed" },
            ] }),
            node("wait_1h", "WAIT_UNTIL", { timeout_seconds: 3600 }),
            node("snap_1h", "FUNCTION", fn("snapshot_metrics")),
            node("wait_24h", "WAIT_UNTIL", { timeout_seconds: 82800 }),
            node("snap_24h", "FUNCTION", fn("snapshot_metrics")),
            node("measured", "END"),
            node("rehearsed", "END"),
            node("failed", "END"),
            node("stopped", "END"),
        ],
        edges: [
            edge("clock", "route"),
            edge("route", "stopped"), edge("route", "publish"), edge("route", "wait_short"), edge("route", "wait_long"),
            edge("wait_long", "recheck"), edge("wait_short", "recheck"), edge("recheck", "route"),
            edge("publish", "published"),
            edge("published", "failed"), edge("published", "wait_1h"), edge("published", "rehearsed"),
            edge("wait_1h", "snap_1h"), edge("snap_1h", "wait_24h"), edge("wait_24h", "snap_24h"), edge("snap_24h", "measured"),
        ],
        start: { type: "DATASTORE_EVENT", config: { table_name: "drafts", operations: ["INSERT", "UPDATE"] } },
    });

    assert.ok(shape);
    assert.equal(shape.trouble, null);
    assert.deepEqual(shape.loose, []);
    assert.deepEqual(outlineOf(shape.flow), [
        "clock",
        // Every rule compares the same thing, so it is said once and each arm
        // is only its value. Posting goes straight on; the waits come back
        // round; stopping is the first edge and ends on its own End.
        { fork: "route", then: "publish", on, arms: [
            { when: "'publish'", items: [] },
            { when: "'wait_short'", items: ["wait_short", "recheck", "↩ route"] },
            { when: "'wait_long'", items: ["wait_long", "→ recheck"] },
            { when: null, items: ["stopped"] },
        ] },
        "publish",
        // Rehearsal and failure are one step each; live is the rest of the
        // workflow, and stays on the line. The backticks round `true` go.
        { fork: "published", then: "wait_1h", on: null, arms: [
            { when: "publish.ok == true && publish.dry_run == false", items: [] },
            { when: "publish.ok == true", items: ["rehearsed"] },
            { when: null, items: ["failed"] },
        ] },
        "wait_1h", "snap_1h", "wait_24h", "snap_24h", "measured",
    ]);
    // The flat order is still there, for counts and the run board.
    assert.equal(shape.ordered.length, 15);
});

test("a body step that goes to what follows the loop runs inside every pass, so it is drawn there", () => {
    // Legal to save, and easy to misread. A body step pointing anywhere but
    // its loop keeps the loop's frame (execution/stepper.py move_past), so
    // `after` runs once per item and again when the items run out. Drawing it
    // only after the loop would say the decision breaks out, which it never
    // does; after the loop it is pointed at instead.
    const shape = readShape({
        name: "body-runs-the-tail",
        nodes: [
            node("each", "LOOP", { items_path: "start.rows", child_node_id: "check" }),
            node("check", "DECISION", { rules: [{ condition: "loop.item.ok", next_node_id: "after" }] }),
            node("after", "FUNCTION", { function_name: "record" }),
        ],
        edges: [edge("each", "after"), edge("check", "after")],
        start: { type: "MANUAL", config: null },
    });

    assert.ok(shape);
    assert.deepEqual(outlineOf(shape.flow), [
        { loop: "each", body: [
            { fork: "check", then: "after", on: null, arms: [
                { when: "loop.item.ok", items: [] },
                { when: null, items: [] },
            ] },
            "after",
        ] },
        "→ after",
    ]);
});

test("two arms the same size stay an either/or, each drawn in full", () => {
    const shape = readShape({
        name: "either",
        nodes: [
            node("ask", "DECISION", { rules: [{ condition: "start.ok", next_node_id: "yes" }] }),
            node("yes", "AGENT", { agent_name: "one" }),
            node("no", "AGENT", { agent_name: "two" }),
        ],
        edges: [edge("ask", "no")],
        start: { type: "MANUAL", config: null },
    });
    assert.deepEqual(outlineOf(shape!.flow), [
        { fork: "ask", then: null, on: null, arms: [
            { when: "start.ok", items: ["yes"] },
            { when: null, items: ["no"] },
        ] },
    ]);
});

test("a switch is only a switch when every rule compares the same thing to a value", () => {
    assert.deepEqual(switchOf(["a.b == 'x'", "a.b == `2`"]), { on: "a.b", values: ["'x'", "2"] });
    // `||` binds looser than `==`, so a bare one means half the condition.
    assert.equal(switchOf(["a || b == 'x'", "a || b == 'y'"]), null);
    assert.equal(switchOf(["a == 'x' && b == 'y'", "a == 'x' && b == 'z'"]), null);
    // A double-quoted token is an identifier in JMESPath, not a string.
    assert.equal(switchOf(['a == "x"', 'a == "y"']), null);
    assert.equal(switchOf(["a == 'x'", "c == 'y'"]), null);
    assert.equal(switchOf(["a == 'x'"]), null);
    assert.equal(tidy("n > `5000` && ok == `true` && s == `\"x\"`"), "n > 5000 && ok == true && s == `\"x\"`");
});

test("a decision inside a loop ends the pass, not the run", () => {
    const shape = readShape({
        name: "check-each",
        nodes: [
            node("each", "LOOP", { items_path: "start.rows", child_node_id: "worth-it" }),
            node("worth-it", "DECISION", { rules: [{ condition: "loop.item.score > `3`", next_node_id: "keep" }] }),
            node("keep", "FUNCTION", { function_name: "keep-row" }),
            node("report", "AGENT", { agent_name: "reporter" }),
        ],
        edges: [edge("each", "report"), edge("keep", "each")],
        start: { type: "MANUAL", config: null },
    });

    assert.ok(shape);
    // A guard: the no-match arm skips to the next item and says so, and the
    // matching one carries straight on to `keep`, which stays on the body's
    // main line.
    assert.deepEqual(outlineOf(shape.flow), [
        { loop: "each", body: [
            { fork: "worth-it", then: "keep", on: null, arms: [
                { when: "loop.item.score > 3", items: [] },
                { when: null, items: ["(next one)"] },
            ] },
            "keep",
        ] },
        "report",
    ]);
});

test("a step nothing reaches is found rather than lost", () => {
    // An orphan is a real thing to learn about a workflow — usually a step
    // somebody rewired and left behind. Printing it under its own heading is
    // the only way anybody finds out.
    const shape = readShape({
        name: "contract-renewals",
        nodes: [
            node("wait-out", "WAIT_UNTIL", { timeout_seconds: 5400 }),
            node("draft", "AGENT", { agent_name: "letter-writer" }),
            node("old-notify", "FUNCTION", { function_name: "email-legal" }),
            node("done", "END"),
        ],
        edges: [edge("wait-out", "draft"), edge("draft", "done")],
        start: { type: "EVENT", config: { connector_id: "jira", connector_trigger_id: "issue_created", trigger_config: { project: "FIN" } } },
    });

    assert.ok(shape);
    assert.deepEqual(ids(shape.ordered), ["wait-out", "draft", "done"]);
    assert.deepEqual(ids(shape.orphans), ["old-notify"]);
    // And it is trouble, not tidiness: a step with nothing pointing at it is a
    // second entry node, which the validator refuses to save and the engine
    // refuses to start (domain/graph.py:150). The workflow is broken, and the
    // orphan is why.
    assert.match(shape.trouble ?? "", /Nothing points at 2 of these steps — wait-out, old-notify/);
    assert.equal(shape.ordered[0].detail[0], "Up to 2h.");
    assert.equal(shape.start.detail[0], "Sent by jira, on issue_created.");
    assert.equal(shape.start.detail[1], "Narrowed by project.");
});

test("an empty graph is an empty shape, not a throw", () => {
    const shape = readShape({ name: "brand-new", nodes: [], edges: [], start: null });
    assert.ok(shape);
    assert.deepEqual(shape.ordered, []);
    assert.deepEqual(shape.orphans, []);
    assert.equal(shape.trouble, null);
    // A null start is not a manual one. It is a workflow whose trigger was
    // never set, and saying "runs when you ask" for it is a guess.
    assert.equal(shape.start.says, "How this starts is not recorded.");

    // Missing keys entirely, and a payload that is not a workflow at all.
    const bare = readShape({ name: "bare" });
    assert.deepEqual(bare?.ordered, []);
    assert.equal(readShape(null), null);
    assert.equal(readShape({ description: "no name" }), null);
});

test("an entry that names no step falls back to the wiring, and says so", () => {
    // WorkflowResponse does not carry entry_node_id at all — the backend
    // computes and stores it (domain/workflow.py:40) and leaves it off the
    // wire — so this is the guard for the day it appears, mirroring what the
    // engine does before every run (execution/engine.py:485): trust it only
    // when it names a real node.
    const shape = readShape({
        name: "moved-on",
        entry_node_id: "deleted-step",
        nodes: [node("first", "FORM", { input_schema: {} }), node("second", "END")],
        edges: [edge("first", "second")],
        start: { type: "MANUAL", config: null },
    });

    assert.ok(shape);
    assert.deepEqual(ids(shape.ordered), ["first", "second"]);
    assert.match(shape.trouble ?? "", /deleted-step.*no step by that name/s);

    // And when it does name one, it wins: the server's own answer beats a
    // derivation of it.
    const honoured = readShape({
        name: "honoured",
        entry_node_id: "second",
        nodes: [node("first", "FORM", { input_schema: {} }), node("second", "END")],
        edges: [edge("first", "second")],
        start: { type: "MANUAL", config: null },
    });
    assert.deepEqual(ids(honoured!.ordered), ["second"]);
    assert.deepEqual(ids(honoured!.orphans), ["first"]);
});

test("a graph with no way in still lists every step", () => {
    // Every node pointed at by another: a ring. Nothing can be walked *from*,
    // but every step is still a step this workflow runs, and a shape that is
    // one short lies about what a teammate does.
    const ring = readShape({
        name: "ring",
        nodes: [node("a", "AGENT", { agent_name: "one" }), node("b", "AGENT", { agent_name: "two" })],
        edges: [edge("a", "b"), edge("b", "a")],
        start: { type: "MANUAL", config: null },
    });
    assert.deepEqual(ids(ring!.ordered), ["a", "b"]);
    assert.deepEqual(ring!.orphans, []);
    assert.match(ring!.trouble ?? "", /no first one/);

    // Two starting points is the other way the derivation can fail to land on
    // one. Both are walked, in the order the payload holds them.
    // Two candidates is the other way the derivation fails to land on one. A
    // workflow has one trigger and a run begins at one node, so the first the
    // payload lists heads the order and the rest fall under what nothing
    // reaches — walked, so an orphaned chain still reads in its own order.
    const two = readShape({
        name: "two-starts",
        nodes: [node("a", "END"), node("b", "AGENT", { agent_name: "two" }), node("c", "END")],
        edges: [edge("b", "c")],
        start: { type: "MANUAL", config: null },
    });
    assert.deepEqual(ids(two!.ordered), ["a"]);
    assert.deepEqual(ids(two!.orphans), ["b", "c"]);
    assert.match(two!.trouble ?? "", /Nothing points at 2 of these steps — a, b/);
});

test("a malformed step is a row that says so, never a gap", () => {
    // A missing step is worse than an ugly one: the count stops matching, and
    // somebody reading this to work out what a workflow does is reading a
    // shape that is quietly one step short.
    const shape = readShape({
        name: "ragged",
        nodes: [
            node("start-here", "FORM", { input_schema: {} }),
            "not a node",
            { type: "AGENT", config: { agent_name: "nameless" } },  // no id
            node("mystery", "TELEPORT"),
            node("gone", "DECISION", { rules: [{ condition: "x", next_node_id: "nowhere" }, "not a rule"] }),
        ],
        edges: [edge("start-here", "gone"), edge("start-here", "missing-node")],
        start: { type: "WORMHOLE", config: null },
    });

    assert.ok(shape);
    // Five nodes in, five steps out — split between the two headings, never
    // fewer than went in.
    assert.equal(shape.ordered.length + shape.orphans.length, 5);
    assert.deepEqual(ids(shape.ordered), ["start-here", "gone"]);
    assert.equal(shape.orphans.length, 3);
    // `mystery` heads the leftovers because it is the only one of them with an
    // id; the two that arrived as nonsense follow, in the order they came.
    assert.match(shape.orphans[0].says, /does not know \(TELEPORT\)/);
    assert.equal(shape.orphans[1].unreadable, true);
    assert.match(shape.orphans[1].says, /could not read/);
    assert.equal(shape.orphans[2].unreadable, true);   // a node with no id

    // A rule pointing at a node that does not exist still says where it meant
    // to go — the validator refuses these on save (domain/graph.py:74), so one
    // here means a graph written round the API.
    assert.deepEqual(shape.ordered[1].rules, [{ when: "x", to: "nowhere" }]);
    assert.match(shape.ordered[1].detail[0], /unreadable/);
    const fork = shape.flow[1];
    assert.equal(fork.type, "fork");
    if (fork.type === "fork") assert.deepEqual(fork.arms[0].items, [{ type: "jump", to: null, id: "nowhere", back: false }]);
    // And the outline keeps every step too, the leftovers under their heads.
    assert.equal(shape.loose.flat().length, 3);
    assert.match(shape.start.says, /does not know \(WORMHOLE\)/);
});

test("every node kind says what it does, and none of them throws on an empty config", () => {
    // config is required on every node but FORM's schema, AGENT's name and
    // LOOP's paths are all reachable as nothing over the wire.
    const bare = ["FORM", "AGENT", "FUNCTION", "DECISION", "LOOP", "WAIT_UNTIL", "END"].map(
        (kind, at) => readFlowStep({ id: "n" + at, type: kind, config: {} }, at),
    );
    assert.deepEqual(bare.map((step) => step.says), [
        "Asks a person",
        "Hands it to an agent the payload did not name",
        "Runs a function the payload did not name",
        "Branches",
        "Repeats for each item",
        "Waits",
        "Ends the run",
    ]);
    assert.ok(bare.every((step) => !step.unreadable));
    // An acknowledgement form: no fields is a legal shape and a real one.
    assert.match(bare[0].detail[0], /Nothing to fill in/);
    assert.match(bare[4].detail[1], /points at nothing/);
    assert.match(bare[5].detail[0], /did not carry/);
});

/* ── the board ─────────────────────────────────────────────────────── */

import { boardOf, sayHeldBy } from "../src/workflow/board.ts";

const boardShape = readShape({
    name: "hire",
    start: { type: "MANUAL" },
    nodes: [
        { id: "screen", type: "FORM", label: "Screen the CV", config: {} },
        { id: "route", type: "DECISION", config: { rules: [{ condition: "x", next_node_id: "legal" }] } },
        { id: "legal", type: "FORM", label: "Legal review", config: {} },
        { id: "draft", type: "AGENT", config: { agent_name: "writer" } },
        { id: "done", type: "END", config: {} },
    ],
    edges: [
        { source: "screen", target: "route" },
        { source: "route", target: "draft" },
        { source: "legal", target: "draft" },
        { source: "draft", target: "done" },
    ],
});

function boardRun(id: string, extra: Record<string, unknown>) {
    return readRun({ id, workflow_id: "wf-hire", status: "WAITING", ...extra })!;
}

test("the board has a lane per step a run can sit at, in run order", () => {
    const board = boardOf(boardShape, [], "wf-hire");
    assert.deepEqual(board.columns.map((column) => column.id), ["screen", "legal", "draft"]);
    assert.equal(board.count, 0);
});

test("runs land at their step; finished runs and other workflows stay off", () => {
    const board = boardOf(boardShape, [
        boardRun("a", { current_node_id: "legal", started_at: "2026-09-20T00:00:00Z" }),
        boardRun("b", { current_node_id: "legal", started_at: "2026-09-10T00:00:00Z" }),
        boardRun("c", { current_node_id: "screen" }),
        boardRun("done", { status: "COMPLETED", current_node_id: "legal" }),
        boardRun("other", { workflow_id: "wf-else", current_node_id: "legal" }),
    ], "wf-hire");
    const legal = board.columns.find((column) => column.id === "legal")!;
    assert.deepEqual(legal.cards.map((card) => card.run.id), ["b", "a"], "longest stuck first");
    assert.equal(board.count, 3);
});

test("a run at no known step is kept in a trailing lane rather than dropped", () => {
    const board = boardOf(boardShape, [
        boardRun("pending", { status: "PENDING" }),
        boardRun("gone", { current_node_id: "renamed-away" }),
    ], "wf-hire");
    const last = board.columns.at(-1)!;
    assert.equal(last.id, "");
    assert.equal(last.step, null);
    assert.equal(last.cards.length, 2);
});

test("a wait of yours puts the run first in its lane, at the wait's step", () => {
    const mine = readWait({ ...wait, run_id: "late", node_id: "legal", created_at: "2026-09-25T00:00:00Z" })!;
    const board = boardOf(boardShape, [
        boardRun("early", { current_node_id: "legal", started_at: "2026-09-01T00:00:00Z" }),
        boardRun("late", { current_node_id: "screen", started_at: "2026-09-24T00:00:00Z" }),
    ], "wf-hire", new Map([["late", mine]]));
    const legal = board.columns.find((column) => column.id === "legal")!;
    assert.deepEqual(legal.cards.map((card) => card.run.id), ["late", "early"]);
    assert.equal(legal.cards[0].since, "2026-09-25T00:00:00Z");
    assert.equal(board.mine, 1);
});

test("a card says what it is held by from the kind of step", () => {
    const [screen, , draft] = boardOf(boardShape, [], "wf-hire").columns;
    assert.equal(sayHeldBy(screen.step, boardRun("x", {})), "Waiting on a person");
    assert.equal(sayHeldBy(draft.step, boardRun("x", { status: "RUNNING" })), "With an agent");
    assert.equal(sayHeldBy(null, boardRun("x", { status: "PENDING" })), "Starting");
});

test("a run summary reads its title and the wait it is parked on", () => {
    const read = readRun({
        id: "r", workflow_id: "f", status: "WAITING", title: "Priya Shah · Designer",
        waiting_on: { node_id: "legal", wait_type: "HUMAN", assigned_pod_member_id: "m-9", since: "2026-10-01T00:00:00Z" },
    })!;
    assert.equal(read.title, "Priya Shah · Designer");
    assert.deepEqual(read.waitingOn, { nodeId: "legal", type: "HUMAN", assigneeId: "m-9", since: "2026-10-01T00:00:00Z" });
    const bare = readRun({ id: "r", status: "RUNNING", title: "", waiting_on: { wait_type: "AGENT" } })!;
    assert.equal(bare.title, null, "an empty title is no title");
    assert.equal(bare.waitingOn, null, "a wait with no step cannot place the run");
});

test("a run's own wait places it and dates it, for everybody's runs", () => {
    const board = boardOf(boardShape, [
        boardRun("w", {
            current_node_id: "screen", started_at: "2026-09-01T00:00:00Z",
            waiting_on: { node_id: "legal", wait_type: "HUMAN", since: "2026-09-30T00:00:00Z" },
        }),
        boardRun("bare", { current_node_id: "screen", started_at: "2026-09-02T00:00:00Z" }),
    ], "wf-hire");
    const legal = board.columns.find((column) => column.id === "legal")!;
    assert.equal(legal.cards[0].run.id, "w");
    assert.equal(legal.cards[0].since, "2026-09-30T00:00:00Z");
    assert.equal(legal.cards[0].here, true);
    const screen = board.columns.find((column) => column.id === "screen")!;
    assert.equal(screen.cards[0].here, false, "a start time is not time at the step");
});

test("the wait's kind beats the step's kind when saying what a run is held by", () => {
    const [screen] = boardOf(boardShape, [], "wf-hire").columns;
    const run = boardRun("x", { status: "RUNNING", waiting_on: { node_id: "screen", wait_type: "AGENT" } });
    assert.equal(sayHeldBy(screen.step, run), "With an agent");
});

test("a run assigned to the viewer is theirs even when their wait list missed it", () => {
    const board = boardOf(boardShape, [
        boardRun("old", { current_node_id: "legal", started_at: "2026-09-01T00:00:00Z" }),
        boardRun("mine", {
            current_node_id: "legal", started_at: "2026-09-20T00:00:00Z",
            waiting_on: { node_id: "legal", wait_type: "HUMAN", assigned_pod_member_id: "m-me" },
        }),
    ], "wf-hire", new Map(), "m-me");
    const legal = board.columns.find((column) => column.id === "legal")!;
    assert.deepEqual(legal.cards.map((card) => card.run.id), ["mine", "old"]);
    assert.equal(legal.cards[0].yours, true);
    assert.equal(legal.cards[0].mine, null, "no wait detail without the list");
    assert.equal(board.mine, 1);
});
