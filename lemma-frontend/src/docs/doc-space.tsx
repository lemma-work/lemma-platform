"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { FileView } from "@/thread/file-view";
import { PageToolsContext, type PageTools } from "@/docpages/page-context";
import { threadsOf } from "@/docpages/comments/model";
import { useComments, useCommentsStatus } from "@/docpages/comments/store";
import { CommentsPanel, type CommentBot } from "@/docpages/comments/panel";
import { setComments, useCommentsEntry } from "@/docpages/comments/toggle";

/** Markdown is a doc; everything else keeps the plain file view. */
export function isDoc(path: string): boolean {
    return /\.(md|markdown)$/i.test(path);
}

type Anchor = { quote: string; quotePrefix: string; quoteSuffix: string };

/** A page on the stage: the page itself, and its comments beside it when
 *  they are open. Everything the page's blocks need from around them — where
 *  to go, whom to ask, the threads to draw — is handed down from here. */
export function DocSpace({ pod, path, openFile, openTable, openConversation, sendToBot }: {
    pod: Pod;
    path: string;
    openFile: (path: string) => void;
    openTable?: (name: string) => void;
    openConversation?: (id: string) => void;
    sendToBot: ((text: string) => void) | null;
}) {
    /* Comments live in one table everyone in the space can read, so a
       personal file gets none: a thread on it would be seen by all. */
    const personal = /^\/me(\/|$)/.test(path);
    const status = useCommentsStatus(pod.id);
    const comments = useComments(pod.id, path, status.data === "ready" && !personal);
    const threads = useMemo(() => threadsOf(comments.data ?? []), [comments.data]);
    /* Open or shut from here or from the Comment button beside Share. */
    const panel = useCommentsEntry(path).open;
    const setPanel = useCallback((open: boolean) => setComments(path, { open }), [path]);
    const [active, setActive] = useState<string | null>(null);
    const [draft, setDraft] = useState<Anchor | "page" | null>(null);
    const [statusSlot, setStatusSlot] = useState<HTMLElement | null>(null);

    const agents = useQuery({ queryKey: ["agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 5 * 60_000 });
    const botName = pod.teammate?.name || pod.name;
    const bots: CommentBot[] = useMemo(() => (agents.data ?? [])
        .filter((row) => !row.broken && !row.takesInput)
        .map((row) => ({
            key: row.front ? "POD_DEFAULT" : row.name,
            label: row.front ? botName : row.label,
            iconUrl: row.front ? pod.teammate?.iconUrl ?? null : row.iconUrl,
            seed: row.front ? pod.id : pod.id + ":" + row.name,
        })), [agents.data, botName, pod.id, pod.teammate?.iconUrl]);

    /* Memoized so `anchors`, and the page tools under it, keep their identity
       while nothing changed — every block view reads that context. */
    const open = useMemo(() => threads.filter((one) => !one.root.resolved), [threads]);
    useEffect(() => { setComments(path, { count: open.length }); }, [path, open.length]);
    const anchors = useMemo(() => open
        .filter((one) => one.root.quote)
        .map((one) => ({ id: one.root.id, quote: one.root.quote, quotePrefix: one.root.quotePrefix, quoteSuffix: one.root.quoteSuffix })),
    [open]);

    const tools: PageTools = useMemo(() => ({
        podId: pod.id,
        path,
        botName,
        openFile,
        openTable,
        statusSlot,
        sendToBot,
        comments: personal ? null : {
            anchors,
            active,
            start: (anchor) => { setPanel(true); setDraft(anchor ?? "page"); },
            focus: (id) => { setActive(id); if (id) setPanel(true); },
        },
    }), [pod.id, path, botName, openFile, openTable, statusSlot, sendToBot, anchors, active, personal, setPanel]);

    const focusEnd = (event: React.MouseEvent<HTMLDivElement>) => {
        const target = event.target as Element;
        if (target.closest(".ProseMirror, button, a, input, textarea, select, [contenteditable], [role=dialog]")) return;
        /* TipTap hangs the editor on its own root, which is the one handle a
           wrapper that does not own the editor can reach it by. */
        const root = event.currentTarget.querySelector<HTMLElement & { editor?: { isEditable: boolean; commands: { focus: (at: "end") => boolean } } }>(".ProseMirror");
        if (!root?.editor?.isEditable) return;
        event.preventDefault();
        root.editor.commands.focus("end");
    };

    return (
        <PageToolsContext.Provider value={tools}>
            <div className={"docspace" + (panel ? " docspace--comments" : "")}>
                <div className="docspace__main">
                    <div className="docspace__bar">
                        <span className="docspace__status" ref={setStatusSlot} />

                    </div>
                    {/* The whole sheet is the page: a click in the space below the
                        last line puts the caret at the end, as in any editor. */}
                    <div className="docspace__page" onMouseDown={focusEnd}>
                        <FileView podId={pod.id} path={path} full />
                    </div>
                </div>
                {panel && !personal && (
                    <CommentsPanel
                        podId={pod.id}
                        path={path}
                        status={status.data}
                        threads={threads}
                        loading={comments.isPending && status.data === "ready"}
                        members={pod.members}
                        bots={bots}
                        draft={draft}
                        active={active}
                        onFocus={setActive}
                        onDraftDone={() => setDraft(null)}
                        onClose={() => { setPanel(false); setActive(null); setDraft(null); }}
                        onOpenConversation={openConversation}
                    />
                )}
            </div>
        </PageToolsContext.Provider>
    );
}
