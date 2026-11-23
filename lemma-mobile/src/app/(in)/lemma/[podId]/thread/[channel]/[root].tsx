import { useLocalSearchParams } from "expo-router";
import { ThreadScreen } from "../../../../../../areas/chats/thread-view";

/** One thread of a chat, by its address (PARITY.md, E): it opens the chat
 *  with the thread drawn up over it as a drawer. `line` is a reply to go
 *  to, from the bell or search. */
export default function Thread() {
    const params = useLocalSearchParams<{ podId: string; channel: string; root: string; line?: string }>();
    return <ThreadScreen podId={String(params.podId)} channelId={String(params.channel)} rootId={String(params.root)} focusLine={params.line ? String(params.line) : null} />;
}
