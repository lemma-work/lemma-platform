import type { Conversation } from '@/lib/types';

function conversationTime(conversation: Conversation): number {
    const value = conversation.updated_at || conversation.created_at;
    const timestamp = value ? new Date(value).getTime() : 0;
    return Number.isFinite(timestamp) ? timestamp : 0;
}

/**
 * The workspace sidebar has a lightweight pod-wide history query so it can
 * render on a cold resource route. When the assistant controller is active it
 * may also contain fresher local state. Merge both without selecting anything;
 * controller records win for status/model changes, then the list is recency
 * ordered for stable sidebar placement.
 */
export function mergeSidebarConversations(
    history: Conversation[],
    controller: Conversation[],
): Conversation[] {
    const conversationsById = new Map<string, Conversation>();
    history.forEach((conversation) => conversationsById.set(conversation.id, conversation));
    controller.forEach((conversation) => conversationsById.set(conversation.id, conversation));

    return Array.from(conversationsById.values())
        .sort((left, right) => conversationTime(right) - conversationTime(left));
}
