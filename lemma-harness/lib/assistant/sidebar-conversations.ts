import type { Agent, Conversation } from '@/lib/types';
import { formatAgentName } from '@/lib/utils/agents';

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

/* Who a conversation was with, as the row's leading mark. The pod's history
   mixes responders — the assistant answers most of it, an agent answers the
   rest — and a list that shows only titles cannot tell you which, so two runs
   both called "hey" are indistinguishable. */
export type ConversationMark =
    | { kind: 'assistant' }
    | { kind: 'agent'; seed: string; label: string; iconUrl?: string | null };

/**
 * A conversation with no `agent_id` was answered by Lem — that is
 * the default responder, not a missing value — so it takes the assistant's own
 * mark rather than falling through to the dot. An id we cannot resolve does
 * fall through: the agent was deleted, or the list has not arrived, and
 * inventing a seeded face for an unknown id would draw a stranger with the
 * confidence of a real one.
 */
export function getConversationMark(
    conversation: Conversation,
    agentsById: Map<string, Agent>,
): ConversationMark | null {
    const agentId = conversation.agent_id;
    if (!agentId) return { kind: 'assistant' };

    const agent = agentsById.get(agentId);
    if (!agent) return null;

    return {
        kind: 'agent',
        seed: agent.name,
        label: formatAgentName(agent.name),
        iconUrl: agent.icon_url,
    };
}
