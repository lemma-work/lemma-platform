import { readChannel, readLine, readPerson, type Channel, type ChannelDetail, type Line, type LinePage, type Page, type ThreadPage, type Unread } from "./model";
import type { Inbox, PersonRef } from "./waiting";

/** What the pod's live socket says about channels, read off the wire and
 *  applied to what the views already hold.
 *
 *  The `channel` topic carries one channel's timeline as whole REST objects
 *  -- a line as `GET …/messages` returns it, the channel as the list shows
 *  it -- so every event is an upsert by id, and a repeat (after a resume, or
 *  across a split batch) changes nothing. The `inbox` topic carries the
 *  reader's counts, absolute, so the later one wins. Pure, so the tests hold
 *  each rule to the frames the server sends. */

type Wire = Record<string, unknown>;

const record = (value: unknown): Wire => (value && typeof value === "object" ? (value as Wire) : {});
const text = (value: unknown): string | null => (typeof value === "string" && value ? value : null);
const count = (value: unknown): number => (typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0);

export type ChannelEvent =
    | { type: "line.created" | "line.edited" | "line.deleted"; line: Line }
    | { type: "question.closed"; messageId: string; state: string }
    | { type: "channel.updated"; channel: Channel }
    | { type: "read.updated"; readSeq: number }
    /** `threadRootId`: typing a reply in that thread, shown only there. */
    | { type: "typing"; user: PersonRef; threadRootId: string | null }
    | { type: "answering.started"; messageId: string; agentId: string | null }
    | { type: "answering.finished"; messageId: string; agentId: string | null }
    | { type: "line.streaming"; messageId: string; agentId: string | null; kind: string; delta: string };

/** One event of the `channel` topic, or null for one this build does not
 *  know -- a newer server's, ignored rather than fatal. */
export function readChannelEvent(raw: unknown): ChannelEvent | null {
    const event = record(raw);
    switch (event.type) {
        case "line.created":
        case "line.edited":
        case "line.deleted": {
            const line = readLine(event.message);
            return line ? { type: event.type, line } : null;
        }
        case "question.closed": {
            const messageId = text(event.message_id);
            return messageId ? { type: "question.closed", messageId, state: String(event.state ?? "") } : null;
        }
        case "channel.updated": {
            const channel = readChannel(event.channel);
            return channel ? { type: "channel.updated", channel } : null;
        }
        case "read.updated":
            return { type: "read.updated", readSeq: count(event.read_seq) };
        case "typing": {
            const user = readPerson(event.user);
            return user ? { type: "typing", user, threadRootId: text(event.thread_root_id) } : null;
        }
        case "answering.started":
        case "answering.finished": {
            const messageId = text(event.message_id);
            return messageId ? { type: event.type, messageId, agentId: text(event.agent_id) } : null;
        }
        case "line.streaming": {
            const messageId = text(event.message_id);
            return messageId ? {
                type: "line.streaming", messageId, agentId: text(event.agent_id),
                kind: typeof event.kind === "string" ? event.kind : "text",
                delta: typeof event.delta === "string" ? event.delta : "",
            } : null;
        }
        default:
            return null;
    }
}

/* ── the timeline ──────────────────────────────────────────────────── */

/** A top-level line put where it belongs in the pages held -- newest page
 *  first, each oldest first inside: replaced where it is, else added to the
 *  newest page in `seq` order. A line older than every page held is not
 *  added: the page that would hold it has not been read yet, and reading it
 *  will bring it. `added` says whether the timeline grew. */
export function upsertTimeline(pages: readonly LinePage[], line: Line): { pages: LinePage[]; added: boolean } {
    if (pages.some((page) => page.items.some((one) => one.id === line.id))) {
        return { pages: pages.map((page) => ({ ...page, items: page.items.map((one) => (one.id === line.id ? line : one)) })), added: false };
    }
    const [newest, ...rest] = pages;
    if (!newest) return { pages: [{ items: [line], nextBefore: null }], added: true };
    const oldestHeld = Math.min(...pages.flatMap((page) => page.items.map((one) => one.seq)).filter((seq) => seq > 0));
    const hasOlder = pages.at(-1)?.nextBefore !== null && pages.at(-1)?.nextBefore !== undefined;
    if (hasOlder && Number.isFinite(oldestHeld) && line.seq > 0 && line.seq < oldestHeld) return { pages: [...pages], added: false };
    const items = [...newest.items, line].sort((a, b) => a.seq - b.seq);
    return { pages: [{ ...newest, items }, ...rest], added: true };
}

/** A thread's pages with one reply more, or one changed: replaced where it
 *  is held, else added to the last page -- but only once the last page is
 *  the newest. While later pages are still to be read, a new reply is left
 *  for them to bring, so it is never shown out of order or twice. */
export function upsertThread(pages: readonly ThreadPage[], line: Line): ThreadPage[] {
    if (pages.some((page) => page.items.some((one) => one.id === line.id))) {
        return pages.map((page) => ({ ...page, items: page.items.map((one) => (one.id === line.id ? line : one)) }));
    }
    const last = pages.at(-1);
    if (!last) return [{ items: [line], nextAfter: null }];
    if (last.nextAfter !== null) return [...pages];
    return [...pages.slice(0, -1), { ...last, items: [...last.items, line].sort((a, b) => a.seq - b.seq) }];
}

/* ── the channel ───────────────────────────────────────────────────── */

/** The channel as the list shows it, laid over the detail held: what the
 *  list carries changes, what only the detail has (its places, members and
 *  the reader's own settings) stays until the detail is read again. */
export function mergeChannel(detail: ChannelDetail, channel: Channel): ChannelDetail {
    return { ...detail, ...channel, me: { ...detail.me, readSeq: Math.max(detail.me.readSeq, channel.unread.readSeq) } };
}

/** The reader's read position moved -- another tab, the read call. It
 *  never goes back. */
export function withReadSeq(detail: ChannelDetail, readSeq: number): ChannelDetail {
    if (readSeq <= detail.me.readSeq) return detail;
    return { ...detail, me: { ...detail.me, readSeq }, unread: { ...detail.unread, readSeq, lines: 0, mentions: 0 } };
}

/** Who is typing in one place: the channel itself (`threadRootId` null) or
 *  one thread -- a reply being typed is not news in the channel. */
export function typingIn<T extends { threadRootId: string | null }>(typing: readonly T[], threadRootId: string | null): T[] {
    return typing.filter((one) => one.threadRootId === threadRootId);
}

/** "Priya is typing…", for however many are. */
export function sayTyping(names: readonly string[]): string {
    if (names.length === 1) return names[0] + " is typing…";
    if (names.length === 2) return names[0] + " and " + names[1] + " are typing…";
    return names.length > 2 ? "Several people are typing…" : "";
}

/* ── an answer being written ───────────────────────────────────────── */

export interface Answering {
    /** The line it answers. */
    messageId: string;
    agentId: string | null;
    /** What has been written so far, for the member it answers; empty for
     *  everyone else, who see only that it is being written. */
    text: string;
}

/** "Lem is answering…" as the events say it: started, written a piece at a
 *  time (only to the member it answers), finished. A `stream_reset` starts
 *  the text over; a `snapshot` -- sent to somebody who arrives mid-answer --
 *  is the text so far, in place of what was held; tool and thinking tokens
 *  are not the answer. */
export function answeringStep(held: readonly Answering[], event: ChannelEvent): Answering[] {
    switch (event.type) {
        case "answering.started":
            return held.some((one) => one.messageId === event.messageId && one.agentId === event.agentId)
                ? [...held]
                : [...held, { messageId: event.messageId, agentId: event.agentId, text: "" }];
        case "answering.finished":
            return held.filter((one) => !(one.messageId === event.messageId && (event.agentId === null || one.agentId === event.agentId)));
        case "line.streaming": {
            const found = held.find((one) => one.messageId === event.messageId && one.agentId === event.agentId);
            const base = found ?? { messageId: event.messageId, agentId: event.agentId, text: "" };
            const next = event.kind === "stream_reset" ? ""
                : event.kind === "snapshot" ? event.delta
                : event.kind === "text" ? base.text + event.delta
                : base.text;
            const updated = { ...base, text: next };
            return found ? held.map((one) => (one === found ? updated : one)) : [...held, updated];
        }
        default:
            return [...held];
    }
}

/** The answers that started between two moments: what is said aloud when
 *  an agent begins writing. One that was already going is not news. */
export function answeringNews(before: readonly Answering[], after: readonly Answering[]): Answering[] {
    const was = new Set(before.map((one) => one.messageId + ":" + (one.agentId ?? "")));
    return after.filter((one) => !was.has(one.messageId + ":" + (one.agentId ?? "")));
}

/* ── the inbox ─────────────────────────────────────────────────────── */

export type InboxEvent =
    | { type: "inbox.updated"; channelId: string; unread: Unread; totals: { channels: number; mentions: number; waiting: number } }
    | { type: "question.waiting"; channelId: string; messageId: string };

export function readInboxEvent(raw: unknown): InboxEvent | null {
    const event = record(raw);
    const channelId = text(event.channel_id);
    if (!channelId) return null;
    if (event.type === "inbox.updated") {
        const unread = record(event.unread);
        const totals = record(event.totals);
        return {
            type: "inbox.updated",
            channelId,
            unread: { lines: count(unread.lines), mentions: count(unread.mentions), waiting: count(unread.waiting), readSeq: count(unread.read_seq) },
            totals: { channels: count(totals.channels), mentions: count(totals.mentions), waiting: count(totals.waiting) },
        };
    }
    if (event.type === "question.waiting") {
        const messageId = text(event.message_id);
        return messageId ? { type: "question.waiting", channelId, messageId } : null;
    }
    return null;
}

/** The inbox's badges as the event says them now; its lists are read again
 *  when a question is new or one it lists is no longer waiting. */
export function withInboxTotals(inbox: Inbox, totals: { channels: number; mentions: number; waiting: number }): Inbox {
    return { ...inbox, ...totals };
}

/** One channel's counts, wherever a list holds it. */
export function withChannelUnread<T extends { id: string; unread: Unread }>(channels: readonly T[], channelId: string, unread: Unread): T[] {
    return channels.map((channel) => (channel.id === channelId ? { ...channel, unread } : channel));
}

/** A new top-level line, as a list row's preview and order key: the row
 *  says it, and moves to the top, as the list orders by its newest line. */
export function withLastLine<T extends Channel>(channels: readonly T[], channelId: string, line: Line): T[] {
    const at = channels.findIndex((channel) => channel.id === channelId);
    if (at === -1 || line.seq < channels[at].lastSeq) return [...channels];
    const moved = { ...channels[at], lastSeq: line.seq, lastMessageAt: line.createdAt, lastLine: { sender: line.sender, text: line.text ?? "", createdAt: line.createdAt } };
    return [moved, ...channels.slice(0, at), ...channels.slice(at + 1)];
}

/** The same across a list's pages: the row leaves whichever page held it
 *  for the top of the first. A channel no page holds is left for the next
 *  read of the list to bring. */
export function withLastLinePages<T extends Channel>(pages: readonly Page<T>[], channelId: string, line: Line): Page<T>[] {
    const holder = pages.findIndex((page) => page.items.some((channel) => channel.id === channelId));
    if (holder === -1) return [...pages];
    const row = pages[holder].items.find((channel) => channel.id === channelId)!;
    if (line.seq < row.lastSeq) return [...pages];
    const [moved] = withLastLine([row], channelId, line);
    return pages.map((page, index) => {
        const rest = index === holder ? page.items.filter((channel) => channel.id !== channelId) : page.items;
        return { ...page, items: index === 0 ? [moved, ...rest] : rest };
    });
}

/** A channel row replaced by what the socket says it is now. */
export function withChannel<T extends Channel>(channels: readonly T[], channel: Channel): T[] {
    return channels.map((one) => (one.id === channel.id ? { ...one, ...channel } : one));
}
