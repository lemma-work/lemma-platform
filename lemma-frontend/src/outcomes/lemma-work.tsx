"use client";

import { useRef, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { connectLinkLabel, failureOf } from "@/data/pod-connectors";
import { isPodDefaultAgent } from "@/data/agent-names";
import { readableName } from "@/library/reading";
import { useRunNow, useScheduleActive, useSchedules } from "@/schedule/queries";
import { SCHEDULE_EDIT, agoOf, healthOf, may, type ScheduleRun, type StandingJob } from "@/schedule/schedules";
import { isForbidden } from "@/session/auth-state";
import { capabilityList } from "@/stage/colleagues";
import { byNewest, sayStatus, sayWhen, type RunRow, type WorkflowRow } from "@/workflow/runs";
import { SPACE_RUNS_PAGE, useSpaceRuns, useWorkflowList } from "@/workflow/use-run";
import type { FrameActions } from "./actions";
import { AutomationsTab } from "./automations";
import { AUTOMATION_SECTIONS, agentKey, agentView, chatView, outcomeView, type Go, type QueueItem, type View } from "./model";
import { Icon, to, type IconName } from "./parts";
import { Calm, Problem } from "./calm";
import { TryAgain, codeOf } from "./read-again";
import { SkeletonRows } from "@/ui/skeleton";

/* ── automations ──────────────────────────────────────────────────────── */

/** The old sub-tabs the one list now covers; their addresses land on it. */
const IN_THE_LIST = new Set<string>(["workflows", "schedules"]);

/** Ideas for the box, offered as chips under it: true of any Lemma, and a
 *  click fills the box rather than setting anything up. */
const EXAMPLES = [
    "Every Friday at 4, send me a summary of the week",
    "Each morning, tell me what needs me today",
    "When a file is added, read it and tell me what’s in it",
];

/** How many runs a row shows when it is open. */
const RUNS_SHOWN = 5;
/** A run older than this is not watched for an answer any more: a firing
 *  handed to an agent can stay "handed over" for good, and an open row
 *  should not ask the server about it every few seconds forever. */
const WATCH_FOR_MS = 10 * 60_000;
/** How often an open row looks again while one of its runs is moving. */
const WATCH_EVERY_MS = 4_000;

/** A Lemma's Automations page: everything it does on its own, in one list.
 *
 *  It used to be seven views behind a row of sub-tabs, which made "what do
 *  you do without me?" a question with seven places to look. Now each
 *  schedule and each workflow is one row that says how it is doing, opens in
 *  place on its runs, and can be run or paused right there, with a box above
 *  to describe a new one in words. The views the list does not cover -- what
 *  waits on you, every run -- keep their addresses and are linked quietly
 *  under it, so an old link still lands somewhere true. */
export function AutomationsPage({ pod, pods, section, queue, go, actions }: {
    pod: Pod;
    pods: readonly Pod[];
    section: string | null;
    queue: readonly QueueItem[];
    go: Go;
    actions: FrameActions;
}) {
    if (section === "agents") return <AgentList pod={pod} go={go} />;
    if (section && !IN_THE_LIST.has(section) && AUTOMATION_SECTIONS.some((one) => one === section)) {
        return <AutomationsTab pod={pod} pods={pods} section={section} queue={queue} go={go} actions={actions} />;
    }
    return <Automations pod={pod} queue={queue} go={go} actions={actions} />;
}

/** The list itself, under the box that adds to it. Schedules and workflows
 *  load separately, so one failing still shows the other, each with its own
 *  way to try again. */
function Automations({ pod, queue, go, actions }: { pod: Pod; queue: readonly QueueItem[]; go: Go; actions: FrameActions }) {
    const schedules = useSchedules(pod.id);
    const workflows = useWorkflowList(pod.id);
    const runs = useSpaceRuns(pod.id);
    const [open, setOpen] = useState<string | null>(null);
    const waiting = queue.filter((item) => item.wait).length;
    const jobs = schedules.data ?? [];
    const flows = workflows.data ?? [];
    const toggle = (key: string) => setOpen((was) => (was === key ? null : key));
    const reading = schedules.isPending || workflows.isPending;
    const none = schedules.isSuccess && workflows.isSuccess && jobs.length === 0 && flows.length === 0;
    /* Neither list came through: the page says so once, not twice. */
    const unread = schedules.isError && workflows.isError && !isForbidden(schedules.error) && !isForbidden(workflows.error);

    return (
        <div className="ox-work">
            <Describe onSend={(text) => actions.ask(pod.id, "Set up an automation: " + text)} />

            {none ? (
                <p className="ox-work__empty">Nothing runs on its own yet. Tell me what to do and when, and I’ll set it up.</p>
            ) : unread ? (
                <Calm
                    tone="warn"
                    title={`Couldn’t read ${pod.name}’s automations`}
                    note={codeOf(schedules.error)}
                    actions={<TryAgain busy={schedules.isFetching || workflows.isFetching} onClick={() => { void schedules.refetch(); void workflows.refetch(); }} />}
                >
                    What it runs on its own carries on. The list of it didn’t come through just now.
                </Calm>
            ) : reading && jobs.length === 0 && flows.length === 0 && !schedules.isError && !workflows.isError ? (
                <SkeletonRows label={`Reading what ${pod.name} runs`} rows={4} slow={`Still reading what ${pod.name} runs. It’s slower than usual.`} />
            ) : (
                <>
                <ul className="ox-work__list" aria-busy={reading || undefined}>
                    {jobs.map((job, at) => {
                        const key = "s:" + (job.id || "unreadable-" + at);
                        return <ScheduleRow key={key} pod={pod} job={job} open={open === key} onOpen={() => toggle(key)} go={go} />;
                    })}
                    {flows.map((flow) => {
                        const key = "w:" + flow.id;
                        return (
                            <WorkflowItem
                                key={key}
                                pod={pod}
                                flow={flow}
                                runs={runs.data}
                                runsFailed={runs.isError}
                                runsPending={runs.isPending}
                                onRetryRuns={() => void runs.refetch()}
                                open={open === key}
                                onOpen={() => toggle(key)}
                                go={go}
                            />
                        );
                    })}
                    {reading && <li className="ox-work__quiet" role="status">Reading the rest…</li>}
                </ul>
                {schedules.isError && (
                    <div className="ox-work__fail">
                        {isForbidden(schedules.error)
                            ? <p className="ox-work__note">You can’t see {pod.name}’s schedules.</p>
                            : <Problem retrying={schedules.isFetching} onRetry={() => void schedules.refetch()}>Couldn’t read {pod.name}’s schedules.</Problem>}
                    </div>
                )}
                {workflows.isError && (
                    <div className="ox-work__fail">
                        {isForbidden(workflows.error)
                            ? <p className="ox-work__note">You can’t see {pod.name}’s workflows.</p>
                            : <Problem retrying={workflows.isFetching} onRetry={() => void workflows.refetch()}>Couldn’t read {pod.name}’s workflows.</Problem>}
                    </div>
                )}
                </>
            )}

            <nav className="ox-work__links" aria-label="More about what I run">
                <a className="ox-link" {...to(outcomeView(pod.id, "automations", "waiting"), go)}>
                    Waiting on you{waiting > 0 && <span className="ox-num"> {waiting}</span>}
                </a>
                <a className="ox-link" {...to(outcomeView(pod.id, "automations", "history"), go)}>All runs</a>
            </nav>
        </div>
    );
}

/** A new automation is described, not configured: the words go to the
 *  Lemma as a chat, and it asks whatever it needs to set the thing up. The
 *  examples fill the box so there is a path with no typing at all, and
 *  nothing is drawn in the list until the Lemma has really made it. */
function Describe({ onSend }: { onSend: (text: string) => void }) {
    const [text, setText] = useState("");
    const box = useRef<HTMLInputElement>(null);
    const said = text.trim();
    const submit = (event: FormEvent) => {
        event.preventDefault();
        if (said) onSend(said);
    };
    return (
        <div className="ox-work__describe">
            <form className="ox-work__teach" onSubmit={submit}>
                <input
                    ref={box}
                    value={text}
                    onChange={(event) => setText(event.target.value)}
                    placeholder="Tell me what to do, and when"
                    aria-label="Tell me what to do, and when"
                    autoComplete="off"
                />
                <button type="submit" className="ox-btn ox-btn--primary" disabled={!said}>Set it up</button>
            </form>
            <div className="ox-work__eg" aria-label="Examples">
                {EXAMPLES.map((example) => (
                    <button
                        key={example}
                        type="button"
                        className="ox-work__chip"
                        aria-pressed={said === example}
                        onClick={() => {
                            setText(example);
                            box.current?.focus();
                        }}
                    >
                        {example}
                    </button>
                ))}
            </div>
        </div>
    );
}

type Tone = "ok" | "off" | "bad" | "warn" | "busy";

/** The firing statuses where the schedule itself is still at work: queued
 *  or starting its target. Once handed over, the target has it. */
const STARTING = new Set(["RECEIVED", "PROCESSING"]);
/** Every status that can still change, so an open row keeps looking. */
const MOVING = new Set(["RECEIVED", "PROCESSING", "DISPATCHED"]);

function recent(run: Pick<ScheduleRun, "at" | "startedAt">): boolean {
    const at = Date.parse(run.startedAt || run.at);
    return Number.isNaN(at) || Date.now() - at < WATCH_FOR_MS;
}

/** How a schedule is doing, short enough for its row. The long reason, where
 *  there is one, waits in the row's open part (`healthOf`). */
function scheduleState(job: StandingJob, busy: boolean): { tone: Tone; last: string } {
    if (busy) return { tone: "busy", last: "Running now…" };
    if (job.pausedByFailures) return { tone: "bad", last: `Stopped after ${job.failures} failures` };
    if (!job.active && job.lastError.startsWith("Turned off:")) return { tone: "bad", last: "Turned off" };
    if (!job.active) return { tone: "off", last: "Paused" };
    if (job.target.kind === "none") return { tone: "bad", last: "Has nothing to run" };
    if (job.needsSetup) return { tone: "warn", last: "Needs setting up" };
    if (job.listening?.state === "lapsed") return { tone: "bad", last: "Stopped hearing events" };
    if (job.failures > 0 || job.lastFireStatus === "ERROR") {
        return { tone: "bad", last: job.lastFiredAt ? "Failed " + agoOf(job.lastFiredAt) : "Failed last time" };
    }
    if (!job.lastFiredAt) return { tone: "ok", last: "Not run yet" };
    if (job.lastFireStatus === "FILTERED") return { tone: "ok", last: "Skipped " + agoOf(job.lastFiredAt) };
    return { tone: "ok", last: "Ran " + agoOf(job.lastFiredAt) };
}

/** Who does the work when it fires, said short for the row. The Lemma's own
 *  agent is the Lemma, so it says "Me". */
function doerOf(job: StandingJob): { icon: IconName; word: string } | null {
    if (job.target.kind === "none") return null;
    if (job.target.kind === "agent" && isPodDefaultAgent(job.target.name)) return { icon: "agent", word: "Me" };
    return { icon: job.target.kind === "workflow" ? "flow" : "agent", word: job.target.label };
}

/** The same, as a sentence in the Lemma's voice, for the open row. */
function doerSentence(job: StandingJob): string {
    if (job.target.kind === "none") return "Its agent or workflow was removed, so there is nothing to run.";
    if (job.target.kind === "agent" && isPodDefaultAgent(job.target.name)) return "I do this myself.";
    if (job.target.kind === "workflow") return `I run ${job.target.name ? job.target.label : "a workflow"}, step by step.`;
    return `I hand it to ${job.target.name ? job.target.label : "an agent"}.`;
}

/** Where one firing's own work can be seen: a workflow run's page, or the
 *  chat an agent ran in. */
function firingView(podId: string, run: ScheduleRun): View | null {
    if (!run.targetRunId) return null;
    if (run.targetKind === "workflow") return { name: "run", podId, runId: run.targetRunId };
    if (run.targetKind === "agent") return chatView({ kind: "chat", podId, id: run.targetRunId });
    return null;
}

/** One schedule: what it is, when it fires, who does it and how it went
 *  last, with its switch at the end. Opened, its recent runs and Run now.
 *
 *  Each row holds its own mutations, so pausing one does not put every
 *  other row's controls into a pending state. Run now starts a sweep along
 *  the row's foot, and the run it started lands at the top of the runs
 *  straight from the answer, before the list is read again; the row keeps
 *  looking until that run is handed over, so the sweep stops when the work
 *  has really moved on, not on a timer. Where Run now cannot work for this
 *  reader or this schedule, the row says why instead of offering it. */
function ScheduleRow({ pod, job, open, onOpen, go }: { pod: Pod; job: StandingJob; open: boolean; onOpen: () => void; go: Go }) {
    const active = useScheduleActive(pod.id);
    const runNow = useRunNow(pod.id, job.id);
    const fresh = runNow.data ?? null;
    /* The same cache entry the workspace's schedule list reads, watched here
       while something in it is moving. */
    const runs = useQuery({
        queryKey: ["schedules", pod.id, job.id, "runs", "fired"],
        queryFn: () => source.listScheduleRuns(pod.id, job.id, { skipped: false }),
        enabled: Boolean(job.id) && (open || fresh !== null),
        staleTime: 15_000,
        refetchInterval: (query) => (query.state.data?.slice(0, 3).some((run) => MOVING.has(run.status) && recent(run)) ? WATCH_EVERY_MS : false),
    });
    const listed = runs.data ?? [];
    const shown = fresh && !listed.some((run) => run.id === fresh.id) ? [fresh, ...listed] : listed;
    const freshNow = fresh ? shown.find((run) => run.id === fresh.id) ?? fresh : null;
    const busy = runNow.isPending || Boolean(freshNow && STARTING.has(freshNow.status) && recent(freshNow));

    if (job.broken) {
        return (
            <li className="ox-work__row">
                <p className="ox-work__quiet">One of these didn’t arrive whole, so I can’t say what it does or how it’s going.</p>
            </li>
        );
    }

    const mayEdit = may(job, SCHEDULE_EDIT);
    const on = active.isPending && active.variables ? active.variables.active : job.active;
    const state = scheduleState({ ...job, active: on }, busy);
    const health = healthOf(job);
    const doer = doerOf(job);
    const owner = pod.members.find((member) => member.userId && member.userId === job.ownerId)?.name ?? null;
    const notYours = `You can see this one, not change it. Ask ${owner ?? "whoever set it up"}.`;
    /* Why Run now is not on offer, in the order the server would refuse. */
    const cannotRun = job.kind !== "TIME"
        ? "It starts when something happens, so there’s nothing to start it with by hand."
        : job.target.kind === "none" ? "There’s nothing to run until it has an agent or workflow again."
        : !mayEdit ? notYours
        : !on ? "Paused. Turn it on to run it now."
        : null;
    const panel = "ox-work-" + job.id;

    return (
        <li className="ox-work__row" data-open={open || undefined} data-busy={busy || undefined}>
            <div className="ox-work__head">
                <button type="button" className="ox-work__open" aria-expanded={open} aria-controls={open ? panel : undefined} onClick={onOpen}>
                    <span className="ox-work__dot" data-tone={state.tone} aria-hidden="true" />
                    <span className="ox-work__name">
                        <span className="ox-work__t">{job.title}</span>
                        <span className="ox-work__when">{job.trigger}</span>
                    </span>
                    <span className="ox-work__does">{doer && <><Icon name={doer.icon} size={13} /><span>{doer.word}</span></>}</span>
                    <span className="ox-work__last" data-tone={state.tone}>{state.last}</span>
                </button>
                <button
                    type="button"
                    role="switch"
                    className="ox-work__switch"
                    aria-checked={on}
                    aria-label={`${job.title}: ${on ? "on" : "paused"}`}
                    title={mayEdit ? (on ? "Pause it" : "Turn it on") : notYours}
                    disabled={!mayEdit || active.isPending}
                    onClick={() => active.mutate({ id: job.id, active: !on })}
                >
                    <i />
                </button>
            </div>
            <span className="ox-work__sweep" aria-hidden="true" />
            {active.isError && <Failed error={active.error} fallback={on ? "Couldn’t pause it." : "Couldn’t turn it on."} retrying={active.isPending} onRetry={() => active.mutate({ id: job.id, active: !on })} />}

            {open && (
                <div className="ox-work__more" id={panel}>
                    <p className="ox-work__what">{doerSentence(job)}</p>
                    {job.instruction && <p className="ox-work__said">{job.instruction}</p>}
                    {(health.tone === "bad" || (health.tone === "warn" && job.lastFiredAt)) && (
                        <p className="ox-work__health" data-tone={health.tone}>{health.line}</p>
                    )}

                    {runs.isPending && !fresh ? <p className="ox-work__note" role="status">Reading its runs…</p>
                        : runs.isError && shown.length === 0 ? (
                            isForbidden(runs.error)
                                ? <p className="ox-work__note">You can’t see its runs.</p>
                                : <Problem retrying={runs.isFetching} onRetry={() => void runs.refetch()}>Couldn’t read its runs.</Problem>
                        )
                        : shown.length === 0 ? <p className="ox-work__note">No runs yet.</p>
                        : (
                            <ol className="ox-work__runs" aria-label="Recent runs">
                                {shown.slice(0, RUNS_SHOWN).map((run, at) => {
                                    const view = firingView(pod.id, run);
                                    const why = run.status === "FILTERED" && !run.error ? run.judgement : run.error;
                                    const body = (
                                        <>
                                            <span className="ox-work__at ox-num">{agoOf(run.at) || (run.id === fresh?.id ? "just now" : "")}</span>
                                            <span className="ox-work__out">{run.outcome}{why && <span className="ox-work__why"> · {why}</span>}</span>
                                            {view && <Icon name="next" size={13} />}
                                        </>
                                    );
                                    return (
                                        <li key={run.id || "unreadable-" + at} data-tone={MOVING.has(run.status) ? "busy" : run.tone} data-fresh={run.id === fresh?.id || undefined}>
                                            {view ? <a className="ox-work__run" {...to(view, go)}>{body}</a> : <div className="ox-work__run">{body}</div>}
                                        </li>
                                    );
                                })}
                            </ol>
                        )}

                    <div className="ox-work__acts">
                        {cannotRun ? <span className="ox-work__cannot">{cannotRun}</span> : (
                            <button type="button" className="ox-btn ox-btn--small" disabled={busy} onClick={() => runNow.mutate()}>
                                {runNow.isPending ? "Starting…" : busy ? "Running…" : "Run now"}
                            </button>
                        )}
                    </div>
                    {runNow.isError && <Failed error={runNow.error} fallback="Couldn’t start it." retrying={runNow.isPending} onRetry={() => runNow.mutate()} />}
                </div>
            )}
        </li>
    );
}

/** A workflow's newest run in the space's recent runs, and whether "none"
 *  can be said: the read is one page of the newest, so a workflow missing
 *  from a full page may simply have run longer ago. */
function lastOf(flow: WorkflowRow, runs: readonly RunRow[] | undefined): { mine: RunRow[]; sure: boolean } {
    const mine = byNewest((runs ?? []).filter((run) => run.workflowId === flow.id));
    return { mine, sure: runs !== undefined && (mine.length > 0 || runs.length < SPACE_RUNS_PAGE) };
}

/** A workflow, which runs step by step when something starts it: no switch
 *  and no Run now here, because both belong to its own page, which the open
 *  row links to with its recent runs. */
function WorkflowItem({ pod, flow, runs, runsFailed, runsPending, onRetryRuns, open, onOpen, go }: {
    pod: Pod;
    flow: WorkflowRow;
    runs: readonly RunRow[] | undefined;
    runsFailed: boolean;
    runsPending: boolean;
    onRetryRuns: () => void;
    open: boolean;
    onOpen: () => void;
    go: Go;
}) {
    const { mine, sure } = lastOf(flow, runs);
    const latest = mine[0] ?? null;
    const when = latest ? sayWhen(latest.startedAt ?? latest.createdAt) : null;
    const state: { tone: Tone; last: string } = !flow.active ? { tone: "off", last: "Off" }
        : !latest ? { tone: "ok", last: sure ? "Not run yet" : "" }
        : latest.status === "RUNNING" || latest.status === "PENDING" ? { tone: "busy", last: sayStatus(latest.status, latest.waitingOn?.type) }
        : latest.status === "WAITING" ? { tone: "warn", last: "Waiting on a person" }
        : latest.status === "FAILED" ? { tone: "bad", last: "Failed" + (when ? " " + when : "") }
        : { tone: "ok", last: (latest.status === "COMPLETED" ? "Ran" : sayStatus(latest.status)) + (when ? " " + when : "") };
    const page: View = { name: "workflow", podId: pod.id, workflow: flow.name };
    const panel = "ox-work-w-" + flow.id;

    return (
        <li className="ox-work__row" data-open={open || undefined} data-busy={state.tone === "busy" || undefined}>
            <div className="ox-work__head">
                <button type="button" className="ox-work__open" aria-expanded={open} aria-controls={open ? panel : undefined} onClick={onOpen}>
                    <span className="ox-work__dot" data-tone={state.tone} aria-hidden="true" />
                    <span className="ox-work__name">
                        <span className="ox-work__t">{readableName(flow.name)}</span>
                        <span className="ox-work__when">Step by step</span>
                    </span>
                    <span className="ox-work__does"><Icon name="flow" size={13} /><span>{flow.steps} {flow.steps === 1 ? "step" : "steps"}</span></span>
                    <span className="ox-work__last" data-tone={state.tone}>{state.last}</span>
                </button>
                <span className="ox-work__gap" aria-hidden="true" />
            </div>
            <span className="ox-work__sweep" aria-hidden="true" />

            {open && (
                <div className="ox-work__more" id={panel}>
                    {flow.description && <p className="ox-work__what">{flow.description}</p>}
                    {runsPending ? <p className="ox-work__note" role="status">Reading its runs…</p>
                        : runsFailed ? (
                            <Problem onRetry={onRetryRuns}>Couldn’t read its runs.</Problem>
                        )
                        : mine.length === 0 ? <p className="ox-work__note">{sure ? "No runs yet." : "No runs lately."}</p>
                        : (
                            <ol className="ox-work__runs" aria-label="Recent runs">
                                {mine.slice(0, RUNS_SHOWN).map((run) => (
                                    <li key={run.id} data-tone={workflowTone(run.status)}>
                                        <a className="ox-work__run" {...to({ name: "run", podId: pod.id, runId: run.id }, go)}>
                                            <span className="ox-work__at ox-num">{sayWhen(run.startedAt ?? run.createdAt) ?? ""}</span>
                                            <span className="ox-work__out">
                                                {sayStatus(run.status, run.waitingOn?.type)}
                                                {run.title && <span className="ox-work__why"> · {run.title}</span>}
                                            </span>
                                            <Icon name="next" size={13} />
                                        </a>
                                    </li>
                                ))}
                            </ol>
                        )}
                    <div className="ox-work__acts">
                        <a className="ox-btn ox-btn--small" {...to(page, go)}>Open it</a>
                        {!flow.active && <span className="ox-work__cannot">It’s off, so nothing starts it.</span>}
                    </div>
                </div>
            )}
        </li>
    );
}

function workflowTone(status: string): Tone {
    if (status === "RUNNING" || status === "PENDING") return "busy";
    if (status === "WAITING") return "warn";
    if (status === "FAILED") return "bad";
    if (status === "CANCELLED") return "off";
    return "ok";
}

/* ── agents ───────────────────────────────────────────────────────────── */

/** How many of what an agent uses go on its card. */
const USES_SHOWN = 4;

/** A Lemma's Agents page: the helpers it hands work to, a card each.
 *
 *  People talk to the Lemma, never to these, so the page opens by saying so
 *  in its voice and then shows each helper by what it is for and what it
 *  can reach -- the things a person checks before trusting it with work.
 *  The agent that answers as the Lemma is left out: it is the Lemma, whose
 *  page this already is. */
export function AgentsPage({ pod, go }: { pod: Pod; go: Go; actions: FrameActions }) {
    return <AgentList pod={pod} go={go} />;
}

function AgentList({ pod, go }: { pod: Pod; go: Go }) {
    const agents = useQuery({ queryKey: ["outcome-agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 5 * 60_000 });
    const rows = (agents.data ?? []).filter((agent) => !agent.front);

    if (agents.isPending) return <SkeletonRows label={`Reading ${pod.name}’s agents`} rows={3} slow={`Still reading ${pod.name}’s agents. It’s slower than usual.`} />;
    if (agents.isError && isForbidden(agents.error)) {
        return <Calm icon="lock" title={`You can’t see ${pod.name}’s agents`}>An admin of {pod.name} can show you who it hands work to.</Calm>;
    }
    if (agents.isError) {
        return (
            <Calm tone="warn" title={`Couldn’t read ${pod.name}’s agents`} note={codeOf(agents.error)} actions={<TryAgain busy={agents.isFetching} onClick={() => void agents.refetch()} />}>
                Nothing about them changed. The list of them didn’t come through just now.
            </Calm>
        );
    }
    if (rows.length === 0) {
        return <p className="ox-work__empty">I do all the work myself for now. A plugin can bring helpers I hand work to.</p>;
    }
    return (
        <div className="ox-work">
            <p className="ox-work__voice">
                <Icon name="chat" size={14} />
                You talk to me, and I hand {rows.length === 1 ? "this one" : `these ${rows.length}`} the parts {rows.length === 1 ? "it’s" : "they’re"} for.
            </p>
            <ul className="ox-work__agents">
                {rows.map((agent, at) => {
                    const view = agentView(pod.id, agent);
                    const uses = capabilityList(agent.toolsets).filter((one) => !one.plain);
                    const body = (
                        <>
                            <span className="ox-work__face"><Icon name="agent" size={19} /></span>
                            <span className="ox-work__who">
                                <span className="ox-work__t">{agent.label}</span>
                                <span className="ox-work__blurb">{agent.blurb || "Nothing written yet about what it’s for."}</span>
                            </span>
                            {uses.length > 0 && (
                                <span className="ox-work__uses">
                                    <span className="ox-work__uses-k">Uses</span>
                                    {uses.slice(0, USES_SHOWN).map((one) => <span key={one.code} className="ox-work__use" title={one.says}>{one.word}</span>)}
                                    {uses.length > USES_SHOWN && <span className="ox-work__uses-k ox-num">+{uses.length - USES_SHOWN}</span>}
                                </span>
                            )}
                            <span className="ox-work__go">
                                {view ? <>How it works<Icon name="next" size={13} /></> : "This one didn’t arrive whole, so it can’t be opened."}
                            </span>
                        </>
                    );
                    return (
                        <li key={agentKey(agent, at)}>
                            {view ? <a className="ox-work__agent" {...to(view, go)}>{body}</a> : <div className="ox-work__agent">{body}</div>}
                        </li>
                    );
                })}
            </ul>
        </div>
    );
}

/** An action that failed, said where it happened with Try again beside it:
 *  the server's words where it gave some, and the link that fixes it where
 *  there is one. */
function Failed({ error, fallback, retrying, onRetry }: { error: unknown; fallback: string; retrying: boolean; onRetry: () => void }) {
    const said = failureOf(error, fallback);
    return (
        <div className="ox-work__problem">
            <Problem retrying={retrying} onRetry={onRetry}>
                {said.message}
                {said.connectUrl && <>{" "}<a className="linkish" href={said.connectUrl}>{connectLinkLabel(said.code)}</a></>}
            </Problem>
        </div>
    );
}
