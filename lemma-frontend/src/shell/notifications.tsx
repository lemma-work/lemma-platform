"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lemma, siteUrl } from "@/session/client";
import { source } from "@/data";
import { BellIcon, CloseIcon, ExternalIcon } from "@/ui/icons";
import {
    badgeCount,
    conflictNote,
    deliveryNote,
    formTarget,
    isUnread,
    moveFor,
    outcomeOf,
    type Notification,
} from "./notification-state";
import { NotificationForm } from "./notification-form";

/** The things the pod has asked this person for.
 *
 *  They existed and went nowhere: the SDK has had a whole notifications
 *  namespace — list, unread count, mark read, respond, acknowledge — and this
 *  app drew none of it. An agent that stops to ask something, on a surface with
 *  no inbox, is an agent waiting on a question nobody was shown.
 *
 *  Scoped to the pod you are in, because that is what the endpoint is scoped
 *  to. A single inbox across every teammate is a fan-out and its own piece of
 *  work.
 */
export function Notifications({ podId }: { podId: string }) {
    const [open, setOpen] = useState(false);
    /* Where to put the panel, measured from the bell.
     *
     *  In a portal, not as an absolutely positioned child of the bell. That
     *  would put it inside `.head`, and `.head` clips its overflow, because
     *  that is what makes the header's collapse animate to a real height
     *  rather than a guess. The panel is then cut off at the bottom edge of
     *  the header and looks like it is hiding behind it — nothing to do with
     *  z-index, because it is never painted in the first place. */
    const [at, setAt] = useState<{ top: number; right: number } | null>(null);
    const cache = useQueryClient();
    const panel = useRef<HTMLDivElement | null>(null);
    const bell = useRef<HTMLButtonElement | null>(null);
    const sheet = useRef<HTMLDivElement | null>(null);
    const sample = source.label === "sample";

    /* The count is its own request and a much cheaper one, so the bell can be
       right without the list being loaded. The list is fetched when the panel
       opens and not before. */
    const unread = useQuery({
        queryKey: ["notifications", podId, "unread"],
        queryFn: async () => {
            if (sample) {
                const { SAMPLE_NOTIFICATIONS } = await import("@/data/fixtures");
                return SAMPLE_NOTIFICATIONS.filter(isUnread).length;
            }
            const answer = await lemma(podId).notifications.unreadCount();
            return (answer as { count?: number })?.count ?? 0;
        },
        staleTime: 60_000,
        refetchOnWindowFocus: true,
    });

    const listed = useQuery({
        queryKey: ["notifications", podId, "list"],
        queryFn: async () => {
            if (sample) {
                const { SAMPLE_NOTIFICATIONS } = await import("@/data/fixtures");
                return SAMPLE_NOTIFICATIONS as Notification[];
            }
            const answer = await lemma(podId).notifications.list({ limit: 25 });
            return ((answer as { items?: Notification[] })?.items ?? []) as Notification[];
        },
        enabled: open,
        staleTime: 30_000,
    });

    useEffect(() => {
        if (!open) { setAt(null); return; }
        /* Right-aligned with the bell — what an absolutely positioned child
           would get for free, and the price of the portal. Recomputed while it
           is open because the header can collapse underneath it. */
        const place = () => {
            const rect = bell.current?.getBoundingClientRect();
            if (!rect) return;
            setAt({ top: rect.bottom + 8, right: Math.max(16, window.innerWidth - rect.right) });
        };
        place();
        const away = (event: MouseEvent) => {
            const target = event.target as Node;
            /* Both, now that the panel is no longer inside the bell's box. */
            if (panel.current?.contains(target) || sheet.current?.contains(target)) return;
            setOpen(false);
        };
        const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
        document.addEventListener("mousedown", away);
        document.addEventListener("keydown", escape);
        window.addEventListener("resize", place);
        window.addEventListener("scroll", place, true);
        return () => {
            document.removeEventListener("mousedown", away);
            document.removeEventListener("keydown", escape);
            window.removeEventListener("resize", place);
            window.removeEventListener("scroll", place, true);
        };
    }, [open]);

    const refresh = () => {
        void cache.invalidateQueries({ queryKey: ["notifications", podId] });
    };

    const markAllRead = useMutation({
        /* Sample mode has nothing to tell; everything up to the act is real
           there, and the act is where it stops. */
        mutationFn: async () => (sample ? undefined : lemma(podId).notifications.markAllRead()),
        onSuccess: refresh,
    });

    const count = badgeCount(unread.data ?? 0);
    const items = listed.data ?? [];

    return (
        <div className="notify" ref={panel}>
            <button
                ref={bell}
                className="icon-button notify__bell"
                title={count ? count + " unread" : "Notifications"}
                aria-label={count ? "Notifications, " + count + " unread" : "Notifications"}
                aria-expanded={open}
                onClick={() => setOpen(was => !was)}
            >
                <BellIcon size={19} />
                {count && <span className="notify__badge" aria-hidden="true">{count}</span>}
            </button>

            {open && at && createPortal((
                <div
                    className="notify__panel"
                    role="dialog"
                    aria-label="Notifications"
                    ref={sheet}
                    style={{ top: at.top, right: at.right }}
                >
                    <header className="notify__head">
                        <h2>Asked of you</h2>
                        <div>
                            {items.some(isUnread) && (
                                <button
                                    className="notify__all"
                                    disabled={markAllRead.isPending}
                                    onClick={() => markAllRead.mutate()}
                                >
                                    Mark all read
                                </button>
                            )}
                            <button className="icon-button" aria-label="Close" onClick={() => setOpen(false)}>
                                <CloseIcon size={16} />
                            </button>
                        </div>
                    </header>

                    {listed.isPending && <p className="notify__quiet" role="status">Reading…</p>}
                    {listed.isError && (
                        <p className="notify__quiet" role="alert">
                            Couldn’t load notifications. <button onClick={() => void listed.refetch()}>Try again</button>
                        </p>
                    )}
                    {!listed.isPending && !listed.isError && items.length === 0 && (
                        <p className="notify__quiet">Nothing is waiting on you here.</p>
                    )}

                    <div className="notify__list">
                        {items.map(one => (
                            <Row key={one.id} podId={podId} notification={one} sample={sample} onChanged={refresh} />
                        ))}
                    </div>
                </div>
            ), document.body)}
        </div>
    );
}

function Row({
    podId,
    notification,
    sample,
    onChanged,
}: {
    podId: string;
    notification: Notification;
    /** The sample source has no backend to answer to, so acting says so
     *  instead of pretending. Everything up to the act is real. */
    sample: boolean;
    onChanged: () => void;
}) {
    const [answer, setAnswer] = useState("");
    const [writing, setWriting] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const move = moveFor(notification);
    const delivery = deliveryNote(notification);
    const outcome = outcomeOf(notification);

    async function act(run: () => Promise<unknown>) {
        if (sample) {
            setProblem("This is the sample source — connect a session to answer anything.");
            return;
        }
        setBusy(true);
        setProblem(null);
        try {
            await run();
            onChanged();
        } catch (failure) {
            /* A 409 here is one of two real things rather than a fault: it is
               answered by completing its own form, or somebody else answered it
               first. Both deserve a sentence. */
            const status = (failure as { statusCode?: number } | null)?.statusCode;
            setProblem(status === 409 ? conflictNote(notification) : failure instanceof Error ? failure.message : "Couldn’t send your response.");
        } finally {
            setBusy(false);
        }
    }

    return (
        <article className="notify__item" data-unread={isUnread(notification) ? "" : undefined}>
            <h3>{notification.title}</h3>
            {notification.body && <p className="notify__body">{notification.body}</p>}
            {delivery && <p className="notify__delivery">{delivery}</p>}
            {outcome && <p className="notify__outcome">{outcome}</p>}

            {/* The form, where the ask arrived. Sending somebody to the platform
                for it was a fair fallback and a poor answer — the point of an
                inbox is that what was asked of you can be done where you were
                told about it. */}
            {move === "form" && (() => {
                const target = formTarget(notification);
                return target ? (
                    <NotificationForm
                        podId={podId}
                        runId={target.runId}
                        nodeId={target.nodeId}
                        onDone={onChanged}
                    />
                ) : null;
            })()}


            {move === "elsewhere" && (
                <a
                    className="notify__act"
                    href={`${siteUrl()}/pod/${encodeURIComponent(podId)}/notifications`}
                    target="_blank"
                    rel="noreferrer"
                >
                    Open its form <ExternalIcon size={14} />
                </a>
            )}

            {move === "acknowledge" && (
                <button
                    className="notify__act"
                    disabled={busy}
                    onClick={() => void act(() => lemma(podId).notifications.acknowledge(notification.id))}
                >
                    {busy ? "Dismissing…" : "Dismiss"}
                </button>
            )}

            {move === "answer" && (writing ? (
                <form
                    className="notify__answer"
                    onSubmit={(event) => {
                        event.preventDefault();
                        if (!answer.trim()) return;
                        void act(() => lemma(podId).notifications.respond(notification.id, { summary: answer.trim() }));
                    }}
                >
                    <input
                        autoFocus
                        aria-label={"Answer: " + notification.title}
                        placeholder="Your answer"
                        value={answer}
                        onChange={(event) => setAnswer(event.target.value)}
                        onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); setWriting(false); } }}
                    />
                    <button className="btn btn--primary" type="submit" disabled={busy || !answer.trim()}>
                        {busy ? "Sending…" : "Send"}
                    </button>
                </form>
            ) : (
                <button className="notify__act" onClick={() => setWriting(true)}>Answer</button>
            ))}

            {problem && <p className="notify__problem" role="alert">{problem}</p>}
        </article>
    );
}
