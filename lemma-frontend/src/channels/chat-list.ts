import type { ConversationRef } from "@/data";
import type { OriginKind } from "@/thread/conversation-origin";
import { channelTitle, directPlaces, platformsOf, previewOf, type Channel } from "./model";

/** One list of everywhere you talk with a teammate: your chats with it, and
 *  the chats that live on a channel -- your default chat (your private one,
 *  fed by WhatsApp or Telegram, and what it sends you), the chats shared with
 *  people in the space, and the groups it is in on a platform. A contact's
 *  chat is not here: those are a support queue, in Contacts.
 *
 *  The one model both frames draw (the classic sidebar and Chats page, the
 *  frame's sidebar, Lemma summary and ⌘K) and the phone imports: what a row
 *  is, its name, its order, what it says is new, how a search finds it, and
 *  the two groups. Pure, so the tests hold every surface to it. Live updates
 *  reach it through the react-query lists it is built from (`live.ts`,
 *  `waiting-panel.tsx`), so a row moves the same way wherever it is drawn. */

/* ── what a chat is ───────────────────────────────────────────────────── */

/** A chat with the teammate, someone's own chat with it (WhatsApp, Telegram,
 *  what it sends them), or a chat shared with people. */
export type ChatKind = "chat" | "line" | "shared";

/** What opening a row opens: a conversation, or a chat that lives on a
 *  channel. The frame's `OpenChat` takes either as it is. */
export interface ChatTarget {
    kind: "chat" | "shared";
    podId: string;
    id: string;
}

export interface ChatItem {
    key: string;
    open: ChatTarget;
    podId: string;
    podName: string;
    kind: ChatKind;
    title: string;
    /** The last thing said, for a shared chat; where it came from, for a
     *  chat that started on a platform. Shown after the name, quieter. */
    said: string | null;
    /** Last active, epoch ms, or null when the server gave no time. */
    at: number | null;
    needsYou: boolean;
    /** New messages in a shared chat since the reader last read it. */
    unread: number;
    /** The teammate is working in it right now. */
    running: boolean;
    /** The platforms feeding it, for its mark. */
    platforms: string[];
    channel: Channel | null;
    conversation: ConversationRef | null;
}

/** Whether a channel is a chat in the list. The default chat is listed once
 *  something is in it, or it is reached from a platform. */
export function listedAsChat(channel: Channel): boolean {
    if (channel.kind === "CONTACT" || channel.archived) return false;
    if (channel.kind === "DIRECT") return channel.sources.length > 0 || channel.lastLine !== null;
    return true;
}

/** Whether a channel is a chat that was archived: listed under Archived, and
 *  only there, so it can be found and brought back. */
export function archivedChat(channel: Channel): boolean {
    return channel.kind !== "CONTACT" && channel.archived;
}

/** Where a conversation came from, when it counts as a chat. A run that a
 *  schedule, a workflow, a notice or another outcome started is the
 *  teammate's own work, read as activity on its outcome; one about a doc
 *  lives on the doc. Any of them still waiting on you is listed, because that
 *  is where it is answered. */
const CHAT_ORIGINS: ReadonlySet<OriginKind> = new Set<OriginKind>(["chat", "channel"]);

export function isChat(conversation: Pick<ConversationRef, "origin" | "waitingOnYou" | "boundTo">): boolean {
    if (conversation.waitingOnYou) return true;
    if (conversation.boundTo) return false;
    return CHAT_ORIGINS.has(conversation.origin?.kind ?? "chat");
}

/** A chat that lives on a channel, by name. Someone's own chat is named for
 *  what it is: theirs, on the platforms it comes through ("Your chat on
 *  WhatsApp"), or "Notices" when it only sends them things. Called just
 *  "Email" beside chats with names, it read as an inbox; and a person never
 *  reads "line" or "channel" (`tests/outcomes-copy.test.ts`). */
export function chatTitle(channel: Pick<Channel, "kind" | "title" | "person" | "sources">, podName: string): string {
    if (channel.kind !== "DIRECT") return channelTitle(channel, podName);
    const places = directPlaces(channel);
    return places === "Notices" ? places : `Your chat on ${places}`;
}

function timeOf(iso: string | null | undefined): number | null {
    const at = iso ? Date.parse(iso) : Number.NaN;
    return Number.isNaN(at) ? null : at;
}

type PodRef = { id: string; name: string };

/** A chat that lives on a channel, as a row. */
export function itemOfChannel(channel: Channel, pod: PodRef, me: string | null): ChatItem {
    return {
        key: `g:${pod.id}:${channel.id}`,
        open: { kind: "shared", podId: pod.id, id: channel.id },
        podId: pod.id,
        podName: pod.name,
        kind: channel.kind === "DIRECT" ? "line" : "shared",
        title: chatTitle(channel, pod.name),
        said: channel.lastLine || channel.topic ? previewOf(channel, me) : null,
        at: timeOf(channel.lastMessageAt ?? channel.createdAt),
        needsYou: channel.unread.waiting > 0,
        unread: channel.unread.lines,
        running: false,
        platforms: platformsOf(channel),
        channel,
        conversation: null,
    };
}

/** A chat with the teammate, as a row. */
export function itemOfChat(conversation: ConversationRef, pod: PodRef): ChatItem {
    const origin = conversation.origin;
    return {
        key: `c:${pod.id}:${conversation.id}`,
        open: { kind: "chat", podId: pod.id, id: conversation.id },
        podId: pod.id,
        podName: pod.name,
        kind: "chat",
        title: conversation.title,
        said: origin && origin.kind !== "chat" ? origin.label : null,
        at: timeOf(conversation.activityAt),
        needsYou: conversation.waitingOnYou === true,
        unread: 0,
        running: (conversation.status ?? "").toUpperCase() === "RUNNING",
        platforms: origin?.platform ? [origin.platform.toUpperCase()] : [],
        channel: null,
        conversation,
    };
}

/** Most recently active first; a row with no time after those with one;
 *  ties by name, so the order never depends on which list answered first. */
export function newestFirst(a: Pick<ChatItem, "at" | "title">, b: Pick<ChatItem, "at" | "title">): number {
    if (a.at !== null && b.at !== null) return b.at - a.at || a.title.localeCompare(b.title);
    if (a.at !== b.at) return a.at === null ? 1 : -1;
    return a.title.localeCompare(b.title);
}

/** Whether a row is found by what was typed: its name, or what was last
 *  said in it. `needle` is lower case and trimmed. */
export function matchesChat(item: Pick<ChatItem, "title" | "said">, needle: string): boolean {
    return !needle || item.title.toLowerCase().includes(needle) || (item.said ?? "").toLowerCase().includes(needle);
}

/** One teammate's chats as one list, newest first. `chats` says which of its
 *  conversations count: every one given, unless told otherwise -- the frame
 *  keeps to `isChat`, the Chats page keeps them all and filters by origin.
 *  `channels` are listed when `listedAsChat`, or with `archived`, when
 *  `archivedChat`. `search` keeps the rows `matchesChat` finds. */
export function chatsOf(input: {
    pod: PodRef;
    conversations: readonly ConversationRef[];
    channels: readonly Channel[];
    me: string | null;
    search?: string;
    chats?: (conversation: ConversationRef) => boolean;
    archived?: boolean;
}): ChatItem[] {
    const listed = input.archived ? archivedChat : listedAsChat;
    const keep = input.chats ?? (() => true);
    const items = [
        ...input.channels.filter(listed).map((channel) => itemOfChannel(channel, input.pod, input.me)),
        ...input.conversations.filter(keep).map((conversation) => itemOfChat(conversation, input.pod)),
    ];
    const needle = input.search?.trim().toLowerCase() ?? "";
    return (needle ? items.filter((item) => matchesChat(item, needle)) : items).sort(newestFirst);
}

/** What waits on the reader, then everything else, each newest first: the
 *  only two groups a chat list has. */
export function byNeed<T extends { needsYou: boolean }>(items: readonly T[]): { needs: T[]; rest: T[] } {
    return { needs: items.filter((item) => item.needsYou), rest: items.filter((item) => !item.needsYou) };
}

/** Every chat across the teammates given, as one list in two groups (the
 *  frame's sidebar and ⌘K). A conversation counts when `isChat`. `search`
 *  also finds every chat of a teammate by its name. */
export function chatItems(input: {
    pods: readonly PodRef[];
    conversations: ReadonlyMap<string, readonly ConversationRef[]>;
    channels: ReadonlyMap<string, readonly Channel[]>;
    me: string | null;
    search?: string;
}): { needs: ChatItem[]; rest: ChatItem[] } {
    const needle = input.search?.trim().toLowerCase() ?? "";
    const items = input.pods.flatMap((pod) => chatsOf({
        pod,
        conversations: input.conversations.get(pod.id) ?? [],
        channels: input.channels.get(pod.id) ?? [],
        me: input.me,
        chats: isChat,
    }));
    const kept = needle ? items.filter((item) => matchesChat(item, needle) || item.podName.toLowerCase().includes(needle)) : items;
    return byNeed(kept.sort(newestFirst));
}

/* ── how a list is cut ────────────────────────────────────────────────── */

export type ChatFilter = "all" | "chats" | "channels" | "automations" | "docs" | "archived";

/** The Chats page's filters, by where a chat came from. Shared chats live on
 *  a channel, so only All and Shared list them; Archived lists the chats on a
 *  channel that were archived, and no conversations. */
export const CHAT_FILTERS: readonly { id: ChatFilter; label: string; kinds: readonly OriginKind[] | null }[] = [
    { id: "all", label: "All", kinds: null },
    { id: "chats", label: "Just you", kinds: ["chat", "ask"] },
    { id: "channels", label: "Shared", kinds: ["channel", "notification"] },
    { id: "automations", label: "Automations", kinds: ["schedule", "workflow", "task"] },
    { id: "docs", label: "On docs", kinds: ["resource"] },
    { id: "archived", label: "Archived", kinds: [] },
];

/** Whether a filter lists the chats that live on a channel. */
export function listsChannels(filter: ChatFilter): boolean {
    return filter === "all" || filter === "channels" || filter === "archived";
}

/** Whether a filter keeps this conversation. */
export function keptBy(filter: ChatFilter): (conversation: Pick<ConversationRef, "origin">) => boolean {
    if (filter === "channels" || filter === "archived") return () => false;
    const kinds = CHAT_FILTERS.find((each) => each.id === filter)?.kinds ?? null;
    return kinds ? (conversation) => kinds.includes((conversation.origin ?? { kind: "chat" }).kind) : () => true;
}

/* ── the phone's older shape ──────────────────────────────────────────── */

/** A row as the phone reads it (`lemma-mobile/src/areas/chats/model.ts`):
 *  the same model, in the shape it had before there was one. */
export type ChatRow =
    | { kind: "chat"; id: string; title: string; at: string | null; chat: ConversationRef }
    | { kind: "channel"; id: string; title: string; at: string | null; channel: Channel };

/** The chats and the channels as one list, most recently active first --
 *  `chatsOf`, for a caller that still reads `ChatRow`s. */
export function chatRows(chats: readonly ConversationRef[], channels: readonly Channel[], space: string): ChatRow[] {
    return chatsOf({ pod: { id: "", name: space }, conversations: chats, channels, me: null }).map((item): ChatRow => item.channel
        ? { kind: "channel", id: "channel:" + item.channel.id, title: item.title, at: item.channel.lastMessageAt ?? item.channel.createdAt, channel: item.channel }
        : { kind: "chat", id: item.conversation!.id, title: item.title, at: item.conversation!.activityAt ?? null, chat: item.conversation! });
}
