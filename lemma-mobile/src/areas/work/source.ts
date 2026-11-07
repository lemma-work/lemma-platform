import type { PodConnector } from "@web/data/pod-connectors";
import type { AgentDetail, AgentDraft, AgentRow } from "@web/data/agents";
import type { Assignment, RunDetail, RunRow, WorkflowRow } from "@web/workflow/runs";
import type { Runtime } from "@web/data/runtimes";
import type { ScheduleRun, ServerEvent, StandingJob } from "@web/schedule/schedules";

/** What a Lemma runs: workflows, runs, schedules, agents, its plan (PARITY.md, D).
 *
 *  Everything these screens read or change, behind one interface with a
 *  live side (`live.ts`) and a sample side (`sample.ts`). Screens reach it
 *  only through `useArea("work")`, never the SDK directly, so sample data
 *  and a live account are the same screens.
 *
 *  Every read hands back the web's own shapes (`web/`), read by the web's own
 *  readers, so a run, a schedule or an agent says the same thing on both. */

export type { AgentDetail, AgentDraft, AgentRow, Assignment, RunDetail, RunRow, Runtime, ScheduleRun, ServerEvent, StandingJob, WorkflowRow };

export interface Page<T> {
    items: T[];
    next: string | null;
}

/** Someone in the Lemma: `id` is the membership, which is what a form's
 *  assignee names; `userId` is what a run or a schedule names its owner by. */
export interface Member {
    id: string;
    userId: string | null;
    name: string;
}

/** Where people reach the Lemma, or one of its agents (`podSurfaces.list`). */
export interface Surface {
    id: string;
    platform: string;
    handle: string;
    active: boolean;
    status: string;
    /** Answers as the Lemma itself. */
    mine: boolean;
    /** The agent it answers as, by row name, when it is not the Lemma's own. */
    agentKey: string | null;
}

/** A chat with one agent, for its page. */
export interface AgentChat {
    id: string;
    title: string;
    at: string | null;
}

export interface WorkSource {
    /* ── workflows and their runs ───────────────────────────────────── */
    workflows(podId: string): Promise<WorkflowRow[]>;
    /** One workflow as stored: nodes, edges, start. The web's readers
     *  (`readShape`, `readGraph`, `automationOf`) make sense of it. */
    workflow(podId: string, name: string): Promise<unknown>;
    /** The Lemma's latest runs across every workflow, newest first. */
    recentRuns(podId: string, limit: number): Promise<RunRow[]>;
    /** One workflow's runs, a page at a time, newest first. */
    workflowRuns(podId: string, name: string, page?: string): Promise<Page<RunRow>>;
    /** One workflow's runs still going, every one of them, for its board. */
    runsInFlight(podId: string, workflowId: string): Promise<RunRow[]>;
    /** The forms assigned to the reader here, each with its run. */
    waitingOnMe(podId: string): Promise<Assignment[]>;
    run(podId: string, runId: string): Promise<RunDetail | null>;
    /** The run as the server pushes it, every change, until it ends or
     *  `signal` aborts; null when this source cannot push (sample data). */
    watchRun: ((podId: string, runId: string, onRun: (run: RunDetail) => void, signal: AbortSignal) => Promise<void>) | null;
    startRun(podId: string, name: string): Promise<RunRow | null>;
    cancelRun(podId: string, runId: string): Promise<void>;
    retryRun(podId: string, runId: string): Promise<void>;
    submitForm(podId: string, runId: string, nodeId: string, inputs: Record<string, unknown>): Promise<void>;

    /* ── schedules ──────────────────────────────────────────────────── */
    schedules(podId: string): Promise<StandingJob[]>;
    scheduleRuns(podId: string, scheduleId: string, skipped: boolean): Promise<ScheduleRun[]>;
    setScheduleActive(podId: string, scheduleId: string, active: boolean): Promise<StandingJob>;
    setScheduleVisibility(podId: string, scheduleId: string, visibility: "POD" | "PERSONAL"): Promise<void>;
    retryScheduleRun(podId: string, scheduleId: string, runId: string): Promise<ScheduleRun>;
    /** A schedule made from a request the web's own builders wrote
     *  (`turnOnRequest`, `copyRequest`, `wakeRequest`). */
    createSchedule(podId: string, body: Record<string, unknown>): Promise<void>;
    deleteSchedule(podId: string, scheduleId: string): Promise<void>;
    /** Events on servers the reader connected, for what a copy listens to. */
    serverEvents(podId: string): Promise<ServerEvent[]>;
    /** The Lemma's connectors: what an app's event is heard through. */
    podConnectors(podId: string): Promise<PodConnector[]>;

    /* ── agents ─────────────────────────────────────────────────────── */
    agents(podId: string): Promise<AgentRow[]>;
    agent(podId: string, name: string): Promise<AgentDetail>;
    updateAgent(podId: string, name: string, before: AgentDraft, after: AgentDraft): Promise<AgentDetail>;
    deleteAgent(podId: string, name: string): Promise<void>;
    /** The reader's chats with one agent, newest first. */
    agentChats(podId: string, agent: Pick<AgentRow, "name" | "front">): Promise<AgentChat[]>;
    /** A new chat with one agent (or the Lemma's own, `null`), the words
     *  sent in it; answers the chat's id. */
    talkTo(podId: string, agentName: string | null, text: string): Promise<string>;
    runtimes(orgId: string): Promise<Runtime[]>;
    runtime(orgId: string, runtimeId: string): Promise<Runtime | null>;
    surfaces(podId: string): Promise<Surface[]>;
    members(podId: string): Promise<Member[]>;

    /* ── the plan ───────────────────────────────────────────────────── */
    /** `/plan.md` as written, or null when there is none. Any other failure
     *  throws: a plan that could not be read is never a Lemma with no plan. */
    planText(podId: string): Promise<string | null>;
    writePlan(podId: string, text: string, exists: boolean): Promise<void>;
    /** The number a measure answers: its rows. */
    query(podId: string, sql: string): Promise<Record<string, unknown>[]>;
    /** The address an assistant (Claude, ChatGPT) connects to for this Lemma. */
    mcpUrl(podId: string): string | null;
}
