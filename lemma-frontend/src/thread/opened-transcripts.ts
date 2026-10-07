import { normalizeConversationStatus, type Conversation, type ConversationMessage } from "lemma-sdk";
import { lemma } from "@/session/client";
import { NEW_CONVERSATION, source } from "@/data";
import { TranscriptCache } from "./transcript-cache";

/** The conversations this page has had open, for the pane to draw at once on
 *  the way back to one. See `transcript-cache.ts`. Cleared with the query
 *  cache whenever the person signed in changes. */
export const openedTranscripts = new TranscriptCache<ConversationMessage, Conversation>();

/** How long a pointer has to rest on a conversation before it is fetched:
 *  long enough that sweeping across the list does not fetch every row. */
const INTENT_MS = 120;

/** Read a conversation into the cache ahead of it being opened. */
export function prefetchConversation(podId: string, conversationId: string | null | undefined): void {
    if (!conversationId || conversationId === NEW_CONVERSATION || source.label !== "live") return;
    const client = lemma(podId);
    void openedTranscripts.prefetch(podId, conversationId, async () => {
        const [conversation, page] = await Promise.all([
            client.conversations.get(conversationId, { pod_id: podId }),
            client.conversations.messages.list(conversationId, { limit: 100 }),
        ]);
        return {
            conversation,
            status: normalizeConversationStatus(typeof conversation.status === "string" ? conversation.status : undefined),
            messages: page.items ?? [],
            olderToken: page.next_page_token ?? null,
        };
    });
}

/** The handlers a conversation row spreads onto itself to be fetched when
 *  someone is about to open it: resting on it, or focusing it. */
export function prefetchOnIntent(podId: string, conversationId: string) {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const cancel = () => {
        if (timer) clearTimeout(timer);
        timer = null;
    };
    return {
        onPointerEnter: () => {
            cancel();
            timer = setTimeout(() => prefetchConversation(podId, conversationId), INTENT_MS);
        },
        onPointerLeave: cancel,
        onFocus: () => prefetchConversation(podId, conversationId),
    };
}
