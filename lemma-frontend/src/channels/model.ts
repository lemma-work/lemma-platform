import { platformName } from "./sources";
import type { PersonRef, WaitingQuestion } from "./waiting";

/** A pod's channels as the app draws them, read off the wire and said.
 *
 *  A channel is one shared timeline: `DIRECT` is the reader and the pod (their
 *  own; nobody else's is listed), `CONTACT` one contact and the pod, `GROUP` a
 *  chat several people read. It may be fed by outside places -- a WhatsApp
 *  group, a Slack channel, an email thread -- or by none. Everybody in one is
 *  a `PersonRef`: a member, a contact or an agent.
 *
 *  Pure, so the test runner can hold every sentence the pages say to what the
 *  API actually sent. */

export type ChannelKind = "DIRECT" | "CONTACT" | "GROUP";
export type NotifyLevel = "ALL" | "MENTIONS" | "NONE";
export type Visibility = "PUBLIC" | "PRIVATE";
export type ContactStatus = "open" | "waiting_on_contact" | "closed";
export type MemberRole = "owner" | "member" | "guest";
export type AgentAnswers = "MENTIONED" | "EVERY_LINE";

export interface Unread {
    lines: number;
    mentions: number;
    waiting: number;
    /** The newest line the reader has seen, by its place in the channel. */
    readSeq: number;
}

export interface SourceSummary {
    platform: string;
    title: string | null;
}

export interface Channel {
    id: string;
    kind: ChannelKind;
    title: string | null;
    topic: string | null;
    /** Whose it is: the member's for a DIRECT one, the contact's for a
     *  CONTACT one; null for a group. */
    person: PersonRef | null;
    /** A group's: every member sees a public one, its members a private one. */
    visibility: Visibility;
    /** A group's: an agent's answer made with the asker's own access is read
     *  by everybody in it (the default) -- or, off, kept to whoever asked
     *  until they share it. */
    answersShared: boolean;
    sources: SourceSummary[];
    /** A contact's channel: the member answering them -- by writing to them,
     *  or assigned. Its agent waits until they have been quiet
     *  `handBackAfterHours`. */
    assignee: PersonRef | null;
    /** A contact's channel: whether its agent answers them at all. */
    agentsAnswer: boolean;
    handBackAfterHours: number;
    status: ContactStatus | null;
    /** It asks for a person, and why. */
    attention: { since: string; reason: string | null } | null;
    archived: boolean;
    /** Whether the reader is in it. An admin is listed a bot's private
     *  groups they are not in, to join on request. */
    joined: boolean;
    lastLine: { sender: PersonRef; text: string; createdAt: string } | null;
    lastMessageAt: string | null;
    /** The newest line's place in the channel; lines are numbered from 1. */
    lastSeq: number;
    createdAt: string;
    unread: Unread;
}

/** One outside place feeding a channel. */
export interface SourcePlace {
    id: string;
    platform: string;
    kind: "PERSON" | "CHAT";
    title: string | null;
    surfaceId: string | null;
    pending: boolean;
    lastInboundAt: string | null;
}

/** Somebody in a channel: a person with a role, or an agent with when it
 *  answers. */
export interface Member {
    person: PersonRef;
    role: MemberRole;
    answers: AgentAnswers | null;
}

export interface ChannelDetail extends Channel {
    places: SourcePlace[];
    members: Member[];
    me: {
        joined: boolean;
        notify: NotifyLevel;
        /** Where they hear of it outside the app; null is the pod's rule. */
        notifyVia: string[] | null;
        /** Muted until then, when it is still to come. */
        mutedUntil: string | null;
        readSeq: number;
        role: MemberRole | null;
        canManage: boolean;
    };
}

export interface Interaction {
    kind: "ASK" | "APPROVAL" | "FORM";
    /** `ANSWERING` while somebody's answer is being taken. */
    state: "OPEN" | "ANSWERING" | "ANSWERED" | "CANCELLED" | "EXPIRED";
    question: Record<string, unknown>;
    /** The one person who may answer it. */
    awaits: PersonRef | null;
    answer: Record<string, unknown> | null;
    answeredBy: PersonRef | null;
    answeredAt: string | null;
}

/** What a line carries beside its text: somebody it names, a pod file it
 *  shares, a link. Platform markup was turned into these when it came in. */
export type Part =
    | { type: "mention"; kind: "MEMBER" | "CONTACT" | "AGENT"; id: string; name: string | null }
    /** `fileId` is the server's once the line is stored: readers fetch the
     *  file through the line (`…/messages/{m}/files/{fileId}`), never by
     *  its path, which is the writer's. */
    | { type: "file"; path: string; name: string; mimeType: string | null; size: number | null; fileId: string | null }
    | { type: "link"; url: string; label: string | null };

export interface Line {
    id: string;
    /** Its place in the channel, from 1: what read state counts by. */
    seq: number;
    sender: PersonRef;
    /** For an agent's line: whom it acted for, with whose access. */
    onBehalfOf: PersonRef | null;
    /** Null where the line is withheld from the reader, or deleted. */
    text: string | null;
    createdAt: string;
    editedAt: string | null;
    deleted: boolean;
    /** On a deleted line: the member who removed it, when that was not who
     *  wrote it -- somebody who may change the channel. */
    removedBy: PersonRef | null;
    withheld: boolean;
    /** `asker_only`: an agent's answer made with one member's own access,
     *  which only that member reads in full. */
    visibility: "asker_only" | "channel";
    /** The reader is that member, and may share it with the channel. */
    canShare: boolean;
    membersOnly: boolean;
    /** Where it was said; null for a line written in Lemma. */
    sourceId: string | null;
    threadRootId: string | null;
    /** The line this one answers: an agent's answer points at its question. */
    replyToId: string | null;
    replyCount: number;
    /** On a thread's first line: when its newest reply was written. */
    lastReplyAt: string | null;
    interaction: Interaction | null;
    parts: Part[];
    /** The composer's name for it, when it was written in Lemma. */
    clientMessageId: string | null;
    /** Where the reader's own line went outside, one per place; null on
     *  anybody else's line, and on one that went nowhere outside. */
    delivered: LineDelivery[] | null;
}

export type LineDeliveryStatus = "PENDING" | "SENT" | "FAILED" | "SKIPPED" | "DEAD";

/** One send of the reader's own line to a place outside Lemma. */
export interface LineDelivery {
    id: string;
    sourceId: string | null;
    platform: string | null;
    status: LineDeliveryStatus;
    reason: string | null;
}

const DELIVERY_STATUSES: readonly LineDeliveryStatus[] = ["PENDING", "SENT", "FAILED", "SKIPPED", "DEAD"];

function readDelivered(raw: unknown): LineDelivery[] | null {
    if (!Array.isArray(raw)) return null;
    const out = raw.flatMap((entry): LineDelivery[] => {
        const row = record(entry);
        const id = words(row.id);
        if (!id || !DELIVERY_STATUSES.includes(row.status as LineDeliveryStatus)) return [];
        return [{ id, sourceId: words(row.source_id), platform: words(row.platform), status: row.status as LineDeliveryStatus, reason: words(row.reason) }];
    });
    return out.length ? out : null;
}

/** One page of a list, and what to ask for the page after it: an opaque
 *  cursor for channels, a line's `seq` for a timeline. */
export interface Page<T, Cursor = string> {
    items: T[];
    nextBefore: Cursor | null;
}

export type LinePage = Page<Line, number>;

/* ── off the wire ──────────────────────────────────────────────────── */

type Wire = Record<string, unknown>;

const record = (value: unknown): Wire => (value && typeof value === "object" ? (value as Wire) : {});

function words(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value : null;
}

function count(value: unknown): number {
    return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

function rows(value: unknown): unknown[] {
    return Array.isArray(value) ? value : [];
}

const KINDS: readonly ChannelKind[] = ["DIRECT", "CONTACT", "GROUP"];
const LEVELS: readonly NotifyLevel[] = ["ALL", "MENTIONS", "NONE"];
const STATUSES: readonly ContactStatus[] = ["open", "waiting_on_contact", "closed"];
const ROLES: readonly MemberRole[] = ["owner", "member", "guest"];

function role(value: unknown): MemberRole | null {
    return ROLES.includes(value as MemberRole) ? (value as MemberRole) : null;
}

function seq(value: unknown): number | null {
    return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function level(value: unknown, fallback: NotifyLevel = "ALL"): NotifyLevel {
    return LEVELS.includes(value as NotifyLevel) ? (value as NotifyLevel) : fallback;
}

export function readPerson(raw: unknown): PersonRef | null {
    if (!raw || typeof raw !== "object") return null;
    const row = raw as Wire;
    const kind = row.kind === "CONTACT" || row.kind === "AGENT" ? row.kind : "MEMBER";
    return { kind, id: words(row.id), name: words(row.name) };
}

const SOMEBODY: PersonRef = { kind: "MEMBER", id: null, name: null };

/** One channel off a list. Null without the id everything about it is keyed by. */
export function readChannel(raw: unknown): Channel | null {
    const row = record(raw);
    const id = words(row.id);
    if (!id) return null;
    const attention = row.attention && typeof row.attention === "object" ? record(row.attention) : null;
    const last = row.last_line && typeof row.last_line === "object" ? record(row.last_line) : null;
    const unread = record(row.unread);
    return {
        id,
        kind: KINDS.includes(row.kind as ChannelKind) ? (row.kind as ChannelKind) : "GROUP",
        title: words(row.title),
        topic: words(row.topic),
        person: readPerson(row.person),
        visibility: row.visibility === "PRIVATE" ? "PRIVATE" : "PUBLIC",
        answersShared: row.answers_shared !== false,
        sources: rows(row.sources).flatMap((entry) => {
            const source = record(entry);
            const platform = words(source.platform);
            return platform ? [{ platform: platform.toUpperCase(), title: words(source.title) }] : [];
        }),
        assignee: readPerson(row.assignee),
        agentsAnswer: row.agents_answer !== false,
        handBackAfterHours: typeof row.hand_back_after_hours === "number" && row.hand_back_after_hours > 0 ? row.hand_back_after_hours : 12,
        status: STATUSES.includes(row.status as ContactStatus) ? (row.status as ContactStatus) : null,
        attention: attention ? { since: String(attention.since ?? ""), reason: words(attention.reason) } : null,
        archived: row.archived === true,
        joined: row.joined === true || record(row.me).joined === true,
        lastLine: last ? {
            sender: readPerson(last.sender) ?? SOMEBODY,
            text: typeof last.text === "string" ? last.text : "",
            createdAt: String(last.created_at ?? ""),
        } : null,
        lastMessageAt: words(row.last_line_at),
        lastSeq: count(row.last_seq),
        createdAt: String(row.created_at ?? ""),
        unread: { lines: count(unread.lines), mentions: count(unread.mentions), waiting: count(unread.waiting), readSeq: count(unread.read_seq) },
    };
}

export function readChannelPage(raw: unknown): Page<Channel> {
    const body = record(raw);
    return {
        items: rows(body.items).map(readChannel).filter((channel): channel is Channel => channel !== null),
        nextBefore: words(body.next_cursor),
    };
}

export function readChannelDetail(raw: unknown): ChannelDetail | null {
    const channel = readChannel(raw);
    if (!channel) return null;
    const row = record(raw);
    const me = record(row.me);
    return {
        ...channel,
        places: rows(row.source_places).flatMap((entry) => {
            const place = record(entry);
            const id = words(place.id);
            if (!id) return [];
            return [{
                id,
                platform: (words(place.platform) ?? "").toUpperCase(),
                kind: place.kind === "CHAT" ? "CHAT" as const : "PERSON" as const,
                title: words(place.title),
                surfaceId: words(place.surface_id),
                pending: place.pending === true,
                lastInboundAt: words(place.last_inbound_at),
            }];
        }),
        members: rows(row.members).flatMap((entry) => {
            const member = record(entry);
            const person = readPerson(member.person);
            if (!person) return [];
            const answers = member.answers === "EVERY_LINE" || member.answers === "MENTIONED" ? member.answers : null;
            return [{ person, role: role(member.role) ?? (person.kind === "CONTACT" ? "guest" : "member"), answers }];
        }),
        me: {
            joined: me.joined === true,
            notify: level(me.notify),
            notifyVia: Array.isArray(me.notify_via) ? me.notify_via.filter((one): one is string => typeof one === "string") : null,
            mutedUntil: words(me.muted_until),
            readSeq: count(me.read_seq),
            role: role(me.role),
            canManage: me.can_manage === true,
        },
    };
}

/** What a line asks, off the wire's `question`. */
function readQuestion(raw: unknown): Interaction | null {
    if (!raw || typeof raw !== "object") return null;
    const row = raw as Wire;
    const kind = row.kind === "APPROVAL" || row.kind === "FORM" ? row.kind : row.kind === "ASK" ? "ASK" : null;
    if (!kind) return null;
    const state = row.state === "ANSWERING" || row.state === "ANSWERED" || row.state === "CANCELLED" || row.state === "EXPIRED" ? row.state : "OPEN";
    return {
        kind,
        state,
        question: record(row.question),
        awaits: readPerson(row.awaits),
        answer: row.answer && typeof row.answer === "object" ? (row.answer as Wire) : null,
        answeredBy: readPerson(row.answered_by),
        answeredAt: words(row.answered_at),
    };
}

export function readLine(raw: unknown): Line | null {
    const row = record(raw);
    const id = words(row.id);
    if (!id) return null;
    const deleted = row.deleted === true;
    const withheld = row.withheld === true;
    return {
        id,
        seq: count(row.seq),
        sender: readPerson(row.sender) ?? SOMEBODY,
        onBehalfOf: readPerson(row.on_behalf_of),
        /* A withheld or deleted line keeps no text even if some arrived. */
        text: deleted || withheld ? null : typeof row.text === "string" ? row.text : null,
        createdAt: String(row.created_at ?? ""),
        editedAt: words(row.edited_at),
        deleted,
        removedBy: deleted ? readPerson(row.removed_by) : null,
        withheld,
        visibility: row.visibility === "asker_only" ? "asker_only" : "channel",
        canShare: row.can_share === true && !deleted,
        membersOnly: row.members_only === true,
        sourceId: words(row.source_id),
        threadRootId: words(row.thread_root_id),
        replyToId: words(row.reply_to_id),
        replyCount: count(row.reply_count),
        lastReplyAt: words(row.last_reply_at),
        interaction: readQuestion(row.question),
        parts: deleted || withheld ? [] : readParts(row.parts),
        clientMessageId: words(row.client_message_id),
        delivered: deleted ? null : readDelivered(row.delivered),
    };
}

/** A line's parts, keeping only the ones this build can draw: a part of a
 *  kind it does not know, or without what it needs, is left out. */
export function readParts(raw: unknown): Part[] {
    return rows(raw).flatMap((entry): Part[] => {
        const part = record(entry);
        if (part.type === "mention") {
            const id = words(part.id);
            const kind = part.kind === "CONTACT" || part.kind === "AGENT" ? part.kind : part.kind === "MEMBER" ? "MEMBER" : null;
            return id && kind ? [{ type: "mention", kind, id, name: words(part.name) }] : [];
        }
        if (part.type === "file") {
            const path = words(part.path);
            if (!path) return [];
            return [{
                type: "file", path, name: words(part.name) ?? path.split("/").filter(Boolean).at(-1) ?? path,
                mimeType: words(part.mime_type), size: typeof part.size === "number" && part.size >= 0 ? part.size : null,
                fileId: words(part.file_id),
            }];
        }
        if (part.type === "link") {
            const url = words(part.url);
            return url && /^https?:\/\//i.test(url) ? [{ type: "link", url, label: words(part.label) }] : [];
        }
        return [];
    });
}

/** Parts as the API takes them back -- `readParts` the other way: a file by
 *  its path, which the server reads again as the writer. */
export function writeParts(parts: readonly Part[]): Record<string, unknown>[] {
    return parts.map((part) => part.type === "mention"
        ? { type: "mention", id: part.id, kind: part.kind, ...(part.name ? { name: part.name } : {}) }
        : part.type === "file"
            ? {
                type: "file", path: part.path, name: part.name,
                ...(part.mimeType ? { mime_type: part.mimeType } : {}),
                ...(part.size !== null ? { size: part.size } : {}),
            }
            : { type: "link", url: part.url, ...(part.label ? { label: part.label } : {}) });
}

/** A page of a timeline, or a thread's replies. The page before it is asked
 *  for by the `seq` it starts at, and only while there are older lines. */
export function readLinePage(raw: unknown): LinePage {
    const body = record(raw);
    return {
        items: rows(body.items).map(readLine).filter((line): line is Line => line !== null),
        nextBefore: seq(body.next_before_seq),
    };
}

/** A page of a thread's replies, oldest first, and the `seq` to read the
 *  next page after -- null once the newest reply is held. */
export interface ThreadPage {
    items: Line[];
    nextAfter: number | null;
}

export function readThreadPage(raw: unknown): ThreadPage {
    const body = record(raw);
    return {
        items: rows(body.items).map(readLine).filter((line): line is Line => line !== null),
        nextAfter: body.has_more === true ? seq(body.next_after_seq) : null,
    };
}

/** A thread's pages as one run of replies, oldest first, each once. */
export function joinThread(pages: readonly ThreadPage[]): Line[] {
    const seen = new Set<string>();
    return pages.flatMap((page) => page.items).filter((line) => !seen.has(line.id) && Boolean(seen.add(line.id)));
}

/** The newest line in a timeline, by `seq`: what reading it marks read. */
export function newestSeq(lines: readonly Pick<Line, "seq">[]): number | null {
    let newest: number | null = null;
    for (const line of lines) if (line.seq > 0 && (newest === null || line.seq > newest)) newest = line.seq;
    return newest;
}

/** The pages of a timeline as react-query holds them -- newest first, each
 *  oldest first inside -- as one run, oldest first, each line once. */
export function joinPages(pages: readonly LinePage[]): Line[] {
    const seen = new Set<string>();
    const out: Line[] = [];
    for (const page of [...pages].reverse()) {
        for (const line of page.items) {
            if (seen.has(line.id)) continue;
            seen.add(line.id);
            out.push(line);
        }
    }
    return out;
}

/** A line written or changed here, put where it belongs in the pages already
 *  held: replaced where it is, or added to the newest page. */
export function withLine(pages: readonly LinePage[], line: Line): LinePage[] {
    if (pages.some((page) => page.items.some((one) => one.id === line.id))) {
        return pages.map((page) => ({ ...page, items: page.items.map((one) => (one.id === line.id ? line : one)) }));
    }
    const [newest, ...rest] = pages;
    return newest ? [{ ...newest, items: [...newest.items, line] }, ...rest] : [{ items: [line], nextBefore: null }];
}

/* ── what a channel is called ──────────────────────────────────────── */

/** What to call a person, to the reader: "You" for themselves. */
export function personName(person: PersonRef | null, me: string | null): string {
    if (!person) return "Someone";
    if (me && person.kind === "MEMBER" && person.id === me) return "You";
    if (person.name) return person.name;
    return person.kind === "AGENT" ? "An agent" : person.kind === "CONTACT" ? "A contact" : "A member";
}

/** A contact nobody has named, by how they came: somebody on the
 *  website's chat is a visitor; anybody else, a contact. */
export function nameless(channel: Pick<Channel, "sources">): string {
    return channel.sources.some((source) => source.platform === "WEB") ? "A website visitor" : "A contact";
}

/** A chat's name. The reader's default chat -- their private line with the
 *  teammate -- is named by where they reach it ("WhatsApp"), or "Notices"
 *  when it is only what the teammate sends them; a contact's is the
 *  contact; a group is what it was called, or where it is. `space` is kept
 *  for callers that name things after the space. */
export function channelTitle(channel: Pick<Channel, "kind" | "title" | "person" | "sources">, _space: string): string {
    if (channel.kind === "DIRECT") {
        const names = [...new Set(channel.sources.map((source) => platformName(source.platform)))];
        return names.length ? sayList(names) : "Notices";
    }
    if (channel.kind === "CONTACT") return channel.person?.name ?? channel.title ?? nameless(channel);
    return channel.title ?? channel.sources.find((source) => source.title)?.title ?? "Untitled chat";
}

/** "A", "A and B", "A, B and C". */
export function sayList(items: readonly string[]): string {
    if (items.length <= 1) return items[0] ?? "";
    return items.slice(0, -1).join(", ") + " and " + items.at(-1);
}

/** The platforms feeding a channel, each once, in the order they came. */
export function platformsOf(channel: Pick<Channel, "sources">): string[] {
    return [...new Set(channel.sources.map((source) => source.platform.toUpperCase()))];
}

/** The last line, as a row's second line: "Priya: Thursday works". */
export function previewOf(channel: Pick<Channel, "lastLine" | "topic">, me: string | null): string {
    const last = channel.lastLine;
    if (!last) return channel.topic ?? "Nothing said yet.";
    const text = last.text.replace(/\s+/g, " ").trim();
    return personName(last.sender, me) + ": " + (text || "…");
}

/** The three ways a list is cut: the reader's own, the groups, and -- for
 *  an editor -- the contacts. A section with nothing in it is left out. */
export interface Section {
    key: "direct" | "groups" | "contacts";
    label: string;
    channels: Channel[];
}

export function sectionsOf(channels: readonly Channel[], maySeeContacts: boolean): Section[] {
    const of = (kind: ChannelKind) => channels.filter((channel) => channel.kind === kind);
    const sections: Section[] = [
        { key: "direct", label: "Your channel", channels: of("DIRECT") },
        { key: "groups", label: "Groups", channels: of("GROUP") },
    ];
    if (maySeeContacts) sections.push({ key: "contacts", label: "Contacts", channels: of("CONTACT") });
    return sections.filter((section) => section.channels.length > 0);
}

/** What is new in a channel, for a row's accessible name. */
export function sayUnread(unread: Unread): string | null {
    const parts: string[] = [];
    if (unread.lines) parts.push(unread.lines >= 100 ? "100 or more new" : unread.lines + " new");
    if (unread.mentions) parts.push(unread.mentions === 1 ? "mentions you" : unread.mentions + " mention you");
    if (unread.waiting) parts.push(unread.waiting === 1 ? "a question waiting on you" : unread.waiting + " questions waiting on you");
    return parts.length ? parts.join(", ") : null;
}

/** A count for a badge: nothing, the number, or "99+". */
export function badge(count: number): string | null {
    if (count <= 0) return null;
    return count > 99 ? "99+" : String(count);
}

/** What the sidebar's badge counts and says: what the bell counts -- the
 *  questions waiting on the reader, the lines naming them, and the new lines
 *  in their own channel -- so the two never disagree. Its tone is the most
 *  pressing of them: a question, which only the reader can answer, then a
 *  mention. A group with new lines and nothing for the reader is shown on
 *  its row, not counted here: every public group would count otherwise. */
export function inboxBadge(totals: { mentions: number; waiting: number }, directLines = 0): { count: number; tone: "waiting" | "mention" | "new"; said: string } | null {
    const count = totals.waiting + totals.mentions + directLines;
    if (count <= 0) return null;
    const parts: string[] = [];
    if (totals.waiting) parts.push(totals.waiting === 1 ? "1 question waiting on you" : totals.waiting + " questions waiting on you");
    if (totals.mentions) parts.push(totals.mentions === 1 ? "1 mention of you" : totals.mentions + " mentions of you");
    if (directLines) parts.push(directLines === 1 ? "1 new message for you" : directLines + " new messages for you");
    return { count, tone: totals.waiting ? "waiting" : totals.mentions ? "mention" : "new", said: parts.join(", ") };
}

/** When something happened, short enough for a column: 12 min, 2 h,
 *  Yesterday, 3 days, then the date. */
export function sinceShort(iso: string | null | undefined, now = new Date()): string {
    if (!iso) return "";
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return "";
    const minutes = Math.floor((now.getTime() - at.getTime()) / 60_000);
    if (minutes < 1) return "Now";
    if (minutes < 60) return minutes + " min";
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + " h";
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const day = new Date(at.getFullYear(), at.getMonth(), at.getDate()).getTime();
    const days = Math.round((today - day) / 86_400_000);
    if (days <= 1) return "Yesterday";
    if (days < 7) return days + " days";
    return at.toLocaleDateString([], at.getFullYear() === now.getFullYear()
        ? { day: "numeric", month: "short" }
        : { day: "numeric", month: "short", year: "numeric" });
}

/* ── one line ──────────────────────────────────────────────────────── */

/** Who wrote a line, as the reader sees it. An agent's line says whom it
 *  acted for: "Lem, for Priya". */
export function senderLabel(line: Pick<Line, "sender" | "onBehalfOf">, me: string | null): string {
    const name = personName(line.sender, me);
    if (line.sender.kind !== "AGENT" || !line.onBehalfOf) return name;
    /* Answering the reader goes without saying; answering somebody else is
       worth a word, since it answered with their access. */
    const whom = personName(line.onBehalfOf, me);
    return whom === "You" ? name : name + ", answering " + whom;
}

/** Where a line was said: "#acme (Slack)", "WhatsApp". Nothing for a line
 *  written in Lemma, or from a place the reader cannot see. */
export function sourceLabel(sourceId: string | null, places: readonly SourcePlace[]): string | null {
    if (!sourceId) return null;
    const place = places.find((candidate) => candidate.id === sourceId);
    if (!place) return null;
    const platform = platformName(place.platform);
    return place.title ? place.title + " (" + platform + ")" : platform;
}

/** A line's small print after its sender: where, and when. */
export function lineMeta(line: Pick<Line, "sourceId" | "createdAt">, places: readonly SourcePlace[]): string {
    return [sourceLabel(line.sourceId, places), clockTime(line.createdAt)].filter(Boolean).join(" · ");
}

/** "10:02", in the reader's own clock. */
export function clockTime(iso: string): string {
    const at = new Date(iso);
    return Number.isNaN(at.getTime()) ? "" : at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** What stands where a withheld line's text would be: which agent answered
 *  whom, privately -- with that person's own access, so only they read it. */
export function withheldNote(line: Pick<Line, "withheld" | "onBehalfOf" | "sender">, me: string | null): string | null {
    if (!line.withheld) return null;
    const agent = line.sender.kind === "AGENT" && line.sender.name ? line.sender.name : "An agent";
    const whom = line.onBehalfOf ? personName(line.onBehalfOf, me) : null;
    return whom && whom !== "You"
        ? agent + " answered " + whom + " privately"
        : agent + " answered someone privately";
}

/** What the asker reads under an answer only they can see. */
export const ASKER_ONLY_NOTE = "Only you can see this — it used your access";

/** Whether the reader may change or delete a line: their own, still there. */
export function isMine(line: Pick<Line, "sender" | "deleted" | "withheld">, me: string | null): boolean {
    return Boolean(me && line.sender.kind === "MEMBER" && line.sender.id === me && !line.deleted && !line.withheld);
}

/** Where a question on a line stands, to the reader. */
export type QuestionState =
    /** `answering`: somebody's answer is being taken. It is still offered:
     *  a claim that lapses (its tab closed, its worker gone) puts it back, and
     *  answering one still being taken is refused in words that say so. */
    | { kind: "yours"; answering?: boolean }
    | { kind: "waiting"; on: string }
    | { kind: "answering" }
    | { kind: "answered"; by: string | null }
    | { kind: "closed"; why: "CANCELLED" | "EXPIRED" };

export function questionState(interaction: Interaction, me: string | null): QuestionState {
    if (interaction.state === "ANSWERED") return { kind: "answered", by: interaction.answeredBy ? personName(interaction.answeredBy, me) : null };
    if (interaction.state === "CANCELLED" || interaction.state === "EXPIRED") return { kind: "closed", why: interaction.state };
    const awaits = interaction.awaits;
    const mine = Boolean(awaits && me && awaits.kind === "MEMBER" && awaits.id === me);
    if (interaction.state === "ANSWERING") return mine ? { kind: "yours", answering: true } : { kind: "answering" };
    if (mine) return { kind: "yours" };
    return { kind: "waiting", on: awaits ? personName(awaits, me) : "someone" };
}

/** What a question that is not the reader's to answer says. */
export function sayQuestion(state: QuestionState): string | null {
    switch (state.kind) {
        case "yours": return null;
        case "waiting": return "Waiting on " + state.on;
        case "answering": return "Being answered";
        case "answered": return state.by ? "Answered by " + (state.by === "You" ? "you" : state.by) : "Answered";
        case "closed": return state.why === "EXPIRED" ? "No longer waiting: it expired" : "No longer waiting";
    }
}

/** What an answer said, in a few words: the choice, the words, or the
 *  decision. An agent's question is answered as `{answers: {header:
 *  value}}` -- each value the words or the choices picked -- and shows
 *  those. A form's fields are not repeated on the timeline. */
export function answerSummary(answer: Record<string, unknown> | null): string | null {
    if (!answer) return null;
    if (typeof answer.choice === "string" && answer.choice) return answer.choice;
    if (typeof answer.text === "string" && answer.text.trim()) return answer.text.trim();
    if (typeof answer.decision === "string") return answer.decision === "DENY" ? "Denied" : "Approved";
    if (typeof answer.approved === "boolean") return answer.approved ? "Approved" : "Denied";
    if (answer.answers && typeof answer.answers === "object" && !Array.isArray(answer.answers)) {
        const said = Object.values(answer.answers as Record<string, unknown>).flatMap((value) => {
            const one = Array.isArray(value) ? value.filter((pick): pick is string => typeof pick === "string" && Boolean(pick.trim())).join(", ") : typeof value === "string" ? value.trim() : "";
            return one ? [one] : [];
        });
        return said.length ? said.join("; ") : null;
    }
    return null;
}

/** A question on a line, in the shape the waiting bell answers. */
export function asWaitingQuestion(line: Line, channel: Pick<Channel, "id" | "kind" | "title">, me: string | null): WaitingQuestion | null {
    if (!line.interaction) return null;
    return {
        id: line.id,
        channelId: channel.id,
        channelTitle: channel.title,
        channelKind: channel.kind,
        kind: line.interaction.kind,
        text: line.text ?? "",
        from: senderLabel(line, me),
        askedAt: line.createdAt,
        question: line.interaction.question,
        threadRootId: line.threadRootId,
    };
}

/** The timeline, cut at each day, the days named the way a chat names them. */
export function lineDays<T extends { createdAt: string }>(lines: readonly T[], now = new Date()): { key: string; label: string; lines: T[] }[] {
    const days: { key: string; label: string; lines: T[] }[] = [];
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    for (const line of lines) {
        const at = new Date(line.createdAt);
        if (Number.isNaN(at.getTime())) continue;
        const day = new Date(at.getFullYear(), at.getMonth(), at.getDate());
        const key = day.toDateString();
        const last = days.at(-1);
        if (last?.key === key) { last.lines.push(line); continue; }
        const back = Math.round((today - day.getTime()) / 86_400_000);
        const label = back === 0 ? "Today" : back === 1 ? "Yesterday"
            : at.toLocaleDateString([], at.getFullYear() === now.getFullYear()
                ? { weekday: "long", day: "numeric", month: "long" }
                : { day: "numeric", month: "long", year: "numeric" });
        days.push({ key, label, lines: [line] });
    }
    return days;
}

/** Two letters for a face. */
export function initials(name: string): string {
    const parts = name.replace(/^[@+]+/, "").split(/[\s._-]+/).filter(Boolean);
    const letters = parts.slice(0, 2).map((word) => word[0] ?? "").join("");
    return (letters || name.slice(0, 1) || "?").toUpperCase();
}

/** What a composer in this channel offers. A note to members is only a thing
 *  where something outside would otherwise hear it; a choice of place only
 *  where there is more than one, and only of places that exist yet. */
export function composerChoices(detail: Pick<ChannelDetail, "places">): { notes: boolean; places: SourcePlace[] } {
    const live = detail.places.filter((place) => !place.pending);
    return { notes: detail.places.length > 0, places: live.length > 1 ? live : [] };
}

/** Who answers in a channel when nobody is named. In the reader's own the
 *  pod's agent answers every line; anywhere else an agent answers only when
 *  @mentioned, and not at all while a member has stepped in. */
export function whoAnswers(channel: Pick<Channel, "kind" | "assignee" | "agentsAnswer" | "handBackAfterHours">, space: string, me: string | null): string {
    if (channel.kind === "CONTACT" && !channel.agentsAnswer) return "The agent is off here: only people answer them.";
    if (channel.assignee) {
        const name = personName(channel.assignee, me);
        const back = "its agent answers again " + channel.handBackAfterHours + " hours after " + (name === "You" ? "your" : "their") + " last message.";
        return (name === "You" ? "You are answering here; " : name + " is answering here; ") + back;
    }
    if (channel.kind === "DIRECT") return space + " answers everything you write here.";
    if (channel.kind === "CONTACT") return space + "’s agent answers them. Write here to answer them yourself.";
    return "@mention an agent to have it answer.";
}

/** The members of the space not in the channel yet, by user id. */
export function addable<M extends { userId?: string; kind?: string }>(members: readonly M[], inChannel: readonly Member[]): M[] {
    const inIt = new Set(inChannel.filter((one) => one.person.kind === "MEMBER").map((one) => one.person.id));
    return members.filter((member) => member.userId && member.kind !== "teammate" && !inIt.has(member.userId));
}
