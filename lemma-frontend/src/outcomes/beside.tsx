"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Pod, type Tab } from "@/data";
import { CommentsButton } from "@/docpages/comments/button";
import { ComputerView } from "@/computer/computer-view";
import { RefreshIcon } from "@/ui/icons";
import { AppFrameView } from "@/desktop/app-frame";
import { FileView } from "@/thread/file-view";
import { TableView } from "@/library/library";
import { DocSpace, isDoc } from "@/docs/doc-space";
import { docTitle } from "@/library/doc-title";
import { readableName } from "@/library/reading";
import { registerFrame } from "@/thread/compose-bridge";
import { chatView, type Go, type View } from "./model";
import type { FrameActions } from "./actions";
import { Empty, Icon, to } from "./parts";
import { HeldActions } from "./stage";

/** What a chat has open beside it: a page or file, a table, an app, or the
 *  machine it works on. */
export type Beside =
    | { kind: "file"; path: string }
    | { kind: "table"; name: string }
    | { kind: "app"; name: string }
    | { kind: "computer" };

/** Where the thing beside a chat opens when it is given the whole stage. */
function fullView(podId: string, conversationId: string | null, beside: Beside): View {
    switch (beside.kind) {
        case "file": return { name: "file", podId, path: beside.path };
        case "table": return { name: "table", podId, table: beside.name };
        case "app": return { name: "app", podId, app: beside.name };
        case "computer": return { name: "computer", podId, conversationId };
    }
}

/** The pane beside a chat, as in the workspace: what the chat made or
 *  opened sits next to the conversation that made it, rather than taking
 *  the reader away from it. Its one bar names it, offers what its own page
 *  offers -- download, copy its link, share, comments, reload -- opens it
 *  on that page, or puts it away. Asking about what is in it puts the words
 *  in this chat's box. */
export function BesidePane({ pod, conversationId, beside, onOpen, onClose, go, actions }: {
    pod: Pod;
    /** The chat it is beside; null for one not started yet. */
    conversationId: string | null;
    beside: Beside;
    /** Something it links to, opened in its place. */
    onOpen: (next: Beside) => void;
    onClose: () => void;
    go: Go;
    actions: FrameActions;
}) {
    const apps = useQuery({ queryKey: ["outcome-apps", pod.id], queryFn: () => source.listTabs(pod.id), staleTime: 5 * 60_000, enabled: beside.kind === "app" });
    const app = beside.kind === "app"
        ? (apps.data ?? []).flatMap((tab) => (tab.kind === "app" && tab.id === "app:" + beside.name ? [tab] : []))[0] ?? null
        : null;
    const title = beside.kind === "file" ? docTitle(beside.path)
        : beside.kind === "table" ? readableName(beside.name)
        : beside.kind === "app" ? app?.label ?? readableName(beside.name)
        : "Computer";
    const fill = (text: string) => actions.fill(pod.id, text);
    const cache = useQueryClient();
    /* A new key draws the app afresh: its own Reload. */
    const [reloads, setReloads] = useState(0);
    let held: ReactNode = null;
    if (beside.kind === "file") {
        const tab: Tab = { id: "file:" + beside.path, kind: "file", label: title, path: beside.path };
        held = (
            <>
                {isDoc(beside.path) && !/^\/me(\/|$)/.test(beside.path) && <CommentsButton path={beside.path} />}
                <HeldActions pod={pod} tab={tab} subject={{ kind: "file", path: beside.path, label: title }} actions={actions} />
            </>
        );
    } else if (beside.kind === "table") {
        held = <HeldActions pod={pod} tab={{ id: "table:" + beside.name, kind: "table", label: title, name: beside.name }} subject={{ kind: "table", label: title }} actions={actions} />;
    } else if (beside.kind === "app" && app) {
        held = <HeldActions pod={pod} tab={app} subject={{ kind: "app", label: title }} actions={actions} onReload={() => setReloads((n) => n + 1)} />;
    }
    const ends = (
        <>
            <a className="ox-beside__b" {...to(fullView(pod.id, conversationId, beside), go)} aria-label={`Open ${title} on its own page`} title="Open on its own page">
                <Icon name="out" size={15} />
            </a>
            <button type="button" className="ox-beside__b" onClick={onClose} aria-label={`Close ${title}`} title="Close">
                <Icon name="close" size={15} />
            </button>
        </>
    );
    return (
        <aside className="ox-beside" aria-label={title}>
            {/* The computer has a bar of its own, and these go in it. */}
            {beside.kind !== "computer" && (
                <div className="ox-beside__bar">
                    <span className="ox-beside__t">{title}</span>
                    {held && <div className="ox-beside__acts">{held}</div>}
                    {ends}
                </div>
            )}
            <div className="ox-beside__body">
                {beside.kind === "file" && (isDoc(beside.path) ? (
                    <div className="pane file-tab-pane doc-tab-pane">
                        <DocSpace
                            key={beside.path}
                            pod={pod}
                            path={beside.path}
                            openFile={(path) => onOpen({ kind: "file", path })}
                            openTable={(name) => onOpen({ kind: "table", name })}
                            openConversation={(id) => go(chatView({ kind: "chat", podId: pod.id, id }))}
                            sendToBot={fill}
                        />
                    </div>
                ) : (
                    <div className="pane file-tab-pane">
                        <div className="pane__inner">
                            <FileView key={beside.path} podId={pod.id} path={beside.path} full onOpenTab={(path) => onOpen({ kind: "file", path })} />
                        </div>
                    </div>
                ))}
                {beside.kind === "table" && (
                    <div className="pane library-pane">
                        <TableView
                            key={beside.name}
                            podId={pod.id}
                            name={beside.name}
                            teammate={pod.teammate?.name || pod.name}
                            onOpenRecord={(name, recordId) => go({ name: "record", podId: pod.id, table: name, recordId })}
                            onAsk={fill}
                        />
                    </div>
                )}
                {beside.kind === "app" && (apps.isPending
                    ? <p className="ox-loading">Opening the app…</p>
                    : app ? <BesideApp key={app.id + ":" + reloads} url={app.url} />
                    : <div className="ox-stage__none"><Empty title="This app isn’t here."><p>It may have been removed or renamed.</p></Empty></div>)}
                {beside.kind === "computer" && (
                    <div className="pane library-pane">
                        <ComputerView
                            podId={pod.id}
                            conversationId={conversationId}
                            visible
                            lead={<span className="ox-beside__t ox-beside__t--lead">{title}</span>}
                            trail={(
                                <>
                                    <button type="button" className="ox-beside__b" onClick={() => void cache.invalidateQueries({ queryKey: ["computer"] })} aria-label="Refresh" title="Refresh">
                                        <RefreshIcon size={15} />
                                    </button>
                                    {ends}
                                </>
                            )}
                        />
                    </div>
                )}
            </div>
        </aside>
    );
}

const NOTHING = () => undefined;

/** An app beside a chat, trusted to put words in its box while it is here. */
function BesideApp({ url }: { url: string }) {
    const [loaded, setLoaded] = useState<Window | null>(null);
    useEffect(() => registerFrame(loaded), [loaded]);
    return <AppFrameView url={url} frameRef={NOTHING} onFrameLoad={setLoaded} />;
}
