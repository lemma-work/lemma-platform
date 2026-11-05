"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient, type InfiniteData, type QueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { source } from "@/data";
import { pollWhenDown, useLiveStatus, useLiveTopic } from "@/live/pod-live";
import { usePanelFocus } from "@/ui/focus-trap";
import { BellIcon, CloseIcon } from "@/ui/icons";
import { ClampedProse } from "@/thread/clamped-prose";
import { channelApi } from "./api";
import { openAtLine } from "./focus";
import { FormAnswer } from "./form-answer";
import { readInboxEvent, withChannelUnread, withInboxTotals } from "./live";
import { channelTitle, sinceShort, type Channel, type Page } from "./model";
import { problemOf } from "./problem";
import { allChannels, answeredKeys, inboxKey, useChannelList } from "./queries";
import {
    bellCount,
    choicesOf,
    formTarget,
    moveFor,
    whereAsked,
    type Inbox,
    type WaitingQuestion,
} from "./waiting";

export { inboxKey };

/** The reader's own channel, for its unread lines in the bell. */
const DIRECT = { kind: "DIRECT", archived: false } as const;

/** What is new for you in this pod: the badges, the questions waiting on
 *  you and the unread lines naming you. Read once and kept by the socket's
 *  `inbox` topic; read again on a poll only while the socket is down. */
export function useInbox(podId: string, enabled = true) {
    const status = useLiveStatus(podId);
    return useQuery({
        queryKey: inboxKey(podId),
        enabled,
        queryFn: (): Promise<Inbox> => channelApi(podId).inbox(),
        staleTime: 30_000,
        refetchOnWindowFocus: true,
        refetchInterval: pollWhenDown(status, 60_000),
    });
}

/** The `inbox` topic, applied: counts are absolute, so each event is the
 *  truth for its channel and the totals; a question new to the reader is
 *  read with the inbox again, so it arrives answerable. Mounted once per
 *  pod, by the bell. */
export function useInboxLive(podId: string): void {
    const cache = useQueryClient();
    const later = useRef<ReturnType<typeof setTimeout> | null>(null);
    useEffect(() => () => { if (later.current) clearTimeout(later.current); }, []);
    const reread = () => void cache.invalidateQueries({ queryKey: inboxKey(podId) });
    useLiveTopic(podId, "inbox", {}, {
        /* Subscribed first, then read: events that arrived meanwhile are
           absolute, so the read and the events agree whichever lands last. */
        onSubscribed: reread,
        onResync: reread,
        onEvents: (events) => {
            for (const raw of events) {
                const event = readInboxEvent(raw);
                if (!event) continue;
                if (event.type === "question.waiting") { reread(); continue; }
                cache.setQueryData<Inbox>(inboxKey(podId), (was) => was ? withInboxTotals(was, event.totals) : was);
                patchUnread(cache, podId, event.channelId, event.unread);
                /* A mention listed may have been read; a list's previews are
                   not in the event. Both are read again, once the burst is over. */
                if (later.current) clearTimeout(later.current);
                later.current = setTimeout(() => {
                    reread();
                    void cache.invalidateQueries({ queryKey: ["channel", podId, "list"] });
                }, 1_500);
            }
        },
    });
}

function patchUnread(cache: QueryClient, podId: string, channelId: string, unread: Channel["unread"]): void {
    cache.setQueriesData<InfiniteData<Page<Channel>>>({ queryKey: ["channel", podId, "list"] }, (was) => was
        ? { ...was, pages: was.pages.map((page) => ({ ...page, items: withChannelUnread(page.items, channelId, unread) })) }
        : was);
}

/** The reader's own channel with the pod, whose new lines the bell -- and
 *  the sidebar's badge, which counts what the bell does -- count. */
export function useMyChannel(podId: string): Channel | null {
    const direct = useChannelList(podId, DIRECT);
    return allChannels(direct.data)[0] ?? null;
}

/** The bell is the inbox: the questions waiting on you, the lines naming
 *  you, and what is new in your own channel -- each opening its channel, and
 *  each question answered where it is. Holds the keyboard while open: Escape
 *  closes it and gives focus back to the bell. */
export function WaitingBell({ podId }: { podId: string }) {
    const [open, setOpen] = useState(false);
    /* In a portal, placed from the bell: the header clips its overflow, which
       would cut the panel off at its bottom edge. */
    const [at, setAt] = useState<{ top: number; right: number } | null>(null);
    const panel = useRef<HTMLDivElement | null>(null);
    const bell = useRef<HTMLButtonElement | null>(null);
    const inbox = useInbox(podId);
    useInboxLive(podId);
    const mine = useMyChannel(podId);
    const count = bellCount(inbox.data ?? null, mine?.unread.lines ?? 0);

    useEffect(() => {
        if (!open) { setAt(null); return; }
        const place = () => {
            const rect = bell.current?.getBoundingClientRect();
            if (!rect) return;
            setAt({ top: rect.bottom + 8, right: Math.max(16, window.innerWidth - rect.right) });
        };
        place();
        const away = (event: MouseEvent) => {
            const target = event.target as Node;
            if (panel.current?.contains(target) || bell.current?.contains(target)) return;
            setOpen(false);
        };
        document.addEventListener("mousedown", away);
        window.addEventListener("resize", place);
        window.addEventListener("scroll", place, true);
        return () => {
            document.removeEventListener("mousedown", away);
            window.removeEventListener("resize", place);
            window.removeEventListener("scroll", place, true);
        };
    }, [open]);

    return (
        <div className="notify">
            <button
                ref={bell}
                className="icon-button notify__bell"
                data-tour="needs"
                title={count.said}
                aria-label={count.badge ? "Inbox, " + count.said : "Inbox"}
                aria-expanded={open}
                aria-haspopup="dialog"
                onClick={() => setOpen(was => !was)}
            >
                <BellIcon size={19} />
                {count.badge && <span className="notify__badge" aria-hidden="true">{count.badge}</span>}
            </button>

            {open && at && createPortal(
                <InboxPanel
                    podId={podId}
                    inbox={inbox}
                    mine={mine}
                    at={at}
                    panel={panel}
                    onClose={() => setOpen(false)}
                />,
                document.body,
            )}
        </div>
    );
}

export function InboxPanel({ podId, inbox, mine, at, panel, onClose }: {
    podId: string;
    inbox: ReturnType<typeof useInbox>;
    mine: Channel | null;
    at: { top: number; right: number };
    panel: RefObject<HTMLDivElement | null>;
    onClose: () => void;
}) {
    const keys = usePanelFocus(panel, onClose);
    const items = inbox.data?.items ?? [];
    const mentioned = inbox.data?.mentioned ?? [];
    const directNew = mine?.unread.lines ?? 0;
    const open = (channelId: string, lineId?: string, threadRootId: string | null = null) => {
        onClose();
        openAtLine(podId, channelId, { lineId: lineId ?? "", threadRootId });
    };
    const nothing = !inbox.isPending && !inbox.isError && items.length === 0 && mentioned.length === 0 && directNew === 0;
    return (
        <div
            className="notify__panel"
            role="dialog"
            aria-label="Inbox"
            aria-modal="false"
            ref={panel}
            tabIndex={-1}
            onKeyDown={keys}
            style={{ top: at.top, right: at.right }}
        >
            <header className="notify__head">
                <h2>Inbox</h2>
                <button className="icon-button" aria-label="Close the inbox" onClick={onClose}>
                    <CloseIcon size={16} />
                </button>
            </header>
            {inbox.isPending && <p className="notify__quiet" role="status">Reading…</p>}
            {inbox.isError && (
                <p className="notify__quiet" role="alert">
                    Couldn’t read your inbox. <button onClick={() => void inbox.refetch()}>Try again</button>
                </p>
            )}
            {nothing && <p className="notify__quiet">Nothing new for you here.</p>}
            {mine && directNew > 0 && (
                <section className="notify__group" aria-label="Sent to you">
                    <button type="button" className="notify__open" onClick={() => open(mine.id)}>
                        <span>{channelTitle(mine, "")}</span>
                        <small>{directNew === 1 ? "1 new message" : (directNew > 99 ? "99+" : directNew) + " new messages"}</small>
                    </button>
                </section>
            )}
            {items.length > 0 && (
                <section className="notify__group" aria-labelledby="inbox-waiting">
                    <h3 id="inbox-waiting" className="notify__label">Waiting on you</h3>
                    <div className="notify__list">
                        {items.map(question => (
                            <WaitingRow key={question.id} podId={podId} question={question} onOpen={() => open(question.channelId, question.id, question.threadRootId)} />
                        ))}
                    </div>
                </section>
            )}
            {mentioned.length > 0 && (
                <section className="notify__group" aria-labelledby="inbox-mentions">
                    <h3 id="inbox-mentions" className="notify__label">Mentions</h3>
                    <ul className="notify__mentions">
                        {mentioned.map((item) => (
                            <li key={item.id}>
                                <button type="button" className="notify__open" onClick={() => open(item.channelId, item.id, item.threadRootId)}>
                                    <span>{item.from} <small>{item.channelKind === "DIRECT" ? "sent to you" : "in " + (item.channelTitle ?? "a shared chat")} · {sinceShort(item.at)}</small></span>
                                    {item.text && <small className="notify__said">{item.text.replace(/\s+/g, " ").slice(0, 140)}</small>}
                                </button>
                            </li>
                        ))}
                    </ul>
                </section>
            )}
        </div>
    );
}

/** One question, and the way to answer it. `bare` on a channel's timeline,
 *  where the line above it already says who asked, and where. */
export function WaitingRow({ podId, question, bare = false, onOpen }: { podId: string; question: WaitingQuestion; bare?: boolean; onOpen?: () => void }) {
    const cache = useQueryClient();
    const [text, setText] = useState("");
    const [problem, setProblem] = useState<string | null>(null);
    const sample = source.label === "sample";
    const move = moveFor(question);
    const target = formTarget(question);

    const answer = useMutation({
        mutationFn: async (given: Record<string, unknown>) => {
            if (sample) throw new Error("This is the sample source — connect a session to answer anything.");
            return lemma(podId).channels.answer(podId, question.channelId, question.id, given);
        },
        onSuccess: () => {
            setProblem(null);
            void cache.invalidateQueries({ queryKey: inboxKey(podId) });
            /* Only the channel it was asked in: the line moves, nothing else does. */
            for (const key of answeredKeys(podId, question)) void cache.invalidateQueries({ queryKey: key });
        },
        /* The server's sentence -- "Somebody is answering this already" -- is
           the useful one; it is said as given. */
        onError: (failure) => setProblem(problemOf(failure, "Couldn’t send your answer.")),
    });

    return (
        <article className="notify__item" data-unread="" data-bare={bare || undefined}>
            {!bare && <>
                <h3>{question.from}</h3>
                <p className="notify__delivery">
                    {onOpen ? <button type="button" className="linkish" onClick={onOpen}>{whereAsked(question)}</button> : whereAsked(question)}
                </p>
                {question.text && <div className="notify__body"><ClampedProse text={question.text} /></div>}
            </>}

            {move === "choose" && (
                <div className="notify__choices" role="group" aria-label="Answers">
                    {choicesOf(question).map(choice => (
                        <button
                            key={choice}
                            className="notify__act"
                            disabled={answer.isPending}
                            onClick={() => answer.mutate({ choice })}
                        >
                            {choice}
                        </button>
                    ))}
                </div>
            )}

            {move === "approve" && (
                <div className="notify__choices" role="group" aria-label="Decision">
                    <button className="btn btn--primary" disabled={answer.isPending} onClick={() => answer.mutate({ decision: "APPROVE_ONCE" })}>
                        Approve
                    </button>
                    <button className="notify__act" disabled={answer.isPending} onClick={() => answer.mutate({ decision: "DENY" })}>
                        Deny
                    </button>
                </div>
            )}

            {move === "form" && target && (
                <FormAnswer
                    podId={podId}
                    runId={target.runId}
                    nodeId={target.nodeId}
                    onDone={() => void cache.invalidateQueries({ queryKey: inboxKey(podId) })}
                />
            )}

            {(move === "write" || move === "choose") && (
                <form
                    className="notify__answer"
                    onSubmit={(event) => {
                        event.preventDefault();
                        if (!text.trim()) return;
                        answer.mutate({ text: text.trim() });
                    }}
                >
                    <input
                        aria-label={"Answer " + question.from}
                        placeholder={move === "choose" ? "Or write your own" : "Your answer"}
                        value={text}
                        onChange={(event) => setText(event.target.value)}
                    />
                    <button className="btn btn--primary" type="submit" disabled={answer.isPending || !text.trim()}>
                        {answer.isPending ? "Sending…" : "Send"}
                    </button>
                </form>
            )}

            {problem && <p className="notify__problem" role="alert">{problem}</p>}
        </article>
    );
}
