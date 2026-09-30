"use client";

import { useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { source, type Member } from "@/data";
import { lemma } from "@/session/client";
import { useMe } from "@/session/use-me";
import { useSession } from "@/session/session";
import { isForbidden } from "@/session/auth-state";
import { Mark } from "@/shell/mark";
import { docTitle } from "@/library/doc-title";
import { sayWhen } from "@/workflow/runs";
import { useSchedules } from "@/schedule/queries";
import { CheckIcon, CloseIcon, DeleteIcon } from "@/ui/icons";
import { commentRow, mentionAt, mentionsIn, pendingAsk, type CommentRow, type Mentionable, type Thread } from "./model";
import { addComment, askAgain, deleteComment, enableComments, updateComment, type CommentsStatus } from "./store";
import { canAskAgain, isPaused, wakeFor, wakeRequest } from "./wake";
import { useQuery } from "@tanstack/react-query";

export interface CommentBot { key: string; label: string; iconUrl: string | null; seed: string }

type Anchor = { quote: string; quotePrefix: string; quoteSuffix: string };

/** A page's comments, beside it.
 *
 *  Threads in the order they were started, open ones first. Each is the
 *  words it is about, what people and bots said, and a box to answer in.
 *  Naming a bot with @ asks it — it wakes, reads the page, does the thing,
 *  and answers in the thread; naming a person tells them. */
export function CommentsPanel({ podId, path, status, threads, loading, members, bots, draft, active, onFocus, onDraftDone, onClose, onOpenConversation }: {
    podId: string;
    path: string;
    status: CommentsStatus | undefined;
    threads: Thread[];
    loading: boolean;
    members: Member[];
    bots: CommentBot[];
    draft: Anchor | null | "page";
    active: string | null;
    onFocus: (id: string | null) => void;
    onDraftDone: () => void;
    onClose: () => void;
    onOpenConversation?: (id: string) => void;
}) {
    const cache = useQueryClient();
    const [showResolved, setShowResolved] = useState(false);
    const enable = useMutation({
        mutationFn: () => enableComments(podId),
        onSuccess: () => void cache.invalidateQueries({ queryKey: ["comments-status", podId] }),
    });
    const open = threads.filter((one) => !one.root.resolved);
    const done = threads.filter((one) => one.root.resolved);
    const shown = showResolved ? done : open;

    return (
        <aside className="cpanel" aria-label="Comments">
            <header className="cpanel__head">
                <h2>Comments</h2>
                {status === "ready" && (
                    <div className="cpanel__tabs" role="tablist">
                        <button role="tab" aria-selected={!showResolved} onClick={() => setShowResolved(false)}>Open{open.length ? " " + open.length : ""}</button>
                        <button role="tab" aria-selected={showResolved} onClick={() => setShowResolved(true)}>Resolved{done.length ? " " + done.length : ""}</button>
                    </div>
                )}
                <button className="cpanel__close" aria-label="Close comments" onClick={onClose}><CloseIcon size={15} /></button>
            </header>

            {status === "missing" && (
                <div className="cpanel__setup">
                    <p>Comments aren’t on in this space yet. Turning them on adds one table, <code>doc_comments</code>, that everyone here can read — and that bots can be woken by.</p>
                    <button className="cpanel__primary" disabled={enable.isPending} onClick={() => enable.mutate()}>
                        {enable.isPending ? "Turning on…" : "Turn on comments"}
                    </button>
                    {enable.isError && (
                        <p className="cpanel__problem">
                            {isForbidden(enable.error) ? "Only an editor of this space can turn comments on." : "Couldn’t turn comments on."}
                        </p>
                    )}
                </div>
            )}
            {status === "forbidden" && <p className="cpanel__quiet">You can’t read comments in this space.</p>}

            {status === "ready" && (
                <div className="cpanel__list">
                    {draft && (
                        <div className="cthread cthread--draft">
                            {draft !== "page" && draft.quote && <blockquote className="cthread__quote">{draft.quote}</blockquote>}
                            <Composer
                                podId={podId}
                                path={path}
                                members={members}
                                bots={bots}
                                anchor={draft === "page" ? null : draft}
                                parentId={null}
                                autoFocus
                                placeholder={draft === "page" ? "Comment on this page…" : "Comment, or @ a bot to ask it…"}
                                onDone={(id) => { onDraftDone(); if (id) onFocus(id); }}
                                onCancel={onDraftDone}
                            />
                        </div>
                    )}
                    {loading && <p className="cpanel__quiet">Loading…</p>}
                    {!loading && !draft && shown.length === 0 && (
                        <p className="cpanel__quiet">
                            {showResolved ? "Nothing resolved yet." : "No comments. Select some text and choose Comment — or @ a bot there to ask it to change it."}
                        </p>
                    )}
                    {shown.map((thread) => (
                        <ThreadView key={thread.root.id} podId={podId} path={path} thread={thread} members={members} bots={bots}
                            active={thread.root.id === active} onFocus={() => onFocus(thread.root.id)} onOpenConversation={onOpenConversation} />
                    ))}
                </div>
            )}
        </aside>
    );
}

function ThreadView({ podId, path, thread, members, bots, active, onFocus, onOpenConversation }: {
    podId: string;
    path: string;
    thread: Thread;
    members: Member[];
    bots: CommentBot[];
    active: boolean;
    onFocus: () => void;
    onOpenConversation?: (id: string) => void;
}) {
    const waiting = thread.root.resolved ? null : pendingAsk(thread);
    const resolve = useMutation({ mutationFn: () => updateComment(podId, thread.root.id, { resolved: !thread.root.resolved }) });
    const [replying, setReplying] = useState(false);
    return (
        <article
            className="cthread"
            data-active={active || undefined}
            data-resolved={thread.root.resolved || undefined}
            onClick={() => {
                onFocus();
                document.querySelector(`[data-comment-id="${CSS.escape(thread.root.id)}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
            }}
        >
            {thread.root.quote && <blockquote className="cthread__quote">{thread.root.quote}</blockquote>}
            {[thread.root, ...thread.replies].map((row) => (
                <Message key={row.id} podId={podId} row={row} members={members} bots={bots} />
            ))}
            {waiting && <BotStatus podId={podId} ask={waiting} bots={bots} onOpenConversation={onOpenConversation} />}
            <div className="cthread__acts" onClick={(event) => event.stopPropagation()}>
                {!thread.root.resolved && !replying && <button onClick={() => setReplying(true)}>Reply</button>}
                <button onClick={() => resolve.mutate()} disabled={resolve.isPending}>
                    <CheckIcon size={13} /> {thread.root.resolved ? "Reopen" : "Resolve"}
                </button>
            </div>
            {replying && (
                <div onClick={(event) => event.stopPropagation()}>
                    <Composer podId={podId} path={path} members={members} bots={bots} anchor={null} parentId={thread.root.id}
                        autoFocus placeholder="Reply, or @ a bot…" onDone={() => setReplying(false)} onCancel={() => setReplying(false)} />
                </div>
            )}
        </article>
    );
}

function Message({ podId, row, members, bots }: { podId: string; row: CommentRow; members: Member[]; bots: CommentBot[] }) {
    const me = useMe();
    const remove = useMutation({ mutationFn: () => deleteComment(podId, row.id) });
    const bot = row.authorAgent ? bots.find((one) => one.label.toLowerCase() === row.authorAgent!.toLowerCase() || one.key.toLowerCase() === row.authorAgent!.toLowerCase()) : null;
    const person = !row.authorAgent ? members.find((one) => one.userId && one.userId === row.authorId) : null;
    const mine = !row.authorAgent && row.authorId !== null && row.authorId === me;
    const name = bot?.label ?? row.authorAgent ?? (mine ? "You" : person?.name ?? row.authorName ?? "Someone");
    const known: Mentionable[] = [
        ...bots.map((one) => ({ kind: "agent" as const, label: one.label, key: one.key })),
        ...members.map((one) => ({ kind: "person" as const, label: one.name, key: one.userId ?? one.id })),
    ];
    return (
        <div className="cmsg">
            <span className="cmsg__face">
                {bot ? <Mark seed={bot.seed} name={bot.label} icon={bot.iconUrl} size={22} still /> : <span className="cmsg__initials">{(name[0] ?? "?").toUpperCase()}</span>}
            </span>
            <div className="cmsg__body">
                <p className="cmsg__who"><b>{name}</b><small>{sayWhen(row.createdAt)}</small>
                    {mine && (
                        <button className="cmsg__del" title="Delete" aria-label="Delete comment" onClick={(event) => { event.stopPropagation(); remove.mutate(); }}>
                            <DeleteIcon size={13} />
                        </button>
                    )}
                </p>
                <p className="cmsg__text"><WithMentions body={row.body} known={known} /></p>
            </div>
        </div>
    );
}

/** What the named bot is doing about a comment, read off its wake-up's own
 *  ledger: the firing whose row is this comment, and the conversation that
 *  firing started. Watched every few seconds until the bot answers. */
function BotStatus({ podId, ask, bots, onOpenConversation }: {
    podId: string;
    ask: CommentRow;
    bots: CommentBot[];
    onOpenConversation?: (id: string) => void;
}) {
    const cache = useQueryClient();
    const key = ask.mentionedAgent!;
    const bot = bots.find((one) => one.key.toLowerCase() === key.toLowerCase());
    const name = bot?.label ?? key;
    const schedules = useSchedules(podId);
    const job = wakeFor(schedules.data ?? [], key);
    const runs = useQuery({
        queryKey: ["schedules", podId, job?.id ?? null, "runs", "comment", ask.id],
        enabled: Boolean(job) && source.label === "live",
        queryFn: () => source.listScheduleRuns(podId, job!.id),
        refetchInterval: 4000,
    });
    const run = (runs.data ?? []).find((one) => one.subjectId === ask.id) ?? null;
    const turnOn = useMutation({
        mutationFn: async () => {
            if (!job) await lemma(podId).request("POST", "/pods/" + podId + "/schedules", { body: wakeRequest(key, name) });
            else if (isPaused(job)) await source.setScheduleActive(podId, job.id, true);
            await askAgain(podId, ask.id, key);
        },
        onSuccess: () => void cache.invalidateQueries({ queryKey: ["schedules", podId] }),
    });
    const face = bot ? <Mark seed={bot.seed} name={bot.label} icon={bot.iconUrl} size={18} still /> : null;
    const aged = ask.createdAt ? Date.now() - Date.parse(ask.createdAt) : 0;

    if (source.label !== "live") {
        return <p className="cbot">{face}<span className="cbot__text">{name} answers here once you are signed in.</span></p>;
    }
    if (schedules.isPending) return null;
    if (!job) {
        return (
            <p className="cbot cbot--off" onClick={(event) => event.stopPropagation()}>
                {face}
                <span className="cbot__text">{name} isn’t set to answer comments, so it hasn’t seen this.</span>
                <button className="linkish" disabled={turnOn.isPending} onClick={() => turnOn.mutate()}>
                    {turnOn.isPending ? "Turning on…" : "Let " + name + " answer"}
                </button>
                {turnOn.isError && <em>{isForbidden(turnOn.error) ? "Only an editor can turn that on." : "Couldn’t turn it on."}</em>}
            </p>
        );
    }
    if (isPaused(job) && !run) {
        return (
            <p className="cbot cbot--off" onClick={(event) => event.stopPropagation()}>
                {face}
                <span className="cbot__text">{name}’s comment replies are paused{job.pausedByFailures ? " after repeated failures" : ""}, so it hasn’t seen this.</span>
                <button className="linkish" disabled={turnOn.isPending} onClick={() => turnOn.mutate()}>
                    {turnOn.isPending ? "Turning on…" : "Turn back on"}
                </button>
                {turnOn.isError && <em>{isForbidden(turnOn.error) ? "Only an editor can turn that on." : "Couldn’t turn it on."}</em>}
            </p>
        );
    }
    const failed = run && (run.tone === "bad");
    const working = run && (run.status === "DISPATCHED" || run.status === "PROCESSING" || run.status === "RECEIVED");
    return (
        <p className={"cbot" + (failed ? " cbot--bad" : working || !run ? " cbot--live" : "")} onClick={(event) => event.stopPropagation()}>
            {face}
            <span className="cbot__text">
                {failed ? name + " couldn’t answer" + (run.error ? " — " + run.error : "")
                    : run?.status === "FILTERED" ? name + " passed on this one"
                    : run?.status === "COMPLETED" ? name + " finished — its reply is on the way"
                    : run?.status === "DISPATCHED" ? name + " is working on it…"
                    : run ? name + " is starting…"
                    : aged > 90_000 ? name + " hasn’t picked this up"
                    : name + " will pick this up in a moment…"}
            </span>
            {run?.targetRunId && run.targetKind === "agent" && onOpenConversation && (
                <button className="linkish" onClick={() => onOpenConversation(run.targetRunId)}>Watch</button>
            )}
            {(failed || (!run && aged > 90_000)) && canAskAgain(job) && (
                <button className="linkish" disabled={turnOn.isPending} onClick={() => turnOn.mutate()}>Ask again</button>
            )}
        </p>
    );
}

/** The body, with the names it mentions picked out. */
function WithMentions({ body, known }: { body: string; known: Mentionable[] }) {
    const names = mentionsIn(body, known).map((one) => "@" + one.label);
    if (names.length === 0) return <>{body}</>;
    const pattern = new RegExp("(" + names.map((one) => one.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")", "gi");
    return <>{body.split(pattern).map((part, at) => (at % 2 === 1 ? <span key={at} className="cmsg__mention">{part}</span> : part))}</>;
}

function Composer({ podId, path, members, bots, anchor, parentId, autoFocus, placeholder, onDone, onCancel }: {
    podId: string;
    path: string;
    members: Member[];
    bots: CommentBot[];
    anchor: Anchor | null;
    parentId: string | null;
    autoFocus?: boolean;
    placeholder: string;
    onDone: (id: string | null) => void;
    onCancel: () => void;
}) {
    const me = useMe();
    const { user } = useSession();
    const myName = user?.name || members.find((one) => one.userId && one.userId === me)?.name || user?.email || "Someone";
    const cache = useQueryClient();
    const [body, setBody] = useState("");
    const [caret, setCaret] = useState(0);
    const [pickIndex, setPickIndex] = useState(0);
    const area = useRef<HTMLTextAreaElement>(null);
    const known: Mentionable[] = useMemo(() => [
        ...bots.map((one) => ({ kind: "agent" as const, label: one.label, key: one.key })),
        ...members.filter((one) => one.kind === "person" && one.userId !== me).map((one) => ({ kind: "person" as const, label: one.name, key: one.userId ?? one.email ?? one.id })),
    ], [bots, members, me]);
    const typing = mentionAt(body, caret);
    const choices = typing ? known.filter((one) => one.label.toLowerCase().startsWith(typing.query.toLowerCase())).slice(0, 6) : [];
    const named = mentionsIn(body, known);
    const bot = named.find((one) => one.kind === "agent") ?? null;

    const schedules = useSchedules(podId);
    const botWakes = bot ? wakeFor(schedules.data ?? [], bot.key) : null;
    const wake = useMutation({
        mutationFn: async () => {
            if (botWakes && isPaused(botWakes)) await source.setScheduleActive(podId, botWakes.id, true);
            else await lemma(podId).request("POST", "/pods/" + podId + "/schedules", { body: wakeRequest(bot!.key, bot!.label) });
        },
        onSuccess: () => void cache.invalidateQueries({ queryKey: ["schedules", podId] }),
    });

    const send = useMutation({
        mutationFn: async () => {
            const row = await addComment(podId, commentRow({ filePath: path, anchor, body, parentId, mentions: named }), { id: me, name: myName });
            /* People named are told, wherever they last talked to the space.
               Best effort: the comment stands whether or not a notice went. */
            if (source.label === "live") {
                for (const person of named.filter((one) => one.kind === "person")) {
                    void lemma(podId).notifications.send({
                        recipient: person.key,
                        title: "You were mentioned on " + docTitle(path),
                        body: body.trim(),
                        expects_response: false,
                    } as never).catch(() => undefined);
                }
            }
            return row;
        },
        onSuccess: (row) => {
            setBody("");
            void cache.invalidateQueries({ queryKey: ["comments", podId, path] });
            onDone(row.parentId ?? row.id);
        },
    });

    const choose = (one: Mentionable) => {
        if (!typing) return;
        const next = body.slice(0, typing.start) + "@" + one.label + " " + body.slice(caret);
        const at = typing.start + one.label.length + 2;
        setBody(next);
        setPickIndex(0);
        requestAnimationFrame(() => { area.current?.focus(); area.current?.setSelectionRange(at, at); setCaret(at); });
    };

    return (
        <div className="ccompose" onClick={(event) => event.stopPropagation()}>
            <textarea
                ref={area}
                autoFocus={autoFocus}
                rows={2}
                value={body}
                placeholder={placeholder}
                onChange={(event) => { setBody(event.target.value); setCaret(event.target.selectionStart ?? event.target.value.length); setPickIndex(0); }}
                onSelect={(event) => setCaret((event.target as HTMLTextAreaElement).selectionStart ?? 0)}
                onKeyDown={(event) => {
                    if (choices.length > 0) {
                        if (event.key === "ArrowDown") { event.preventDefault(); setPickIndex((pickIndex + 1) % choices.length); return; }
                        if (event.key === "ArrowUp") { event.preventDefault(); setPickIndex((pickIndex - 1 + choices.length) % choices.length); return; }
                        if (event.key === "Enter" || event.key === "Tab") { event.preventDefault(); choose(choices[pickIndex]); return; }
                    }
                    /* The button is disabled while a send is in flight; Enter must be
                       too, or a second row wakes the bot twice. */
                    if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        if (body.trim() && !send.isPending) send.mutate();
                        return;
                    }
                    if (event.key === "Escape") onCancel();
                }}
            />
            {choices.length > 0 && (
                <div className="ccompose__pick" role="listbox">
                    {choices.map((one, at) => (
                        <button key={one.kind + one.key} role="option" aria-selected={at === pickIndex} onMouseDown={(event) => { event.preventDefault(); choose(one); }}>
                            <span>{one.label}</span><small>{one.kind === "agent" ? "Bot" : "Person"}</small>
                        </button>
                    ))}
                </div>
            )}
            {bot && (
                <p className="ccompose__note">
                    {botWakes && !isPaused(botWakes)
                        ? bot.label + " will read this and answer here."
                        : botWakes
                            ? <>
                                {bot.label}’s comment replies are paused.{" "}
                                {source.label === "live" && (
                                    <button className="linkish" disabled={wake.isPending} onClick={() => wake.mutate()}>
                                        {wake.isPending ? "Turning on…" : "Turn back on"}
                                    </button>
                                )}
                                {wake.isError && <span className="cpanel__problem"> {isForbidden(wake.error) ? "Only an editor can turn that on." : "Couldn’t turn it on."}</span>}
                            </>
                        : schedules.isSuccess
                            ? <>
                                {bot.label} isn’t set to answer comments yet.{" "}
                                {source.label === "live" && (
                                    <button className="linkish" disabled={wake.isPending} onClick={() => wake.mutate()}>
                                        {wake.isPending ? "Turning on…" : "Let " + bot.label + " answer"}
                                    </button>
                                )}
                                {wake.isError && <span className="cpanel__problem"> {isForbidden(wake.error) ? "Only an editor can set that up." : "Couldn’t set that up."}</span>}
                            </>
                            : null}
                </p>
            )}
            <div className="ccompose__acts">
                <button className="ccompose__cancel" onClick={onCancel}>Cancel</button>
                <button className="cpanel__primary" disabled={!body.trim() || send.isPending} onClick={() => send.mutate()}>
                    {send.isPending ? "Sending…" : parentId ? "Reply" : "Comment"}
                </button>
            </div>
            {send.isError && <p className="cpanel__problem">{send.error instanceof Error ? send.error.message : "Couldn’t post that."}</p>}
        </div>
    );
}
