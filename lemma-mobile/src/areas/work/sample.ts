import { SAMPLE_LEMMAS } from "../../data/sample";
import { readPodConnectors } from "@web/data/pod-connectors";
import { agentChanges, agentRows, readAgentDetail } from "@web/data/agents";
import { readAssignments, readRun, readRunDetail, readRuns, readWorkflows, stillGoing } from "@web/workflow/runs";
import { readRuntime, type Runtime } from "@web/data/runtimes";
import { readRun as readScheduleRun, readRuns as readScheduleRuns, readSchedule, readSchedules } from "@web/schedule/schedules";
import type { AgentChat, Member, Surface, WorkSource } from "./source";

/** What a Lemma runs, as sample data: shaped like a real Lemma, never a real
 *  one. Raw payloads in the API's own shapes, read by the web's own readers,
 *  so sample mode exercises the same reading code a live account does.
 *
 *  Support carries every state worth drawing: a run waiting on your form, one
 *  running, one that failed where it can be retried, one cancelled; a
 *  schedule the breaker stopped, a paused one, one someone else shared; an
 *  agent you may edit, one you may only read, one that takes arguments; a
 *  plan. Launch and Books hold the two forms Home's sample asks for. Writes
 *  stay in memory until the app restarts. */

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

const [SUPPORT, LAUNCH, , , BOOKS] = SAMPLE_LEMMAS;

/** The sample reader (`data/sample.ts`'s `me`), and their place in Support. */
const ME = "me";
const MY_MEMBER = "member-me";
const SAM = "user-sam";

const MEMBERS: Member[] = [
    { id: MY_MEMBER, userId: ME, name: "Ada Lovelace" },
    { id: "member-sam", userId: SAM, name: "Sam Okafor" },
    { id: "member-dev", userId: "user-dev", name: "Dev Patel" },
];

const ALL = ["workflow.read", "workflow.update", "workflow.execute"];

/* ── workflows ───────────────────────────────────────────────────────── */

type Raw = Record<string, unknown>;

interface Flow {
    podId: string;
    summary: Raw;
    graph: Raw;
}

const node = (id: string, type: string, label: string | null, config: Raw = {}): Raw => ({ id, type, label, config });
const edge = (source: string, target: string): Raw => ({ id: source + "_to_" + target, source, target });

const REFUND_FORM = {
    type: "object",
    properties: {
        approve: { type: "boolean", title: "Refund it" },
        amount: { type: "number", title: "Amount", description: "In dollars, as the customer paid." },
        reason: { type: "string", title: "Reason", enum: ["Arrived broken", "Never arrived", "Wrong item", "Changed their mind"] },
        note: { type: "string", title: "Note to the customer", format: "textarea" },
    },
    required: ["approve", "amount"],
};
const REFUND_UI = { "ui:order": ["approve", "amount", "reason", "note"], note: { "ui:widget": "textarea" } };

const QUOTE_FORM = {
    type: "object",
    properties: {
        partner: { type: "string", title: "Partner" },
        amount: { type: "number", title: "Quoted amount" },
        currency: { type: "string", title: "Currency", enum: ["USD", "EUR", "GBP"] },
        valid_until: { type: "string", format: "date", title: "Valid until" },
        notes: { type: "string", title: "Notes" },
    },
    required: ["partner", "amount", "currency"],
};

const SUPPLIER_FORM = {
    type: "object",
    properties: {
        supplier: { type: "string", title: "Supplier name" },
        email: { type: "string", format: "email", title: "Billing email" },
        terms: { type: "string", title: "Payment terms", enum: ["Net 15", "Net 30", "Net 60"] },
        approved: { type: "boolean", title: "Approved to pay" },
    },
    required: ["supplier", "email", "terms"],
};

function flows(): Flow[] {
    return [
        {
            podId: SUPPORT.id,
            summary: { id: "wf-chase", name: "chase_unanswered_tickets", description: "Nudges every ticket nobody has answered in a day.", node_count: 4, node_types: ["FUNCTION", "LOOP", "AGENT", "END"], is_active: true, mode: "GLOBAL", allowed_actions: ALL, node_targets: ["function:find_unanswered", "agent:reply_writer"] },
            graph: {
                name: "chase_unanswered_tickets",
                description: "Nudges every ticket nobody has answered in a day.",
                is_active: true,
                mode: "GLOBAL",
                start: { type: "SCHEDULED", config: { cron: "0 * * * *" } },
                nodes: [
                    node("find", "FUNCTION", "Find tickets with no reply in a day", { function_name: "find_unanswered" }),
                    node("each", "LOOP", "For each ticket", { items_path: "find.tickets", child_node_id: "nudge" }),
                    node("nudge", "AGENT", "Write the nudge", { agent_name: "reply_writer" }),
                    node("done", "END", null),
                ],
                edges: [edge("find", "each"), edge("each", "done"), edge("nudge", "each")],
                allowed_actions: ALL,
            },
        },
        {
            podId: SUPPORT.id,
            summary: { id: "wf-triage", name: "triage_new_tickets", description: "Reads each new ticket, sends refunds to a person, answers the rest.", node_count: 5, node_types: ["AGENT", "DECISION", "FORM", "AGENT", "END"], is_active: true, mode: "GLOBAL", allowed_actions: ALL, node_targets: ["agent:triager", "agent:reply_writer"] },
            graph: {
                name: "triage_new_tickets",
                description: "Reads each new ticket, sends refunds to a person, answers the rest.",
                is_active: true,
                mode: "GLOBAL",
                start: { type: "DATASTORE_EVENT", config: { table_name: "tickets", operations: ["INSERT"] } },
                nodes: [
                    node("read", "AGENT", "Read the ticket", { agent_name: "triager" }),
                    node("route", "DECISION", "Is it a refund?", { question: { routes: { true: "refund", false: "reply" }, unsure_next_node_id: "reply" } }),
                    node("refund", "FORM", "Approve the refund", { input_schema: REFUND_FORM, ui_schema: REFUND_UI, assignee_pod_member_id: MY_MEMBER }),
                    node("reply", "AGENT", "Send the reply", { agent_name: "reply_writer" }),
                    node("done", "END", null),
                ],
                edges: [edge("read", "route"), edge("route", "reply"), edge("refund", "reply"), edge("reply", "done")],
                allowed_actions: ALL,
            },
        },
        {
            podId: SUPPORT.id,
            summary: { id: "wf-weekly", name: "weekly_report", description: "Your own Friday summary of what you answered.", node_count: 2, node_types: ["AGENT", "END"], is_active: false, mode: "USER", allowed_actions: ALL, node_targets: ["agent:reply_writer"] },
            graph: {
                name: "weekly_report",
                description: "Your own Friday summary of what you answered.",
                is_active: false,
                mode: "USER",
                start: { type: "SCHEDULED", config: { cron: "0 16 * * 5" } },
                nodes: [node("sum", "AGENT", "Sum up the week", { agent_name: "reply_writer" }), node("done", "END", null)],
                edges: [edge("sum", "done")],
                allowed_actions: ALL,
            },
        },
        {
            podId: SUPPORT.id,
            summary: { id: "wf-vip", name: "vip_escalation", description: "When a VIP writes in Slack, a person hears at once.", node_count: 2, node_types: ["AGENT", "END"], is_active: true, mode: "GLOBAL", allowed_actions: ALL, node_targets: ["agent:escalations"] },
            graph: {
                name: "vip_escalation",
                description: "When a VIP writes in Slack, a person hears at once.",
                is_active: true,
                mode: "USER",
                start: { type: "EVENT", config: { connector_id: "slack", connector_trigger_id: "message_posted" } },
                nodes: [node("tell", "AGENT", "Tell the account owner", { agent_name: "escalations" }), node("done", "END", null)],
                edges: [edge("tell", "done")],
                allowed_actions: ALL,
            },
        },
        {
            podId: LAUNCH.id,
            summary: { id: "wf-quotes", name: "partner_quotes", description: "Collects each partner’s quote before the launch budget is set.", node_count: 3, node_types: ["FORM", "AGENT", "END"], is_active: true, mode: "GLOBAL", allowed_actions: ALL, node_targets: [] },
            graph: {
                name: "partner_quotes",
                description: "Collects each partner’s quote before the launch budget is set.",
                is_active: true,
                mode: "GLOBAL",
                start: { type: "MANUAL" },
                nodes: [node("quote", "FORM", "The partner’s quote", { input_schema: QUOTE_FORM }), node("budget", "AGENT", "Update the budget", { agent_name: "POD_DEFAULT" }), node("done", "END", null)],
                edges: [edge("quote", "budget"), edge("budget", "done")],
                allowed_actions: ALL,
            },
        },
        {
            podId: BOOKS.id,
            summary: { id: "wf-supplier", name: "supplier_onboarding", description: "A new supplier is checked and set up before the first payment.", node_count: 3, node_types: ["FORM", "FUNCTION", "END"], is_active: true, mode: "GLOBAL", allowed_actions: ALL, node_targets: ["function:create_supplier"] },
            graph: {
                name: "supplier_onboarding",
                description: "A new supplier is checked and set up before the first payment.",
                is_active: true,
                mode: "GLOBAL",
                start: { type: "MANUAL" },
                nodes: [node("details", "FORM", "Supplier details", { input_schema: SUPPLIER_FORM }), node("create", "FUNCTION", "Add them to the books", { function_name: "create_supplier" }), node("done", "END", null)],
                edges: [edge("details", "create"), edge("create", "done")],
                allowed_actions: ALL,
            },
        },
    ];
}

/* ── runs ────────────────────────────────────────────────────────────── */

function iso(ago: number, now: number): string {
    return new Date(now - ago).toISOString();
}

interface Step {
    node: string;
    status: string;
    ago: number;
    took: number | null;
    output?: unknown;
    error?: string;
    ref?: string;
}

function steps(list: Step[], now: number): Raw[] {
    return list.map((step, index) => ({
        step_index: index,
        node_id: step.node,
        status: step.status,
        started_at: iso(step.ago, now),
        completed_at: step.took === null ? null : iso(step.ago - step.took, now),
        error: step.error ?? null,
        output_data: step.output,
        external_ref: step.ref ?? null,
    }));
}

function wait(id: string, runId: string, workflowId: string, podId: string, nodeId: string, type: string, ago: number, now: number, payload: Raw = {}, ref: string | null = null): Raw {
    return { id, run_id: runId, workflow_id: workflowId, pod_id: podId, node_id: nodeId, wait_type: type, payload, created_at: iso(ago, now), external_ref: ref };
}

/** A run as `workflows.runs.get` answers it. */
function run(input: {
    id: string; workflow: string; podId: string; status: string; ago: number; took?: number | null; start?: string; user?: string;
    current?: string | null; failed?: string | null; error?: string | null; title?: string | null; steps: Raw[]; wait?: Raw | null; context?: Raw;
}, now: number): Raw {
    const active = input.wait ?? null;
    return {
        id: input.id,
        workflow_id: input.workflow,
        pod_id: input.podId,
        status: input.status,
        failed_node_id: input.failed ?? null,
        current_node_id: input.current ?? null,
        error: input.error ?? null,
        started_at: iso(input.ago, now),
        created_at: iso(input.ago, now),
        completed_at: input.took === undefined || input.took === null ? null : iso(input.ago - input.took, now),
        start_type: input.start ?? "MANUAL",
        user_id: input.user ?? ME,
        title: input.title ?? null,
        waiting_on: active ? { node_id: active.node_id, wait_type: active.wait_type, assigned_pod_member_id: (active.payload as Raw)?.assignee ?? null, since: active.created_at } : null,
        step_history: input.steps,
        active_wait: active,
        execution_context: input.context ?? {},
    };
}

const REPLY_CHAT = "support-tone";

function runs(now: number): Raw[] {
    const out: Raw[] = [];

    /* chase_unanswered_tickets: hourly, one going now, one that failed at a
       step it can be retried from. */
    out.push(run({
        id: "run-chase-0", workflow: "wf-chase", podId: SUPPORT.id, status: "RUNNING", ago: 4 * MIN, start: "SCHEDULED", current: "nudge",
        steps: steps([
            { node: "find", status: "COMPLETED", ago: 4 * MIN, took: 2_000, output: { tickets: ["T-1043", "T-1047", "T-1051"], summary: "Three tickets have waited more than a day." } },
            { node: "each", status: "COMPLETED", ago: 4 * MIN - 2_000, took: 50 },
            { node: "nudge", status: "COMPLETED", ago: 3 * MIN, took: 40_000, output: { summary: "Nudged Northwind about T-1043.", ticket: "T-1043" }, ref: REPLY_CHAT },
            { node: "nudge", status: "COMPLETED", ago: 2 * MIN, took: 35_000, output: { summary: "Nudged Globex about T-1047.", ticket: "T-1047" }, ref: REPLY_CHAT },
            { node: "nudge", status: "RUNNING", ago: 50_000, took: null, ref: REPLY_CHAT },
        ], now),
        wait: wait("wait-chase-0", "run-chase-0", "wf-chase", SUPPORT.id, "nudge", "AGENT", 50_000, now, { agent_name: "reply_writer" }, REPLY_CHAT),
        context: { find: { tickets: ["T-1043", "T-1047", "T-1051"] } },
    }, now));
    for (let index = 1; index < 12; index += 1) {
        const ago = 4 * MIN + index * 45 * MIN;
        const failed = index === 3;
        out.push(run({
            id: "run-chase-" + index, workflow: "wf-chase", podId: SUPPORT.id, status: failed ? "FAILED" : "COMPLETED", ago, took: failed ? 31_000 : 2 * MIN + index * 1_000, start: "SCHEDULED",
            failed: failed ? "find" : null, error: failed ? "The tickets table did not answer within 30 seconds." : null,
            steps: steps(failed
                ? [{ node: "find", status: "FAILED", ago, took: 31_000, error: "The tickets table did not answer within 30 seconds." }]
                : [
                    { node: "find", status: "COMPLETED", ago, took: 2_000, output: { tickets: ["T-10" + (20 + index)], summary: "One ticket has waited more than a day." } },
                    { node: "each", status: "COMPLETED", ago: ago - 2_000, took: 50 },
                    { node: "nudge", status: "COMPLETED", ago: ago - 3_000, took: 50_000, output: { summary: "Nudged the customer.", ticket: "T-10" + (20 + index) }, ref: REPLY_CHAT },
                    { node: "done", status: "COMPLETED", ago: ago - 60_000, took: 0 },
                ], now),
        }, now));
    }

    /* triage_new_tickets: one waiting on your yes, one with an agent, the
       rest done down one branch or the other. */
    out.push(run({
        id: "run-triage-0", workflow: "wf-triage", podId: SUPPORT.id, status: "WAITING", ago: 40 * MIN, start: "DATASTORE_EVENT", current: "refund", title: "Northwind: lamp arrived broken",
        steps: steps([
            { node: "read", status: "COMPLETED", ago: 40 * MIN, took: 25_000, output: { summary: "Northwind’s lamp arrived broken; they want their $240 back.", customer: "Northwind", amount: 240 }, ref: REPLY_CHAT },
            { node: "route", status: "COMPLETED", ago: 39 * MIN, took: 900, output: { answer: true, confidence: 0.94, route: "refund" } },
            { node: "refund", status: "WAITING", ago: 39 * MIN, took: null },
        ], now),
        wait: wait("wait-triage-0", "run-triage-0", "wf-triage", SUPPORT.id, "refund", "HUMAN", 39 * MIN, now, { input_schema: REFUND_FORM, ui_schema: REFUND_UI, assignee: MY_MEMBER }),
        context: { read: { customer: "Northwind", amount: 240 } },
    }, now));
    out.push(run({
        id: "run-triage-1", workflow: "wf-triage", podId: SUPPORT.id, status: "RUNNING", ago: 10 * MIN, start: "DATASTORE_EVENT", current: "reply", title: "Initech: where is my invoice?",
        steps: steps([
            { node: "read", status: "COMPLETED", ago: 10 * MIN, took: 20_000, output: { summary: "Initech wants last month’s invoice again.", customer: "Initech" }, ref: REPLY_CHAT },
            { node: "route", status: "COMPLETED", ago: 9 * MIN, took: 700, output: { answer: false, confidence: 0.97, route: "reply" } },
            { node: "reply", status: "RUNNING", ago: 9 * MIN, took: null, ref: REPLY_CHAT },
        ], now),
        wait: wait("wait-triage-1", "run-triage-1", "wf-triage", SUPPORT.id, "reply", "AGENT", 9 * MIN, now, { agent_name: "reply_writer" }, REPLY_CHAT),
    }, now));
    for (let index = 2; index < 28; index += 1) {
        const ago = 10 * MIN + index * 20 * MIN;
        const refund = index % 5 === 0;
        const cancelled = index === 7;
        out.push(run({
            id: "run-triage-" + index, workflow: "wf-triage", podId: SUPPORT.id, status: cancelled ? "CANCELLED" : "COMPLETED", ago, took: 3 * MIN, start: "DATASTORE_EVENT", user: index % 3 === 0 ? SAM : ME,
            title: refund ? "A refund for order " + (4400 + index) : "A question about order " + (4400 + index),
            steps: steps(cancelled
                ? [{ node: "read", status: "COMPLETED", ago, took: 20_000, output: { summary: "A duplicate of an earlier ticket." }, ref: REPLY_CHAT }]
                : [
                    { node: "read", status: "COMPLETED", ago, took: 20_000, output: { summary: refund ? "Wants a refund." : "Asks where the order is." }, ref: REPLY_CHAT },
                    { node: "route", status: "COMPLETED", ago: ago - 20_000, took: 800, output: { answer: refund, confidence: 0.9, route: refund ? "refund" : "reply" } },
                    ...(refund ? [{ node: "refund", status: "COMPLETED", ago: ago - 21_000, took: 60_000, output: { approve: true, amount: 60, reason: "Arrived broken" } }] : []),
                    { node: "reply", status: "COMPLETED", ago: ago - 90_000, took: 40_000, output: { summary: "Replied with the tracking link." }, ref: REPLY_CHAT },
                    { node: "done", status: "COMPLETED", ago: ago - 130_000, took: 0 },
                ], now),
        }, now));
    }

    /* The two forms Home's sample asks for, elsewhere. */
    out.push(run({
        id: "run-quotes", workflow: "wf-quotes", podId: LAUNCH.id, status: "WAITING", ago: 26 * HOUR, current: "quote", title: "Print partner",
        steps: steps([{ node: "quote", status: "WAITING", ago: 26 * HOUR, took: null }], now),
        wait: wait("wait-launch-form", "run-quotes", "wf-quotes", LAUNCH.id, "quote", "HUMAN", 26 * HOUR, now, { input_schema: QUOTE_FORM, assignee: MY_MEMBER }),
    }, now));
    out.push(run({
        id: "run-supplier", workflow: "wf-supplier", podId: BOOKS.id, status: "WAITING", ago: DAY + 3 * HOUR, current: "details", title: "Umbrella Supplies",
        steps: steps([{ node: "details", status: "WAITING", ago: DAY + 3 * HOUR, took: null }], now),
        wait: wait("wait-books-supplier", "run-supplier", "wf-supplier", BOOKS.id, "details", "HUMAN", DAY + 3 * HOUR, now, { input_schema: SUPPLIER_FORM, assignee: MY_MEMBER }),
    }, now));
    return out;
}

/* ── schedules ───────────────────────────────────────────────────────── */

const MINE = ["schedule.read", "schedule.update", "schedule.delete"];

function schedules(now: number): Raw[] {
    return [
        { id: "sched-digest", name: "morning-digest", schedule_type: "TIME", config: { cron: "0 9 * * 1-5", timezone: "Europe/London" }, agent_name: "POD_DEFAULT", instruction: "Sum up what waited longest yesterday, and what is still open.", is_active: true, user_id: ME, visibility: "POD", last_fired_at: iso(9 * HOUR, now), last_fire_status: "TRIGGERED", consecutive_failures: 0, allowed_actions: MINE, created_at: iso(40 * DAY, now), pod_id: SUPPORT.id },
        { id: "sched-sweep", name: "stale-ticket-sweep", schedule_type: "TIME", config: { cron: "0 7 * * *" }, agent_name: "reply_writer", instruction: "Close tickets nobody has written on in two weeks, saying why.", is_active: false, paused_by_failures: true, user_id: ME, visibility: "POD", last_fired_at: iso(26 * HOUR, now), last_fire_status: "ERROR", last_error: "The model refused the request: the output was longer than the limit.", consecutive_failures: 5, allowed_actions: MINE, created_at: iso(30 * DAY, now), pod_id: SUPPORT.id },
        { id: "sched-chase", name: "chase-hourly", schedule_type: "TIME", config: { cron: "0 * * * *" }, workflow_name: "chase_unanswered_tickets", is_active: true, user_id: ME, visibility: "POD", last_fired_at: iso(4 * MIN, now), last_fire_status: "TRIGGERED", consecutive_failures: 0, allowed_actions: MINE, created_at: iso(20 * DAY, now), pod_id: SUPPORT.id },
        { id: "sched-triage", name: "triage-on-new-ticket", schedule_type: "DATASTORE", config: { table_name: "tickets", operations: ["INSERT"] }, workflow_name: "triage_new_tickets", is_active: true, user_id: ME, visibility: "POD", last_fired_at: iso(10 * MIN, now), last_fire_status: "TRIGGERED", consecutive_failures: 0, allowed_actions: MINE, created_at: iso(20 * DAY, now), pod_id: SUPPORT.id },
        { id: "sched-vip", name: "vip-slack-watch", schedule_type: "WEBHOOK", config: { source: "slack" }, connector_trigger_id: "slack_message_posted", account_id: "acct-sam-slack", agent_name: "reply_writer", filter_instruction: "Only when the customer is on the VIP list.", instruction: "Tell the account owner in their own chat.", is_active: true, user_id: SAM, visibility: "POD", last_fired_at: iso(3 * HOUR, now), last_fire_status: "FILTERED", consecutive_failures: 0, allowed_actions: ["schedule.read"], created_at: iso(12 * DAY, now), pod_id: SUPPORT.id },
        { id: "sched-mine", name: "my-friday-wrap", schedule_type: "TIME", config: { cron: "0 16 * * 5" }, agent_name: "reply_writer", instruction: "Tell me what I answered this week.", is_active: false, user_id: ME, visibility: "PERSONAL", last_fired_at: iso(8 * DAY, now), last_fire_status: "TRIGGERED", consecutive_failures: 0, allowed_actions: MINE, created_at: iso(15 * DAY, now), pod_id: SUPPORT.id },
        { id: "sched-radar", name: "renewal-radar", schedule_type: "TIME", config: { cron: "0 8 * * 1" }, agent_name: "POD_DEFAULT", instruction: "List renewals in the next 30 days and who owns each.", is_active: true, user_id: ME, visibility: "POD", last_fired_at: iso(4 * DAY, now), last_fire_status: "TRIGGERED", consecutive_failures: 0, allowed_actions: MINE, created_at: iso(60 * DAY, now), pod_id: SAMPLE_LEMMAS[2].id },
    ];
}

function scheduleRuns(id: string, skipped: boolean, now: number): Raw[] {
    const at = (ago: number) => iso(ago, now);
    if (id === "sched-sweep") {
        return Array.from({ length: 6 }, (_, index) => ({
            id: "sweep-" + index,
            status: index < 5 ? "FAILED" : "COMPLETED",
            attempts: index < 5 ? 3 : 1,
            target_kind: "AGENT",
            target_run_id: REPLY_CHAT,
            error_type: index < 5 ? "AgentRunFailed" : null,
            error_code: index < 5 ? "output_too_long" : null,
            source_occurred_at: at(26 * HOUR + index * DAY),
            created_at: at(26 * HOUR + index * DAY),
        }));
    }
    if (id === "sched-vip") {
        return skipped
            ? [0, 1, 2].map((index) => ({ id: "vip-skip-" + index, status: "FILTERED", attempts: 1, target_kind: "AGENT", llm_output: { should_proceed: false, _decision: { confidence: { should_proceed: 0.88 - index * 0.05 } } }, source_occurred_at: at(3 * HOUR + index * 5 * HOUR) }))
            : [{ id: "vip-0", status: "COMPLETED", attempts: 1, target_kind: "AGENT", target_run_id: REPLY_CHAT, llm_output: { should_proceed: true, _decision: { confidence: { should_proceed: 0.93 } } }, source_occurred_at: at(2 * DAY) }];
    }
    if (id === "sched-chase") {
        return Array.from({ length: 8 }, (_, index) => ({
            id: "chase-fire-" + index,
            status: index === 3 ? "TARGET_FAILED" : index === 0 ? "DISPATCHED" : "COMPLETED",
            attempts: 1,
            target_kind: "WORKFLOW",
            target_run_id: "run-chase-" + index,
            source_occurred_at: at(4 * MIN + index * 45 * MIN),
        }));
    }
    if (id === "sched-triage") {
        return Array.from({ length: 6 }, (_, index) => ({ id: "triage-fire-" + index, status: index < 2 ? "DISPATCHED" : "COMPLETED", attempts: 1, target_kind: "WORKFLOW", target_run_id: "run-triage-" + index, payload: { id: "ticket-" + index }, source_occurred_at: at(10 * MIN + index * 20 * MIN) }));
    }
    return Array.from({ length: 4 }, (_, index) => ({ id: id + "-fire-" + index, status: "COMPLETED", attempts: 1, target_kind: "AGENT", target_run_id: REPLY_CHAT, source_occurred_at: at(9 * HOUR + index * DAY) }));
}

/* ── agents ──────────────────────────────────────────────────────────── */

function agents(now: number): Record<string, Raw[]> {
    const runtime = { profile_id: "rt-anthropic", model_name: "anthropic/models/sonnet", settings: { reasoning_effort: "medium", max_output_tokens: 4000 } };
    return {
        [SUPPORT.id]: [
            { id: "agent-front", name: "pod_default", kind: "POD_DEFAULT", description: "", instruction: "", toolsets: [], visibility: "POD", allowed_actions: ["agent.read", "agent.execute", "agent.update", "agent.delete"], takes_input: false, updated_at: iso(2 * DAY, now) },
            {
                id: "agent-writer", name: "reply_writer", kind: "AGENT", description: "Drafts replies to customers in the team’s voice.",
                instruction: "You write replies to customers for the Support team.\n\nKeep each reply under 120 words. Lead with what will happen next and when. Sign off as \"The team\".\n\nNever promise a refund: a refund over $50 is a person's call, and the triage workflow asks one.\n\nWhen a ticket names an order, look it up in the orders table before you answer.",
                toolsets: ["POD", "MESSAGING", "WEB_SEARCH"], visibility: "POD", allowed_actions: ["agent.read", "agent.execute", "agent.update", "agent.delete"], takes_input: false, has_pinned_runtime: true, agent_runtime: runtime,
                permissions: { grants: [{ resource_type: "TABLE", resource_name: "tickets", permission_ids: ["table.read", "table.update"] }, { resource_type: "TABLE", resource_name: "customers", permission_ids: ["table.read"] }] },
                updated_at: iso(5 * DAY, now),
            },
            {
                id: "agent-triager", name: "triager", kind: "AGENT", description: "Reads a new ticket and says what it is about.",
                instruction: "Read the ticket and answer with the customer, what they want, and the amount when there is one.",
                toolsets: ["POD"], visibility: "POD", allowed_actions: ["agent.read", "agent.execute"], takes_input: true,
                input_schema: { type: "object", properties: { ticket_id: { type: "string" } }, required: ["ticket_id"] },
                output_schema: { type: "object", properties: { customer: { type: "string" }, summary: { type: "string" }, amount: { type: "number" } } },
                updated_at: iso(9 * DAY, now),
            },
            {
                id: "agent-auditor", name: "refund_auditor", kind: "AGENT", description: "Checks each refund against the policy before it is paid.",
                instruction: "Compare each approved refund with the refund policy page and flag any that do not fit it.",
                toolsets: ["POD", "SKILLS"], visibility: "RESTRICTED", allowed_actions: ["agent.read", "agent.execute"], takes_input: false,
                updated_at: iso(14 * DAY, now),
            },
        ],
    };
}

const RUNTIMES: Runtime[] = [
    readRuntime({ id: "rt-anthropic", name: "Anthropic", kind: "PROVIDER", scope: "ORGANIZATION", default_model_name: "anthropic/models/sonnet", model_catalog: [{ name: "anthropic/models/sonnet", display_name: "Sonnet", capabilities: ["REASONING"] }, { name: "anthropic/models/haiku", display_name: "Haiku" }] }),
].filter((one): one is Runtime => one !== null);

const SURFACES: Record<string, Surface[]> = {
    [SUPPORT.id]: [
        { id: "surface-wa", platform: "WHATSAPP", handle: "+44 20 7946 0000", active: true, status: "ACTIVE", mine: true, agentKey: null },
        { id: "surface-mail", platform: "EMAIL", handle: "support@acme.example", active: true, status: "ACTIVE", mine: true, agentKey: null },
        { id: "surface-writer", platform: "SLACK", handle: "#customer-replies", active: false, status: "NEEDS_SETUP", mine: false, agentKey: "reply_writer" },
    ],
};

/* ── the plan ────────────────────────────────────────────────────────── */

const SUPPORT_PLAN = [
    "---",
    "outcome: Every customer hears back within a day",
    "kind: week",
    "target: \"40\"",
    "measure: SELECT count(*) FROM tickets WHERE replied_within_hours <= 24 AND created_at > now() - interval '7 days'",
    "---",
    "",
    "## What must be true",
    "- Every customer hears back within a day — Ada, Oct 2",
    "- A refund over $50 waits for a person — Ada, Oct 2",
    "",
    "## How we work",
    "- Unanswered tickets are nudged every hour — Lemma, Oct 3",
    "- Replies follow the tone page — Sam, Oct 9",
    "",
    "## Who holds what",
    "- Ada approves refunds — Ada, Oct 2",
    "- Dev owns the order lookups — Dev, Oct 11",
    "",
    "## Never",
    "- Promise a delivery date the carrier has not given — Sam, Oct 9",
    "",
].join("\n");

const CHATS: AgentChat[] = [
    { id: "support-tone", title: "Tone for the apology emails", at: null },
    { id: "support-refund", title: "Refund for a broken order", at: null },
];

/** The forms a Lemma assigns the reader, in `waitingAssignedToMe`'s shape. */
function assignments(all: Raw[], podId: string): Raw[] {
    return all
        .filter((one) => one.pod_id === podId && one.status === "WAITING" && (one.active_wait as Raw | null)?.wait_type === "HUMAN")
        .map((one) => ({ wait: one.active_wait, run: one }));
}

export function workSample(): WorkSource {
    /* Writes, kept in memory: what changed since the app started. */
    const madeRuns: Raw[] = [];
    const runPatches = new Map<string, (run: Raw, now: number) => Raw>();
    const schedulePatches = new Map<string, Raw>();
    const madeSchedules: Raw[] = [];
    const goneSchedules = new Set<string>();
    const agentPatches = new Map<string, Raw>();
    const goneAgents = new Set<string>();
    const plans = new Map<string, string>([[SUPPORT.id, SUPPORT_PLAN]]);
    const settle = () => new Promise((resolve) => setTimeout(resolve, 300));

    /* Every run, with what has happened to it here. The fixtures are drawn
       afresh against the clock, so a run four minutes old stays so. */
    const allRuns = (): Raw[] => {
        const now = Date.now();
        return [...madeRuns, ...runs(now)].map((one) => {
            const patch = runPatches.get(String(one.id));
            return patch ? patch(one, now) : one;
        });
    };
    const flowOf = (podId: string, name: string) => flows().find((one) => one.podId === podId && one.summary.name === name) ?? null;
    const flowById = (id: string) => flows().find((one) => one.summary.id === id) ?? null;
    const allSchedules = (podId: string) => [...madeSchedules, ...schedules(Date.now())]
        .filter((one) => one.pod_id === podId && !goneSchedules.has(String(one.id)))
        .map((one) => ({ ...one, ...(schedulePatches.get(String(one.id)) ?? {}) }));
    const allAgents = (podId: string) => (agents(Date.now())[podId] ?? [])
        .filter((one) => !goneAgents.has(podId + ":" + String(one.name)))
        .map((one) => ({ ...one, ...(agentPatches.get(podId + ":" + String(one.name)) ?? {}) }));

    return {
        workflows: async (podId) => readWorkflows({ items: flows().filter((one) => one.podId === podId).map((one) => one.summary) }),
        workflow: async (podId, name) => flowOf(podId, name)?.graph ?? null,
        recentRuns: async (podId, limit) => readRuns({ items: newest(allRuns().filter((one) => one.pod_id === podId)).slice(0, limit) }),
        workflowRuns: async (podId, name, page) => {
            const flow = flowOf(podId, name);
            const mine = newest(allRuns().filter((one) => one.workflow_id === flow?.summary.id));
            const from = page ? Number(page) : 0;
            return { items: readRuns({ items: mine.slice(from, from + 20) }), next: from + 20 < mine.length ? String(from + 20) : null };
        },
        runsInFlight: async (podId, workflowId) => readRuns({ items: allRuns().filter((one) => one.pod_id === podId && one.workflow_id === workflowId) }).filter((one) => stillGoing(one.status)),
        waitingOnMe: async (podId) => readAssignments({ items: assignments(allRuns(), podId) }),
        run: async (_podId, runId) => readRunDetail(allRuns().find((one) => one.id === runId) ?? null),
        watchRun: null,
        startRun: async (podId, name) => {
            await settle();
            const flow = flowOf(podId, name);
            if (!flow) throw new Error("That workflow is gone.");
            const nodes = (flow.graph.nodes as Raw[]) ?? [];
            const first = nodes[0];
            const id = "run-new-" + Date.now().toString(36);
            const at = Date.now();
            const form = first?.type === "FORM";
            const made = run({
                id, workflow: String(flow.summary.id), podId, status: form ? "WAITING" : "RUNNING", ago: 0, current: String(first?.id ?? ""),
                steps: steps([{ node: String(first?.id ?? ""), status: form ? "WAITING" : "RUNNING", ago: 0, took: null }], at),
                wait: form ? wait("wait-" + id, id, String(flow.summary.id), podId, String(first.id), "HUMAN", 0, at, { input_schema: (first.config as Raw).input_schema, ui_schema: (first.config as Raw).ui_schema, assignee: MY_MEMBER }) : null,
            }, at);
            madeRuns.unshift(made);
            return readRun(made);
        },
        cancelRun: async (_podId, runId) => {
            await settle();
            runPatches.set(runId, (one, now) => ({ ...one, status: "CANCELLED", completed_at: new Date(now).toISOString(), active_wait: null, waiting_on: null, current_node_id: null }));
        },
        retryRun: async (_podId, runId) => {
            await settle();
            const flow = flowById(String(allRuns().find((one) => one.id === runId)?.workflow_id ?? ""));
            runPatches.set(runId, (one) => ({
                ...one,
                status: "RUNNING",
                error: null,
                failed_node_id: null,
                current_node_id: (one.failed_node_id as string) ?? null,
                completed_at: null,
                step_history: [...(one.step_history as Raw[]), { step_index: (one.step_history as Raw[]).length, node_id: one.failed_node_id, status: "RUNNING", started_at: new Date().toISOString(), completed_at: null, output_data: null }],
                workflow_id: flow?.summary.id ?? one.workflow_id,
            }));
        },
        submitForm: async (_podId, runId, nodeId, inputs) => {
            await settle();
            const held = allRuns().find((one) => one.id === runId);
            const waiting = held?.active_wait as Raw | null | undefined;
            if (!held || !waiting) throw new Error("This run is no longer waiting on a form — somebody got there first, or it moved on.");
            if (waiting.node_id !== nodeId) throw new Error("The run has moved to a different step, so this form can no longer be submitted.");
            runPatches.set(runId, (one, now) => {
                const history = (one.step_history as Raw[]).map((step) => (step.node_id === nodeId && step.status === "WAITING" ? { ...step, status: "COMPLETED", completed_at: new Date(now).toISOString(), output_data: inputs } : step));
                return { ...one, status: "COMPLETED", completed_at: new Date(now).toISOString(), active_wait: null, waiting_on: null, current_node_id: null, step_history: history };
            });
        },

        schedules: async (podId) => readSchedules({ items: allSchedules(podId) }),
        scheduleRuns: async (_podId, scheduleId, skipped) => readScheduleRuns({ items: scheduleRuns(scheduleId, skipped, Date.now()) }),
        setScheduleActive: async (podId, scheduleId, active) => {
            await settle();
            schedulePatches.set(scheduleId, { ...(schedulePatches.get(scheduleId) ?? {}), is_active: active, ...(active ? { paused_by_failures: false, consecutive_failures: 0 } : {}) });
            return readSchedule(allSchedules(podId).find((one) => one.id === scheduleId));
        },
        setScheduleVisibility: async (_podId, scheduleId, visibility) => {
            await settle();
            schedulePatches.set(scheduleId, { ...(schedulePatches.get(scheduleId) ?? {}), visibility });
        },
        retryScheduleRun: async (_podId, scheduleId, runId) => {
            await settle();
            return readScheduleRun({ id: runId + "-again", status: "RECEIVED", attempts: 1, redrive_of_run_id: runId, created_at: new Date().toISOString(), schedule_id: scheduleId });
        },
        createSchedule: async (podId, body) => {
            await settle();
            madeSchedules.unshift({
                id: "sched-made-" + Date.now().toString(36),
                name: body.name ?? (body.workflow_name ? body.workflow_name + "-for-me" : "my-copy"),
                ...body,
                is_active: true,
                user_id: ME,
                allowed_actions: MINE,
                created_at: new Date().toISOString(),
                pod_id: podId,
            });
        },
        deleteSchedule: async (_podId, scheduleId) => {
            await settle();
            goneSchedules.add(scheduleId);
        },
        serverEvents: async () => [],
        podConnectors: async () => readPodConnectors([
            { slug: "slack", connector_id: "slack", mode: "PER_MEMBER", status: "READY", purpose: "Hears the team's channels" },
        ]),

        agents: async (podId) => agentRows({ items: allAgents(podId) }),
        agent: async (podId, name) => {
            const raw = allAgents(podId).find((one) => one.name === name);
            if (!raw) throw new Error("That agent is gone.");
            return readAgentDetail(raw);
        },
        updateAgent: async (podId, name, before, after) => {
            await settle();
            const patch = agentChanges(before, after);
            const key = podId + ":" + name;
            const held = allAgents(podId).find((one) => one.name === name) ?? {};
            agentPatches.set(key, {
                ...(agentPatches.get(key) ?? {}),
                ...("description" in patch ? { description: patch.description ?? "" } : {}),
                ...(patch.instruction !== undefined ? { instruction: patch.instruction } : {}),
                ...(patch.agent_runtime ? { agent_runtime: { ...(held.agent_runtime as Raw), ...patch.agent_runtime } } : {}),
            });
            return readAgentDetail(allAgents(podId).find((one) => one.name === name));
        },
        deleteAgent: async (podId, name) => {
            await settle();
            goneAgents.add(podId + ":" + name);
        },
        agentChats: async (podId, agent) => (podId === SUPPORT.id && !agent.front && agent.name !== "reply_writer" ? [] : CHATS.map((one, index) => ({ ...one, at: new Date(Date.now() - (index + 1) * 5 * HOUR).toISOString() }))),
        talkTo: async () => {
            await settle();
            return REPLY_CHAT;
        },
        runtimes: async () => RUNTIMES,
        runtime: async (_orgId, id) => RUNTIMES.find((one) => one.id === id) ?? null,
        surfaces: async (podId) => SURFACES[podId] ?? [],
        members: async (podId) => (podId === SUPPORT.id ? MEMBERS : MEMBERS.slice(0, 1)),

        planText: async (podId) => plans.get(podId) ?? null,
        writePlan: async (podId, text) => {
            await settle();
            plans.set(podId, text);
        },
        query: async (podId) => (podId === SUPPORT.id ? [{ count: 31 }] : []),
        mcpUrl: (podId) => `https://api.lemma.work/mcp/${podId}`,
    };
}

function newest(list: Raw[]): Raw[] {
    return [...list].sort((a, b) => Date.parse(String(b.started_at ?? b.created_at)) - Date.parse(String(a.started_at ?? a.created_at)));
}
