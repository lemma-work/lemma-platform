'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type {
    Assistant,
    Conversation,
    ConversationModel,
    CreateAssistantData,
    Message,
    PaginatedResponse,
    UpdateAssistantData,
} from '@/lib/types';
import { getLemmaClient } from '../sdk/lemma-client';

export interface ConversationScope {
    podId?: string | null;
    agentName?: string | null;
    assistantName?: string | null;
    assistantId?: string | null; // deprecated alias
    organizationId?: string | null;
}

function asPaginatedArray<T>(response: unknown): PaginatedResponse<T> {
    if (Array.isArray(response)) {
        return {
            items: response as T[],
            limit: response.length,
            total: response.length,
            next_page_cursor: null,
        };
    }
    return response as PaginatedResponse<T>;
}

function normalizeAgentAssistant(raw: Record<string, unknown>): Assistant {
    const toolsets = (raw.toolsets as Assistant['tool_sets'] | undefined) || (raw.tool_sets as Assistant['tool_sets'] | undefined) || [];
    return {
        id: String(raw.id || ''),
        pod_id: String(raw.pod_id || ''),
        user_id: String(raw.user_id || ''),
        name: String(raw.name || ''),
        description: (raw.description as string | null | undefined) ?? null,
        icon_url: (raw.icon_url as string | null | undefined) ?? null,
        instruction: String(raw.instruction || ''),
        input_schema: (raw.input_schema as Record<string, unknown> | null | undefined) || {},
        output_schema: (raw.output_schema as Record<string, unknown> | null | undefined) || {},
        tool_sets: toolsets,
        toolsets,
        accessible_tables: (raw.accessible_tables as Assistant['accessible_tables'] | undefined) || [],
        accessible_folders: (raw.accessible_folders as string[] | undefined) || [],
        accessible_connectors: (raw.accessible_connectors as Assistant['accessible_connectors'] | undefined) || [],
        agent_names: (raw.agent_names as string[] | undefined) || [],
        function_names: (raw.function_names as string[] | undefined) || [],
        created_at: String(raw.created_at || ''),
        updated_at: String(raw.updated_at || raw.created_at || ''),
    } as Assistant;
}

function toAgentPayload<T extends CreateAssistantData | UpdateAssistantData>(data: T) {
    const { tool_sets: toolSetsAlias, toolsets, ...rest } = data;
    return {
        ...rest,
        toolsets: toolsets ?? toolSetsAlias ?? undefined,
    };
}

export function useAssistants(podId: string, params?: { limit?: number; cursor?: string }) {
    return useQuery({
        queryKey: ['assistants', podId, params],
        queryFn: async () => {
            const response = await getLemmaClient(podId).agents.list({
                limit: params?.limit,
                pageToken: params?.cursor,
            }) as {
                items?: unknown[];
                next_page_token?: string | null;
            };

            return {
                items: (response.items || []).map((item) => normalizeAgentAssistant(item as Record<string, unknown>)),
                limit: params?.limit || 100,
                next_page_cursor: response.next_page_token,
            } as PaginatedResponse<Assistant>;
        },
        enabled: !!podId,
    });
}

export function useAssistant(podId: string, assistantName: string) {
    return useQuery({
        queryKey: ['assistants', podId, assistantName],
        queryFn: async () => normalizeAgentAssistant(await getLemmaClient(podId).agents.get(assistantName) as unknown as Record<string, unknown>),
        enabled: !!podId && !!assistantName,
    });
}

export function useCreateAssistant() {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, data }: { podId: string; data: CreateAssistantData }) =>
            getLemmaClient(podId).agents.create(toAgentPayload(data)).then((response) =>
                normalizeAgentAssistant(response as unknown as Record<string, unknown>)
            ),
        onSuccess: (_, { podId }) => {
            queryClient.invalidateQueries({ queryKey: ['assistants', podId] });
        },
    });
}

export function useUpdateAssistant() {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, assistantName, data }: { podId: string; assistantName: string; data: UpdateAssistantData }) =>
            getLemmaClient(podId).agents.update(assistantName, toAgentPayload(data)).then((response) =>
                normalizeAgentAssistant(response as unknown as Record<string, unknown>)
            ),
        onSuccess: (_, { podId, assistantName }) => {
            queryClient.invalidateQueries({ queryKey: ['assistants', podId] });
            queryClient.invalidateQueries({ queryKey: ['assistants', podId, assistantName] });
        },
    });
}

export function useDeleteAssistant() {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, assistantName }: { podId: string; assistantName: string }) =>
            getLemmaClient(podId).agents.delete(assistantName),
        onSuccess: (_, { podId }) => {
            queryClient.invalidateQueries({ queryKey: ['assistants', podId] });
        },
    });
}

// Conversations
export function useConversations(podId: string, assistantName: string, params?: { limit?: number; cursor?: string }) {
    return useQuery({
        queryKey: ['conversations', podId, assistantName, params],
        queryFn: async () => {
            const response = await getLemmaClient(podId).conversations.list({
                pod_id: podId,
                agent_name: assistantName,
                limit: params?.limit,
                page_token: params?.cursor,
            });
            return asPaginatedArray<Conversation>(response);
        },
        enabled: !!podId && !!assistantName,
    });
}

export function useScopedConversations(scope: ConversationScope, params?: { limit?: number; cursor?: string; enabled?: boolean }) {
    return useQuery({
        queryKey: ['conversations', scope, params],
        queryFn: async () => {
            const agentName = scope.agentName ?? scope.assistantName ?? scope.assistantId ?? undefined;
            const response = await getLemmaClient(scope.podId || undefined).conversations.list({
                pod_id: scope.podId ?? undefined,
                agent_name: agentName,
                limit: params?.limit,
                page_token: params?.cursor,
            });
            return asPaginatedArray<Conversation>(response);
        },
        enabled: params?.enabled !== false,
    });
}

export function useConversation(podId: string, id: string) {
    return useQuery({
        queryKey: ['conversations', podId, id],
        queryFn: () => getLemmaClient(podId || undefined).conversations.get(id, { pod_id: podId }) as Promise<Conversation>,
        enabled: !!podId && !!id,
    });
}

export function useCreateConversation() {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ podId, assistantName, data }: { podId: string; assistantName: string; data: { title?: string | null; model?: ConversationModel | null } }) =>
            getLemmaClient(podId).conversations.create({
                title: data.title ?? undefined,
                pod_id: podId,
                agent_name: assistantName,
                model: typeof data.model === 'undefined' ? undefined : (data.model as unknown as never),
            }) as Promise<Conversation>,
        onSuccess: (_, { podId, assistantName }) => {
            queryClient.invalidateQueries({ queryKey: ['conversations', podId, assistantName] });
        },
    });
}

export function useCreateScopedConversation() {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({ data }: {
            data: {
                title?: string | null;
                model?: ConversationModel | null;
                podId?: string | null;
                assistantName?: string | null;
                assistantId?: string | null; // deprecated alias
                organizationId?: string | null;
            };
        }) => {
            const assistantName = data.assistantName ?? data.assistantId ?? undefined;
            return getLemmaClient(data.podId || undefined).conversations.create({
                title: data.title ?? undefined,
                pod_id: data.podId ?? undefined,
                agent_name: assistantName,
                model: typeof data.model === 'undefined' ? undefined : (data.model as unknown as never),
            }) as Promise<Conversation>;
        },
        onSuccess: (_, { data }) => {
            queryClient.invalidateQueries({ queryKey: ['conversations'] });
            const assistantName = data.assistantName ?? data.assistantId;
            if (data.podId && assistantName) {
                queryClient.invalidateQueries({ queryKey: ['conversations', data.podId, assistantName] });
            }
        },
    });
}

// Messages
export function useMessages(podId: string, conversationId: string, params?: { limit?: number; cursor?: string }) {
    return useQuery({
        queryKey: ['conversations', podId, conversationId, 'messages', params],
        queryFn: async () => {
            const response = await getLemmaClient().conversations.messages.list(
                conversationId,
                {
                    limit: params?.limit,
                    page_token: params?.cursor,
                }
            );
            return asPaginatedArray<Message>(response);
        },
        enabled: !!podId && !!conversationId,
    });
}

export function useConversationMessages(conversationId: string, params?: { limit?: number; cursor?: string }) {
    return useQuery({
        queryKey: ['conversations', conversationId, 'messages', params],
        queryFn: async () => {
            const response = await getLemmaClient().conversations.messages.list(
                conversationId,
                {
                    limit: params?.limit,
                    page_token: params?.cursor,
                }
            );
            return asPaginatedArray<Message>(response);
        },
        enabled: !!conversationId,
    });
}
