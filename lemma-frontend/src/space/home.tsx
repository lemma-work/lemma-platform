"use client";

import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type ConversationRef, type Pod } from "@/data";
import { AskBox } from "@/chat/ask-box";
import { gather } from "@/workflow/waiting-inbox";
import { byAge, gatherAsked, sayAsked, type AskedRow } from "@/thread/waiting-on-you";
import { refreshConversationLists } from "@/thread/conversation-list";
import { lemma } from "@/session/client";
import { agoOf } from "@/schedule/schedules";
import { sayStuckFor, sayWaitingOn } from "@/workflow/runs";
import { AppIcon, ChevronRightIcon, CloseIcon, FileIcon, SearchIcon, SlidesIcon, TableIcon } from "@/ui/icons";
import { useMaking } from "./making";
import { TeammateFace } from "./teammate-face";

/** A starter that is a sentence for the teammate rather than a form: the
 *  box fills with the beginning of the ask and the person finishes it. */
const STARTERS: { key: string; title: string; note: string; icon: React.ReactNode; ask?: string }[] = [
    { key: "page", title: "Page", note: "A blank doc to write in", icon: <FileIcon size={20} /> },
    { key: "deck", title: "Presentation", note: "Slides from what is here", icon: <SlidesIcon size={20} />, ask: "Make a presentation about " },
    { key: "table", title: "Table", note: "Track something together", icon: <TableIcon size={20} />, ask: "Set up a table to track " },
    { key: "app", title: "App", note: "A screen for the team", icon: <AppIcon size={20} />, ask: "Build an app that " },
    { key: "research", title: "Research", note: "Look into something", icon: <SearchIcon size={20} />, ask: "Research " },
];

/** Where a conversation began that nobody started from this app: a channel,
 *  a schedule, a workflow's step, a run on its own. What the teammate did
 *  while you were elsewhere. */
const ON_ITS_OWN = new Set(["channel", "notification", "schedule", "workflow", "task", "ask"]);
const RECENT_ROWS = 4;

/** Where a space opens: the teammate whose space it is, what is waiting on
 *  you, what it has been doing without you, a few ways to start something,
 *  and a plain box to ask it. Sending from the box starts the conversation
 *  and moves you into it — Home stays a place you come back to, not a thread
 *  that grows. */
export function Home({ pod, pods, onNewPage, onOpenRun, onOpenConversation, onAbout, onAsk }: {
    pod: Pod;
    pods: Pod[];
    onNewPage: () => Promise<void>;
    onOpenRun: (runId: string, workflowName: string) => void;
    onOpenConversation: (id: string) => void;
    /** The teammate's own page. */
    onAbout: () => void;
    /** Start a conversation with these words, in the Chat tab. */
    onAsk: (text: string) => void;
}) {
    const mate = pod.teammate?.name || pod.name;
    const [fill, setFill] = useState<{ text: string; id: number } | null>(null);
    const asks = useRef(0);

    const cache = useQueryClient();
    /* The same queue the bell's neighbour reads, narrowed to this space. */
    const waiting = useQuery({
        queryKey: ["workflow-waiting", pods.map(each => each.id).join(",")],
        queryFn: () => gather(pods, source.label === "sample", cache),
        enabled: pods.length > 0,
        staleTime: 60_000,
    });
    const owed = (waiting.data?.rows ?? []).filter(row => row.podId === pod.id);
    /* And the conversations here that stopped to ask you something, under the
       key the rail reads. */
    const asking = useQuery({
        queryKey: ["conversation-asks", pods.map(each => each.id).join(",")],
        queryFn: () => gatherAsked(pods, source.label === "sample"),
        enabled: pods.length > 0,
        staleTime: 60_000,
    });
    const { fresh: asked, quiet } = byAge((asking.data ?? []).filter(row => row.podId === pod.id));
    const [showQuiet, setShowQuiet] = useState(false);
    const [putAway, setPutAway] = useState<ReadonlySet<string>>(new Set());
    /* Dismissing archives the conversation: the same "put away" the chat
       list offers, so it leaves this list on every device and is still
       there under Archived. The run behind it is left exactly as it was. */
    const dismiss = async (row: AskedRow) => {
        setPutAway(was => new Set(was).add(row.conversationId));
        try {
            if (source.label !== "sample") await lemma(row.podId).conversations.update(row.conversationId, { is_archived: true }, { pod_id: row.podId });
        } catch {
            setPutAway(was => { const next = new Set(was); next.delete(row.conversationId); return next; });
        } finally {
            void cache.invalidateQueries({ queryKey: ["conversation-asks"] });
            void refreshConversationLists(cache, row.podId);
        }
    };
    const shownAsks = [...asked, ...(showQuiet ? quiet : [])].filter(row => !putAway.has(row.conversationId));
    const quietLeft = quiet.filter(row => !putAway.has(row.conversationId)).length;

    /* The list the sidebar's Chats already read, under its key. */
    const chats = useQuery({
        queryKey: ["conversations", pod.id],
        queryFn: () => source.listConversations(pod.id),
        staleTime: 60_000,
    });
    const recent = (chats.data ?? []).filter(chat => chat.origin && ON_ITS_OWN.has(chat.origin.kind)).slice(0, RECENT_ROWS);
    const open = (chat: ConversationRef) => {
        if (chat.origin?.kind === "workflow" && chat.origin.runId) onOpenRun(chat.origin.runId, "Workflow run");
        else onOpenConversation(chat.id);
    };

    const maker = useMaking();
    const start = (key: string, ask?: string) => {
        if (key === "page") { void maker.run("page", onNewPage); return; }
        if (!ask) return;
        asks.current += 1;
        setFill({ text: ask, id: asks.current });
    };

    return (
        <div className="home">
            <div className="home__column">
                <header className="home__head">
                    <button className="home__face" onClick={onAbout} title={"About " + pod.name} aria-label={"About " + pod.name}>
                        <TeammateFace pod={pod} size={60} live />
                    </button>
                    <div className="home__who">
                        <h1>{pod.name}</h1>
                        {pod.description && <p>{pod.description}</p>}
                    </div>
                </header>

                {/* Only when something is. A heading over "nothing" is a
                    section somebody has to read to learn it is empty. */}
                {(owed.length > 0 || shownAsks.length > 0 || quietLeft > 0) && (
                    <section className="home__section" aria-label="Waiting on you">
                        <h2>Waiting on you</h2>
                        <ul className="home__owed">
                            {shownAsks.map(row => (
                                <li key={row.conversationId} className="home__ask">
                                    <button onClick={() => onOpenConversation(row.conversationId)}>
                                        <span className="home__dot" aria-hidden="true" />
                                        <span className="home__owed-text">
                                            <span>{row.title}</span>
                                            <small>{sayAsked(row)} · {agoOf(new Date(row.sinceMs).toISOString())}</small>
                                        </span>
                                        <ChevronRightIcon size={16} />
                                    </button>
                                    <button className="home__dismiss" onClick={() => void dismiss(row)}
                                        title="Dismiss — archives the conversation" aria-label={"Dismiss " + row.title}>
                                        <CloseIcon size={14} />
                                    </button>
                                </li>
                            ))}
                            {owed.map(row => (
                                <li key={row.wait.id}>
                                    <button onClick={() => onOpenRun(row.run.id, row.workflowName)}>
                                        <span className="home__dot" aria-hidden="true" />
                                        <span className="home__owed-text">
                                            <span>{row.workflowName}</span>
                                            <small>{sayWaitingOn(row.wait.type)} · {row.wait.nodeId} · {sayStuckFor(row.wait, row.run)}</small>
                                        </span>
                                        <ChevronRightIcon size={16} />
                                    </button>
                                </li>
                            ))}
                        </ul>
                        {quietLeft > 0 && (
                            <button className="home__older" onClick={() => setShowQuiet(was => !was)}>
                                {showQuiet ? "Hide older" : quietLeft + " older, quiet for over a week"}
                            </button>
                        )}
                    </section>
                )}

                {recent.length > 0 && (
                    <section className="home__section" aria-label="Recently">
                        <h2>Recently</h2>
                        <ul className="home__recent">
                            {recent.map(chat => (
                                <li key={chat.id}>
                                    <button onClick={() => open(chat)}>
                                        <span className="home__recent-title">{chat.title}</span>
                                        <small>{chat.origin?.label}</small>
                                        <time>{chat.at}</time>
                                    </button>
                                </li>
                            ))}
                        </ul>
                    </section>
                )}

                <section className="home__section" aria-label="Start something">
                    <h2>Start something</h2>
                    <div className="home__starters">
                        {STARTERS.map(starter => (
                            <button key={starter.key} className="home__starter" disabled={maker.busy === starter.key} onClick={() => start(starter.key, starter.ask)}>
                                <span className="home__starter-icon">{starter.icon}</span>
                                <span className="home__starter-title">{maker.busy === starter.key ? "Making…" : starter.title}</span>
                                <small>{starter.note}</small>
                            </button>
                        ))}
                    </div>
                    {maker.error && (
                        <p className="all__error" role="alert">
                            Couldn’t make that page. {maker.error}
                            <button onClick={maker.clear} aria-label="Dismiss">×</button>
                        </p>
                    )}
                </section>
            </div>

            <div className="home__chat" data-tour="ask">
                <AskBox placeholder={"Ask " + mate + "…"} fill={fill} onFilled={() => setFill(null)} onAsk={onAsk} />
            </div>
        </div>
    );
}
