"use client";

import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { readableName } from "@/library/reading";
import { StandingWork } from "@/schedule/standing-work";
import { WorkflowsPage, type WorkflowsView } from "@/space/workflows-page";
import { AgentsView } from "@/stage/agents-view";
import { platformName } from "@/thread/conversation-origin";
import { useWorkflowList } from "@/workflow/use-run";
import type { FrameActions } from "./actions";
import { AUTOMATION_SECTIONS, ageOf, chatView, outcomeView, type AutomationSection, type Go, type QueueItem } from "./model";
import { Icon, to, type IconName } from "./parts";

const LABELS: Record<AutomationSection, string> = {
    workflows: "Workflows",
    waiting: "Waiting on you",
    running: "Running",
    history: "History",
    schedules: "Schedules",
    agents: "Agents",
    xray: "X-ray",
};

/** The four views the workspace's Workflows page keeps, by this tab's names. */
const WORKFLOW_VIEWS: Partial<Record<AutomationSection, WorkflowsView>> = {
    workflows: "workflows",
    waiting: "waiting",
    running: "running",
    history: "recent",
};

/** Everything a Lemma runs, on one tab: its workflows and their runs, its
 *  schedules, its agents, and the X-ray of all of it. The workspace keeps
 *  these on four pages -- Workflows, a schedule list in About, Agents in the
 *  profile, and the X-ray -- which is four places to look for one answer to
 *  "what does it do on its own". The phone app put them behind one row of
 *  views, and so does this. Each view is the workspace's own page, so a run,
 *  a schedule's health and an agent's settings read the same everywhere. */
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
            <nav className="ox-subtabs" aria-label={`What ${pod.name} runs`}>
                {AUTOMATION_SECTIONS.map((one) => (
                    <a key={one} className="ox-subtab" aria-current={one === shown ? "page" : undefined} {...to(outcomeView(pod.id, "automations", one === "workflows" ? null : one), go, { replace: true })}>
                        {LABELS[one]}
                        {one === "waiting" && waiting > 0 && <span>{waiting}</span>}
                    </a>
                ))}
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
                    orgId={pod.orgId}
                    onOpenRun={openRun}
                    onOpenConversation={(id) => go(chatView({ kind: "chat", podId: pod.id, id }))}
                />
            )}
            {shown === "agents" && (
                <AgentsView
                    podId={pod.id}
                    orgId={pod.orgId}
                    teammate={pod.name}
                    embedded
                    open={null}
                    onOpen={(agent) => { if (agent) go({ name: "agent", podId: pod.id, agent }); }}
                />
            )}
            {shown === "xray" && <XrayTab pod={pod} go={go} actions={actions} />}
        </div>
    );
}

/* ── x-ray ────────────────────────────────────────────────────────────── */

function lastRan(at: string): string {
    const ms = at ? Date.parse(at) : Number.NaN;
    return Number.isNaN(ms) ? "" : ` · last ran ${ageOf(ms, Date.now())} ago`;
}

/** One thing a Lemma runs on. Opens in the frame when the frame shows that
 *  kind of thing, in the workspace when it does not. */
function Piece({ icon, name, line, link, tone }: { icon: IconName; name: string; line?: string; link: { href: string; onClick?: ReturnType<typeof to>["onClick"] }; tone?: "lemma" | "off" }) {
    return (
        <a className={"ox-piece" + (tone === "off" ? " ox-piece--off" : "")} {...link}>
            <span className={"ox-piece__i" + (tone === "lemma" ? " ox-piece__i--lemma" : "")}><Icon name={icon} /></span>
            <span className="ox-piece__t">{name}</span>
            {line && <span className="ox-piece__d">{line}</span>}
        </a>
    );
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
    const groups: { title: string; pending: boolean; failed: boolean; empty: string; pieces: ReactNode[] }[] = [
        {
            title: "Runs on its own",
            pending: schedules.isPending,
            failed: schedules.isError,
            empty: "Nothing scheduled.",
            pieces: jobs.map((job) => <Piece key={job.id} icon="clock" name={job.title} line={`${job.trigger}${job.active ? "" : " · paused"}${lastRan(job.lastFiredAt)}`} link={to(outcomeView(pod.id, "automations", "schedules"), go)} tone={job.active ? "lemma" : "off"} />),
        },
        {
            title: "Workflows",
            pending: workflows.isPending,
            failed: workflows.isError,
            empty: "No workflows yet.",
            pieces: (workflows.data ?? []).map((flow) => <Piece key={flow.id} icon="flow" name={readableName(flow.name)} line={[flow.description, `${flow.steps} ${flow.steps === 1 ? "step" : "steps"}`, flow.active ? "" : "off"].filter(Boolean).join(" · ")} link={to({ name: "workflow", podId: pod.id, workflow: flow.name }, go)} tone={flow.active ? "lemma" : "off"} />),
        },
        {
            title: "Agents",
            pending: agents.isPending,
            failed: agents.isError,
            empty: "Only its own agent.",
            pieces: (agents.data ?? []).map((agent) => <Piece key={agent.id} icon="agent" name={agent.front ? `${agent.label} (answers here)` : agent.label} line={agent.blurb} link={to({ name: "agent", podId: pod.id, agent: agent.name }, go)} tone="lemma" />),
        },
        {
            title: "Tables",
            pending: tables.isPending,
            failed: tables.isError,
            empty: "No tables yet.",
            pieces: (tables.data?.items ?? []).map((table) => <Piece key={table.id} icon="table" name={table.name} line={table.detail} link={to({ name: "table", podId: pod.id, table: table.name }, go)} />),
        },
        {
            title: "Apps",
            pending: tabs.isPending,
            failed: tabs.isError,
            empty: "No apps yet.",
            pieces: apps.map((app) => <Piece key={app.id} icon="app" name={app.label} line={app.visibility ? app.visibility.toLowerCase() : undefined} link={to({ name: "app", podId: pod.id, app: app.id.replace(/^app:/, "") }, go)} />),
        },
        {
            title: "Skills it follows",
            pending: skills.isPending,
            failed: skills.isError,
            empty: "No skills written here yet.",
            pieces: (skills.data?.items ?? []).map((item) => <Piece key={item.id} icon="skill" name={item.name.replace(/\.md$/i, "")} line={item.description} link={to({ name: "file", podId: pod.id, path: item.kind === "file" ? item.path : item.path.replace(/\/+$/, "") + "/SKILL.md" }, go)} />),
        },
        {
            title: "Where people reach it",
            pending: surfaces.isPending,
            failed: surfaces.isError,
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
                        {group.pending ? <p className="ox-loading">Loading…</p> : group.failed ? <p className="ox-vs">Couldn’t read this.</p> : group.pieces.length === 0 ? <p className="ox-vs">{group.empty}</p> : <div className="ox-pieces">{group.pieces}</div>}
                    </section>
                ))}
            </div>
        </div>
    );
}

