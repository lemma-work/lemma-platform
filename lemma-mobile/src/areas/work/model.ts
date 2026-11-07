/** The phone's own reading of what a Lemma runs, on top of the web's (`web/`).
 *
 *  Some of these are the web's, lifted out of its components because they
 *  live there beside the markup (`sayRunLine` from `workflow/run-row.tsx`,
 *  `whatStarts` from `space/workflow-page.tsx`, `sayAge` from
 *  `space/run-board.tsx`, `sayPermissions` and the access sentence from
 *  `space/agent-page.tsx`, the save rule from `outcomes/data.ts`); each says
 *  where it came from. The rest is how a phone lays the same facts out.
 *  Pure, and tested in `tests/work.test.ts`. */

import { stampOf } from "../../data/model";
import { isPodDefaultAgent } from "@web/data/agent-names";
import { AGENT_EDIT, AGENT_REMOVE, AGENT_RUN, may as mayAgent, type AgentDetail, type AgentRow } from "@web/data/agents";
import type { BoardCard } from "@web/workflow/board";
import { draftPlan, PLAN_SECTIONS, serializePlan, stampOf as stampLine, type Plan, type PlanLine, type PlanSection } from "./web/plan";
import { millisOf, runMillis, runTone, sayFor, sayWhen, stillGoing, type Assignment, type RunRow, type WorkflowRow } from "@web/workflow/runs";
import type { Automation } from "@web/workflow/turn-on";
import type { WorkflowShape } from "@web/workflow/shape";
import { humanizeName, type Scope, type StandingJob } from "@web/schedule/schedules";
import type { Member, Surface } from "./source";

/* ── runs, in a line ─────────────────────────────────────────────────── */

const HOW: Record<string, string> = {
    MANUAL: "by hand",
    SCHEDULED: "on a schedule",
    EVENT: "by an event",
    DATASTORE_EVENT: "when a row changed",
};

/** What a run is doing, in one line: where it died, where it is, or how it
 *  started (`workflow/run-row.tsx`). Named by the step's label when the
 *  caller has the steps. */
export function sayRunLine(run: RunRow, names?: ReadonlyMap<string, string>): string {
    const step = (id: string) => names?.get(id) ?? id;
    if (run.failedNodeId) return "Failed at " + step(run.failedNodeId) + (run.error ? ": " + run.error : "");
    if (stillGoing(run.status) && run.currentNodeId) return "At " + step(run.currentNodeId);
    return "Started " + (HOW[run.startType ?? "MANUAL"] ?? "by hand");
}

/** Why a run exists, for its page (`space/run-page.tsx`). */
export const STARTED: Record<string, string> = {
    MANUAL: "Started by hand",
    SCHEDULED: "Started on a schedule",
    EVENT: "Started by an event",
    DATASTORE_EVENT: "Started when a row changed",
};

/** How a run's row ends: the dot the app draws everywhere. Violet is for what
 *  needs the reader or is moving; amber for what went wrong. */
export type RunMark = "yours" | "going" | "failed" | "quiet";

export function markOf(run: Pick<RunRow, "status">, yours: boolean): RunMark {
    if (yours) return "yours";
    const tone = runTone(run.status);
    if (tone === "going") return "going";
    if (tone === "bad") return "failed";
    return "quiet";
}

/** The run's line under its title on its own page: how far, who, when, how
 *  long. "3 of 4" on a finished run reads as one missing, so a finished run
 *  says how many ran. */
export function runMeta(run: RunRow, progress: { done: number; total: number } | null, owner: string | null, now = Date.now()): string {
    const going = stillGoing(run.status);
    const took = sayFor(runMillis(run, now));
    return [
        progress && progress.total > 0
            ? going ? progress.done + " of " + progress.total + " steps" : progress.done + (progress.done === 1 ? " step ran" : " steps ran")
            : null,
        ((run.startType && STARTED[run.startType]) || "Started") + (owner ? " by " + owner : ""),
        sayWhen(run.startedAt ?? run.createdAt, now),
        took ? (going ? "going for " : "took ") + took : null,
    ].filter(Boolean).join(" · ");
}

/** When something happened, as every list in the app says it: "4m", "Tue",
 *  "Oct 3". A zoneless time off the wire is UTC (`millisOf`). */
export function stampAt(iso: string | null | undefined, now = Date.now()): string | null {
    const at = millisOf(iso ?? null);
    return at === null ? null : stampOf(at, now);
}

/** Who a run, a schedule or a wait belongs to, by user id: "you", a name, or
 *  null when nobody here is that person. */
export function personOf(userId: string | null | undefined, me: string | null, members: readonly Member[]): string | null {
    if (!userId) return null;
    if (me && userId === me) return "you";
    return members.find((member) => member.userId === userId)?.name ?? null;
}

/* ── the Runs tab ────────────────────────────────────────────────────── */

export interface FlowSummary {
    flow: WorkflowRow;
    last: RunRow | null;
    /** Runs of it in the Lemma's latest, newest first. */
    runs: number;
    /** Forms of its runs waiting on the reader. */
    waiting: number;
    /** Something of it is going now. */
    going: boolean;
    /** Schedules on that run it. */
    schedules: number;
    /** Agents it hands work to that are not here (`workflows-page.tsx`). */
    missing: string[];
}

/** One line per workflow, from the reads the tab already holds. */
export function flowSummaries(input: {
    flows: readonly WorkflowRow[];
    runs: readonly RunRow[];
    waiting: readonly Assignment[];
    schedules: readonly StandingJob[];
    agentNames: readonly string[] | null;
}): FlowSummary[] {
    return input.flows.map((flow) => {
        const own = input.runs.filter((run) => run.workflowId === flow.id);
        const last = [...own].sort((a, b) => (millisOf(b.startedAt ?? b.createdAt) ?? 0) - (millisOf(a.startedAt ?? a.createdAt) ?? 0))[0] ?? null;
        const missing = input.agentNames
            ? flow.targets.filter((one) => one.startsWith("agent:")).map((one) => one.slice(6))
                .filter((agent) => agent && !isPodDefaultAgent(agent) && !input.agentNames!.includes(agent))
            : [];
        return {
            flow,
            last,
            runs: own.length,
            waiting: input.waiting.filter((one) => one.run.workflowId === flow.id).length,
            going: own.some((run) => stillGoing(run.status) && run.status !== "WAITING"),
            schedules: input.schedules.filter((job) => job.target.kind === "workflow" && job.target.name === flow.name && job.active).length,
            missing,
        };
    });
}

/** The line under a workflow's name in the list. */
export function flowLine(summary: FlowSummary, everyRun: boolean, now = Date.now()): string {
    const { flow, last } = summary;
    const what = [flow.perPerson ? "Each person" : "Admin", !flow.active ? "Paused" : null, summary.missing.length ? "Needs setup" : null];
    const when = last ? "last ran " + (sayWhen(last.startedAt ?? last.createdAt, now) ?? "—") : everyRun ? "never run" : "no recent runs";
    const on = summary.schedules > 0 ? summary.schedules + (summary.schedules === 1 ? " schedule on" : " schedules on") : null;
    return [...what, when, on].filter(Boolean).join(" · ");
}

/** The runs still going that are not waiting on the reader, and the ones
 *  that finished: the tab's Running and History views. */
export function splitRuns(runs: readonly RunRow[], waiting: readonly Assignment[]): { running: RunRow[]; history: RunRow[] } {
    const owed = new Set(waiting.map((one) => one.run.id));
    return {
        running: runs.filter((run) => stillGoing(run.status) && !owed.has(run.id)),
        history: runs.filter((run) => !stillGoing(run.status)),
    };
}

/* ── one workflow ────────────────────────────────────────────────────── */

/** What starts it, said (`space/workflow-page.tsx`). */
export function whatStarts(automation: Automation): string {
    switch (automation.kind) {
        case "manual": return "Someone presses Run now.";
        case "time": return "A schedule, on the times you choose.";
        case "event": return "An event in " + (automation.connectorId || "a connected app") + (automation.triggerId ? " — " + automation.triggerId.replace(/_/g, " ") : "") + ".";
        case "rows": return "A row " + (automation.operations.length ? automation.operations.map((op) => op.toLowerCase() + "d").join(" or ").replace("insertd", "added").replace("updated", "changed").replace("deleted", "removed") : "changing") + " in " + (automation.table || "a table") + ".";
    }
}

/** Each step's label by its id, for run rows beside the steps. */
export function stepNames(shape: Pick<WorkflowShape, "ordered" | "orphans"> | null | undefined): Map<string, string> {
    return new Map([...(shape?.ordered ?? []), ...(shape?.orphans ?? [])].filter((step) => step.label).map((step) => [step.id, step.label as string]));
}

/** How many steps a run counts, without the End (so a workflow's page and a
 *  run's "3 of 5" agree). */
export function stepCount(shape: Pick<WorkflowShape, "ordered" | "orphans"> | null | undefined, fallback: number | null): number | null {
    if (!shape) return fallback;
    return [...shape.ordered, ...shape.orphans].filter((step) => step.kind !== "END").length;
}

/** How long a card has been at its step, or when its run started, said as
 *  such (`space/run-board.tsx`). */
export function sayAge(card: Pick<BoardCard, "since" | "here">, now = Date.now()): string {
    const at = millisOf(card.since);
    if (card.here && at !== null) {
        const said = sayFor(now - at);
        if (said) return said + " here";
    }
    return "started " + (sayWhen(card.since, now) ?? "—");
}

/* ── a step's output, as fields ──────────────────────────────────────── */

/** `ticket_id` → "Ticket id", `replyText` → "Reply text" (`workflow/data-view.tsx`). */
export function wordsOf(key: string): string {
    const spaced = key.replace(/[_-]+/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").trim();
    return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export type Shown = { kind: "text"; text: string } | { kind: "prose"; text: string } | { kind: "data"; text: string };

/** One value as a line of a document (`workflow/data-view.tsx`'s `Scalar`). */
export function shownOf(value: unknown): Shown {
    if (typeof value === "boolean") return { kind: "text", text: value ? "Yes" : "No" };
    if (typeof value === "number") return { kind: "text", text: value.toLocaleString() };
    if (typeof value === "string") return value.length > 160 || value.includes("\n") ? { kind: "prose", text: value } : { kind: "text", text: value };
    if (Array.isArray(value) && value.every((one) => ["string", "number", "boolean"].includes(typeof one))) {
        return { kind: "text", text: value.length === 0 ? "None" : value.map(String).join(", ") };
    }
    return { kind: "data", text: jsonOf(value) };
}

export function jsonOf(value: unknown): string {
    try {
        return typeof value === "string" ? value : JSON.stringify(value, null, 2);
    } catch {
        return String(value);
    }
}

/** A step's output as rows, the sentence already said above it left out;
 *  null when there is nothing to show. */
export function fieldsOf(value: unknown, lead: string | null): { lead: string | null; fields: { label: string; value: Shown }[]; whole: Shown | null } | null {
    if (value === undefined || value === null) return null;
    if (typeof value === "object" && !Array.isArray(value) && Object.keys(value as object).length === 0) return null;
    if (typeof value !== "object") return { lead: null, fields: [], whole: { kind: "prose", text: String(value) } };
    if (Array.isArray(value)) return { lead: null, fields: [], whole: shownOf(value) };
    const fields = Object.entries(value as Record<string, unknown>)
        .filter(([, one]) => one !== undefined && one !== null && !(typeof one === "string" && one === lead))
        .map(([key, one]) => ({ label: wordsOf(key), value: shownOf(one) }));
    return { lead, fields, whole: null };
}

/* ── schedules ───────────────────────────────────────────────────────── */

/** What a schedule runs, as a sentence: the Lemma's own agent is the Lemma
 *  doing it itself (`schedule/standing-work.tsx`). */
export function runsLine(job: StandingJob, lemmaName: string): string {
    if (job.target.kind === "none") return "Runs nothing";
    if (!job.target.name) return "Runs " + job.target.label;
    if (isPodDefaultAgent(job.target.name)) return lemmaName + " does this itself";
    return "Runs the " + job.target.kind + " " + job.target.label;
}

/** Yours first, then what others shared; not split until the reader is known. */
export function scheduleGroups(jobs: readonly StandingJob[], me: string | null): { key: "mine" | "space"; title: string; rows: StandingJob[] }[] {
    if (me === null) return [];
    return [
        { key: "mine" as const, title: "Yours", rows: jobs.filter((job) => !job.ownerId || job.ownerId === me) },
        { key: "space" as const, title: "Shared with everyone here", rows: jobs.filter((job) => job.ownerId && job.ownerId !== me) },
    ].filter((group) => group.rows.length > 0);
}

/* ── agents ──────────────────────────────────────────────────────────── */

/** `document.read`, `document.write` → "can read and write" (`space/agent-page.tsx`). */
export function sayPermissions(codes: readonly string[]): string {
    const verbs = [...new Set(codes.map((code) => code.split(".").pop()!.toLowerCase().replace(/_/g, " ")))];
    if (verbs.length === 0) return "no access";
    return "can " + (verbs.length === 1 ? verbs[0] : verbs.slice(0, -1).join(", ") + " and " + verbs[verbs.length - 1]);
}

/** Who may use an agent and what the reader may do to it, as two sentences
 *  (`space/agent-page.tsx`'s Access). */
export function accessOf(detail: AgentDetail, lemmaName: string): { who: string; you: string } {
    /* The web's `whyNot` names the pod and the API; the same reasons, said
       the phone's way. */
    const whyNot = (agent: AgentDetail, action: string) => {
        if (mayAgent(agent, action)) return "";
        if (agent.broken) return "This row has no name, so nothing can be done to it.";
        if (agent.front) return lemmaName + "’s own agent is changed by talking to it, not here.";
        return "You may read " + agent.label + ", not change it.";
    };
    const verbs: string[] = [];
    if (mayAgent(detail, AGENT_RUN)) verbs.push("use it");
    if (mayAgent(detail, AGENT_EDIT)) verbs.push("change it");
    if (mayAgent(detail, AGENT_REMOVE) && !detail.front) verbs.push("delete it");
    const you = verbs.length === 0
        ? "You can see it, but not use or change it."
        : "You can " + (verbs.length === 1 ? verbs[0] : verbs.slice(0, -1).join(", ") + " and " + verbs[verbs.length - 1]) + ".";
    const why = !mayAgent(detail, AGENT_EDIT) ? whyNot(detail, AGENT_EDIT) : "";
    return {
        who: detail.visibility === "RESTRICTED" ? "Only the people it is shared with can use it." : "Everyone in " + lemmaName + " can use it.",
        you: you + (why ? " " + why : ""),
    };
}

/** What hands an agent work: schedules aimed at it, workflows with a step
 *  it takes (`space/agent-page.tsx`'s Used by). */
export function usedBy(agent: Pick<AgentRow, "name" | "front">, jobs: readonly StandingJob[], flows: readonly WorkflowRow[]): { jobs: StandingJob[]; flows: WorkflowRow[] } {
    const wire = agent.front ? "POD_DEFAULT" : agent.name;
    return {
        jobs: jobs.filter((job) => job.target.kind === "agent" && (job.target.name === wire || job.target.name === agent.name)),
        flows: flows.filter((flow) => flow.targets.includes("agent:" + agent.name) || flow.targets.includes("agent:" + wire)),
    };
}

/** The agent's own surfaces (`data/surface-settings.ts`'s `surfacesForAgent`). */
export function surfacesFor(surfaces: readonly Surface[], agent: Pick<AgentRow, "name" | "front">): Surface[] {
    return surfaces.filter((surface) => (agent.front ? surface.mine : !surface.mine && surface.agentKey === agent.name));
}

/** A surface's state in words (`data/surface-settings.ts`'s `surfaceStatus`). */
export function surfaceState(status: string, active: boolean): string {
    switch (status) {
        case "PENDING_ADMIN_CONSENT": return "Needs administrator approval";
        case "NEEDS_SETUP": return "Finish setup";
        case "ERROR": return "Needs attention";
        case "INACTIVE": return "Disabled";
        default: return active ? "Connected" : "Needs attention";
    }
}

/** A platform's name as people say it. */
export function platformWord(platform: string): string {
    const said: Record<string, string> = { WHATSAPP: "WhatsApp", TELEGRAM: "Telegram", SLACK: "Slack", TEAMS: "Teams", DISCORD: "Discord", EMAIL: "Email", RESEND: "Email", GROUP: "Group chat" };
    return said[platform.toUpperCase()] ?? humanizeName(platform.toLowerCase());
}

/* ── the web's words, the phone's way ────────────────────────────────── */

/** Who a schedule is for, in the phone's words: the web's `SCOPE_NOTE`
 *  says "the whole space", and the phone says neither space nor pod. */
export const SCOPE_NOTE: Record<Scope, string> = {
    space: "Runs once, for everyone here",
    person: "Runs separately for each person, as them",
    mine: "Yours alone; nobody else sees it",
};

/** Who a workflow runs for (the web's `runsForOf`, without "space"). */
export function runsFor(automation: Automation, perPerson: boolean): string {
    if (automation.kind === "manual") return "Runs when someone starts it, as that person.";
    if (automation.kind === "rows") return "Runs as whoever owns the row that changed.";
    return perPerson
        ? "Each person turns it on for themselves; it runs as them, with their own accounts."
        : "One switch for everyone here; it runs as whoever turned it on.";
}

/** A line the web's readers write, said without the platform's own nouns. */
export function phoneWords(line: string): string {
    return line.replace("The timetable lives on a pod schedule, not on the workflow.", "The timetable is a schedule of its own, not part of the workflow.");
}

/** How loud a schedule's health is on a phone: amber only when it failed. */
export function healthMark(tone: "ok" | "off" | "warn" | "bad"): "bad" | "quiet" {
    return tone === "bad" ? "bad" : "quiet";
}

/* ── the plan ────────────────────────────────────────────────────────── */

/** plan.md changed between reading it and saving over it (`outcomes/data.ts`). */
export class PlanMovedOn extends Error {
    constructor() {
        super("plan.md changed since it was read. It has been read again; make the change once more.");
        this.name = "PlanMovedOn";
    }
}

/** The text to write for a plan (`outcomes/data.ts`'s `savePlan`): a new
 *  file is the plan written out; an existing one is edited in place, and
 *  only when it still says what the plan was read from, so a line someone
 *  (often the Lemma) wrote since is never written over. */
export function planToWrite(plan: Plan, current: string | null, exists: boolean): string {
    if (!exists) return serializePlan(plan);
    if (plan.file !== undefined && current !== plan.file) throw new PlanMovedOn();
    return serializePlan(plan, current);
}

function withSection(plan: Plan, section: PlanSection, lines: PlanLine[]): Plan {
    return { ...plan, sections: { ...plan.sections, [section]: lines } };
}

/** A line added at the end of a section, signed. */
export function addLine(plan: Plan, section: PlanSection, text: string, by: string, at: Date): Plan {
    const said = text.trim();
    if (!said) return plan;
    return withSection(plan, section, [...plan.sections[section], { text: said, by, on: stampLine(at) }]);
}

/** A line changed where it stands; emptied, it goes. Null when nothing changed. */
export function editLine(plan: Plan, section: PlanSection, index: number, text: string): Plan | null {
    const lines = [...plan.sections[section]];
    const said = text.trim();
    if (!lines[index] || said === lines[index].text) return null;
    if (said) lines[index] = { ...lines[index], text: said };
    else lines.splice(index, 1);
    return withSection(plan, section, lines);
}

export function removeLine(plan: Plan, section: PlanSection, index: number): Plan {
    const lines = [...plan.sections[section]];
    lines.splice(index, 1);
    return withSection(plan, section, lines);
}

/** A first plan from what the Lemma runs, every line signed by the reader
 *  who saves it (`outcomes/outcome.tsx`'s `PlanDraft`). */
export function draftOf(input: Parameters<typeof draftPlan>[0], by: string, at: Date): { draft: Plan; stamped: Plan } {
    const draft = draftPlan(input);
    const stamped: Plan = {
        ...draft,
        sections: Object.fromEntries(PLAN_SECTIONS.map((section) => [section, draft.sections[section].map((line) => ({ ...line, by, on: stampLine(at) }))])) as Record<PlanSection, PlanLine[]>,
    };
    return { draft, stamped };
}

/** What the plan's measure form edits, as text. */
export interface PlanMeta {
    outcome: string;
    kind: "week" | "date";
    target: string;
    unit: string;
    due: string;
    measure: string;
}

export function metaOf(plan: Plan): PlanMeta {
    return { outcome: plan.outcome, kind: plan.kind === "date" ? "date" : "week", target: plan.target ?? "", unit: plan.unit ?? "", due: plan.due ?? "", measure: plan.measure ?? "" };
}

export function withMeta(plan: Plan, meta: PlanMeta): Plan {
    return {
        ...plan,
        outcome: meta.outcome.trim(),
        kind: meta.kind,
        target: meta.target.trim() || null,
        unit: meta.unit.trim() || null,
        due: meta.kind === "date" ? meta.due.trim() || null : null,
        measure: meta.measure.trim() || null,
    };
}

/* ── X-ray ───────────────────────────────────────────────────────────── */

/** One thing a Lemma runs on, and where it opens. */
export interface Piece {
    key: string;
    icon: "clock" | "automation" | "agent" | "table" | "app" | "skill" | "plug";
    name: string;
    line: string;
    href: string | null;
    /** Off, paused or not yet set up: drawn quieter. */
    off: boolean;
}

export interface XrayGroup {
    title: string;
    empty: string;
    pieces: Piece[] | null;
    failed: boolean;
}

/** Everything a Lemma runs on, a group each (`outcomes/outcome.tsx`'s X-ray).
 *  A group whose read is still out is `pieces: null`. */
export function xrayGroups(input: {
    lemmaName: string;
    schedules: { data: readonly StandingJob[] | undefined; failed: boolean };
    workflows: { data: readonly WorkflowRow[] | undefined; failed: boolean };
    agents: { data: readonly AgentRow[] | undefined; failed: boolean };
    tables: { data: readonly { name: string; title: string; detail: string }[] | undefined; failed: boolean };
    apps: { data: readonly { name: string; label: string; detail: string }[] | undefined; failed: boolean };
    skills: { data: readonly { name: string; path: string; description: string }[] | undefined; failed: boolean };
    surfaces: { data: readonly Surface[] | undefined; failed: boolean };
    href: {
        schedule: (id: string) => string;
        workflow: (name: string) => string;
        agent: (name: string) => string;
        table: (name: string) => string;
        app: (name: string) => string;
        file: (path: string) => string;
        reach: string;
    };
    now?: number;
}): XrayGroup[] {
    const now = input.now ?? Date.now();
    const group = <T,>(title: string, empty: string, read: { data: readonly T[] | undefined; failed: boolean }, piece: (one: T) => Piece): XrayGroup => ({
        title, empty, failed: read.failed, pieces: read.data ? read.data.map(piece) : null,
    });
    const ran = (at: string) => {
        const said = at ? sayWhen(at, now) : null;
        return said ? "last ran " + said : "";
    };
    return [
        group("Runs on its own", "Nothing scheduled.", { ...input.schedules, data: input.schedules.data?.filter((job) => !job.internal && !job.broken) }, (job) => ({
            key: "s:" + job.id, icon: "clock", name: job.title, line: [job.trigger, job.active ? "" : "paused", ran(job.lastFiredAt)].filter(Boolean).join(" · "), href: input.href.schedule(job.id), off: !job.active,
        })),
        group("Workflows", "No workflows yet.", input.workflows, (flow) => ({
            key: "w:" + flow.id, icon: "automation", name: humanizeName(flow.name), line: [flow.description, flow.steps + (flow.steps === 1 ? " step" : " steps"), flow.active ? "" : "off"].filter(Boolean).join(" · "), href: input.href.workflow(flow.name), off: !flow.active,
        })),
        group("Agents", "Only its own agent.", input.agents, (agent) => ({
            key: "a:" + (agent.id || agent.name), icon: "agent", name: agent.front ? agent.label + " (answers here)" : agent.label, line: agent.blurb, href: agent.broken ? null : input.href.agent(agent.name), off: false,
        })),
        group("Tables", "No tables yet.", input.tables, (table) => ({
            key: "t:" + table.name, icon: "table", name: table.title, line: table.detail, href: input.href.table(table.name), off: false,
        })),
        group("Apps", "No apps yet.", input.apps, (app) => ({
            key: "p:" + app.name, icon: "app", name: app.label, line: app.detail, href: input.href.app(app.name), off: false,
        })),
        group("Skills it follows", "No skills written here yet.", input.skills, (skill) => ({
            key: "k:" + skill.path, icon: "skill", name: skill.name.replace(/\.md$/i, ""), line: skill.description, href: input.href.file(skill.path), off: false,
        })),
        group("Where people reach it", "Only here.", input.surfaces, (surface) => ({
            key: "r:" + surface.id, icon: "plug", name: platformWord(surface.platform), line: [surface.handle, surface.active ? "" : "off"].filter(Boolean).join(" · "), href: input.href.reach, off: !surface.active,
        })),
    ];
}

/** Where a skill's text is: a file, or a folder's `SKILL.md`. */
export function skillPath(item: { kind: string; path: string }): string {
    return item.kind === "file" ? item.path : item.path.replace(/\/+$/, "") + "/SKILL.md";
}
