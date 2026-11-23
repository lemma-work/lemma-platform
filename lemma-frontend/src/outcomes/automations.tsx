"use client";

import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { source, type AgentRow, type Pod } from "@/data";
import { readableName } from "@/library/reading";
import { StandingWork } from "@/schedule/standing-work";
import { WorkflowsPage, type WorkflowsView } from "@/space/workflows-page";
import { platformName } from "@/thread/conversation-origin";
import { useWorkflowList } from "@/workflow/use-run";
import type { FrameActions } from "./actions";
import { AUTOMATION_SECTIONS, agentKey, agentView, ageOf, chatView, outcomeView, type AutomationSection, type Go, type QueueItem } from "./model";
import { Icon, to, type IconName } from "./parts";
import { Calm, Problem } from "./calm";
import { TryAgain, codeOf } from "./read-again";
import { SkeletonRows } from "@/ui/skeleton";

const LABELS: Record<AutomationSection, string> = {
    workflows: "Workflows",
    waiting: "Waiting on you",
    running: "Running",
    history: "All runs",
    schedules: "Schedules",
    agents: "Agents",
    xray: "Map",
};

/** The four views the workspace's Workflows page keeps, by this tab's names. */
const WORKFLOW_VIEWS: Partial<Record<AutomationSection, WorkflowsView>> = {
    workflows: "workflows",
    waiting: "waiting",
    running: "running",
    history: "recent",
};

/** One view of what a Lemma runs, at the address it has always had: what
 *  waits on you, what is running, every run, the map. The Automations page
 *  itself is one list now (`lemma-work.tsx`) and links here quietly, so
 *  these views no longer sit behind a row of sub-tabs; each leads back to
 *  the list instead. Each view is the workspace's own page, so a run, a
 *  schedule's health and an agent's settings read the same everywhere. */
export function AutomationsTab({ pod, pods, section, queue, go, actions }: {
    pod: Pod;
    pods: readonly Pod[];
    section: string | null;
    /** This Lemma's queue, for how many of its runs wait on you. */
    queue: readonly QueueItem[];
    go: Go;
    actions: FrameActions;
}) {
    const shown: AutomationSection = AUTOMATION_SECTIONS.find((each) => each === section) ?? "workflows";
    const waiting = queue.filter((item) => item.wait).length;
    const workflowView = WORKFLOW_VIEWS[shown];
    const openRun = (runId: string) => go({ name: "run", podId: pod.id, runId });
    return (
        <div className="ox-autom">
            <nav className="ox-work__back" aria-label={`What ${pod.name} runs`}>
                <a className="ox-link" {...to(outcomeView(pod.id, "automations"), go)}>
                    <Icon name="back" size={13} />
                    All automations
                </a>
                <span aria-hidden="true">/</span>
                <span aria-current="page">
                    {LABELS[shown]}
                    {shown === "waiting" && waiting > 0 && <span className="ox-num"> {waiting}</span>}
                </span>
            </nav>
            {workflowView && (
                <WorkflowsPage
                    pod={pod}
                    pods={[...pods]}
                    view={workflowView}
                    bare
                    onOpenWorkflow={(workflow) => go({ name: "workflow", podId: pod.id, workflow })}
                    onOpenRun={openRun}
                    onAsk={(text) => actions.prompt(pod.id, text)}
                />
            )}
            {shown === "schedules" && (
                <StandingWork
                    podId={pod.id}
                    teammate={pod.name}
                    members={pod.members}
                    onOpenRun={openRun}
                    onOpenConversation={(id) => go(chatView({ kind: "chat", podId: pod.id, id }))}
                />
            )}
            {shown === "agents" && <AgentsTab pod={pod} go={go} />}
            {shown === "xray" && <XrayTab pod={pod} go={go} actions={actions} />}
        </div>
    );
}

/* ── agents ───────────────────────────────────────────────────────────── */

/** What the Lemma hands work to, each opening on its own page. The agent
 *  that answers as the Lemma is left out: it is the Lemma, whose page this
 *  already is. */
export function AgentsTab({ pod, go }: { pod: Pod; go: Go }) {
    const agents = useQuery({ queryKey: ["outcome-agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 5 * 60_000 });
    const rows = (agents.data ?? []).filter((agent) => !agent.front);
    if (agents.isPending) return <SkeletonRows label={`Reading ${pod.name}’s agents`} rows={3} slow={`Still reading ${pod.name}’s agents. It’s slower than usual.`} />;
    if (agents.isError) {
        return (
            <Calm tone="warn" title={`Couldn’t read ${pod.name}’s agents`} note={codeOf(agents.error)} actions={<TryAgain busy={agents.isFetching} onClick={() => void agents.refetch()} />}>
                Nothing about them changed. The list of them didn’t come through just now.
            </Calm>
        );
    }
    if (rows.length === 0) return <p className="ox-vs">{pod.name} does its own work, and hands nothing on.</p>;
    return (
        <div className="ox-pieces">
            {rows.map((agent, at) => (
                <Piece key={agentKey(agent, at)} icon="agent" name={agent.label} line={agent.blurb} link={agentLink(pod, agent, go)} tone="lemma" />
            ))}
        </div>
    );
}

/* ── x-ray ────────────────────────────────────────────────────────────── */

function lastRan(at: string): string {
    const ms = at ? Date.parse(at) : Number.NaN;
    return Number.isNaN(ms) ? "" : ` · last ran ${ageOf(ms, Date.now())} ago`;
}

/** An agent row's link, or none for a row with nothing to open
 *  (`agentView`). */
function agentLink(pod: Pod, agent: AgentRow, go: Go): PieceLink | undefined {
    const view = agentView(pod.id, agent);
    return view ? to(view, go) : undefined;
}

type PieceLink = { href: string; onClick?: ReturnType<typeof to>["onClick"] };

/** One thing a Lemma runs on. Opens in the frame when the frame shows that
 *  kind of thing, in the workspace when it does not; with no link it is only
 *  shown. */
function Piece({ icon, name, line, link, tone }: { icon: IconName; name: string; line?: string; link?: PieceLink; tone?: "lemma" | "off" }) {
    const className = "ox-piece" + (tone === "off" ? " ox-piece--off" : "");
    const body = (
        <>
            <span className={"ox-piece__i" + (tone === "lemma" ? " ox-piece__i--lemma" : "")}><Icon name={icon} /></span>
            <span className="ox-piece__t">{name}</span>
            {line && <span className="ox-piece__d">{line}</span>}
        </>
    );
    return link ? <a className={className} {...link}>{body}</a> : <div className={className}>{body}</div>;
}

export function XrayTab({ pod, go, actions }: { pod: Pod; go: Go; actions: FrameActions }) {
    const agents = useQuery({ queryKey: ["outcome-agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 5 * 60_000 });
    const schedules = useQuery({ queryKey: ["outcome-schedules", pod.id], queryFn: () => source.listSchedules(pod.id), staleTime: 5 * 60_000 });
    const tables = useQuery({ queryKey: ["outcome-tables", pod.id], queryFn: () => source.listLibrary(pod.id, "tables", "/"), staleTime: 5 * 60_000 });
    const tabs = useQuery({ queryKey: ["outcome-apps", pod.id], queryFn: () => source.listTabs(pod.id), staleTime: 5 * 60_000 });
    const skills = useQuery({ queryKey: ["outcome-skills", pod.id], queryFn: () => source.listLibrary(pod.id, "files", "/skills"), staleTime: 5 * 60_000, retry: false });
    const surfaces = useQuery({ queryKey: ["outcome-surfaces", pod.id], queryFn: () => source.listSurfaces(pod.id), staleTime: 5 * 60_000, retry: false });
    const workflows = useWorkflowList(pod.id);

    const apps = (tabs.data ?? []).flatMap((tab) => (tab.kind === "app" ? [tab] : []));
    const jobs = (schedules.data ?? []).filter((job) => !job.internal);
    const groups: { title: string; pending: boolean; failed: boolean; retry: () => void; retrying: boolean; empty: string; pieces: ReactNode[] }[] = [
        {
            title: "Runs on its own",
            pending: schedules.isPending,
            failed: schedules.isError,
            retry: () => void schedules.refetch(),
            retrying: schedules.isFetching,
            empty: "Nothing scheduled.",
            pieces: jobs.map((job) => <Piece key={job.id} icon="clock" name={job.title} line={`${job.trigger}${job.active ? "" : " · paused"}${lastRan(job.lastFiredAt)}`} link={to(outcomeView(pod.id, "automations", "schedules"), go)} tone={job.active ? "lemma" : "off"} />),
        },
        {
            title: "Workflows",
            pending: workflows.isPending,
            failed: workflows.isError,
            retry: () => void workflows.refetch(),
            retrying: workflows.isFetching,
            empty: "No workflows yet.",
            pieces: (workflows.data ?? []).map((flow) => <Piece key={flow.id} icon="flow" name={readableName(flow.name)} line={[flow.description, `${flow.steps} ${flow.steps === 1 ? "step" : "steps"}`, flow.active ? "" : "off"].filter(Boolean).join(" · ")} link={to({ name: "workflow", podId: pod.id, workflow: flow.name }, go)} tone={flow.active ? "lemma" : "off"} />),
        },
        {
            title: "Agents",
            pending: agents.isPending,
            failed: agents.isError,
            retry: () => void agents.refetch(),
            retrying: agents.isFetching,
            empty: "Only its own agent.",
            pieces: (agents.data ?? []).map((agent, at) => <Piece key={agentKey(agent, at)} icon="agent" name={agent.front ? `${agent.label} (answers here)` : agent.label} line={agent.blurb} link={agentLink(pod, agent, go)} tone="lemma" />),
        },
        {
            title: "Tables",
            pending: tables.isPending,
            failed: tables.isError,
            retry: () => void tables.refetch(),
            retrying: tables.isFetching,
            empty: "No tables yet.",
            pieces: (tables.data?.items ?? []).map((table) => <Piece key={table.id} icon="table" name={table.name} line={table.detail} link={to({ name: "table", podId: pod.id, table: table.name }, go)} />),
        },
        {
            title: "Apps",
            pending: tabs.isPending,
            failed: tabs.isError,
            retry: () => void tabs.refetch(),
            retrying: tabs.isFetching,
            empty: "No apps yet.",
            pieces: apps.map((app) => <Piece key={app.id} icon="app" name={app.label} line={app.visibility ? app.visibility.toLowerCase() : undefined} link={to({ name: "app", podId: pod.id, app: app.id.replace(/^app:/, "") }, go)} />),
        },
        {
            title: "Skills it follows",
            pending: skills.isPending,
            failed: skills.isError,
            retry: () => void skills.refetch(),
            retrying: skills.isFetching,
            empty: "No skills written here yet.",
            pieces: (skills.data?.items ?? []).map((item) => <Piece key={item.id} icon="skill" name={item.name.replace(/\.md$/i, "")} line={item.description} link={to({ name: "file", podId: pod.id, path: item.kind === "file" ? item.path : item.path.replace(/\/+$/, "") + "/SKILL.md" }, go)} />),
        },
        {
            title: `Where ${pod.name} answers`,
            pending: surfaces.isPending,
            failed: surfaces.isError,
            retry: () => void surfaces.refetch(),
            retrying: surfaces.isFetching,
            empty: "Only here.",
            pieces: (surfaces.data ?? []).map((surface) => <Piece key={surface.id} icon="plug" name={platformName(surface.platform)} line={[surface.handle, surface.active ? "" : "off"].filter(Boolean).join(" · ")} link={{ href: "#", onClick: (event) => { event.preventDefault(); actions.connect(pod.id); } }} tone={surface.active ? undefined : "off"} />),
        },
    ];
    return (
        <div>
            <p className="ox-xray__lead">Everything {pod.name} runs on. Each piece opens here.</p>
            <div className="ox-xray">
                {groups.map((group) => (
                    <section key={group.title} className="ox-xray__g">
                        <h2>{group.title}{!group.pending && !group.failed && group.pieces.length > 0 && <span className="ox-vs"> · {group.pieces.length}</span>}</h2>
                        {group.pending ? <SkeletonRows label={`Reading ${group.title.toLowerCase()}`} rows={2} />
                            : group.failed ? <div className="ox-sectfail"><Problem retrying={group.retrying} onRetry={group.retry}>Couldn’t read this.</Problem></div>
                            : group.pieces.length === 0 ? <p className="ox-vs">{group.empty}</p> : <div className="ox-pieces">{group.pieces}</div>}
                    </section>
                ))}
            </div>
        </div>
    );
}

