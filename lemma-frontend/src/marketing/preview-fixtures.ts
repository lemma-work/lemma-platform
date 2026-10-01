/** The sample constants the landing's demo reads: every general fixture,
 *  with the sample teammates' own workflows in place of the generic ones:
 *  Kit's launch checks and June's customer imports. Workflow views import
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
        id: "wf-readiness",
        name: "readiness-check",
        description: "Every Thursday at 9: checks each launch asset, chases the late ones, and asks Priya to sign off.",
        pod_id: "kit",
        node_count: 5,
        node_types: ["AGENT", "DECISION", "AGENT", "FORM", "END"],
        is_active: true,
        updated_at: ago(2 * DAY),
        allowed_actions: ["read", "run", "update"],
    },
    {
        id: "wf-intake",
        name: "asset-intake",
        description: "When an asset is added, drafts its brief and asks the owner to confirm the date.",
        pod_id: "kit",
        node_count: 3,
        node_types: ["AGENT", "FORM", "END"],
        is_active: true,
        updated_at: ago(6 * DAY),
        allowed_actions: ["read", "run"],
    },
    {
        id: "wf-press",
        name: "press-kit",
        description: "Gathers the approved assets into one page and brings it to Priya before it goes out.",
        pod_id: "kit",
        node_count: 3,
        node_types: ["AGENT", "FORM", "END"],
        is_active: true,
        updated_at: ago(9 * DAY),
        allowed_actions: ["read", "run"],
    },
];

const WAITING_RUN = {
    id: "run-readiness-now",
    workflow_id: "wf-readiness",
    pod_id: "kit",
    user_id: "kit",
    status: "WAITING",
    start_type: "SCHEDULED",
    current_node_id: "sign-off",
    started_at: ago(3 * HOUR),
    created_at: ago(3 * HOUR),
};

const SIGN_OFF_SCHEMA = {
    type: "object",
    required: ["go"],
    properties: {
        go: { type: "boolean", title: "Ship on the planned date" },
        note: { type: "string", title: "Note for Kit" },
    },
};

const SIGN_OFF_WAIT = {
    id: "wait-sign-off", run_id: WAITING_RUN.id, workflow_id: "wf-readiness", pod_id: "kit", node_id: "sign-off",
    wait_type: "HUMAN", status: "ACTIVE", assigned_pod_member_id: "priya", created_at: ago(2 * HOUR),
    payload: { input_schema: SIGN_OFF_SCHEMA, ui_schema: { "ui:order": ["go", "note"] } },
};

const KIT_RUNS: Record<string, unknown[]> = {
    "readiness-check": [
        WAITING_RUN,
        { id: "run-readiness-last", workflow_id: "wf-readiness", pod_id: "kit", user_id: "kit", status: "COMPLETED", start_type: "SCHEDULED",
            started_at: ago(7 * DAY + 3 * HOUR), completed_at: ago(7 * DAY + HOUR), created_at: ago(7 * DAY + 3 * HOUR) },
        { id: "run-readiness-before", workflow_id: "wf-readiness", pod_id: "kit", user_id: "kit", status: "COMPLETED", start_type: "SCHEDULED",
            started_at: ago(14 * DAY + 3 * HOUR), completed_at: ago(14 * DAY + 2 * HOUR), created_at: ago(14 * DAY + 3 * HOUR) },
    ],
    "asset-intake": [
        { id: "run-intake", workflow_id: "wf-intake", pod_id: "kit", user_id: "kit", status: "COMPLETED", start_type: "DATASTORE_EVENT",
            started_at: ago(26 * HOUR), completed_at: ago(25 * HOUR), created_at: ago(26 * HOUR) },
    ],
    "press-kit": [],
};

const KIT_RUN_DETAIL: Record<string, unknown> = {
    "run-readiness-now": {
        ...WAITING_RUN,
        active_wait: SIGN_OFF_WAIT,
        execution_context: { start: { trigger: "SCHEDULE" } },
        step_history: [
            { step_index: 0, node_id: "check-assets", status: "COMPLETED", started_at: ago(3 * HOUR), completed_at: ago(3 * HOUR - 4 * 60_000),
                output_data: { summary: "12 assets: 3 ready, 3 in review, 4 drafting, 2 waiting on a customer.", late: 2 } },
            { step_index: 1, node_id: "any-late", status: "COMPLETED", started_at: ago(3 * HOUR - 4 * 60_000), completed_at: ago(3 * HOUR - 4 * 60_000 + 30),
                output_data: { matched_condition: "check-assets.late > `0`" } },
            { step_index: 2, node_id: "chase", status: "COMPLETED", started_at: ago(3 * HOUR - 4 * 60_000), completed_at: ago(2 * HOUR),
                output_data: { summary: "Asked Rohan about the demo video and Northfield about their quote." } },
            { step_index: 3, node_id: "sign-off", status: "WAITING", started_at: ago(2 * HOUR) },
        ],
    },
};

const KIT_SHAPES: Record<string, unknown> = {
    "readiness-check": {
        id: "wf-readiness",
        name: "readiness-check",
        description: KIT_WORKFLOWS[0].description,
        pod_id: "kit",
        is_active: true,
        allowed_actions: ["read", "run", "update"],
        start: { type: "SCHEDULED", config: { schedule_type: "CRON" } },
        nodes: [
            { id: "check-assets", type: "AGENT", label: "Check every asset", position: { x: 40, y: 40 }, config: { agent_name: "Kit" } },
            { id: "any-late", type: "DECISION", label: "Anything late?", position: { x: 320, y: 40 },
                config: { rules: [{ condition: "check-assets.late > `0`", next_node_id: "chase" }, { condition: "check-assets.late == `0`", next_node_id: "sign-off" }] } },
            { id: "chase", type: "AGENT", label: "Chase the owners", position: { x: 320, y: 200 }, config: { agent_name: "Kit" } },
            { id: "sign-off", type: "FORM", label: "Priya signs off", position: { x: 620, y: 40 },
                config: { input_schema: SIGN_OFF_SCHEMA, assignee_pod_member_id: "priya" } },
            { id: "done", type: "END", label: null, position: { x: 900, y: 40 }, config: {} },
        ],
        edges: [
            { id: "e1", source: "check-assets", target: "any-late", label: null },
            { id: "e2", source: "chase", target: "sign-off", label: null },
            { id: "e3", source: "sign-off", target: "done", label: null },
        ],
    },
    "asset-intake": {
        id: "wf-intake",
        name: "asset-intake",
        description: KIT_WORKFLOWS[1].description,
        pod_id: "kit",
        is_active: true,
        allowed_actions: ["read", "run"],
        start: { type: "DATASTORE_EVENT", config: { table_name: "launch_assets", operations: ["INSERT"] } },
        nodes: [
            { id: "draft-brief", type: "AGENT", label: "Draft the brief", position: { x: 40, y: 40 }, config: { agent_name: "Kit" } },
            { id: "confirm-date", type: "FORM", label: "Owner confirms the date", position: { x: 320, y: 40 },
                config: { input_schema: { type: "object", properties: { due: { type: "string", format: "date", title: "Due" } } } } },
            { id: "done", type: "END", label: null, position: { x: 620, y: 40 }, config: {} },
        ],
        edges: [
            { id: "e1", source: "draft-brief", target: "confirm-date", label: null },
            { id: "e2", source: "confirm-date", target: "done", label: null },
        ],
    },
    "press-kit": {
        id: "wf-press",
        name: "press-kit",
        description: KIT_WORKFLOWS[2].description,
        pod_id: "kit",
        is_active: true,
        allowed_actions: ["read", "run"],
        start: { type: "MANUAL", config: null },
        nodes: [
            { id: "gather", type: "AGENT", label: "Gather the approved assets", position: { x: 40, y: 40 }, config: { agent_name: "Kit" } },
            { id: "approve", type: "FORM", label: "Priya approves", position: { x: 320, y: 40 },
                config: { input_schema: { type: "object", properties: { approved: { type: "boolean", title: "Approved" } } }, assignee_pod_member_id: "priya" } },
            { id: "done", type: "END", label: null, position: { x: 620, y: 40 }, config: {} },
        ],
        edges: [
            { id: "e1", source: "gather", target: "approve", label: null },
            { id: "e2", source: "approve", target: "done", label: null },
        ],
    },
};

/** Waiting on a person: the sign-off above, which is Priya's. */
const KIT_WAITING = [
    {
        wait: SIGN_OFF_WAIT,
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
