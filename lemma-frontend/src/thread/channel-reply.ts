import { key, type KeyValueStore } from "@/session/storage";
import { platformName } from "./conversation-origin";

/** A conversation that lives on a chat platform as well as here.
 *
 *  What is typed into one goes to the bot either way; what differs is where
 *  the bot's answer lands. Without a mark, the backend posts it to the
 *  platform — the group reads it. With `private_note`, the answer stays in
 *  Lemma and nothing is sent. So the composer asks. In a group or a channel
 *  it starts on the note, which cannot say something in front of the group
 *  by accident; in a person's own DM or email thread it starts on the reply,
 *  because the only one there to answer is the person typing. Whatever they
 *  choose is kept for that conversation, in this browser. */

export type ReplyMode = "note" | "reply";

export interface ChannelThread {
    /** TELEGRAM, SLACK, … — for the platform's mark. */
    platform: string;
    /** Where a reply lands, in words: "Design partners", "#launch", "WhatsApp". */
    place: string;
    /** DM | CHANNEL | EMAIL, when the backend said. */
    kind: string | null;
    /** The group's people from outside the space ask in here. */
    outsiders: boolean;
}

export interface ReplyChoice {
    mode: ReplyMode;
    label: string;
    /** One line under the composer: what this choice does. */
    hint: string;
}

/** Platforms whose channels are #named. A group on Telegram is just a name. */
const HASHED = new Set(["SLACK", "TEAMS"]);

function text(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

/** Null for a conversation that lives only in Lemma. */
export function channelThread(metadata: Record<string, unknown> | null | undefined): ChannelThread | null {
    const meta = metadata ?? {};
    const platform = text(meta.surface_platform)?.toUpperCase();
    if (!platform) return null;
    const channel = text(meta.channel_name);
    const place = channel ? (HASHED.has(platform) ? "#" + channel.replace(/^#/, "") : channel) : platformName(platform);
    return {
        platform,
        place,
        kind: text(meta.conversation_kind)?.toUpperCase() ?? null,
        outsiders: text(meta.audience) === "outsiders",
    };
}

/** The person's own conversation with the bot: their DM, or their email
 *  thread. Nobody else reads a reply there. */
function ownThread(thread: ChannelThread): boolean {
    return thread.kind === "DM" || thread.kind === "EMAIL";
}

/** The composer's two ways to send, always in this order: the note, then
 *  the reply. Which is chosen is `replyModeFor`'s to say. */
export function replyChoices(thread: ChannelThread, bot: string): ReplyChoice[] {
    const lands = thread.kind === "DM" ? "goes to " + platformName(thread.platform)
        : thread.kind === "EMAIL" ? "goes out by email"
        : HASHED.has(thread.platform) ? "goes to " + thread.place
        : "goes to the group";
    /* In a group a note is kept from the people in it. In a person's own DM
       there is nobody to keep it from — only nothing sent — so it says that,
       rather than sounding like a secret from the chat. */
    const sent = thread.kind === "DM" ? " Nothing is sent to " + platformName(thread.platform) + "."
        : thread.kind === "EMAIL" ? " No email is sent."
        : "";
    return [
        { mode: "note", label: "Note to " + bot, hint: "Only " + bot + " sees this." + sent },
        {
            mode: "reply",
            label: thread.kind === "EMAIL" ? "Reply by email" : "Reply in " + thread.place,
            hint: bot + "’s answer " + lands + ".",
        },
    ];
}

/** Where a conversation starts before anybody chose: a reply in the
 *  person's own DM or email thread, a note anywhere others read along — and
 *  a note where the backend did not say which this is. */
export function defaultReplyMode(thread: ChannelThread): ReplyMode {
    return ownThread(thread) ? "reply" : "note";
}

/** The choice on screen: the one last made in this conversation, else its
 *  default. */
export function replyModeFor(thread: ChannelThread, remembered: ReplyMode | null): ReplyMode {
    return remembered ?? defaultReplyMode(thread);
}

/* ── kept per conversation, in this browser ────────────────────────────── */

type Store = Pick<KeyValueStore, "getItem" | "setItem">;

/** One entry under the app's prefix for every conversation's choice, most
 *  recently chosen last; past this many, the longest-untouched is dropped.
 *  A list rather than an object, so the order is the order chosen whatever
 *  an id looks like. */
const STORED = "reply-modes";
const KEPT = 200;

function isMode(value: unknown): value is ReplyMode {
    return value === "note" || value === "reply";
}

function readKept(store: Store | null): [string, ReplyMode][] {
    try {
        const raw = store?.getItem(key(STORED));
        const parsed: unknown = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed)
            ? parsed.filter((entry): entry is [string, ReplyMode] =>
                Array.isArray(entry) && typeof entry[0] === "string" && isMode(entry[1]))
            : [];
    } catch {
        /* No storage, refused, or something else's words under the key. */
        return [];
    }
}

/** The last choice made in this conversation in this browser, or null. */
export function rememberedReplyMode(store: Store | null, conversationId: string | null): ReplyMode | null {
    if (!conversationId) return null;
    return readKept(store).find(([id]) => id === conversationId)?.[1] ?? null;
}

/** Keep the choice made in a conversation. Without storage, or where it
 *  refuses, nothing is kept and the choice lasts as long as the page. */
export function rememberReplyMode(store: Store | null, conversationId: string | null, mode: ReplyMode): void {
    if (!store || !conversationId) return;
    const kept = readKept(store).filter(([id]) => id !== conversationId);
    kept.push([conversationId, mode]);
    try {
        store.setItem(key(STORED), JSON.stringify(kept.slice(-KEPT)));
    } catch {
        /* Full, or refused: the page still has it. */
    }
}

/** This browser's storage, or null where there is none — a server render, or
 *  a browser that refuses it. */
export function browserStore(): Store | null {
    try {
        return typeof localStorage === "undefined" ? null : localStorage;
    } catch {
        return null;
    }
}

/** What a message carries: the note mark, or nothing at all — never a
 *  `private_note: false`, which would be a second way to say "reply". */
export function sendMetadata(thread: ChannelThread | null, mode: ReplyMode): Record<string, unknown> | undefined {
    return thread && mode === "note" ? { private_note: true } : undefined;
}
