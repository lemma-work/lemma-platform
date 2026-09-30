import { platformName } from "./conversation-origin";

/** A conversation that lives on a chat platform as well as here.
 *
 *  What is typed into one goes to the bot either way; what differs is where
 *  the bot's answer lands. Without a mark, the backend posts it to the
 *  platform — the group reads it. With `private_note`, the answer stays in
 *  Lemma and nothing is sent. So the composer asks, and the default is the one
 *  that cannot say something in front of a group by accident. */

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

/** The composer's two ways to send, the note first because it is the default. */
export function replyChoices(thread: ChannelThread, bot: string): ReplyChoice[] {
    const lands = thread.kind === "DM" ? "goes to " + platformName(thread.platform)
        : thread.kind === "EMAIL" ? "goes out by email"
        : HASHED.has(thread.platform) ? "goes to " + thread.place
        : "goes to the group";
    return [
        { mode: "note", label: "Note to " + bot, hint: "Only " + bot + " sees this." },
        {
            mode: "reply",
            label: thread.kind === "EMAIL" ? "Reply by email" : "Reply in " + thread.place,
            hint: bot + "’s answer " + lands + ".",
        },
    ];
}

/** What a message carries: the note mark, or nothing at all — never a
 *  `private_note: false`, which would be a second way to say "reply". */
export function sendMetadata(thread: ChannelThread | null, mode: ReplyMode): Record<string, unknown> | undefined {
    return thread && mode === "note" ? { private_note: true } : undefined;
}
