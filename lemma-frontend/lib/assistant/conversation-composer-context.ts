import type { AgentRuntimeConfig } from 'lemma-sdk';

export interface ConversationAgentReference {
    id?: string | null;
    name: string;
}

export function resolveConversationAgentName(
    agentId: string | null | undefined,
    agents: ConversationAgentReference[],
): string | null {
    if (!agentId) return null;
    return agents.find((agent) => agent.id === agentId)?.name ?? null;
}

export function resolveHydratedConversationRuntime({
    isNewConversation,
    hasPersistedConversation,
    persistedRuntime,
    controllerRuntime,
}: {
    isNewConversation: boolean;
    hasPersistedConversation: boolean;
    persistedRuntime?: AgentRuntimeConfig | null;
    controllerRuntime?: AgentRuntimeConfig | null;
}): AgentRuntimeConfig | null {
    if (isNewConversation) return controllerRuntime ?? null;
    if (hasPersistedConversation) return persistedRuntime ?? null;
    return controllerRuntime ?? null;
}

export function updateConversationAgentQuery(
    searchParams: string,
    agentName: string | null,
): string {
    const nextParams = new URLSearchParams(searchParams);
    const normalizedAgentName = agentName?.trim() || null;

    if (normalizedAgentName) {
        nextParams.set('agent', normalizedAgentName);
    } else {
        nextParams.delete('agent');
    }

    return nextParams.toString();
}

export function buildScopedConversationHref({
    podId,
    conversationId,
    agentName,
}: {
    podId: string;
    conversationId: string;
    agentName?: string | null;
}): string {
    const normalizedAgentName = agentName?.trim() || null;
    const query = normalizedAgentName
        ? `?agent=${encodeURIComponent(normalizedAgentName)}`
        : '';

    return `/pod/${encodeURIComponent(podId)}/conversations/${encodeURIComponent(conversationId)}${query}`;
}
