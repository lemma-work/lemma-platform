/** The sample constants the landing's demo reads: every general fixture,
 *  with the sample teammates' own workflows in place of the generic ones:
 *  Kit's feedback loop and June's customer imports. Workflow views import
 *  their sample straight from the fixtures rather than through `PodSource`,
 *  so `samples()` in `@/data/samples` picks this module on the demo, and
 *  `forPod` narrows it to the space being looked at.
 *
 *  Shapes are the wire shapes the general fixtures document
 *  (src/data/fixtures.ts, "workflows"). */
export * from "@/data/fixtures";

const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const ago = (ms: number): string => new Date(Date.now() - ms).toISOString();

const KIT_WORKFLOWS = [
    {
        id: "wf-capture",
        name: "capture-feedback",
        description: "Every new message in #feedback, email to feedback@ and report from the app: files it under a theme, or starts a new one.",
        pod_id: "kit",
        node_count: 2,
        node_types: ["AGENT", "END"],
        is_active: true,
        updated_at: ago(DAY + 17 * HOUR),
        allowed_actions: ["read", "run", "update"],
    },
    {
        id: "wf-close-loop",
        name: "close-the-loop",
        description: "When a PR that fixes a theme merges: drafts a reply for everyone who reported it, gives Enterprise ones to Sam, and waits for the engineer who shipped it to confirm it’s live.",
        pod_id: "kit",
        node_count: 5,
        node_types: ["AGENT", "AGENT", "FORM", "AGENT", "END"],
        is_active: true,
        updated_at: ago(DAY + 17 * HOUR),
        allowed_actions: ["read", "run", "update"],
    },
    {
        id: "wf-reconcile",
        name: "nightly-reconcile",
        description: "Every night at 02:00: re-reads the day’s reports, starts new themes, suggests merges, and asks again anyone whose fix is live.",
        pod_id: "kit",
        node_count: 3,
        node_types: ["AGENT", "AGENT", "END"],
        is_active: true,
        updated_at: ago(DAY + 17 * HOUR),
        allowed_actions: ["read", "run"],
    },
];

const WAITING_RUN = {
    id: "run-close-482",
    workflow_id: "wf-close-loop",
    pod_id: "kit",
    user_id: "kit",
    status: "WAITING",
    start_type: "EVENT",
    current_node_id: "confirm-live",
    started_at: ago(11 * HOUR),
    created_at: ago(11 * HOUR),
};

const CONFIRM_SCHEMA = {
    type: "object",
    required: ["live"],
    properties: {
        live: { type: "boolean", title: "PR #482 is live for everyone" },
        note: { type: "string", title: "Note for Kit" },
    },
};

const CONFIRM_WAIT = {
    id: "wait-confirm-live", run_id: WAITING_RUN.id, workflow_id: "wf-close-loop", pod_id: "kit", node_id: "confirm-live",
    wait_type: "HUMAN", status: "ACTIVE", assigned_pod_member_id: "dev", created_at: ago(11 * HOUR - 60_000),
    payload: { input_schema: CONFIRM_SCHEMA, ui_schema: { "ui:order": ["live", "note"] } },
};

const captured = (minutes: number, i: number) => ({ id: "run-capture-" + i, workflow_id: "wf-capture", pod_id: "kit", user_id: "kit", status: "COMPLETED",
    start_type: "EVENT", started_at: ago(minutes * 60_000), completed_at: ago(minutes * 60_000 - 20_000), created_at: ago(minutes * 60_000) });

const KIT_RUNS: Record<string, unknown[]> = {
    "capture-feedback": [2, 47, 95, 260, 610, 640, 700].map(captured),
    "close-the-loop": [
        WAITING_RUN,
        { id: "run-close-478", workflow_id: "wf-close-loop", pod_id: "kit", user_id: "kit", status: "COMPLETED", start_type: "EVENT",
            started_at: ago(3 * DAY), completed_at: ago(2 * DAY + 20 * HOUR), created_at: ago(3 * DAY) },
        { id: "run-close-474", workflow_id: "wf-close-loop", pod_id: "kit", user_id: "kit", status: "COMPLETED", start_type: "EVENT",
            started_at: ago(6 * DAY), completed_at: ago(5 * DAY + 22 * HOUR), created_at: ago(6 * DAY) },
    ],
    "nightly-reconcile": [0, 1, 2].map((day) => ({ id: "run-reconcile-" + day, workflow_id: "wf-reconcile", pod_id: "kit", user_id: "kit", status: "COMPLETED",
        start_type: "SCHEDULED", started_at: ago(day * DAY + 8.4 * HOUR), completed_at: ago(day * DAY + 8.4 * HOUR - 3 * 60_000), created_at: ago(day * DAY + 8.4 * HOUR) })),
};

const KIT_RUN_DETAIL: Record<string, unknown> = {
    "run-close-482": {
        ...WAITING_RUN,
        active_wait: CONFIRM_WAIT,
        execution_context: { start: { trigger: "EVENT" } },
        step_history: [
            { step_index: 0, node_id: "match-theme", status: "COMPLETED", started_at: ago(11 * HOUR), completed_at: ago(11 * HOUR - 30_000),
                output_data: { summary: "PR #482 by Dev fixes LIN-231, Dates import as text (22 reports)." } },
            { step_index: 1, node_id: "draft-replies", status: "COMPLETED", started_at: ago(11 * HOUR - 30_000), completed_at: ago(11 * HOUR - 60_000),
                output_data: { summary: "22 retry replies drafted, one in each thread. 3 Enterprise accounts go to Sam.", drafted: 22, via_sam: 3 } },
            { step_index: 2, node_id: "confirm-live", status: "WAITING", started_at: ago(11 * HOUR - 60_000) },
        ],
    },
};

const KIT_SHAPES: Record<string, unknown> = {
    "capture-feedback": {
        id: "wf-capture",
        name: "capture-feedback",
        description: KIT_WORKFLOWS[0].description,
        pod_id: "kit",
        is_active: true,
        allowed_actions: ["read", "run", "update"],
        start: { type: "EVENT", config: { connector_id: "slack", connector_trigger_id: "message posted in #feedback" } },
        nodes: [
            { id: "file-it", type: "AGENT", label: "File it under a theme", position: { x: 40, y: 40 }, config: { agent_name: "Kit" } },
            { id: "done", type: "END", label: null, position: { x: 320, y: 40 }, config: {} },
        ],
        edges: [{ id: "e1", source: "file-it", target: "done", label: null }],
    },
    "close-the-loop": {
        id: "wf-close-loop",
        name: "close-the-loop",
        description: KIT_WORKFLOWS[1].description,
        pod_id: "kit",
        is_active: true,
        allowed_actions: ["read", "run", "update"],
        start: { type: "EVENT", config: { connector_id: "github", connector_trigger_id: "pull request merged" } },
        nodes: [
            { id: "match-theme", type: "AGENT", label: "Match the PR to a theme", position: { x: 40, y: 40 }, config: { agent_name: "Kit" } },
            { id: "draft-replies", type: "AGENT", label: "Draft a reply for each reporter", position: { x: 320, y: 40 }, config: { agent_name: "Kit" } },
            { id: "confirm-live", type: "FORM", label: "The PR’s author confirms it’s live", position: { x: 600, y: 40 },
                config: { input_schema: CONFIRM_SCHEMA, assignee_pod_member_id: "dev" } },
            { id: "send-replies", type: "AGENT", label: "Post the replies", position: { x: 880, y: 40 }, config: { agent_name: "Kit" } },
            { id: "done", type: "END", label: null, position: { x: 1160, y: 40 }, config: {} },
        ],
        edges: [
            { id: "e1", source: "match-theme", target: "draft-replies", label: null },
            { id: "e2", source: "draft-replies", target: "confirm-live", label: null },
            { id: "e3", source: "confirm-live", target: "send-replies", label: null },
            { id: "e4", source: "send-replies", target: "done", label: null },
        ],
    },
    "nightly-reconcile": {
        id: "wf-reconcile",
        name: "nightly-reconcile",
        description: KIT_WORKFLOWS[2].description,
        pod_id: "kit",
        is_active: true,
        allowed_actions: ["read", "run"],
        start: { type: "SCHEDULED", config: { schedule_type: "CRON" } },
        nodes: [
            { id: "reconcile", type: "AGENT", label: "Re-read the day’s reports", position: { x: 40, y: 40 }, config: { agent_name: "Kit" } },
            { id: "ask-again", type: "AGENT", label: "Ask again where a fix is live", position: { x: 320, y: 40 }, config: { agent_name: "Kit" } },
            { id: "done", type: "END", label: null, position: { x: 600, y: 40 }, config: {} },
        ],
        edges: [
            { id: "e1", source: "reconcile", target: "ask-again", label: null },
            { id: "e2", source: "ask-again", target: "done", label: null },
        ],
    },
};

/** Waiting on a person: Dev, to confirm the fix Dev shipped is live before
 *  anyone is told it is. */
const KIT_WAITING = [
    {
        wait: CONFIRM_WAIT,
        run: WAITING_RUN,
    },
];

/* ── June: customer imports ─────────────────────────────────────────── */

const JUNE_WORKFLOWS = [
    {
        id: "wf-first-import",
        name: "first-import-check",
        description: "When a customer sends their first file: checks every row, prepares a fix for anything that won’t import, and asks Dev before it runs.",
        pod_id: "june",
        node_count: 6,
        node_types: ["AGENT", "DECISION", "AGENT", "FORM", "FUNCTION", "END"],
        is_active: true,
        updated_at: ago(4 * DAY),
        allowed_actions: ["read", "run", "update"],
    },
    {
        id: "wf-onboarding-digest",
        name: "onboarding-digest",
        description: "Every Monday at 9: where each new customer stands, and who is waiting on whom.",
        pod_id: "june",
        node_count: 2,
        node_types: ["AGENT", "END"],
        is_active: true,
        updated_at: ago(11 * DAY),
        allowed_actions: ["read", "run"],
    },
    {
        id: "wf-training-reminder",
        name: "training-reminder",
        description: "Two days before training, checks the import worked, then reminds the customer. Holds the reminder if it didn’t.",
        pod_id: "june",
        node_count: 4,
        node_types: ["WAIT_UNTIL", "AGENT", "DECISION", "END"],
        is_active: true,
        updated_at: ago(15 * DAY),
        allowed_actions: ["read", "run"],
    },
];

const HARBOR_RUN = {
    id: "run-harbor-import",
    workflow_id: "wf-first-import",
    pod_id: "june",
    user_id: "june",
    status: "WAITING",
    start_type: "DATASTORE_EVENT",
    current_node_id: "dev-approves",
    started_at: ago(5 * HOUR),
    created_at: ago(5 * HOUR),
};

const FIX_SCHEMA = {
    type: "object",
    required: ["run_it"],
    properties: {
        run_it: { type: "boolean", title: "Import the corrected file" },
        note: { type: "string", title: "Note for June" },
    },
};

const DEV_WAIT = {
    id: "wait-dev-approves", run_id: HARBOR_RUN.id, workflow_id: "wf-first-import", pod_id: "june", node_id: "dev-approves",
    wait_type: "HUMAN", status: "ACTIVE", assigned_pod_member_id: "dev", created_at: ago(4 * HOUR),
    payload: { input_schema: FIX_SCHEMA, ui_schema: { "ui:order": ["run_it", "note"] } },
};

const JUNE_RUNS: Record<string, unknown[]> = {
    "first-import-check": [
        HARBOR_RUN,
        { id: "run-moss-import", workflow_id: "wf-first-import", pod_id: "june", user_id: "june", status: "COMPLETED", start_type: "DATASTORE_EVENT",
            started_at: ago(2 * DAY + 3 * HOUR), completed_at: ago(2 * DAY + HOUR), created_at: ago(2 * DAY + 3 * HOUR) },
        { id: "run-fern-import", workflow_id: "wf-first-import", pod_id: "june", user_id: "june", status: "COMPLETED", start_type: "DATASTORE_EVENT",
            started_at: ago(3 * DAY + 2 * HOUR), completed_at: ago(3 * DAY + HOUR), created_at: ago(3 * DAY + 2 * HOUR) },
    ],
    "onboarding-digest": [
        { id: "run-digest", workflow_id: "wf-onboarding-digest", pod_id: "june", user_id: "june", status: "COMPLETED", start_type: "SCHEDULED",
            started_at: ago(3 * DAY), completed_at: ago(3 * DAY - 6 * 60_000), created_at: ago(3 * DAY) },
    ],
    "training-reminder": [],
};

const JUNE_RUN_DETAIL: Record<string, unknown> = {
    "run-harbor-import": {
        ...HARBOR_RUN,
        active_wait: DEV_WAIT,
        execution_context: { start: { trigger: "DATASTORE_EVENT" } },
        step_history: [
            { step_index: 0, node_id: "check-rows", status: "COMPLETED", started_at: ago(5 * HOUR), completed_at: ago(5 * HOUR - 3 * 60_000),
                output_data: { summary: "1,840 rows. 212 have dates in two formats (14/09/2026 and 2026-09-14); everything else imports cleanly.", blocking: 212 } },
            { step_index: 1, node_id: "anything-blocking", status: "COMPLETED", started_at: ago(5 * HOUR - 3 * 60_000), completed_at: ago(5 * HOUR - 3 * 60_000 + 40),
                output_data: { matched_condition: "check-rows.blocking > `0`" } },
            { step_index: 2, node_id: "prepare-fix", status: "COMPLETED", started_at: ago(5 * HOUR - 3 * 60_000), completed_at: ago(4 * HOUR),
                output_data: { summary: "Normalised the 212 dates to ISO and kept the original file beside the fix." } },
            { step_index: 3, node_id: "dev-approves", status: "WAITING", started_at: ago(4 * HOUR) },
        ],
    },
};

const JUNE_SHAPES: Record<string, unknown> = {
    "first-import-check": {
        id: "wf-first-import",
        name: "first-import-check",
        description: JUNE_WORKFLOWS[0].description,
        pod_id: "june",
        is_active: true,
        allowed_actions: ["read", "run", "update"],
        start: { type: "DATASTORE_EVENT", config: { table_name: "imports", operations: ["INSERT"] } },
        nodes: [
            { id: "check-rows", type: "AGENT", label: "Check every row", position: { x: 40, y: 40 }, config: { agent_name: "June" } },
            { id: "anything-blocking", type: "DECISION", label: "Anything that won’t import?", position: { x: 320, y: 40 },
                config: { rules: [{ condition: "check-rows.blocking > `0`", next_node_id: "prepare-fix" }, { condition: "check-rows.blocking == `0`", next_node_id: "run-import" }] } },
            { id: "prepare-fix", type: "AGENT", label: "Prepare a corrected file", position: { x: 320, y: 200 }, config: { agent_name: "June" } },
            { id: "dev-approves", type: "FORM", label: "Dev approves the fix", position: { x: 620, y: 200 }, config: { input_schema: FIX_SCHEMA, assignee_pod_member_id: "dev" } },
            { id: "run-import", type: "FUNCTION", label: "Run the import", position: { x: 620, y: 40 }, config: { function_name: "run-import" } },
            { id: "done", type: "END", label: null, position: { x: 900, y: 40 }, config: {} },
        ],
        edges: [
            { id: "e1", source: "check-rows", target: "anything-blocking", label: null },
            { id: "e2", source: "prepare-fix", target: "dev-approves", label: null },
            { id: "e3", source: "dev-approves", target: "run-import", label: null },
            { id: "e4", source: "run-import", target: "done", label: null },
        ],
    },
    "onboarding-digest": {
        id: "wf-onboarding-digest", name: "onboarding-digest", description: JUNE_WORKFLOWS[1].description, pod_id: "june", is_active: true,
        allowed_actions: ["read", "run"], start: { type: "SCHEDULED", config: { schedule_type: "CRON" } },
        nodes: [
            { id: "write-digest", type: "AGENT", label: "Write the Monday digest", position: { x: 40, y: 40 }, config: { agent_name: "June" } },
            { id: "done", type: "END", label: null, position: { x: 320, y: 40 }, config: {} },
        ],
        edges: [{ id: "e1", source: "write-digest", target: "done", label: null }],
    },
    "training-reminder": {
        id: "wf-training-reminder", name: "training-reminder", description: JUNE_WORKFLOWS[2].description, pod_id: "june", is_active: true,
        allowed_actions: ["read", "run"], start: { type: "MANUAL", config: null },
        nodes: [
            { id: "until-two-days-before", type: "WAIT_UNTIL", label: "Until two days before training", position: { x: 40, y: 40 }, config: { timeout_seconds: 5 * 86400 } },
            { id: "check-import", type: "AGENT", label: "Check the import worked", position: { x: 320, y: 40 }, config: { agent_name: "June" } },
            { id: "worked", type: "DECISION", label: "Did it?", position: { x: 620, y: 40 }, config: { rules: [{ condition: "check-import.ok", next_node_id: "done" }] } },
            { id: "done", type: "END", label: null, position: { x: 900, y: 40 }, config: {} },
        ],
        edges: [
            { id: "e1", source: "until-two-days-before", target: "check-import", label: null },
            { id: "e2", source: "check-import", target: "worked", label: null },
        ],
    },
};

const JUNE_WAITING = [{ wait: DEV_WAIT, run: HARBOR_RUN }];

/* ── everything, and one space at a time ────────────────────────────── */

export const SAMPLE_WORKFLOWS = [...KIT_WORKFLOWS, ...JUNE_WORKFLOWS];
export const SAMPLE_WORKFLOW_RUNS: Record<string, unknown[]> = { ...KIT_RUNS, ...JUNE_RUNS };
export const SAMPLE_RUN_DETAIL: Record<string, unknown> = { ...KIT_RUN_DETAIL, ...JUNE_RUN_DETAIL };
export const SAMPLE_WORKFLOW_SHAPES: Record<string, unknown> = { ...KIT_SHAPES, ...JUNE_SHAPES };
export const SAMPLE_WAITING = [...KIT_WAITING, ...JUNE_WAITING];

/** The sample as one space sees it: its own workflows and their runs, so
 *  June's imports do not turn up in Kit's space. Lookups by name or run id
 *  need no narrowing; a space only ever asks for its own. */
export function forPod(podId: string | undefined) {
    if (!podId) return { SAMPLE_WORKFLOWS, SAMPLE_WORKFLOW_RUNS, SAMPLE_WAITING };
    const flows = SAMPLE_WORKFLOWS.filter((flow) => flow.pod_id === podId);
    const names = new Set(flows.map((flow) => flow.name));
    return {
        SAMPLE_WORKFLOWS: flows,
        SAMPLE_WORKFLOW_RUNS: Object.fromEntries(Object.entries(SAMPLE_WORKFLOW_RUNS).filter(([name]) => names.has(name))),
        SAMPLE_WAITING: SAMPLE_WAITING.filter((one) => one.wait.pod_id === podId),
    };
}
