"use client";

import { useMemo, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { lemma } from "@/session/client";
import { useMe } from "@/session/use-me";
import { isForbidden } from "@/session/auth-state";
import { byNewest, readRun, readRuns, type RunRow } from "@/workflow/runs";
import { readShape, type WorkflowShape } from "@/workflow/shape";
import { Shape } from "@/workflow/workflows-view";
import { RunRowButton } from "@/workflow/run-row";
import { useWorkflowGraph, workflowGraphQuery, useWorkflowList } from "@/workflow/use-run";
import { automationOf, runsForOf, schedulesFor, turnOnOf, turnOnRequest, type Automation, type TurnOn } from "@/workflow/turn-on";
import { useSchedules } from "@/schedule/queries";
import { CADENCES, SCOPE_LABEL, agoOf, healthOf, humanizeName, type StandingJob } from "@/schedule/schedules";
import { ChevronLeftIcon, ClockIcon, PlayIcon, RefreshIcon, WorkflowIcon } from "@/ui/icons";
import { samples } from "@/data/samples";

/** One workflow, as a page: what it is and who it runs for, whether it is on
 *  — for you, or for the space — and every run it has made, each one a click
 *  from its own page. */
export function WorkflowPage({ pod, orgId, name, onBack, onOpenRun, onDiscuss, onConnect }: {
    pod: Pod;
    orgId: string | null;
    name: string;
    onBack: () => void;
    onOpenRun: (runId: string, label: string) => void;
    onDiscuss?: (name: string) => void;
    /** Open the organization's connectors, to connect an account first. */
    onConnect?: () => void;
}) {
    const sample = source.label === "sample";
    const flows = useWorkflowList(pod.id);
    const flow = flows.data?.find((one) => one.name === name) ?? null;
    const graph = useWorkflowGraph(pod.id, name);
    const cache = useQueryClient();
    /* Fetches the graph through the same query rather than waiting, disabled,
       for it: a disabled query stays pending forever, so a refused graph read
       left "How it runs" saying Reading… with no error and no Try again. */
    const shape = useQuery({
        queryKey: ["workflow-shape", pod.id, name],
        queryFn: async () => readShape((await cache.fetchQuery(workflowGraphQuery(pod.id, name))).raw ?? null),
        staleTime: 5 * 60_000,
    });
    /* Counted the way a run counts them, without the End, so this page and
       a run's "3 of 5 steps" agree; the list's node count until the steps
       arrive. */
    const steps = shape.data ? [...shape.data.ordered, ...shape.data.orphans].filter((step) => step.kind !== "END").length : flow?.steps ?? null;
    const raw = graph.data?.raw as { start?: unknown; mode?: string; description?: string | null } | null | undefined;
    const automation = automationOf(raw?.start);
    const perPerson = flow?.perPerson ?? raw?.mode === "USER";

    const [problem, setProblem] = useState<string | null>(null);
    const start = useMutation({
        mutationFn: async () => readRun(await lemma(pod.id).workflows.runs.create(name)),
        onSuccess: (run) => {
            void cache.invalidateQueries({ queryKey: ["workflow-runs", pod.id] });
            if (run) onOpenRun(run.id, name);
        },
        onError: (error) => setProblem(isForbidden(error) ? "You may not run this workflow." : error instanceof Error ? error.message : "Couldn’t start a run."),
    });

    return (
        <div className="wfpage">
            <div className="wfpage__inner">
                <nav className="runpage__crumb"><button onClick={onBack}><ChevronLeftIcon size={15} /> Workflows</button></nav>
                <header className="wfpage__head">
                    <span className="wfpage__tile"><WorkflowIcon size={26} /></span>
                    <div className="wfpage__who">
                        <h1>{humanizeName(name)}</h1>
                        <p>{flow?.description || raw?.description || "No description written."}</p>
                        <div className="agentpage__tags">
                            <span className="agentpage__tag" title={runsForOf(automation, perPerson)}>{automation.kind === "rows" ? "Each row’s owner" : perPerson ? "Each person" : "Admin"}</span>
                            {flow && !flow.active && <span className="agentpage__tag">Paused</span>}
                            {steps !== null && <span className="agentpage__tag">{steps} {steps === 1 ? "step" : "steps"}</span>}
                        </div>
                    </div>
                    <div className="agentpage__acts">
                        {onDiscuss && <button className="agentpage__btn" onClick={() => onDiscuss(name)}>Ask to change it</button>}
                        <button className="agentpage__btn agentpage__btn--primary" disabled={sample || start.isPending || (flow ? !flow.active : false)} onClick={() => start.mutate()}>
                            <PlayIcon size={14} weight="fill" /> {start.isPending ? "Starting…" : "Run now"}
                        </button>
                    </div>
                </header>
                {problem && <p className="runpage__problem" role="alert">{problem}</p>}

                <div className="agentpage__grid wfpage__grid">
                    <div className="agentpage__main">
                        <HowItStarts pod={pod} orgId={orgId} name={name} automation={automation} perPerson={perPerson} onConnect={onConnect} />
                        <Shape query={shape} teammate={pod.teammate?.name || pod.name} />
                    </div>
                    <aside className="wfpage__dock" aria-label="Runs">
                        <Runs pod={pod} name={name} names={stepNames(shape.data)} onOpenRun={onOpenRun} />
                    </aside>
                </div>
            </div>
        </div>
    );
}

/* ── is it on, and for whom ────────────────────────────────────────── */

function HowItStarts({ pod, orgId, name, automation, perPerson, onConnect }: {
    pod: Pod;
    orgId: string | null;
    name: string;
    automation: Automation;
    perPerson: boolean;
    onConnect?: () => void;
}) {
    const me = useMe();
    const schedules = useSchedules(pod.id);
    const jobs = useMemo(() => schedulesFor(schedules.data ?? [], name), [schedules.data, name]);
    const state = turnOnOf(automation, perPerson, jobs, me);
    const owner = (job: StandingJob) => (job.ownerId && job.ownerId === me ? "you" : pod.members.find((member) => member.userId === job.ownerId)?.name ?? "someone here");

    return (
        <section className="agentpage__card wfstart">
            <header className="wfstart__head">
                <h2>How it starts</h2>
                <span>{runsForOf(automation, perPerson)}</span>
            </header>
            <p className="wfstart__what">{whatStarts(automation)}</p>
            <StateLine state={state} owner={owner} />
            {(state.status === "off" || (state.status === "stopped" && !state.byYou && perPerson)) && (
                <TurnOnForm pod={pod} orgId={orgId} name={name} automation={automation} perPerson={perPerson} onConnect={onConnect} />
            )}
            {jobs.length > 0 && (
                <ul className="wfstart__jobs">
                    {jobs.map((job) => {
                        const health = healthOf(job);
                        return (
                            <li key={job.id} data-tone={health.tone}>
                                <ClockIcon size={14} />
                                <span>{job.trigger}</span>
                                <small>{SCOPE_LABEL[job.scope]} · {owner(job) === "you" ? "yours" : "by " + owner(job)}{job.lastFiredAt ? " · fired " + agoOf(job.lastFiredAt) : ""}</small>
                                {health.line && health.tone !== "ok" && <em>{health.line}</em>}
                            </li>
                        );
                    })}
                </ul>
            )}
            {perPerson && automation.kind !== "manual" && automation.kind !== "rows" && (
                <p className="wfstart__note">Other people’s personal switches are private to them, so they are not listed here.</p>
            )}
        </section>
    );
}

function whatStarts(automation: Automation): string {
    switch (automation.kind) {
        case "manual": return "Someone presses Run now.";
        case "time": return "A schedule, on the times you choose.";
        case "event": return "An event in " + (automation.connectorId || "a connected app") + (automation.triggerId ? " — " + automation.triggerId.replace(/_/g, " ") : "") + ".";
        case "rows": return "A row " + (automation.operations.length ? automation.operations.map((op) => op.toLowerCase() + "d").join(" or ").replace("insertd", "added").replace("updated", "changed").replace("deleted", "removed") : "changing") + " in " + (automation.table || "a table") + ".";
    }
}

function StateLine({ state, owner }: { state: TurnOn; owner: (job: StandingJob) => string }) {
    if (state.status === "manual") return null;
    if (state.status === "on") {
        return <p className="wfstart__state" data-tone="ok"><i />On{state.byYou ? " — you turned it on" : " — turned on by " + owner(state.job)}.</p>;
    }
    if (state.status === "stopped") {
        const health = healthOf(state.job);
        return <p className="wfstart__state" data-tone="warn"><i />{state.job.needsSetup || health.line || "Paused"}{state.byYou ? "" : " (set up by " + owner(state.job) + ")"}</p>;
    }
    return <p className="wfstart__state" data-tone="off"><i />{state.forYou ? "Not on for you yet." : "Off — nobody has turned it on."}</p>;
}

function TurnOnForm({ pod, orgId, name, automation, perPerson, onConnect }: {
    pod: Pod;
    orgId: string | null;
    name: string;
    automation: Automation;
    perPerson: boolean;
    onConnect?: () => void;
}) {
    const cache = useQueryClient();
    const sample = source.label === "sample";
    const [cron, setCron] = useState(CADENCES[0].cron);
    const [accountId, setAccountId] = useState("");
    const accounts = useQuery({
        queryKey: ["connector-accounts", orgId],
        enabled: automation.kind === "event" && Boolean(orgId) && !sample,
        queryFn: () => source.listAccounts(orgId!),
        staleTime: 60_000,
    });
    const usable = (accounts.data ?? []).filter((account) => account.usable && (automation.kind !== "event" || !automation.connectorId || account.connectorId === automation.connectorId));
    const chosen = accountId || usable[0]?.id || "";
    const request = turnOnRequest(name, automation, perPerson, { cron, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, accountId: chosen });
    const turnOn = useMutation({
        mutationFn: () => lemma(pod.id).request("POST", "/pods/" + pod.id + "/schedules", { body: request }),
        onSuccess: () => void cache.invalidateQueries({ queryKey: ["schedules", pod.id] }),
    });
    const verb = perPerson && automation.kind !== "rows" ? "Turn on for me" : "Turn on for everyone";

    return (
        <div className="wfstart__form">
            {automation.kind === "time" && (
                <label>
                    <span>When</span>
                    <select value={cron} onChange={(event) => setCron(event.target.value)}>
                        {CADENCES.map((one) => <option key={one.cron} value={one.cron}>{one.label}</option>)}
                    </select>
                </label>
            )}
            {automation.kind === "event" && (
                accounts.isPending && !sample ? <p className="wfstart__note">Looking for your {automation.connectorId} account…</p>
                    : usable.length === 0 ? (
                        <p className="wfstart__need">
                            It listens through your own {automation.connectorId || "app"} account, and you have none connected.
                            {onConnect && <button className="agentpage__link" onClick={onConnect}>Connect {automation.connectorId || "an account"}</button>}
                        </p>
                    ) : usable.length > 1 ? (
                        <label>
                            <span>Account</span>
                            <select value={chosen} onChange={(event) => setAccountId(event.target.value)}>
                                {usable.map((account) => <option key={account.id} value={account.id}>{account.label || account.ref || account.id}</option>)}
                            </select>
                        </label>
                    ) : <p className="wfstart__note">Using your {automation.connectorId} account {usable[0].label || usable[0].ref}.</p>
            )}
            <button className="agentpage__btn agentpage__btn--primary" disabled={sample || !request || turnOn.isPending} onClick={() => turnOn.mutate()}>
                {turnOn.isPending ? "Turning on…" : verb}
            </button>
            {sample && <p className="wfstart__note">Sign in to turn workflows on.</p>}
            {turnOn.isError && <p className="runpage__problem" role="alert">{turnOn.error instanceof Error ? turnOn.error.message : "Couldn’t turn it on."}</p>}
        </div>
    );
}

/* ── runs ──────────────────────────────────────────────────────────── */

/** Each step's label by its id, for the run rows beside the steps. */
function stepNames(shape: WorkflowShape | null | undefined): Map<string, string> {
    return new Map([...(shape?.ordered ?? []), ...(shape?.orphans ?? [])].filter((step) => step.label).map((step) => [step.id, step.label as string]));
}

function Runs({ pod, name, names, onOpenRun }: { pod: Pod; name: string; names: ReadonlyMap<string, string>; onOpenRun: (runId: string, label: string) => void }) {
    const runs = useInfiniteQuery({
        queryKey: ["workflow-runs", pod.id, name, "pages"],
        initialPageParam: undefined as string | undefined,
        queryFn: async ({ pageParam }): Promise<{ items: RunRow[]; next: string | null }> => {
            if (source.label === "sample") {
                const { SAMPLE_WORKFLOW_RUNS } = await samples(pod.id);
                return { items: byNewest(readRuns({ items: SAMPLE_WORKFLOW_RUNS[name] ?? [] })), next: null };
            }
            const page = await lemma(pod.id).workflows.runs.list(name, { limit: 20, pageToken: pageParam });
            return { items: byNewest(readRuns(page)), next: (page as { next_page_token?: string | null }).next_page_token ?? null };
        },
        getNextPageParam: (last) => last.next ?? undefined,
        staleTime: 15_000,
        refetchInterval: 20_000,
    });
    const all = runs.data?.pages.flatMap((page) => page.items) ?? [];
    return (
        <section className="wfdock">
            <header>
                <h2>Runs</h2>
                <span>{runs.isSuccess ? all.length + (runs.hasNextPage ? "+" : "") : ""}</span>
                <button className="runpage__icon" title="Refresh" aria-label="Refresh runs" onClick={() => void runs.refetch()}><RefreshIcon size={15} /></button>
            </header>
            {runs.isPending && <p className="runpage__note">Loading…</p>}
            {runs.isError && <p className="runpage__note" role="alert">{isForbidden(runs.error) ? "You may not read these runs." : "Couldn’t load runs."}</p>}
            {runs.isSuccess && all.length === 0 && <p className="runpage__note">It has never run.</p>}
            <div className="wfdock__list">
                {all.map((run) => <RunRowButton key={run.id} run={run} names={names} onOpen={() => onOpenRun(run.id, name)} />)}
            </div>
            {runs.hasNextPage && (
                <button className="chats-page__more" disabled={runs.isFetchingNextPage} onClick={() => void runs.fetchNextPage()}>
                    {runs.isFetchingNextPage ? "Loading…" : "Load more"}
                </button>
            )}
        </section>
    );
}
