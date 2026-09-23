/** Reading a workflow, a run, and the thing a run is stuck on.
 *
 *  Every reader here takes `unknown` and hands back something drawable. That
 *  is not defensive habit: a run is the most heterogeneous payload this app
 *  touches. `execution_context` is a free-form dict, `step_history[].output_data`
 *  is `Any`, and `active_wait.payload` is whatever the executor put there
 *  (`app/modules/workflow/api/schemas.py:523`, `domain/wait.py:53`). A view
 *  that destructures those and throws takes the whole panel with it, so a row
 *  that cannot be read says so and the rest of the list still draws.
 */

/* ── the statuses, as the backend spells them ──────────────────────── */

/** `domain/run.py:39`. PENDING exists only in memory before the first advance,
 *  so it is here for completeness rather than because it is ever seen. */
export const RUN_STATUSES = [
    "PENDING", "RUNNING", "WAITING", "COMPLETED", "FAILED", "CANCELLED",
] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];

/** `domain/wait.py:12`. */
export const WAIT_TYPES = ["HUMAN", "AGENT", "FUNCTION", "TIME"] as const;
export type WaitType = (typeof WAIT_TYPES)[number];

/** What a row is for, at a glance. Four tones rather than six statuses,
 *  because CANCELLED and COMPLETED want the same quiet and PENDING and
 *  RUNNING want the same motion. */
export type RunTone = "going" | "waiting" | "good" | "bad" | "gone";

export function runTone(status: string | null | undefined): RunTone {
    switch (status) {
        case "PENDING":
        case "RUNNING": return "going";
        case "WAITING": return "waiting";
        case "COMPLETED": return "good";
        case "FAILED": return "bad";
        case "CANCELLED": return "gone";
        default: return "gone";
    }
}

/** The status in a person's words.
 *
 *  RUNNING is not always running: a run suspended on an agent, a function job
 *  or a timer stays RUNNING while the wait row records what it is actually on
 *  (`domain/run.py:41`). So the word depends on whether there is a wait, and
 *  "Running" on a run that has been parked on an agent for an hour is a lie
 *  the status field tells by itself.
 */
export function sayStatus(status: string | null | undefined, waiting?: WaitType | null): string {
    if (status === "RUNNING" && waiting && waiting !== "HUMAN") return sayWaitingOn(waiting);
    switch (status) {
        case "PENDING": return "Starting";
        case "RUNNING": return "Running";
        case "WAITING": return "Waiting on a person";
        case "COMPLETED": return "Completed";
        case "FAILED": return "Failed";
        case "CANCELLED": return "Cancelled";
        default: return "Unknown";
    }
}

export function sayWaitingOn(type: WaitType): string {
    switch (type) {
        case "HUMAN": return "Waiting on a person";
        case "AGENT": return "Waiting on an agent";
        case "FUNCTION": return "Waiting on a function";
        case "TIME": return "Waiting on a timer";
    }
}

/** Whether there is anything left to cancel. Mirrors `TERMINAL_STATUSES`
 *  (`domain/run.py:57`) — cancelling a finished run is a 409, not a no-op. */
export function stillGoing(status: string | null | undefined): boolean {
    return status === "PENDING" || status === "RUNNING" || status === "WAITING";
}

/* ── guarded readers ───────────────────────────────────────────────── */

/* Exported for `shape.ts`, which reads the same responses and has to make the
   same two judgements about every field it touches. Two copies of a one-line
   guard is two places for "" and `[]` to start meaning different things. */
export function isRecord(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function str(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value : null;
}

export interface WorkflowRow {
    id: string;
    name: string;
    description: string | null;
    /** `node_count` off the list response — the graph is not in it
     *  (`api/schemas.py:444`), so this is the only step count a list has. */
    steps: number;
    /** FORM / AGENT / FUNCTION / DECISION / LOOP / WAIT_UNTIL / END. */
    kinds: string[];
    active: boolean;
    updated: string | null;
    /** What the server says this caller may do to it. `cancel` on a run is
     *  gated separately, per run. */
    may: string[];
}

/** One workflow out of `workflows.list()`.
 *
 *  Keyed on `name`, not `id`, everywhere it is used: every workflow endpoint
 *  in the SDK takes a workflow *name* (`namespaces/workflows.d.ts`), so a
 *  nameless row cannot be opened and is not a row.
 */
export function readWorkflow(raw: unknown): WorkflowRow | null {
    if (!isRecord(raw)) return null;
    const name = str(raw.name);
    if (!name) return null;
    return {
        id: str(raw.id) ?? name,
        name,
        description: str(raw.description),
        steps: typeof raw.node_count === "number" && raw.node_count >= 0 ? raw.node_count : 0,
        kinds: Array.isArray(raw.node_types) ? raw.node_types.filter((one): one is string => typeof one === "string") : [],
        active: raw.is_active !== false,
        updated: str(raw.updated_at) ?? str(raw.created_at),
        may: Array.isArray(raw.allowed_actions) ? raw.allowed_actions.filter((one): one is string => typeof one === "string") : [],
    };
}

export function readWorkflows(payload: unknown): WorkflowRow[] {
    const items = isRecord(payload) && Array.isArray(payload.items) ? payload.items : [];
    return items.map(readWorkflow).filter((one): one is WorkflowRow => one !== null);
}

export interface RunRow {
    id: string;
    workflowId: string | null;
    podId: string | null;
    status: string;
    /** Only ever set on a FAILED run (`api/schemas.py:490`), so a named node
     *  here is a name worth printing beside the failure. */
    failedNodeId: string | null;
    currentNodeId: string | null;
    error: string | null;
    startedAt: string | null;
    completedAt: string | null;
    createdAt: string | null;
    /** MANUAL, SCHEDULE, EVENT, DATA_STORE — why this run exists. */
    startType: string | null;
}

/** One run summary. A run without an id is unopenable and uncancellable, so
 *  it is dropped rather than drawn as a row that does nothing. */
export function readRun(raw: unknown): RunRow | null {
    if (!isRecord(raw)) return null;
    const id = str(raw.id);
    if (!id) return null;
    return {
        id,
        workflowId: str(raw.workflow_id),
        podId: str(raw.pod_id),
        /* Unknown rather than a guess: a status this app has not heard of is a
           backend that moved, and inventing "Completed" for it would be the
           worst possible wrong answer. */
        status: str(raw.status) ?? "UNKNOWN",
        failedNodeId: str(raw.failed_node_id),
        currentNodeId: str(raw.current_node_id),
        error: str(raw.error),
        startedAt: str(raw.started_at),
        completedAt: str(raw.completed_at),
        createdAt: str(raw.created_at),
        startType: str(raw.start_type),
    };
}

export function readRuns(payload: unknown): RunRow[] {
    const items = isRecord(payload) && Array.isArray(payload.items) ? payload.items : [];
    return items.map(readRun).filter((one): one is RunRow => one !== null);
}

export interface WaitRow {
    id: string;
    runId: string | null;
    workflowId: string | null;
    podId: string | null;
    nodeId: string;
    type: WaitType;
    /** The resolved JSON Schema the form node put on the wait
     *  (`execution/executors/form.py:49`). Null for every non-form wait, and
     *  for a form wait whose payload arrived without one. */
    schema: Record<string, unknown> | null;
    /** `ui:order` and `ui:widget` live here — the same executor writes it
     *  beside the schema. */
    uiSchema: Record<string, unknown> | null;
    createdAt: string | null;
}

export function waitTypeOf(raw: unknown): WaitType | null {
    if (!isRecord(raw)) return null;
    const type = raw.wait_type;
    return typeof type === "string" && (WAIT_TYPES as readonly string[]).includes(type) ? (type as WaitType) : null;
}

/** One wait. `node_id` is required rather than optional: it is what a
 *  submission is checked against (`api/schemas.py:536`), and a wait without
 *  one can never be answered from here. */
export function readWait(raw: unknown): WaitRow | null {
    if (!isRecord(raw)) return null;
    const id = str(raw.id);
    const nodeId = str(raw.node_id);
    const type = waitTypeOf(raw);
    if (!id || !nodeId || !type) return null;
    const payload = isRecord(raw.payload) ? raw.payload : {};
    return {
        id,
        runId: str(raw.run_id),
        workflowId: str(raw.workflow_id),
        podId: str(raw.pod_id),
        nodeId,
        type,
        schema: isRecord(payload.input_schema) ? payload.input_schema : null,
        uiSchema: isRecord(payload.ui_schema) ? payload.ui_schema : null,
        createdAt: str(raw.created_at),
    };
}

/** One entry in the approval queue: a wait and the run that owns it.
 *
 *  `run` is a *summary* — `WorkflowRunWaitAssignment` pairs the wait with
 *  `WorkflowRunSummaryResponse` (`api/schemas.py:558`), which carries
 *  `workflow_id` and no workflow name. So a row that wants to say which
 *  workflow this is has to resolve the name itself.
 */
export interface Assignment {
    wait: WaitRow;
    run: RunRow;
}

export function readAssignments(payload: unknown): Assignment[] {
    const items = isRecord(payload) && Array.isArray(payload.items) ? payload.items : [];
    const out: Assignment[] = [];
    for (const item of items) {
        if (!isRecord(item)) continue;
        const wait = readWait(item.wait);
        const run = readRun(item.run);
        if (wait && run) out.push({ wait, run });
    }
    return out;
}

export interface StepRow {
    index: number;
    nodeId: string;
    status: string;
    startedAt: string | null;
    completedAt: string | null;
    error: string | null;
    /** `Any` on the wire. Kept as-is and printed as JSON where it is not a
     *  scalar — a step's output is the only record of what a node produced,
     *  and dropping the ones that are objects drops most of them. */
    output: unknown;
}

export function readStep(raw: unknown, at: number): StepRow {
    if (!isRecord(raw)) {
        /* Deliberately still a row. A malformed step means the step *ran*;
           hiding it renumbers everything after it and quietly shortens the
           history somebody is reading to work out what happened. */
        return { index: at, nodeId: "", status: "UNKNOWN", startedAt: null, completedAt: null, error: null, output: undefined };
    }
    return {
        index: typeof raw.step_index === "number" ? raw.step_index : at,
        nodeId: str(raw.node_id) ?? "",
        status: str(raw.status) ?? "UNKNOWN",
        startedAt: str(raw.started_at),
        completedAt: str(raw.completed_at),
        error: str(raw.error),
        output: raw.output_data,
    };
}

export function readSteps(raw: unknown): StepRow[] {
    const list = isRecord(raw) && Array.isArray(raw.step_history) ? raw.step_history : [];
    return list.map(readStep).sort((left, right) => left.index - right.index);
}

/** The whole run, opened. */
export interface RunDetail extends RunRow {
    steps: StepRow[];
    wait: WaitRow | null;
    /** The flat view workflow expressions resolve against. Shown as JSON, not
     *  parsed: its keys are node ids the author chose. */
    context: Record<string, unknown>;
}

export function readRunDetail(raw: unknown): RunDetail | null {
    const base = readRun(raw);
    if (!base) return null;
    const record = raw as Record<string, unknown>;
    return {
        ...base,
        steps: readSteps(record),
        wait: readWait(record.active_wait),
        context: isRecord(record.execution_context) ? record.execution_context : {},
    };
}

/* ── time, said out loud ───────────────────────────────────────────── */

/** How long a run took, in ms — or how long it has been going.
 *
 *  Falls back to `created_at` when `started_at` is absent: a run that failed
 *  before its first advance has a created time and nothing else, and
 *  "unknown" for those is worse than the truth, which is that it lasted
 *  roughly no time.
 */
export function runMillis(run: { startedAt: string | null; createdAt: string | null; completedAt: string | null }, now = Date.now()): number | null {
    const from = at(run.startedAt) ?? at(run.createdAt);
    if (from === null) return null;
    const to = at(run.completedAt) ?? now;
    const span = to - from;
    return span >= 0 ? span : null;
}

function at(iso: string | null): number | null {
    if (!iso) return null;
    const stamp = Date.parse(iso);
    return Number.isNaN(stamp) ? null : stamp;
}

/** A duration, rounded to the unit a person would use.
 *
 *  Sub-second is "under a second" rather than "0s": a decision node finishing
 *  in 40ms is a real thing that happened, and a zero reads as a step that was
 *  skipped. */
export function sayFor(millis: number | null): string | null {
    if (millis === null || millis < 0) return null;
    if (millis < 1000) return "under a second";
    const seconds = Math.round(millis / 1000);
    if (seconds < 60) return seconds + "s";
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return minutes + "m";
    const hours = Math.round(minutes / 60);
    if (hours < 48) return hours + "h";
    return Math.round(hours / 24) + "d";
}

/** When something happened, relative, for anything inside a fortnight — and
 *  a date past that, because "19 days ago" is a number nobody converts. */
export function sayWhen(iso: string | null, now = Date.now()): string | null {
    const stamp = at(iso);
    if (stamp === null) return null;
    const ago = now - stamp;
    if (ago < 0) return "just now";
    if (ago < 60_000) return "just now";
    const minutes = Math.floor(ago / 60_000);
    if (minutes < 60) return minutes + "m ago";
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + "h ago";
    const days = Math.floor(hours / 24);
    if (days < 14) return days === 1 ? "yesterday" : days + "d ago";
    return new Date(stamp).toLocaleDateString([], { month: "short", day: "numeric" });
}

/** How long this has been sitting on somebody.
 *
 *  The entire reason the inbox exists: `runs.waitingAssignedToMe` was written
 *  because a form wait can sit for days with nothing anywhere saying so. The
 *  age is the information, so it is said plainly and it is said first.
 */
export function sayStuckFor(wait: WaitRow, run: RunRow, now = Date.now()): string {
    const since = at(wait.createdAt) ?? at(run.startedAt) ?? at(run.createdAt);
    if (since === null) return "Waiting on you";
    const said = sayFor(now - since);
    return said ? "Waiting on you for " + said : "Waiting on you";
}

/* ── ordering ──────────────────────────────────────────────────────── */

/** Oldest first. An inbox sorted newest-first buries the thing that has been
 *  stuck longest, which is the one item on the list that is actually going
 *  wrong.
 *
 *  Generic over the row rather than fixed to `Assignment`: the inbox crosses
 *  pods, so it carries a pod name alongside each pair and then sorts the
 *  merged list. Narrowing to `Assignment` there would mean sorting, widening,
 *  and sorting again. */
export function byStuckLongest<T extends Assignment>(list: T[]): T[] {
    return [...list].sort((left, right) => {
        const leftAt = at(left.wait.createdAt) ?? at(left.run.createdAt) ?? Number.MAX_SAFE_INTEGER;
        const rightAt = at(right.wait.createdAt) ?? at(right.run.createdAt) ?? Number.MAX_SAFE_INTEGER;
        return leftAt - rightAt;
    });
}

/** Newest first, which is what a run list is read for: what happened last. */
export function byNewest(list: RunRow[]): RunRow[] {
    return [...list].sort((left, right) => {
        const leftAt = at(left.startedAt) ?? at(left.createdAt) ?? 0;
        const rightAt = at(right.startedAt) ?? at(right.createdAt) ?? 0;
        return rightAt - leftAt;
    });
}

/* ── refusals, in words ────────────────────────────────────────────── */

/** What a failed submission actually means.
 *
 *  Every one of these is a real state rather than a fault, and each has its
 *  own sentence. 422 is a node mismatch — the run moved to a different form.
 *  409 is "not waiting on a form at all" any more. 403 is somebody else's
 *  form (`api/workflow_run_controller.py:81`).
 */
export function sayRefusal(status: number | undefined, fallback: string): string {
    switch (status) {
        case 403: return "This form is not assigned to you.";
        case 404: return "That run is gone.";
        case 409: return "This run is no longer waiting on a form — somebody got there first, or it moved on.";
        case 422: return "The run has moved to a different step, so this form can no longer be submitted.";
        default: return fallback;
    }
}

/** Why a cancel was refused. A terminal run is the only common one. */
export function sayCancelRefusal(status: number | undefined, fallback: string): string {
    if (status === 409) return "That run had already finished.";
    if (status === 403) return "You may not cancel this run.";
    if (status === 404) return "That run is gone.";
    return fallback;
}
