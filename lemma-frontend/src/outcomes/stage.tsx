"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { source, type Pod, type Tab } from "@/data";
import { RunPage } from "@/space/run-page";
import { ComputerView } from "@/computer/computer-view";
import { ConnectBanner } from "@/connect/pod-connect";
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
import { docTitle } from "@/library/doc-title";
import { ViewActions } from "@/shell/view-actions";
import { CommentsButton } from "@/docpages/comments/button";
import { moveComments } from "@/docpages/comments/store";
import { recordMove } from "@/docpages/moves";
import { renamePage } from "@/docpages/rename";
import { DocAskContext } from "@/docs/doc-ask";
import { FloatingChat, moveDocThread, useFloatingChat, type ChatResource, type FloatingChatState } from "@/chat/floating-chat";
import type { ShareSubject } from "@/space/share-sheet";
import { useResourceConversation } from "@/thread/use-resource-conversation";
import { NEW_CHAT, chatView, outcomeView, type Go, type View } from "./model";
import type { FrameActions } from "./actions";
import { LemmaActions, SectionSwitch } from "./lemma-bar";
import { Empty, to } from "./parts";

const NOTHING = () => undefined;

/** Back where the reader came from in the frame, or to `fallback` when they
 *  arrived by a link and there is nowhere in it to go back to. */
export type Back = (fallback: View) => void;

/** Somewhere inside a Lemma, opened in the frame: a workflow and its runs,
 *  an app, a file or page, a table and its rows, an agent. The bar says
 *  which Lemma it is in and is the way back; the workspace's own view of it
 *  fills the rest. */
export function Stage({ pod, title, here, go, fill, actions, children }: {
    pod: Pod;
    /** Where in the Lemma this is. Left out, the Lemma itself is: its
     *  summary, where the crumb ends at its name. */
    title?: string;
    /** In place of `title`, a step that does more than name the place: a
     *  section's page names itself with a menu of the other sections. */
    here?: ReactNode;
    go: Go;
    fill?: boolean;
    actions?: ReactNode;
    children: ReactNode;
}) {
    const last = here ?? (title ? <span className="ox-crumb__here">{title}</span> : null);
    return (
        <div className={"ox-stagepage" + (fill ? " ox-stagepage--fill" : "")}>
            <div className="ox-stagebar">
                <div className="ox-stagebar__l">
                    <p className="ox-crumb">
                        <a {...to({ name: "outcomes" }, go)}>Lemmas</a>
                        <span aria-hidden="true">/</span>
                        {last ? <a {...to(outcomeView(pod.id), go)}>{pod.name}</a> : <span className="ox-crumb__here">{pod.name}</span>}
                        {last && <span aria-hidden="true">/</span>}
                    </p>
                    {last}
                </div>
                {actions && <div className="ox-stagebar__acts">{actions}</div>}
            </div>
            {/* Connections this Lemma needs that the reader has not made, as
                the workspace asks for them over every page of a pod. */}
            <ConnectBanner key={pod.id} pod={pod} />
            <div className="ox-stage">{children}</div>
        </div>
    );
}

/** The bar's end over something a Lemma holds: what the workspace offers on
 *  it -- open, reload, download, copy its link, refresh -- and sharing it. */
function HeldActions({ pod, tab, subject, actions, onReload = NOTHING }: {
    pod: Pod;
    tab: Tab;
    subject: ShareSubject;
    actions: FrameActions;
    onReload?: () => void;
}) {
    return (
        <>
            <ViewActions tab={tab} podId={pod.id} onNew={NOTHING} onHistory={NOTHING} onComputer={NOTHING} onReload={onReload} />
            <button type="button" className="ox-btn ox-btn--small" onClick={() => actions.share(pod.id, subject)}>Share</button>
        </>
    );
}

/** The chat about what a stage shows -- a page, a table, an app -- floating
 *  beside it, as the workspace has it: that thing's own conversation, which a
 *  page's "Ask for change" and a table's ask both reach. Opened full size, it
 *  is a chat of the frame's like any other. */
function ResourceChat({ pod, resource, state, go }: { pod: Pod; resource: ChatResource; state: FloatingChatState; go: Go }) {
    return (
        <FloatingChat
            pod={pod}
            resource={resource}
            live={source.label === "live"}
            state={state}
            onOpenFile={(path) => go({ name: "file", podId: pod.id, path })}
            onOpenTable={(table) => go({ name: "table", podId: pod.id, table })}
            onOpenApp={(app) => go({ name: "app", podId: pod.id, app })}
            onExpand={(id) => {
                state.setOpen(false);
                go(chatView({ kind: "chat", podId: pod.id, id }));
            }}
        />
    );
}

export function RunView({ pod, runId, go, back }: { pod: Pod; runId: string; go: Go; back: Back }) {
    const [name, setName] = useState<string | null>(null);
    return (
        <Stage pod={pod} title={name ? `${readableName(name)} · run` : "Workflow run"} go={go} fill>
            <div className="run-page">
                <RunPage
                    pod={pod}
                    runId={runId}
                    label="Workflow run"
                    onBack={() => back(outcomeView(pod.id, "automations", "history"))}
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
        <Stage pod={pod} title={readableName(workflow)} go={go} fill>
            <div className="run-page">
                <WorkflowPage
                    pod={pod}
                    name={workflow}
                    onBack={() => go(outcomeView(pod.id, "automations"))}
                    onOpenRun={(runId) => go({ name: "run", podId: pod.id, runId })}
                    onDiscuss={source.label === "live" ? (name) => void discuss(name) : undefined}
                />
            </div>
        </Stage>
    );
}

/** An app, framed here the way the workspace frames it, and registered so
 *  a button in it that asks Lemma something reaches a chat in this frame. */
export function AppView({ pod, app, go, actions, shown }: {
    pod: Pod;
    app: string;
    go: Go;
    actions: FrameActions;
    /** Whether it is the app in front. A kept app behind something else
     *  stays mounted but is not trusted to ask for a chat: only the app on
     *  screen may put words in a box. */
    shown: boolean;
}) {
    const tabs = useQuery({ queryKey: ["outcome-apps", pod.id], queryFn: () => source.listTabs(pod.id), staleTime: 5 * 60_000 });
    const found = (tabs.data ?? []).flatMap((tab) => (tab.kind === "app" && tab.id === "app:" + app ? [tab] : []))[0] ?? null;
    /* The window the app last loaded in, registered with the compose bridge
       only while the app is shown, and again after each load. */
    const [loaded, setLoaded] = useState<Window | null>(null);
    useEffect(() => (shown ? registerFrame(loaded) : undefined), [shown, loaded]);
    const frame = useRef<HTMLIFrameElement | null>(null);
    const chat = useFloatingChat();
    const label = found?.label ?? readableName(app);
    return (
        <Stage
            pod={pod}
            title={label}
            go={go}
            fill
            actions={found && (
                <HeldActions
                    pod={pod}
                    tab={found}
                    subject={{ kind: "app", label: found.label }}
                    actions={actions}
                    /* The src it was given, which on macOS is the app's alias
                       rather than its URL. */
                    onReload={() => { if (frame.current) frame.current.src = frame.current.getAttribute("src") ?? found.url; }}
                />
            )}
        >
            {tabs.isPending
                ? <p className="ox-loading">Opening the app…</p>
                : !found
                    ? <div className="ox-stage__none"><Empty title="This app isn’t here."><p>It may have been removed or renamed.</p></Empty></div>
                    : <AppFrameView
                        url={found.url}
                        frameRef={(element) => { frame.current = element; }}
                        onFrameLoad={setLoaded}
                    />}
            {found && <ResourceChat pod={pod} resource={{ kind: "app", name: app, label }} state={chat} go={go} />}
        </Stage>
    );
}

/** A file. A page (Markdown) opens as the workspace's own page, edited in
 *  place, with its comments and the chat about it beside it; anything else
 *  as the file viewer. */
export function FilePage({ pod, path, go, actions }: { pod: Pod; path: string; go: Go; actions: FrameActions }) {
    const name = path.split("/").filter(Boolean).at(-1) ?? path;
    const page = isDoc(path);
    const chat = useFloatingChat();
    const cache = useQueryClient();
    const docAsk = useMemo(() => ({ label: pod.name, ask: chat.ask }), [pod.name, chat.ask]);
    /* A page's file follows its title, as in the workspace: the file moves
       with what it owns, the chat about it goes along, and the address is
       replaced rather than pushed. */
    const rename = useCallback(async (from: string, title: string): Promise<string> => {
        const podId = pod.id;
        const to = await renamePage({
            move: (a, b) => source.renameFile(podId, a, b),
            read: async (at) => {
                try { return (await source.readFile(podId, at)).text ?? null; }
                catch (error) { if ((error as { statusCode?: number } | null)?.statusCode === 404) return null; throw error; }
            },
            write: (at, text) => source.writeFile(podId, at, text),
            moveComments: (a, b) => moveComments(podId, a, b),
        }, from, title);
        if (to === from) return from;
        if (source.label === "live") await moveDocThread(podId, from, to).catch(() => undefined);
        void cache.invalidateQueries({ queryKey: ["resource-thread", podId, "file"] });
        recordMove(podId, from, to);
        /* Read afresh at the new name rather than carried over: the rename
           rewrote the page's own links there, and the page is drawn again
           for its new address, so an old copy would be the text it shows --
           and saves back. */
        cache.removeQueries({ queryKey: ["file", podId, to], exact: true });
        go({ name: "file", podId, path: to }, { replace: true });
        return to;
    }, [pod.id, cache, go]);
    const tab: Tab = { id: "file:" + path, kind: "file", label: docTitle(path), path };
    return (
        <Stage
            pod={pod}
            title={name}
            go={go}
            fill
            actions={(
                <>
                    {page && !/^\/me(\/|$)/.test(path) && <CommentsButton path={path} />}
                    <HeldActions pod={pod} tab={tab} subject={{ kind: "file", path, label: tab.label }} actions={actions} />
                </>
            )}
        >
            {page ? (
                <div className="pane file-tab-pane doc-tab-pane">
                    <DocAskContext.Provider value={docAsk}>
                        <DocSpace
                            pod={pod}
                            path={path}
                            renamePage={rename}
                            openFile={(next) => go({ name: "file", podId: pod.id, path: next })}
                            openTable={(table) => go({ name: "table", podId: pod.id, table })}
                            openConversation={(id) => go(chatView({ kind: "chat", podId: pod.id, id }))}
                            sendToBot={chat.send}
                        />
                    </DocAskContext.Provider>
                </div>
            ) : (
                <div className="pane file-tab-pane">
                    <div className="pane__inner">
                        <FileView podId={pod.id} path={path} full onOpenTab={(next) => go({ name: "file", podId: pod.id, path: next })} />
                    </div>
                </div>
            )}
            <ResourceChat pod={pod} resource={{ kind: "file", name: path, label: tab.label }} state={chat} go={go} />
        </Stage>
    );
}

export function TablePage({ pod, table, go, actions }: { pod: Pod; table: string; go: Go; actions: FrameActions }) {
    const chat = useFloatingChat();
    const label = readableName(table);
    return (
        <Stage
            pod={pod}
            title={label}
            go={go}
            fill
            actions={<HeldActions pod={pod} tab={{ id: "table:" + table, kind: "table", label, name: table }} subject={{ kind: "table", label }} actions={actions} />}
        >
            <div className="pane library-pane">
                <TableView
                    podId={pod.id}
                    name={table}
                    teammate={pod.teammate?.name || pod.name}
                    onOpenRecord={(name, recordId) => go({ name: "record", podId: pod.id, table: name, recordId })}
                    onAsk={chat.prompt}
                />
            </div>
            <ResourceChat pod={pod} resource={{ kind: "table", name: table, label }} state={chat} go={go} />
        </Stage>
    );
}

/** One row of a table, on its own page. */
export function RecordPage({ pod, table, recordId, go, actions }: { pod: Pod; table: string; recordId: string; go: Go; actions: FrameActions }) {
    return (
        <Stage pod={pod} title={readableName(table)} go={go} fill actions={<HeldActions pod={pod} tab={{ id: "table:" + table, kind: "table", label: readableName(table), name: table }} subject={{ kind: "table", label: readableName(table) }} actions={actions} />}>
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
        <Stage pod={pod} title={displayAgentName(agent)} go={go} fill>
            <div className="pane bot-pane">
                <AgentPage
                    pod={pod}
                    name={agent}
                    live={source.label === "live"}
                    onBack={() => back(outcomeView(pod.id, "settings", "agents"))}
                    onOpenConversation={(id) => go(chatView({ kind: "chat", podId: pod.id, id }))}
                    onAsk={(text, createWith, files) => actions.ask(pod.id, text, createWith, files)}
                    onOpenSchedules={() => go(outcomeView(pod.id, "settings", "schedules"))}
                    onOpenWorkflows={() => go(outcomeView(pod.id, "automations"))}
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
        <Stage pod={pod} here={<SectionSwitch pod={pod} here="settings" go={go} />} go={go} actions={<LemmaActions pod={pod} go={go} actions={actions} />}>
            <div className="ox-page ox-section" data-section="settings">
                <h1 className="ox-section__t">Settings</h1>
                <AboutPage
                    framed
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
            </div>
        </Stage>
    );
}

/** The machine a Lemma works on: what is on its screen, the sites it is
 *  signed in to, and its files -- from one chat's working folder when it is
 *  opened from that chat, else the whole machine. */
export function ComputerPage({ pod, conversationId, go }: { pod: Pod; conversationId: string | null; go: Go }) {
    const cache = useQueryClient();
    return (
        <Stage
            pod={pod}
            title="Computer"
            go={go}
            fill
            actions={<button type="button" className="ox-btn ox-btn--small" onClick={() => void cache.invalidateQueries({ queryKey: ["computer"] })}>Refresh</button>}
        >
            <div className="pane library-pane">
                <ComputerView podId={pod.id} conversationId={conversationId} visible />
            </div>
        </Stage>
    );
}
