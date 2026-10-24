import { useLocalSearchParams } from "expo-router";
import { SharedChatScreen } from "../../../../areas/chats/chat-view";

/** A chat that lives on a channel: someone's own chat with a Lemma (on
 *  WhatsApp, Telegram, or its notices), a group made here or mirrored from a
 *  platform, or a contact's chat (PARITY.md, E). `line` opens it at one
 *  message; `thread` opens that message's thread over it. */
export default function SharedChat() {
    const params = useLocalSearchParams<{ podId: string; id: string; line?: string; thread?: string }>();
    return <SharedChatScreen podId={String(params.podId)} channelId={String(params.id)} focusLine={params.line ? String(params.line) : null} focusThread={params.thread ? String(params.thread) : null} />;
}
