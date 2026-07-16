'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { AgentRuntimeConfig, AvailableModelInfo } from 'lemma-sdk';
import {
    useAssistantController,
    type AssistantMessagePart as SdkAssistantMessagePart,
    type AssistantPendingFileUpload as SdkAssistantPendingFileUpload,
    type AssistantRenderableMessage as SdkAssistantRenderableMessage,
    type AssistantStreamingTool as SdkAssistantStreamingTool,
    type AssistantToolInvocation as SdkAssistantToolInvocation,
} from 'lemma-sdk/react';
import type { AssistantContext, PodContext, AIAction } from '@/lib/types/ai';
import type { Conversation, ConversationModel, Message as RawConversationMessage } from '@/lib/types';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import {
    buildDisplayResourceHref,
    extractDisplayResourceFromInvocation,
    type DisplayResourceRequest,
} from '@/lib/assistant/display-resource';
import { buildConversationPresentationHref } from '@/lib/assistant/conversation-presentation';

interface ConversationScope {
    podId?: string | null;
    agentName?: string | null;
    assistantName?: string | null;
    assistantId?: string | null; // deprecated alias
    organizationId?: string | null;
}

type SendMessageOptions = {
    forceNewConversation?: boolean;
    instructions?: string | null;
    metadata?: Record<string, unknown> | null;
    conversationMetadata?: Record<string, unknown> | null;
    title?: string | null;
};

export type Message = SdkAssistantRenderableMessage;
export type ToolInvocation = SdkAssistantToolInvocation;
export type StreamingTool = SdkAssistantStreamingTool;
export type PendingFileUpload = SdkAssistantPendingFileUpload;
export type AssistantMessagePart = SdkAssistantMessagePart;

function isRecord(value: unknown): value is Record<string, unknown> {
    return !!value && typeof value === 'object' && !Array.isArray(value);
}

function normalizedToolResult(value: unknown): Record<string, unknown> {
    if (isRecord(value)) return value;
    if (typeof value === 'undefined' || value === null) return {};
    return { output: value };
}

function rawToolReturnPayload(message: RawConversationMessage): {
    toolCallId: string;
    toolName?: string;
    result: Record<string, unknown>;
} | null {
    const metadata = isRecord(message.metadata) ? message.metadata : null;
    const messageMetadata = isRecord(message.message_metadata) ? message.message_metadata : null;
    // Flat message shape: tool fields live at the top level of the message.
    const kind = typeof message.kind === 'string'
        ? message.kind
        : metadata?.message_type ?? messageMetadata?.message_type;
    const isToolReturn = kind === 'TOOL_RETURN' || kind === 'tool_return' || message.role === 'tool';

    if (!isToolReturn) return null;

    const toolCallId = (typeof message.tool_call_id === 'string' ? message.tool_call_id : undefined)
        ?? metadata?.tool_call_id ?? messageMetadata?.tool_call_id;
    if (typeof toolCallId !== 'string' || !toolCallId.trim()) return null;

    const toolName = (typeof message.tool_name === 'string' ? message.tool_name : undefined)
        ?? metadata?.tool_name ?? messageMetadata?.tool_name;
    const rawResult = message.tool_result ?? metadata?.result ?? messageMetadata?.result;

    return {
        toolCallId,
        toolName: typeof toolName === 'string' && toolName.trim() ? toolName : undefined,
        result: normalizedToolResult(rawResult),
    };
}

function hydrateToolReturnMessages(
    messages: SdkAssistantRenderableMessage[],
    rawMessages: RawConversationMessage[],
): SdkAssistantRenderableMessage[] {
    if (messages.length === 0 || rawMessages.length === 0) return messages;

    const rawReturnsByToolCallId = new Map<string, NonNullable<ReturnType<typeof rawToolReturnPayload>>>();
    rawMessages.forEach((message) => {
        const payload = rawToolReturnPayload(message);
        if (payload) rawReturnsByToolCallId.set(payload.toolCallId, payload);
    });

    if (rawReturnsByToolCallId.size === 0) return messages;

    let changed = false;
    const nextMessages = messages.map((message) => {
        const hydrateInvocation = (invocation: SdkAssistantToolInvocation): SdkAssistantToolInvocation => {
            const payload = rawReturnsByToolCallId.get(invocation.toolCallId);
            if (!payload) return invocation;
            changed = true;
            return {
                ...invocation,
                toolName: invocation.toolName === 'tool' && payload.toolName ? payload.toolName : invocation.toolName,
                state: 'result',
                result: payload.result,
            };
        };
        const nextToolInvocations = message.toolInvocations?.map(hydrateInvocation);
        const nextParts = message.parts?.map((part) => (
            part.type === 'tool'
                ? { ...part, toolInvocation: hydrateInvocation(part.toolInvocation) }
                : part
        ));

        if (nextToolInvocations === message.toolInvocations && nextParts === message.parts) return message;

        return {
            ...message,
            toolInvocations: nextToolInvocations,
            parts: nextParts,
        };
    });

    return changed ? nextMessages : messages;
}

interface AIAssistantContextType {
    isOpen: boolean;
    isReady: boolean;
    hasPodContext: boolean;
    podContext: PodContext | null | undefined;
    conversationPodId: string | null;
    openAssistant: () => void;
    closeAssistant: (options?: { skipUrlSync?: boolean; suppressUrlRestore?: boolean }) => void;
    toggleAssistant: () => void;
    messages: Message[];
    conversations: Conversation[];
    openedConversationId: string | null;
    activeConversationId: string | null;
    availableModels: AvailableModelInfo[];
    conversationModel: ConversationModel | null;
    conversationRuntime?: AgentRuntimeConfig | null;
    setConversationModel: (model: ConversationModel | null, runtime?: AgentRuntimeConfig | null) => Promise<void>;
    isOpenedConversationRunning: boolean;
    isActiveConversationRunning: boolean;
    openConversation: (conversationId: string) => void;
    closeConversation: () => void;
    selectConversation: (conversationId: string | null) => void;
    isLoading: boolean;
    isLoadingConversations: boolean;
    isLoadingMessages: boolean;
    isLoadingOlderMessages: boolean;
    hasOlderMessages: boolean;
    error: string | null;
    sendMessage: (content: string, options?: SendMessageOptions) => Promise<void>;
    uploadFiles: (files: File[], options?: { deferUntilSend?: boolean }) => Promise<void>;
    isUploadingFiles: boolean;
    pendingFiles: File[];
    pendingFileUploads: PendingFileUpload[];
    removePendingFile: (fileKey: string) => void;
    clearPendingFiles: () => void;
    loadOlderMessages: () => Promise<boolean>;
    resolveUserApproval: (
        approvalId: string,
        decision: 'APPROVE_ONCE' | 'APPROVE_FOR_SESSION' | 'DENY',
        response?: Record<string, unknown> | null,
    ) => Promise<void>;
    clearMessages: () => void;
    stop: () => void;
    pendingActions: AIAction[];
    completedActions: AIAction[];
    streamingTool: StreamingTool | null;
    navigateToResource: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
    lastCreatedResource: { type: string; id: string } | null;
}

const AIAssistantContext = createContext<AIAssistantContextType | undefined>(undefined);
const AUTO_NAVIGATION_BLOCKLIST = new Set<string>();
const ASSISTANT_CONVERSATION_PARAM = 'assistantConversationId';

function appendAssistantConversationParam(href: string, conversationId?: string | null): string {
    if (!conversationId) return href;
    const [withoutHash, hash = ''] = href.split('#');
    const [path, query = ''] = withoutHash.split('?');
    const params = new URLSearchParams(query);
    params.set(ASSISTANT_CONVERSATION_PARAM, conversationId);
    const nextQuery = params.toString();
    return `${path}${nextQuery ? `?${nextQuery}` : ''}${hash ? `#${hash}` : ''}`;
}

function isSuccessfulToolInvocation(invocation: SdkAssistantToolInvocation): boolean {
    return invocation.state === 'result' && invocation.result?.success !== false;
}

function latestSuccessfulToolInvocations(
    messages: SdkAssistantRenderableMessage[],
): SdkAssistantToolInvocation[] {
    const invocations: SdkAssistantToolInvocation[] = [];

    for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
        const message = messages[messageIndex];
        const tools = message.toolInvocations || [];
        for (let toolIndex = tools.length - 1; toolIndex >= 0; toolIndex -= 1) {
            const invocation = tools[toolIndex];
            if (isSuccessfulToolInvocation(invocation)) {
                invocations.push(invocation);
            }
        }
    }

    return invocations;
}

function markToolInvocationsSeen(
    seenToolCallIds: Set<string>,
    messages: SdkAssistantRenderableMessage[],
) {
    latestSuccessfulToolInvocations(messages).forEach((invocation) => {
        seenToolCallIds.add(invocation.toolCallId);
    });
}

function waitForControllerReset() {
    if (typeof window === 'undefined') {
        return Promise.resolve();
    }

    return new Promise<void>((resolve) => {
        window.requestAnimationFrame(() => {
            window.requestAnimationFrame(() => resolve());
        });
    });
}

interface AIAssistantProviderProps {
    children: ReactNode;
    podContext: PodContext | null | undefined;
    assistantContext?: AssistantContext | null;
    conversationScopeOverride?: Partial<ConversationScope> | null;
    enabled?: boolean;
    onOpenAssistant?: () => void;
}

export function AIAssistantProvider({
    children,
    podContext,
    assistantContext,
    conversationScopeOverride,
    enabled = true,
    onOpenAssistant,
}: AIAssistantProviderProps) {
    const [isOpen, setIsOpen] = useState(false);
    const [lastCreatedResource, setLastCreatedResource] = useState<{ type: string; id: string } | null>(null);
    const [sideViewMessageLoadGeneration, setSideViewMessageLoadGeneration] = useState(0);
    const [readySideViewMessageLoadGeneration, setReadySideViewMessageLoadGeneration] = useState(-1);
    const isOpenRef = useRef(isOpen);
    const seenAutoNavigationToolCallIds = useRef<Set<string>>(new Set());
    const allowAutoNavigationRef = useRef(false);
    const suppressAssistantUrlRestoreRef = useRef(false);
    const skipNextAssistantUrlSyncRef = useRef(false);
    const router = useRouter();
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const searchParamsString = searchParams.toString();

    const overridePodId = conversationScopeOverride?.podId;
    const overrideAgentName = conversationScopeOverride?.agentName;
    const overrideAssistantName = conversationScopeOverride?.assistantName;
    const overrideAssistantId = conversationScopeOverride?.assistantId;
    const overrideOrganizationId = conversationScopeOverride?.organizationId;
    const hasConversationScopeOverride = conversationScopeOverride != null;
    const isProviderEnabled = enabled;
    const routePodId = useMemo(() => {
        const match = pathname.match(/^\/pod\/([^/]+)/);
        return match?.[1] ? decodeURIComponent(match[1]) : undefined;
    }, [pathname]);

    const podContextPodId = podContext?.pod?.id;
    const assistantContextOrganizationId = assistantContext?.currentOrganizationId;

    const conversationScope = useMemo<ConversationScope>(() => {
        const baseScope: ConversationScope = (() => {
            if (podContextPodId) {
                return { podId: podContextPodId };
            }
            if (routePodId) {
                return { podId: routePodId };
            }
            if (assistantContextOrganizationId) {
                return { organizationId: assistantContextOrganizationId };
            }
            return {};
        })();

        if (!hasConversationScopeOverride) {
            return baseScope;
        }

        return {
            ...baseScope,
            ...(typeof overridePodId !== 'undefined' ? { podId: overridePodId } : {}),
            ...(typeof overrideAgentName !== 'undefined' ? { agentName: overrideAgentName } : {}),
            ...(typeof overrideAssistantName !== 'undefined' ? { assistantName: overrideAssistantName } : {}),
            ...(typeof overrideAssistantId !== 'undefined' ? { assistantId: overrideAssistantId } : {}),
            ...(typeof overrideOrganizationId !== 'undefined'
                ? { organizationId: overrideOrganizationId }
                : {}),
        };
    }, [
        assistantContextOrganizationId,
        hasConversationScopeOverride,
        overrideAgentName,
        overrideAssistantName,
        overrideAssistantId,
        overrideOrganizationId,
        overridePodId,
        podContextPodId,
        routePodId,
    ]);

    const resolvedAgentName = conversationScope.agentName ?? conversationScope.assistantName ?? conversationScope.assistantId ?? undefined;
    const controllerClient = useMemo(
        () => getLemmaClient(conversationScope.podId || undefined),
        [conversationScope.podId],
    );
    const isConversationRoute = /^\/pod\/[^/]+\/conversations(?:\/|$)/.test(pathname);
    const urlAssistantConversationId = searchParams.get(ASSISTANT_CONVERSATION_PARAM);
    const shouldRestoreAssistantFromUrl = !isConversationRoute && Boolean(urlAssistantConversationId);
    const shouldPrepareSideViewMessages = isProviderEnabled && !isConversationRoute && isOpen;

    const queryClient = useQueryClient();
    // When the side view opens for a conversation whose transcript is already
    // cached (e.g. clicking a display resource from the conversation page), load
    // it immediately instead of deferring. The deferral keeps a *cold* first paint
    // smooth, but with a warm cache it just makes the messages visibly reload.
    const activeConversationMessagesCached = Boolean(
        conversationScope.podId
        && urlAssistantConversationId
        && queryClient.getQueryData([
            'assistant-raw-conversation-messages',
            conversationScope.podId,
            urlAssistantConversationId,
        ]),
    );

    useEffect(() => {
        if (!shouldPrepareSideViewMessages) {
            return;
        }

        // Give sidebar and display-resource side views a clean first paint before hydrating transcripts.
        let frameId = window.requestAnimationFrame(() => {
            frameId = window.requestAnimationFrame(() => {
                setReadySideViewMessageLoadGeneration(sideViewMessageLoadGeneration);
            });
        });

        return () => {
            window.cancelAnimationFrame(frameId);
        };
    }, [shouldPrepareSideViewMessages, sideViewMessageLoadGeneration]);

    // Keep loading enabled continuously whenever the transcript is already cached
    // — including the brief window after navigation where the side panel hasn't
    // re-opened yet (isOpen false). Without this, the gate dips to false for a
    // render, the controller drops its messages, and the side view shows a
    // "Loading messages" spinner before re-hydrating identical data. Cold opens
    // (no cache) still defer for a clean first paint.
    const shouldLoadActiveConversationMessages = isConversationRoute
        || activeConversationMessagesCached
        || (shouldPrepareSideViewMessages && readySideViewMessageLoadGeneration === sideViewMessageLoadGeneration);

    const controller = useAssistantController({
        client: controllerClient,
        podId: conversationScope.podId ?? undefined,
        agentName: resolvedAgentName,
        organizationId: conversationScope.organizationId ?? undefined,
        enabled: isProviderEnabled,
        autoLoadMessages: shouldLoadActiveConversationMessages,
    });

    const rawMessagesQuery = useQuery({
        queryKey: ['assistant-raw-conversation-messages', conversationScope.podId, controller.openedConversationId],
        queryFn: async () => {
            if (!conversationScope.podId || !controller.openedConversationId) {
                return [] as RawConversationMessage[];
            }
            const response = await controllerClient.conversations.messages.list(
                controller.openedConversationId,
                {
                    pod_id: conversationScope.podId,
                    limit: 100,
                },
            );
            return (response.items || []) as RawConversationMessage[];
        },
        enabled: !!conversationScope.podId && !!controller.openedConversationId && isProviderEnabled && shouldLoadActiveConversationMessages,
        refetchOnWindowFocus: false,
    });
    const rawMessages = useMemo(() => rawMessagesQuery.data ?? [], [rawMessagesQuery.data]);
    const refetchRawMessages = rawMessagesQuery.refetch;
    const controllerRef = useRef(controller);

    useEffect(() => {
        isOpenRef.current = isOpen;
    }, [isOpen]);

    useEffect(() => {
        controllerRef.current = controller;
    }, [controller]);

    const openAssistant = useCallback(() => {
        suppressAssistantUrlRestoreRef.current = false;
        skipNextAssistantUrlSyncRef.current = false;
        onOpenAssistant?.();
        if (isOpenRef.current) return;
        isOpenRef.current = true;
        setSideViewMessageLoadGeneration((generation) => generation + 1);
        setIsOpen(true);
    }, [onOpenAssistant]);
    const closeAssistant = useCallback((options?: { skipUrlSync?: boolean; suppressUrlRestore?: boolean }) => {
        suppressAssistantUrlRestoreRef.current = options?.suppressUrlRestore !== false;
        if (!isOpenRef.current) return;
        skipNextAssistantUrlSyncRef.current = options?.skipUrlSync === true;
        isOpenRef.current = false;
        setIsOpen(false);
    }, []);
    const toggleAssistant = useCallback(() => {
        setIsOpen((prev) => {
            const next = !prev;
            isOpenRef.current = next;
            if (next) {
                onOpenAssistant?.();
                setSideViewMessageLoadGeneration((generation) => generation + 1);
            }
            return next;
        });
    }, [onOpenAssistant]);

    useEffect(() => {
        if (!isProviderEnabled || !shouldRestoreAssistantFromUrl) return;
        if (suppressAssistantUrlRestoreRef.current) return;

        if (!isOpenRef.current) {
            window.queueMicrotask(() => {
                if (!isOpenRef.current) {
                    openAssistant();
                }
            });
        }

        if (
            urlAssistantConversationId
            && controllerRef.current.openedConversationId !== urlAssistantConversationId
        ) {
            controllerRef.current.openConversation(urlAssistantConversationId);
        }
    }, [isProviderEnabled, openAssistant, shouldRestoreAssistantFromUrl, urlAssistantConversationId]);

    useEffect(() => {
        if (!isProviderEnabled || isConversationRoute) return;

        if (!shouldRestoreAssistantFromUrl) {
            suppressAssistantUrlRestoreRef.current = false;
        }

        if (shouldRestoreAssistantFromUrl && !isOpen) {
            return;
        }

        if (!isOpen && skipNextAssistantUrlSyncRef.current) {
            skipNextAssistantUrlSyncRef.current = false;
            return;
        }

        const nextParams = new URLSearchParams(searchParamsString);
        let changed = false;

        const setParam = (key: string, value: string) => {
            if (nextParams.get(key) === value) return;
            nextParams.set(key, value);
            changed = true;
        };

        const deleteParam = (key: string) => {
            if (!nextParams.has(key)) return;
            nextParams.delete(key);
            changed = true;
        };

        if (isOpen) {
            if (controller.openedConversationId) {
                setParam(ASSISTANT_CONVERSATION_PARAM, controller.openedConversationId);
            } else if (!urlAssistantConversationId) {
                deleteParam(ASSISTANT_CONVERSATION_PARAM);
            }
        } else {
            deleteParam(ASSISTANT_CONVERSATION_PARAM);
        }
        deleteParam('assistant');
        deleteParam('presentation');

        if (!changed) return;

        const nextQuery = nextParams.toString();
        router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, { scroll: false });
    }, [
        controller.openedConversationId,
        isConversationRoute,
        isOpen,
        isProviderEnabled,
        pathname,
        router,
        searchParamsString,
        shouldRestoreAssistantFromUrl,
        urlAssistantConversationId,
    ]);

    const navigateToResolvedResource = useCallback((href: string) => {
        const conversationPresentationHref = buildConversationPresentationHref({
            pathname,
            searchParams: searchParamsString,
            resourceHref: href,
            activeConversationId: controllerRef.current.openedConversationId,
        });
        router.push(conversationPresentationHref || href);
    }, [pathname, router, searchParamsString]);

    const navigateToResource = useCallback((resourceType: string, resourceId: string, meta?: Record<string, unknown>) => {
        if (resourceType === 'pod') {
            const buildPrompt = typeof meta?.buildPrompt === 'string' ? meta.buildPrompt : '';
            const buildQuery = buildPrompt ? `?build=${encodeURIComponent(buildPrompt)}` : '';
            router.push(`/pod/${resourceId}${buildQuery}`);
            return;
        }
        const pathParts = pathname.split('/');
        const podId = podContext?.pod?.id || pathParts[2];

        if (resourceType === 'connector') {
            router.push(podId ? `/pod/${podId}/connectors` : '/');
            return;
        }

        if (!podId) {
            return;
        }

        if (resourceType === 'display_resource') {
            const request = meta?.request as DisplayResourceRequest | undefined;
            if (!request) return;
            const href = buildDisplayResourceHref({
                podId,
                request,
                conversationId: typeof meta?.conversationId === 'string'
                    ? meta.conversationId
                    : controllerRef.current.openedConversationId || urlAssistantConversationId,
                toolCallId: resourceId,
            });
            if (href) {
                navigateToResolvedResource(href);
            }
            return;
        }

        const [a, b] = resourceId.split('/');
        const encodedResourceId = encodeURIComponent(resourceId);

        const routes: Record<string, string> = {
            agent: `/pod/${podId}/agents/${encodedResourceId}`,
            function: `/pod/${podId}/functions/${encodedResourceId}`,
            flow: `/pod/${podId}/flows/${encodedResourceId}`,
            datastore: `/pod/${podId}/data?datastore=${encodedResourceId}`,
            app_page: `/pod/${podId}/app/view?page=${encodeURIComponent(resourceId)}`,
            table: `/pod/${podId}/data?datastore=${encodeURIComponent(a)}&tab=${encodeURIComponent(b)}`,
        };

        const route = routes[resourceType];
        if (route) {
            const routeConversationId = typeof meta?.conversationId === 'string'
                ? meta.conversationId
                : controllerRef.current.openedConversationId || urlAssistantConversationId;
            setLastCreatedResource({ type: resourceType, id: resourceId });
            router.push(appendAssistantConversationParam(
                route,
                routeConversationId,
            ));
        }
    }, [navigateToResolvedResource, pathname, podContext?.pod?.id, router, urlAssistantConversationId]);

    const displayMessages = useMemo(
        () => hydrateToolReturnMessages(controller.messages, rawMessages),
        [controller.messages, rawMessages],
    );

    useEffect(() => {
        if (!shouldLoadActiveConversationMessages) return;
        if (!controller.openedConversationId || controller.isLoading) return;
        void refetchRawMessages();
    }, [controller.openedConversationId, controller.isLoading, refetchRawMessages, shouldLoadActiveConversationMessages]);

    useEffect(() => {
        const successfulTools = latestSuccessfulToolInvocations(displayMessages);

        if (!allowAutoNavigationRef.current) {
            successfulTools.forEach((invocation) => {
                seenAutoNavigationToolCallIds.current.add(invocation.toolCallId);
            });
            return;
        }

        const lastTool = successfulTools.find((invocation) => (
            !seenAutoNavigationToolCallIds.current.has(invocation.toolCallId)
        ));

        successfulTools.forEach((invocation) => {
            seenAutoNavigationToolCallIds.current.add(invocation.toolCallId);
        });

        if (!lastTool) {
            allowAutoNavigationRef.current = false;
            return;
        }

        allowAutoNavigationRef.current = false;

        if (lastTool) {
            if (AUTO_NAVIGATION_BLOCKLIST.has(lastTool.toolName)) {
                return;
            }

            const displayResource = extractDisplayResourceFromInvocation(lastTool);
            if (displayResource && conversationScope.podId && controller.openedConversationId) {
                const href = buildDisplayResourceHref({
                    podId: conversationScope.podId,
                    request: displayResource.request,
                    conversationId: controller.openedConversationId,
                    toolCallId: displayResource.toolCallId,
                });
                if (href) {
                    setLastCreatedResource({
                        type: displayResource.request.type.toLowerCase(),
                        id: displayResource.request.name || displayResource.request.path || displayResource.toolCallId,
                    });
                    navigateToResolvedResource(href);
                    return;
                }
            }

            const resourceType = typeof lastTool.result?.resourceType === 'string'
                ? lastTool.result.resourceType
                : null;
            const resourceId = typeof lastTool.result?.resourceId === 'string'
                ? lastTool.result.resourceId
                : null;

            if (resourceType && resourceId) {
                setTimeout(() => {
                    navigateToResource(resourceType, resourceId, lastTool?.result);
                }, 500);
            }
        }
    }, [controller.openedConversationId, conversationScope.podId, displayMessages, navigateToResolvedResource, navigateToResource]);

    const clearMessages = useCallback(() => {
        setLastCreatedResource(null);
        controllerRef.current.closeConversation();
        controllerRef.current.clearPendingFiles();
    }, []);

    const openConversation = useCallback((conversationId: string) => {
        controllerRef.current.openConversation(conversationId);
    }, []);

    const closeConversation = useCallback(() => {
        controllerRef.current.closeConversation();
    }, []);

    const selectConversation = useCallback((conversationId: string | null) => {
        if (conversationId) {
            controllerRef.current.openConversation(conversationId);
            return;
        }
        controllerRef.current.closeConversation();
    }, []);

    const sendMessage = useCallback(async (content: string, options?: SendMessageOptions) => {
        const trimmed = content.trim();
        if (!trimmed || !isProviderEnabled) {
            return;
        }

        markToolInvocationsSeen(seenAutoNavigationToolCallIds.current, displayMessages);
        allowAutoNavigationRef.current = true;

        if (options?.forceNewConversation && controllerRef.current.openedConversationId) {
            controllerRef.current.closeConversation();
            await waitForControllerReset();
        }

        await controllerRef.current.sendMessage(trimmed, {
            instructions: options?.instructions,
            conversationMetadata: options?.conversationMetadata,
            metadata: options?.metadata
                ? {
                    source: 'lemma_frontend',
                    ...options.metadata,
            }
                : undefined,
        });
    }, [displayMessages, isProviderEnabled]);

    const resolveUserApproval = useCallback(async (
        approvalId: string,
        decision: 'APPROVE_ONCE' | 'APPROVE_FOR_SESSION' | 'DENY',
        response?: Record<string, unknown> | null,
    ) => {
        markToolInvocationsSeen(seenAutoNavigationToolCallIds.current, displayMessages);
        allowAutoNavigationRef.current = true;
        await controllerRef.current.resolveUserApproval(approvalId, decision, response);
        await refetchRawMessages();
    }, [displayMessages, refetchRawMessages]);

    const pendingActions = useMemo(() => controller.pendingActions as AIAction[], [controller.pendingActions]);
    const completedActions = useMemo(() => controller.completedActions as AIAction[], [controller.completedActions]);

    const contextValue = useMemo<AIAssistantContextType>(() => ({
        isOpen,
        isReady: isProviderEnabled && (!!podContext || !!assistantContext),
        hasPodContext: isProviderEnabled && !!podContext,
        podContext,
        conversationPodId: conversationScope.podId ?? null,
        openAssistant,
        closeAssistant,
        toggleAssistant,
        messages: displayMessages,
        conversations: controller.conversations,
        openedConversationId: controller.openedConversationId,
        activeConversationId: controller.openedConversationId,
        availableModels: controller.availableModels,
        conversationModel: controller.conversationModel as ConversationModel | null,
        conversationRuntime: controller.conversationRuntime,
        setConversationModel: controller.setConversationModel as (model: ConversationModel | null, runtime?: AgentRuntimeConfig | null) => Promise<void>,
        isOpenedConversationRunning: controller.isOpenedConversationRunning,
        isActiveConversationRunning: controller.isOpenedConversationRunning,
        openConversation,
        closeConversation,
        selectConversation,
        isLoading: controller.isLoading,
        isLoadingConversations: controller.isLoadingConversations,
        isLoadingMessages: controller.isLoadingMessages,
        isLoadingOlderMessages: controller.isLoadingOlderMessages,
        hasOlderMessages: controller.hasOlderMessages,
        error: controller.error,
        sendMessage,
        uploadFiles: controller.uploadFiles,
        isUploadingFiles: controller.isUploadingFiles,
        pendingFiles: controller.pendingFiles,
        pendingFileUploads: controller.pendingFileUploads,
        removePendingFile: controller.removePendingFile,
        clearPendingFiles: controller.clearPendingFiles,
        loadOlderMessages: controller.loadOlderMessages,
        resolveUserApproval,
        clearMessages,
        stop: controller.stop,
        pendingActions,
        completedActions,
        streamingTool: controller.streamingTool,
        navigateToResource,
        lastCreatedResource,
    }), [
        assistantContext,
        clearMessages,
        closeAssistant,
        completedActions,
        closeConversation,
        controller.openedConversationId,
        controller.availableModels,
        controller.clearPendingFiles,
        controller.conversationModel,
        controller.conversationRuntime,
        controller.conversations,
        controller.error,
        controller.hasOlderMessages,
        controller.isOpenedConversationRunning,
        controller.isLoading,
        controller.isLoadingConversations,
        controller.isLoadingMessages,
        controller.isLoadingOlderMessages,
        controller.isUploadingFiles,
        controller.loadOlderMessages,
        displayMessages,
        controller.pendingFiles,
        controller.pendingFileUploads,
        controller.streamingTool,
        controller.removePendingFile,
        controller.setConversationModel,
        controller.stop,
        controller.uploadFiles,
        conversationScope.podId,
        isOpen,
        isProviderEnabled,
        lastCreatedResource,
        navigateToResource,
        openConversation,
        openAssistant,
        pendingActions,
        podContext,
        resolveUserApproval,
        selectConversation,
        sendMessage,
        toggleAssistant,
    ]);

    return (
        <AIAssistantContext.Provider value={contextValue}>
            {children}
        </AIAssistantContext.Provider>
    );
}

export function useAIAssistant() {
    const context = useContext(AIAssistantContext);
    if (context === undefined) {
        throw new Error('useAIAssistant must be used within an AIAssistantProvider');
    }
    return context;
}
