"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { isForbidden } from "@/session/auth-state";
import { gather } from "@/workflow/waiting-inbox";
import { byNewest, runTone, sayStuckFor, sayWaitingOn, sayWhen, stillGoing, type RunRow, type WorkflowRow } from "@/workflow/runs";
import { RunRowButton } from "@/workflow/run-row";
import { useSpaceRuns, useWorkflowList } from "@/workflow/use-run";
import { useSchedules } from "@/schedule/queries";
import { ChevronRightIcon, WorkflowIcon } from "@/ui/icons";
import { emptyFor } from "./empty-copy";
import { SpaceEmpty } from "./empty-state";
import { guideTitle } from "@/tour/guides";
import { humanizeName } from "@/schedule/schedules";

type View = "workflows" | "waiting" | "running" | "recent";

/** A space's workflows, and everything they have been doing.
 *
 *  Four views of one set of facts, each answering its own question: what is
 *  there; what is waiting on me; what is going right now; what happened.
 *  Every run in every view opens its own page — the list is never a dead end
 *  that only leads back to the workflow. */
export function WorkflowsPage({ pod, pods, onOpenWorkflow, onOpenRun, onAsk, onLearn }: {
    pod: Pod;
    pods: Pod[];
    onOpenWorkflow: (name: string) => void;
    onOpenRun: (runId: string, label: string) => void;
    /** Put words for the teammate in the chat box, to finish or send. */
    onAsk?: (text: string) => void;
    /** Open the guide to workflows. */
    onLearn?: () => void;
}) {
    const [view, setView] = useState<View>("workflows");
    const flows = useWorkflowList(pod.id);
    const runs = useSpaceRuns(pod.id);
    const schedules = useSchedules(pod.id);
    const agents = useQuery({ queryKey: ["agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 5 * 60_000 });
    const waiting = useQuery({
        queryKey: ["workflow-waiting", pods.map((each) => each.id).join(",")],
        queryFn: () => gather(pods, source.label === "sample"),
        enabled: pods.length > 0,
        staleTime: 30_000,
    });

    const nameOf = useMemo(() => new Map((flows.data ?? []).map((flow) => [flow.id, flow.name])), [flows.data]);
    const all = useMemo(() => byNewest(runs.data ?? []), [runs.data]);
    const owed = (waiting.data?.rows ?? []).filter((row) => row.podId === pod.id);
    const owedRuns = new Set(owed.map((row) => row.run.id));
    const running = all.filter((run) => stillGoing(run.status) && !owedRuns.has(run.id));
    const recent = all.filter((run) => !stillGoing(run.status));
    const byFlow = useMemo(() => {
        const out = new Map<string, RunRow[]>();
        for (const run of all) if (run.workflowId) out.set(run.workflowId, [...(out.get(run.workflowId) ?? []), run]);
        return out;
    }, [all]);
    const agentNames = agents.data?.map((agent) => agent.name);

    const tabs: { id: View; label: string; count: number | null }[] = [
        { id: "workflows", label: "Workflows", count: flows.data?.length ?? null },
        { id: "waiting", label: "Waiting on you", count: waiting.isSuccess ? owed.length : null },
        { id: "running", label: "Running", count: runs.isSuccess ? running.length : null },
        { id: "recent", label: "Recent runs", count: null },
    ];

    return (
        <div className="all workflows-page">
            <header className="all__head">
                <h1>Workflows</h1>
            </header>
            <p className="all__scope-note workflows-page__note">Steps that run in order, wait for people where they have to, and pick up again.</p>
            <div className="all__tabs wfindex__tabs" role="tablist" aria-label="Workflows">
                {tabs.map((tab) => (
                    <button key={tab.id} role="tab" aria-selected={view === tab.id} onClick={() => setView(tab.id)}>
                        {tab.label}
                        {tab.count !== null && tab.count > 0 && <span className="wfindex__count" data-hot={tab.id === "waiting" || undefined}>{tab.count}</span>}
                    </button>
                ))}
            </div>

            {view === "workflows" && (
                <>
                    {flows.isPending && <p className="all__empty">Loading…</p>}
                    {flows.isError && <p className="all__empty">{isForbidden(flows.error) ? "You may not list the workflows here." : "Couldn’t load workflows."}</p>}
                    {flows.isSuccess && flows.data.length === 0 && (
                        <SpaceEmpty empty={emptyFor({ place: "workflows" }, pod.teammate?.name || pod.name)} on={{ ask: onAsk }}
                            learn={onLearn ? { label: guideTitle("workflows"), onOpen: onLearn } : undefined} />
                    )}
                    <ul className="wfindex">
                        {(flows.data ?? []).map((flow) => (
                            <FlowRow key={flow.id} flow={flow} runs={byFlow.get(flow.id) ?? []}
                                schedules={(schedules.data ?? []).filter((job) => job.target.kind === "workflow" && job.target.name === flow.name && job.active).length}
                                agentNames={agentNames} onOpen={() => onOpenWorkflow(flow.name)} />
                        ))}
                    </ul>
                </>
            )}

            {view === "waiting" && (
                <div className="wfindex__runs">
                    {waiting.isPending && <p className="all__empty">Checking…</p>}
                    {waiting.isSuccess && owed.length === 0 && <p className="all__empty">Nothing is waiting on you.</p>}
                    {owed.map((row) => (
                        <button key={row.wait.id} className="runrow" onClick={() => onOpenRun(row.run.id, row.workflowName)}>
                            <span className="runrow__dot" data-tone="waiting" aria-hidden="true" />
                            <span className="runrow__body">
                                <span className="runrow__title"><b>{row.workflowName}</b><em data-tone="waiting">{sayWaitingOn(row.wait.type)}</em></span>
                                <small>At {row.wait.nodeId}</small>
                            </span>
                            <span className="runrow__when">{sayStuckFor(row.wait, row.run)}</span>
                            <ChevronRightIcon size={15} />
                        </button>
                    ))}
                </div>
            )}

            {(view === "running" || view === "recent") && (
                <div className="wfindex__runs">
                    {runs.isPending && <p className="all__empty">Loading…</p>}
                    {runs.isError && <p className="all__empty">{isForbidden(runs.error) ? "You may not read runs here." : "Couldn’t load runs."}</p>}
                    {runs.isSuccess && (view === "running" ? running : recent).length === 0 && (
                        <p className="all__empty">{view === "running" ? "Nothing is running." : "Nothing has run yet."}</p>
                    )}
                    {(view === "running" ? running : recent).map((run) => {
                        const workflow = (run.workflowId && nameOf.get(run.workflowId)) || "Workflow";
                        return <RunRowButton key={run.id} run={run} workflow={workflow} onOpen={() => onOpenRun(run.id, workflow)} />;
                    })}
                </div>
            )}
        </div>
    );
}

function FlowRow({ flow, runs, schedules, agentNames, onOpen }: {
    flow: WorkflowRow;
    runs: RunRow[];
    schedules: number;
    agentNames?: string[];
    onOpen: () => void;
}) {
    const last = runs[0] ?? null;
    const missing = agentNames
        ? flow.targets.filter((one) => one.startsWith("agent:")).map((one) => one.slice(6)).filter((agent) => agent && agent !== "POD_DEFAULT" && !agentNames.includes(agent))
        : [];
    return (
        <li>
            <button className="wfindex__row" onClick={onOpen}>
                <span className="wfindex__tile"><WorkflowIcon size={18} /></span>
                <span className="wfindex__body">
                    <span className="wfindex__title">
                        <b>{humanizeName(flow.name)}</b>
                        <em title={flow.perPerson ? "Runs separately for each person, as them" : "Runs once, for everyone here"}>{flow.perPerson ? "Each person" : "Admin"}</em>
                        {!flow.active && <em>Paused</em>}
                        {missing.length > 0 && <em data-warn title={"Hands work to " + missing.join(", ") + ", which is not here"}>Needs setup</em>}
                    </span>
                    <small>{flow.description || flow.steps + (flow.steps === 1 ? " step" : " steps")}</small>
                </span>
                <span className="wfindex__meta">
                    {last ? (
                        <span className="wfindex__last">
                            <i data-tone={runTone(last.status)} />
                            Last run {sayWhen(last.startedAt ?? last.createdAt) ?? "—"}
                        </span>
                    ) : <span className="wfindex__last">Never run</span>}
                    <small>{schedules > 0 ? schedules + (schedules === 1 ? " schedule" : " schedules") + " on" : runs.length > 0 ? runs.length + " recent runs" : ""}</small>
                </span>
                <ChevronRightIcon size={16} />
            </button>
        </li>
    );
}
