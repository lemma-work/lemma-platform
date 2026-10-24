import type { OriginKind } from "../../data/origin";
import { routes } from "../../data/routes";
import type { ConversationRef } from "../../data/types";
import { chatsRoutes } from "./routes";
import { answersHint, placeName, sharedWith } from "@web/channels/chat-copy";
import { chatRows, listedAsChat } from "@web/channels/chat-list";
import {
    channelTitle,
    clockTime,
    isMine,
    nameless,
    personName,
    platformsOf,
    sayList,
    sinceShort,
    sourceLabel,
    type Channel,
    type ChannelDetail,
    type Line,
} from "@web/channels/model";
import { platformName, type Starter } from "@web/channels/sources";
import type { Inbox, MentionItem, WaitingQuestion } from "@web/channels/waiting";

/** What the phone's chat screens work out that is not drawing, in the app's
 *  own words. The web's sentences are used where they already say what a
 *  person would (`web/`); where one says "channel", "line", "agent" or
 *  "bot", the phone says it again here, since the phone names a Lemma by its
 *  name and calls what is said in a chat a message. Pure, for the tests. */

/* ── the chat list ─────────────────────────────────────────────────── */

export type ChatFilter = "all" | "chats" | "shared" | "automations" | "docs";

/** The web Chats page's filters (`space/chats-page.tsx`), by where a chat
 *  came from. Shared chats live on a channel, so only All and Shared list them. */
export const CHAT_FILTERS: readonly { id: ChatFilter; label: string; kinds: readonly OriginKind[] | null }[] = [
    { id: "all", label: "All", kinds: null },
    { id: "chats", label: "Just you", kinds: ["chat", "ask"] },
    { id: "shared", label: "Shared", kinds: ["channel", "notification"] },
    { id: "automations", label: "Automations", kinds: ["schedule", "workflow", "task"] },
    { id: "docs", label: "On docs", kinds: ["resource"] },
];

/** What a row's glyph is: a chat with the Lemma, a group, a private group,
 *  a platform it is fed from, the reader's own notices, a contact. */
export type ChatGlyph =
    | { kind: "ring" }
    | { kind: "hash" }
    | { kind: "lock" }
    | { kind: "platform"; platform: string }
    | { kind: "bell" }
    | { kind: "face"; name: string }
    | { kind: "automation" }
    | { kind: "doc" };

export interface ChatEntry {
    key: string;
    title: string;
    /** The second thing said about it: who said what last, or where it came from. */
    said: string | null;
    at: number | null;
    href: string;
    glyph: ChatGlyph;
    needsYou: boolean;
    unread: number;
    running: boolean;
    /** Present for a chat that lives on a channel. */
    channel: Channel | null;
}

export function glyphOf(channel: Pick<Channel, "kind" | "sources" | "person" | "visibility">): ChatGlyph {
    const [platform] = platformsOf(channel);
    if (platform) return { kind: "platform", platform };
    if (channel.kind === "CONTACT") return { kind: "face", name: channel.person?.name ?? "?" };
    if (channel.kind === "DIRECT") return { kind: "bell" };
    if (channel.visibility === "PRIVATE") return { kind: "lock" };
    return { kind: "hash" };
}

function chatGlyph(chat: ConversationRef): ChatGlyph {
    const origin = chat.origin;
    if ((origin.kind === "channel" || origin.kind === "notification") && origin.platform) return { kind: "platform", platform: origin.platform };
    if (origin.kind === "notification") return { kind: "bell" };
    if (origin.kind === "schedule" || origin.kind === "workflow" || origin.kind === "task") return { kind: "automation" };
    if (origin.kind === "resource") return { kind: "doc" };
    return { kind: "ring" };
}

/** The last thing said in a chat on a channel, as a row's second line:
 *  "Priya: Thursday works". */
export function lastSaid(channel: Pick<Channel, "lastLine" | "topic">, me: string | null): string | null {
    const last = channel.lastLine;
    if (!last) return channel.topic;
    const text = last.text.replace(/\s+/g, " ").trim();
    return personName(last.sender, me) + ": " + (text || "…");
}

/** Chats with the Lemma and the chats that live on a channel, as one list,
 *  most recently active first (the web's `chatRows`), cut by the filter. A
 *  channel is matched by its name, as a chat is by its title on the server. */
export function chatEntries(input: {
    podId: string;
    lemmaName: string;
    chats: readonly ConversationRef[];
    channels: readonly Channel[];
    filter: ChatFilter;
    search: string;
    me: string | null;
}): ChatEntry[] {
    const kinds = CHAT_FILTERS.find((one) => one.id === input.filter)?.kinds ?? null;
    const chats = kinds ? input.chats.filter((chat) => kinds.includes(chat.origin.kind)) : input.chats;
    const needle = input.search.trim().toLowerCase();
    const channels = input.filter === "all" || input.filter === "shared"
        ? input.channels.filter(listedAsChat).filter((channel) => !needle || channelTitle(channel, input.lemmaName).toLowerCase().includes(needle))
        : [];
    return chatRows(chats, channels, input.lemmaName).map((row): ChatEntry => {
        if (row.kind === "channel") {
            const channel = row.channel;
            return {
                key: row.id,
                title: row.title,
                said: lastSaid(channel, input.me),
                at: row.at ? Date.parse(row.at) || null : null,
                href: chatsRoutes.shared(input.podId, channel.id),
                glyph: glyphOf(channel),
                needsYou: channel.unread.waiting > 0 || channel.unread.mentions > 0,
                unread: channel.unread.lines,
                running: false,
                channel,
            };
        }
        const chat = row.chat;
        return {
            key: "chat:" + chat.id,
            title: chat.title,
            said: chat.origin.kind === "chat" ? null : chat.origin.label,
            at: chat.activityAt ? Date.parse(chat.activityAt) || null : null,
            href: routes.chat(input.podId, chat.id),
            glyph: chatGlyph(chat),
            needsYou: chat.waitingOnYou,
            unread: 0,
            running: (chat.status ?? "").toUpperCase() === "RUNNING",
            channel: null,
        };
    });
}

/* ── waiting on you ────────────────────────────────────────────────── */

export type WaitingEntry =
    | { kind: "question"; key: string; question: WaitingQuestion }
    | { kind: "mention"; key: string; mention: MentionItem }
    | { kind: "direct"; key: string; channel: Channel; count: number };

/** What the web's bell lists, in its order: new messages in the reader's own
 *  chat with the Lemma, the questions waiting on them, then the messages
 *  naming them. */
export function waitingEntries(inbox: Inbox | null | undefined, mine: Channel | null): WaitingEntry[] {
    const out: WaitingEntry[] = [];
    if (mine && mine.unread.lines > 0) out.push({ kind: "direct", key: "direct:" + mine.id, channel: mine, count: mine.unread.lines });
    for (const question of inbox?.items ?? []) out.push({ kind: "question", key: "q:" + question.id, question });
    for (const mention of inbox?.mentioned ?? []) out.push({ kind: "mention", key: "m:" + mention.id, mention });
    return out;
}

/** Where a question or a mention was said, in the phone's words. */
export function whereSaid(item: { channelKind: Channel["kind"]; channelTitle: string | null }): string {
    if (item.channelKind === "DIRECT") return "Sent to you";
    return item.channelTitle ? "In " + item.channelTitle.replace(/^#/, "") : "In a shared chat";
}

/** "1 new message", "12 new messages". */
export function newMessages(count: number): string {
    return count === 1 ? "1 new message" : (count > 99 ? "99+" : count) + " new messages";
}

/* ── one chat ──────────────────────────────────────────────────────── */

/** What the box says before anything is typed: what it does here. */
export function placeholderFor(channel: Pick<Channel, "kind">, lemmaName: string): string {
    if (channel.kind === "DIRECT") return "Message " + lemmaName;
    if (channel.kind === "CONTACT") return "Write a reply";
    return "Message everyone, or type @ to ask " + lemmaName;
}

/** The first thing at the top of a chat's history: what it is, who answers
 *  and how to ask. */
export function introOf(channel: ChannelDetail, lemmaName: string, me: string | null): string {
    const places = channel.places.filter((one) => !one.pending);
    if (channel.kind === "DIRECT") return "Just you and " + lemmaName + ". " + answersHint(channel, lemmaName) + " Nobody else reads it.";
    if (channel.kind === "CONTACT") {
        const via = [...new Set(places.map((one) => platformName(one.platform)))];
        const who = channel.person?.name ?? (nameless(channel) === "A website visitor" ? "This visitor" : "This person");
        return who + " is outside " + lemmaName + (via.length ? " and writes by " + sayList(via) : "") + ". Your replies go back the same way.";
    }
    if (places.length) return "This chat mirrors " + sayList(places.map(placeName)) + ". Messages there show up here, and what you send here goes back. " + answersHint(channel, lemmaName);
    const who = sharedWith(channel.members, me);
    if (who === "Just you") return "Just you so far. Add people to talk here together; anyone in it can type @ to ask " + lemmaName + ".";
    const since = new Date(channel.createdAt);
    const day = Number.isNaN(since.getTime()) ? "" : ", since " + since.toLocaleDateString([], { day: "numeric", month: "long" });
    return who + day + ". " + answersHint(channel, lemmaName);
}

/** Who answers in a chat when nobody is named (the web's `whoAnswers`). */
export function whoAnswers(channel: Pick<Channel, "kind" | "assignee" | "agentsAnswer" | "handBackAfterHours">, lemmaName: string, me: string | null): string {
    if (channel.kind === "CONTACT" && !channel.agentsAnswer) return lemmaName + " is off here: only people answer them.";
    if (channel.assignee) {
        const name = personName(channel.assignee, me);
        const back = lemmaName + " answers again " + channel.handBackAfterHours + " hours after " + (name === "You" ? "your" : "their") + " last message.";
        return (name === "You" ? "You are answering here; " : name + " is answering here; ") + back;
    }
    if (channel.kind === "DIRECT") return lemmaName + " answers everything you write here.";
    if (channel.kind === "CONTACT") return lemmaName + " answers them. Write here to answer them yourself.";
    return "Type @ to ask " + lemmaName + ".";
}

/** A contact's chat, where it stands, in one sentence, and the one move
 *  that changes it: take over from the Lemma, or hand back to it (the web's
 *  `ContactStrip`). */
export function contactStrip(channel: ChannelDetail, me: string | null, lemmaName: string, mayAct: boolean, now = new Date()): {
    state: string;
    asks: boolean;
    act: { to: "me" | null; label: string } | null;
} {
    const asks = Boolean(channel.attention && !channel.assignee);
    const mine = Boolean(me) && channel.assignee?.id === me;
    const open = channel.status !== "closed";
    const state = asks
        ? "Needs a person" + (channel.attention?.reason ? ": " + channel.attention.reason : "") + " · " + sinceShort(channel.attention?.since, now)
        : channel.assignee
            ? (mine ? "You are answering." : (channel.assignee.name ?? "Someone") + " is answering.")
                + (channel.agentsAnswer ? " " + lemmaName + " takes over after " + channel.handBackAfterHours + " quiet hours." : "")
            : !channel.agentsAnswer
                ? lemmaName + " is off here. Only people answer."
                : channel.status === "waiting_on_contact" ? "Waiting on their reply. " + lemmaName + " answers when they write."
                    : !open ? "Closed." : lemmaName + " is answering.";
    const act = !mayAct || !open
        ? null
        : !channel.assignee
            ? { to: "me" as const, label: channel.agentsAnswer ? "Take over" : "I’ll answer" }
            : mine && channel.agentsAnswer ? { to: null, label: "Hand back to " + lemmaName } : null;
    return { state, asks, act };
}

/** A line under the header: the topic, where it is mirrored, or who is in it. */
export function subtitleOf(channel: ChannelDetail, lemmaName: string, me: string | null): string {
    if (channel.kind === "DIRECT") return lemmaName;
    if (channel.kind === "CONTACT") return "A contact of " + lemmaName;
    if (channel.topic) return channel.topic;
    const places = channel.places.filter((one) => !one.pending);
    if (places.length) return sayList(places.map(placeName));
    return sharedWith(channel.members, me) + " · " + lemmaName;
}

/* ── one message ───────────────────────────────────────────────────── */

/** What holding a message offers: reply in its thread, copy, change, delete
 *  or remove, share an answer kept to the reader (the web's line tools).
 *  Said or sent outside already, a message is not changed here: the copy
 *  there would not change with it. */
export interface LineOffers {
    reply: boolean;
    copy: boolean;
    edit: boolean;
    remove: boolean;
    share: boolean;
    /** Asked before a delete, as one sentence. */
    removeAsk: string;
    /** The same as a question and what follows from it, for a dialog. */
    removeTitle: string;
    removeNote: string;
    /** "Delete" for one's own; "Remove for everyone" for somebody else's. */
    removeLabel: string;
}

/** Where a message is outside the app already: where it was said, and
 *  where one's own went. "Acme × Northwind (WhatsApp)". */
export function outsideOf(line: Pick<Line, "sourceId" | "delivered">, channel: Pick<ChannelDetail, "places">): string | null {
    const places = [
        ...(line.sourceId ? [sourceLabel(line.sourceId, channel.places) ?? "the place it was said"] : []),
        ...(line.delivered ?? []).filter((one) => one.status === "SENT").map((one) => sourceLabel(one.sourceId, channel.places) ?? (one.platform ? platformName(one.platform) : "outside")),
    ];
    return places.length ? sayList([...new Set(places)]) : null;
}

export function lineOffers(line: Line, channel: ChannelDetail, me: string | null, mayWrite: boolean, inThread: boolean): LineOffers {
    const mine = isMine(line, me) && mayWrite;
    const removable = !line.deleted && (mine || (mayWrite && channel.me.canManage));
    const outside = outsideOf(line, channel);
    const name = personName(line.sender, me);
    return {
        reply: !inThread && !line.deleted && !line.threadRootId,
        copy: Boolean(line.text) && !line.deleted && !line.withheld,
        edit: mine && line.text !== null && !outside,
        remove: removable,
        share: line.canShare,
        removeAsk: (mine ? "Delete this message?" : "Remove " + name + "’s message for everyone?") + (outside ? " It goes from here only; the copy in " + outside + " stays." : ""),
        removeTitle: mine ? "Delete this message?" : "Remove " + name + "’s message?",
        removeNote: outside ? "It goes from here only; the copy in " + outside + " stays." : "It goes for everyone in this chat.",
        removeLabel: mine ? "Delete" : "Remove for everyone",
    };
}

/** What a deleted message says in its place: who removed it, when that was
 *  not who wrote it. */
export function removedNote(line: Pick<Line, "removedBy">, me: string | null): string {
    if (!line.removedBy) return "This message was deleted.";
    const who = me && line.removedBy.id === me ? "you" : personName(line.removedBy, me);
    return "Removed by " + who + ".";
}

/** When a thread's newest reply was written: today or yesterday at a time,
 *  else "on" and the day. */
export function lastReplySaid(iso: string, now = new Date()): string {
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return "";
    const day = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
    const back = Math.round((day(now) - day(at)) / 86_400_000);
    if (back === 0) return "today at " + clockTime(iso);
    if (back === 1) return "yesterday at " + clockTime(iso);
    return "on " + at.toLocaleDateString([], at.getFullYear() === now.getFullYear() ? { day: "numeric", month: "long" } : { day: "numeric", month: "long", year: "numeric" });
}

/** "3 replies · Last reply today at 10:02". */
export function threadSaid(count: number, lastAt: string | null, now = new Date()): string {
    const replies = count === 1 ? "1 reply" : count + " replies";
    return lastAt ? replies + " · last " + lastReplySaid(lastAt, now) : replies;
}

/** Who a message is from, as its head says it; in a contact's chat, a
 *  contact nobody named is called what the chat is. */
export function whoSaid(line: Pick<Line, "sender">, channel: Pick<Channel, "kind" | "title" | "person" | "sources">, me: string | null): string {
    if (channel.kind === "CONTACT" && line.sender.kind === "CONTACT" && !line.sender.name) return channelTitle(channel, "");
    return personName(line.sender, me);
}

/** "answering Priya" beside the Lemma's name, when it answered somebody
 *  other than the reader, with their access. Not in a contact's chat, where
 *  everything is with them. */
export function answeringWhom(line: Pick<Line, "sender" | "onBehalfOf">, channel: Pick<Channel, "kind">, me: string | null): string | null {
    if (line.sender.kind !== "AGENT" || !line.onBehalfOf || channel.kind === "CONTACT") return null;
    const whom = personName(line.onBehalfOf, me);
    return whom === "You" ? null : "answering " + whom;
}

/** Where a message was said, when it matters: not in a contact's chat that
 *  has only one way in. */
export function viaOf(line: Pick<Line, "sourceId">, channel: Pick<ChannelDetail, "kind" | "places">): string | null {
    const oneWay = channel.kind === "CONTACT" && new Set(channel.places.map((one) => one.platform)).size <= 1;
    return oneWay ? null : sourceLabel(line.sourceId, channel.places);
}

/** When something last happened, said after "last message": "12 min
 *  ago", "yesterday", "on 3 Oct" -- never "Yesterday ago". */
export function agoSaid(iso: string | null | undefined, now = new Date()): string {
    const short = sinceShort(iso, now);
    if (!short) return "";
    if (short === "Now") return "just now";
    if (short === "Yesterday") return "yesterday";
    if (/^\d+ (min|h|days)$/.test(short)) return short + " ago";
    return "on " + short;
}

/* ── reading ───────────────────────────────────────────────────────── */

/** Where a message sits in the scrolled list, by its place in the chat. */
export interface LineBox {
    seq: number;
    y: number;
    height: number;
}

/** The highest `seq` on screen: a message counts as seen once most of it is
 *  in view, or it fills half the view (the web's `useSeenSeq`). */
export function seenOnScreen(boxes: Iterable<LineBox>, top: number, viewport: number): number {
    let best = 0;
    const bottom = top + viewport;
    for (const box of boxes) {
        if (box.seq <= 0 || box.height <= 0) continue;
        const shown = Math.min(bottom, box.y + box.height) - Math.max(top, box.y);
        if (shown <= 0) continue;
        if (shown / box.height >= 0.6 || shown >= viewport / 2) best = Math.max(best, box.seq);
    }
    return best;
}

/* ── starting a chat with people ───────────────────────────────────── */

/** What the start menu says of one platform (the web's `StartChat`). */
export function starterWords(starter: Pick<Starter, "platform" | "state">, lemmaName: string): { title: string; note: string } {
    const name = platformName(starter.platform);
    if (starter.state === "missing") return { title: "Connect " + name + " first", note: "Then " + lemmaName + " can join " + (starter.platform === "SLACK" ? "Slack channels" : "groups") + " there." };
    if (starter.state === "unwell") return { title: name + " needs attention", note: "Open its settings first." };
    if (starter.platform === "WHATSAPP") return { title: "Start a WhatsApp group", note: lemmaName + " creates it; you share the link." };
    if (starter.platform === "TELEGRAM") return { title: "Add " + lemmaName + " to a Telegram group", note: "Pick the group in Telegram." };
    return { title: "Add " + lemmaName + " to a Slack channel", note: "Invite it there with one command." };
}

/* ── the people in a Lemma ─────────────────────────────────────────── */

export interface PersonRow {
    userId: string;
    name: string;
    email: string | null;
    role: string;
    here: boolean;
    you: boolean;
}

/** The reader first, then whoever has the app open, then everyone else by name. */
export function peopleRows(people: readonly { userId: string; name: string; email: string | null; role: string }[], online: ReadonlySet<string>, me: string | null): PersonRow[] {
    return people
        .map((one) => ({ ...one, here: online.has(one.userId), you: one.userId === me }))
        .sort((a, b) => Number(b.you) - Number(a.you) || Number(b.here) - Number(a.here) || a.name.localeCompare(b.name));
}

/* ── sentences the web says with words the phone does not use ──────── */

/** Whom one of the Lemma's own numbers or addresses answers when a stranger
 *  writes to it (the web's `answerChoices`). */
export function botAnswerChoices(lemmaName: string): { value: "off" | "known" | "anyone"; label: string; note: string }[] {
    return [
        { value: "off", label: "Only people in " + lemmaName, note: "Anyone outside " + lemmaName + " is turned away." },
        { value: "known", label: "People in it and contacts", note: "People already in Contacts are answered too. Strangers are turned away." },
        { value: "anyone", label: "Everyone", note: "Strangers are answered too, and join Contacts with their first message." },
    ];
}

/** What a person is told after writing to a contact first (the web's
 *  `outreachSaid`). */
export function followUpSaid(sent: { status: string; reason: string | null; platform: string | null }): string {
    if (sent.platform === "WEB" && sent.status === "SENT") return "In their chat on your website, for when they next open it.";
    if (sent.platform === "WEB" && sent.status === "PENDING") return "In their chat, on its way to your website.";
    const where = sent.platform === "WHATSAPP" ? "WhatsApp" : sent.platform === "TELEGRAM" ? "Telegram" : "email";
    const why = sent.reason ? ": " + sent.reason.replace(/\.$/, "") : "";
    switch (sent.status) {
        case "SENT": return "Sent by " + where + ". It is in their chat.";
        case "PENDING": return "In their chat, on its way by " + where + ".";
        case "SKIPPED": return "In their chat, but not sent" + why + ".";
        default: return "In their chat, but it did not go" + why + ". Try it again from there.";
    }
}

/** Whether a group shows the Lemma's answers to everybody in it. */
export function answersSharedSaid(shared: boolean, lemmaName: string): { label: string; note: string } {
    return {
        label: "Show " + lemmaName + "’s answers to everyone here",
        note: shared
            ? "Everyone here reads what " + lemmaName + " answers, even when it used the asker’s own access."
            : "An answer stays with whoever asked until they share it. Everyone else sees that it was answered, not what it said.",
    };
}

/* ── refusals ──────────────────────────────────────────────────────── */

/** The HTTP status a refusal kept, or null. */
export function statusOf(error: unknown): number | null {
    const code = (error as { statusCode?: unknown } | null)?.statusCode;
    return typeof code === "number" ? code : null;
}

export const isForbidden = (error: unknown) => statusOf(error) === 403;
