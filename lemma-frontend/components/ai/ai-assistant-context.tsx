'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
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
import type { Conversation, ConversationModel } from '@/lib/types';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import {
    buildDisplayResourceHref,
    extractDisplayResourceFromInvocation,
    type DisplayResourceRequest,
} from '@/lib/assistant/display-resource';
import { buildConversationPresentationHref } from '@/lib/assistant/conversation-presentation';
import { resolveAssistantControllerGates } from '@/lib/assistant/controller-gates';
import {
    projectConversationMetadata,
    type ProjectSelection,
} from '@/lib/assistant/project-selection';
import { normalizeAppPageSlug } from '@/lib/utils/app-page-slugs';

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

interface AIAssistantContextType {
    isOpen: boolean;
    isReady: boolean;
    hasPodContext: boolean;
    podContext: PodContext | null | undefined;
    conversationPodId: string | null;
    conversationOrganizationId: string | null;
    openAssistant: () => void;
    closeAssistant: (options?: { skipUrlSync?: boolean; suppressUrlRestore?: boolean }) => void;
    toggleAssistant: () => void;
    conversations: Conversation[];
    openedConversationId: string | null;
    activeConversationId: string | null;
    availableModels: AvailableModelInfo[];
    conversationModel: ConversationModel | null;
    conversationRuntime?: AgentRuntimeConfig | null;
    setConversationModel: (model: ConversationModel | null, runtime?: AgentRuntimeConfig | null) => Promise<void>;
    // The project the *next* conversation starts in. It lives here rather than
    // in a composer because every composer in the app shares one assistant, and
    // the choice has to survive the composer being unmounted and remounted on
    // the way from pod home into the conversation it creates.
    pendingProject: ProjectSelection | null;
    setPendingProject: (project: ProjectSelection | null) => void;
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
    errorCode: string | null;
    errorReason: string | null;
    canRetryFailedMessage: boolean;
    sendMessage: (content: string, options?: SendMessageOptions) => Promise<void>;
    /** Append a follow-up to a conversation that already has a run in flight. */
    steerMessage: (content: string) => Promise<void>;
    retryFailedMessage: () => Promise<void>;
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
    navigateToResource: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
    lastCreatedResource: { type: string; id: string } | null;
}

const AIAssistantContext = createContext<AIAssistantContextType | undefined>(undefined);
/**
 * The live transcript, split out from the rest of the assistant because these
 * are the values that change on every streaming flush. Held together with
 * everything else, their identity dragged every consumer of the assistant —
 * the pod shell, the sidebar, the layout provider — into a re-render per token,
 * none of which display a transcript.
 */
interface AIAssistantTranscript {
    messages: Message[];
    streamingTool: StreamingTool | null;
}

const AIAssistantTranscriptContext = createContext<AIAssistantTranscript | undefined>(undefined);
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
    const [hasActivatedController, setHasActivatedController] = useState(false);
    const [lastCreatedResource, setLastCreatedResource] = useState<{ type: string; id: string } | null>(null);
    const [pendingProject, setPendingProjectState] = useState<ProjectSelection | null>(null);
    // Mirrored into a ref so `sendMessage` can read the current selection
    // without taking it as a dependency and changing identity on every pick.
    const pendingProjectRef = useRef<ProjectSelection | null>(null);
    const setPendingProject = useCallback((project: ProjectSelection | null) => {
        pendingProjectRef.current = project;
        setPendingProjectState(project);
    }, []);
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
    const hasAssistantLaunchRequest = Boolean(searchParams.get('assistantMessage'));
    const isControllerEnabled = isProviderEnabled && (
        hasActivatedController
        || isOpen
        || isConversationRoute
        || shouldRestoreAssistantFromUrl
        || hasAssistantLaunchRequest
    );
    const controllerGates = resolveAssistantControllerGates(isProviderEnabled, isControllerEnabled);

    // The controller loads a transcript once and keeps the last few resident, so
    // there is nothing left to gate: deferring the load only ever bought a clean
    // first paint against a store that used to discard itself. It no longer does.
    const controller = useAssistantController({
        client: controllerClient,
        podId: conversationScope.podId ?? undefined,
        agentName: resolvedAgentName,
        organizationId: conversationScope.organizationId ?? undefined,
        enabled: controllerGates.enabled,
        autoLoad: controllerGates.autoLoad,
        autoLoadMessages: isControllerEnabled,
    });

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
        setHasActivatedController(true);
        if (isOpenRef.current) return;
        isOpenRef.current = true;
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
                setHasActivatedController(true);
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
            // The app index addresses a page by the slug of the app's name, so
            // the resource name is slugged rather than linked verbatim.
            app_page: `/pod/${podId}/app/view?page=${encodeURIComponent(normalizeAppPageSlug(resourceId))}`,
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

    // The controller's messages are the transcript. There is no second copy to
    // merge and nothing to refetch when a run ends: the stream already delivered
    // every message, tool returns folded into their calls.
    const displayMessages = controller.messages;

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
        setHasActivatedController(true);
        controllerRef.current.openConversation(conversationId);
    }, []);

    const closeConversation = useCallback(() => {
        controllerRef.current.closeConversation();
    }, []);

    const selectConversation = useCallback((conversationId: string | null) => {
        if (conversationId) {
            setHasActivatedController(true);
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

        setHasActivatedController(true);

        markToolInvocationsSeen(seenAutoNavigationToolCallIds.current, controllerRef.current.messages);
        allowAutoNavigationRef.current = true;

        if (options?.forceNewConversation && controllerRef.current.openedConversationId) {
            controllerRef.current.closeConversation();
            await waitForControllerReset();
        }

        try {
            await controllerRef.current.sendMessage(trimmed, {
                instructions: options?.instructions,
                // Merged, not chosen between: a start path or remix link says
                // what the conversation is *for*, the composer's chip says
                // where it runs, and those are different questions. Explicit
                // metadata still wins key-for-key, so a launch that names its
                // own repo is not overridden by a stale chip.
                conversationMetadata: {
                    ...projectConversationMetadata(pendingProjectRef.current),
                    ...(options?.conversationMetadata ?? {}),
                },
                metadata: options?.metadata
                    ? {
                        source: 'lemma_frontend',
                        ...options.metadata,
                }
                    : undefined,
            });
        } catch (error) {
            throw error;
        }
    }, [isProviderEnabled]);

    const steerMessage = useCallback(async (content: string) => {
        const trimmed = content.trim();
        if (!trimmed || !isProviderEnabled) {
            return;
        }

        markToolInvocationsSeen(seenAutoNavigationToolCallIds.current, controllerRef.current.messages);
        allowAutoNavigationRef.current = true;

        await controllerRef.current.steerMessage(trimmed);
    }, [isProviderEnabled]);

    const retryFailedMessage = useCallback(async () => {
        markToolInvocationsSeen(seenAutoNavigationToolCallIds.current, controllerRef.current.messages);
        allowAutoNavigationRef.current = true;
        await controllerRef.current.retryFailedMessage();
    }, []);

    const resolveUserApproval = useCallback(async (
        approvalId: string,
        decision: 'APPROVE_ONCE' | 'APPROVE_FOR_SESSION' | 'DENY',
        response?: Record<string, unknown> | null,
    ) => {
        markToolInvocationsSeen(seenAutoNavigationToolCallIds.current, controllerRef.current.messages);
        allowAutoNavigationRef.current = true;
        await controllerRef.current.resolveUserApproval(approvalId, decision, response);
    }, []);

    const pendingActions = useMemo(() => controller.pendingActions as AIAction[], [controller.pendingActions]);
    const completedActions = useMemo(() => controller.completedActions as AIAction[], [controller.completedActions]);

    const contextValue = useMemo<AIAssistantContextType>(() => ({
        isOpen,
        isReady: isProviderEnabled && (!!podContext || !!assistantContext),
        hasPodContext: isProviderEnabled && !!podContext,
        podContext,
        conversationPodId: conversationScope.podId ?? null,
        conversationOrganizationId: controller.conversations.find(conversation => conversation.id === controller.openedConversationId)?.organization_id ?? conversationScope.organizationId ?? null,
        openAssistant,
        closeAssistant,
        toggleAssistant,
        conversations: controller.conversations,
        openedConversationId: controller.openedConversationId,
        activeConversationId: controller.openedConversationId,
        availableModels: controller.availableModels,
        conversationModel: controller.conversationModel as ConversationModel | null,
        conversationRuntime: controller.conversationRuntime,
        setConversationModel: controller.setConversationModel as (model: ConversationModel | null, runtime?: AgentRuntimeConfig | null) => Promise<void>,
        pendingProject,
        setPendingProject,
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
        errorCode: controller.errorCode,
        errorReason: controller.errorReason,
        canRetryFailedMessage: controller.canRetryFailedMessage,
        sendMessage,
        steerMessage,
        retryFailedMessage,
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
        navigateToResource,
        lastCreatedResource,
    }), [
        assistantContext,
        clearMessages,
        closeAssistant,
        completedActions,
        closeConversation,
        pendingProject,
        setPendingProject,
        controller.openedConversationId,
        controller.availableModels,
        controller.canRetryFailedMessage,
        controller.clearPendingFiles,
        controller.conversationModel,
        controller.conversationRuntime,
        controller.conversations,
        controller.error,
        controller.errorCode,
        controller.errorReason,
        controller.hasOlderMessages,
        controller.isOpenedConversationRunning,
        controller.isLoading,
        controller.isLoadingConversations,
        controller.isLoadingMessages,
        controller.isLoadingOlderMessages,
        controller.isUploadingFiles,
        controller.loadOlderMessages,
        controller.pendingFiles,
        controller.pendingFileUploads,
        controller.removePendingFile,
        controller.setConversationModel,
        controller.stop,
        controller.uploadFiles,
        conversationScope.podId,
        conversationScope.organizationId,
        isOpen,
        isProviderEnabled,
        lastCreatedResource,
        navigateToResource,
        openConversation,
        openAssistant,
        pendingActions,
        podContext,
        resolveUserApproval,
        retryFailedMessage,
        selectConversation,
        sendMessage,
        steerMessage,
        toggleAssistant,
    ]);

    const transcriptValue = useMemo<AIAssistantTranscript>(
        () => ({ messages: displayMessages, streamingTool: controller.streamingTool }),
        [controller.streamingTool, displayMessages],
    );

    return (
        <AIAssistantContext.Provider value={contextValue}>
            <AIAssistantTranscriptContext.Provider value={transcriptValue}>
                {children}
            </AIAssistantTranscriptContext.Provider>
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

/**
 * The live transcript, subscribed to separately from the rest of the assistant.
 *
 * Only a surface that draws messages should call this — it updates on every
 * streaming flush, and anything that reads it re-renders at that rate.
 */
export function useAIAssistantTranscript() {
    const transcript = useContext(AIAssistantTranscriptContext);
    if (transcript === undefined) {
        throw new Error('useAIAssistantTranscript must be used within an AIAssistantProvider');
    }
    return transcript;
}
