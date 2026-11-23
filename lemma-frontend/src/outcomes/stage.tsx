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
import { AboutPage, aboutHead } from "@/space/about-page";
import { LemmaPageHead } from "./lemma-page";
import { isAboutSection } from "@/space/about-sections";
import { useQueryClient } from "@tanstack/react-query";
import type { Org } from "@/data";
import { displayAgentName } from "@/data/agent-names";
import { readableName } from "@/library/reading";
import { registerFrame } from "@/thread/compose-bridge";
import { acknowledgeOpen, appUrlAt, readLocation, readOpen, tellLocation } from "@/thread/location-bridge";
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
import { chatView, outcomeView, type Go, type View } from "./model";
import type { FrameActions } from "./actions";
import { Icon, to } from "./parts";
import { AppMark } from "./app-mark";
import { Calm } from "./calm";
import { TryAgain, codeOf } from "./read-again";
import { SHOW_AFTER_MS, useLasted } from "@/ui/skeleton";
import { openPodApp } from "@/desktop/pod-apps";

const NOTHING = () => undefined;

/** Back where the reader came from in the frame, or to `fallback` when they
 *  arrived by a link and there is nowhere in it to go back to. */
export type Back = (fallback: View) => void;

/** Somewhere inside a Lemma, opened in the frame: a workflow and its runs,
 *  an app, a file or page, a table and its rows, an agent. The bar says
 *  which Lemma it is in and is the way back; the workspace's own view of it
 *  fills the rest. */
export function Stage({ pod, title, here, head, go, fill, ownBar = false, actions, children }: {
    pod: Pod;
    /** Where in the Lemma this is. Left out, the Lemma itself is: its
     *  summary, where the crumb ends at its name. */
    title?: string;
    /** In place of `title`, a step that does more than name the place: a
     *  section's page names itself with a menu of the other sections. */
    here?: ReactNode;
    /** In place of the bar, the Lemma's own header: who it is, its tabs.
     *  A Lemma's own pages wear it; what it holds wears the bar. */
    head?: ReactNode;
    go: Go;
    fill?: boolean;
    /** The page draws the one bar itself, with `StageCrumbs` at its start:
     *  a page with a bar of its own under this one read as two headers. */
    ownBar?: boolean;
    actions?: ReactNode;
    children: ReactNode;
}) {
    return (
        <div className={"ox-stagepage" + (fill ? " ox-stagepage--fill" : "")}>
            {head}
            {!head && !ownBar && (
                <div className="ox-stagebar">
                    <StageCrumbs pod={pod} title={title} here={here} go={go} />
                    {actions && <div className="ox-stagebar__acts">{actions}</div>}
                </div>
            )}
            {/* Connections this Lemma needs that the reader has not made, as
                the workspace asks for them over every page of a pod. */}
            <ConnectBanner key={pod.id} pod={pod} />
            <div className="ox-stage">{children}</div>
        </div>
    );
}

/** The start of a stage's bar: which Lemma this is in, and where in it. */
export function StageCrumbs({ pod, title, here, go }: { pod: Pod; title?: string; here?: ReactNode; go: Go }) {
    const last = here ?? (title ? <span className="ox-crumb__here">{title}</span> : null);
    return (
        <div className="ox-stagebar__l">
            <p className="ox-crumb">
                <a {...to({ name: "outcomes" }, go)}>Lemmas</a>
                <span aria-hidden="true">/</span>
                {last ? <a {...to(outcomeView(pod.id), go)}>{pod.name}</a> : <span className="ox-crumb__here">{pod.name}</span>}
                {last && <span aria-hidden="true">/</span>}
            </p>
            {last}
        </div>
    );
}

/** The bar's end over something a Lemma holds: what the workspace offers on
 *  it -- open, reload, download, copy its link, refresh -- and sharing it. */
export function HeldActions({ pod, tab, subject, actions, onReload = NOTHING }: {
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
export function AppView({ pod, app, at, go, actions, shown, onMove }: {
    pod: Pod;
    app: string;
    /** Where the address says the app is inside itself: `/` for its one
     *  screen, or the page for one of its records. Read while it is in front. */
    at: string;
    go: Go;
    actions: FrameActions;
    /** Whether it is the app in front. A kept app behind something else
     *  stays mounted but is not trusted to ask for a chat: only the app on
     *  screen may put words in a box. */
    shown: boolean;
    /** The app in front moved inside itself, or asked for another app of
     *  this Lemma: the app named, and where in it. The address follows. */
    onMove: (podId: string, app: string, path: string) => void;
}) {
    const tabs = useQuery({ queryKey: ["outcome-apps", pod.id], queryFn: () => source.listTabs(pod.id), staleTime: 5 * 60_000 });
    const found = (tabs.data ?? []).flatMap((tab) => (tab.kind === "app" && tab.id === "app:" + app ? [tab] : []))[0] ?? null;
    /* The window the app last loaded in, registered with the compose bridge
       only while the app is shown, and again after each load. */
    const [loaded, setLoaded] = useState<Window | null>(null);
    useEffect(() => (shown ? registerFrame(loaded) : undefined), [shown, loaded]);
    const frame = useRef<HTMLIFrameElement | null>(null);
    /* The frame starts on the path the address named when it was first
       drawn. After that the address is told to it, never loaded into it: a
       frame given a new `src` starts the app over. */
    const [start] = useState(at);
    const address = found?.url ?? null;
    const url = useMemo(() => (address ? appUrlAt(address, start) : null), [address, start]);
    /* Where the frame is, as far as this side knows: where it started, what
       it last said, or what it was last told. A move it made is not told
       back to it, and one it was told is not heard as its own. */
    const frameAt = useRef(start);
    const podId = pod.id;
    useEffect(() => {
        if (!shown || !loaded) return;
        const listen = (event: MessageEvent) => {
            const moved = readLocation(event, loaded);
            if (moved !== null) {
                frameAt.current = moved;
                onMove(podId, app, moved);
                return;
            }
            const asked = readOpen(event, loaded);
            if (!asked) return;
            onMove(podId, asked.app, asked.path);
            acknowledgeOpen(event, asked);
        };
        window.addEventListener("message", listen);
        return () => window.removeEventListener("message", listen);
    }, [shown, loaded, podId, app, onMove]);
    useEffect(() => {
        if (!shown || !loaded || frameAt.current === at) return;
        const src = frame.current?.getAttribute("src");
        if (!src) return;
        frameAt.current = at;
        tellLocation(loaded, new URL(src, window.location.href).origin, at);
    }, [shown, loaded, at]);
    const chat = useFloatingChat();
    const label = found?.label ?? readableName(app);
    /* Each Reload or Try again loads the frame afresh -- on the src it was
       given, which on macOS is the app's alias rather than its URL -- and
       shows it opening again. */
    const [attempt, setAttempt] = useState(0);
    const again = () => setAttempt((n) => n + 1);
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
                    onReload={again}
                />
            )}
        >
            {tabs.isPending
                ? <div className="app-veil"><AppOpening name={app} label={label} lemma={pod.name} /></div>
                : tabs.isError && !found
                    ? (
                        <div className="ox-stage__none">
                            <Calm
                                tone="warn"
                                title={`Couldn’t read ${pod.name}’s apps`}
                                note={codeOf(tabs.error)}
                                actions={<TryAgain busy={tabs.isFetching} onClick={() => void tabs.refetch()} />}
                            >
                                Nothing in them changed. The list of them didn’t come through just now.
                            </Calm>
                        </div>
                    )
                    : !found || !url
                        ? (
                            <div className="ox-stage__none">
                                <Calm
                                    icon="search"
                                    title="This app isn’t here"
                                    actions={<a className="b b--m b--primary" {...to(outcomeView(pod.id), go)}>Go to {pod.name}</a>}
                                >
                                    It may have been removed or renamed.
                                </Calm>
                            </div>
                        )
                        : <AppFrameView
                            url={url}
                            attempt={attempt}
                            frameRef={(element) => { frame.current = element; }}
                            /* A load is the app starting over, on the path its
                               frame was given. */
                            onFrameLoad={(view) => { frameAt.current = start; setLoaded(view); }}
                            mark={<AppMark name={app} label={label} size={64} className="app-open__app" />}
                            cover={(
                                <AppOpening
                                    key={attempt}
                                    name={app}
                                    label={label}
                                    lemma={pod.name}
                                    url={url}
                                    onAsk={() => actions.ask(pod.id, appTrouble(label))}
                                    onRetry={again}
                                />
                            )}
                        />}
            {found && <ResourceChat pod={pod} resource={{ kind: "app", name: app, label }} state={chat} go={go} />}
        </Stage>
    );
}

/** How long an app may take to answer before it is said to be slow, and
 *  before it is said not to have opened. */
const APP_SLOW_MS = 6_000;
const APP_GONE_MS = 30_000;

/** What the reader asks a Lemma when its app didn't open. */
export function appTrouble(label: string): string {
    return `${label} didn’t open: nothing came back from it in 30 seconds. Can you look at what’s wrong with it?`;
}

/** An app opening in the frame, over it until the frame answers (design.md,
 *  "States"): nothing for the first moment, then its mark and a thin bar;
 *  slow after a few seconds, with its own tab offered; and after 30 seconds
 *  with nothing back, said plainly, with Try again and the Lemma asked to
 *  look. Without `url` -- the app still being found -- it only says it is
 *  opening. */
export function AppOpening({ name, label, lemma, url, onAsk, onRetry }: {
    name: string;
    label: string;
    /** The Lemma it is in, by name. */
    lemma: string;
    url?: string | null;
    onAsk?: () => void;
    onRetry?: () => void;
}) {
    const shown = useLasted(true, SHOW_AFTER_MS);
    const waits = Boolean(url);
    const slow = useLasted(waits, APP_SLOW_MS);
    const gone = useLasted(waits, APP_GONE_MS);
    if (gone) {
        return (
            <div className="app-open" role="alert">
                <span className="kcalm__i" data-tone="warn"><Icon name="warn" size={22} /></span>
                <h2 className="app-open__t">{label} didn’t open</h2>
                <p className="app-open__line">Nothing came back from it in 30 seconds. It may be restarting, or something in it broke.</p>
                <div className="app-open__acts">
                    {onAsk && <button type="button" className="b b--m b--secondary" onClick={onAsk}>Ask {lemma} to look</button>}
                    {onRetry && <TryAgain onClick={onRetry} />}
                </div>
            </div>
        );
    }
    if (!shown) return null;
    const mark = <AppMark name={name} label={label} size={64} className="app-open__app" />;
    if (slow && url) {
        return (
            <div className="app-open" role="status">
                {mark}
                <span className="app-open__bar" aria-hidden="true"><i /></span>
                <h2 className="app-open__t">Still opening {label}</h2>
                <p className="app-open__line">It’s slower to answer than usual. It keeps trying here, or it can open on its own.</p>
                <div className="app-open__acts">
                    <button type="button" className="b b--m b--secondary" onClick={() => openPodApp(url)}>
                        <Icon name="out" size={15} />Open in a new tab
                    </button>
                </div>
            </div>
        );
    }
    return (
        <div className="app-open app-open--quiet" role="status">
            {mark}
            <span className="app-open__bar" aria-hidden="true"><i /></span>
            <p className="app-open__line">Opening {label}</p>
        </div>
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
    const part = isAboutSection(section) ? section : null;
    /* The page's header is every Lemma page's: the way back to it, the
       part's title and its one line. The overview is its Settings. */
    const head = part ? aboutHead(part, pod.name) : { title: "Settings", note: `${pod.name}’s name and job, and everything about how it works.` };
    return (
        <Stage pod={pod} go={go} head={<LemmaPageHead pod={pod} tab="settings" title={head.title} says={head.note} go={go} actions={actions} />}>
            <div className="ox-page ox-section" data-section="settings">
                <AboutPage
                    framed
                    bare
                    pod={pod}
                    orgId={org.id}
                    orgName={org.name}
                    section={part}
                    onSection={(next) => go(outcomeView(pod.id, "settings", next))}
                    onOpenSettings={(next) => actions.openSettings(next)}
                    onAsk={() => go(outcomeView(pod.id))}
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
 *  opened from that chat, else the whole machine. The screen takes the whole
 *  stage, under one bar: where this is, how the machine is, and what opens
 *  beside the screen. */
export function ComputerPage({ pod, conversationId, go }: { pod: Pod; conversationId: string | null; go: Go }) {
    const cache = useQueryClient();
    return (
        <Stage pod={pod} title="Computer" go={go} fill ownBar>
            <div className="pane library-pane">
                <ComputerView
                    podId={pod.id}
                    conversationId={conversationId}
                    visible
                    lead={<StageCrumbs pod={pod} title="Computer" go={go} />}
                    trail={<button type="button" className="ox-btn ox-btn--small" onClick={() => void cache.invalidateQueries({ queryKey: ["computer"] })}>Refresh</button>}
                />
            </div>
        </Stage>
    );
}
