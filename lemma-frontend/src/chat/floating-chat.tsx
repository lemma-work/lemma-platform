"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { NEW_CONVERSATION, type Pod } from "@/data";
import { lemma } from "@/session/client";
import { LiveConversation } from "@/thread/live-conversation";
import { ConversationPane } from "@/thread/conversation";
import {
    ATTACHED_FILE_KEY,
    findQuery,
    RESOURCE_KEY,
    resourceInstructions,
    resourceKey,
    resourceTitle,
    type ResourceKind,
} from "@/thread/resource-conversation";
import { Mark } from "@/shell/mark";
import { AppIcon, ChatIcon, CloseIcon, ExpandIcon, FileIcon, MinimizeIcon, TableIcon } from "@/ui/icons";
import { quoted } from "@/docs/doc-ask";

/** What is on the stage, which the chat is about unless told otherwise. */
export type ChatResource = { kind: ResourceKind; name: string; label: string };

const NOUN: Partial<Record<ResourceKind, string>> = { file: "Doc", app: "App", table: "Table" };

function instructionsFor(resource: ChatResource): string {
    if (resource.kind === "file") {
        return (
            "This conversation is attached to the doc `" + resource.name + "` in this pod, and the person has it open beside you, waiting on the page. " +
            "When they ask for a change without naming where, it is a change to that doc. Make it with `pod_edit_file` (the text to replace and what goes there) — " +
            "not by downloading, rewriting or re-uploading the file — keep everything they did not ask about exactly as it is, and say in a sentence what you changed. " +
            "A passage they quote is the part they mean. " +
            "The doc is markdown the page editor reads as blocks, so keep its conventions: a line holding only a link to a pod path " +
            "(`[Title](/pages/…/Child.md)`) is a sub-page or an attached file, `![alt](/path)` is an image, `- [ ]` / `- [x]` are to-dos. " +
            "A line like `⟦… is writing: …⟧` marks where you were asked to write — replace that whole line with what you write. " +
            "The words after `is writing:` are the person's own, typed fast: take their plainest, most common reading and do not search for another. " +
            "If the subject is in this doc, the doc is the source — do not go looking in tables too; if it is not, try the pod's tables; if it is about the world (news, scores, prices), look it up on the web. " +
            "If it is genuinely ambiguous, pick the likeliest reading and name it in one clause of your reply.\n\n" +
            "A ```lemma-widget fenced block is HTML drawn live in the page (a chart, a diagram, a small tool). What it needs, and all it needs — " +
            "do not load the lemma-widget skill, which describes chat widgets, a different thing: " +
            "one self-contained HTML fragment with inline <style> and <script>; its data written into it, because it cannot reach the pod or the SDK when it is viewed; " +
            "colours from the page theme as `var(--lemma-widget-text, #1f1f1f)`, `--lemma-widget-muted`, `--lemma-widget-border`, `--lemma-widget-surface`, `--lemma-widget-accent` " +
            "and `--lemma-widget-chart-1`…`5`, each with a fallback; the page's width, so fluid layout and an SVG with a viewBox; any height, the frame follows it; " +
            "no backtick characters anywhere inside it, or the fence breaks. Draw charts in SVG by hand rather than loading a library: put the data in a small array in the block's own <script> and let a few lines of that script lay it out — never compute coordinates beforehand in Python or anywhere else. " +
            "Lead with the finding in one line, then the chart; keep it compact, but there is no size limit to trim toward. " +
            "Compose it once, directly in the `pod_edit_file` call — do not draft it in a workspace file first — and trust that call's result: " +
            "no reading the doc back, no screenshots, no rendering, no pixel checks. " +
            "The person is looking at the page, so there is nothing to display and nothing to note in memory for a change like this. " +
            "`window.lemma.compose(text)` puts a message in this chat if a button should ask a follow-up.\n\n" +
            "A ```lemma-view fenced block is a live view of pod tables: an optional `-- Title` line, then one read-only SELECT (joins allowed); " +
            "write one when they want rows, counts or a breakdown from a table rather than copying numbers into the text, and drop any `-- spec:` line when you change its SQL."
        );
    }
    return resourceInstructions(resource.kind, resource.name) +
        " The person has it open beside you while you talk.";
}

/** Open, minimised, and anything waiting to go into the composer.
 *
 *  Held by the shell rather than inside the chat, because the doc editor asks
 *  through it too: a selection handed over while the chat is minimised has to
 *  open it and land in the composer. */
export function useFloatingChat() {
    const [open, setOpen] = useState(false);
    const [fill, setFill] = useState<{ text: string; id: number } | null>(null);
    /** Words to send as soon as the chat is up — asked for from the page
     *  itself ("Ask for change", "write here"), not typed in the box. */
    const [pending, setPending] = useState<{ text: string; id: number } | null>(null);
    const asks = useRef(0);
    const ask = useCallback((passage: string) => {
        setOpen(true);
        asks.current += 1;
        setFill({ text: quoted(passage), id: asks.current });
    }, []);
    const send = useCallback((text: string) => {
        setOpen(true);
        asks.current += 1;
        setPending({ text, id: asks.current });
    }, []);
    const clearPending = useCallback(() => setPending(null), []);
    return { open, setOpen, fill, clearFill: () => setFill(null), ask, send, pending, clearPending };
}
export type FloatingChatState = ReturnType<typeof useFloatingChat>;

/** The resource's standing conversation, found and never created here: it
 *  begins on the first message, carrying `createWith`, so opening forty docs
 *  does not leave forty empty conversations behind. */
function useResourceThread(podId: string, resource: ChatResource | null, live: boolean) {
    return useQuery({
        queryKey: ["resource-thread", podId, resource?.kind, resource?.name],
        enabled: live && resource !== null,
        staleTime: 60_000,
        queryFn: async () => {
            if (!resource) return null;
            type Found = { id: string; instructions?: string | null; metadata?: Record<string, unknown> | null };
            const found = await lemma(podId).request<{ items?: Found[] }>(
                "GET",
                `/pods/${podId}/conversations`,
                { params: findQuery(resource.kind, resource.name) },
            );
            const thread = found?.items?.[0];
            if (!thread) return null;
            /* A conversation keeps the instructions it began with, so one made
               before they last changed would go on following the old ones.
               Bring it up to date the first time it is opened again. */
            const instructions = instructionsFor(resource);
            const stale = thread.instructions !== instructions ||
                (resource.kind === "file" && thread.metadata?.[ATTACHED_FILE_KEY] !== resource.name);
            if (stale) {
                const metadata = resource.kind === "file" ? { ...thread.metadata, [ATTACHED_FILE_KEY]: resource.name } : thread.metadata ?? undefined;
                await lemma(podId).request("PATCH", `/pods/${podId}/conversations/${thread.id}`, { body: { instructions, metadata } })
                    .catch(() => undefined);
            }
            return thread.id;
        },
    });
}

/** What an empty chat says, about whatever it is attached to. */
function hintFor(resource: ChatResource | null, attached: boolean, space: string): { title: string; body: string } {
    if (!resource || !attached) return { title: "Ask anything", body: "About " + space + ", or something to get done in it." };
    if (resource.kind === "file") return { title: "Ask about this doc", body: "Select a passage to quote it, or say what to change." };
    if (resource.kind === "app") return { title: "Ask about this app", body: "It sees the app you have open." };
    return { title: "Ask about this table", body: "Questions about the rows, or changes to make." };
}

function ResourceGlyph({ kind }: { kind: ResourceKind }) {
    if (kind === "app") return <AppIcon size={15} />;
    if (kind === "table") return <TableIcon size={15} />;
    return <FileIcon size={15} />;
}

export function FloatingChat({ pod, resource, live, state, onOpenFile, onOpenTable, onOpenApp, onExpand }: {
    pod: Pod;
    /** What is open on the stage, or null on a list, where the chat is
     *  simply with the space. */
    resource: ChatResource | null;
    live: boolean;
    state: FloatingChatState;
    onOpenFile?: (path: string) => void;
    onOpenTable?: (name: string) => void;
    onOpenApp?: (name: string) => void;
    /** Open this conversation as the main view, with what it is about beside
     *  it. Offered once the conversation exists. */
    onExpand?: (conversationId: string, resource: ChatResource | null) => void;
}) {
    const key = resource ? resourceKey(resource.kind, resource.name) : "space";
    /* Detaching is per resource: taking a doc off this conversation does not
       take the next app you open off the one after it. */
    const [detached, setDetached] = useState<Record<string, boolean>>({});
    const attached = resource !== null && !detached[key];
    const [made, setMade] = useState<Record<string, string>>({});
    const thread = useResourceThread(pod.id, attached ? resource : null, live);

    const createWith = useMemo(() => attached && resource ? {
        title: resourceTitle(resource.kind, resource.name),
        type: "PROJECT",
        instructions: instructionsFor(resource),
        /* The exact path too: the key is lowercased for matching, and the
           backend reads the doc into each run from this. */
        metadata: resource.kind === "file" ? { [RESOURCE_KEY]: key, [ATTACHED_FILE_KEY]: resource.name } : { [RESOURCE_KEY]: key },
    } : undefined, [attached, key, resource]);

    const threadKey = attached ? key : "general:" + key;
    const conversationId = made[threadKey] ?? (attached ? thread.data : null) ?? NEW_CONVERSATION;

    const hint = hintFor(resource, attached, pod.name);

    if (!state.open) {
        return (
            <button className="fchat-pill" onClick={() => state.setOpen(true)} title={"Ask " + (pod.teammate?.name || pod.name)}>
                <Mark seed={pod.id} name={pod.name} icon={pod.iconUrl} size={22} still />
                <span>Ask</span>
            </button>
        );
    }

    return (
        <section className="fchat" aria-label={"Conversation with " + pod.name}>
            <header className="fchat__head">
                <Mark seed={pod.id} name={pod.name} icon={pod.iconUrl} size={26} still />
                <span className="fchat__title">
                    <span>{pod.teammate?.name || pod.name}</span>
                    <small>{attached && resource ? "On " + resource.label : "In " + pod.name}</small>
                </span>
                {onExpand && conversationId !== NEW_CONVERSATION && (
                    <button className="fchat__close" aria-label="Open full size" title="Open full size, beside it"
                        onClick={() => onExpand(conversationId, attached ? resource : null)}>
                        <ExpandIcon size={16} />
                    </button>
                )}
                <button className="fchat__close" aria-label="Minimise" title="Minimise" onClick={() => state.setOpen(false)}>
                    <MinimizeIcon size={16} />
                </button>
            </header>
            {!resource ? null : attached ? (
                <div className="fchat__context">
                    <span className="fchat__chip" title={resource.name}>
                        <ResourceGlyph kind={resource.kind} />
                        <span className="fchat__chip-name">{resource.label}</span>
                        <span className="fchat__chip-kind">{NOUN[resource.kind] ?? "Open"}</span>
                        <button aria-label={"Talk without " + resource.label} title="Talk without it" onClick={() => setDetached(was => ({ ...was, [key]: true }))}>
                            <CloseIcon size={11} />
                        </button>
                    </span>
                </div>
            ) : (
                <div className="fchat__context">
                    <button className="fchat__attach" onClick={() => setDetached(was => ({ ...was, [key]: false }))}>
                        <ChatIcon size={14} /> Talk about {resource.label}
                    </button>
                </div>
            )}
            <div className="fchat__thread convo-host">
                {live ? (
                    attached && thread.isPending ? (
                        <div className="fchat__wait">Opening…</div>
                    ) : (
                        <LiveConversation
                            key={pod.id + ":" + threadKey + ":" + (attached ? thread.data ?? "new" : "new")}
                            pod={pod}
                            conversationId={conversationId}
                            createWith={createWith}
                            fill={state.fill}
                            onFilled={state.clearFill}
                            autoSend={state.pending}
                            onAutoSent={state.clearPending}
                            onCreated={id => setMade(was => ({ ...was, [threadKey]: id }))}
                            onOpenFile={onOpenFile}
                            onOpenTable={onOpenTable}
                            onOpenApp={onOpenApp}
                            emptyHint={hint}
                        />
                    )
                ) : (
                    <ConversationPane
                        key={pod.id + ":" + threadKey}
                        pod={pod}
                        conversationId={NEW_CONVERSATION}
                        fill={state.fill ?? state.pending}
                        onFilled={() => { state.clearFill(); state.clearPending(); }}
                        onOpenFile={onOpenFile}
                        onOpenTable={onOpenTable}
                        onOpenApp={onOpenApp}
                        emptyHint={hint}
                    />
                )}
            </div>
        </section>
    );
}
