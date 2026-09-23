import { displayAgentName } from "@/data/agent-names";

/** A pod's schedules, read off the wire.
 *
 *  Pure, and tested, for the reason `agents.ts` is: every field here is a wire
 *  field, and reading one that does not exist fails silently rather than
 *  loudly. The failure this module exists to prevent is the quiet one — a
 *  schedule that has errored five times running drawing exactly like a healthy
 *  one, because the profile kept `is_active` and threw the rest away.
 *
 *  Two shapes, because the API has two. `ScheduleDetailResponse` is a standing
 *  arrangement; `ScheduleRunResponse` is one firing of it. They live in
 *  `app/modules/schedule/api/schemas/schedule_schemas.py`, and the enums they
 *  carry in `app/modules/schedule/domain/schedule.py`.
 */

/* ── what an action is called ───────────────────────────────────────
   `allowed_actions` carries permission ids, and these are the three the
   schedule routes check (`Permissions.SCHEDULE_*`). Retry is gated on
   `schedule.update` rather than a retry permission of its own — see
   `retry_schedule_run` in `services/schedule_run_service.py`, which requires
   SCHEDULE_UPDATE. */
export const SCHEDULE_READ = "schedule.read";
export const SCHEDULE_EDIT = "schedule.update";
export const SCHEDULE_REMOVE = "schedule.delete";

/** What fires a schedule. `UNKNOWN` is a deployment shipping a fourth kind,
 *  not a bug — the row still draws, it just cannot say more than the word. */
export type ScheduleKind = "TIME" | "WEBHOOK" | "DATASTORE" | "UNKNOWN";

/** The outcome of the last fire attempt. `FILTERED` is not a failure: the AI
 *  filter read the event and said it did not matter. */
export type FireStatus = "TRIGGERED" | "FILTERED" | "ERROR" | "";

/** What a schedule wakes. Exactly one of the two, enforced by the API's own
 *  `require_one_target_name` validator — but a row can still arrive with
 *  neither, because a target can be deleted out from under a schedule. */
export interface Target {
    kind: "agent" | "workflow" | "none";
    /** The wire name, which is what a call is keyed by. */
    name: string;
    /** What to call it on screen. `POD_DEFAULT` is Lem, never "Pod default". */
    label: string;
}

export interface StandingJob {
    id: string;
    /** The pod-scoped name, normalised by the server. The identifier, not the
     *  heading — see `title`. */
    name: string;
    title: string;
    kind: ScheduleKind;
    /** What fires it, in words: "Every weekday at 09:00", "When Slack sends". */
    trigger: string;
    /** The literal behind those words — a cron string, a source, a table —
     *  shown alongside rather than instead, because a sentence that paraphrases
     *  a cron is a guess and the cron is the fact. */
    triggerLiteral: string;
    target: Target;
    instruction: string;
    /** An AI filter on the incoming event: the schedule fires only when this
     *  says the event mattered. Distinct from `instruction`, which directs the
     *  work once the firing is settled. */
    filter: string;
    active: boolean;
    /** The breaker paused it, rather than a person. Computed by the server
     *  (`paused_by_failures`), because the threshold is a deployment setting
     *  this client cannot see. */
    pausedByFailures: boolean;
    /** Created by workflow execution for its own waits and timeouts. */
    internal: boolean;
    since: string;
    lastFiredAt: string;
    lastFireStatus: FireStatus;
    lastError: string;
    /** The number that turns "it ran" into "it has been broken since Tuesday". */
    failures: number;
    actions: string[];
    /** Set when the item was not a schedule shape at all. The row still draws;
     *  it just says so. */
    broken: boolean;
}

function record(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function text(value: unknown): string {
    if (value === null || value === undefined) return "";
    if (typeof value === "string") return value.trim();
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    return "";
}

function count(value: unknown): number {
    return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

/** `daily-competitor-refresh` is a filename; "Daily competitor refresh" is a
 *  title. The server normalises every schedule name to that first form. */
export function humanizeName(raw: string): string {
    const words = raw.replace(/[-_]+/g, " ").trim();
    return words ? words[0].toUpperCase() + words.slice(1) : raw;
}

const KINDS: ScheduleKind[] = ["TIME", "WEBHOOK", "DATASTORE"];

function kindOf(value: unknown): ScheduleKind {
    const said = text(value).toUpperCase() as ScheduleKind;
    return KINDS.includes(said) ? said : "UNKNOWN";
}

const FIRE_STATUSES: FireStatus[] = ["TRIGGERED", "FILTERED", "ERROR"];

function fireStatusOf(value: unknown): FireStatus {
    const said = text(value).toUpperCase() as FireStatus;
    return FIRE_STATUSES.includes(said) ? said : "";
}

const DAY_WORDS: Record<string, string> = {
    "1-5": "every weekday",
    "*": "every day",
    "?": "every day",
    "0,6": "at weekends",
    "6,0": "at weekends",
};

const WEEKDAYS = ["on Sundays", "on Mondays", "on Tuesdays", "on Wednesdays", "on Thursdays", "on Fridays", "on Saturdays"];

export function describeCron(cron: string): string {
    const parts = cron.trim().split(/\s+/);
    if (parts.length !== 5) return cron.trim();
    const [minute, hour, day, month, weekday] = parts;
    if (day !== "*" || month !== "*") return cron.trim();
    if (minute.startsWith("*/") && hour === "*") {
        const every = Number(minute.slice(2));
        return Number.isFinite(every) && every > 0 ? "Every " + every + " minutes" : cron.trim();
    }
    if (hour === "*" && weekday === "*" && /^\d+$/.test(minute)) {
        return Number(minute) === 0 ? "Every hour" : "Every hour at " + String(minute).padStart(2, "0") + " past";
    }
    if (!/^\d+$/.test(minute) || !/^\d+$/.test(hour)) return cron.trim();
    const time = String(hour).padStart(2, "0") + ":" + String(minute).padStart(2, "0");
    const days = DAY_WORDS[weekday] ?? (/^\d$/.test(weekday) ? WEEKDAYS[Number(weekday) % 7] : "");
    return days ? days[0].toUpperCase() + days.slice(1) + " at " + time : "Every day at " + time;
}

/** What fires this, said in words, and the literal it was read off.
 *
 *  Nothing here is invented. A TIME schedule's config is a cron or a
 *  `scheduled_at`; a WEBHOOK's is a `source` the deployment's registry
 *  recognises (plus, for a connector trigger, an id nothing else can decode);
 *  a DATASTORE's is a table and a set of operations, both of which the API
 *  normalises before storing (`normalize_datastore_schedule_config`).
 */
export function triggerOf(kind: ScheduleKind, config: Record<string, unknown>, connectorTriggerId: string): {
    trigger: string;
    literal: string;
} {
    if (kind === "TIME") {
        const cron = text(config["cron"]);
        const zone = text(config["timezone"]);
        if (cron) {
            /* The zone is part of the sentence, not a footnote: "every weekday
               at 09:00" means a different instant in Berlin than in UTC, and a
               config with the key absent means UTC — `zone_name_of` in
               `time_schedule_policy.py` says so. */
            const said = describeCron(cron);
            /* An expression with no sentence for it returns itself, and
               printing that beside its own literal drew `0 3 1 1,4,7,10 * UTC
               0 3 1 1,4,7,10 *` on the row. Say there is a cron, and let the
               literal beside it be the cron. */
            if (said === cron.trim()) return { trigger: "On a cron, read in " + (zone || "UTC"), literal: cron.trim() };
            return { trigger: said + " " + (zone || "UTC"), literal: cron.trim() };
        }
        const once = text(config["scheduled_at"]);
        if (once) return { trigger: "Once", literal: once };
        return { trigger: "On a schedule", literal: "" };
    }
    if (kind === "WEBHOOK") {
        const source = text(config["source"]);
        const said = source ? "When " + humanizeName(source).toLowerCase() + " sends something" : "When something arrives";
        return { trigger: said, literal: connectorTriggerId || source };
    }
    if (kind === "DATASTORE") {
        const table = text(config["table_name"]);
        const operations = Array.isArray(config["operations"])
            ? (config["operations"] as unknown[]).map((one) => text(one).toLowerCase()).filter(Boolean)
            : [];
        const when = Object.keys(record(config["when"]));
        const verbs = operations.length ? operations.join(" or ") : "changes";
        const said = table ? "When a row in " + table + " " + verbs : "When a row " + verbs;
        return { trigger: said, literal: when.length ? "only when " + when.join(", ") + " matches" : "" };
    }
    return { trigger: "Fires on something this app does not know", literal: "" };
}

function targetOf(row: Record<string, unknown>): Target {
    const workflow = text(row["workflow_name"]);
    if (workflow) return { kind: "workflow", name: workflow, label: humanizeName(workflow) };
    const agent = text(row["agent_name"]);
    if (agent) return { kind: "agent", name: agent, label: displayAgentName(agent) };
    /* Neither name, but an id: the target exists and this caller may not read
       its name. Different from a schedule wired to nothing, which is the case
       below and is a real fault — `has_target` on the entity is the same test. */
    if (text(row["workflow_id"])) return { kind: "workflow", name: "", label: "a workflow" };
    if (text(row["agent_id"])) return { kind: "agent", name: "", label: "an agent" };
    return { kind: "none", name: "", label: "" };
}

export function readSchedule(raw: unknown): StandingJob {
    const row = record(raw);
    const id = text(row["id"]);
    const name = text(row["name"]);
    const kind = kindOf(row["schedule_type"]);
    const config = record(row["config"]);
    const { trigger, literal } = triggerOf(kind, config, text(row["connector_trigger_id"]));
    return {
        id,
        name,
        title: humanizeName(name || text(row["workflow_name"]) || text(row["agent_name"]) || "Standing work"),
        kind,
        trigger,
        triggerLiteral: literal,
        target: targetOf(row),
        instruction: text(row["instruction"]),
        filter: text(row["filter_instruction"]),
        /* Absent reads as active, matching the model default. A row that omits
           the field is not a paused row. */
        active: row["is_active"] !== false,
        pausedByFailures: row["paused_by_failures"] === true,
        internal: row["is_internal"] === true,
        since: text(row["created_at"]),
        lastFiredAt: text(row["last_fired_at"]),
        lastFireStatus: fireStatusOf(row["last_fire_status"]),
        lastError: text(row["last_error"]),
        failures: count(row["consecutive_failures"]),
        actions: Array.isArray(row["allowed_actions"]) ? (row["allowed_actions"] as unknown[]).map(text).filter(Boolean) : [],
        broken: !id,
    };
}

/** The list, with the rows workflow execution made for itself left out.
 *
 *  `GET /pods/{id}/schedules` already excludes them — `ScheduleRepository.list`
 *  filters `is_internal is False` on both branches — so this is belt and
 *  braces rather than the filter. It is kept because the field is on the
 *  response and a deployment that stops filtering server-side would otherwise
 *  put a workflow's own thirty-minute timeout on a teammate's profile as
 *  though it were a job somebody gave it.
 */
export function readSchedules(raw: unknown): StandingJob[] {
    const items = Array.isArray(raw) ? raw : (record(raw)["items"] as unknown[] | undefined) ?? [];
    return (Array.isArray(items) ? items : []).map(readSchedule).filter((job) => !job.internal);
}

/** Whether an action is on offer. A broken row offers none: there is no id to
 *  aim a call at. */
export function may(job: Pick<StandingJob, "actions" | "broken">, action: string): boolean {
    if (job.broken) return false;
    return job.actions.includes(action);
}

/* ── how it is doing ────────────────────────────────────────────────── */

export type Tone = "ok" | "warn" | "bad" | "off";

/** One line about a schedule's health, and how loudly to say it.
 *
 *  The order is by consequence, not by field. A schedule the breaker paused is
 *  the worst case there is — it is off *and* broken, and the two halves used
 *  to be indistinguishable on the wire, which is what `paused_by_failures`
 *  exists to fix. A deliberate pause is not a fault, so it is `off` and not
 *  `warn`.
 */
export function healthOf(job: StandingJob): { tone: Tone; line: string } {
    if (job.broken) return { tone: "bad", line: "This row did not arrive as a schedule." };
    if (job.pausedByFailures) {
        return {
            tone: "bad",
            line: "Stopped by itself after " + job.failures + " failures in a row. Resuming clears the count.",
        };
    }
    if (!job.active) return { tone: "off", line: "It will not fire until somebody resumes it." };
    if (job.target.kind === "none") return { tone: "bad", line: "No agent or workflow is assigned to this schedule." };
    if (job.failures > 0) {
        const times = job.failures === 1 ? "once" : job.failures + " times in a row";
        return { tone: "bad", line: "Failed " + times + "." };
    }
    if (job.lastFireStatus === "ERROR") return { tone: "bad", line: "The last trigger failed." };
    if (job.lastFireStatus === "FILTERED") return { tone: "warn", line: "Skipped because the event did not match the filter." };
    if (!job.lastFiredAt) return { tone: "warn", line: "Not triggered yet." };
    return { tone: "ok", line: "" };
}

/* ── one firing ─────────────────────────────────────────────────────── */

export type RunStatus =
    | "RECEIVED" | "PROCESSING" | "DISPATCHED" | "COMPLETED"
    | "TARGET_FAILED" | "CANCELLED" | "FILTERED" | "FAILED" | "DEAD_LETTERED" | "";

const RUN_STATUSES: RunStatus[] = [
    "RECEIVED", "PROCESSING", "DISPATCHED", "COMPLETED",
    "TARGET_FAILED", "CANCELLED", "FILTERED", "FAILED", "DEAD_LETTERED",
];

/** The three the redrive accepts. `create_redrive` in
 *  `repositories/schedule_run_repository.py` tests exactly this set and
 *  answers nothing at all for the rest, which reaches the client as a 4xx
 *  about a run that was never retryable — so the button is not drawn. */
export const RETRYABLE: RunStatus[] = ["FAILED", "DEAD_LETTERED", "TARGET_FAILED"];

export interface ScheduleRun {
    id: string;
    status: RunStatus;
    /** In a person's words. */
    outcome: string;
    tone: Tone;
    attempts: number;
    targetKind: string;
    /** The run the target itself made — an agent run or a workflow run. */
    targetRunId: string;
    /** Set when this run is itself a retry of an earlier one. */
    retryOf: string;
    error: string;
    at: string;
    startedAt: string;
    finishedAt: string;
    broken: boolean;
}

const OUTCOMES: Record<string, { word: string; tone: Tone }> = {
    RECEIVED: { word: "Queued", tone: "warn" },
    PROCESSING: { word: "Running", tone: "warn" },
    DISPATCHED: { word: "Handed over", tone: "warn" },
    COMPLETED: { word: "Done", tone: "ok" },
    TARGET_FAILED: { word: "The target failed", tone: "bad" },
    CANCELLED: { word: "Cancelled", tone: "off" },
    FILTERED: { word: "Filtered out", tone: "off" },
    FAILED: { word: "Failed", tone: "bad" },
    DEAD_LETTERED: { word: "Retries exhausted", tone: "bad" },
};

export function readRun(raw: unknown): ScheduleRun {
    const row = record(raw);
    const id = text(row["id"]);
    const said = text(row["status"]).toUpperCase() as RunStatus;
    const status = RUN_STATUSES.includes(said) ? said : "";
    const known = OUTCOMES[status];
    /* `error_type` is the class of fault and `error_code` the specific one.
       Both are often null on a target failure — the detail lives on the
       target's own run — so the outcome word has to stand on its own. */
    const error = [text(row["error_type"]), text(row["error_code"])].filter(Boolean).join(" · ");
    return {
        id,
        status,
        outcome: known?.word ?? (status || "Unreadable"),
        tone: known?.tone ?? "warn",
        attempts: count(row["attempts"]),
        targetKind: text(row["target_kind"]).toLowerCase(),
        targetRunId: text(row["target_run_id"]),
        retryOf: text(row["redrive_of_run_id"]),
        error,
        /* When the event happened, falling back to when the row was written.
           A TIME schedule has no source event, so `source_occurred_at` is the
           scheduled instant there and null on some webhook deliveries. */
        at: text(row["source_occurred_at"]) || text(row["created_at"]),
        startedAt: text(row["started_at"]),
        finishedAt: text(row["completed_at"]),
        broken: !id,
    };
}

export function readRuns(raw: unknown): ScheduleRun[] {
    const items = Array.isArray(raw) ? raw : (record(raw)["items"] as unknown[] | undefined) ?? [];
    return (Array.isArray(items) ? items : []).map(readRun);
}

/** Whether this run can be redriven.
 *
 *  `status` on the response is already the *effective* status: `to_entity` in
 *  `infrastructure/models/run.py` reads `target_outcome or status`, so a run
 *  that dispatched cleanly and whose workflow then failed arrives as
 *  TARGET_FAILED rather than DISPATCHED. That is the whole reason this test
 *  can be a one-liner on the client.
 */
export function canRetry(run: Pick<ScheduleRun, "status" | "broken">): boolean {
    return !run.broken && RETRYABLE.includes(run.status);
}

/* ── how long ago ───────────────────────────────────────────────────── */

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "3h ago", "5d ago". Pure so it can be tested, with `now` passed in rather
 *  than read, because a clock in a test is a flaky test. */
export function agoOf(iso: string, now: number = Date.now()): string {
    if (!iso) return "";
    const at = new Date(iso).getTime();
    if (Number.isNaN(at)) return "";
    const gap = now - at;
    if (gap < 0) return "in a moment";
    if (gap < MINUTE) return "just now";
    if (gap < HOUR) return Math.floor(gap / MINUTE) + "m ago";
    if (gap < DAY) return Math.floor(gap / HOUR) + "h ago";
    if (gap < 7 * DAY) return Math.floor(gap / DAY) + "d ago";
    return Math.floor(gap / (7 * DAY)) + "w ago";
}

/* ── making one ─────────────────────────────────────────────────────── */

/** A new TIME schedule, as this app lets one be written.
 *
 *  Only TIME. A WEBHOOK schedule needs a connected account and a connector
 *  trigger id, and a DATASTORE one needs a table plus an explicit operation
 *  set the workflow is built to handle — neither is a form this section can
 *  put in front of somebody honestly, so neither is offered.
 */
export interface ScheduleDraft {
    name: string;
    cron: string;
    timezone: string;
    target: "agent" | "workflow";
    agentName: string;
    workflowName: string;
    instruction: string;
}

/** Something a new schedule could be pointed at. The name is what the request
 *  carries; the label is what a person picks. */
export interface TargetChoice {
    kind: "agent" | "workflow";
    name: string;
    label: string;
}

export function blankDraft(): ScheduleDraft {
    return { name: "", cron: "0 9 * * 1-5", timezone: "", target: "agent", agentName: "", workflowName: "", instruction: "" };
}

/** The floor the platform enforces, from `schedule_minimum_interval_minutes`
 *  in `app/modules/schedule/config.py`. It is a deployment setting, so this is
 *  the default rather than the truth — the server is still the one that
 *  refuses, and its refusal is shown verbatim when it comes. */
export const MINIMUM_MINUTES = 15;

/** Everything wrong with a draft, keyed by field. Empty means send it.
 *
 *  This is the client half of rules the API also enforces; it exists so a
 *  person is told before the round trip, not instead of it. The one rule that
 *  is *only* enforceable server-side — "an agent with no standing instruction
 *  of its own must be told what to do" — depends on the resolved agent and is
 *  deliberately not guessed at here. It arrives as a 400.
 */
export function draftProblems(draft: ScheduleDraft): Record<string, string> {
    const wrong: Record<string, string> = {};
    if (!draft.name.trim()) wrong.name = "Give it a name. It is what this row will be called.";
    const fields = draft.cron.trim().split(/\s+/).filter(Boolean);
    if (!draft.cron.trim()) {
        wrong.cron = "A cron expression says when it fires.";
    } else if (fields.length !== 5) {
        wrong.cron = "Five fields: minute, hour, day of month, month, day of week. This has " + fields.length + ".";
    } else if (/^\*(\/1)?$/.test(fields[0])) {
        wrong.cron = "Every minute is refused — the platform's floor is one fire every " + MINIMUM_MINUTES + " minutes.";
    } else {
        const step = /^\*\/(\d+)$/.exec(fields[0]);
        if (step && Number(step[1]) < MINIMUM_MINUTES) {
            wrong.cron = "The platform's floor is one fire every " + MINIMUM_MINUTES + " minutes.";
        }
    }
    if (draft.target === "agent" && !draft.agentName.trim()) wrong.target = "Pick the agent this wakes.";
    if (draft.target === "workflow" && !draft.workflowName.trim()) wrong.target = "Pick the workflow this runs.";
    return wrong;
}

/** The POST body, in the shape `CreateScheduleRequest` takes.
 *
 *  Exactly one of `agent_name` / `workflow_name` — its `require_one_target_name`
 *  validator rejects both and neither — so the unused one is left off rather
 *  than sent as null, which would count as present.
 */
export function createRequest(draft: ScheduleDraft): Record<string, unknown> {
    const config: Record<string, unknown> = { cron: draft.cron.trim() };
    /* An absent `timezone` means UTC and stays absent: `resolve_zone` reads
       None as UTC, and writing "UTC" into the config would give every schedule
       a diff to no effect. */
    if (draft.timezone.trim()) config.timezone = draft.timezone.trim();
    const body: Record<string, unknown> = {
        name: draft.name.trim(),
        schedule_type: "TIME",
        config,
    };
    if (draft.target === "workflow") body.workflow_name = draft.workflowName.trim();
    else body.agent_name = draft.agentName.trim();
    if (draft.instruction.trim()) body.instruction = draft.instruction.trim();
    return body;
}

/** A few cadences worth offering, each with the cron it actually produces.
 *
 *  Every label here is `describeCron` of its own expression, and a test holds
 *  that. It means the words in the picker are the same words the row will show
 *  afterwards — a picker whose "weekly" becomes "0 8 * * 1" in the list is the
 *  kind that teaches somebody not to trust the list. The cron field below the
 *  picker stays visible for the same reason, and every entry clears the
 *  platform's frequency floor.
 */
export const CADENCES: { label: string; cron: string }[] = [
    "0 9 * * 1-5",
    "0 7 * * *",
    "0 9 * * 1",
    "0 * * * *",
    "*/30 * * * *",
].map((cron) => ({ label: describeCron(cron), cron }));
