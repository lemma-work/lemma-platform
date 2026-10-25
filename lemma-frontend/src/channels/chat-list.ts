import type { ConversationRef } from "@/data";
import { channelTitle, type Channel } from "./model";

/** One list of everywhere you talk with the teammate: your chats with it,
 *  and the chats that live on a channel -- your default chat (your private
 *  line, fed by WhatsApp or Telegram, and what it sends you), the chats
 *  shared with people in the space, and the groups it is in on a platform.
 *  A contact's chat is not here: those are a support queue, in Contacts. */
/** What the list reads of a chat; a row hands back the chat it was given. */
type ListedChat = Pick<ConversationRef, "id" | "title" | "activityAt">;

export type ChatRow<C extends ListedChat = ConversationRef> =
    | { kind: "chat"; id: string; title: string; at: string | null; chat: C }
    | { kind: "channel"; id: string; title: string; at: string | null; channel: Channel };

/** Whether a channel is a chat in the list. The default chat is listed once
 *  something is in it, or it is reached from a platform. */
export function listedAsChat(channel: Channel): boolean {
    if (channel.kind === "CONTACT" || channel.archived) return false;
    if (channel.kind === "DIRECT") return channel.sources.length > 0 || channel.lastLine !== null;
    return true;
}

/** The chats and the channels as one list, most recently active first. A
 *  row with no time keeps its place after those with one. */
export function chatRows<C extends ListedChat = ConversationRef>(chats: readonly C[], channels: readonly Channel[], space: string): ChatRow<C>[] {
    const rows: ChatRow<C>[] = [
        ...channels.filter(listedAsChat).map((channel): ChatRow<C> => ({
            kind: "channel",
            id: "channel:" + channel.id,
            title: channelTitle(channel, space),
            at: channel.lastMessageAt ?? channel.createdAt,
            channel,
        })),
        ...chats.map((chat): ChatRow<C> => ({ kind: "chat", id: chat.id, title: chat.title, at: chat.activityAt ?? null, chat })),
    ];
    return rows
        .map((row, index) => ({ row, index, time: row.at ? Date.parse(row.at) : Number.NaN }))
        .sort((a, b) => {
            const aKnown = Number.isFinite(a.time);
            const bKnown = Number.isFinite(b.time);
            if (aKnown && bKnown) return b.time - a.time || a.index - b.index;
            if (aKnown !== bKnown) return aKnown ? -1 : 1;
            return a.index - b.index;
        })
        .map(({ row }) => row);
}
