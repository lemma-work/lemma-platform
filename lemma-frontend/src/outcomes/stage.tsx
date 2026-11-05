"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { RunPage } from "@/space/run-page";
import { WorkflowPage } from "@/space/workflow-page";
import { AppFrameView } from "@/desktop/app-frame";
import { FileView } from "@/thread/file-view";
import { TableView } from "@/library/library";
import { RecordView } from "@/library/record-view";
import { DocSpace, isDoc } from "@/docs/doc-space";
import { AgentPage } from "@/space/agent-page";
import { AboutPage } from "@/space/about-page";
import { isAboutSection } from "@/space/about-sections";
import { useQueryClient } from "@tanstack/react-query";
import type { Org } from "@/data";
import { displayAgentName } from "@/data/agent-names";
import { readableName } from "@/library/reading";
import { registerFrame } from "@/thread/compose-bridge";
import { useResourceConversation } from "@/thread/use-resource-conversation";
import { NEW_CHAT, chatView, outcomeView, type Go, type View } from "./model";
import { classic } from "./data";
import type { FrameActions } from "./actions";
import { Empty, Icon, to } from "./parts";

const NO_FRAME_REF = () => undefined;

/** Back where the reader came from in the frame, or to `fallback` when they
 *  arrived by a link and there is nowhere in it to go back to. */
export type Back = (fallback: View) => void;

/** Somewhere inside a Lemma, opened in the frame: a workflow and its runs,
 *  an app, a file or page, a table and its rows, an agent. The bar says
 *  which Lemma it is in and is the way back; the workspace's own view of it
 *  fills the rest. */
function Stage({ pod, title, go, fill, actions, children }: { pod: Pod; title: string; go: Go; fill?: boolean; actions?: ReactNode; children: ReactNode }) {
    return (
        <div className={"ox-stagepage" + (fill ? " ox-stagepage--fill" : "")}>
            <div className="ox-stagebar">
                <p className="ox-crumb">
                    <a {...to({ name: "outcomes" }, go)}>Lemmas</a>
                    <span aria-hidden="true">/</span>
                    <a {...to(outcomeView(pod.id), go)}>{pod.name}</a>
                    <span aria-hidden="true">/</span>
                    <span className="ox-crumb__here">{title}</span>
                </p>
                {actions && <div className="ox-stagebar__acts">{actions}</div>}
            </div>
            <div className="ox-stage">{children}</div>
        </div>
    );
}

function Workspace({ href }: { href: string }) {
    return <a className="ox-btn ox-btn--small" href={href}>Open in the workspace</a>;
}

export function RunView({ pod, runId, go, back }: { pod: Pod; runId: string; go: Go; back: Back }) {
    const [name, setName] = useState<string | null>(null);
    return (
        <Stage pod={pod} title={name ? `${readableName(name)} · run` : "Workflow run"} go={go} fill actions={<Workspace href={classic.run(pod.id, runId)} />}>
            <div className="run-page">
                <RunPage
                    pod={pod}
                    runId={runId}
                    label="Workflow run"
                    onBack={() => back(outcomeView(pod.id, "xray"))}
                    onOpenConversation={(id) => go(chatView({ kind: "chat", podId: pod.id, id }))}
                    onOpenRun={(next) => go({ name: "run", podId: pod.id, runId: next })}
                    onOpenWorkflow={(workflow) => go({ name: "workflow", podId: pod.id, workflow })}
                    onNamed={setName}
                />
            </div>
        </Stage>
    );
}

/** A workflow: how it starts and runs, and every run of it. Its "back" is
 *  the outcome's X-ray, where the workflows it runs are listed. */
export function WorkflowView({ pod, workflow, go }: { pod: Pod; workflow: string; go: Go }) {
    const discussion = useResourceConversation(pod.id);
    const discuss = async (name: string) => {
        const id = await discussion.open("workflow", name);
        if (id) go(chatView({ kind: "chat", podId: pod.id, id }));
    };
    return (
        <Stage pod={pod} title={readableName(workflow)} go={go} fill actions={<Workspace href={classic.workflow(pod.id, workflow)} />}>
            <div className="run-page">
                <WorkflowPage
                    pod={pod}
                    name={workflow}
                    onBack={() => go(outcomeView(pod.id, "xray"))}
                    onOpenRun={(runId) => go({ name: "run", podId: pod.id, runId })}
                    onDiscuss={source.label === "live" ? (name) => void discuss(name) : undefined}
                />
            </div>
        </Stage>
    );
}

/** An app, framed here the way the workspace frames it, and registered so
 *  a button in it that asks Lemma something reaches a chat in this frame. */
export function AppView({ pod, app, go }: { pod: Pod; app: string; go: Go }) {
    const tabs = useQuery({ queryKey: ["outcome-apps", pod.id], queryFn: () => source.listTabs(pod.id), staleTime: 5 * 60_000 });
    const found = (tabs.data ?? []).flatMap((tab) => (tab.kind === "app" && tab.id === "app:" + app ? [tab] : []))[0] ?? null;
    const unregister = useRef<(() => void) | null>(null);
    useEffect(() => () => { unregister.current?.(); }, []);
    return (
        <Stage
            pod={pod}
            title={found?.label ?? readableName(app)}
            go={go}
            fill
            actions={found && <a className="ox-btn ox-btn--small" href={found.url} target="_blank" rel="noreferrer"><Icon name="out" size={13} />New tab</a>}
        >
            {tabs.isPending
                ? <p className="ox-loading">Opening the app…</p>
                : !found
                    ? <div className="ox-stage__none"><Empty title="This app isn’t here."><p>It may have been removed or renamed.</p></Empty></div>
                    : <AppFrameView
                        url={found.url}
                        frameRef={NO_FRAME_REF}
                        onFrameLoad={(view) => {
                            unregister.current?.();
                            unregister.current = registerFrame(view);
                        }}
                    />}
        </Stage>
    );
}

/** A file. A page (Markdown) opens as the workspace's own page, edited in
 *  place with its comments; anything else as the file viewer. */
export function FilePage({ pod, path, go, actions }: { pod: Pod; path: string; go: Go; actions: FrameActions }) {
    const name = path.split("/").filter(Boolean).at(-1) ?? path;
    const page = isDoc(path);
    return (
        <Stage pod={pod} title={name} go={go} fill actions={<Workspace href={classic.file(pod.id, path)} />}>
            {page ? (
                <div className="pane file-tab-pane doc-tab-pane">
                    <DocSpace
                        pod={pod}
                        path={path}
                        openFile={(next) => go({ name: "file", podId: pod.id, path: next })}
                        openTable={(table) => go({ name: "table", podId: pod.id, table })}
                        openConversation={(id) => go(chatView({ kind: "chat", podId: pod.id, id }))}
                        sendToBot={(text) => actions.prompt(pod.id, text)}
                    />
                </div>
            ) : (
                <div className="pane file-tab-pane">
                    <div className="pane__inner">
                        <FileView podId={pod.id} path={path} full onOpenTab={(next) => go({ name: "file", podId: pod.id, path: next })} />
                    </div>
                </div>
            )}
        </Stage>
    );
}

export function TablePage({ pod, table, go, actions }: { pod: Pod; table: string; go: Go; actions: FrameActions }) {
    return (
        <Stage pod={pod} title={readableName(table)} go={go} fill actions={<Workspace href={classic.table(pod.id, table)} />}>
            <div className="pane library-pane">
                <TableView
                    podId={pod.id}
                    name={table}
                    teammate={pod.teammate?.name || pod.name}
                    onOpenRecord={(name, recordId) => go({ name: "record", podId: pod.id, table: name, recordId })}
                    onAsk={(text) => actions.prompt(pod.id, text)}
                />
            </div>
        </Stage>
    );
}

/** One row of a table, on its own page. */
export function RecordPage({ pod, table, recordId, go }: { pod: Pod; table: string; recordId: string; go: Go }) {
    return (
        <Stage pod={pod} title={readableName(table)} go={go} fill actions={<Workspace href={classic.record(pod.id, table, recordId)} />}>
            <div className="pane library-pane">
                <RecordView
                    podId={pod.id}
                    tableName={table}
                    recordId={recordId}
                    onOpenTable={(name) => go({ name: "table", podId: pod.id, table: name })}
                    onOpenRecord={(name, id) => go({ name: "record", podId: pod.id, table: name, recordId: id })}
                />
            </div>
        </Stage>
    );
}

/** One of a Lemma's agents: who it is, what it may do, and chats with it. */
export function AgentView({ pod, agent, go, actions, back }: { pod: Pod; agent: string; go: Go; actions: FrameActions; back: Back }) {
    return (
        <Stage pod={pod} title={displayAgentName(agent)} go={go} fill actions={<Workspace href={classic.agent(pod.id, agent)} />}>
            <div className="pane bot-pane">
                <AgentPage
                    pod={pod}
                    name={agent}
                    live={source.label === "live"}
                    onBack={() => back(outcomeView(pod.id, "settings", "agents"))}
                    onOpenConversation={(id) => go(chatView({ kind: "chat", podId: pod.id, id }))}
                    onAsk={(text, createWith) => actions.ask(pod.id, text, createWith)}
                    onOpenSchedules={() => go(outcomeView(pod.id, "settings", "schedules"))}
                    onOpenWorkflows={() => go(outcomeView(pod.id, "xray"))}
                />
            </div>
        </Stage>
    );
}

/** A Lemma's settings: the workspace's own About -- its model, skills,
 *  memory, agents, connectors, schedules, channels, people, MCP -- full
 *  height, with the way back to the Lemma. */
export function SettingsView({ pod, org, section, go, actions }: { pod: Pod; org: Org; section: string | null; go: Go; actions: FrameActions }) {
    const cache = useQueryClient();
    return (
        <Stage pod={pod} title="Settings" go={go} fill actions={<Workspace href={classic.pod(pod.id)} />}>
            <AboutPage
                pod={pod}
                orgId={org.id}
                orgName={org.name}
                section={isAboutSection(section) ? section : null}
                onSection={(next) => go(outcomeView(pod.id, "settings", next))}
                onOpenSettings={(next) => actions.openSettings(next)}
                onAsk={() => go(chatView({ kind: "chat", podId: pod.id, id: NEW_CHAT }))}
                onOpenAgent={(name) => go({ name: "agent", podId: pod.id, agent: name })}
                onOpenTeammate={(id) => go(outcomeView(id))}
                onAskFor={(text) => actions.prompt(pod.id, text)}
                onOpenRun={(runId) => go({ name: "run", podId: pod.id, runId })}
                onOpenConversation={(id) => go(chatView({ kind: "chat", podId: pod.id, id }))}
                onFile={(path) => go({ name: "file", podId: pod.id, path })}
                onDeleted={() => { void cache.invalidateQueries({ queryKey: ["pods"] }); go({ name: "outcomes" }); }}
            />
        </Stage>
    );
}
