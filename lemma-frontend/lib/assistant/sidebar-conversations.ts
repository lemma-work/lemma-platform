import type { Conversation } from '@/lib/types';

/**
 * How many conversations the sidebar shows. It is a recency preview, not the
 * archive — the full history lives on the pod's conversations page.
 */
export const SIDEBAR_CONVERSATION_LIMIT = 15;

function conversationTime(conversation: Conversation): number {
    const value = conversation.updated_at || conversation.created_at;
    const timestamp = value ? new Date(value).getTime() : 0;
    return Number.isFinite(timestamp) ? timestamp : 0;
}

/**
 * The workspace sidebar has a lightweight pod-wide history query so it can
 * render on a cold resource route. When the assistant controller is active it
 * may also contain fresher local state. Merge both without selecting anything,
 * then recency order for stable sidebar placement.
 *
 * The controller is live for exactly one conversation — the one it is driving —
 * and for conversations it has just created, which the server list has not seen
 * yet. Everywhere else it holds whatever it last observed before you navigated
 * away, which is a snapshot with no clock on it: a run it watched start but not
 * finish stays `running` in that copy for the rest of the session. Letting those
 * records outrank the server made one stale row unfixable by any refetch, so
 * they lose to history and only their recency survives.
 */
export function mergeSidebarConversations(
    history: Conversation[],
    controller: Conversation[],
    liveConversationId?: string | null,
): Conversation[] {
    const conversationsById = new Map<string, Conversation>();
    history.forEach((conversation) => conversationsById.set(conversation.id, conversation));

    controller.forEach((conversation) => {
        const known = conversationsById.get(conversation.id);

        if (!known || conversation.id === liveConversationId) {
            conversationsById.set(conversation.id, conversation);
            return;
        }

        // History is the truth for this row, but the controller can have moved
        // it since that fetch. Keeping the later timestamp stops a row from
        // visibly dropping down the list and climbing back on the next refetch.
        if (conversationTime(conversation) > conversationTime(known)) {
            conversationsById.set(conversation.id, {
                ...known,
                updated_at: conversation.updated_at,
            });
        }
    });

    return Array.from(conversationsById.values())
        .sort((left, right) => conversationTime(right) - conversationTime(left));
}

/**
 * Client-side title filter over the rows the sidebar already holds. Deliberately
 * not a server search: it narrows the preview in front of you rather than
 * promising to reach the rest of the history, which lives on its own page.
 */
export function filterSidebarConversations(
    conversations: Conversation[],
    query: string,
): Conversation[] {
    const needle = query.trim().toLowerCase();
    if (!needle) return conversations;

    return conversations.filter((conversation) =>
        (conversation.title || 'Untitled conversation').toLowerCase().includes(needle));
}
