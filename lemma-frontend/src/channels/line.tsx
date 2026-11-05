"use client";

import { useContext, useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMe } from "@/session/use-me";
import { Prose } from "@/thread/markdown";
import { ChatIcon, DeleteIcon, EditIcon } from "@/ui/icons";
import { Avatar } from "./avatar";
import { channelApi } from "./api";
import {
    ASKER_ONLY_NOTE,
    answerSummary,
    asWaitingQuestion,
    channelTitle,
    clockTime,
    isMine,
    personName,
    questionState,
    sayQuestion,
    sayList,
    sourceLabel,
    withheldNote,
    type ChannelDetail,
    type Line,
    type Part,
} from "./model";
import type { Answering } from "./live";
import { MentionablesContext, useMentionPicker } from "./mention-picker";
import { mentionsIn, type Mentionable } from "./mentions";
import { DeliveryStatus, LineParts, LineText } from "./parts";
import { problemOf } from "./problem";
import { lineKeys, useThread } from "./queries";
import { platformName } from "./sources";
import { WaitingRow } from "./waiting-panel";

export { problemOf };

/** Changing what one's own line says: the composer's `@` menu, and the
 *  caret after the last word. Who it names goes with the words only when
 *  that changed -- then with its files and links, which the change keeps. */
function EditBox({ line, pending, problem, onSave, onCancel }: {
    line: Line;
    pending: boolean;
    problem: string | null;
    onSave: (change: { text: string; parts?: Part[] }) => void;
    onCancel: () => void;
}) {
    const people = useContext(MentionablesContext);
    const box = useRef<HTMLTextAreaElement>(null);
    const named = line.parts.filter((part): part is Extract<Part, { type: "mention" }> => part.type === "mention");
    const [draft, setDraft] = useState(line.text ?? "");
    const [picked, setPicked] = useState<Mentionable[]>(() => named.flatMap((part) => part.name ? [{ kind: part.kind, id: part.id, name: part.name }] : []));
    const mention = useMentionPicker({ people, text: draft, setText: setDraft, box, onPicked: (who) => setPicked((was) => [...was, who]) });
    const { setCaret } = mention;
    useEffect(() => {
        const end = box.current?.value.length ?? 0;
        box.current?.focus({ preventScroll: true });
        box.current?.setSelectionRange(end, end);
        setCaret(end);
    }, [setCaret]);

    const save = (event: { preventDefault(): void }) => {
        event.preventDefault();
        const text = draft.trim();
        if (!text || pending) return;
        /* A name with no words to find it by -- named by an id alone -- is
           kept as it was. */
        const kept = named.filter((part) => !part.name);
        const now = mentionsIn(text, picked);
        const key = (one: { kind: string; id: string }) => one.kind + ":" + one.id;
        const before = new Set(named.map(key));
        const after = new Set([...now, ...kept].map(key));
        const renamed = before.size !== after.size || [...after].some((one) => !before.has(one));
        if (!renamed) {
            if (text === line.text) onCancel();
            else onSave({ text });
            return;
        }
        const mentions: Part[] = [
            ...kept,
            ...now.map((one): Part => ({ type: "mention", kind: one.kind, id: one.id, name: picked.find((who) => key(who) === key(one))?.name ?? null })),
        ];
        onSave({ text, parts: [...mentions, ...line.parts.filter((part) => part.type !== "mention")] });
    };

    return (
        <form className="cline__edit" onSubmit={save}>
            {mention.menu}
            <textarea
                ref={box}
                aria-label="Change what you said"
                value={draft}
                rows={Math.min(8, Math.max(2, draft.split("\n").length))}
                {...mention.boxProps}
                onChange={(event) => { mention.changed(event.target.value, event.target.selectionStart); }}
                onKeyDown={(event) => {
                    if (mention.keyDown(event)) return;
                    if (event.key === "Escape") { event.preventDefault(); onCancel(); }
                    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); save(event); }
                }}
            />
            <div className="cline__edit-acts">
                <button type="button" className="linkish" onClick={onCancel}>Cancel</button>
                <button type="submit" className="pill-button" disabled={pending || !draft.trim()}>{pending ? "Saving…" : "Save"}</button>
            </div>
            {problem && <p className="cline__problem" role="alert">{problem}</p>}
        </form>
    );
}

/** One line of a channel: who said it and for whom, where and when, what it
 *  said -- or that it was withheld, or deleted -- and, on a question, the way
 *  to answer it when it waits on the reader. The reader's own lines can be
 *  changed or deleted here, and whoever may change the channel can remove
 *  anybody's; a copy already carried outside stays as sent. */
export function LineView({ podId, channel, line, onThread, mayWrite = true, answering = [], agentName, highlighted = false, visible = true, continued = false, quote = null }: {
    podId: string;
    channel: ChannelDetail;
    line: Line;
    /** The line this answers, when it is not the one just above. */
    quote?: Line | null;
    /** On screen: a thread's summary under its line is kept live while it is. */
    visible?: boolean;
    /** Said by the same person moments after the line above: drawn without
     *  a face or a name, the way a chat groups a run of messages. */
    continued?: boolean;
    /** Open the line's thread. Absent inside a thread, which has no threads. */
    onThread?: (line: Line) => void;
    /** The reader may write here: their own lines can be changed. */
    mayWrite?: boolean;
    /** Agents writing an answer to this line right now. */
    answering?: Answering[];
    agentName?: (agentId: string | null) => string;
    /** Opened from search: drawn out, and scrolled to. */
    highlighted?: boolean;
}) {
    const me = useMe();
    const cache = useQueryClient();
    const [editing, setEditing] = useState(false);
    const [confirming, setConfirming] = useState(false);
    /* Only where this line is held; the socket brings the change too. */
    const reread = () => { for (const key of lineKeys(podId, channel.id, line)) void cache.invalidateQueries({ queryKey: key }); };
    const edit = useMutation({
        mutationFn: (change: { text: string; parts?: Part[] }) => channelApi(podId).edit(channel.id, line.id, change.text, change.parts),
        onSuccess: () => { setEditing(false); reread(); },
    });
    const remove = useMutation({
        mutationFn: () => channelApi(podId).remove(channel.id, line.id),
        onSuccess: () => { setConfirming(false); reread(); },
    });
    const [sharing, setSharing] = useState(false);
    const share = useMutation({
        mutationFn: () => channelApi(podId).share(channel.id, line.id),
        onSuccess: () => { setSharing(false); reread(); },
    });
    const mine = isMine(line, me) && mayWrite;
    /* Who may change the channel removes anybody's line in it -- never edits
       it. The server asks again; a copy already sent outside stays as sent. */
    const removable = mine || (mayWrite && channel.me.canManage);
    const agent = line.sender.kind === "AGENT";
    const withheld = withheldNote(line, me);
    const time = clockTime(line.createdAt);
    /* In a contact's chat everything is with them, by the one way they
       write: "answering", "Contact" and "via Web chat" on every message would
       say only that. A contact on two ways still shows which. */
    const withContact = channel.kind === "CONTACT";
    const oneWay = withContact && new Set(channel.places.map((place) => place.platform)).size <= 1;
    const where = oneWay ? null : sourceLabel(line.sourceId, channel.places);
    const whom = agent && line.onBehalfOf && !withContact ? personName(line.onBehalfOf, me) : null;
    const who = withContact && line.sender.kind === "CONTACT" && !line.sender.name ? channelTitle(channel, "") : personName(line.sender, me);
    /* Said outside, or sent there already -- a Slack channel, a WhatsApp
       group, a contact's inbox -- an edit or a delete changes only what is
       read here: the copy there stays as it is. So an edit is not offered on
       it, and a delete says so before it happens. */
    const sentOut = (line.delivered ?? []).filter((one) => one.status === "SENT");
    const places = [
        /* Said there in the first place: written on Slack, mirrored here. */
        ...(line.sourceId ? [sourceLabel(line.sourceId, channel.places) ?? "the place it was said"] : []),
        ...sentOut.map((one) => sourceLabel(one.sourceId, channel.places) ?? (one.platform ? platformName(one.platform) : "outside")),
    ];
    const outside = places.length ? sayList([...new Set(places)]) : null;
    const editable = mine && line.text !== null && !outside;
    const tools = !line.deleted && !editing && !confirming && (Boolean(onThread) || editable || removable);
    return (
        <li
            className="cline"
            id={"line-" + line.id}
            data-seq={line.seq || undefined}
            data-continued={continued || undefined}
            data-highlight={highlighted || undefined}
            data-agent={agent || undefined}
            data-note={line.membersOnly || undefined}
            data-gone={line.deleted || line.withheld || undefined}
        >
            <span className="cline__gutter" aria-hidden="true">
                {continued ? <time className="cline__stamp" dateTime={line.createdAt}>{time}</time> : <Avatar person={line.sender} />}
            </span>
            <div className="cline__body">
                {!continued && (
                    <div className="cline__head">
                        <span className="cline__who">{who}</span>
                        {whom && whom !== "You" && <span className="cline__for">answering {whom}</span>}
                        {line.sender.kind === "CONTACT" && !withContact && <span className="cline__chip">Contact</span>}
                        {line.membersOnly && <span className="cline__chip cline__chip--note">Only in Lemma</span>}
                        <time className="cline__at" dateTime={line.createdAt}>{time}</time>
                        {where && <span className="cline__at">via {where}</span>}
                    </div>
                )}
                {continued && <span className="sr-only">{who}, {time}</span>}
                {quote && (
                    <button
                        type="button"
                        className="cline__quote"
                        title="Go to the message this answers"
                        onClick={() => document.getElementById("line-" + quote.id)?.scrollIntoView({ block: "center" })}
                    >
                        <span className="cline__quote-who">{personName(quote.sender, me)}</span>
                        <span className="cline__quote-text">{quote.deleted ? "This message was deleted." : (quote.text ?? "").replace(/\s+/g, " ").slice(0, 140)}</span>
                    </button>
                )}

                {line.deleted ? (
                    <p className="cline__gone">{removedNote(line, me)}</p>
                ) : withheld ? (
                    <p className="cline__gone">{withheld}</p>
                ) : editing ? (
                    <EditBox
                        line={line}
                        pending={edit.isPending}
                        problem={edit.isError ? problemOf(edit.error, "Couldn’t change it.") : null}
                        onSave={(change) => edit.mutate(change)}
                        onCancel={() => setEditing(false)}
                    />
                ) : line.text ? (
                    /* An agent writes markdown, as everywhere it answers; a
                       person's line is kept as typed. */
                    agent ? <div className="cline__text cline__text--md"><Prose text={line.text} /></div>
                        : <LineText text={line.text} parts={line.parts} me={me} />
                ) : null}
                {line.editedAt && !line.deleted && !editing && <span className="cline__edited">(edited)</span>}
                {!line.deleted && !withheld && !editing && <LineParts podId={podId} channelId={channel.id} line={line} />}
                {line.canShare && (
                    <div className="cline__private">
                        <span>{ASKER_ONLY_NOTE}</span>
                        {sharing ? (
                            <>
                                <span className="cline__ask-delete">Everyone in this chat will read it.</span>
                                <button type="button" className="cline__act" disabled={share.isPending} onClick={() => share.mutate()}>{share.isPending ? "Sharing…" : "Share"}</button>
                                <button type="button" className="cline__act" onClick={() => setSharing(false)}>Keep private</button>
                            </>
                        ) : (
                            <button type="button" className="cline__act" onClick={() => { share.reset(); setSharing(true); }}>Share with channel</button>
                        )}
                    </div>
                )}
                {share.isError && <p className="cline__problem" role="alert">{problemOf(share.error, "Couldn’t share it.")}</p>}
                {line.delivered && isMine(line, me) && <DeliveryStatus podId={podId} channel={channel} line={line} canRetry={mayWrite} />}

                {line.interaction && <Question podId={podId} channel={channel} line={line} me={me} />}

                {confirming && (
                    <div className="cline__confirm" role="group" aria-label="Delete this message">
                        <span className="cline__ask-delete">
                            {!mine ? "Remove " + personName(line.sender, me) + "’s message for everyone?" : "Delete this message?"}
                            {outside && " It goes from here only; the copy in " + outside + " stays."}
                        </span>
                        <button type="button" className="cline__act cline__act--bad" disabled={remove.isPending} onClick={() => remove.mutate()}>{remove.isPending ? "Deleting…" : "Delete"}</button>
                        <button type="button" className="cline__act" onClick={() => setConfirming(false)}>Keep</button>
                    </div>
                )}
                {remove.isError && <p className="cline__problem" role="alert">{problemOf(remove.error, "Couldn’t delete it.")}</p>}
                {onThread && line.replyCount > 0 && !line.threadRootId && (
                    <ThreadSummary podId={podId} channel={channel} root={line} visible={visible} onOpen={() => onThread(line)} />
                )}
                {answering.map((one) => (
                    /* Not a live region: every piece written would be read out
                       again. Its start and its finished line are said by the
                       timeline's own announcer. */
                    <div key={one.messageId + (one.agentId ?? "")} className="cline__answering">
                        <span className="cline__answering-who">{(agentName?.(one.agentId) ?? "An agent") + " is answering…"}</span>
                        {one.text && <div className="cline__text cline__text--md cline__text--draft"><Prose text={one.text} /></div>}
                    </div>
                ))}
            </div>
            {tools && (
                <div className="cline__tools" role="toolbar" aria-label="Message actions">
                    {onThread && (
                        <button type="button" className="cline__tool" title="Reply in thread" onClick={() => onThread(line)}>
                            <ChatIcon size={17} aria-hidden="true" /><span className="sr-only">Reply</span>
                        </button>
                    )}
                    {editable && (
                        <button type="button" className="cline__tool" title="Edit message" onClick={() => { edit.reset(); setEditing(true); }}>
                            <EditIcon size={17} aria-hidden="true" /><span className="sr-only">Edit</span>
                        </button>
                    )}
                    {removable && (
                        <button type="button" className="cline__tool cline__tool--bad" title={mine ? "Delete message" : "Remove for everyone"} onClick={() => setConfirming(true)}>
                            <DeleteIcon size={17} aria-hidden="true" /><span className="sr-only">Delete</span>
                        </button>
                    )}
                </div>
            )}
        </li>
    );
}

/** When a thread's newest reply was written, the way a chat says it:
 *  today or yesterday at a time, else "on" and the day. */
export function lastReplySaid(iso: string, now = new Date()): string {
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return "";
    const day = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
    const back = Math.round((day(now) - day(at)) / 86_400_000);
    if (back === 0) return "today at " + clockTime(iso);
    if (back === 1) return "yesterday at " + clockTime(iso);
    return "on " + at.toLocaleDateString([], at.getFullYear() === now.getFullYear() ? { day: "numeric", month: "long" } : { day: "numeric", month: "long", year: "numeric" });
}

/** A thread under the line it hangs off: who replied, how many replies, and
 *  when the last came -- one row that opens the thread beside the chat. */
function ThreadSummary({ podId, channel, root, visible, onOpen }: {
    podId: string;
    channel: ChannelDetail;
    root: Line;
    visible: boolean;
    onOpen: () => void;
}) {
    const thread = useThread(podId, channel.id, root.id, visible);
    const seen = new Set<string>();
    const repliers = [...thread.replies].reverse().filter((reply) => {
        const key = reply.sender.kind + ":" + (reply.sender.id ?? reply.sender.name ?? "");
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    }).slice(0, 3);
    const count = Math.max(root.replyCount, thread.replies.length);
    const last = root.lastReplyAt ?? thread.replies.at(-1)?.createdAt ?? null;
    return (
        <button type="button" className="cline__thread" onClick={onOpen}>
            {repliers.length > 0 && (
                <span className="cline__thread-faces" aria-hidden="true">
                    {repliers.map((reply) => <Avatar key={reply.id} person={reply.sender} size={22} />)}
                </span>
            )}
            <span className="cline__thread-count">{count === 1 ? "1 reply" : count + " replies"}</span>
            {last && <span className="cline__thread-last">Last reply {lastReplySaid(last)}</span>}
            <span className="cline__thread-view" aria-hidden="true">View thread</span>
        </button>
    );
}

/** What a deleted line says in its place: who removed it, when that was
 *  not who wrote it. */
function removedNote(line: Line, me: string | null): string {
    if (!line.removedBy) return "This message was deleted.";
    const who = me && line.removedBy.id === me ? "you" : personName(line.removedBy, me);
    return "Removed by " + who + ".";
}

/** The question a line carries: answered here when it waits on the reader,
 *  else where it stands. */
function Question({ podId, channel, line, me }: { podId: string; channel: ChannelDetail; line: Line; me: string | null }) {
    const interaction = line.interaction;
    if (!interaction) return null;
    const state = questionState(interaction, me);
    if (state.kind === "yours") {
        const question = asWaitingQuestion(line, channel, me);
        return question ? (
            <div className="cline__ask">
                {state.answering && <p className="cline__asked">Being answered… If that answer doesn’t arrive, answer it again here.</p>}
                <WaitingRow podId={podId} question={question} bare />
            </div>
        ) : null;
    }
    const said = sayQuestion(state);
    const answer = state.kind === "answered" ? answerSummary(interaction.answer) : null;
    return (
        <p className="cline__asked" data-open={state.kind === "waiting" || undefined}>
            {said}{answer && <>: <span className="cline__answer">{answer}</span></>}
        </p>
    );
}
